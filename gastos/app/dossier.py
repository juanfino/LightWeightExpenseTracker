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
    period_rows = db.get_expenses_for_period_art(year, month)
    history_rows = db.get_expenses_excluding_period(year, month)
    months_with_data = db.get_months_with_data()

    monthly_totals = _monthly_totals(history_rows)
    monthly_totals[(year, month)] = sum(r["amount"] for r in period_rows)

    dossier = {
        "period": {"year": year, "month": month, "label": _month_label(year, month)},
        "base": _build_base(period_rows),
        "contrasts": _build_contrasts(year, month, monthly_totals),
        "delta_attribution": _build_delta_attribution(year, month, period_rows, history_rows),
        "outliers": _build_outliers(year, month, period_rows, history_rows),
        "fixed_expenses": _build_fixed_expenses(year, month),
        "dollars": _build_dollars(year, month, sum(r["amount"] for r in period_rows)),
        "registration_coverage": _build_registration_coverage(period_rows),
        "taxonomy": _build_taxonomy(year, month, period_rows, history_rows),
        "recurrence_evidence": _build_recurrence_evidence(year, month, period_rows, history_rows),
        "hard_facts": {
            "first_expense_date": db.get_first_expense_date(),
            "months_available": len(months_with_data),
        },
        "inflation_unavailable": db.get_ipc_value(year, month) is None,
        "variable_expenses": [
            {
                "expense_id": r["id"],
                "concept": r["concept"],
                "amount": r["amount"],
                "category": r["category_name"] or "Sin categoría",
                "date": r["created_at"],
            }
            for r in period_rows
            if r["fixed_expense_id"] is None
        ],
    }
    return dossier


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


def _build_contrasts(year: int, month: int, monthly_totals: dict) -> dict:
    current_total = monthly_totals.get((year, month), 0.0)
    contrasts = {}

    def _nominal_and_real(baseline_period: tuple[int, int] | None, label: str, is_average: bool = False,
                           avg_periods: list[tuple[int, int]] | None = None) -> dict:
        if is_average:
            available_periods = [p for p in avg_periods if p in monthly_totals]
            if not available_periods:
                return {"available": False, "label": label}
            baseline_total = sum(monthly_totals[p] for p in available_periods) / len(available_periods)
            # Real comparison for an average baseline deflates each contributing month to
            # the current period's prices, then averages — a single "average period" has
            # no IPC entry of its own to deflate from.
            real_values = [
                inflation.deflate(monthly_totals[p], p[0], p[1], year, month) for p in available_periods
            ]
            real_baseline = (
                sum(v for v in real_values if v is not None) / len([v for v in real_values if v is not None])
                if any(v is not None for v in real_values) else None
            )
        else:
            if baseline_period is None or baseline_period not in monthly_totals:
                return {"available": False, "label": label}
            baseline_total = monthly_totals[baseline_period]
            real_baseline = inflation.deflate(baseline_total, baseline_period[0], baseline_period[1], year, month)

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
        if real_baseline is not None:
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


def _build_fixed_expenses(year: int, month: int) -> dict:
    payments = db.get_fixed_payments_for_period(year, month)
    prev_y, prev_m = _prev_period(year, month)
    prev_payments = {p["id"]: p for p in db.get_fixed_payments_for_period(prev_y, prev_m)}
    summary = db.get_fixed_expense_monthly_summary(year, month)

    items = []
    unpaid = []
    for p in payments:
        entry = {
            "id": p["id"],
            "concept": p["concept"],
            "estimated_amount": p["estimated_amount"],
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
        "count_total": summary["count_total"],
        "count_paid": summary["count_paid"],
        "total_estimated": summary["total_estimated"],
        "total_paid": summary["total_paid"],
    }


def _build_dollars(year: int, month: int, total_spending: float) -> dict:
    by_tipo = db.get_cambios_resumen_mes_by_tipo(year, month)
    coverage_ratio = (
        round(by_tipo["venta"]["total_ars"] / total_spending, 3) if total_spending else None
    )
    return {"venta": by_tipo["venta"], "compra": by_tipo["compra"], "coverage_ratio": coverage_ratio}


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


def _build_recurrence_evidence(year: int, month: int, period_rows: list[dict], history_rows: list[dict]) -> dict:
    all_variable = [r for r in period_rows if r["fixed_expense_id"] is None] + \
        [r for r in history_rows if r["fixed_expense_id"] is None]

    by_concept: dict[str, list[dict]] = {}
    for r in all_variable:
        key = categorizer.normalize(r["concept"])
        if not key:
            continue
        y, m = _art_period(r["created_at"])
        by_concept.setdefault(key, []).append({"year": y, "month": m, "amount": r["amount"], "concept": r["concept"]})

    months_available = len(db.get_months_with_data())
    evidence = {}
    for key, occurrences in by_concept.items():
        months_seen = len({(o["year"], o["month"]) for o in occurrences})
        evidence[key] = {
            "display_concept": occurrences[0]["concept"],
            "months_seen": months_seen,
            "months_available": months_available,
            "occurrences": occurrences,
        }
    return evidence
