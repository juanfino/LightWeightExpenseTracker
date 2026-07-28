import io
import json
import logging

import anthropic
import openai
import llm_usage
import llm_limits

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"
_WHISPER_PROMPT = "Gastos en pesos argentinos o dólares: 10000, 148900, 5000, 15 USD."

_EXTRACT_PROMPT = (
    "Analizá esta transcripción de un mensaje de voz sobre uno o varios gastos. "
    "Identificá cada gasto mencionado y convertí los números escritos en español a dígitos "
    "(ej: 'diez mil' → 10000, 'quinientos' → 500, 'tres mil' → 3000). "
    "Manejá expresiones coloquiales argentinas (ej: 'pesos', 'lucas', 'guita', 'mangos') y "
    "detectá USD cuando diga 'dólares', 'USD', 'US$' o 'U$S'; si no se aclara, usá ARS. "
    "El usuario puede mencionar varios gastos en una sola oración, "
    "ej: 'gasté mil en la verdulería, tres mil en la ferretería y quinientos en nafta'. "
    "Si un gasto mencionado no tiene monto claro, incluilo igual con amount null. "
    "Para cada gasto agregá un campo 'confidence' (número entre 0 y 1) que indique qué tan "
    "seguro estás de haber entendido bien el concepto y el monto: usá valores altos (>=0.9) "
    "solo cuando el concepto y el monto son claros e inequívocos, y valores más bajos cuando "
    "la transcripción es ambigua, confusa o el monto es dudoso.\n"
    "Respondé ÚNICAMENTE con un array JSON válido sin explicaciones ni bloques de código:\n"
    '[{"concept": "<nombre del comercio o concepto, capitalizado>", '
    '"amount": <monto como número float, o null si no se detecta>, "currency": "ARS" o "USD", '
    '"confidence": <número entre 0 y 1>}, ...]'
)


def _parse_amount(raw) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_confidence(raw) -> float:
    """Confianza en [0, 1]; ante cualquier duda devuelve 0.0 para forzar confirmación."""
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, val))


def transcribe(audio_bytes: bytes, openai_api_key: str) -> str:
    """Transcribe voice audio to text using Whisper. Raises RuntimeError on failure."""
    oa_client = openai.OpenAI(api_key=openai_api_key, timeout=15.0, max_retries=0)
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = "voice.ogg"

    try:
        call_started = llm_usage.started()
        with llm_limits.routine_call():
            transcript = oa_client.audio.transcriptions.create(
                model="whisper-1", file=audio_file, language="es",
                prompt=_WHISPER_PROMPT, response_format="verbose_json",
            )
        llm_usage.record("audio_whisper", "whisper-1", call_started, response=transcript)
        transcription = transcript.text.strip()
    except Exception as e:
        if "call_started" in locals():
            llm_usage.record("audio_whisper", "whisper-1", call_started, error=e)
        raise RuntimeError(f"Error en transcripción de audio: {e}") from e

    if not transcription:
        raise RuntimeError("Whisper devolvió una transcripción vacía")

    logger.info("Transcripción Whisper: %s", transcription)
    return transcription


def extract_expenses(transcription: str, anthropic_api_key: str) -> list[dict]:
    """Extract one or more expenses from a transcription.

    Returns [{"concept": str, "amount": float | None, "confidence": float}, ...].
    Raises RuntimeError on extraction failure.
    """
    try:
        an_client = anthropic.Anthropic(api_key=anthropic_api_key, timeout=15.0, max_retries=0)
        call_started = llm_usage.started()
        with llm_limits.routine_call():
            message = an_client.messages.create(
                model=_MODEL, max_tokens=512, messages=[{
                    "role": "user", "content": f"{_EXTRACT_PROMPT}\n\nTranscripción: {transcription}",
                }],
            )
        llm_usage.record("audio_extract", _MODEL, call_started, response=message)
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw
        data = json.loads(raw)
    except Exception as e:
        if "call_started" in locals() and "message" not in locals():
            llm_usage.record("audio_extract", _MODEL, call_started, error=e)
        raise RuntimeError(f"Error al extraer datos del gasto: {e}") from e

    if not isinstance(data, list):
        raise RuntimeError(f"Claude devolvió formato inesperado (esperaba array): {data!r}")

    expenses = [
        {
            "concept": (item.get("concept") or "").strip() or "Desconocido",
            "amount": _parse_amount(item.get("amount")),
            "currency": "USD" if str(item.get("currency", "ARS")).upper() == "USD" else "ARS",
            "confidence": _parse_confidence(item.get("confidence")),
        }
        for item in data
        if isinstance(item, dict)
    ]

    if not expenses:
        raise RuntimeError("Claude no detectó ningún gasto en la transcripción")

    return expenses


def transcribe_and_extract(audio_bytes: bytes, openai_api_key: str, anthropic_api_key: str) -> dict:
    """Transcribe voice audio and extract one or more expenses.

    Returns:
        {
            "transcription": str,
            "expenses": [{"concept": str, "amount": float | None, "confidence": float}, ...]
        }
    Raises RuntimeError on transcription or extraction failure.
    """
    transcription = transcribe(audio_bytes, openai_api_key)
    return {
        "transcription": transcription,
        "expenses": extract_expenses(transcription, anthropic_api_key),
    }
