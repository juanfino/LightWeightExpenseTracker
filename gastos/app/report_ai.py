"""The two narrow LLM calls behind the monthly report: classification and narration.

Both calls receive pre-computed aggregates from dossier.py and never do arithmetic
themselves — the classification call returns a label per expense (no sums), and the
analysis call narrates over a fixed/recurring/exceptional partition code has already
computed. Configured separately from the extraction model used by
intent.py/ocr.py/audio.py (Haiku) via its own env var: this runs a handful of times a
year and quality matters far more than cost, the opposite tradeoff of those.

Performs no DB I/O and no Telegram/Flask I/O — report.py owns persistence and
orchestration; this module only talks to the Anthropic API.
"""

import json
import logging
import os

import anthropic

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-opus-4-8"
_TIMEOUT_S = 120.0

_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "classifications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "expense_id": {"type": "integer"},
                    "label": {"type": "string", "enum": ["recurring", "exceptional"]},
                    "confidence": {"type": "number"},
                },
                "required": ["expense_id", "label", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["classifications"],
    "additionalProperties": False,
}

_ANALYZE_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        },
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["uncategorized", "unlinked_fixed", "other"]},
                    "text": {"type": "string"},
                    "fixed_expense_id": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                },
                "required": ["type", "text", "fixed_expense_id"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "summary", "findings", "questions"],
    "additionalProperties": False,
}

_CLASSIFY_SYSTEM = (
    "You classify this month's variable (non-fixed) household expenses into exactly "
    "one of two labels: \"recurring\" (regular behavior — groceries, the usual "
    "outings, subscriptions) or \"exceptional\" (one-off — a repair, an appliance, an "
    "unusual purchase). Every expense gets exactly one label plus a confidence between "
    "0 and 1.\n\n"
    "You have two signals, and how much to weigh each depends on how much history is "
    "available:\n"
    "1. World knowledge — often decisive on its own (a washing machine purchase is "
    "exceptional even the first time you see one; a greengrocer is recurring even on "
    "first appearance).\n"
    "2. Empirical recurrence evidence — how many of the available months this exact "
    "concept appeared in, and at what amounts. With little history, lean on world "
    "knowledge; with several months of evidence, lean on the empirical pattern.\n\n"
    "For cross-month consistency, you're shown how the same or similar concepts were "
    "classified in prior months. Prefer staying consistent unless the amount or context "
    "clearly indicates something changed (same store, a wildly different amount -> "
    "probably a genuinely different kind of purchase, not the same recurring one).\n\n"
    "Return ONLY classifications. Do not narrate, do not sum amounts, do not analyze "
    "the month — a separate step aggregates your labels."
)

_ANALYZE_SYSTEM = (
    "You write the monthly findings for a household expense report, in Rioplatense "
    "Spanish. Every number in the input \"dossier\" is pre-computed by code — you "
    "narrate, you never calculate, and you never contradict a number you were given.\n\n"
    "Rules:\n"
    "- Every finding must cite a concrete figure from the dossier (an amount, a "
    "percentage, a count). A finding with no number is not acceptable — never write "
    "generic advice like 'gastaste mucho en X, planificá mejor.'\n"
    "- Few findings: five good ones beat twelve padded ones.\n"
    "- No recommendations. This app has no budgets, so there is no target to advise "
    "against — never suggest what the user should do differently.\n"
    "- questions[] is for actionable observations that need a human decision, not "
    "advice. Use type=\"uncategorized\" for the aggregate uncategorized-spending "
    "question (fixed_expense_id null), type=\"unlinked_fixed\" once per unpaid/"
    "unlinked fixed expense you want to flag (set fixed_expense_id to its id from the "
    "dossier), type=\"other\" for anything else worth flagging that doesn't map to "
    "either link (fixed_expense_id null). Never invent a fixed_expense_id that isn't "
    "in the dossier.\n"
    "- If hard_facts.months_available is small, present every contrast as an "
    "observation ('recién es el N-ésimo mes de datos') rather than a conclusion about "
    "behavior change — recent app adoption explains differences at least as well as "
    "real spending changes, and you must say so explicitly when history is short.\n"
    "- registration_coverage measures who LOGGED an expense, not who spent the money — "
    "never infer spending behavior, fairness, or effort from it; if you mention it, "
    "describe it only as app usage.\n"
    "- The fixed/recurring/exceptional partition is already computed — cite it, don't "
    "recompute or contradict it.\n"
    "- Do not invent URLs, links, category names, or ids beyond what's in the input."
)


def model_name() -> str:
    return os.environ.get("REPORT_ANTHROPIC_MODEL", _DEFAULT_MODEL)


def _client() -> anthropic.Anthropic | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return anthropic.Anthropic(api_key=api_key, timeout=_TIMEOUT_S, max_retries=1)


def _first_text(message) -> str | None:
    for block in message.content:
        if getattr(block, "type", None) == "text":
            return block.text
    return None


def classify_expenses(dossier: dict, prior_classifications: list[dict]) -> list[dict] | None:
    """Returns [{expense_id, label, confidence}] for the dossier's variable expenses,
    or None if the call failed (report.py degrades to a dossier-only report)."""
    variable = dossier["variable_expenses"]
    if not variable:
        return []

    client = _client()
    if client is None:
        return None

    prior_lines = [
        f"- {c['year']:04d}-{c['month']:02d}: \"{c['concept']}\" (${c['amount']:.0f}) -> {c['label']}"
        for c in prior_classifications
    ]
    payload = {
        "expenses": variable,
        "recurrence_evidence": dossier["recurrence_evidence"],
        "hard_facts": dossier["hard_facts"],
        "prior_months_classifications": prior_lines,
    }

    try:
        message = client.messages.create(
            model=model_name(),
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "high",
                "format": {"type": "json_schema", "schema": _CLASSIFY_SCHEMA},
            },
            system=_CLASSIFY_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
        )
    except Exception as e:
        logger.error("Error en clasificación de gastos del resumen: %s", e)
        return None

    text = _first_text(message)
    if text is None:
        logger.error("Clasificación del resumen sin bloque de texto en la respuesta")
        return None
    try:
        return json.loads(text)["classifications"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error("Respuesta de clasificación del resumen inválida: %s", e)
        return None


def analyze(dossier: dict, partition: dict) -> dict | None:
    """Returns {headline, summary, findings, questions}, or None if the call failed
    (report.py still persists the dossier-only report with llm_ok=False)."""
    client = _client()
    if client is None:
        return None

    payload = {"dossier": dossier, "partition": partition}

    try:
        message = client.messages.create(
            model=model_name(),
            max_tokens=8000,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "high",
                "format": {"type": "json_schema", "schema": _ANALYZE_SCHEMA},
            },
            system=_ANALYZE_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
        )
    except Exception as e:
        logger.error("Error en análisis del resumen: %s", e)
        return None

    text = _first_text(message)
    if text is None:
        logger.error("Análisis del resumen sin bloque de texto en la respuesta")
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("Respuesta de análisis del resumen inválida: %s", e)
        return None
