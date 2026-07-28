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
    return {"used": used, "limit": ROUTINE_DAILY_LIMIT, "remaining": max(0, ROUTINE_DAILY_LIMIT - used)}


def summary_usage() -> dict:
    used = db.count_reports_this_month()
    return {"used": used, "limit": SUMMARY_MONTHLY_LIMIT, "remaining": max(0, SUMMARY_MONTHLY_LIMIT - used)}


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
            if usage["used"] + pending >= ROUTINE_DAILY_LIMIT:
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
        if usage["used"] + pending >= SUMMARY_MONTHLY_LIMIT:
            raise QuotaExceeded(
                "Alcanzaron el límite mensual de 15 Resúmenes. Se habilita de nuevo el mes próximo."
            )
        _pending_summaries[family_id] = pending + 1
    try:
        yield
    finally:
        with _lock:
            _pending_summaries[family_id] -= 1
