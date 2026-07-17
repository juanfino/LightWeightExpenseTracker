# LightWeightExpenseTracker

Family expense tracker. Users send plain-text messages to a Telegram bot; the app parses, categorizes, and persists expenses to SQLite. A Flask dashboard provides monthly/annual visualizations, history, and configuration.

- **Version:** 2.0.0 (canonical source: `gastos/config.yaml`)
- **Dashboard:** https://expenses.juampifinochietto.com
- **Repo:** https://github.com/juanfino/LightWeightExpenseTracker

## Architecture

**Infrastructure:** Raspberry Pi 4 (SSD), Raspberry Pi OS Lite, Docker Compose. User `juanfino`, hostname `rbp-casaribera`, IP `192.168.68.72`. Data at `~/gastos-data/gastos.db`.

**Services** (all `network_mode: host`):
- `gastos` — Flask dashboard + Telegram bot (same process, separate thread)
- `cloudflared` — Cloudflare Tunnel, exposes `localhost:8090` as `expenses.juampifinochietto.com` (dashboard's code default is port 5000; the Pi's `~/.env` sets `DASHBOARD_PORT=8090` to free up 5000 for Frigate)
- `homeassistant` — unrelated, colocated

**Access:** Cloudflare Tunnel + Cloudflare Access (Google SSO). `cloudflared` targets use container/service names — never `localhost`.

**Process model:** Flask runs in a daemon thread; `python-telegram-bot` long polling blocks the main thread. Known tradeoff, accepted.

**Database:** SQLite at `/data/gastos.db`, mounted from `~/gastos-data`. 7 tables: `users`, `categories`, `subcategories`, `keywords`, `expenses`, `fixed_expenses`, `cambios_dolar`. A fixed-expense payment is a property of the expense itself — `expenses.fixed_expense_id` (+ `fixed_expense_year`/`fixed_expense_month`) — not a separate joined entity; any number of expenses may share the same fixed expense + period (e.g. a legitimate double payment). As of 2.0.0 (previously a separate `fixed_expense_payments` join table). All timestamps stored as UTC; dashboard converts to `America/Argentina/Buenos_Aires` (UTC-3).

**Backup:** Daily at 21:00 ART via APScheduler — sends `gastos.db` as Telegram document to all configured users. Also triggerable via `POST /admin/backup-now`.

## Stack

- Python, Flask, SQLite
- `python-telegram-bot` v20 (async, long polling)
- Anthropic API (`claude-haiku-4-5-20251001`) for OCR receipt scanning, voice/dollar extraction, and the natural-language intent layer (tool use / function calling)
- Docker (Alpine base), multi-arch (`linux/arm64`, `linux/amd64`)
- GitHub Actions → `ghcr.io/juanfino/lightweightexpensetracker` (public registry, auto-build on merge to `main`)
- Deploys are **manual**: `docker compose pull gastos && docker compose up -d gastos` on the Pi

## Key Features

- **Expense entry via Telegram:** plain text, e.g. `Supermercado 150000`
- **Natural-language intent layer:** conversational messages that aren't the plain `concept amount` form route through a Claude tool-use layer (`intent.py`) covering four intent families — logging (`"anotame 100 lucas en el súper"`), editing (`"el último gasto fueron 90000"`, `"el gasto 124: total 40000 y categoría nafta"`), taxonomy management (`"agregá la categoría Niños"`), and read-only reports (`"cuánto gastó Cele en comida en marzo"`). Hybrid routing keeps the deterministic parser as the instant fast path; a keyword heuristic (`_needs_intent`) escalates the rest. **Tools:** `log_expense`, `edit_expense`, `create_category`, `create_subcategory`, `run_report`. Mutations are structured/parameterized and confirmed with inline buttons (logging auto-saves with an edit keyboard); reads are model-generated SQL run under strict guardrails (`sqlro.py`): `SELECT`/`WITH` only, single statement, read-only connection (`mode=ro`), statement timeout. Via Telegram a user may only edit their own expenses (targeting SQL filters by user + ownership re-check before the parameterized UPDATE); the web dashboard is unchanged. Short-lived conversational memory: a per-chat sliding window (5 min / last 10 messages, whichever hits first, in-process only) lets follow-ups like "dame el desglose por persona" resolve against the prior turn; outside that window the model is instructed to ask for the full question rather than guess.
- **Auto-categorization:** keyword matching (two-level: category + subcategory). Silent inference — no extra prompts.
- **OCR receipt scanning:** send a photo; bot extracts `{comercio, monto, fecha}` via Anthropic Vision, prompts for confirmation before saving
- **Voice expense entry:** send a voice note (e.g. "ferretería diez mil pesos"); bot transcribes with OpenAI Whisper, normalizes written numbers to digits via Claude, and prompts for confirmation before saving
- **Argentine number formatting:** `.` = thousands separator, `,` = decimal (e.g. `$5.580,00`). `_parse_monto()` handles both notations; `100.000` → 100000, `2.500,50` → 2500.5
- **Gastos Fijos:** recurring fixed expense tracking; `/fijos` shows the month's payment status with inline buttons to register a payment or search for one already logged. Detection runs downstream of expense creation on **every** input path (plain text, NL, voice, OCR, dashboard manual add) — not just the plain-text fast path — via a shared `fixed_matcher.py` (word-overlap ≥3 chars) so the fixed/variable split doesn't depend on how the user happened to log the expense. Linking always forces the expense's category/subcategory to the fixed expense's own, so a recurring bill can't drift category month to month. "✓ Ya lo pagué" searches already-logged, unlinked expenses for the period (concept overlap + category + amount proximity) and offers to link one instead of just flagging "paid" with no amount; explicitly declining still lets the user log the amount directly. The link (+ period) is an ordinary, editable field on the expense, alongside category/subcategory
- **USD/ARS exchange rate tracking:** natural-language ("vendí 500 dólares a 1700", "compré 1000 dólares a 1550") via `dolar.py`, gated by a cheap keyword check (`looks_like_dolar`) before spending an LLM call; confidence-based auto-save, same as voice. Legacy `CambioDolar <usd> <cotizacion>` command still works (always records a sale). Dedicated dashboard page (`/dolares`) with history, monthly summary, and historical rate chart
- **Flask dashboard:** mobile-friendly, per-member filter, Chart.js visualizations (monthly, annual, weekly, by category, last-6-months trend), sortable/filterable history with inline edit, full category/subcategory/keyword CRUD, fixed-expense CRUD, DB backup/restore panel. Visual identity redesigned to amber/orange (from violet) across all 6 screens as of 1.15.0–1.17.0 — see **Screens** below
- **Users:** Juampi (active), Cele (configured, not yet onboarded)

## Screens

**Telegram** is the primary input surface — no separate "screens," just chat plus inline keyboards (category picker, edit/confirm buttons, fixed-expense payment buttons, OCR/voice confirmation, NL edit candidate picker).

**Web dashboard** (Flask, 6 pages, `templates/*.html`, shared `base.html` shell with mobile nav):

| Route | Template | Purpose |
|---|---|---|
| `/` | `index.html` | Dashboard: month total (+ vs. prior month), Gastos/Promedio diario/Top del mes strip, "Top 3 del mes" list, charts (by category, by week w/ prior-month overlay, last 6 months, annual), per-member filter |
| `/history` | `history.html` | Full expense history, filterable (month/year/category/user), inline edit (concept, amount, category, subcategory, fixed-expense link), delete |
| `/settings` | `settings.html` | Categories: create/edit/delete (name, icon, color); subcategories CRUD; keywords CRUD (add/edit/delete, category + optional subcategory) |
| `/fijos` | `fijos.html` | Fixed expenses: CRUD (name, amount, category), current month's paid/pending status with progress bar, register-payment modal, "ya lo pagué" candidate search to link an already-logged expense instead |
| `/dolares` | `dolares.html` | USD/ARS operations: history, monthly summary, historical-rate chart, delete/edit an operation |
| `/config` | `config.html` | System: backup status + "Backup ahora" button, restore DB from a public HTTPS URL (saves a `.bak` of current state first, then restarts) |

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
- `sqlro.py` — read-only SQL executor (guardrails): `SELECT`/`WITH` only, single statement, `mode=ro` connection, statement timeout, row cap. Used by reports and edit-targeting
- `parser.py` — parses free-text into `{concept, amount}`; returns `None` if no valid amount
- `categorizer.py` — keyword matching (accent/case-insensitive); returns `(category_id, subcategory_id)`, both nullable. `normalize()` is reused for taxonomy dup-guarding
- `db.py` — all SQLite ops; `get_conn()` context manager auto-commits/rollbacks; `DB_PATH` set by `main.py`. `update_expense_fields()` is the parameterized, user-scoped UPDATE used by bot edits. `link_expense_to_fixed()`/`unlink_expense_from_fixed()` are the single choke point for attaching/detaching a fixed-expense link (forces category/subcategory from the fixed expense on link)
- `dashboard.py` — Flask app; UTC → Buenos Aires conversion for all display
- `ocr.py` — Anthropic SDK call; returns `{comercio, monto, fecha}`
- `audio.py` — Whisper transcription + Claude extraction; returns `[{concept, amount, confidence}]`
- `dolar.py` — natural-language USD buy/sell parsing (`looks_like_dolar` gate + `parse_dolar`); confidence-based auto-save
- `backup.py` — sends DB file as Telegram document; called by scheduler and admin endpoint
- `seed.py` — idempotent `seed(conn)` run on every startup; handles schema migrations and default data

## Config

Environment variables only — no HA Supervisor dependency. On the Pi, loaded from `~/.env` via `env_file` in Compose.

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | Yes | Bot token |
| `USERS_JSON` | Yes | `[{"telegram_id": "...", "name": "..."}]` |
| `ANTHROPIC_API_KEY` | No | Enables OCR, voice/dollar extraction, and the natural-language intent layer |
| `OPENAI_API_KEY` | No | Enables voice message expense entry |
| `DB_PATH` | No | Default: `/data/gastos.db` |
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

## Infrastructure Philosophy

The `docker-compose.yml` on the Pi (`/home/juanfino/docker-compose.yml`) is the **operational source of truth** — it is managed manually and may include services from multiple unrelated projects. The copy committed to this repo exists for **auditing and history only** and is not read directly by the Pi.

When CC modifies `docker-compose.yml` as part of a PR, the relevant changes must be manually applied to the Pi's copy. The repo copy should then be updated to match.

The Pi is intended to host multiple independent projects. A single global compose file on the host is preferred over per-project compose files to keep service management centralized.

## Active Backlog

- Clean up / review `/api/weekly` endpoint
- Consolidate sparkline queries
- Automate Pi deployments via Tailscale (when warranted)
- Onboard Cele once app is sufficiently polished

## Workflow & Conventions

- **Division of labor:** Claude (architecture/design/prompts) → Claude Code (implementation) → Juampi (git, deploy, reporting)
- `config.yaml` is the canonical version source; `CHANGELOG.md` is the canonical change record — both must be updated at the end of every deployable session
- Version bumps: patch (`1.x.x`) for bugfixes, minor (`x.x.0`) for features, major for breaking config/DB schema changes
- Multiple related changes consolidated into single CC prompts — avoid noisy PR chains
- Merging PRs is a manual step by design
- Conversations with Claude in Spanish; code and CC prompts in English
- `categorizer.categorize()` always returns `(category_id, subcategory_id)` — all expense creation flows must pass both
