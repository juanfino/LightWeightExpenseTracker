"""Read-only SQL executor with guardrails.

The natural-language intent layer lets the model generate SQL for two purposes:
reporting queries and edit-targeting SELECTs. That SQL must never be able to
mutate the database, hang the bot, or run more than one statement. This module
is the single choke point that enforces those guarantees:

- SELECT/WITH only, single statement, no stacked queries.
- Executed on a physically read-only connection (SQLite ``mode=ro`` URI), so even
  if validation were bypassed the DB could not be written.
- Wall-clock statement timeout via a progress handler.
- Hard row cap so a huge result can't blow up the bot's memory/context.
"""

import re
from psycopg import errors
from psycopg.rows import dict_row

import pgcompat

# DB_PATH is resolved the same way db.py does, at call time, so tests that set
# the env var after import still work.
_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|replace|drop|alter|create|attach|detach|"
    r"pragma|vacuum|reindex|analyze|begin|commit|rollback|truncate|grant)\b",
    re.IGNORECASE,
)


class ReadOnlySQLError(ValueError):
    """Raised when a statement fails the read-only guardrails."""


def _strip_sql(sql: str) -> str:
    """Remove trailing semicolons/whitespace and reject stacked statements."""
    stripped = (sql or "").strip()
    # Allow a single trailing semicolon; anything before the end is a stacked query.
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    if ";" in stripped:
        raise ReadOnlySQLError("Solo se permite una sola sentencia SQL.")
    return stripped


def validate(sql: str) -> str:
    """Validate that ``sql`` is a single read-only statement. Returns the cleaned
    statement or raises ReadOnlySQLError."""
    stmt = _strip_sql(sql)
    if not stmt:
        raise ReadOnlySQLError("SQL vacío.")
    head = stmt.lstrip("(").lstrip()
    if not re.match(r"(?is)^(select|with)\b", head):
        raise ReadOnlySQLError("Solo se permiten consultas SELECT/WITH.")
    if _FORBIDDEN_RE.search(stmt):
        raise ReadOnlySQLError("La consulta contiene una operación no permitida.")
    return stmt


def run_readonly(sql: str, params=(), *, max_rows: int = 200, timeout_s: float = 3.0) -> list[dict]:
    """Execute a validated read-only SELECT and return up to ``max_rows`` rows as
    dicts. Raises ReadOnlySQLError on invalid SQL or if the statement times out."""
    stmt = validate(sql)

    timeout_ms = max(1, int(timeout_s * 1000))
    with pgcompat.pool("readonly").connection() as conn:
        try:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL ROLE gastos_readonly")
            conn.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (f"{timeout_ms}ms",),
            )
            cur = conn.cursor(row_factory=dict_row)
            query = stmt.replace("?", "__SQLRO_PARAM__")
            query = query.replace("%", "%%").replace("__SQLRO_PARAM__", "%s")
            cur.execute(query, params)
            rows = cur.fetchmany(max_rows)
            conn.rollback()
        except errors.QueryCanceled as e:
            conn.rollback()
            raise ReadOnlySQLError("La consulta tardó demasiado.") from e
        except Exception as e:
            conn.rollback()
            raise ReadOnlySQLError(f"Error ejecutando la consulta: {e}") from e
        return rows
