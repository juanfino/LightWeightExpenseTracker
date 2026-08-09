"""Shared, cheap currency detection for every Telegram input surface."""

import re


class UnknownCurrencyError(ValueError):
    def __init__(self, token: str):
        self.token = token.upper()
        super().__init__(f"Moneda no reconocida: {self.token}")


_ALIASES = {
    "USD": (r"d[oó]lares?", r"dollars?", r"u\$s", r"us\$"),
    "EUR": (r"euros?",),
    "BRL": (r"reales?", r"real(?:es)?\s+brasile[ñn]os?"),
    "ARS": (r"pesos?\s+argentinos?",),
}
_AMBIGUOUS_DEFAULT = re.compile(r"(?<!\w)(?:\$|pesos?)(?!\w)", re.IGNORECASE)
_ISO_TOKEN = re.compile(r"(?<!\w)([A-Za-z]{3})(?!\w)")


def catalogue_prompt(currencies: list[dict], default_currency: str) -> str:
    codes = ", ".join(row["code"] for row in currencies)
    return (
        f"Monedas admitidas: {codes}. La moneda por defecto familiar es {default_currency}. "
        "Si el mensaje no nombra otra moneda, o usa solamente '$' o 'pesos', usá la moneda "
        "por defecto. Un código ISO o símbolo específico explícito tiene prioridad."
    )


def detect_and_strip(text: str, currencies: list[dict], default_currency: str) -> tuple[str, str, bool]:
    """Return ``(currency, cleaned_text, explicit)`` without database access.

    Codes and catalogue symbols are dynamic. Colloquial aliases live in this single
    module, so all input surfaces resolve the same signal in the same way.
    """
    supported = {row["code"].upper(): row for row in currencies}
    default = default_currency.upper()
    matches: list[tuple[int, int, str, str]] = []

    for match in _ISO_TOKEN.finditer(text):
        token = match.group(1).upper()
        if token in supported:
            matches.append((match.start(), match.end(), token, match.group(0)))
        # An unknown all-caps suffix after an amount is almost certainly an
        # attempted ISO currency ("Hotel 200 XYZ"). Before the amount it may be
        # a merchant acronym ("YPF 200"), so it remains part of the concept.
        elif match.group(1).isupper() and re.search(r"\d\s*$", text[:match.start()]):
            raise UnknownCurrencyError(token)

    for code, row in supported.items():
        symbol = row.get("symbol") or ""
        if symbol and symbol != "$":
            for match in re.finditer(re.escape(symbol), text, re.IGNORECASE):
                matches.append((match.start(), match.end(), code, match.group(0)))
        for alias in _ALIASES.get(code, ()):
            for match in re.finditer(rf"(?<!\w)(?:{alias})(?!\w)", text, re.IGNORECASE):
                matches.append((match.start(), match.end(), code, match.group(0)))

    specific = {item[2] for item in matches}
    if len(specific) > 1:
        raise ValueError("El mensaje menciona más de una moneda")

    ambiguous = list(_AMBIGUOUS_DEFAULT.finditer(text))
    currency = next(iter(specific), default)
    spans = [(a, b) for a, b, _, _ in matches]
    if not specific:
        spans.extend((m.start(), m.end()) for m in ambiguous)

    cleaned = text
    for start, end in sorted(set(spans), reverse=True):
        cleaned = cleaned[:start] + " " + cleaned[end:]
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return currency, cleaned, bool(matches or ambiguous)


def mentions_currency(text: str, currencies: list[dict]) -> bool:
    """Cheap exchange prefilter: no LLM, DB query or number-only trigger."""
    try:
        _, _, explicit = detect_and_strip(text or "", currencies, currencies[0]["code"])
        return explicit
    except (UnknownCurrencyError, ValueError):
        return True
