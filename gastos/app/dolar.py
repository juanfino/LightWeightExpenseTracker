import json
import logging
import re

import anthropic
import llm_usage

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"

# Prefiltro barato: solo llamamos al LLM si el texto menciona dólares, para no
# gastar una llamada por cada mensaje de gasto normal.
_DOLAR_RE = re.compile(r"\b(d[oó]lar(?:es)?|usd|u\$s|u\$d)\b", re.IGNORECASE)

_PARSE_PROMPT = (
    "Analizá este mensaje. Determiná si describe una operación de compra o venta de dólares "
    "(por ejemplo: 'vendí 500 dólares a 1700', 'compré 1000 dólares a 1550 cada uno', "
    "'me pagaron 1700 pesos por cada dólar por 300 dólares'). "
    "Los números pueden estar escritos en palabras ('quinientos' → 500, 'mil quinientos cincuenta' → 1550) "
    "y en formato argentino (el punto es separador de miles y la coma es decimal). "
    "La 'cotizacion' es el precio en pesos de UN dólar. "
    "Si el mensaje NO es una operación de dólar (por ejemplo es un gasto común), respondé exactamente null.\n"
    "Si SÍ lo es, respondé ÚNICAMENTE con un JSON válido (sin explicaciones ni bloques de código):\n"
    '{"tipo": "venta" o "compra", '
    '"monto_usd": <cantidad de dólares como número>, '
    '"cotizacion": <precio en pesos de un dólar como número>, '
    '"confidence": <número entre 0 y 1 según qué tan seguro estés de haber entendido bien '
    "tipo, monto y cotización>}\n"
    "Usá confidence alto (>=0.9) solo cuando tipo, monto y cotización son claros e inequívocos."
)


def looks_like_dolar(text: str) -> bool:
    """True si el texto menciona dólares (prefiltro antes de llamar al LLM)."""
    return bool(_DOLAR_RE.search(text or ""))


def _parse_number(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if val > 0 else None
    try:
        val = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


def _parse_confidence(raw) -> float:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, val))


def parse_dolar(text: str, anthropic_api_key: str) -> dict | None:
    """Interpreta un mensaje en lenguaje natural como una operación de dólar.

    Returns {"tipo": "venta"|"compra", "monto_usd": float, "cotizacion": float,
             "confidence": float} o None si el texto no es una operación de dólar
    (o no se pudo interpretar).
    """
    try:
        client = anthropic.Anthropic(api_key=anthropic_api_key)
        call_started = llm_usage.started()
        message = client.messages.create(
            model=_MODEL,
            max_tokens=256,
            messages=[{
                "role": "user",
                "content": f"{_PARSE_PROMPT}\n\nMensaje: {text}",
            }],
        )
        llm_usage.record("dolar", _MODEL, call_started, response=message)
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw
        if raw.lower() == "null" or not raw:
            return None
        data = json.loads(raw)
    except Exception as e:
        if "call_started" in locals() and "message" not in locals():
            llm_usage.record("dolar", _MODEL, call_started, error=e)
        logger.error("Error interpretando operación de dólar: %s", e)
        return None

    if not isinstance(data, dict):
        return None

    tipo = (data.get("tipo") or "").strip().lower()
    if tipo not in ("venta", "compra"):
        return None

    monto_usd = _parse_number(data.get("monto_usd"))
    cotizacion = _parse_number(data.get("cotizacion"))
    if monto_usd is None or cotizacion is None:
        return None

    return {
        "tipo": tipo,
        "monto_usd": monto_usd,
        "cotizacion": cotizacion,
        "confidence": _parse_confidence(data.get("confidence")),
    }
