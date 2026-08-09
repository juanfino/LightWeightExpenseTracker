"""Natural-language extraction and display semantics for currency exchanges."""

import json
import logging
import re
from decimal import Decimal, InvalidOperation

import anthropic
import currency_detection
import llm_limits
import llm_usage
import money

logger = logging.getLogger(__name__)
_MODEL = "claude-haiku-4-5-20251001"


def looks_like_exchange(text: str, currencies: list[dict]) -> bool:
    if not currency_detection.mentions_currency(text, currencies):
        return False
    return bool(re.search(
        r"\b(?:vend[ií]|compr[eé]|cambi[eé]|convert[ií]|entregu[eé]|recib[ií]|me\s+dieron|"
        r"cotizaci[oó]n|por\s+cada)\b|(?:→|->)", text or "", re.IGNORECASE,
    ))


def _number(raw) -> Decimal | None:
    try:
        value = money.amount(raw)
        return value if value > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _confidence(raw) -> float:
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.0


def derived_trade_label(operation: dict, default_currency: str) -> str | None:
    """Buy/sell exists only when one side is the family's default currency."""
    if operation["currency_received"] == default_currency and operation["currency_given"] != default_currency:
        return "venta"
    if operation["currency_given"] == default_currency and operation["currency_received"] != default_currency:
        return "compra"
    return None


def parse_exchange(text: str, anthropic_api_key: str, currencies: list[dict], default_currency: str) -> dict | None:
    codes = [row["code"] for row in currencies]
    prompt = (
        "Analizá si el mensaje describe una conversión de moneda: una cantidad entregada en una "
        "moneda y otra recibida en otra. No clasifiques la operación como compra o venta. "
        f"Monedas admitidas: {', '.join(codes)}. Moneda por defecto: {default_currency}. "
        "'$' y 'pesos' sin más detalle significan la moneda por defecto. Los números pueden estar "
        "escritos en palabras o en formato argentino. Si se da una tasa y un solo monto, calculá el "
        "otro monto respetando la dirección. Si no es una conversión, respondé null. Si lo es, "
        "respondé solo JSON: "
        '{"amount_given": número, "currency_given": "código", "amount_received": número, '
        '"currency_received": "código", "confidence": número entre 0 y 1}. '
        "Usá confianza >=0.9 solo si dirección, monedas y ambos montos son inequívocos.\n\n"
        f"Mensaje: {text}"
    )
    try:
        client = anthropic.Anthropic(api_key=anthropic_api_key, timeout=15.0, max_retries=0)
        started = llm_usage.started()
        with llm_limits.routine_call():
            message = client.messages.create(
                model=_MODEL, max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
        llm_usage.record("exchange", _MODEL, started, response=message)
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw
        if not raw or raw.lower() == "null":
            return None
        data = json.loads(raw)
        given_currency = str(data.get("currency_given", "")).upper()
        received_currency = str(data.get("currency_received", "")).upper()
        if given_currency not in codes or received_currency not in codes or given_currency == received_currency:
            return None
        amount_given = _number(data.get("amount_given"))
        amount_received = _number(data.get("amount_received"))
        if amount_given is None or amount_received is None:
            return None
        return {
            "amount_given": amount_given,
            "currency_given": given_currency,
            "amount_received": amount_received,
            "currency_received": received_currency,
            "rate_received_per_given": amount_received / amount_given,
            "confidence": _confidence(data.get("confidence")),
        }
    except Exception as exc:
        if "started" in locals() and "message" not in locals():
            llm_usage.record("exchange", _MODEL, started, error=exc)
        logger.error("Error interpretando cambio de moneda: %s", exc)
        return None
