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

Currency blocks derive from currencies used in the period plus the family default.
No aggregate sums amounts across currencies. ``equivalence`` contains isolated,
reference-only valuations of each non-default currency in the default currency at
the family's own rates; they never feed a contrast, IPC, or partition total.
"""

import statistics
from datetime import datetime, timedelta, timezone

import categorizer
import db
import fixed_matcher
import inflation
import money

BAIRES = timezone(timedelta(hours=-3))

MONTHS_ES = [
    "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]

_MIN_HISTORY_FOR_OUTLIERS = 3
_OUTLIER_STDEV_MULTIPLIER = 2


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
    default_currency = db.get_family_default_currency()
    all_period_rows = db.get_expenses_for_period_art(year, month)
    used = {row["currency"] for row in all_period_rows}
    currencies = [default_currency] + [
        row["code"] for row in db.get_currencies()
        if row["code"] in used and row["code"] != default_currency
    ]
    period_rows = {
        currency: [row for row in all_period_rows if row["currency"] == currency]
        for currency in currencies
    }
    history_rows = {
        currency: db.get_expenses_excluding_period(year, month, currency)
        for currency in currencies
    }
    months_with_data = db.get_months_with_data()

    monthly_totals = {}
    for currency in currencies:
        totals = _monthly_totals(history_rows[currency])
        totals[(year, month)] = sum((r["amount"] for r in period_rows[currency]), money.MONEY_ZERO)
        monthly_totals[currency] = totals

    by_tipo = db.get_cambios_resumen_mes_by_tipo(year, month)
    period_totals = {currency: monthly_totals[currency][(year, month)] for currency in currencies}
    equivalence = _build_equivalence(year, month, default_currency, period_totals)
    dollars = _build_dollars(year, month, by_tipo, equivalence)
    exchanges = db.get_cambios_resumen_mes(year, month)
    exchanges.update({
        "coverage_ratio": dollars["coverage_ratio"],
        "coverage_basis": dollars["coverage_basis"],
        "converted_into_default": dollars["converted_into_default"],
        "default_currency": default_currency,
    })

    dossier = {
        "period": {"year": year, "month": month, "label": _month_label(year, month)},
        "hard_facts": {
            "first_expense_date": db.get_first_expense_date(),
            "months_available": len(months_with_data),
        },
        "default_currency": default_currency,
        "currency_order": currencies,
        "currency_metadata": {row["code"]: row for row in db.get_currencies()},
        "dollars": dollars,  # 7.7–7.16 compatibility shape
        "exchanges": exchanges,
        "equivalence": equivalence,
        "recurrence_evidence": _build_recurrence_evidence(period_rows, history_rows),
        "currencies": {
            currency: _build_currency_block(
                year, month, currency, period_rows[currency], history_rows[currency],
                monthly_totals[currency], apply_ipc=inflation.has_series(currency),
            )
            for currency in currencies
        },
    }
    for currency in currencies:
        if inflation.has_series(currency):
            dossier["currencies"][currency]["inflation_unavailable"] = (
                db.get_ipc_value(year, month) is None
            )
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


def _monthly_totals(history_rows: list[dict]) -> dict:
    totals = {}
    for r in history_rows:
        key = _art_period(r["created_at"])
        totals[key] = totals.get(key, money.MONEY_ZERO) + r["amount"]
    return totals


def _build_base(period_rows: list[dict]) -> dict:
    total = sum((r["amount"] for r in period_rows), money.MONEY_ZERO)
    count = len(period_rows)
    days_in_month = 31  # daily_avg is a rough denominator, not a calendar-exact one
    by_cat: dict[str, dict] = {}
    for r in period_rows:
        name = r["category_name"] or "Sin categoría"
        by_cat.setdefault(name, {"category": name, "total": money.MONEY_ZERO, "subcategorias": {}})
        by_cat[name]["total"] += r["amount"]
        if r["subcategory_name"]:
            sub = by_cat[name]["subcategorias"]
            sub[r["subcategory_name"]] = sub.get(r["subcategory_name"], money.MONEY_ZERO) + r["amount"]

    by_category = []
    for name, d in sorted(by_cat.items(), key=lambda kv: kv[1]["total"], reverse=True):
        by_category.append({
            "category": name,
            "total": d["total"],
            "pct": money.statistic(d["total"] / total * 100, 1) if total else 0,
            "subcategorias": [
                {"name": n, "total": t}
                for n, t in sorted(d["subcategorias"].items(), key=lambda kv: kv[1], reverse=True)
            ],
        })

    return {
        "total": total,
        "count": count,
        "daily_avg": money.rounded(total / days_in_month) if total else money.MONEY_ZERO,
        "by_category": by_category,
    }


def _build_contrasts(year: int, month: int, monthly_totals: dict, apply_ipc: bool = True) -> dict:
    """apply_ipc=False (USD) skips inflation.deflate entirely and marks every entry
    ``real_not_applicable`` — distinct from ``real_unavailable`` (ARS with no IPC data
    for that period): one says the concept doesn't apply, the other says the data is
    missing, and the narrative must not conflate them."""
    current_total = monthly_totals.get((year, month), money.MONEY_ZERO)
    contrasts = {}

    def _nominal_and_real(baseline_period: tuple[int, int] | None, label: str, is_average: bool = False,
                           avg_periods: list[tuple[int, int]] | None = None) -> dict:
        if is_average:
            available_periods = [p for p in avg_periods if p in monthly_totals]
            if not available_periods:
                return {"available": False, "label": label}
            baseline_total = money.rounded(
                sum(monthly_totals[p] for p in available_periods) / len(available_periods)
            )
            if apply_ipc:
                # Real comparison for an average baseline deflates each contributing month
                # to the current period's prices, then averages — a single "average
                # period" has no IPC entry of its own to deflate from.
                real_values = [
                    inflation.deflate(monthly_totals[p], p[0], p[1], year, month) for p in available_periods
                ]
                real_baseline = (
                    money.rounded(sum(v for v in real_values if v is not None) / len([v for v in real_values if v is not None]))
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
            "nominal_delta_pct": money.statistic((current_total - baseline_total) / baseline_total * 100, 1)
            if baseline_total else None,
        }
        if not apply_ipc:
            entry["real_not_applicable"] = True
        elif real_baseline is not None:
            entry["real_current"] = real_current
            entry["real_baseline"] = real_baseline
            entry["real_delta"] = real_current - real_baseline
            entry["real_delta_pct"] = money.statistic((real_current - real_baseline) / real_baseline * 100, 1) \
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

    def _by_category(rows: list[dict]) -> dict:
        totals = {}
        for r in rows:
            name = r["category_name"] or "Sin categoría"
            totals[name] = totals.get(name, money.MONEY_ZERO) + r["amount"]
        return totals

    current_by_cat = _by_category(period_rows)
    prev_by_cat = _by_category(prev_rows)
    categories = set(current_by_cat) | set(prev_by_cat)
    total_delta = sum(current_by_cat.values(), money.MONEY_ZERO) - sum(prev_by_cat.values(), money.MONEY_ZERO)

    items = []
    for cat in categories:
        delta = current_by_cat.get(cat, money.MONEY_ZERO) - prev_by_cat.get(cat, money.MONEY_ZERO)
        if delta == 0:
            continue
        items.append({
            "category": cat,
            "delta_nominal": delta,
            "pct_of_total_delta": money.statistic(delta / total_delta * 100, 1) if total_delta else None,
        })
    items.sort(key=lambda i: abs(i["delta_nominal"]), reverse=True)
    return {"available": True, "total_delta": total_delta, "items": items}


def _build_outliers(year: int, month: int, period_rows: list[dict], history_rows: list[dict]) -> dict:
    by_category: dict[str, list] = {}
    for r in history_rows:
        if r["fixed_expense_id"] is not None or r["category_id"] is None:
            continue
        by_category.setdefault(r["category_name"], []).append(r["amount"])

    stats: dict[str, tuple] = {}
    for cat, amounts in by_category.items():
        if len(amounts) < _MIN_HISTORY_FOR_OUTLIERS:
            continue
        stdev = statistics.stdev(amounts)
        if stdev == 0:
            continue
        stats[cat] = (statistics.mean(amounts), stdev)

    items = []
    outlier_total = money.MONEY_ZERO
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
                "category_mean": money.rounded(mean),
                "category_stdev": money.rounded(stdev),
            })
            outlier_total += r["amount"]

    total = sum((r["amount"] for r in period_rows), money.MONEY_ZERO)
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
            entry["delta_vs_prev_period_pct"] = money.statistic(
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
        "total_estimated": sum((p["estimated_amount"] or money.MONEY_ZERO for p in items), money.MONEY_ZERO),
        "total_paid": sum((p["total_paid"] or money.MONEY_ZERO for p in items), money.MONEY_ZERO),
    }


def _build_equivalence(year: int, month: int, default_currency: str,
                       totals: dict) -> dict:
    """Reference-only valuations in the family default, one per foreign currency."""
    default_total = totals[default_currency]
    items = {}
    for foreign_currency, foreign_total in totals.items():
        if foreign_currency == default_currency:
            continue
        quote = db.get_exchange_rate_for_pair(
            year, month, foreign_currency, default_currency
        )
        valued = money.amount(foreign_total * quote["rate"]) if quote else None
        items[foreign_currency] = {
            "foreign_currency": foreign_currency,
            "default_currency": default_currency,
            "available": quote is not None,
            "rate": quote["rate"] if quote else None,
            "rate_source": quote["rate_source"] if quote else None,
            "rate_period": quote["rate_period"] if quote else {"year": year, "month": month},
            "foreign_total": foreign_total,
            "total_in_default": valued,
        }

    combined = money.amount(
        default_total + sum(
            (item["total_in_default"] or money.MONEY_ZERO for item in items.values()),
            money.MONEY_ZERO,
        )
    )
    for item in items.values():
        item["share_pct"] = (
            money.statistic(item["total_in_default"] / combined * 100, 1)
            if item["total_in_default"] is not None and combined else 0.0
        )
    result = {
        "default_currency": default_currency,
        "default_total": default_total,
        "combined_default_equivalent": combined,
        "items": items,
    }

    # Keep the 7.7–7.16 ARS/USD numeric contract as aliases. Historical consumers
    # and regression fixtures can compare this familiar shape unchanged.
    if default_currency == "ARS" and "USD" in items:
        usd = items["USD"]
        legacy_sources = {
            "sale_current_period": "ventas_mes",
            "purchase_current_period": "compras_mes",
            "recent_operation": "mes_anterior",
        }
        result.update({
            "available": usd["available"],
            "rate": usd["rate"],
            "rate_source": legacy_sources.get(usd["rate_source"]),
            "rate_period": usd["rate_period"],
            "usd_total": usd["foreign_total"],
            "usd_total_in_ars": usd["total_in_default"],
            "ars_total": default_total,
            "combined_ars_equivalent": combined,
            "usd_share_pct": usd["share_pct"],
        })
    return result


def _build_dollars(year: int, month: int, by_tipo: dict, equivalence: dict) -> dict:
    default = equivalence["default_currency"]
    items = equivalence["items"]
    unavailable = [code for code, item in items.items() if item["foreign_total"] and not item["available"]]
    if default == "ARS" and set(items) <= {"USD"}:
        usd = items.get("USD", {"foreign_total": money.MONEY_ZERO, "available": False})
        if usd["available"] or not usd["foreign_total"]:
            basis_total = equivalence["combined_default_equivalent"]
            basis = "pesos + dólares equivalentes" if usd["foreign_total"] else "sólo pesos"
        else:
            basis_total = equivalence["default_total"]
            basis = "sólo pesos (sin cotización para convertir los dólares del mes)"
    else:
        basis_total = equivalence["combined_default_equivalent"]
        basis = f"{default} + equivalencias disponibles"
        if unavailable:
            basis += f" (sin cotización para {', '.join(unavailable)})"
    converted_into_default = db.get_converted_into_default_total(year, month)
    coverage_ratio = money.statistic(converted_into_default / basis_total, 3) if basis_total else None
    return {
        "venta": by_tipo["venta"],
        "compra": by_tipo["compra"],
        "coverage_ratio": coverage_ratio,
        "coverage_basis": basis,
        "converted_into_default": converted_into_default,
        "default_currency": default,
    }


def _build_registration_coverage(period_rows: list[dict]) -> list[dict]:
    by_user: dict[str, dict] = {}
    for r in period_rows:
        d = by_user.setdefault(r["user_name"], {"user": r["user_name"], "count": 0, "total": money.MONEY_ZERO})
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
        "uncategorized_total": sum((r["amount"] for r in uncategorized), money.MONEY_ZERO),
        "uncategorized_count": len(uncategorized),
        "new_categories": sorted(current_categories - recent_categories),
        "absent_categories": sorted(recent_categories - current_categories),
    }


def _build_recurrence_evidence(period_rows_by_currency: dict, history_rows_by_currency: dict) -> dict:
    by_concept: dict[str, list[dict]] = {}
    concept_currency: dict[str, str] = {}
    for currency in period_rows_by_currency:
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
