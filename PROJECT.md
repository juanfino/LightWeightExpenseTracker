# LightWeightExpenseTracker

Family expense tracker. Users send plain-text messages to a Telegram bot; the app parses, categorizes, and persists ARS/USD expenses to PostgreSQL. A Flask dashboard provides monthly/annual visualizations, history, and configuration.

- **Version:** 7.2.0 (canonical source: `gastos/config.yaml`)
- **Dashboard:** https://mangoteca.juampifinochietto.com
- **Repo:** https://github.com/juanfino/LightWeightExpenseTracker

## Architecture

**Infrastructure:** Raspberry Pi 4 (SSD), Raspberry Pi OS Lite, Docker Compose. User `juanfino`, hostname `rbp-casaribera`, IP `192.168.68.72`. PostgreSQL data at `~/postgres-data`; the final SQLite snapshot is retained only for rollback.

**Services** (all `network_mode: host`):
- `gastos` — Flask dashboard + Telegram bot (same process, separate thread)
- `postgres` — PostgreSQL 17, healthchecked before `gastos` starts
- `cloudflared` — Cloudflare Tunnel, exposes `localhost:8090` as `mangoteca.juampifinochietto.com` (dashboard's code default is port 5000; the Pi's `~/.env` sets `DASHBOARD_PORT=8090` to free up 5000 for Frigate)
- `homeassistant` — unrelated, colocated

**Access:** application-owned Google OAuth/email OTP login. Phase 3 was deployed and verified behind Cloudflare Access, then the `expenses` Access application was deleted on 2026-07-27; the Cloudflare Tunnel and Turnstile remain active. Phase 4 was deployed and verified on 2026-07-27: `/familia` is live, Cele's legacy Telegram identity is linked to one web user/member without duplication, and exactly one user is bootstrapped as superadmin. Google OAuth is External/In production, requests only basic identity scopes (`openid`, `email`, `profile`) and always sends `prompt=select_account`. `cloudflared` exposes the Pi's host port 8090.

**Process model:** Flask runs in a daemon thread; `python-telegram-bot` long polling blocks the main thread. Known tradeoff, accepted.

**Database:** PostgreSQL 17 with Alembic. Platform tables are `families`, `users`, `memberships`, `sessions`, `otp_codes`, `oauth_identities`, `invitations` and `telegram_link_tokens`; removing a member keeps an inactive historical membership, while a partial unique index permits only one active family per user. Tenant tables are `categories`, `subcategories`, `keywords`, `expenses`, `fixed_expenses`, `cambios_dolar`, `ipc_series`, `reports`, `expense_classifications` and `llm_calls`. Tenant rows carry `family_id NOT NULL` with forced RLS on transaction-local `app.family_id`; composite tenant foreign keys reject cross-family references. Normal and read-only roles remain subject to RLS; `gastos_superadmin` is the dedicated `BYPASSRLS` role. Amounts retain native ARS/USD and timestamps are UTC; families store their display timezone.

Phase 5 adds the platform table `telegram_link_tokens` (no `family_id`): only SHA-256 token hashes are stored, tokens expire after 15 minutes and are single-use.

**Backup:** Daily at 21:00 ART via APScheduler — custom-format `pg_dump` uploaded to private Cloudflare R2 and remotely size-verified. R2 retains 90 days. Restore is SSH-only (`docs/RUNBOOK.md`) and was tested from production.

## Stack

- Python, Flask, PostgreSQL 17, psycopg 3, Alembic
- `python-telegram-bot` v20 (async, long polling)
- Anthropic API (`claude-haiku-4-5-20251001`) for OCR receipt scanning, voice/dollar extraction, and the natural-language intent layer (tool use / function calling); `claude-opus-4-8` (configurable, separate model tier) for the monthly report's classification and narration calls (structured JSON outputs, no tool use)
- The national open-data time-series API at `apis.datos.gob.ar` (IPC Nacional series) — free, unauthenticated, no API key. The app's first external dependency outside Anthropic/OpenAI; see **Monthly AI report** below for its failure mode
- Docker (Alpine base), multi-arch (`linux/arm64`, `linux/amd64`)
- GitHub Actions → `ghcr.io/juanfino/lightweightexpensetracker` (public registry, auto-build on merge to `main`)
- Deploys are **manual**: `docker compose pull gastos && docker compose up -d gastos` on the Pi

## Key Features

- **Expense entry via Telegram:** plain text, e.g. `Supermercado 150000`
- **Natural-language intent layer:** conversational messages that aren't the plain `concept amount` form route through a Claude tool-use layer (`intent.py`) covering logging, editing, taxonomy management and read-only reports. Model-generated reads run through `sqlro.py`: one `SELECT`/`WITH`, PostgreSQL read-only role, tenant RLS, statement timeout and row cap. Mutations stay parameterized and Telegram users may edit only their own expenses.
- **Auto-categorization:** keyword matching (two-level: category + subcategory). Silent inference — no extra prompts.
- **OCR receipt scanning:** send a photo; bot extracts `{comercio, monto, fecha}` via Anthropic Vision, prompts for confirmation before saving
- **Voice expense entry:** send a voice note (e.g. "ferretería diez mil pesos"); bot transcribes with OpenAI Whisper, normalizes written numbers to digits via Claude, and prompts for confirmation before saving
- **Argentine number formatting:** `.` = thousands separator, `,` = decimal (e.g. `$5.580,00`). `_parse_monto()` handles both notations; `100.000` → 100000, `2.500,50` → 2500.5
- **Gastos Fijos:** recurring fixed expense tracking; `/fijos` shows the month's payment status with inline buttons to register a payment or search for one already logged. Detection runs downstream of expense creation on **every** input path (plain text, NL, voice, OCR, dashboard manual add) — not just the plain-text fast path — via a shared `fixed_matcher.py` (word-overlap ≥3 chars) so the fixed/variable split doesn't depend on how the user happened to log the expense. Linking always forces the expense's category/subcategory to the fixed expense's own, so a recurring bill can't drift category month to month. "✓ Ya lo pagué" searches already-logged, unlinked expenses for the period (concept overlap + category + amount proximity) and offers to link one instead of just flagging "paid" with no amount; explicitly declining still lets the user log the amount directly. The link (+ period) is an ordinary, editable field on the expense, alongside category/subcategory. The date is also editable everywhere expenses are (dashboard history row, `/editar ID fecha DD/MM/AAAA`, NL edit — `"el gasto 124 fue el 15 de junio"`); a date-only edit preserves the fixed-expense period it's linked to (period and date are independent — see 2.1.0). Registering a past-period payment (the dashboard's "+ Registrar pago"/"ya lo pagué" flow when browsing a month other than the current one) defaults the new expense's date within that period instead of today, and the picker is constrained to that month
- **USD/ARS exchange rate tracking:** natural-language ("vendí 500 dólares a 1700", "compré 1000 dólares a 1550") via `dolar.py`, gated by a cheap keyword check (`looks_like_dolar`) before spending an LLM call; confidence-based auto-save, same as voice. Legacy `CambioDolar <usd> <cotizacion>` command still works (always records a sale). Dedicated dashboard page (`/dolares`) with history, monthly summary, and historical rate chart
- **Flask dashboard:** mobile-friendly, per-member filter, Chart.js visualizations (monthly, annual, weekly, by category, last-6-months trend), sortable/filterable history with inline edit, full category/subcategory/keyword CRUD, fixed-expense CRUD, DB backup/restore panel. Visual identity redesigned to amber/orange (from violet) across all 6 original screens as of 1.15.0–1.17.0 — see **Screens** below
- **Monthly AI-generated report** (2.3.0): `/resumenes` — see dedicated section below
- **Users:** Juampi and Cele, both onboarded and actively using the app
- **Family management:** `/familia` lets the owner generate/revoke seven-day single-use invitation links, rename the family, logically remove members, transfer ownership, or delete the family with exact-name confirmation. Invitees join as members through Google or email OTP. `SUPERADMIN_EMAIL` is startup-only; no HTTP path can change the flag.
- **Telegram linking and LLM safeguards:** `/vincular-telegram` provides a 15-minute, one-use deep link and QR with live confirmation; unknown chats get linking help, groups are rejected, and unlinking lives in `/familia`. Routine AI calls are capped at 100/family/day, Resúmenes at 15 generations/family/month, and `llm_limits.py` permits at most two concurrent LLM calls per family. Unhandled bot/web errors alert the linked superadmin Telegram with tenant/user context.

## Monthly AI report

A retrospective on demand, not a schedule (scheduling/Telegram delivery are a follow-up). The governing rule: the model never does arithmetic. Every number is computed by `dossier.py` from SQL aggregation; the model only narrates and makes one bounded judgment call.

- **`dossier.py`** builds a deterministic snapshot for a period (cash-basis: grouped by the expense's own ART-adjusted date, not `fixed_expense_year`/`month` — that field is for the fixed-expense view specifically): totals, category breakdown, contrasts vs. prior month/3-mo avg/6-mo avg/same month last year (each individually present or absent depending on history depth, nominal **and** real), delta attribution by category, statistical outliers (per-category mean + 2·stdev, with the month's total shown with and without them), fixed-expense status (including which are unpaid/unlinked this period), dollars (both sides always, plus what fraction of spending was covered by pesos obtained selling dollars), registration coverage (explicitly worded as "who logged it," not spending share — see the gotcha below), taxonomy health, per-concept recurrence evidence, and hard facts (first-ever expense date, months of history available) that both LLM calls use to calibrate confidence.
- **`inflation.py`** caches the IPC Nacional index (INDEC, via the `apis.datos.gob.ar` series API, series `148.3_INIVELNAL_DICI_M_26`) in `ipc_series`, used to deflate nominal contrasts to real terms. INDEC publishes a month's index around mid-the-following-month, so the most recent month is usually missing — `refresh()` estimates *only that one* month by projecting the average month-over-month ratio of the last 3 published months, and overwrites the estimate with the real value once it's published. Never estimates more than one month out. If the API is unreachable, the report degrades to nominal-only and says so in the dossier — it never blocks report generation.
- **`report_ai.py`** makes exactly two Claude calls, both against `REPORT_ANTHROPIC_MODEL` (default `claude-opus-4-8` — a stronger/pricier tier than the Haiku used elsewhere, deliberately: this runs a handful of times a year and quality matters far more than cost) using adaptive thinking (`output_config.effort: "high"`) + structured JSON outputs (`output_config.format`, `json_schema`, no tool use): (1) `classify_expenses()` — system prompt instructs the model to label each of the month's *variable* (non-fixed) expenses as `"recurring"` or `"exceptional"` with a confidence, weighing world knowledge against the dossier's empirical recurrence evidence and prior-month classifications for cross-month consistency; the user-turn payload is `{expenses: dossier["variable_expenses"], recurrence_evidence, hard_facts, prior_months_classifications}` (the last one rendered as one text line per prior period: `"YYYY-MM: \"concept\" ($amount) -> label"`, from `db.get_recent_classifications_before()`, default 6-month lookback); `max_tokens=16000`. Returns `None` (not a partial result) on any failure, which short-circuits the second call. (2) `analyze()` — system prompt instructs headline/summary/findings (each required to cite a concrete dossier figure; no recommendations section, since the app has no budgets to advise against)/questions (tagged by type — `uncategorized` / `unlinked_fixed` / `other` — so the dashboard, not the model, builds the actual link); the user-turn payload is `{dossier, partition}` where `partition` is the code-computed fixed/recurring/exceptional split; `max_tokens=8000`. Both calls send the *entire* dossier/payload as a single JSON-serialized user turn — no chat history, no prompt caching (each generation's payload differs enough that caching wouldn't hit). **Cost per generation is not currently measured** (`response.usage` isn't logged) — a rough estimate from typical payload sizes for a household with 50–150 monthly expenses is **~$0.10–0.20 total for both calls** at Opus 4.8 pricing ($5/$25 per MTok), dominated by adaptive-thinking output tokens; this is an estimate, not a measurement, and should be replaced with real numbers once `llm_calls` instrumentation lands in Phase 2 of the multi-tenant plan.
> **4.0.0 telemetry update:** the earlier cost paragraph predates Phase 2. Actual usage metadata, estimated cost, latency and success/error are now persisted in tenant-scoped `llm_calls` for both Resúmenes calls and every other Anthropic/OpenAI path.

- **`report.py`** orchestrates dossier → classify → analyze → persist, and computes the fingerprint (SHA256 of the period's *local* facts only — its expenses' id/amount/category/subcategory/user/date/fixed-link and dollar operations — deliberately excluding derived values like averages, so re-fingerprinting an unchanged period always reproduces the same hash even months later). The fingerprint isn't consumed yet — it's computed now so a drift badge landing in a follow-up PR has a baseline for every report generated from 2.3.0 on.
- **Persistence is append-only.** `reports` never gets an UPDATE — every generation or regeneration is a new row; the latest (by `generated_at`) is what's displayed, but the full history stays queryable. `expense_classifications` rows are tied to the `reports.id` that produced them, for audit and for building the cross-month-consistency context on the next classification call.
- **Degrades gracefully.** If either LLM call fails (bad key, network, malformed output despite the schema), the report is still persisted with `llm_ok=0` — the dossier's fixed sections (all the numbers) render regardless; only the narrative layer is missing, and the page says so explicitly rather than showing a blank state.
- **`/resumenes`** (dashboard.py) shows the most recent report with a month selector; `/resumenes/YYYY-MM` is the deep link. A period with no report shows a Generate button (synchronous — the two LLM calls take roughly 40-60s combined; `dashboard.py`'s Flask app runs with `threaded=True` specifically so this doesn't block other dashboard requests). Regenerate is deliberately understated (small text button, not primary) so it doesn't invite re-rolling until the report says something more pleasant.

## Screens

**Telegram** is the primary input surface — no separate "screens," just chat plus inline keyboards (category picker, edit/confirm buttons, fixed-expense payment buttons, OCR/voice confirmation, NL edit candidate picker).

**Web application** (Flask, public auth/legal pages plus 9 private product pages):

| Route | Template | Purpose |
|---|---|---|
| `/` | `landing.html` | Public product landing: first-expense workflow, family sharing and privacy boundary; authenticated users continue to the dashboard |
| `/login`, `/registro` | `login.html`, `register.html` | Google OAuth (always shows the account selector) or email OTP; registration creates the user, family and default taxonomy |
| `/privacy`, `/terms` | `privacy.html`, `terms.html` | Public legal pages required for OAuth publication (`/privacidad` and `/terminos` remain aliases) |
| `/unirme/<token>` | `join_family.html` | Public invitation landing; Google/email acceptance into the inviter's family |
| `/dashboard` | `index.html` | Dashboard: self-dismissing onboarding checklist for new families, designed empty states, month total (+ vs. prior month), Gastos/Promedio diario/Top del mes strip, charts and per-member filter |
| `/history` | `history.html` | Full expense history, filterable (concept search, month/year — each with an "all" option, category incl. uncategorized, subcategory scoped to the chosen category, fixed/variable status, user), active filters shown as removable chips, filter state reflected in the URL, inline edit (date, concept, amount, category, subcategory, fixed-expense link), delete. **Nav label is "Movimientos"** (renamed from "Historial" in 2.5.1 to avoid confusion with "Fijos") — route and template name are unchanged, only the visible label moved |
| `/ingresos` | `incomes.html` | Tenant-scoped income history and CRUD in ARS/USD, with a separate fully administrable income taxonomy; members may mutate only their own rows |
| `/settings` | `settings.html` | Categories: create/edit/delete (name, icon, color); subcategories CRUD; keywords CRUD (add/edit/delete, category + optional subcategory) |
| `/fijos` | `fijos.html` | Fixed expenses: CRUD (name, amount, category), any month's paid/pending status with progress bar, register-payment modal (amount + date, date constrained to the period being viewed), "ya lo pagué" candidate search to link an already-logged expense instead. As of 2.3.0, accepts `?year=&month=` to open a specific period directly (used by the monthly report's "unlinked fixed expense" question links) |
| `/dolares` | `dolares.html` | USD/ARS operations: history, monthly summary, historical-rate chart, delete/edit an operation |
| `/resumenes` | `resumenes.html` | Monthly AI-generated report (2.3.0) — see dedicated section above. Most recent report with a month selector; `/resumenes/YYYY-MM` deep link; Generate button when a period has none; understated Regenerate always available |
| `/config` | `config.html` | System: backup status + "Backup ahora"; restore is SSH-only |
| `/familia` | `family.html` | Members, invitations, family rename, logical removal, ownership transfer, leave/delete actions |
| `/vincular-telegram` | `telegram_link.html` | Telegram deep link, desktop QR and live connected status |

All screens share the amber/orange design system (Plus Jakarta Sans, borderless cards with large radii, CSS-variable-driven Chart.js colors synced light/dark).

## Telegram Commands

| Command | Does |
|---|---|
| `Concepto Monto` (no slash) | Fast-path expense log, e.g. `Supermercado 15000` |
| `/gastos` | Month summary: total + breakdown by category |
| `/semana` | This week's expenses (Sun–Sat, ART) |
| `/hoy` | Today's expenses (ART) |
| `/sincat` | Expenses with no category assigned |
| `/fijos` | This month's fixed-expense status, with pay buttons |
| `/editar ID monto VALOR` | Edit an expense's amount |
| `/editar ID categoria NOMBRE` | Edit an expense's category |
| `/editar ID fecha DD/MM/AAAA` | Edit an expense's date |
| `/recat CONCEPTO CATEGORÍA` | Bulk-reassign expenses matching a concept to a category |
| `/borrar ID` | Delete an expense |
| `/add_keyword PALABRA CATEGORÍA` | Add a keyword → category mapping |
| `/categorias` | List categories |
| `/nueva_categoria Nombre Emoji Color` | Create a category (emoji/color optional) |
| `/ayuda` | Full command + usage reference |
| `CambioDolar <usd> <cotizacion>` | Legacy explicit dollar-sale command |
| photo/document image | OCR ticket scan (`ocr.py`) → confirm before saving |
| voice note | Whisper transcription + Claude extraction (`audio.py`) → auto-save if confidence ≥ 0.9, else confirm |
| free-form text (anything not matched above) | Routed to the NL intent layer (`intent.py`) if it looks conversational — see **Natural-language intent layer** above |

## Module Responsibilities

- `main.py` — entrypoint; loads env config, initializes DB, schedules backup, starts Flask thread, starts bot polling
- `bot.py` — Telegram handlers; holds per-`chat_id` pending-state dicts (`pending_ocr`, `pending_amount_edit`, `pending_dolar`, `pending_nl_confirm`, `pending_nl_pick`, `pending_fixed_direct`, …). Hybrid routing in `handle_message`: fast path for plain `concept amount`, else the intent layer. `_maybe_offer_fixed_link()` is the single seam every expense-creation path calls after saving to offer a fixed-expense link
- `intent.py` — natural-language intent layer via Claude tool use; returns a structured result dict (`log`/`edit`/`category`/`subcategory`/`report`/`reply`). Injects taxonomy + the user's recent expenses + ART date; performs no Telegram I/O. `edit_expense`'s `changes` can include `fixed_expense` (link by name, or `"ninguno"` to unlink)
- `fixed_matcher.py` — matching heuristics shared by `bot.py` and `dashboard.py` so both surfaces agree on what counts as a match: `find_fixed_expense_matches` (new expense → fixed-expense definition, word-overlap) and `find_candidate_expenses` (fixed expense → already-logged unlinked expenses for a period, scored by word overlap + category + amount proximity). Also `expense_period()`, converting an expense's own UTC timestamp to a (year, month) in a given tz
- `sqlro.py` — PostgreSQL read-only executor: `SELECT`/`WITH` only, one statement, `gastos_readonly` role, tenant RLS, timeout and row cap
- `parser.py` — parses free-text into `{concept, amount}`; returns `None` if no valid amount
- `categorizer.py` — keyword matching (accent/case-insensitive); returns `(category_id, subcategory_id)`, both nullable. `normalize()` is reused for taxonomy dup-guarding and for the history screen's concept search
- `db.py` — raw PostgreSQL operations through psycopg pools. `get_conn()` applies the RLS-bound role and transaction-local tenant before each domain transaction; platform identity resolution uses the dedicated bypass role.
- `dashboard.py` — Flask app; UTC → Buenos Aires conversion for all display
- `auth.py` — opaque server-side sessions, hashed OTPs, Resend, Google identity linking, Turnstile/rate limits, invitation lifecycle, active/historical memberships, ownership transfer, and account/family creation. Platform access uses transaction-local `gastos_superadmin`.
- `llm_limits.py` — per-family admission control for all Anthropic/OpenAI calls: 100 routine calls/day, 15 report generations/month and two concurrent calls; calendar limits use `families.timezone`.
- `ocr.py` — Anthropic SDK call; returns `{comercio, monto, fecha}`
- `audio.py` — Whisper transcription + Claude extraction; returns `[{concept, amount, confidence}]`
- `dolar.py` — natural-language USD buy/sell parsing (`looks_like_dolar` gate + `parse_dolar`); confidence-based auto-save
- `backup.py` — custom-format `pg_dump` to private R2 with remote size verification and 90-day lifecycle
- `seed.py` — `create_family_defaults(conn, family_id)` creates generic taxonomy for a new family; schema changes are Alembic-only
- `dossier.py` — deterministic aggregation for the monthly report (2.3.0); no LLM involved. `build_dossier(year, month)` reads via `db.py` (ART-adjusted cash-basis queries) plus `inflation.py`, returns the full structured snapshot — totals, contrasts, delta attribution, outliers, fixed-expense status, dollars, registration coverage, taxonomy, recurrence evidence, hard facts
- `inflation.py` — IPC Nacional fetch/cache/estimate/deflate (2.3.0); `refresh()` hits `apis.datos.gob.ar`, `deflate()` converts a nominal amount between two periods' prices, returning `None` (not a silent nominal fallback) when an index is missing
- `report_ai.py` — the two Claude calls behind the monthly report (2.3.0): `classify_expenses()` (recurring/exceptional per variable expense) and `analyze()` (narration). Structured JSON outputs, adaptive thinking, `REPORT_ANTHROPIC_MODEL` (default `claude-opus-4-8`) — separate model config from the Haiku extraction calls elsewhere. No DB/Telegram/Flask I/O
- `report.py` — orchestrates dossier → classify → analyze → persist for the monthly report (2.3.0); computes the append-only-friendly `fingerprint()` (period-local facts only, no derived values). Degrades to a dossier-only report (`llm_ok=0`) if either LLM call fails rather than losing the generation entirely

## Config

Environment variables only — no HA Supervisor dependency. On the Pi, loaded from `~/.env` via `env_file` in Compose.

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | Yes | Bot token |
| `TELEGRAM_BOT_USERNAME` | Yes | Bot username without `@`, used for deep links |
| `PUBLIC_DASHBOARD_URL` | No | Base URL sent to unlinked chats; defaults to `https://mangoteca.juampifinochietto.com` |
| `USERS_JSON` | Yes | `[{"telegram_id": "...", "name": "...", "email": "optional@example.com"}]`; optional email is a NULL-only legacy identity link |
| `AUTH_SECRET_KEY` | Yes | Random secret for OAuth/pre-auth state |
| `AUTH_BOOTSTRAP_EMAIL` | Yes | Email attached once to the existing family-1 owner |
| `SUPERADMIN_EMAIL` | Yes | Sole superadmin email, applied only at startup |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Yes | Google OAuth web client |
| `RESEND_API_KEY` | Yes | Sends email OTPs |
| `RESEND_FROM_EMAIL` | No | Verified transactional sender |
| `TURNSTILE_SECRET` | Yes | Private Turnstile siteverify secret; the site key is public and embedded |
| `ANTHROPIC_API_KEY` | No | Enables OCR, voice/dollar extraction, the natural-language intent layer, and the monthly report (2.3.0) |
| `REPORT_ANTHROPIC_MODEL` | No | Default: `claude-opus-4-8`. Model used for the monthly report's two LLM calls (2.3.0) — separate from the Haiku model used elsewhere, since this runs a handful of times a year and quality matters more than cost |
| `OPENAI_API_KEY` | No | Enables voice message expense entry |
| `DASHBOARD_PORT` | No | Default: `5000`. The Pi sets `8090` to free up 5000 for Frigate |

## Known Gotchas

- **Dashboard port:** the code's own default is 5000, but the Pi runs it on 8090 (`DASHBOARD_PORT` in `~/.env`) to leave 5000 free for Frigate. Don't assume 5000 is what's live on the Pi.
- **Argentine number format:** `.` is thousands, `,` is decimal. Test edge cases when touching `parser.py`.
- **`cloudflared` targets:** use service names, never `localhost` — it runs `network_mode: host` but Cloudflare's tunnel config references Docker service DNS.
- **Flask + bot in one process:** threading is a deliberate tradeoff. Don't split into separate services without explicit discussion.
- **Subcategory inference is silent:** no extra Telegram prompts after category assignment.
- **DNS staleness on long-running containers:** `resolv.conf` can go stale if the host network changes. Mitigated by `dns: [8.8.8.8, 1.1.1.1]` and healthcheck in `docker-compose.yml`. Symptom: `[Errno -3] Try again` in bot logs while container shows `Up`.
- **Whisper returns written numbers:** Whisper transcribes verbatim — "diez mil" stays as text. Claude (`audio.py`) normalizes them to digits before saving. If you bypass `audio.py` and use Whisper output directly, amounts will be `null`.
- **Always `git pull` locally before starting a Claude Code session** — CC builds from local disk, not from GitHub.
- **Dockerfile build context is the repo root** (not `gastos/`): `docker build -f gastos/Dockerfile .`
- **2.0.0 fixed-expense migration is lossy by design:** old `fixed_expense_payments` rows with no linked expense (from the old "✓ Ya lo pagué" flag-only flow) had no amount to migrate and were dropped rather than fabricated from `estimated_amount`. Check the startup logs after upgrading a DB that predates 2.0.0 for the converted/dropped counts, and re-link any dropped months by hand via the new "ya lo pagué" candidate search.
- **Date-only writes are always stored at `03:00:00` UTC** (`create_expense_full`, `update_expense`, `update_expense_fields`, the fixed-expense "pay" flow) — that's exactly midnight ART, chosen so the stored UTC date and the ART-displayed date are always the same calendar day, at every month/year boundary, regardless of whether a query adjusts for the `-3h` offset or reads `created_at` raw (the codebase does both, inconsistently, elsewhere). Don't "improve" this by storing a different time-of-day without re-verifying that invariant.
- **The IPC time-series API is unauthenticated and has no SLA.** `inflation.refresh()` catches every failure and leaves the cache as-is — report generation never blocks on it, and the dossier explicitly flags `inflation_unavailable` so the model doesn't narrate un-deflated numbers as if they were real. If real IPC data looks stale on the dashboard, check `apis.datos.gob.ar` directly before assuming a code bug.
- **`fixed_expense_year`/`month` vs. cash-basis reporting are two different partitions, on purpose.** The monthly report groups by the expense's own ART date (`dossier.py`, `db.get_expenses_for_period_art`), not by the fixed-expense period fields — otherwise fixed + variable wouldn't sum to the report's own total. Don't switch the report's queries to the `fixed_expense_year`/`month` columns to "simplify" — that's a different, deliberately separate concept (see the 2.1.0 changelog entry on why the two are independent).

## Infrastructure Philosophy

The `docker-compose.yml` on the Pi (`/home/juanfino/docker-compose.yml`) is the **operational source of truth** — it is managed manually and may include services from multiple unrelated projects. The copy committed to this repo exists for **auditing and history only** and is not read directly by the Pi.

When CC modifies `docker-compose.yml` as part of a PR, the relevant changes must be manually applied to the Pi's copy. The repo copy should then be updated to match.

The Pi is intended to host multiple independent projects. A single global compose file on the host is preferred over per-project compose files to keep service management centralized.

## Active Backlog

- Clean up / review `/api/weekly` endpoint
- Consolidate sparkline queries
- Automate Pi deployments via Tailscale (when warranted)

## Workflow & Conventions

- **Division of labor:** Claude (architecture/design/prompts) → Claude Code (implementation) → Juampi (git, deploy, reporting)
- `config.yaml` is the canonical version source; `CHANGELOG.md` is the canonical change record — both must be updated at the end of every deployable session
- Version bumps: patch (`1.x.x`) for bugfixes, minor (`x.x.0`) for features, major for breaking config/DB schema changes
- Multiple related changes consolidated into single CC prompts — avoid noisy PR chains
- Merging PRs is a manual step by design
- Conversations with Claude in Spanish; code and CC prompts in English
- `categorizer.categorize()` always returns `(category_id, subcategory_id)` — all expense creation flows must pass both
