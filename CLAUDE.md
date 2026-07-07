# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A family expense tracker that records spending via Telegram and shows it in a web dashboard. Users send messages like `Supermercado 150000` to a Telegram bot; the app parses, categorizes, and stores the expense in SQLite. Runs as a Docker container on a Raspberry Pi 4 (aarch64), deployed via Docker Compose alongside Home Assistant and Cloudflare Tunnel.

## Development setup

**Local run:**
```bash
cd gastos
pip install -r requirements.txt
export TELEGRAM_TOKEN=<token>
export USERS_JSON='[{"telegram_id": "123456", "name": "Juampi"}]'
export ANTHROPIC_API_KEY=<key>   # only needed for OCR
export DB_PATH=/tmp/gastos.db
python3 app/main.py
```

**Build Docker image** (from repo root — Dockerfile path-copies `gastos/` subdirectory):
```bash
docker build -f gastos/Dockerfile -t gastos .
```

**Run inline module tests** (parser and categorizer have inline test suites):
```bash
python3 gastos/app/parser.py
python3 gastos/app/categorizer.py
```

## Architecture

`main.py` is the entrypoint. It:
1. Loads config from env vars (`TELEGRAM_TOKEN`, `USERS_JSON`, `ANTHROPIC_API_KEY`, `DB_PATH`)
2. Initializes SQLite via `db.py`
3. Schedules a daily backup job (APScheduler, 21:00 ART)
4. Starts Flask dashboard in a daemon thread
5. Starts the Telegram bot (blocks main thread via polling)

**Module responsibilities:**
- `bot.py` — Telegram command handlers and message routing. Holds `pending_ocr` (OCR confirmation flow) and `pending_amount_edit` (waiting for user to type new amount after tapping inline button) module-level dicts keyed by `chat_id`. Uses `TELEGRAM_TOKEN` and `USERS` module-level globals set by `main.py`.
- `parser.py` — Parses free-text messages into `{concept, amount}`. Handles Argentine number formats (dot=thousands, comma=decimal). Returns `None` if no valid amount found.
- `categorizer.py` — Matches concept against keyword list from DB (accent/case-insensitive). Returns `(category_id, subcategory_id)` tuple; both may be `None`.
- `db.py` — All SQLite operations. Uses a `get_conn()` context manager that auto-commits/rollbacks. `DB_PATH` is a module-level global set by `main.py`.
- `dashboard.py` — Flask app. Timestamps stored as UTC in DB; `dashboard.py` converts to Buenos Aires time (UTC-3) for display.
- `ocr.py` — Uses `claude-haiku-4-5-20251001` via the Anthropic SDK to extract `{comercio, monto, fecha}` from ticket images.
- `audio.py` — Voice pipeline. `transcribe()` (OpenAI Whisper `whisper-1`, `es`) → `extract_expenses()` (Claude `claude-haiku-4-5-20251001`) returns `[{concept, amount, confidence}]`. `confidence` (0–1) drives auto-save: `bot.py` registers voice expenses ≥ `AUTOSAVE_CONFIDENCE` (0.9) directly and only queues the rest for inline confirmation.
- `dolar.py` — Uses `claude-haiku-4-5-20251001` to interpret natural-language dollar operations (`parse_dolar` → `{tipo: venta|compra, monto_usd, cotizacion, confidence}` or `None`). Gated by `looks_like_dolar()` (cheap keyword regex). Routed from both `handle_message` (text) and `handle_voice` (audio); high confidence registers directly, low confidence asks inline confirmation (`pending_dolar`). Legacy `CambioDolar <usd> <cotizacion>` command still works and records a sale.
- `backup.py` — Sends `gastos.db` as a Telegram document to all configured users. Called by APScheduler at 21:00 ART and via `POST /admin/backup-now`.
- `seed.py` — Populates default categories, subcategories, and keywords on first DB creation. Also runs idempotent migrations on every startup.

**DB schema** (6 tables): `users`, `categories`, `subcategories`, `keywords`, `expenses`, `fixed_expenses`, `fixed_expense_payments`, `cambios_dolar`. Categories have a protected "Sin categoría" that cannot be edited or deleted. `expenses` and `keywords` have an optional `subcategory_id` FK. `users.color` is assigned from a distinct palette (`_sync_users`) so users are visually separable in the dashboard. `cambios_dolar` has a `tipo` column (`venta`/`compra`, default `venta`).

## Config

Config is loaded exclusively from environment variables at startup — there is no HA Supervisor dependency:

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | Yes | Bot token |
| `USERS_JSON` | Yes | JSON array `[{"telegram_id": "...", "name": "..."}]` |
| `ANTHROPIC_API_KEY` | No | Enables OCR |
| `DB_PATH` | No | Default: `/data/gastos.db` |

On the Pi these live in `~/.env`, loaded by Docker Compose via `env_file: ~/.env`.

## Deployment

The Docker image is published to `ghcr.io/juanfino/lightweightexpensetracker` on every push to `main` via `.github/workflows/docker-publish.yml`. The workflow builds `linux/arm64` and `linux/amd64` images using QEMU. **Deploy to the Pi is manual** — GitHub Actions does not auto-pull.

**Pi:** user `juanfino`, hostname `rbp-casaribera`, IP `192.168.68.72`. Docker Compose at `~/docker-compose.yml`. Data persisted at `~/gastos-data/gastos.db`. Dashboard exposed at `https://expenses.juampifinochietto.com` via Cloudflare Tunnel → `localhost:8090`.

On the Pi, the app runs as a Docker Compose service alongside `homeassistant` and `cloudflared` (all `network_mode: host`). The canonical service definition is `docker-compose.yml` in this repo. Env vars for all services live in `~/.env`.

To update:
```bash
ssh juanfino@192.168.68.72 "docker compose pull gastos && docker compose up -d gastos"
ssh juanfino@192.168.68.72 "docker logs -f gastos"
```

## Versioning

The app version lives in `gastos/config.yaml` (`version` field). Bump it at the end of every session that produces a deployable change:
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
- Confidence-based auto-save: voice expenses and natural-language dollar operations are registered without confirmation when the LLM-reported `confidence` ≥ `AUTOSAVE_CONFIDENCE` (0.9 in `bot.py`); otherwise the user confirms via inline buttons. Auto-saved voice expenses still get an edit/category keyboard so nothing is unrecoverable.
- Dollar operations are detected in both text and voice by a cheap `dolar.looks_like_dolar()` keyword gate before spending an LLM call; `parse_dolar` returns `None` for non-dollar messages so they fall through to normal expense handling.
- User authorization is enforced per-request via `_get_authorized_user()` in `bot.py` — only `telegram_id`s in config are allowed.
- `categorizer.categorize()` returns `(category_id, subcategory_id)` — both can be `None`. All expense creation flows must pass both values.
- Dockerfile build context is the **repo root** (not the `gastos/` subdirectory): `docker build -f gastos/Dockerfile .`
