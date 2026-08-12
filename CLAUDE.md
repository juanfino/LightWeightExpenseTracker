# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Before making any code change**, see [AGENTS.md](AGENTS.md) for the mandatory git sync/branch ritual and the documentation-update checklist that applies after every change.

## What this is

A family expense tracker that records spending via Telegram and shows it in a web dashboard. Users send messages like `Supermercado 150000` to a Telegram bot; the app parses, categorizes, and stores the expense in PostgreSQL. Runs as Docker containers on a Raspberry Pi 4 (aarch64), deployed via Docker Compose alongside Home Assistant and Cloudflare Tunnel.

## Development setup

**Local run:**
```bash
cd gastos
pip install -r requirements.txt
export TELEGRAM_TOKEN=<token>
export TELEGRAM_BOT_USERNAME=<bot_username_without_at>
export PUBLIC_DASHBOARD_URL=https://mangoteca.juampifinochietto.com
export USERS_JSON='[{"telegram_id": "123456", "name": "Juampi"}]'
export ANTHROPIC_API_KEY=<key>   # only needed for OCR
export DATABASE_URL=postgresql://gastos:password@localhost:5432/gastos
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
1. Loads bot, database and web-auth config from environment variables
2. Applies Alembic migrations and initializes PostgreSQL via `db.py`
3. Schedules a daily backup job (APScheduler, 21:00 ART)
4. Starts Flask dashboard in a daemon thread
5. Starts the Telegram bot (blocks main thread via polling)

**Module responsibilities:**
- `bot.py` — Telegram command handlers and message routing. Holds per-`chat_id` pending-state dicts (`pending_ocr`, `pending_amount_edit`, `pending_exchange`, `pending_nl_confirm`, `pending_nl_pick`, `pending_fixed_direct`, etc.). Uses `TELEGRAM_TOKEN` and `USERS` module-level globals set by `main.py`. `handle_message` routes hybrid: the deterministic parser is the instant fast path for plain `concept amount`; a keyword heuristic (`_needs_intent`) escalates everything else (edits, taxonomy, queries, richer phrasings) to the intent layer. `_maybe_offer_fixed_link()` runs after every expense-creation path (plain text, NL, voice, OCR) to offer linking to a matching fixed expense.
- `intent.py` — Natural-language intent layer via the Anthropic model using **tool use / function calling** (the only such usage in the project). `route_intent()` classifies a message and returns a structured result dict (`log`/`edit`/`category`/`subcategory`/`report`/`reply`). Tools: `log_expense`, `edit_expense`, `create_category`, `create_subcategory`, `run_report`. Injects taxonomy + the requesting user's recent expenses + ART date so it resolves names→ids and references like "el último" in one round trip. Mutations return structured params (executed by parameterized app code + inline confirmation; logging auto-saves with an edit keyboard); reads are model-generated SQL. Performs no Telegram I/O — `bot.py` turns the result into messages/confirmation flows and re-checks expense ownership before any UPDATE. `edit_expense`'s `changes` can include `fixed_expense` (link by name, or `"ninguno"` to unlink).
- `fixed_matcher.py` — Fixed-expense matching heuristics shared by `bot.py` and `dashboard.py`, so both surfaces agree on what counts as a match: `find_fixed_expense_matches()` (new expense → fixed-expense definition, word-overlap ≥3 chars) and `find_candidate_expenses()` (fixed expense → already-logged unlinked expenses for a period, scored by word overlap + category + amount proximity). `expense_period()` converts an expense's UTC timestamp to a (year, month) in a given tz — a link defaults to the month of the expense's own date, not "today".
- `sqlro.py` — Read-only SQL executor (`run_readonly`) enforcing `SELECT`/`WITH` only, one statement, the `gastos_readonly` PostgreSQL role, tenant RLS, statement timeout and row cap.
- `pgcompat.py` — DB-API compatibility shim so the rest of the codebase keeps sqlite-style `?` placeholders, `Row`/`Cursor` objects and `lastrowid` while PostgreSQL (via `psycopg_pool.ConnectionPool`) is the only storage engine. `Row` preserves psycopg `Decimal` values instead of coercing `NUMERIC` to `float`. Owns the named connection pools (`pool()`/`current_pool()`/`select_pool()`) and the `contextvars`-based transaction-local `family_id`/`user_id` that `db.get_conn()` reads to set `app.family_id` for RLS.
- `llm_usage.py` — Shared best-effort LLM cost/latency accounting: `record()` computes an estimated USD cost from a fixed per-model rate table (token-based for Anthropic models, per-audio-minute for `whisper-1`) and writes one row to `llm_calls` per call. Called by every module that makes an LLM/Whisper call (`ocr.py`, `audio.py`, `exchange.py`, `intent.py`, `report_ai.py`).
- `currency_detection.py` — Shared cheap currency detector for parser, voice and exchange routing. Codes/symbols come from the global catalogue; colloquial aliases are centralized. `$` and generic “pesos” mean the family default; an unknown ISO-like suffix fails visibly.
- `parser.py` — Parses free-text messages into `{concept, amount, currency}` using the shared detector and `families.default_currency`. Handles Argentine number formats and strips explicit catalogue currencies from the concept.
- `categorizer.py` — Matches concept against keyword list from DB (accent/case-insensitive). Returns `(category_id, subcategory_id)` tuple; both may be `None`.
- `db.py` — Raw PostgreSQL operations. `get_conn()` selects the RLS-bound application role and transaction-local `app.family_id`, then auto-commits/rolls back. `SUPPORTED_CURRENCIES` and currency metadata are loaded from the global `currencies` reference table after migrations; `families.default_currency` is the default read path for business inputs with no explicit currency. `period_currency_order()` centralizes the period rule used by reports and summaries: family default first, then catalogue order for currencies actually present, with no cross-currency sum. Fixed-expense link helpers remain the single write choke point. `get_daily_quote()` reads global curated content and selects it with a stable SHA-256 index over family-local date + family id; failures return `None` because quotes are decorative.
- `money.py` — Central exact-money and server-formatting boundary. Application amounts are `Decimal`, quantized to two places with the single `ROUND_HALF_UP` policy. Display precision and symbols come from currency metadata; separators come independently from the reader format (currently Rioplatense Spanish). Derived percentages/ratios cross to `float` explicitly; HTTP, report persistence and LLM payloads convert `Decimal` to JSON numbers only at serialization boundaries. `static/money.js` is the single corresponding browser formatter, configured once by `base.html` with the catalogue and reader locale.
- `static/dialogs.js` — Shared browser dialog boundary for authenticated dashboard pages. `MangotecaDialog.alert()`, `.confirm()` and `.prompt()` render the single accessible `<dialog>` owned by `base.html`; destructive HTML forms can opt into the same async confirmation through `data-confirm-*` attributes. Do not add native `window.alert`, `window.confirm` or `window.prompt` calls.
- `dashboard.py` — Flask app. Timestamps stored as UTC in DB; `dashboard.py` converts to Buenos Aires time (UTC-3) for display. The dashboard-only quote uses the authenticated family's IANA timezone. It also owns the shared accounting-period boundary for Dashboard/Movimientos/Ingresos/Fijos/Cambios/Resúmenes: canonical `?period=YYYY-MM`, explicit URL over a dedicated unsigned cookie, then family-local current month; Movimientos' `year`/`month` remain separate local filters.
- `auth.py` — web identity and family-access layer: opaque server-side sessions, hashed one-time codes, Resend delivery, Turnstile verification, rate limiting, Google identity linking, invitation lifecycle, logical member removal, ownership transfer, and account/family creation. Platform lookups use transaction-local `gastos_superadmin`; `dashboard.py` resolves the authenticated active membership once per request.
- `llm_limits.py` — per-family LLM admission control: defaults to 100 routine calls/day and 15 report generations/month, accepts optional tenant-scoped overrides, and permits at most two concurrent LLM calls. Calendar boundaries use the family's configured timezone.
- `dossier.py` — Deterministic aggregation (no LLM) for `/resumenes`. `build_dossier(year, month)` derives same-shape currency blocks from the currencies actually used in the period plus `families.default_currency`, ordered with the default first. Every aggregate remains currency-scoped. `equivalence.items` values each non-default total in the default currency using only direct family exchange history (current direct → current reverse → latest pair operation within 12 months → unavailable); these figures are reference-only and never feed a total, contrast, partition or forecast. `inflation.has_series(currency)` controls real figures; today only ARS has a series and other currencies carry `real_not_applicable`. Cash basis throughout: grouped by the expense's own ART-adjusted date.
- `report.py` — Orchestrates `dossier.py` → `report_ai.py` classify → `report_ai.py` analyze → persist, append-only (every `generate_report()` call inserts a new `reports` row). `_build_partitions()` aggregates the classification call's per-expense recurring/exceptional labels into one partition per currency (`fixed_total` comes from that currency's own `fixed_expenses.total_paid`). If either LLM call fails, the dossier-only report is still persisted with `llm_ok=False`. `fingerprint()` hashes the period's local facts (currency-agnostic) for the drift badge.
- `inflation.py` — IPC Nacional fetch/cache/estimate/deflate, ARS only; `refresh()` hits `apis.datos.gob.ar` (series `148.3_INIVELNAL_DICI_M_26`), `deflate()` converts a nominal amount between two periods' prices and returns `None` (not a silent nominal fallback) when an index is missing. `has_series(currency)` is the capability check other modules use — currently true only for ARS.
- `forecast.py` — Deterministic next-month forecast persisted with each report for the dossier's derived currency set. Per currency it combines active fixed definitions, habitual-category median/IQR estimates, and a historical tail/IQR; below three months it exposes fixed only. Inflation factors apply only when `inflation.py` declares a series (currently ARS) and use observations through the cutoff. Stored forecasts are never recomputed; target-month actuals are attached read-only.
- `report_ai.py` — The two LLM calls behind the monthly report, via `claude-opus-4-8` by default (`REPORT_ANTHROPIC_MODEL`). `classify_expenses()` labels every variable expense `recurring`/`exceptional`, never comparing currencies. `analyze()` hard rules require the catalogue symbol on every amount, permit real figures only when supplied, keep equivalences approximate/reference-only, and force headline/summary to mention every material or unconvertible non-default-currency spend.
- `report_preferences.py` — Canonical defaults and validation for per-family narrative preferences. Emphasis choices map only to deterministic material present in the dossier, including the optional forecast; the bounded focus text is treated as untrusted prompt data.
- `dashboard.py` superadmin surface — `/superadmin` uses dedicated cross-family queries under `gastos_superadmin` (`BYPASSRLS`) for operational metrics, LLM usage/cost, manually maintained infrastructure-cost assumptions, quota overrides and recent failures. Quota/cost mutations require the normal session + CSRF controls. It never impersonates a member or mutates tenant business data.
- `system_errors` persistence — unhandled web and Telegram exceptions are logged to container stdout and, when a family has already been resolved, stored tenant-scoped for `/superadmin`. They are not sent through the family Telegram bot. Pre-auth/unresolved errors cannot be stored in this RLS table.
- `ocr.py` — Uses `claude-haiku-4-5-20251001` via the Anthropic SDK to extract `{comercio, monto, fecha}` from ticket images.
- `audio.py` — Voice pipeline. `transcribe()` (OpenAI Whisper `whisper-1`, `es`) → `extract_expenses()` (Claude `claude-haiku-4-5-20251001`) returns `[{concept, amount, confidence}]`. `confidence` (0–1) drives auto-save: `bot.py` registers voice expenses ≥ `AUTOSAVE_CONFIDENCE` (0.9) directly and only queues the rest for inline confirmation.
- `exchange.py` — Uses `claude-haiku-4-5-20251001` to extract directional conversions into given/received amount+currency pairs. `looks_like_exchange()` remains a cheap verb+catalogue-marker gate; buy/sell wording is derived only relative to the family default. Legacy `CambioDolar <usd> <cotizacion>` records USD→ARS.
- `backup.py` — Runs a custom-format `pg_dump`, uploads it to private R2 and verifies the remote object. Called daily and by the admin endpoint; restore is SSH-only.
- `export_data.py` — Builds tenant-scoped RFC 4180 CSV exports with UTF-8 BOM, family-local ISO timestamps, spreadsheet-formula neutralization and the complete ZIP exit path.
- `seed.py` — `create_family_defaults(conn, family_id)` creates generic taxonomy for a new family. Schema changes are Alembic-only.

**DB schema:** platform/global tables `families`, `users`, `memberships`, `sessions`, `otp_codes`, `oauth_identities`, `invitations`, `telegram_link_tokens`, `infrastructure_cost_settings`, `quotes`, `currencies`; tenant tables `categories`, `subcategories`, `keywords`, `expenses`, `fixed_expenses`, `income_categories`, `incomes`, `shopping_items`, `cambios_dolar`, `ipc_series`, `reports`, `expense_classifications`, `report_forecasts`, `family_report_preferences`, `llm_calls`, `family_quota_overrides`, `system_errors`. `currencies` is installation-level reference data with no `family_id`, RLS or CRUD UI; it seeds ARS, USD, BRL and EUR and supplies code, symbol and decimal count. `families.default_currency` is FK-backed, starts as ARS and is owner-writable from `/familia`; changing it never converts existing rows. `report_forecasts.currency` also references the catalogue and stores one immutable row per producing report/currency. Tenant tables carry `family_id NOT NULL` with forced RLS and composite foreign keys preventing cross-family references.

`cambios_dolar` retains its legacy table name but stores `amount_given/currency_given` → `amount_received/currency_received`; `rate_received_per_given` is the explicit units-received-per-unit-given convention. `tipo` is not stored. Migration `0014` maps legacy sales to USD→ARS and purchases to ARS→USD.

## Config

Config is loaded exclusively from environment variables at startup — there is no HA Supervisor dependency:

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | Yes | Bot token |
| `TELEGRAM_BOT_USERNAME` | Yes | Bot username without `@`; used for one-tap linking deep links |
| `PUBLIC_DASHBOARD_URL` | No | Public dashboard base URL sent to unlinked chats; defaults to Mangoteca |
| `USERS_JSON` | Yes | JSON array `[{"telegram_id": "...", "name": "...", "email": "optional@example.com"}]`; optional email links a legacy Telegram identity to web auth, NULL-only |
| `AUTH_SECRET_KEY` | Yes | Random secret for OAuth/pre-auth signed state |
| `AUTH_BOOTSTRAP_EMAIL` | Yes | Initial web email for the existing family-1 owner; only fills a NULL email |
| `SUPERADMIN_EMAIL` | Yes | Sole superadmin identity; applied at startup and never writable over HTTP |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Yes | Google OAuth web client |
| `RESEND_API_KEY` | Yes | Transactional OTP email |
| `RESEND_FROM_EMAIL` | No | Verified sender address |
| `TURNSTILE_SECRET` | Yes | Private Turnstile server-verification secret; the public site key is embedded in the app |
| `ANTHROPIC_API_KEY` | No | Enables OCR, voice/dollar extraction, and the natural-language intent layer |
| `OPENAI_API_KEY` | No | Enables voice message transcription (Whisper) |
| `DATABASE_URL` | Yes | PostgreSQL connection URL |
| `R2_ENDPOINT`, `R2_BUCKET` | Yes | Cloudflare R2 backup destination |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Yes | Bucket-scoped credentials |
| `DASHBOARD_PORT` | No | Default: `5000` |

On the Pi these live in `~/.env`, loaded by Docker Compose via `env_file: ~/.env`.

## Deployment

The Docker image is published to `ghcr.io/juanfino/lightweightexpensetracker` on every push to `main` via `.github/workflows/docker-publish.yml`. The workflow builds `linux/arm64` and `linux/amd64` images using QEMU. **Deploy to the Pi is manual** — GitHub Actions does not auto-pull.

**Pi:** user `juanfino`, hostname `rbp-casaribera`, IP `192.168.68.72`. Docker Compose at `~/docker-compose.yml`. PostgreSQL data persisted at `~/postgres-data`. Dashboard exposed at `https://mangoteca.juampifinochietto.com` via Cloudflare Tunnel → `localhost:8090` (the Pi's `~/.env` sets `DASHBOARD_PORT=8090` to free up port 5000 for Frigate; the code's own default, if unset, is 5000).

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
- Currency exchanges are detected in text and voice by `exchange.looks_like_exchange()`, which requires both a catalogue currency signal and an exchange verb/arrow before spending an LLM call; an ordinary `Hotel 200 EUR` stays on the deterministic expense path.
- Telegram identity is resolved once per update by `_get_authorized_user()` in `bot.py`; self-service links and legacy `USERS_JSON` identities both resolve through the active database membership.
- `categorizer.categorize()` returns `(category_id, subcategory_id)` — both can be `None`. All expense creation flows must pass both values.
- Dockerfile build context is the **repo root** (not the `gastos/` subdirectory): `docker build -f gastos/Dockerfile .`
- Fixed-expense linking always forces the expense's `category_id`/`subcategory_id` to the fixed expense's own (`db.link_expense_to_fixed`), overriding whatever the categorizer/NL/OCR guessed — one rule, applied everywhere a link is written, so a recurring bill can't drift category depending on which path registered it.
- Fixed-expense detection is never a gate before saving — every path saves the expense first, then `bot.py`'s `_maybe_offer_fixed_link()` (or the dashboard's `suggested_fixed_expense` response) offers to link it. It only ever suggests; it never auto-links.
- ARS is the initial family default. The owner can select any catalogue currency in `/familia`; this changes future implicit input and the primary block of future reports but never rewrites history. `/resumenes` keeps the default block expanded and additional period currencies in disclosure panels. Aggregates, contrasts, partitions and forecasts are currency-scoped; `equivalence.items` is reference-only and based solely on direct family exchange operations. IPC/"real" figures apply only where `inflation.py` owns a series (currently ARS).
