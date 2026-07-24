# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Before making any code change**, see [AGENTS.md](AGENTS.md) for the mandatory git sync/branch ritual and the documentation-update checklist that applies after every change.

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
- `bot.py` — Telegram command handlers and message routing. Holds per-`chat_id` pending-state dicts (`pending_ocr`, `pending_amount_edit`, `pending_dolar`, `pending_nl_confirm`, `pending_nl_pick`, `pending_fixed_direct`, etc.). Uses `TELEGRAM_TOKEN` and `USERS` module-level globals set by `main.py`. `handle_message` routes hybrid: the deterministic parser is the instant fast path for plain `concept amount`; a keyword heuristic (`_needs_intent`) escalates everything else (edits, taxonomy, queries, richer phrasings) to the intent layer. `_maybe_offer_fixed_link()` runs after every expense-creation path (plain text, NL, voice, OCR) to offer linking to a matching fixed expense.
- `intent.py` — Natural-language intent layer via the Anthropic model using **tool use / function calling** (the only such usage in the project). `route_intent()` classifies a message and returns a structured result dict (`log`/`edit`/`category`/`subcategory`/`report`/`reply`). Tools: `log_expense`, `edit_expense`, `create_category`, `create_subcategory`, `run_report`. Injects taxonomy + the requesting user's recent expenses + ART date so it resolves names→ids and references like "el último" in one round trip. Mutations return structured params (executed by parameterized app code + inline confirmation; logging auto-saves with an edit keyboard); reads are model-generated SQL. Performs no Telegram I/O — `bot.py` turns the result into messages/confirmation flows and re-checks expense ownership before any UPDATE. `edit_expense`'s `changes` can include `fixed_expense` (link by name, or `"ninguno"` to unlink).
- `fixed_matcher.py` — Fixed-expense matching heuristics shared by `bot.py` and `dashboard.py`, so both surfaces agree on what counts as a match: `find_fixed_expense_matches()` (new expense → fixed-expense definition, word-overlap ≥3 chars) and `find_candidate_expenses()` (fixed expense → already-logged unlinked expenses for a period, scored by word overlap + category + amount proximity). `expense_period()` converts an expense's UTC timestamp to a (year, month) in a given tz — a link defaults to the month of the expense's own date, not "today".
- `sqlro.py` — Read-only SQL executor (`run_readonly`) enforcing the query guardrails: `SELECT`/`WITH` only, single statement, physically read-only connection (`mode=ro` URI), statement timeout, row cap. Used by both reporting queries and edit-targeting SELECTs.
- `parser.py` — Parses free-text messages into `{concept, amount, currency}`. ARS is the default; explicit USD/US$/U$S/dólares yields USD. Handles Argentine number formats (dot=thousands, comma=decimal). Returns `None` if no valid amount found.
- `categorizer.py` — Matches concept against keyword list from DB (accent/case-insensitive). Returns `(category_id, subcategory_id)` tuple; both may be `None`.
- `db.py` — All SQLite operations. Uses a `get_conn()` context manager that auto-commits/rollbacks. `DB_PATH` is a module-level global set by `main.py`. `link_expense_to_fixed()`/`unlink_expense_from_fixed()` are the single choke point for attaching/detaching an expense's fixed-expense link (linking forces `category_id`/`subcategory_id` from the fixed expense).
- `dashboard.py` — Flask app. Timestamps stored as UTC in DB; `dashboard.py` converts to Buenos Aires time (UTC-3) for display.
- `ocr.py` — Uses `claude-haiku-4-5-20251001` via the Anthropic SDK to extract `{comercio, monto, fecha}` from ticket images.
- `audio.py` — Voice pipeline. `transcribe()` (OpenAI Whisper `whisper-1`, `es`) → `extract_expenses()` (Claude `claude-haiku-4-5-20251001`) returns `[{concept, amount, confidence}]`. `confidence` (0–1) drives auto-save: `bot.py` registers voice expenses ≥ `AUTOSAVE_CONFIDENCE` (0.9) directly and only queues the rest for inline confirmation.
- `dolar.py` — Uses `claude-haiku-4-5-20251001` to interpret natural-language dollar operations (`parse_dolar` → `{tipo: venta|compra, monto_usd, cotizacion, confidence}` or `None`). Gated by `looks_like_dolar()` (cheap keyword regex). Routed from both `handle_message` (text) and `handle_voice` (audio); high confidence registers directly, low confidence asks inline confirmation (`pending_dolar`). Legacy `CambioDolar <usd> <cotizacion>` command still works and records a sale.
- `backup.py` — Copies `gastos.db` into a local, timestamped file under a `backups/` directory next to the DB (7-day retention). Called by APScheduler at 21:00 ART and via `POST /admin/backup-now`. As of 2.6.0, no longer sends the database file via Telegram — a daily broadcast to every configured user was a data-leak risk once the app becomes multi-tenant; real off-device backups (with restore) land in Phase 1 of `docs/MULTITENANT_PLAN.md`.
- `seed.py` — Populates default categories, subcategories, and keywords on first DB creation. Also runs idempotent migrations on every startup.

**DB schema** (10 tables): `users`, `categories`, `subcategories`, `keywords`, `expenses`, `fixed_expenses`, `cambios_dolar`, `ipc_series`, `reports`, `expense_classifications`. `expenses.currency` and `fixed_expenses.currency` are native ISO `ARS`/`USD` values (default ARS); values are never converted or aggregated across currencies, and linked expenses must match their fixed expense currency. Categories have a protected "Sin categoría" that cannot be edited or deleted. `expenses` and `keywords` have an optional `subcategory_id` FK. `users.color` is assigned from a distinct palette (`_sync_users`) so users are visually separable in the dashboard. `cambios_dolar` has a `tipo` column (`venta`/`compra`, default `venta`). As of 2.0.0, a fixed-expense payment is a property of the expense itself — `expenses.fixed_expense_id` (nullable FK to `fixed_expenses`, `ON DELETE SET NULL`) plus `fixed_expense_year`/`fixed_expense_month` — instead of a separate `fixed_expense_payments` join table; any number of expenses may share the same fixed expense + period.

## Config

Config is loaded exclusively from environment variables at startup — there is no HA Supervisor dependency:

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | Yes | Bot token |
| `USERS_JSON` | Yes | JSON array `[{"telegram_id": "...", "name": "..."}]` |
| `ANTHROPIC_API_KEY` | No | Enables OCR, voice/dollar extraction, and the natural-language intent layer |
| `OPENAI_API_KEY` | No | Enables voice message transcription (Whisper) |
| `DB_PATH` | No | Default: `/data/gastos.db` |
| `DASHBOARD_PORT` | No | Default: `5000` |

On the Pi these live in `~/.env`, loaded by Docker Compose via `env_file: ~/.env`.

## Deployment

The Docker image is published to `ghcr.io/juanfino/lightweightexpensetracker` on every push to `main` via `.github/workflows/docker-publish.yml`. The workflow builds `linux/arm64` and `linux/amd64` images using QEMU. **Deploy to the Pi is manual** — GitHub Actions does not auto-pull.

**Pi:** user `juanfino`, hostname `rbp-casaribera`, IP `192.168.68.72`. Docker Compose at `~/docker-compose.yml`. Data persisted at `~/gastos-data/gastos.db`. Dashboard exposed at `https://expenses.juampifinochietto.com` via Cloudflare Tunnel → `localhost:8090` (the Pi's `~/.env` sets `DASHBOARD_PORT=8090` to free up port 5000 for Frigate; the code's own default, if unset, is 5000).

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
- Fixed-expense linking always forces the expense's `category_id`/`subcategory_id` to the fixed expense's own (`db.link_expense_to_fixed`), overriding whatever the categorizer/NL/OCR guessed — one rule, applied everywhere a link is written, so a recurring bill can't drift category depending on which path registered it.
- Fixed-expense detection is never a gate before saving — every path saves the expense first, then `bot.py`'s `_maybe_offer_fixed_link()` (or the dashboard's `suggested_fixed_expense` response) offers to link it. It only ever suggests; it never auto-links.
- ARS is the dashboard/report default. USD is presented and aggregated separately; IPC only applies to ARS. `cambios_dolar` remains exchange-operation history, not a USD expense conversion source.
