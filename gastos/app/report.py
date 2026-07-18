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
import inflation
import report_ai

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "1"
_CLASSIFICATION_LOOKBACK_MONTHS = 6


def generate_report(year: int, month: int) -> dict:
    """Builds and persists a new report for (year, month). Always returns the
    persisted, parsed report — even when the LLM calls fail — so the caller always
    has fixed-section data to render."""
    inflation.refresh()
    dossier = dossier_module.build_dossier(year, month)

    prior_classifications = db.get_recent_classifications_before(
        year, month, lookback_months=_CLASSIFICATION_LOOKBACK_MONTHS
    )
    classifications = report_ai.classify_expenses(dossier, prior_classifications)

    partition = _build_partition(dossier, classifications)
    dossier["partition"] = partition

    output = None
    if classifications is not None:
        output = report_ai.analyze(dossier, partition)
    llm_ok = output is not None

    fp = fingerprint(year, month)
    report_id = db.create_report(
        year=year,
        month=month,
        model=report_ai.model_name(),
        prompt_version=_PROMPT_VERSION,
        dossier_json=json.dumps(dossier, ensure_ascii=False, default=str),
        output_json=json.dumps(output, ensure_ascii=False) if output is not None else None,
        fingerprint=fp,
        llm_ok=llm_ok,
    )

    if classifications:
        by_id = {e["expense_id"]: e for e in dossier["variable_expenses"]}
        rows = [
            {
                "expense_id": c["expense_id"],
                "concept": by_id[c["expense_id"]]["concept"],
                "amount": by_id[c["expense_id"]]["amount"],
                "label": c["label"],
                "confidence": c.get("confidence"),
            }
            for c in classifications
            if c["expense_id"] in by_id
        ]
        if rows:
            db.save_classifications(report_id, rows)

    return get_report(year, month)


def _build_partition(dossier: dict, classifications: list[dict] | None) -> dict:
    """Aggregates the model's per-expense labels into the three-way split — the one
    piece of arithmetic the classification call itself never does."""
    fixed_total = dossier["fixed_expenses"]["total_paid"]

    if classifications is None:
        return {"available": False, "fixed_total": fixed_total}

    by_id = {e["expense_id"]: e for e in dossier["variable_expenses"]}
    recurring_total = exceptional_total = 0.0
    recurring_count = exceptional_count = 0
    for c in classifications:
        expense = by_id.get(c["expense_id"])
        if expense is None:
            continue
        if c["label"] == "recurring":
            recurring_total += expense["amount"]
            recurring_count += 1
        else:
            exceptional_total += expense["amount"]
            exceptional_count += 1

    return {
        "available": True,
        "fixed_total": fixed_total,
        "recurring_total": recurring_total,
        "recurring_count": recurring_count,
        "exceptional_total": exceptional_total,
        "exceptional_count": exceptional_count,
        "variable_total": sum(e["amount"] for e in dossier["variable_expenses"]),
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

    canonical = json.dumps(
        {"expenses": expense_facts, "cambios": cambio_facts},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_report_row(row: dict | None) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    result["dossier"] = json.loads(result.pop("dossier_json"))
    output_json = result.pop("output_json")
    result["output"] = json.loads(output_json) if output_json else None
    result["llm_ok"] = bool(result["llm_ok"])
    return result


def get_report(year: int, month: int) -> dict | None:
    return _load_report_row(db.get_latest_report(year, month))


def get_latest_report_overall() -> dict | None:
    return _load_report_row(db.get_latest_report_overall())


def get_report_history(year: int, month: int) -> list[dict]:
    return db.get_report_history(year, month)
