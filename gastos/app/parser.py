import re
from decimal import Decimal, InvalidOperation

import money
import currency_detection


# Matches a number that may use:
#   - dot as thousands separator + comma as decimal  → 2.500,50
#   - dot as thousands separator only                → 100.000
#   - comma as thousands separator + dot as decimal  → 2,500.50  (less common but accepted)
#   - plain integer or decimal                       → 1500 / 1500.50
_NUMBER_RE = re.compile(
    r"\b(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\b"
)

def _normalize_amount(raw: str) -> Decimal | None:
    """
    Convierte un string de número local a Decimal monetario.

    Casos:
      "100.000"   → 100000.0   (punto de miles)
      "2.500,50"  → 2500.5     (punto de miles + coma decimal)
      "2,500.50"  → 2500.5     (coma de miles + punto decimal)
      "1500"      → 1500.0
      "1500.50"   → 1500.5
      "1500,50"   → 1500.5
    """
    s = raw.strip()

    dot_count   = s.count(".")
    comma_count = s.count(",")

    if dot_count == 0 and comma_count == 0:
        # plain integer
        try:
            value = money.amount(s)
            return value if value > 0 else None
        except (InvalidOperation, ValueError, TypeError):
            return None

    if dot_count > 1:
        # multiple dots → all dots are thousands separators, last comma (if any) is decimal
        s = s.replace(".", "")
        s = s.replace(",", ".")
    elif comma_count > 1:
        # multiple commas → all commas are thousands separators, last dot (if any) is decimal
        s = s.replace(",", "")
    elif dot_count == 1 and comma_count == 1:
        # both present: whichever comes last is the decimal separator
        if s.rindex(".") > s.rindex(","):
            # format: 2,500.50
            s = s.replace(",", "")
        else:
            # format: 2.500,50
            s = s.replace(".", "")
            s = s.replace(",", ".")
    elif dot_count == 1 and comma_count == 0:
        # could be thousands (100.000) or decimal (1500.50)
        integer_part, frac_part = s.split(".")
        if len(frac_part) == 3:
            # ambiguous: treat as thousands separator (Argentine convention)
            s = s.replace(".", "")
        # else: leave as-is (standard decimal)
    elif comma_count == 1 and dot_count == 0:
        integer_part, frac_part = s.split(",")
        if len(frac_part) == 3:
            # ambiguous: treat as thousands separator (e.g. 100,000)
            s = s.replace(",", "")
        else:
            # Argentine decimal comma: 1500,50
            s = s.replace(",", ".")

    try:
        value = money.amount(s)
        return value if value > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def parse_message(text: str, currencies: list[dict],
                  default_currency: str | None = None) -> dict | None:
    """
    Parsea un mensaje de texto en {concept, amount}.

    Formatos soportados:
      "Supermercado 150000"     → concept=Supermercado,    amount=150000
      "150000 nafta"            → concept=Nafta,           amount=150000
      "Cena cumpleaños 5000"    → concept=Cena cumpleaños, amount=5000
      "YPF 100.000"             → concept=YPF,             amount=100000
      "farmacia 2500,50"        → concept=Farmacia,        amount=2500.5

    Retorna None si no se puede extraer un monto válido.
    """
    text = text.strip()
    catalogue = currencies
    default_currency = default_currency or catalogue[0]["code"]
    currency, text, _ = currency_detection.detect_and_strip(text, catalogue, default_currency)
    matches = list(_NUMBER_RE.finditer(text))
    if not matches:
        return None

    # Try last token as amount (concept + amount format)
    last = matches[-1]
    amount = _normalize_amount(last.group())
    if amount is not None:
        concept = text[: last.start()].strip()
        if not concept and len(matches) > 1:
            # number at end but no text before → try first match as amount
            pass
        elif concept:
            return {"concept": concept.title(), "amount": amount, "currency": currency}

    # Try first token as amount (amount + concept format)
    first = matches[0]
    amount = _normalize_amount(first.group())
    if amount is not None:
        concept = text[first.end() :].strip()
        if concept:
            return {"concept": concept.title(), "amount": amount, "currency": currency}

    # Single token that is a number → no concept, reject
    return None


# ── Tests inline ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cases = [
        ("Supermercado 150000",   {"concept": "Supermercado",      "amount": 150000.0, "currency": "ARS"}),
        ("150000 nafta",          {"concept": "Nafta",             "amount": 150000.0, "currency": "ARS"}),
        ("Cena cumpleaños 5000",  {"concept": "Cena Cumpleaños",   "amount": 5000.0, "currency": "ARS"}),
        ("YPF 100.000",           {"concept": "Ypf",               "amount": 100000.0, "currency": "ARS"}),
        ("YPF 100,000",           {"concept": "Ypf",               "amount": 100000.0, "currency": "ARS"}),
        ("farmacia 2500.50",      {"concept": "Farmacia",          "amount": 2500.5, "currency": "ARS"}),
        ("farmacia 2500,50",      {"concept": "Farmacia",          "amount": 2500.5, "currency": "ARS"}),
        ("super 2.500,50",        {"concept": "Super",             "amount": 2500.5, "currency": "ARS"}),
        ("Netflix 15 USD",        {"concept": "Netflix",           "amount": 15.0, "currency": "USD"}),
        ("Netflix US$ 15",        {"concept": "Netflix",           "amount": 15.0, "currency": "USD"}),
        ("Netflix 15 U$S",        {"concept": "Netflix",           "amount": 15.0, "currency": "USD"}),
        ("Hotel 200 dólares",     {"concept": "Hotel",             "amount": 200.0, "currency": "USD"}),
        ("Doméstica 35000",       {"concept": "Doméstica",         "amount": 35000.0, "currency": "ARS"}),
        ("Domestica 35000",       {"concept": "Domestica",         "amount": 35000.0, "currency": "ARS"}),
        ("Domestica 35.000",      {"concept": "Domestica",         "amount": 35000.0, "currency": "ARS"}),
        ("solo texto",            None),
        ("123456",                None),
    ]

    catalogue = [
        {"code": "ARS", "symbol": "$"}, {"code": "USD", "symbol": "US$"},
        {"code": "BRL", "symbol": "R$"}, {"code": "EUR", "symbol": "€"},
    ]
    passed = 0
    for msg, expected in cases:
        result = parse_message(msg, catalogue, "ARS")
        ok = result == expected
        status = "✅" if ok else "❌"
        print(f"{status} '{msg}'")
        if not ok:
            print(f"   esperado: {expected}")
            print(f"   obtenido: {result}")
        else:
            passed += 1

    print(f"\n{passed}/{len(cases)} tests pasaron")
