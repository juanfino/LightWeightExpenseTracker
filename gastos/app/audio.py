import io
import json
import logging

import anthropic
import openai

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"
_WHISPER_PROMPT = "Gastos en pesos argentinos: 10000, 148900, 5000."

_EXTRACT_PROMPT = (
    "Analizá esta transcripción de un mensaje de voz sobre uno o varios gastos. "
    "Identificá cada gasto mencionado y convertí los números escritos en español a dígitos "
    "(ej: 'diez mil' → 10000, 'quinientos' → 500, 'tres mil' → 3000). "
    "Manejá expresiones coloquiales argentinas (ej: 'pesos', 'lucas', 'guita', 'mangos'). "
    "El usuario puede mencionar varios gastos en una sola oración, "
    "ej: 'gasté mil en la verdulería, tres mil en la ferretería y quinientos en nafta'. "
    "Si un gasto mencionado no tiene monto claro, incluilo igual con amount null. "
    "Respondé ÚNICAMENTE con un array JSON válido sin explicaciones ni bloques de código:\n"
    '[{"concept": "<nombre del comercio o concepto, capitalizado>", '
    '"amount": <monto como número float, o null si no se detecta>}, ...]'
)


def _parse_amount(raw) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def transcribe_and_extract(audio_bytes: bytes, openai_api_key: str, anthropic_api_key: str) -> dict:
    """Transcribe voice audio and extract one or more expenses.

    Returns:
        {
            "transcription": str,
            "expenses": [{"concept": str, "amount": float | None}, ...]
        }
    Raises RuntimeError on transcription or extraction failure.
    """
    oa_client = openai.OpenAI(api_key=openai_api_key)
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = "voice.ogg"

    try:
        transcript = oa_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="es",
            prompt=_WHISPER_PROMPT,
        )
        transcription = transcript.text.strip()
    except Exception as e:
        raise RuntimeError(f"Error en transcripción de audio: {e}") from e

    if not transcription:
        raise RuntimeError("Whisper devolvió una transcripción vacía")

    logger.info("Transcripción Whisper: %s", transcription)

    try:
        an_client = anthropic.Anthropic(api_key=anthropic_api_key)
        message = an_client.messages.create(
            model=_MODEL,
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": f"{_EXTRACT_PROMPT}\n\nTranscripción: {transcription}",
            }],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw
        data = json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"Error al extraer datos del gasto: {e}") from e

    if not isinstance(data, list):
        raise RuntimeError(f"Claude devolvió formato inesperado (esperaba array): {data!r}")

    expenses = [
        {
            "concept": (item.get("concept") or "").strip() or "Desconocido",
            "amount": _parse_amount(item.get("amount")),
        }
        for item in data
        if isinstance(item, dict)
    ]

    if not expenses:
        raise RuntimeError("Claude no detectó ningún gasto en la transcripción")

    return {
        "transcription": transcription,
        "expenses": expenses,
    }
