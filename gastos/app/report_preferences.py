"""Validation and prompt-safe resolution of per-family report preferences."""

import json


TONE_OPTIONS = ("neutral", "warm", "direct")
LENGTH_OPTIONS = ("short", "medium", "long")
EMPHASIS_OPTIONS = {
    "categories": "Categorías y sus movimientos",
    "comparisons": "Comparaciones con otros meses",
    "foreign_currency": "Gastos y posición en dólares",
    "fixed_expenses": "Gastos fijos",
    "outliers": "Gastos atípicos",
    "spending_mix": "Composición fijo, recurrente y excepcional",
    "forecast": "Proyección del mes siguiente",
}
MAX_FOCUS_LENGTH = 400

DEFAULTS = {
    "emphasis": [],
    "tone": "neutral",
    "length": "medium",
    "focus": "",
    "allow_suggestions": False,
}


class InvalidReportPreferences(ValueError):
    pass


def resolve(value: dict | None = None, *, strict: bool = False) -> dict:
    """Return a canonical complete preference dict.

    ``strict`` is used at the HTTP boundary. The tolerant mode is for legacy/missing
    rows and deliberately falls back field-by-field to today's behavior.
    """
    raw = value if isinstance(value, dict) else {}

    emphasis = raw.get("emphasis", DEFAULTS["emphasis"])
    if not isinstance(emphasis, list):
        if strict:
            raise InvalidReportPreferences("El énfasis debe ser una lista")
        emphasis = []
    invalid_emphasis = [item for item in emphasis if item not in EMPHASIS_OPTIONS]
    if invalid_emphasis and strict:
        raise InvalidReportPreferences("Hay opciones de énfasis inválidas")
    emphasis = sorted({item for item in emphasis if item in EMPHASIS_OPTIONS})

    tone = raw.get("tone", DEFAULTS["tone"])
    if tone not in TONE_OPTIONS:
        if strict:
            raise InvalidReportPreferences("El tono elegido no es válido")
        tone = DEFAULTS["tone"]

    length = raw.get("length", DEFAULTS["length"])
    if length not in LENGTH_OPTIONS:
        if strict:
            raise InvalidReportPreferences("La extensión elegida no es válida")
        length = DEFAULTS["length"]

    focus = raw.get("focus", DEFAULTS["focus"])
    if not isinstance(focus, str):
        if strict:
            raise InvalidReportPreferences("El foco debe ser texto")
        focus = ""
    focus = focus.strip()
    if len(focus) > MAX_FOCUS_LENGTH:
        if strict:
            raise InvalidReportPreferences(
                f"El foco puede tener hasta {MAX_FOCUS_LENGTH} caracteres"
            )
        focus = focus[:MAX_FOCUS_LENGTH]

    allow_suggestions = raw.get("allow_suggestions", DEFAULTS["allow_suggestions"])
    if not isinstance(allow_suggestions, bool):
        if strict:
            raise InvalidReportPreferences("La opción de sugerencias debe ser sí o no")
        allow_suggestions = DEFAULTS["allow_suggestions"]

    return {
        "emphasis": emphasis,
        "tone": tone,
        "length": length,
        "focus": focus,
        "allow_suggestions": allow_suggestions,
    }


def from_storage(row: dict | None) -> dict:
    if not row:
        return resolve()
    try:
        emphasis = json.loads(row.get("emphasis_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        emphasis = []
    return resolve({
        "emphasis": emphasis,
        "tone": row.get("tone"),
        "length": row.get("length"),
        "focus": row.get("focus"),
        "allow_suggestions": row.get("allow_suggestions"),
    })


def to_storage(preferences: dict) -> tuple[str, str, str, str, bool]:
    resolved = resolve(preferences, strict=True)
    return (
        json.dumps(resolved["emphasis"], ensure_ascii=False, separators=(",", ":")),
        resolved["tone"],
        resolved["length"],
        resolved["focus"],
        resolved["allow_suggestions"],
    )
