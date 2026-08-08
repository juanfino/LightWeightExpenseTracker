"""Orchestrates monthly report generation: dossier -> classify -> analyze -> persist.

Append-only, matching the reports table: every call to generate_report() inserts a
new row rather than overwriting a prior generation for the same period. If either LLM
call fails, the report is still persisted — dossier_json (and the fixed sections
rendered from it) is always present; output_json/llm_ok simply reflect that the
narrative layer didn't come through this time. Performs no Telegram/Flask I/O;
dashboard.py calls into this.
"""

import hashlib
import json
import logging

import db
import dossier as dossier_module
import forecast as forecast_module
import inflation
import report_ai
import report_preferences
import money

logger = logging.getLogger(__name__)

_CLASSIFICATION_LOOKBACK_MONTHS = 6
_CURRENCIES = ("ARS", "USD")


def generate_report(year: int, month: int) -> dict:
    """Builds and persists a new report for (year, month). Always returns the
    persisted, parsed report — even when the LLM calls fail — so the caller always
    has fixed-section data to render."""
    inflation.refresh()
    preferences = report_preferences.from_storage(db.get_report_preferences())
    dossier = dossier_module.build_dossier(year, month)
    computed_forecast = forecast_module.build_forecast(year, month)
    if "forecast" in preferences["emphasis"] or preferences["allow_suggestions"]:
        dossier["forecast"] = computed_forecast
    all_variable = [e for cur in _CURRENCIES for e in dossier["currencies"][cur]["variable_expenses"]]

    prior_classifications = db.get_recent_classifications_before(
        year, month, lookback_months=_CLASSIFICATION_LOOKBACK_MONTHS
    )
    classifications = report_ai.classify_expenses(dossier, all_variable, prior_classifications)

    partitions = _build_partitions(dossier, all_variable, classifications)
    for cur in _CURRENCIES:
        dossier["currencies"][cur]["partition"] = partitions[cur]

    output = None
    if classifications is not None:
        output = report_ai.analyze(dossier, preferences)
    llm_ok = output is not None

    fp = fingerprint(year, month)
    report_id = db.create_report(
        year=year,
        month=month,
        model=report_ai.model_name(),
        prompt_version=report_ai.prompt_version(preferences),
        dossier_json=money.json_dumps(dossier, ensure_ascii=False),
        output_json=money.json_dumps(output, ensure_ascii=False) if output is not None else None,
        fingerprint=fp,
        llm_ok=llm_ok,
        preferences_json=money.json_dumps(preferences, ensure_ascii=False),
    )
    db.save_report_forecast(report_id, computed_forecast)

    if classifications:
        by_id = {e["expense_id"]: e for e in all_variable}
        rows = [
            {
                "expense_id": c["expense_id"],
                "concept": by_id[c["expense_id"]]["concept"],
                "amount": by_id[c["expense_id"]]["amount"],
                "currency": by_id[c["expense_id"]].get("currency", "ARS"),
                "label": c["label"],
                "confidence": c.get("confidence"),
            }
            for c in classifications
            if c["expense_id"] in by_id
        ]
        if rows:
            db.save_classifications(report_id, rows)

    return get_report(year, month)


def _build_partitions(dossier: dict, all_variable: list[dict], classifications: list[dict] | None) -> dict:
    """Aggregates the model's per-expense labels into a three-way split, one per
    currency — the one piece of arithmetic the classification call itself never does.
    A USD expense never contributes to the ARS partition or vice versa."""
    by_id = {e["expense_id"]: e for e in all_variable}
    fixed_totals = {cur: dossier["currencies"][cur]["fixed_expenses"]["total_paid"] for cur in _CURRENCIES}

    if classifications is None:
        return {cur: {"available": False, "fixed_total": fixed_totals[cur]} for cur in _CURRENCIES}

    totals = {cur: {"recurring_total": money.MONEY_ZERO, "recurring_count": 0,
                     "exceptional_total": money.MONEY_ZERO, "exceptional_count": 0} for cur in _CURRENCIES}
    for c in classifications:
        expense = by_id.get(c["expense_id"])
        if expense is None:
            continue
        cur = expense.get("currency", "ARS")
        bucket = totals.setdefault(cur, {"recurring_total": money.MONEY_ZERO, "recurring_count": 0,
                                          "exceptional_total": money.MONEY_ZERO, "exceptional_count": 0})
        if c["label"] == "recurring":
            bucket["recurring_total"] += expense["amount"]
            bucket["recurring_count"] += 1
        else:
            bucket["exceptional_total"] += expense["amount"]
            bucket["exceptional_count"] += 1

    return {
        cur: {
            "available": True,
            "fixed_total": fixed_totals[cur],
            "recurring_total": totals[cur]["recurring_total"],
            "recurring_count": totals[cur]["recurring_count"],
            "exceptional_total": totals[cur]["exceptional_total"],
            "exceptional_count": totals[cur]["exceptional_count"],
            "variable_total": sum(
                (e["amount"] for e in all_variable if e.get("currency", "ARS") == cur),
                money.MONEY_ZERO,
            ),
        }
        for cur in _CURRENCIES
    }


def fingerprint(year: int, month: int) -> str:
    """SHA256 of the period's *local facts only* — this period's expenses (id, amount,
    category, subcategory, user, date, fixed-expense link) and dollar operations.
    Deliberately excludes derived values (averages, comparisons) so re-fingerprinting
    an unchanged period reproduces the same hash even months later, when trailing
    averages computed against it would have drifted for reasons that have nothing to
    do with that period's own data. Consumed by the drift badge shipping in the
    follow-up PR; computed here so no report generated now is missing a baseline."""
    expenses = db.get_expenses_for_period_art(year, month)
    expense_facts = sorted(
        (
            {
                "id": e["id"],
                "amount": e["amount"],
                "currency": e["currency"],
                "category_id": e["category_id"],
                "subcategory_id": e["subcategory_id"],
                "user_id": e["user_id"],
                "date": e["created_at"],
                "fixed_expense_id": e["fixed_expense_id"],
            }
            for e in expenses
        ),
        key=lambda d: d["id"],
    )
    cambio_facts = db.get_cambios_for_period(year, month)

    canonical = money.json_dumps(
        {"expenses": expense_facts, "cambios": cambio_facts},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_report_row(row: dict | None) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    result["dossier"] = json.loads(result.pop("dossier_json"))
    output_json = result.pop("output_json")
    result["output"] = json.loads(output_json) if output_json else None
    preferences_json = result.pop("preferences_json", None)
    result["preferences"] = json.loads(preferences_json) if preferences_json else None
    result["llm_ok"] = bool(result["llm_ok"])
    stored_forecast = db.get_report_forecast(result["id"])
    if stored_forecast is not None:
        stored_forecast["actuals"] = forecast_module.actuals(stored_forecast)
    result["forecast"] = stored_forecast
    return result


def get_report(year: int, month: int) -> dict | None:
    return _load_report_row(db.get_latest_report(year, month))


def get_latest_report_overall() -> dict | None:
    return _load_report_row(db.get_latest_report_overall())


def get_report_history(year: int, month: int) -> list[dict]:
    return db.get_report_history(year, month)
