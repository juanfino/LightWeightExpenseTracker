"""Deterministic aggregation for the monthly AI-generated report.

No LLM involved here — every number in ``build_dossier`` is computed by code. The
two LLM calls in ``report_ai.py`` only narrate and (for the variable-expense
partition) classify against evidence this module already computed. This is the
foundation the "the model never does arithmetic" guarantee rests on.

Cash basis: everything is grouped by the expense's own ART-adjusted date
(``db.get_expenses_for_period_art`` / ``get_expenses_excluding_period``), not by
``fixed_expense_year``/``month`` — see the module docstring on those two db.py
functions. That's what keeps fixed + variable summing to the same total the
dashboard already shows for the period.

ARS and USD are built as two parallel, same-shape blocks under ``currencies``
(``base``, ``contrasts``, ``partition``, ``fixed_expenses``, etc.) — neither is a
derivative of the other, and no aggregate anywhere sums amounts across currencies.
The one exception is ``equivalence``: a single reference-only ARS-equivalent of the
month's USD spending, at the family's own dollar rate, used solely to convey
magnitude (in the hero and, once, in the narrative) — it never feeds a contrast,
IPC, or a partition total.
"""

import statistics
from datetime import datetime, timedelta, timezone

import categorizer
import db
import fixed_matcher
import inflation

BAIRES = timezone(timedelta(hours=-3))

MONTHS_ES = [
    "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]

_MIN_HISTORY_FOR_OUTLIERS = 3
_OUTLIER_STDEV_MULTIPLIER = 2
_CURRENCIES = ("ARS", "USD")


def _prev_period(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _shift_period(year: int, month: int, delta: int) -> tuple[int, int]:
    """delta months back (delta > 0) from (year, month)."""
    total = year * 12 + (month - 1) - delta
    return total // 12, total % 12 + 1


def _art_period(created_at_utc: str) -> tuple[int, int]:
    return fixed_matcher.expense_period(created_at_utc, BAIRES)


def _month_label(year: int, month: int) -> str:
    return f"{MONTHS_ES[month]} {year}"


def build_dossier(year: int, month: int) -> dict:
    period_rows = {c: db.get_expenses_for_period_art(year, month, c) for c in _CURRENCIES}
    history_rows = {c: db.get_expenses_excluding_period(year, month, c) for c in _CURRENCIES}
    months_with_data = db.get_months_with_data()

    monthly_totals = {}
    for currency in _CURRENCIES:
        totals = _monthly_totals(history_rows[currency])
        totals[(year, month)] = sum(r["amount"] for r in period_rows[currency])
        monthly_totals[currency] = totals

    ars_total = monthly_totals["ARS"][(year, month)]
    usd_total = monthly_totals["USD"][(year, month)]

    by_tipo = db.get_cambios_resumen_mes_by_tipo(year, month)
    equivalence = _build_equivalence(year, month, ars_total, usd_total, by_tipo)

    dossier = {
        "period": {"year": year, "month": month, "label": _month_label(year, month)},
        "hard_facts": {
            "first_expense_date": db.get_first_expense_date(),
            "months_available": len(months_with_data),
        },
        "dollars": _build_dollars(by_tipo, equivalence),
        "equivalence": equivalence,
        "recurrence_evidence": _build_recurrence_evidence(period_rows, history_rows),
        "currencies": {
            "ARS": _build_currency_block(
                year, month, "ARS", period_rows["ARS"], history_rows["ARS"],
                monthly_totals["ARS"], apply_ipc=True,
            ),
            "USD": _build_currency_block(
                year, month, "USD", period_rows["USD"], history_rows["USD"],
                monthly_totals["USD"], apply_ipc=False,
            ),
        },
    }
    dossier["currencies"]["ARS"]["inflation_unavailable"] = db.get_ipc_value(year, month) is None
    return dossier


def _build_currency_block(
    year: int, month: int, currency: str, period_rows: list[dict], history_rows: list[dict],
    monthly_totals: dict, apply_ipc: bool,
) -> dict:
    return {
        "currency": currency,
        "base": _build_base(period_rows),
        "contrasts": _build_contrasts(year, month, monthly_totals, apply_ipc=apply_ipc),
        "delta_attribution": _build_delta_attribution(year, month, period_rows, history_rows),
        "outliers": _build_outliers(year, month, period_rows, history_rows),
        "fixed_expenses": _build_fixed_expenses(year, month, currency),
        "taxonomy": _build_taxonomy(year, month, period_rows, history_rows),
        "registration_coverage": _build_registration_coverage(period_rows),
        "months_available": _months_available_for_currency(year, month, period_rows, history_rows),
        "variable_expenses": [
            {
                "expense_id": r["id"],
                "concept": r["concept"],
                "amount": r["amount"],
                "currency": currency,
                "category": r["category_name"] or "Sin categoría",
                "date": r["created_at"],
            }
            for r in period_rows
            if r["fixed_expense_id"] is None
        ],
    }


def _months_available_for_currency(year: int, month: int, period_rows: list[dict], history_rows: list[dict]) -> int:
    """Distinct ART periods with at least one expense *in this currency* — unlike the
    global ``hard_facts.months_available``, a month with only USD activity doesn't
    count as a month of ARS history (and vice versa), so an empty side of a currency
    doesn't masquerade as a spending drop in that currency's own contrasts."""
    periods = {_art_period(r["created_at"]) for r in history_rows}
    if period_rows:
        periods.add((year, month))
    return len(periods)


def _monthly_totals(history_rows: list[dict]) -> dict[tuple[int, int], float]:
    totals: dict[tuple[int, int], float] = {}
    for r in history_rows:
        key = _art_period(r["created_at"])
        totals[key] = totals.get(key, 0.0) + r["amount"]
    return totals


def _build_base(period_rows: list[dict]) -> dict:
    total = sum(r["amount"] for r in period_rows)
    count = len(period_rows)
    days_in_month = 31  # daily_avg is a rough denominator, not a calendar-exact one
    by_cat: dict[str, dict] = {}
    for r in period_rows:
        name = r["category_name"] or "Sin categoría"
        by_cat.setdefault(name, {"category": name, "total": 0.0, "subcategorias": {}})
        by_cat[name]["total"] += r["amount"]
        if r["subcategory_name"]:
            sub = by_cat[name]["subcategorias"]
            sub[r["subcategory_name"]] = sub.get(r["subcategory_name"], 0.0) + r["amount"]

    by_category = []
    for name, d in sorted(by_cat.items(), key=lambda kv: kv[1]["total"], reverse=True):
        by_category.append({
            "category": name,
            "total": d["total"],
            "pct": round(d["total"] / total * 100, 1) if total else 0,
            "subcategorias": [
                {"name": n, "total": t}
                for n, t in sorted(d["subcategorias"].items(), key=lambda kv: kv[1], reverse=True)
            ],
        })

    return {
        "total": total,
        "count": count,
        "daily_avg": round(total / days_in_month, 2) if total else 0.0,
        "by_category": by_category,
    }


def _build_contrasts(year: int, month: int, monthly_totals: dict, apply_ipc: bool = True) -> dict:
    """apply_ipc=False (USD) skips inflation.deflate entirely and marks every entry
    ``real_not_applicable`` — distinct from ``real_unavailable`` (ARS with no IPC data
    for that period): one says the concept doesn't apply, the other says the data is
    missing, and the narrative must not conflate them."""
    current_total = monthly_totals.get((year, month), 0.0)
    contrasts = {}

    def _nominal_and_real(baseline_period: tuple[int, int] | None, label: str, is_average: bool = False,
                           avg_periods: list[tuple[int, int]] | None = None) -> dict:
        if is_average:
            available_periods = [p for p in avg_periods if p in monthly_totals]
            if not available_periods:
                return {"available": False, "label": label}
            baseline_total = sum(monthly_totals[p] for p in available_periods) / len(available_periods)
            if apply_ipc:
                # Real comparison for an average baseline deflates each contributing month
                # to the current period's prices, then averages — a single "average
                # period" has no IPC entry of its own to deflate from.
                real_values = [
                    inflation.deflate(monthly_totals[p], p[0], p[1], year, month) for p in available_periods
                ]
                real_baseline = (
                    sum(v for v in real_values if v is not None) / len([v for v in real_values if v is not None])
                    if any(v is not None for v in real_values) else None
                )
            else:
                real_baseline = None
        else:
            if baseline_period is None or baseline_period not in monthly_totals:
                return {"available": False, "label": label}
            baseline_total = monthly_totals[baseline_period]
            real_baseline = (
                inflation.deflate(baseline_total, baseline_period[0], baseline_period[1], year, month)
                if apply_ipc else None
            )

        real_current = current_total  # already in this period's own prices
        entry = {
            "available": True,
            "label": label,
            "nominal_current": current_total,
            "nominal_baseline": baseline_total,
            "nominal_delta": current_total - baseline_total,
            "nominal_delta_pct": round((current_total - baseline_total) / baseline_total * 100, 1)
            if baseline_total else None,
        }
        if not apply_ipc:
            entry["real_not_applicable"] = True
        elif real_baseline is not None:
            entry["real_current"] = real_current
            entry["real_baseline"] = real_baseline
            entry["real_delta"] = real_current - real_baseline
            entry["real_delta_pct"] = round((real_current - real_baseline) / real_baseline * 100, 1) \
                if real_baseline else None
        else:
            entry["real_unavailable"] = True
        return entry

    contrasts["prev_month"] = _nominal_and_real(_prev_period(year, month), "mes anterior")
    contrasts["avg_3m"] = _nominal_and_real(
        None, "promedio 3 meses", is_average=True,
        avg_periods=[_shift_period(year, month, i) for i in (1, 2, 3)],
    )
    contrasts["avg_6m"] = _nominal_and_real(
        None, "promedio 6 meses", is_average=True,
        avg_periods=[_shift_period(year, month, i) for i in (1, 2, 3, 4, 5, 6)],
    )
    contrasts["same_month_last_year"] = _nominal_and_real((year - 1, month), "mismo mes año anterior")
    return contrasts


def _build_delta_attribution(year: int, month: int, period_rows: list[dict], history_rows: list[dict]) -> dict:
    prev_y, prev_m = _prev_period(year, month)
    prev_rows = [r for r in history_rows if _art_period(r["created_at"]) == (prev_y, prev_m)]
    if not prev_rows:
        return {"available": False}

    def _by_category(rows: list[dict]) -> dict[str, float]:
        totals: dict[str, float] = {}
        for r in rows:
            name = r["category_name"] or "Sin categoría"
            totals[name] = totals.get(name, 0.0) + r["amount"]
        return totals

    current_by_cat = _by_category(period_rows)
    prev_by_cat = _by_category(prev_rows)
    categories = set(current_by_cat) | set(prev_by_cat)
    total_delta = sum(current_by_cat.values()) - sum(prev_by_cat.values())

    items = []
    for cat in categories:
        delta = current_by_cat.get(cat, 0.0) - prev_by_cat.get(cat, 0.0)
        if delta == 0:
            continue
        items.append({
            "category": cat,
            "delta_nominal": delta,
            "pct_of_total_delta": round(delta / total_delta * 100, 1) if total_delta else None,
        })
    items.sort(key=lambda i: abs(i["delta_nominal"]), reverse=True)
    return {"available": True, "total_delta": total_delta, "items": items}


def _build_outliers(year: int, month: int, period_rows: list[dict], history_rows: list[dict]) -> dict:
    by_category: dict[str, list[float]] = {}
    for r in history_rows:
        if r["fixed_expense_id"] is not None or r["category_id"] is None:
            continue
        by_category.setdefault(r["category_name"], []).append(r["amount"])

    stats: dict[str, tuple[float, float]] = {}
    for cat, amounts in by_category.items():
        if len(amounts) < _MIN_HISTORY_FOR_OUTLIERS:
            continue
        stdev = statistics.stdev(amounts)
        if stdev == 0:
            continue
        stats[cat] = (statistics.mean(amounts), stdev)

    items = []
    outlier_total = 0.0
    for r in period_rows:
        if r["fixed_expense_id"] is not None or r["category_id"] is None:
            continue
        cat = r["category_name"]
        if cat not in stats:
            continue
        mean, stdev = stats[cat]
        if r["amount"] > mean + _OUTLIER_STDEV_MULTIPLIER * stdev:
            items.append({
                "expense_id": r["id"],
                "concept": r["concept"],
                "amount": r["amount"],
                "category": cat,
                "category_mean": round(mean, 2),
                "category_stdev": round(stdev, 2),
            })
            outlier_total += r["amount"]

    total = sum(r["amount"] for r in period_rows)
    return {
        "items": items,
        "total_with_outliers": total,
        "total_without_outliers": total - outlier_total,
    }


def _build_fixed_expenses(year: int, month: int, currency: str) -> dict:
    payments = [p for p in db.get_fixed_payments_for_period(year, month) if p["currency"] == currency]
    prev_y, prev_m = _prev_period(year, month)
    prev_payments = {p["id"]: p for p in db.get_fixed_payments_for_period(prev_y, prev_m) if p["currency"] == currency}

    items = []
    unpaid = []
    for p in payments:
        entry = {
            "id": p["id"],
            "concept": p["concept"],
            "estimated_amount": p["estimated_amount"],
            "currency": currency,
            "paid": p["paid"],
            "total_paid": p["total_paid"],
            "category": p["category_name"],
        }
        prev = prev_payments.get(p["id"])
        if p["paid"] and prev and prev["paid"] and prev["total_paid"]:
            entry["delta_vs_prev_period_pct"] = round(
                (p["total_paid"] - prev["total_paid"]) / prev["total_paid"] * 100, 1
            )
        items.append(entry)
        if not p["paid"]:
            unpaid.append({"id": p["id"], "concept": p["concept"], "estimated_amount": p["estimated_amount"]})

    return {
        "items": items,
        "unpaid": unpaid,
        "count_total": len(items),
        "count_paid": sum(1 for p in items if p["paid"]),
        "total_estimated": sum(p["estimated_amount"] or 0 for p in items),
        "total_paid": sum(p["total_paid"] or 0 for p in items),
    }


def _build_equivalence(year: int, month: int, ars_total: float, usd_total: float, by_tipo: dict) -> dict:
    """Reference-only ARS equivalent of the month's USD spending, at the family's own
    dollar rate — used to convey magnitude (hero card, and once in the narrative),
    never to feed a contrast, IPC or a partition total.

    Rate picked in order: this month's own sale rate (the rate the family actually
    got pesos at) -> this month's own purchase rate -> the most recent recorded
    operation within the last 12 months -> unavailable."""
    rate = rate_source = None
    rate_period = {"year": year, "month": month}

    if by_tipo["venta"]["cnt"]:
        rate, rate_source = by_tipo["venta"]["cotizacion_promedio"], "ventas_mes"
    elif by_tipo["compra"]["cnt"]:
        rate, rate_source = by_tipo["compra"]["cotizacion_promedio"], "compras_mes"
    else:
        latest = db.get_latest_cotizacion_upto(year, month)
        if latest:
            rate, rate_source = latest["cotizacion"], "mes_anterior"
            fecha = str(latest["fecha"])
            rate_period = {"year": int(fecha[:4]), "month": int(fecha[5:7])}

    available = rate is not None
    usd_total_in_ars = round(usd_total * rate, 2) if available else None
    combined = round(ars_total + (usd_total_in_ars or 0.0), 2)
    usd_share_pct = round((usd_total_in_ars or 0.0) / combined * 100, 1) if combined else 0.0

    return {
        "available": available,
        "rate": rate,
        "rate_source": rate_source,
        "rate_period": rate_period,
        "usd_total": usd_total,
        "usd_total_in_ars": usd_total_in_ars,
        "ars_total": ars_total,
        "combined_ars_equivalent": combined,
        "usd_share_pct": usd_share_pct,
    }


def _build_dollars(by_tipo: dict, equivalence: dict) -> dict:
    if equivalence["available"] or not equivalence["usd_total"]:
        basis_total = equivalence["combined_ars_equivalent"]
        basis = "pesos + dólares equivalentes" if equivalence["usd_total"] else "sólo pesos"
    else:
        # USD spending exists but there's no rate this period (or ever) to convert it —
        # fall back to an ARS-only basis rather than silently dropping USD spend from
        # the denominator.
        basis_total = equivalence["ars_total"]
        basis = "sólo pesos (sin cotización para convertir los dólares del mes)"
    coverage_ratio = round(by_tipo["venta"]["total_ars"] / basis_total, 3) if basis_total else None
    return {
        "venta": by_tipo["venta"],
        "compra": by_tipo["compra"],
        "coverage_ratio": coverage_ratio,
        "coverage_basis": basis,
    }


def _build_registration_coverage(period_rows: list[dict]) -> list[dict]:
    by_user: dict[str, dict] = {}
    for r in period_rows:
        d = by_user.setdefault(r["user_name"], {"user": r["user_name"], "count": 0, "total": 0.0})
        d["count"] += 1
        d["total"] += r["amount"]
    return sorted(by_user.values(), key=lambda d: d["total"], reverse=True)


def _build_taxonomy(year: int, month: int, period_rows: list[dict], history_rows: list[dict]) -> dict:
    uncategorized = [r for r in period_rows if r["category_id"] is None]
    current_categories = {r["category_name"] for r in period_rows if r["category_name"]}

    recent_periods = {_shift_period(year, month, i) for i in (1, 2, 3)}
    recent_categories = {
        r["category_name"]
        for r in history_rows
        if r["category_name"] and _art_period(r["created_at"]) in recent_periods
    }

    return {
        "uncategorized_total": sum(r["amount"] for r in uncategorized),
        "uncategorized_count": len(uncategorized),
        "new_categories": sorted(current_categories - recent_categories),
        "absent_categories": sorted(recent_categories - current_categories),
    }


def _build_recurrence_evidence(period_rows_by_currency: dict, history_rows_by_currency: dict) -> dict:
    by_concept: dict[str, list[dict]] = {}
    concept_currency: dict[str, str] = {}
    for currency in _CURRENCIES:
        all_variable = [r for r in period_rows_by_currency[currency] if r["fixed_expense_id"] is None] + \
            [r for r in history_rows_by_currency[currency] if r["fixed_expense_id"] is None]
        for r in all_variable:
            key = categorizer.normalize(r["concept"])
            if not key:
                continue
            evidence_key = f"{currency}:{key}"
            concept_currency[evidence_key] = currency
            y, m = _art_period(r["created_at"])
            by_concept.setdefault(evidence_key, []).append(
                {"year": y, "month": m, "amount": r["amount"], "concept": r["concept"]}
            )

    months_available = len(db.get_months_with_data())
    evidence = {}
    for key, occurrences in by_concept.items():
        months_seen = len({(o["year"], o["month"]) for o in occurrences})
        evidence[key] = {
            "display_concept": occurrences[0]["concept"],
            "currency": concept_currency[key],
            "months_seen": months_seen,
            "months_available": months_available,
            "occurrences": occurrences,
        }
    return evidence
