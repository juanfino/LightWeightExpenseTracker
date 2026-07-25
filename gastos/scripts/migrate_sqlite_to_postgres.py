#!/usr/bin/env python3
"""Migrate the Phase-0 SQLite database into an empty Alembic-managed PostgreSQL DB."""
import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import psycopg

TABLES = (
    "users",
    "categories",
    "subcategories",
    "keywords",
    "fixed_expenses",
    "expenses",
    "cambios_dolar",
    "ipc_series",
    "reports",
    "expense_classifications",
)
IDENTITY_TABLES = set(TABLES) - {"ipc_series"}
BOOLEAN_COLUMNS = {
    "fixed_expenses": {"active"},
    "ipc_series": {"is_estimated"},
    "reports": {"llm_ok"},
}
TIMESTAMP_COLUMNS = {
    "users": {"created_at"},
    "categories": {"created_at"},
    "fixed_expenses": {"created_at"},
    "expenses": {"created_at"},
    "cambios_dolar": {"fecha"},
    "ipc_series": {"updated_at"},
    "reports": {"generated_at"},
    "expense_classifications": {"created_at"},
}
NUMERIC_COLUMNS = {
    "fixed_expenses": {"estimated_amount"},
    "expenses": {"amount"},
    "cambios_dolar": {"monto_usd", "cotizacion", "monto_ars"},
    "ipc_series": {"value"},
    "expense_classifications": {"amount", "confidence"},
}


def _timestamp(value):
    if value is None or isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)


def _convert(table, column, value):
    if value is None:
        return None
    if column in BOOLEAN_COLUMNS.get(table, set()):
        return bool(value)
    if column in TIMESTAMP_COLUMNS.get(table, set()):
        return _timestamp(value)
    if column in NUMERIC_COLUMNS.get(table, set()):
        return Decimal(str(value))
    return value


def _canonical(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, float):
        return format(Decimal(str(value)).normalize(), "f")
    return value


def _checksum(table, columns, rows):
    digest = hashlib.sha256()
    for row in rows:
        payload = [_canonical(_convert(
            table, column, row[column] if isinstance(row, sqlite3.Row) else row[i]
        ))
                   for i, column in enumerate(columns)]
        digest.update(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _canonical_row(table, columns, row):
    return [
        _canonical(_convert(
            table, column, row[column] if isinstance(row, sqlite3.Row) else row[i]
        ))
        for i, column in enumerate(columns)
    ]


def migrate(sqlite_path: str, database_url: str):
    source = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    target = psycopg.connect(database_url)
    results = {}
    try:
        with target.transaction():
            target.execute(
                "TRUNCATE TABLE " + ", ".join(f'"{table}"' for table in reversed(TABLES))
                + " RESTART IDENTITY CASCADE"
            )
            for table in TABLES:
                columns = [
                    row["name"]
                    for row in source.execute(f'PRAGMA table_info("{table}")').fetchall()
                ]
                if not columns:
                    raise RuntimeError(f"Tabla SQLite ausente: {table}")
                source_rows = source.execute(
                    f'SELECT * FROM "{table}" ORDER BY ' +
                    ("id" if "id" in columns else ", ".join(columns))
                ).fetchall()
                if source_rows:
                    quoted = ", ".join(f'"{column}"' for column in columns)
                    placeholders = ", ".join(["%s"] * len(columns))
                    override = " OVERRIDING SYSTEM VALUE" if table in IDENTITY_TABLES else ""
                    sql = f'INSERT INTO "{table}" ({quoted}){override} VALUES ({placeholders})'
                    values = [
                        tuple(_convert(table, column, row[column]) for column in columns)
                        for row in source_rows
                    ]
                    with target.cursor() as cursor:
                        cursor.executemany(sql, values)
                selected_columns = ", ".join(f'"{column}"' for column in columns)
                order_columns = "id" if "id" in columns else ", ".join(columns)
                target_rows = target.execute(
                    f'SELECT {selected_columns} FROM "{table}" ORDER BY {order_columns}'
                ).fetchall()
                source_hash = _checksum(table, columns, source_rows)
                target_hash = _checksum(table, columns, target_rows)
                if len(source_rows) != len(target_rows) or source_hash != target_hash:
                    mismatch = next(
                        (
                            (index, _canonical_row(table, columns, left),
                             _canonical_row(table, columns, right))
                            for index, (left, right) in enumerate(zip(source_rows, target_rows))
                            if _canonical_row(table, columns, left)
                            != _canonical_row(table, columns, right)
                        ),
                        None,
                    )
                    raise RuntimeError(
                        f"Verificación fallida en {table}: "
                        f"rows {len(source_rows)}/{len(target_rows)}, "
                        f"sha256 {source_hash}/{target_hash}, first_diff={mismatch}"
                    )
                results[table] = {"rows": len(source_rows), "sha256": source_hash}

            for table in IDENTITY_TABLES:
                target.execute(
                    "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                    "COALESCE((SELECT MAX(id) FROM " + table + "), 1), "
                    "EXISTS(SELECT 1 FROM " + table + "))",
                    (table,),
                )
    finally:
        target.close()
        source.close()
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("sqlite_path")
    parser.add_argument("database_url")
    args = parser.parse_args()
    results = migrate(args.sqlite_path, args.database_url)
    for table, result in results.items():
        print(f"{table}: rows={result['rows']} sha256={result['sha256']}")


if __name__ == "__main__":
    main()
