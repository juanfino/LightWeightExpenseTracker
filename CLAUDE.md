# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Home Assistant custom add-on that records family expenses via Telegram and shows them in a web dashboard. It runs as a Docker container on a Raspberry Pi 4 (aarch64). Users send messages like `Supermercado 150000` to a Telegram bot; the app parses, categorizes, and stores the expense in SQLite.

## Development setup

**Local run (without HA Supervisor):**
```bash
cd gastos
pip install -r requirements.txt
export TELEGRAM_TOKEN=<token>
export USERS_JSON='[{"telegram_id": "123456", "name": "Juampi"}]'
export ANTHROPIC_API_KEY=<key>   # only needed for OCR
export DB_PATH=/tmp/gastos.db
python3 app/main.py
```

**Build Docker image:**
```bash
docker build -t gastos gastos/
```

**Run inline module tests** (parser and categorizer have inline test suites):
```bash
python3 gastos/app/parser.py
python3 gastos/app/categorizer.py
```

## Architecture

`main.py` is the entrypoint. It:
1. Loads config from `/data/options.json` (written by HA Supervisor) or falls back to env vars
2. Initializes SQLite via `db.py`
3. Starts Flask dashboard in a daemon thread
4. Starts the Telegram bot (blocks main thread via polling)

**Module responsibilities:**
- `bot.py` — Telegram command handlers and message routing. Holds `pending_ocr` (OCR confirmation flow) and `pending_amount_edit` (waiting for user to type new amount after tapping inline button) module-level dicts keyed by `chat_id`. Uses `TELEGRAM_TOKEN` and `USERS` module-level globals set by `main.py`.
- `parser.py` — Parses free-text messages into `{concept, amount}`. Handles Argentine number formats (dot=thousands, comma=decimal). Returns `None` if no valid amount found.
- `categorizer.py` — Matches concept against keyword list from DB (accent/case-insensitive). Returns first matching `category_id` or `None`.
- `db.py` — All SQLite operations. Uses a `get_conn()` context manager that auto-commits/rollbacks. `DB_PATH` is a module-level global set by `main.py`.
- `dashboard.py` — Flask app. Timestamps stored as UTC in DB; `dashboard.py` converts to Buenos Aires time (UTC-3) for display.
- `ocr.py` — Uses `claude-haiku-4-5-20251001` via the Anthropic SDK to extract `{comercio, monto, fecha}` from ticket images.
- `seed.py` — Populates default categories and keywords on first DB creation.

**DB schema** (4 tables): `users`, `categories`, `keywords`, `expenses`. Categories have a protected "Sin categoría" that cannot be edited or deleted.

## Config

The add-on is configured via `gastos/config.yaml`. Options are written to `/data/options.json` by HA Supervisor at runtime. Required fields: `telegram_token`, `users` (list of `{telegram_id, name}`). Optional: `anthropic_api_key` (enables OCR).

## Versioning

The app version lives in `gastos/config.yaml` (`version` field). HA uses this to detect updates and prompt the user to upgrade. Bump it at the end of every session that produces a deployable change:
- patch (`1.1.x`) — bugfixes
- minor (`1.x.0`) — new features
- major (`x.0.0`) — breaking changes to config schema or DB

Also update `gastos/CHANGELOG.md` with a new entry matching the bumped version. Follow the existing format: one bullet per meaningful change, concise and in Spanish.

## Before starting any task

Make sure to fully understand what is being asked before writing any code. If anything is unclear — scope, edge cases, expected behavior — ask first. It's better to ask too many questions than to implement the wrong thing.

## Key conventions

- All DB timestamps stored as UTC; dashboard converts to `America/Argentina/Buenos_Aires` (UTC-3 fixed offset).
- Amount parsing handles Argentine notation: `100.000` → 100000, `2.500,50` → 2500.5. When only a dot or comma is present with 3 digits after it, it's treated as a thousands separator.
- OCR flow is two-step: bot sends extracted data back to the user for confirmation before saving.
- User authorization is enforced per-request via `_get_authorized_user()` in `bot.py` — only `telegram_id`s in config are allowed.
