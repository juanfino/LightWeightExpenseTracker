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

import os
import re
import sqlite3
import time

# DB_PATH is resolved the same way db.py does, at call time, so tests that set
# the env var after import still work.
_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|replace|drop|alter|create|attach|detach|"
    r"pragma|vacuum|reindex|analyze|begin|commit|rollback|truncate|grant)\b",
    re.IGNORECASE,
)


class ReadOnlySQLError(ValueError):
    """Raised when a statement fails the read-only guardrails."""


def _db_path() -> str:
    return os.environ.get("DB_PATH", "/data/gastos.db")


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

    conn = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        deadline = time.monotonic() + timeout_s

        def _guard():
            # Returning non-zero aborts the running statement.
            return 1 if time.monotonic() > deadline else 0

        # Fire the progress handler every ~1000 VM instructions.
        conn.set_progress_handler(_guard, 1000)
        try:
            cur = conn.execute(stmt, params)
            rows = cur.fetchmany(max_rows)
        except sqlite3.OperationalError as e:
            if time.monotonic() > deadline:
                raise ReadOnlySQLError("La consulta tardó demasiado.") from e
            raise ReadOnlySQLError(f"Error ejecutando la consulta: {e}") from e
        finally:
            conn.set_progress_handler(None, 0)
        return [dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    import tempfile

    # Build a throwaway DB to exercise the guardrails.
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.environ["DB_PATH"] = tmp.name
    seed = sqlite3.connect(tmp.name)
    seed.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, n TEXT)")
    seed.executemany("INSERT INTO t (n) VALUES (?)", [("a",), ("b",), ("c",)])
    seed.commit()
    seed.close()

    passed = 0
    failed = 0

    def check(desc, cond):
        global passed, failed
        if cond:
            passed += 1
            print(f"  ok  {desc}")
        else:
            failed += 1
            print(f"FAIL  {desc}")

    # Valid SELECT returns rows.
    rows = run_readonly("SELECT n FROM t ORDER BY id")
    check("SELECT returns rows", [r["n"] for r in rows] == ["a", "b", "c"])

    # WITH is allowed.
    rows = run_readonly("WITH x AS (SELECT 1 AS v) SELECT v FROM x")
    check("WITH allowed", rows == [{"v": 1}])

    # Row cap.
    rows = run_readonly("SELECT n FROM t", max_rows=2)
    check("row cap honored", len(rows) == 2)

    # Trailing semicolon tolerated.
    rows = run_readonly("SELECT COUNT(*) AS c FROM t;")
    check("trailing semicolon ok", rows == [{"c": 3}])

    def rejects(desc, sql):
        try:
            run_readonly(sql)
            check(desc, False)
        except ReadOnlySQLError:
            check(desc, True)

    rejects("rejects UPDATE", "UPDATE t SET n='z' WHERE id=1")
    rejects("rejects DELETE", "DELETE FROM t")
    rejects("rejects INSERT", "INSERT INTO t (n) VALUES ('z')")
    rejects("rejects PRAGMA", "PRAGMA table_info(t)")
    rejects("rejects stacked statements", "SELECT 1; DROP TABLE t")
    rejects("rejects DROP", "DROP TABLE t")
    rejects("rejects non-select", "EXPLAIN SELECT 1")

    # mode=ro physically blocks writes even if validation were bypassed.
    try:
        conn = sqlite3.connect(f"file:{tmp.name}?mode=ro", uri=True)
        try:
            conn.execute("UPDATE t SET n='z'")
            check("mode=ro blocks writes", False)
        except sqlite3.OperationalError:
            check("mode=ro blocks writes", True)
        finally:
            conn.close()
    except Exception:
        check("mode=ro blocks writes", False)

    # Timeout aborts a long-running query (cartesian blowup).
    try:
        big = sqlite3.connect(tmp.name)
        big.execute("CREATE TABLE big (x INTEGER)")
        big.executemany("INSERT INTO big (x) VALUES (?)", [(i,) for i in range(2000)])
        big.commit()
        big.close()
        run_readonly(
            "SELECT COUNT(*) FROM big a, big b, big c, big d",
            timeout_s=0.3,
        )
        check("timeout aborts long query", False)
    except ReadOnlySQLError:
        check("timeout aborts long query", True)

    os.unlink(tmp.name)
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
