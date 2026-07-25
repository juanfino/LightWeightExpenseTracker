"""Shared best-effort LLM usage accounting."""
import time

import db

# USD per million tokens. Estimates are deliberately centralized and can be
# adjusted without touching callers.
MODEL_RATES = {
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-opus-4-8": (5.0, 25.0),
}


def started() -> float:
    return time.monotonic()


def record(module: str, model: str, started_at: float, *, response=None, error=None) -> None:
    usage = getattr(response, "usage", None)
    tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
    input_rate, output_rate = MODEL_RATES.get(model, (0.0, 0.0))
    cost = (tokens_in * input_rate + tokens_out * output_rate) / 1_000_000
    if model == "whisper-1":
        # Whisper is billed per audio minute rather than token.
        duration_seconds = float(getattr(response, "duration", 0) or 0)
        cost = duration_seconds / 60 * 0.006
    db.record_llm_call(
        module=module,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd_estimate=cost,
        latency_ms=round((time.monotonic() - started_at) * 1000),
        success=error is None,
        error_text=str(error) if error is not None else None,
    )
