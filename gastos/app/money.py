"""Exact monetary arithmetic and explicit JSON-number boundaries."""

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


MONEY_QUANTUM = Decimal("0.01")
MONEY_ZERO = Decimal("0.00")
ROUNDING = ROUND_HALF_UP


def amount(value) -> Decimal:
    """Parse and quantize a monetary value without passing through binary float."""
    if isinstance(value, bool) or value is None:
        raise InvalidOperation
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise InvalidOperation
    return parsed.quantize(MONEY_QUANTUM, rounding=ROUNDING)


def rounded(value: Decimal, places: int = 2) -> Decimal:
    """Round a Decimal with the application's single, human-facing policy."""
    quantum = Decimal(1).scaleb(-places)
    return value.quantize(quantum, rounding=ROUNDING)


def statistic(value: Decimal, places: int) -> float:
    """Make the Decimal-to-derived-statistic boundary explicit."""
    return float(rounded(value, places))


def json_numbers(value):
    """Recursively convert Decimal values to JSON numbers at a Python boundary."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return str(value)
    if isinstance(value, dict):
        return {key: json_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_numbers(item) for item in value]
    return value


def json_dumps(value, **kwargs) -> str:
    """Serialize Decimal-bearing application data as JSON numbers, never strings."""
    return json.dumps(json_numbers(value), **kwargs)
