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

import hashlib
import json
import logging
import os
from functools import cache

import anthropic
import llm_usage
import llm_limits
import money

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
    "Expenses come in two currencies, ARS and USD, each carrying its own 'currency' "
    "field. Never compare or weigh amounts across currencies — a USD amount has fewer "
    "digits than an ARS one but is not therefore small. Recurrence evidence is also "
    "currency-scoped: a concept's prior occurrences only count if they're in the same "
    "currency as the expense you're classifying.\n\n"
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
    "dossier — fixed expenses exist in both currencies.ARS.fixed_expenses and "
    "currencies.USD.fixed_expenses, and ids are unique across both), type=\"other\" for "
    "anything else worth flagging that doesn't map to either link (fixed_expense_id "
    "null). Never invent a fixed_expense_id that isn't in the dossier.\n"
    "- If hard_facts.months_available is small, present every contrast as an "
    "observation ('recién es el N-ésimo mes de datos') rather than a conclusion about "
    "behavior change — recent app adoption explains differences at least as well as "
    "real spending changes, and you must say so explicitly when history is short. Each "
    "currency block also carries its own months_available — a currency with little "
    "history of its own gets the same treatment even if the other currency has plenty.\n"
    "- registration_coverage measures who LOGGED an expense, not who spent the money — "
    "never infer spending behavior, fairness, or effort from it; if you mention it, "
    "describe it only as app usage.\n"
    "- The fixed/recurring/exceptional partition is already computed per currency "
    "(currencies.ARS.partition, currencies.USD.partition) — cite it, don't recompute "
    "or contradict it.\n"
    "- Do not invent URLs, links, category names, or ids beyond what's in the input.\n\n"
    "Currency rules — this dossier covers two independent currencies, ARS and USD, as "
    "parallel same-shape blocks under \"currencies\". This is not a pesos report with a "
    "dollar footnote: both currencies are first-class and both deserve findings when "
    "material.\n"
    "- NEVER sum, average, or otherwise combine an ARS amount with a USD amount. Each "
    "currency's totals, contrasts, and partition are self-contained.\n"
    "- Every amount you write carries its currency's symbol explicitly: \"$\" for ARS, "
    "\"U$S\" for USD. A bare number with no symbol is not acceptable.\n"
    "- IPC/inflation ('real' figures) only exists for ARS. currencies.USD.contrasts "
    "entries carry real_not_applicable instead of real figures — never call a USD "
    "amount 'real' or inflation-adjusted, and never explain a USD change using IPC.\n"
    "- \"equivalence\" is a reference-only ARS-equivalent of this month's USD spending, "
    "at the family's own dollar rate (equivalence.rate, from equivalence.rate_source). "
    "You may cite it AT MOST ONCE, only to convey how large the USD spending was in "
    "terms the reader already thinks in, and always labeled as an approximation that "
    "names the rate used. Never treat it as a total, never feed it into a contrast, and "
    "never use it as if it were a real peso expense.\n"
    "- Materiality rule: if equivalence.usd_share_pct >= 10, or if there is USD "
    "spending but equivalence.available is false, the headline and summary MUST "
    "mention the USD spending explicitly (its own total, in U$S, plus a finding about "
    "it) — a large or unconvertible dollar expense cannot be relegated to a minor "
    "finding or omitted. Below that threshold, mention USD only if there's something "
    "specific worth noting (e.g. a new recurring USD subscription)."
)

_CLASSIFY_CALL_CONFIG = {
    "max_tokens": 16000,
    "thinking": {"type": "adaptive"},
    "output_config": {
        "effort": "high",
        "format": {"type": "json_schema", "schema": _CLASSIFY_SCHEMA},
    },
    "system": _CLASSIFY_SYSTEM,
}

_ANALYZE_CALL_CONFIG = {
    "max_tokens": 8000,
    "thinking": {"type": "adaptive"},
    "output_config": {
        "effort": "high",
        "format": {"type": "json_schema", "schema": _ANALYZE_SCHEMA},
    },
    "system": _ANALYZE_SYSTEM,
}


def _derive_prompt_version(classify_config: dict, analyze_config: dict) -> str:
    """Return a stable identifier for the response-shaping report configuration.

    The model and user payload are deliberately absent: the former is persisted in
    reports.model, while the latter is the report's input rather than prompt config.
    """
    canonical = json.dumps(
        {"classify": classify_config, "analyze": analyze_config},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"report-v1-{digest[:12]}:sha256:{digest}"


@cache
def prompt_version() -> str:
    """Fingerprint the exact system prompts, schemas and response parameters."""
    return _derive_prompt_version(_CLASSIFY_CALL_CONFIG, _ANALYZE_CALL_CONFIG)


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


def classify_expenses(dossier: dict, variable: list[dict], prior_classifications: list[dict]) -> list[dict] | None:
    """Returns [{expense_id, label, confidence}] for the given variable expenses
    (both currencies, each item already carrying its own "currency"), or None if the
    call failed (report.py degrades to a dossier-only report)."""
    if not variable:
        return []

    client = _client()
    if client is None:
        return None

    prior_lines = [
        f"- {c['year']:04d}-{c['month']:02d}: \"{c['concept']}\" "
        f"({'U$S' if c.get('currency') == 'USD' else '$'}{c['amount']:.0f}) -> {c['label']}"
        for c in prior_classifications
    ]
    payload = {
        "expenses": variable,
        "recurrence_evidence": dossier["recurrence_evidence"],
        "hard_facts": dossier["hard_facts"],
        "prior_months_classifications": prior_lines,
    }

    try:
        call_started = llm_usage.started()
        with llm_limits.summary_call():
            message = client.messages.create(
                model=model_name(),
                messages=[{"role": "user", "content": money.json_dumps(payload, ensure_ascii=False)}],
                **_CLASSIFY_CALL_CONFIG,
            )
        llm_usage.record("resumen", model_name(), call_started, response=message)
    except Exception as e:
        llm_usage.record("resumen", model_name(), call_started, error=e)
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


def analyze(dossier: dict) -> dict | None:
    """Returns {headline, summary, findings, questions}, or None if the call failed
    (report.py still persists the dossier-only report with llm_ok=False). The
    fixed/recurring/exceptional partition is already embedded per currency at
    dossier["currencies"][cur]["partition"] by the time this is called."""
    client = _client()
    if client is None:
        return None

    payload = {"dossier": dossier}

    try:
        call_started = llm_usage.started()
        with llm_limits.summary_call():
            message = client.messages.create(
                model=model_name(),
                messages=[{"role": "user", "content": money.json_dumps(payload, ensure_ascii=False)}],
                **_ANALYZE_CALL_CONFIG,
            )
        llm_usage.record("resumen", model_name(), call_started, response=message)
    except Exception as e:
        llm_usage.record("resumen", model_name(), call_started, error=e)
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
