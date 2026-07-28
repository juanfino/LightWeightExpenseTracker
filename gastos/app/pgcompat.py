"""Small DB-API compatibility layer for the application's raw-SQL style.

It keeps sqlite-like ``?`` placeholders and row access while PostgreSQL is the
only storage engine. SQL dialect differences remain explicit in db.py.
"""
import os
import re
import threading
from contextvars import ContextVar
from collections.abc import Mapping
from decimal import Decimal

from psycopg_pool import ConnectionPool

_pools: dict[str, ConnectionPool] = {}
_lock = threading.Lock()
_pool_name = ContextVar("db_pool_name", default="bot")
_family_id = ContextVar("family_id", default=None)
_user_id = ContextVar("user_id", default=None)
_INSERT_WITH_ID = {
    "users", "categories", "subcategories", "keywords", "expenses",
    "fixed_expenses", "cambios_dolar", "reports", "expense_classifications",
    "income_categories", "incomes",
}


class Row(Mapping):
    def __init__(self, columns, values):
        self._columns = tuple(columns)
        self._values = tuple(float(value) if isinstance(value, Decimal) else value for value in values)
        self._data = dict(zip(self._columns, self._values))

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, int) else self._data[key]

    def __iter__(self):
        return iter(self._columns)

    def __len__(self):
        return len(self._columns)


class Cursor:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def _row(self, values):
        if values is None:
            return None
        columns = [column.name for column in self._cursor.description]
        return Row(columns, values)

    def fetchone(self):
        return self._row(self._cursor.fetchone())

    def fetchall(self):
        rows = self._cursor.fetchall()
        columns = [column.name for column in self._cursor.description]
        return [Row(columns, row) for row in rows]

    def fetchmany(self, size=1):
        rows = self._cursor.fetchmany(size)
        columns = [column.name for column in self._cursor.description]
        return [Row(columns, row) for row in rows]


def _sql(sql: str) -> tuple[str, bool]:
    statement = sql.strip()
    ignore_conflict = bool(re.match(r"(?is)^INSERT\s+OR\s+IGNORE\s+INTO", statement))
    statement = re.sub(r"(?i)^INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", statement)
    if ignore_conflict and "ON CONFLICT" not in statement.upper():
        statement += " ON CONFLICT DO NOTHING"
    placeholder = "__PGCOMPAT_PLACEHOLDER__"
    statement = statement.replace("?", placeholder)
    statement = statement.replace("%", "%%").replace(placeholder, "%s")
    statement = re.sub(r"\bfe\.active\s*=\s*1\b", "fe.active IS TRUE", statement)
    statement = re.sub(r"\bactive\s*=\s*0\b", "active = FALSE", statement)
    match = re.match(r"(?is)^INSERT\s+INTO\s+([a-z_]+)", statement)
    wants_id = bool(
        match and match.group(1).lower() in _INSERT_WITH_ID
        and "RETURNING" not in statement.upper() and not ignore_conflict
    )
    if wants_id:
        statement += " RETURNING id"
    return statement, wants_id


class Connection:
    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql, params=()):
        statement, wants_id = _sql(sql)
        cursor = self._connection.execute(statement, params)
        lastrowid = cursor.fetchone()[0] if wants_id else None
        return Cursor(cursor, lastrowid)

    def executemany(self, sql, params_seq):
        statement, _ = _sql(sql)
        return Cursor(self._connection.executemany(statement, params_seq))


def pool(name: str = "app") -> ConnectionPool:
    with _lock:
        if name not in _pools:
            url = os.environ["DATABASE_URL"]
            max_size = int(os.environ.get(f"DB_POOL_{name.upper()}_MAX", "5"))
            _pools[name] = ConnectionPool(
                conninfo=url,
                min_size=1,
                max_size=max_size,
                kwargs={"autocommit": False},
                open=True,
            )
        return _pools[name]


def current_pool() -> ConnectionPool:
    return pool(_pool_name.get())


def select_pool(name: str):
    _pool_name.set(name)


def set_family_id(family_id: int | None):
    """Set the tenant for the current request/update context."""
    _family_id.set(int(family_id) if family_id is not None else None)


def current_family_id() -> int | None:
    return _family_id.get()


def set_user_id(user_id: int | None):
    _user_id.set(int(user_id) if user_id is not None else None)


def current_user_id() -> int | None:
    return _user_id.get()
