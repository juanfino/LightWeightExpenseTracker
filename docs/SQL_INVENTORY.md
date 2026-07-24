# SQL Inventory

Produced in Phase 0 (Ground truth) of [MULTITENANT_PLAN.md](MULTITENANT_PLAN.md). Every raw SQL statement in the codebase, as of `gastos/config.yaml` version 2.5.2, listed as file → function → tables touched → read/write. This is the checklist for Phase 1 (Postgres port) and Phase 2 (adding `family_id` + RLS to every domain table).

**Scope note:** `db.py` is the *only* module in `gastos/app/` that issues raw SQL. Every other module (`bot.py`, `dashboard.py`, `dossier.py`, `report.py`, `report_ai.py`, `intent.py`, `ocr.py`, `audio.py`, `dolar.py`, `backup.py`) reads/writes exclusively through `db.py`'s functions — confirmed by grepping for `.execute(`/`.executemany(` across every file in `gastos/app/`, which returns zero hits outside `db.py` and `sqlro.py`. The one exception is `intent.py`'s `run_report` tool, which has the model generate SQL text at runtime; that SQL is never handwritten in the codebase, but it is always executed through `sqlro.py`'s guardrails (see below) — never through `db.py`'s writable connection.

`db.py`: 114 raw `.execute`/`.executemany` calls across 87 functions (several functions issue more than one statement — mostly the `_migrate_*` functions and a few read paths that run a guard `SELECT` before a `SELECT`/`UPDATE`). `sqlro.py`: 6 execute-family calls, all guardrail-related (3 in the module's real functions, 3 more in its `__main__` inline test harness).

---

## `db.py`

| Function | Line | Tables | R/W | Notes |
|---|---|---|---|---|
| `get_conn` | 145 | (none — PRAGMA) | write | Context manager; runs `PRAGMA foreign_keys = ON` on every connection open; auto commit/rollback wrapper used by every function below |
| `_migrate_users_color` | 160 | users | write | `ALTER TABLE users ADD COLUMN`; swallows `sqlite3.OperationalError` if column exists — a Postgres port needs `IF NOT EXISTS` or to catch `DuplicateColumn` |
| `_migrate_cambios_tipo` | 169 | cambios_dolar | write | `ALTER TABLE ... ADD COLUMN`, same idempotency-via-exception pattern |
| `_migrate_fixed_expenses_to_expense_link` | 178 | sqlite_master, expenses, fixed_expense_payments (legacy) | both | `SELECT ... FROM sqlite_master WHERE type='table'` to check table existence (SQLite-specific catalog); `PRAGMA table_info(expenses)`; 3× conditional `ALTER TABLE ADD COLUMN`; SELECT + per-row UPDATE loop; `DROP TABLE fixed_expense_payments`. Row-by-row migration in Python, not set-based |
| `_migrate_expenses_subcategory` | 232 | expenses | both | `PRAGMA table_info(expenses)` check, then conditional `ALTER TABLE ADD COLUMN` |
| `_migrate_keywords_subcategory` | 239 | keywords | both | Same `PRAGMA table_info` + conditional `ALTER TABLE` pattern |
| `_migrate_fixed_expenses_subcategory` | 246 | fixed_expenses | both | Same `PRAGMA table_info` + conditional `ALTER TABLE` pattern |
| `_migrate_currencies` | 253 | expenses, fixed_expenses, expense_classifications | both | 3× `PRAGMA table_info` checks, 3× conditional `ALTER TABLE ADD COLUMN`, 2× `UPDATE ... WHERE currency IS NULL OR currency NOT IN (...)` backfill |
| `init_db` | 274 | all 10 tables | write | `conn.executescript(SCHEMA)` — creates all tables via `CREATE TABLE IF NOT EXISTS`; retried once after deleting a corrupt DB file; schema uses `strftime('%Y-%m-%d %H:%M:%S','now')` as column defaults (fixed_expenses, ipc_series, reports, expense_classifications) and `CHECK(currency IN ('ARS','USD'))` / `CHECK(label IN ('recurring','exceptional'))` constraints — needs `DEFAULT now()` / equivalent CHECK syntax in Postgres; orchestrates all `_migrate_*` calls in sequence |
| `_sync_users` | 300 | users | write | `INSERT ... ON CONFLICT(telegram_id) DO UPDATE SET name=excluded.name` (upsert — ports as-is) |
| `_assign_default_user_colors` | 312 | users | both | SELECT all users, then per-row conditional `UPDATE users SET color=?` in a Python loop |
| `get_user_by_telegram_id` | 329 | users | read | simple |
| `create_expense` | 344 | expenses | write | INSERT; timestamp computed in Python (`datetime.now(timezone.utc)`), not SQL |
| `create_expense_full` | 356 | expenses | write | INSERT; stores explicit ART date as fixed `"{date} 03:00:00"` UTC string — the app's ART/UTC convention, not a SQL date function |
| `delete_expense` | 371 | expenses | write | simple DELETE |
| `get_recent_expenses` | 377 | expenses, users, categories, subcategories | read | static JOIN/LEFT JOIN, LIMIT |
| `get_recent_expenses_for_user` | 400 | expenses, users, categories, subcategories | read | same pattern, WHERE user_id + LIMIT |
| `get_expenses_filtered` | 425 | expenses, users, categories, subcategories | read | **Dynamic WHERE built with an f-string** from optional year/month; uses `strftime('%Y', ...)`/`strftime('%m', ...)` |
| `get_expense_years` | 459 | expenses | read | `SELECT DISTINCT strftime('%Y', created_at)` |
| `get_expenses_by_week` | 469 | expenses, users, categories | read | `date(datetime(e.created_at, '-3 hours')) BETWEEN ? AND ?` — SQLite date-math for the ART offset |
| `get_expenses_today` | 488 | expenses, users, categories | read | `date(datetime(created_at,'-3 hours')) = date(datetime('now','-3 hours'))` — relies on SQLite's `'now'` keyword and offset modifiers |
| `get_expenses_summary_by_category` | 505 | expenses, categories, users (conditional) | read | **f-string dynamic JOIN/WHERE** (`user_join`, `user_filter`); `strftime` filters |
| `get_expenses_by_week_of_month` | 547 | expenses, users | read | f-string dynamic `user_filter`; `CAST(strftime('%d', ...) AS INTEGER)`; week bucketing done in Python after fetch |
| `get_expenses_by_user` | 588 | expenses, users | read | static, `strftime` filters |
| `update_expense` | 607 | expenses | both | SELECT `fixed_expense_id, currency` guard, then **f-string dynamic SET clause** for the UPDATE |
| `get_expense_by_id` | 642 | expenses, subcategories | read | simple LEFT JOIN |
| `get_expenses_uncategorized` | 655 | expenses, users | read | `WHERE category_id IS NULL` |
| `update_expense_amount` | 669 | expenses | write | UPDATE scoped by `id AND user_id` — ownership enforced in SQL |
| `update_expense_category` | 678 | expenses | write | same ownership-scoped UPDATE pattern |
| `update_expense_fields` | 687 | expenses | both | **f-string dynamic SET clause**, plus a guard SELECT (`fixed_expense_id, currency`) before allowing a currency change; ownership enforced via `WHERE id=? AND user_id=?` |
| `recategorize_by_concept` | 740 | expenses | write | `WHERE LOWER(concept) LIKE LOWER(?)` bulk UPDATE (no LIMIT — updates every match) |
| `get_all_categories` | 755 | categories | read | simple |
| `find_category_normalized` | 776 | (none — calls `get_all_categories`) | read | Pure Python normalization loop over an already-fetched list; no SQL of its own |
| `get_category_by_id` | 762 | categories | read | simple |
| `get_category_by_name` | 769 | categories | read | simple |
| `get_expense_count_by_category` | 790 | expenses | read | `GROUP BY category_id` |
| `create_category` | 799 | categories | write | INSERT; catches `sqlite3.IntegrityError` (unique name) → returns `None` — needs `UniqueViolation` equivalent in Postgres |
| `update_category` | 811 | categories | write | UPDATE; catches `sqlite3.IntegrityError` for duplicate name; protected-name check done in Python before the SQL |
| `delete_category` | 828 | expenses, categories | both | SELECT COUNT(*) guard (dependent-row check), then DELETE |
| `get_all_users` | 847 | users | read | simple |
| `get_expenses_by_week_of_month_by_user` | 852 | expenses, users | read | f-string dynamic `user_filter`; `CAST(strftime('%d',...) AS INTEGER)`; per-user week bucketing in Python |
| `get_gastos_por_categoria` | 902 | expenses, users, categories, subcategories | read | f-string dynamic `user_filter`; `strftime` filters; `GROUP BY category_id, subcategory_id` |
| `get_annual_data` | 955 | expenses, categories, users | read | Two separate queries (by-category, by-user), both `strftime('%m',...)` + `CAST ... AS INTEGER`; reshaped in Python after fetch |
| `get_monthly_totals` | 1047 | expenses | read | `strftime('%Y-%m', created_at)`; window start date computed in Python, not SQL |
| `get_all_keywords` | 1101 | keywords, categories, subcategories | read | simple JOIN/LEFT JOIN |
| `add_keyword` | 1115 | keywords | both | SELECT existing category_id, then `INSERT ... ON CONFLICT(keyword) DO UPDATE SET category_id=excluded.category_id, subcategory_id=excluded.subcategory_id` (upsert) |
| `delete_keyword` | 1139 | keywords | write | simple |
| `get_expense_count_by_subcategory` | 1145 | expenses | read | simple COUNT |
| `update_keyword` | 1152 | keywords | write | simple UPDATE |
| `get_subcategories` | 1164 | subcategories | read | simple |
| `get_all_subcategories` | 1172 | subcategories, categories | read | simple JOIN |
| `get_subcategory_by_id` | 1184 | subcategories | read | simple |
| `find_subcategory_normalized` | 1191 | (none — calls `get_subcategories`) | read | Pure Python normalization loop; no SQL of its own |
| `add_subcategory` | 1204 | subcategories | write | simple INSERT |
| `delete_subcategory` | 1213 | subcategories | write | simple DELETE |
| `update_expense_subcategory` | 1219 | expenses | write | simple UPDATE |
| `update_keyword_subcategory` | 1228 | keywords | write | simple UPDATE |
| `get_all_fixed_expenses` | 1239 | fixed_expenses, categories, subcategories | read | `WHERE active = 1` |
| `get_fixed_expense_by_id` | 1257 | fixed_expenses, categories, subcategories | read | simple |
| `create_fixed_expense` | 1275 | fixed_expenses | write | simple INSERT |
| `update_fixed_expense` | 1285 | expenses, fixed_expenses | both | Guard SELECT `1 FROM expenses WHERE fixed_expense_id=?` blocks a currency change once payments are linked; then UPDATE |
| `deactivate_fixed_expense` | 1303 | fixed_expenses | write | soft-delete via `active=0` |
| `get_fixed_payments_for_period` | 1308 | fixed_expenses, categories, subcategories, expenses | read | Two SELECTs in one connection (fixed defs + linked expenses for period); "any number of payments per period" aggregation done in Python (dict grouping), not SQL |
| `get_fixed_expense_monthly_summary` | 1407 | (none — calls `get_fixed_payments_for_period`) | read | Pure Python aggregation over an already-fetched list; no SQL of its own |
| `get_unlinked_expenses_for_period` | 1357 | expenses, users | read | `WHERE fixed_expense_id IS NULL`; `strftime` period filter |
| `link_expense_to_fixed` | 1376 | expenses, fixed_expenses (via helper call) | both | Calls `get_fixed_expense_by_id`; SELECT `currency` guard (must match the fixed expense's currency), then UPDATE forcing category/subcategory + link fields — the single choke point per CLAUDE.md |
| `unlink_expense_from_fixed` | 1397 | expenses | write | sets 3 columns to NULL |
| `registrar_cambio` | 1428 | cambios_dolar | write | `monto_ars` computed in Python before INSERT |
| `get_cambios_resumen_mes` | 1439 | cambios_dolar | read | `strftime('%Y'/'%m', fecha)` filters, aggregate SUM/AVG |
| `get_cambios_historial` | 1459 | cambios_dolar | read | `ORDER BY fecha DESC LIMIT ?` |
| `get_cambios_por_mes` | 1468 | cambios_dolar | read | `strftime('%Y-%m', fecha)` GROUP BY; window start date computed in Python |
| `get_cambios_cotizacion_historica` | 1493 | cambios_dolar | read | simple, full history |
| `delete_cambio` | 1500 | cambios_dolar | write | simple DELETE |
| `get_expenses_for_period_art` | 1508 | expenses, users, categories, subcategories | read | `strftime('%Y-%m', datetime(e.created_at, '-3 hours')) = ?` — ART cash-basis period logic; f-string conditional currency filter |
| `get_expenses_excluding_period` | 1537 | expenses, categories | read | Same ART date-math pattern with `!=` (complement set); f-string conditional currency filter |
| `get_first_expense_date` | 1560 | expenses | read | `MIN(date(datetime(created_at,'-3 hours')))` |
| `get_months_with_data` | 1570 | expenses | read | `SELECT DISTINCT strftime('%Y-%m', datetime(created_at,'-3 hours'))` |
| `get_cambios_resumen_mes_by_tipo` | 1584 | cambios_dolar | read | `GROUP BY tipo`; `strftime` filters; Python fills in zeroed defaults for a missing `tipo` |
| `get_cambios_for_period` | 1610 | cambios_dolar | read | `strftime` filters, `ORDER BY id` |
| `get_ipc_series` | 1625 | ipc_series | read | simple, full series |
| `get_ipc_value` | 1634 | ipc_series | read | composite-key lookup (year, month) |
| `upsert_ipc_value` | 1644 | ipc_series | write | `INSERT ... ON CONFLICT(year, month) DO UPDATE SET ...` (upsert) |
| `create_report` | 1658 | reports | write | append-only INSERT (never overwrites, by design) |
| `get_latest_report` | 1674 | reports | read | `ORDER BY generated_at DESC, id DESC LIMIT 1` |
| `get_latest_report_overall` | 1684 | reports | read | `ORDER BY year DESC, month DESC, generated_at DESC, id DESC LIMIT 1` |
| `get_report_history` | 1693 | reports | read | simple, ordered |
| `save_classifications` | 1703 | expense_classifications | write | `conn.executemany(...)` — the only `executemany` in normal (non-migration) app code |
| `get_classifications_for_report` | 1719 | expense_classifications | read | simple |
| `get_recent_classifications_before` | 1729 | reports, expense_classifications | read | Loops over N prior (year, month) periods in Python, issuing 2 SELECTs per period — not a single set-based query |
| `update_cambio` | 1765 | cambios_dolar | write | Two mutually-exclusive UPDATE variants (with/without `tipo`) chosen in Python; `monto_ars` recomputed in Python |

## `sqlro.py`

The read-only SQL executor used by `intent.py`'s `run_report` tool (model-generated reporting SQL) and by edit-targeting SELECTs. Enforces four guardrails, all in this one module:

| Function | Line | Guardrail enforced | Exact mechanism |
|---|---|---|---|
| `_strip_sql` | 37 | Single-statement check | Strips one trailing `;`, then raises `ReadOnlySQLError` if any `;` remains — rejects stacked queries. Pure string validation, no SQL executed. |
| `validate` | 48 | Statement-type allow-list | Calls `_strip_sql`; rejects empty statements; `re.match(r"(?is)^(select\|with)\b", head)` requires the (optionally paren-wrapped) statement to start with `SELECT`/`WITH`; then `_FORBIDDEN_RE` (lines 22–26) blocks `insert\|update\|delete\|replace\|drop\|alter\|create\|attach\|detach\|pragma\|vacuum\|reindex\|analyze\|begin\|commit\|rollback\|truncate\|grant` anywhere in the statement (word-boundary, case-insensitive) — a keyword blocklist layered on top of the head-match allow-list. |
| `run_readonly` | 62 | Read-only connection, statement timeout, row cap | **Read-only mode:** `sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)` — a physically read-only connection via URI flag, so a validation bypass still can't write. **Timeout:** `conn.set_progress_handler(_guard, 1000)` fires `_guard()` every ~1000 VM instructions; `_guard` returns non-zero once `time.monotonic() > deadline` (default `timeout_s=3.0`), aborting the statement — caught as `sqlite3.OperationalError` and re-raised as `ReadOnlySQLError("La consulta tardó demasiado.")`. **Row cap:** `cur.fetchmany(max_rows)` (default 200) instead of `fetchall()`. Calls `validate(sql)` first. |

The `if __name__ == "__main__":` block (lines 92–178) is a standalone inline test harness exercising the guardrails against a throwaway temp DB (valid SELECT/WITH, row cap, semicolon tolerance, rejection of UPDATE/DELETE/INSERT/PRAGMA/DROP/stacked/EXPLAIN, `mode=ro` write-block, a cartesian-product timeout case). Not a reusable function, but worth preserving as living documentation of the guardrail contract when this ports to Postgres.

---

## Cross-cutting items for the Postgres port (Phase 1)

- **`executescript(SCHEMA)`** (`init_db`, line 278/285) creates all 10 tables in one script. Needs replacing with a proper Alembic migration; embedded `CHECK` constraints and `strftime(...)`-as-default columns need Postgres equivalents (`DEFAULT now()` / standard `CHECK`).
- **`PRAGMA table_info(<table>)`** — used 8 times across the `_migrate_*` functions as the "does this column exist" check (SQLite catalog introspection). Postgres equivalent is `information_schema.columns`, but adopting Alembic's own migration-state tracking would likely obsolete these functions entirely rather than needing a line-for-line port.
- **`ALTER TABLE ... ADD COLUMN`** — appears in 8 migration functions, each guarded by catching `sqlite3.OperationalError` for "column already exists" rather than checking first. Postgres raises `psycopg2.errors.DuplicateColumn`/`UndefinedColumn` differently — the error handling needs updating, not just the SQL dialect.
- **`INSERT ... ON CONFLICT(...) DO UPDATE SET ...`** (SQLite upsert syntax) — identical in Postgres. `_sync_users`, `add_keyword`, `upsert_ipc_value` should port with minimal change.
- **SQLite-specific date functions** (`strftime`, `date()`, `datetime()`, the `'-3 hours'`/`'now'` modifiers) appear in roughly 25 of the 87 `db.py` functions — the single largest porting surface. Postgres needs `to_char`/`EXTRACT`/`AT TIME ZONE 'America/Argentina/Buenos_Aires'` (or the fixed `-3 hours` arithmetic via `INTERVAL '-3 hours'`), rewritten per call site. This is also the highest-risk item for `intent.py`'s SQL-generation prompt (see Phase 1 scope in the plan) since it teaches the model SQLite syntax today.
- **f-string–built dynamic SQL** (conditional WHERE/JOIN/SET clauses) — appears in ~10 functions (`get_expenses_filtered`, `get_expenses_summary_by_category`, `get_expenses_by_week_of_month`, `update_expense`, `update_expense_fields`, `get_expenses_by_week_of_month_by_user`, `get_gastos_por_categoria`, `get_expenses_for_period_art`, `get_expenses_excluding_period`). All values are still parameterized (`?` placeholders) — no SQL-injection risk — so the string-concatenation pattern itself is dialect-agnostic and can port as-is (swap `?` for `%s` if using `psycopg2`, or keep `?` with a DB-API driver that translates paramstyle).
- **`conn.executemany`** appears exactly once in normal app code (`save_classifications`) — straightforward to port.
- **Everything in `sqlro.py` is SQLite-specific by design** (the `mode=ro` URI, `set_progress_handler` timeout) and needs a different mechanism entirely in Postgres — a read-only role/transaction plus the `statement_timeout` GUC — rather than a line-by-line translation. This is exactly the guardrail invariant I1/I2 in the plan depend on, so the replacement must preserve all four guarantees (statement-type allow-list, single-statement, read-only, timeout+row-cap), not just "some of them."
- **`intent.py`'s `run_report` tool** generates SQL text at runtime against `sqlro.py`'s guardrails. No static SQL to inventory, but its prompt's SQLite-dialect instructions are the single highest-risk item called out in Phase 1 of the plan.
