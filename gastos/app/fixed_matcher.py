"""Shared fixed-expense matching heuristics, used by both bot.py (Telegram) and
dashboard.py (web) so the two surfaces can't diverge on what counts as a match.

Two distinct matching problems:
  - find_fixed_expense_matches: a newly logged expense's concept -> which fixed expense
    definition(s) it might be a payment for (high precision; used to *offer* a link right
    after the expense is saved).
  - find_candidate_expenses: a fixed expense definition -> which already-logged, unlinked
    expenses in a period might be that payment (used by the "ya lo pagué" search; broader
    recall is fine here since the user always has a "ninguno de estos" escape hatch).

A false positive (wrongly linking) is worse than a false negative — both functions only ever
suggest a match, never link on their own.
"""

import re
from decimal import Decimal
from datetime import datetime, timezone

_MIN_WORD_LEN = 3


def _field(record, key: str, default=None):
    """Read a mapping-like value from both dicts and sqlite3.Row objects.

    SQLite rows implement ``record[key]`` but not ``dict.get``. DB query results are
    passed directly into this module in several dashboard flows, so treating every
    record as a dict makes matching crash only when real fixed expenses exist.
    """
    try:
        return record[key]
    except (KeyError, IndexError, TypeError):
        return default


def expense_period(created_at_utc: str | datetime, tz) -> tuple[int, int]:
    """(year, month) an expense's own date falls in, converted to `tz` — a fixed-expense
    link defaults to the month of the expense's own date, not "today" (an OCR receipt dated
    last month should land in last month's period). Shared by bot.py and dashboard.py so
    both compute the period the same way."""
    if isinstance(created_at_utc, datetime):
        dt = created_at_utc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = datetime.strptime(created_at_utc, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    local = dt.astimezone(tz)
    return local.year, local.month


def concept_words(text: str) -> set[str]:
    """Words of 3+ chars from a concept, lowercased, punctuation stripped."""
    return {w for w in re.sub(r"[^\w\s]", "", (text or "").lower()).split() if len(w) >= _MIN_WORD_LEN}


def find_fixed_expense_matches(concept: str, fixed_expenses, currency: str = "ARS") -> list:
    """Fixed expenses whose concept shares a word (3+ chars) with a newly logged expense's
    concept. Used to offer linking a brand-new expense right after it's saved."""
    words = concept_words(concept)
    return [fe for fe in fixed_expenses
            if _field(fe, "currency", "ARS") == currency and words & concept_words(fe["concept"])]


def find_candidate_expenses(fixed_expense, expenses, limit: int = 5) -> list:
    """Scores unlinked expenses (typically from db.get_unlinked_expenses_for_period) as
    candidates for "this is the payment I already logged for fixed expense X". Scored by
    concept word overlap, same-category bonus, and closeness to estimated_amount. Only
    positive-score matches are returned, capped at `limit`, highest score first."""
    fe_words = concept_words(fixed_expense["concept"])
    estimated = fixed_expense["estimated_amount"]
    fe_category_id = fixed_expense["category_id"]

    scored = []
    for exp in expenses:
        if _field(exp, "currency", "ARS") != _field(fixed_expense, "currency", "ARS"):
            continue
        score = 0.0
        shared = fe_words & concept_words(exp["concept"])
        score += 2 * len(shared)
        if fe_category_id is not None and exp["category_id"] == fe_category_id:
            score += 3
        if estimated and exp["amount"] and abs(exp["amount"] - estimated) <= Decimal("0.2") * estimated:
            score += 2
        if score > 0:
            scored.append((score, exp))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [exp for _, exp in scored[:limit]]
