"""IPC (national CPI) series fetch, cache, and deflator.

Source: the national open-data time-series API at apis.datos.gob.ar, series
148.3_INIVELNAL_DICI_M_26 (IPC Nacional, Nivel General, base December 2016). Free,
no API key. The index base is irrelevant — deflation only uses ratios between two
periods, so the base cancels out.

INDEC publishes a month's index around the middle of the following month, so the
most recent month is usually missing when a report for it runs. We resolve this by
estimating the single missing month, projecting from the average month-over-month
ratio of the last 3 published months — never further back than that; anything older
than the single most-recent gap is left absent rather than fabricated. When the real
value is later published, refresh() overwrites the estimate and every future report
uses the real number from then on.
"""

import logging

import requests

import db

logger = logging.getLogger(__name__)

_SERIES_ID = "148.3_INIVELNAL_DICI_M_26"
_API_URL = "https://apis.datos.gob.ar/series/api/series/"
_TIMEOUT_S = 15


def refresh() -> bool:
    """Fetches the latest published IPC values and upserts them, then estimates the
    single most-recent month if it's still missing. Returns True if the fetch
    succeeded, False if the API was unreachable — callers should degrade to
    nominal-only and say so rather than raise."""
    try:
        resp = requests.get(
            _API_URL,
            params={"ids": _SERIES_ID, "limit": 60, "sort": "desc"},
            timeout=_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except Exception as e:
        logger.warning("No se pudo actualizar el índice IPC: %s", e)
        return False

    for row in data:
        try:
            iso_date, value = row[0], float(row[1])
        except (IndexError, TypeError, ValueError):
            continue
        year, month = int(iso_date[:4]), int(iso_date[5:7])
        db.upsert_ipc_value(year, month, value, is_estimated=False)

    _estimate_latest_if_missing()
    return True


def _estimate_latest_if_missing() -> None:
    """Estimates the single calendar month right after the latest *real* value, if
    it isn't already present (real or estimated). Never chains further than one
    month — a gap older than that is left absent rather than compounding guesses."""
    series = db.get_ipc_series()
    real = sorted(
        (r for r in series if not r["is_estimated"]),
        key=lambda r: (r["year"], r["month"]),
    )
    if len(real) < 4:
        return  # not enough published history for a 3-month average ratio

    latest = real[-1]
    next_year, next_month = latest["year"], latest["month"] + 1
    if next_month == 13:
        next_year, next_month = next_year + 1, 1

    if db.get_ipc_value(next_year, next_month) is not None:
        return  # already have a value (real or estimated) for that month

    last4 = real[-4:]
    ratios = [last4[i]["value"] / last4[i - 1]["value"] for i in range(1, len(last4))]
    avg_ratio = sum(ratios) / len(ratios)
    estimated_value = latest["value"] * avg_ratio
    db.upsert_ipc_value(next_year, next_month, estimated_value, is_estimated=True)


def get_index(year: int, month: int) -> dict | None:
    return db.get_ipc_value(year, month)


def deflate(amount: float, from_year: int, from_month: int, to_year: int, to_month: int) -> float | None:
    """Converts a nominal amount from from_year/from_month prices into to_year/to_month
    prices. Returns None — not the nominal value, not zero — if either index is
    missing, so callers can tell "computed" apart from "unavailable" instead of
    silently presenting a non-deflated number as if it were real."""
    from_idx = db.get_ipc_value(from_year, from_month)
    to_idx = db.get_ipc_value(to_year, to_month)
    if from_idx is None or to_idx is None:
        return None
    return amount * (to_idx["value"] / from_idx["value"])
