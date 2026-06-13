import io
import json
import logging

import anthropic
import openai

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"
_WHISPER_PROMPT = "Gastos en pesos argentinos: 10000, 148900, 5000."

_EXTRACT_PROMPT = (
    "Analizá esta transcripción de un mensaje de voz sobre un gasto. "
    "Convertí los números escritos en español a dígitos "
    "(ej: 'diez mil' → 10000, 'ciento cuarenta y ocho mil novecientos' → 148900). "
    "Manejá expresiones coloquiales argentinas (ej: 'pesos', 'lucas', 'guita'). "
    "Respondé ÚNICAMENTE con un JSON válido sin explicaciones:\n"
    '{"concept": "<nombre del comercio o concepto, capitalizado>", '
    '"amount": <monto como número float, o null si no se detecta>}'
)


def transcribe_and_extract(audio_bytes: bytes, openai_api_key: str, anthropic_api_key: str) -> dict:
    """Transcribe voice audio and extract expense data.

    Returns dict with keys: 'transcription', 'concept', 'amount' (float or None).
    Raises RuntimeError on failure.
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
            max_tokens=128,
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

    amount = data.get("amount")
    if amount is not None:
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            amount = None

    return {
        "transcription": transcription,
        "concept": data.get("concept") or "",
        "amount": amount,
    }
