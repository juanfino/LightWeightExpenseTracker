import base64
import json
import logging
from decimal import Decimal, InvalidOperation
from datetime import date

import anthropic
import llm_usage
import llm_limits
import money

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"

_PROMPT = (
    "Analizá este ticket o comprobante de compra. "
    "Los montos usan formato argentino: el punto . es separador de miles y la coma , es separador decimal. "
    "Ejemplo: $5.580,00 equivale a 5580.00. Devolvé el monto siempre como número float sin separadores de miles. "
    "Respondé ÚNICAMENTE con un JSON válido (sin explicaciones ni bloques de código) con estos campos:\n"
    '{"comercio": "<nombre del comercio o null>", '
    '"monto": <monto total final en números o null>, '
    '"fecha": "<fecha en formato YYYY-MM-DD o null>"}\n'
    "El monto es el total final pagado después de descuentos, no subtotales. "
    "Si no podés determinar un valor, usá null."
)


def _parse_monto(raw) -> Decimal | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        try:
            return money.amount(raw)
        except (InvalidOperation, ValueError, TypeError):
            return None
    s = str(raw).strip().replace(" ", "")
    if "," in s and "." in s:
        if s.index(",") > s.index("."):
            # Formato argentino: 5.580,00
            s = s.replace(".", "").replace(",", ".")
        else:
            # Formato USA: 5,580.00
            s = s.replace(",", "")
    elif "," in s:
        # Sin separador de miles: 5580,00
        s = s.replace(",", ".")
    try:
        return money.amount(s)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _detect_media_type(image_bytes: bytes) -> str:
    if image_bytes[:2] == b"\xff\xd8":
        return "image/jpeg"
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:4] in (b"GIF8", b"GIF9"):
        return "image/gif"
    return "image/jpeg"


def extract_ticket_data(image_bytes: bytes, api_key: str) -> dict | None:
    try:
        client = anthropic.Anthropic(api_key=api_key)
        image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        media_type = _detect_media_type(image_bytes)

        call_started = llm_usage.started()
        try:
            with llm_limits.routine_call():
                message = client.messages.create(
                    model=_MODEL, max_tokens=256, messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": _PROMPT},
                        ],
                    }
                    ],
                )
        except Exception as e:
            llm_usage.record("ocr", _MODEL, call_started, error=e)
            raise
        llm_usage.record("ocr", _MODEL, call_started, response=message)

        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw

        data = json.loads(raw)

        result: dict = {
            "comercio": data.get("comercio"),
            "monto": _parse_monto(data.get("monto")),
            "fecha": date.today(),
            "fecha_inferida": True,
        }

        raw_fecha = data.get("fecha")
        if raw_fecha:
            try:
                result["fecha"] = date.fromisoformat(raw_fecha)
                result["fecha_inferida"] = False
            except ValueError:
                pass

        return result

    except Exception as e:
        logger.error("Error en OCR de ticket: %s", e)
        return None
