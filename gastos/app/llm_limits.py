"""Per-family LLM quotas and concurrency isolation."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import db
import pgcompat

ROUTINE_DAILY_LIMIT = 100
SUMMARY_MONTHLY_LIMIT = 15
FAMILY_CONCURRENCY_LIMIT = 2

_lock = threading.Lock()
_semaphores: dict[int, threading.BoundedSemaphore] = {}
_pending_routine: dict[int, int] = {}
_pending_summaries: dict[int, int] = {}


class QuotaExceeded(RuntimeError):
    pass


def _family_semaphore(family_id: int) -> threading.BoundedSemaphore:
    with _lock:
        return _semaphores.setdefault(
            family_id, threading.BoundedSemaphore(FAMILY_CONCURRENCY_LIMIT)
        )


def routine_usage() -> dict:
    used = db.count_routine_llm_calls_today()
    override = db.get_family_quota_limits()["routine_daily_limit"]
    limit = override or ROUTINE_DAILY_LIMIT
    return {"used": used, "limit": limit, "remaining": max(0, limit - used)}


def summary_usage() -> dict:
    used = db.count_reports_this_month()
    override = db.get_family_quota_limits()["summary_monthly_limit"]
    limit = override or SUMMARY_MONTHLY_LIMIT
    return {"used": used, "limit": limit, "remaining": max(0, limit - used)}


@contextmanager
def routine_call():
    family_id = pgcompat.current_family_id()
    if family_id is None:
        raise RuntimeError("No hay contexto familiar para la llamada LLM.")
    semaphore = _family_semaphore(family_id)
    semaphore.acquire()
    try:
        with _lock:
            usage = routine_usage()
            pending = _pending_routine.get(family_id, 0)
            if usage["used"] + pending >= usage["limit"]:
                timezone_name = db.get_current_family_timezone()
                now = datetime.now(ZoneInfo(timezone_name))
                reset = now.date().fromordinal(now.date().toordinal() + 1).strftime("%d/%m")
                raise QuotaExceeded(
                    f"Alcanzaron el límite diario de IA. Se habilita de nuevo el {reset} a las 00:00."
                )
            _pending_routine[family_id] = pending + 1
        try:
            yield
        finally:
            with _lock:
                _pending_routine[family_id] -= 1
    finally:
        semaphore.release()


@contextmanager
def summary_call():
    family_id = pgcompat.current_family_id()
    if family_id is None:
        raise RuntimeError("No hay contexto familiar para la llamada LLM.")
    semaphore = _family_semaphore(family_id)
    semaphore.acquire()
    try:
        yield
    finally:
        semaphore.release()


@contextmanager
def summary_generation():
    """Reserve one of the 15 monthly report generations across concurrent requests."""
    family_id = pgcompat.current_family_id()
    if family_id is None:
        raise RuntimeError("No hay contexto familiar para generar el Resumen.")
    with _lock:
        usage = summary_usage()
        pending = _pending_summaries.get(family_id, 0)
        if usage["used"] + pending >= usage["limit"]:
            raise QuotaExceeded(
                f"Alcanzaron el límite mensual de {usage['limit']} Resúmenes. "
                "Se habilita de nuevo el mes próximo."
            )
        _pending_summaries[family_id] = pending + 1
    try:
        yield
    finally:
        with _lock:
            _pending_summaries[family_id] -= 1
