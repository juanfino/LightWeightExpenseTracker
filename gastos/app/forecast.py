"""Deterministic next-month forecast for persisted monthly reports.

The target is always the calendar month immediately after the report period and
all inputs are cut off at the end of that report period.  Forecasts are computed
once, stored with the producing report, and never recomputed.
"""

from __future__ import annotations

from datetime import timedelta, timezone
from decimal import Decimal

import db
import fixed_matcher
import money


METHOD_ID = "category_median_iqr_tail_v1"
HISTORY_WINDOW_MONTHS = 6
MIN_HISTORY_MONTHS = 3
MATERIALITY_RATIO = Decimal("0.02")
_CURRENCIES = ("ARS", "USD")
_BAIRES = timezone(timedelta(hours=-3))


def next_period(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _period_key(year: int, month: int) -> int:
    return year * 12 + month - 1


def _quantile(values: list[Decimal], percentile: Decimal) -> Decimal:
    """Linear-interpolated quantile using Decimal throughout."""
    ordered = sorted(values)
    if not ordered:
        return money.MONEY_ZERO
    if len(ordered) == 1:
        return ordered[0]
    position = Decimal(len(ordered) - 1) * percentile
    lower = int(position)
    fraction = position - lower
    if not fraction:
        return ordered[lower]
    return ordered[lower] + (ordered[lower + 1] - ordered[lower]) * fraction


def _range(values: list[Decimal]) -> dict:
    return {
        "low": money.amount(_quantile(values, Decimal("0.25"))),
        "central": money.amount(_quantile(values, Decimal("0.50"))),
        "high": money.amount(_quantile(values, Decimal("0.75"))),
    }


def _stability(value_range: dict) -> str:
    central = value_range["central"]
    if central <= 0:
        return "variable"
    relative_iqr = (value_range["high"] - value_range["low"]) / central
    if relative_iqr <= Decimal("0.25"):
        return "stable"
    if relative_iqr <= Decimal("0.75"):
        return "variable"
    return "high_variance"


def _projected_inflation_factors(
    periods: list[tuple[int, int]], cutoff: tuple[int, int], series: list[dict]
) -> tuple[dict[tuple[int, int], Decimal] | None, str]:
    """Return factors from each history month into target-month terms.

    Only index observations through ``cutoff`` participate.  The target index is
    projected from up to the last three sequential month-over-month ratios, so a
    later-published actual target index can never rewrite the historical forecast.
    """
    values = {
        (row["year"], row["month"]): Decimal(str(row["value"]))
        for row in series
        if _period_key(row["year"], row["month"]) <= _period_key(*cutoff)
    }
    if cutoff not in values or any(period not in values for period in periods):
        return None, "real_unavailable"

    ordered = sorted(values)
    ratios: list[Decimal] = []
    for previous, current in zip(ordered, ordered[1:]):
        if _period_key(*current) == _period_key(*previous) + 1 and current <= cutoff:
            ratios.append(values[current] / values[previous])
        elif current <= cutoff:
            ratios = []
    if not ratios:
        return None, "real_unavailable"

    forward_ratio = sum(ratios[-3:], Decimal("0")) / len(ratios[-3:])
    target_index = values[cutoff] * forward_ratio
    return {period: target_index / values[period] for period in periods}, "adjusted"


def _category_key(row: dict) -> str:
    category_id = row.get("category_id")
    return str(category_id) if category_id is not None else "uncategorized"


def _currency_forecast(
    year: int, month: int, currency: str, rows: list[dict], fixed_rows: list[dict], ipc_series: list[dict]
) -> dict:
    periods_with_data = sorted({fixed_matcher.expense_period(row["created_at"], _BAIRES) for row in rows})
    observed_periods = periods_with_data[-HISTORY_WINDOW_MONTHS:]
    history_months = len(observed_periods)
    variable_available = history_months >= MIN_HISTORY_MONTHS

    fixed_items = [
        {
            "fixed_expense_id": row["id"],
            "concept": row["concept"],
            "amount": row["estimated_amount"],
            "category_id": row["category_id"],
        }
        for row in fixed_rows
    ]
    fixed_total = sum(
        (row["estimated_amount"] or money.MONEY_ZERO for row in fixed_rows), money.MONEY_ZERO
    )
    fixed_bucket = {
        "confidence": "high",
        "items": fixed_items,
        "low": fixed_total,
        "central": fixed_total,
        "high": fixed_total,
        "has_unknown_amounts": any(row["estimated_amount"] is None for row in fixed_rows),
    }

    result = {
        "currency": currency,
        "history_months": history_months,
        "history_window": [f"{py:04d}-{pm:02d}" for py, pm in observed_periods],
        "data_floor_months": MIN_HISTORY_MONTHS,
        "variable_available": variable_available,
        "fixed": fixed_bucket,
        "inflation_status": "real_not_applicable" if currency != "ARS" else "real_unavailable",
        "habitual": {"confidence": "medium", "categories": []},
        "tail": {"confidence": "low"},
    }
    if not variable_available:
        result["total"] = dict(fixed_bucket, fixed_only=True)
        return result

    period_set = set(observed_periods)
    variable_rows = [
        row for row in rows
        if row["fixed_expense_id"] is None
        and fixed_matcher.expense_period(row["created_at"], _BAIRES) in period_set
    ]
    factors = None
    if currency == "ARS":
        factors, result["inflation_status"] = _projected_inflation_factors(
            observed_periods, (year, month), ipc_series
        )

    monthly_by_category: dict[str, dict[tuple[int, int], Decimal]] = {}
    category_names: dict[str, str] = {}
    for row in variable_rows:
        period = fixed_matcher.expense_period(row["created_at"], _BAIRES)
        key = _category_key(row)
        category_names[key] = row.get("category_name") or "Sin categoría"
        adjusted = row["amount"] * factors[period] if factors else row["amount"]
        monthly_by_category.setdefault(key, {})
        monthly_by_category[key][period] = monthly_by_category[key].get(period, money.MONEY_ZERO) + adjusted

    monthly_variable_totals = [
        sum((values.get(period, money.MONEY_ZERO) for values in monthly_by_category.values()), money.MONEY_ZERO)
        for period in observed_periods
    ]
    materiality_floor = _quantile(monthly_variable_totals, Decimal("0.50")) * MATERIALITY_RATIO
    required_presence = history_months // 2 + 1
    habitual_keys = []
    habitual_ranges = []
    for key, by_period in monthly_by_category.items():
        nonzero = [value for value in by_period.values() if value > 0]
        if len(nonzero) < required_presence:
            continue
        values = [by_period.get(period, money.MONEY_ZERO) for period in observed_periods]
        value_range = _range(values)
        if value_range["central"] < materiality_floor:
            continue
        habitual_keys.append(key)
        habitual_ranges.append(value_range)
        result["habitual"]["categories"].append({
            "category_key": key,
            "category": category_names[key],
            "months_present": len(nonzero),
            "range": value_range,
            "stability": _stability(value_range),
        })

    result["habitual"].update({
        bound: sum((item[bound] for item in habitual_ranges), money.MONEY_ZERO)
        for bound in ("low", "central", "high")
    })
    tail_monthly = []
    for period in observed_periods:
        tail_monthly.append(sum(
            (values.get(period, money.MONEY_ZERO) for key, values in monthly_by_category.items()
             if key not in habitual_keys),
            money.MONEY_ZERO,
        ))
    nonzero_tail = [value for value in tail_monthly if value > 0]
    if nonzero_tail:
        observed_range = _range(tail_monthly)
        conditional_range = _range(nonzero_tail)
        occurrence_rate = Decimal(len(nonzero_tail)) / history_months
        tail_range = {
            "low": observed_range["low"],
            "central": money.amount(max(
                observed_range["low"], conditional_range["central"] * occurrence_rate
            )),
            "high": max(conditional_range["high"], observed_range["high"]),
        }
    else:
        tail_range = _range(tail_monthly)
    result["tail"].update(tail_range)
    result["tail"]["months_with_tail"] = len(nonzero_tail)
    result["total"] = {
        bound: fixed_bucket[bound] + result["habitual"][bound] + result["tail"][bound]
        for bound in ("low", "central", "high")
    }
    result["total"]["fixed_only"] = False
    return result


def build_forecast(year: int, month: int) -> dict:
    """Forecast the month after ``year``/``month`` using no later facts."""
    target_year, target_month = next_period(year, month)
    all_rows = db.get_expenses_through_period_art(year, month)
    ipc_series = db.get_ipc_series()
    currencies = {}
    for currency in _CURRENCIES:
        rows = [row for row in all_rows if row["currency"] == currency]
        fixed_rows = db.get_active_fixed_expenses_at_cutoff(year, month, currency)
        currencies[currency] = _currency_forecast(
            year, month, currency, rows, fixed_rows, ipc_series
        )
    return {
        "method_id": METHOD_ID,
        "source_period": {"year": year, "month": month},
        "target_period": {"year": target_year, "month": target_month},
        "currencies": currencies,
    }


def actuals(forecast: dict) -> dict | None:
    """Bucket target-period actuals using the stored forecast category membership."""
    target = forecast["target_period"]
    rows = db.get_expenses_for_period_art(target["year"], target["month"])
    if not rows:
        return None
    result = {}
    for currency in _CURRENCIES:
        currency_rows = [row for row in rows if row["currency"] == currency]
        habitual_categories = {
            item["category_key"] for item in forecast["currencies"][currency]["habitual"].get("categories", [])
        }
        fixed_total = sum(
            (row["amount"] for row in currency_rows if row["fixed_expense_id"] is not None),
            money.MONEY_ZERO,
        )
        habitual_total = sum(
            (row["amount"] for row in currency_rows
             if row["fixed_expense_id"] is None and _category_key(row) in habitual_categories),
            money.MONEY_ZERO,
        )
        tail_total = sum(
            (row["amount"] for row in currency_rows
             if row["fixed_expense_id"] is None and _category_key(row) not in habitual_categories),
            money.MONEY_ZERO,
        )
        result[currency] = {
            "fixed": fixed_total,
            "habitual": habitual_total,
            "tail": tail_total,
            "total": fixed_total + habitual_total + tail_total,
        }
    return result
