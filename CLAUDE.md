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
- `bot.py` — Telegram command handlers and message routing. Holds per-`chat_id` pending-state dicts (`pending_ocr`, `pending_amount_edit`, `pending_dolar`, `pending_nl_confirm`, `pending_nl_pick`, `pending_fixed_direct`, etc.). Uses `TELEGRAM_TOKEN` and `USERS` module-level globals set by `main.py`. `handle_message` routes hybrid: the deterministic parser is the instant fast path for plain `concept amount`; a keyword heuristic (`_needs_intent`) escalates everything else (edits, taxonomy, queries, richer phrasings) to the intent layer. `_maybe_offer_fixed_link()` runs after every expense-creation path (plain text, NL, voice, OCR) to offer linking to a matching fixed expense.
- `intent.py` — Natural-language intent layer via the Anthropic model using **tool use / function calling** (the only such usage in the project). `route_intent()` classifies a message and returns a structured result dict (`log`/`edit`/`category`/`subcategory`/`report`/`reply`). Tools: `log_expense`, `edit_expense`, `create_category`, `create_subcategory`, `run_report`. Injects taxonomy + the requesting user's recent expenses + ART date so it resolves names→ids and references like "el último" in one round trip. Mutations return structured params (executed by parameterized app code + inline confirmation; logging auto-saves with an edit keyboard); reads are model-generated SQL. Performs no Telegram I/O — `bot.py` turns the result into messages/confirmation flows and re-checks expense ownership before any UPDATE. `edit_expense`'s `changes` can include `fixed_expense` (link by name, or `"ninguno"` to unlink).
- `fixed_matcher.py` — Fixed-expense matching heuristics shared by `bot.py` and `dashboard.py`, so both surfaces agree on what counts as a match: `find_fixed_expense_matches()` (new expense → fixed-expense definition, word-overlap ≥3 chars) and `find_candidate_expenses()` (fixed expense → already-logged unlinked expenses for a period, scored by word overlap + category + amount proximity). `expense_period()` converts an expense's UTC timestamp to a (year, month) in a given tz — a link defaults to the month of the expense's own date, not "today".
- `sqlro.py` — Read-only SQL executor (`run_readonly`) enforcing `SELECT`/`WITH` only, one statement, the `gastos_readonly` PostgreSQL role, tenant RLS, statement timeout and row cap.
- `parser.py` — Parses free-text messages into `{concept, amount, currency}`. ARS is the default; explicit USD/US$/U$S/dólares yields USD. Handles Argentine number formats (dot=thousands, comma=decimal). Returns `None` if no valid amount found.
- `categorizer.py` — Matches concept against keyword list from DB (accent/case-insensitive). Returns `(category_id, subcategory_id)` tuple; both may be `None`.
- `db.py` — Raw PostgreSQL operations. `get_conn()` selects the RLS-bound application role and transaction-local `app.family_id`, then auto-commits/rolls back. Fixed-expense link helpers remain the single write choke point. `get_daily_quote()` reads global curated content and selects it with a stable SHA-256 index over family-local date + family id; failures return `None` because quotes are decorative.
- `money.py` — Central exact-money boundary. Application amounts are `Decimal`, quantized to two places with the single `ROUND_HALF_UP` policy. Derived percentages/ratios cross to `float` explicitly; HTTP, report persistence and LLM payloads convert `Decimal` to JSON numbers only at serialization boundaries.
- `dashboard.py` — Flask app. Timestamps stored as UTC in DB; `dashboard.py` converts to Buenos Aires time (UTC-3) for display. The dashboard-only quote uses the authenticated family's IANA timezone.
- `auth.py` — web identity and family-access layer: opaque server-side sessions, hashed one-time codes, Resend delivery, Turnstile verification, rate limiting, Google identity linking, invitation lifecycle, logical member removal, ownership transfer, and account/family creation. Platform lookups use transaction-local `gastos_superadmin`; `dashboard.py` resolves the authenticated active membership once per request.
- `llm_limits.py` — per-family LLM admission control: defaults to 100 routine calls/day and 15 report generations/month, accepts optional tenant-scoped overrides, and permits at most two concurrent LLM calls. Calendar boundaries use the family's configured timezone.
- `dossier.py` — Deterministic aggregation (no LLM) for the monthly AI report shown at `/resumenes`. `build_dossier(year, month)` returns ARS and USD as two parallel, same-shape blocks under `currencies.{ARS,USD}` (`base`, `contrasts`, `delta_attribution`, `outliers`, `fixed_expenses`, `taxonomy`, `registration_coverage`, `variable_expenses`, `months_available`, and — once `report.py` classifies expenses — `partition`). No aggregate ever sums an ARS amount with a USD one; the one exception is the top-level `equivalence` block, a reference-only ARS-equivalent of the month's USD spending at the family's own dollar rate (this month's sale rate → this month's purchase rate → most recent rate within 12 months → unavailable), used only to convey magnitude and never fed into a contrast or total. `contrasts` only applies IPC deflation for ARS (`apply_ipc`); USD entries carry `real_not_applicable` instead of real figures. Cash basis throughout: grouped by the expense's own ART-adjusted date via `db.get_expenses_for_period_art`/`get_expenses_excluding_period`, not by `fixed_expense_year`/`month`.
- `report.py` — Orchestrates `dossier.py` → `report_ai.py` classify → `report_ai.py` analyze → persist, append-only (every `generate_report()` call inserts a new `reports` row). `_build_partitions()` aggregates the classification call's per-expense recurring/exceptional labels into one partition per currency (`fixed_total` comes from that currency's own `fixed_expenses.total_paid`). If either LLM call fails, the dossier-only report is still persisted with `llm_ok=False`. `fingerprint()` hashes the period's local facts (currency-agnostic) for the drift badge.
- `report_ai.py` — The two LLM calls behind the monthly report, via `claude-opus-4-8` by default (`REPORT_ANTHROPIC_MODEL`), configured separately from the Haiku extraction calls elsewhere since this runs a handful of times a month and quality matters more than cost. `classify_expenses()` labels each variable expense (both currencies) `recurring`/`exceptional`, never comparing amounts across currencies. `analyze()` narrates over the full dossier into `{headline, summary, findings, questions}`; the system prompt requires every cited amount to carry its currency symbol (`$`/`U$S`), forbids treating USD as IPC-adjusted, and — the materiality rule — requires the headline/summary to mention USD spending explicitly when `equivalence.usd_share_pct >= 10` or when there's USD spend with no rate to convert it.
- `dashboard.py` superadmin surface — `/superadmin` uses dedicated cross-family queries under `gastos_superadmin` (`BYPASSRLS`) for operational metrics, LLM usage/cost, manually maintained infrastructure-cost assumptions, quota overrides and recent failures. Quota/cost mutations require the normal session + CSRF controls. It never impersonates a member or mutates tenant business data.
- `system_errors` persistence — unhandled web and Telegram exceptions are logged to container stdout and, when a family has already been resolved, stored tenant-scoped for `/superadmin`. They are not sent through the family Telegram bot. Pre-auth/unresolved errors cannot be stored in this RLS table.
- `ocr.py` — Uses `claude-haiku-4-5-20251001` via the Anthropic SDK to extract `{comercio, monto, fecha}` from ticket images.
- `audio.py` — Voice pipeline. `transcribe()` (OpenAI Whisper `whisper-1`, `es`) → `extract_expenses()` (Claude `claude-haiku-4-5-20251001`) returns `[{concept, amount, confidence}]`. `confidence` (0–1) drives auto-save: `bot.py` registers voice expenses ≥ `AUTOSAVE_CONFIDENCE` (0.9) directly and only queues the rest for inline confirmation.
- `dolar.py` — Uses `claude-haiku-4-5-20251001` to interpret natural-language dollar operations (`parse_dolar` → `{tipo: venta|compra, monto_usd, cotizacion, confidence}` or `None`). Gated by `looks_like_dolar()` (cheap keyword regex). Routed from both `handle_message` (text) and `handle_voice` (audio); high confidence registers directly, low confidence asks inline confirmation (`pending_dolar`). Legacy `CambioDolar <usd> <cotizacion>` command still works and records a sale.
- `backup.py` — Runs a custom-format `pg_dump`, uploads it to private R2 and verifies the remote object. Called daily and by the admin endpoint; restore is SSH-only.
- `export_data.py` — Builds tenant-scoped RFC 4180 CSV exports with UTF-8 BOM, family-local ISO timestamps, spreadsheet-formula neutralization and the complete ZIP exit path.
- `seed.py` — `create_family_defaults(conn, family_id)` creates generic taxonomy for a new family. Schema changes are Alembic-only.

**DB schema:** platform/global tables `families`, `users`, `memberships`, `sessions`, `otp_codes`, `oauth_identities`, `invitations`, `telegram_link_tokens`, `infrastructure_cost_settings`, `quotes`; tenant tables `categories`, `subcategories`, `keywords`, `expenses`, `fixed_expenses`, `income_categories`, `incomes`, `shopping_items`, `cambios_dolar`, `ipc_series`, `reports`, `expense_classifications`, `llm_calls`, `family_quota_overrides`, `system_errors`. `quotes` is installation-level curated content with no `family_id`, tenant RLS or CRUD UI. Membership removals are logical (`active=false`) because expenses and incomes retain composite references to the historical membership; a partial unique index permits only one active family per user. Telegram linking stores only SHA-256 token hashes. Tenant tables carry `family_id NOT NULL` with forced RLS and composite foreign keys preventing cross-family references.

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
- Dollar operations are detected in both text and voice by a cheap `dolar.looks_like_dolar()` keyword gate before spending an LLM call; `parse_dolar` returns `None` for non-dollar messages so they fall through to normal expense handling.
- Telegram identity is resolved once per update by `_get_authorized_user()` in `bot.py`; self-service links and legacy `USERS_JSON` identities both resolve through the active database membership.
- `categorizer.categorize()` returns `(category_id, subcategory_id)` — both can be `None`. All expense creation flows must pass both values.
- Dockerfile build context is the **repo root** (not the `gastos/` subdirectory): `docker build -f gastos/Dockerfile .`
- Fixed-expense linking always forces the expense's `category_id`/`subcategory_id` to the fixed expense's own (`db.link_expense_to_fixed`), overriding whatever the categorizer/NL/OCR guessed — one rule, applied everywhere a link is written, so a recurring bill can't drift category depending on which path registered it.
- Fixed-expense detection is never a gate before saving — every path saves the expense first, then `bot.py`'s `_maybe_offer_fixed_link()` (or the dashboard's `suggested_fixed_expense` response) offers to link it. It only ever suggests; it never auto-links.
- ARS is the dashboard default for logging and most screens. In the monthly report (`/resumenes`), ARS and USD are two first-class, parallel currency blocks with the same visual/structural weight — not a pesos report with a USD footnote — but they're never summed: every aggregate, contrast and partition is currency-scoped, and the one cross-currency number (`equivalence`, dossier.py) is an explicitly-labeled reference-only ARS-equivalent used solely to convey magnitude. IPC/"real" figures only apply to ARS. `cambios_dolar` remains exchange-operation history, not a USD expense conversion source.
