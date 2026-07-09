# LightWeightExpenseTracker

Family expense tracker. Users send plain-text messages to a Telegram bot; the app parses, categorizes, and persists expenses to SQLite. A Flask dashboard provides monthly/annual visualizations, history, and configuration.

- **Version:** 1.13.0 (canonical source: `gastos/config.yaml`)
- **Dashboard:** https://expenses.juampifinochietto.com
- **Repo:** https://github.com/juanfino/LightWeightExpenseTracker

## Architecture

**Infrastructure:** Raspberry Pi 4 (SSD), Raspberry Pi OS Lite, Docker Compose. User `juanfino`, hostname `rbp-casaribera`, IP `192.168.68.72`. Data at `~/gastos-data/gastos.db`.

**Services** (all `network_mode: host`):
- `gastos` — Flask dashboard + Telegram bot (same process, separate thread)
- `cloudflared` — Cloudflare Tunnel, exposes `localhost:5000` as `expenses.juampifinochietto.com`
- `homeassistant` — unrelated, colocated

**Access:** Cloudflare Tunnel + Cloudflare Access (Google SSO). `cloudflared` targets use container/service names — never `localhost`.

**Process model:** Flask runs in a daemon thread; `python-telegram-bot` long polling blocks the main thread. Known tradeoff, accepted.

**Database:** SQLite at `/data/gastos.db`, mounted from `~/gastos-data`. 6 tables: `users`, `categories`, `subcategories`, `keywords`, `expenses`, `fixed_expenses`, `fixed_expense_payments`, `cambios_dolar`. All timestamps stored as UTC; dashboard converts to `America/Argentina/Buenos_Aires` (UTC-3).

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
- **Natural-language intent layer:** conversational messages that aren't the plain `concept amount` form route through a Claude tool-use layer (`intent.py`) covering four intent families — logging (`"anotame 100 lucas en el súper"`), editing (`"el último gasto fueron 90000"`, `"el gasto 124: total 40000 y categoría nafta"`), taxonomy management (`"agregá la categoría Niños"`), and read-only reports (`"cuánto gastó Cele en comida en marzo"`). Hybrid routing keeps the deterministic parser as the instant fast path; a keyword heuristic (`_needs_intent`) escalates the rest. **Tools:** `log_expense`, `edit_expense`, `create_category`, `create_subcategory`, `run_report`. Mutations are structured/parameterized and confirmed with inline buttons (logging auto-saves with an edit keyboard); reads are model-generated SQL run under strict guardrails (`sqlro.py`): `SELECT`/`WITH` only, single statement, read-only connection (`mode=ro`), statement timeout. Via Telegram a user may only edit their own expenses (targeting SQL filters by user + ownership re-check before the parameterized UPDATE); the web dashboard is unchanged.
- **Auto-categorization:** keyword matching (two-level: category + subcategory). Silent inference — no extra prompts.
- **OCR receipt scanning:** send a photo; bot extracts `{comercio, monto, fecha}` via Anthropic Vision, prompts for confirmation before saving
- **Voice expense entry:** send a voice note (e.g. "ferretería diez mil pesos"); bot transcribes with OpenAI Whisper, normalizes written numbers to digits via Claude, and prompts for confirmation before saving
- **Argentine number formatting:** `.` = thousands separator, `,` = decimal (e.g. `$5.580,00`). `_parse_monto()` handles both notations; `100.000` → 100000, `2.500,50` → 2500.5
- **Gastos Fijos:** recurring fixed expense tracking with monthly payment status and inline bot flow
- **USD/ARS exchange rate tracking:** `CambioDolar <monto_usd> <cotizacion>` command; dedicated dashboard page
- **Flask dashboard:** mobile-friendly, per-member filter, Chart.js visualizations (monthly, annual, weekly, by category), sortable history, inline edit
- **Users:** Juampi (active), Cele (configured, not yet onboarded)

## Module Responsibilities

- `main.py` — entrypoint; loads env config, initializes DB, schedules backup, starts Flask thread, starts bot polling
- `bot.py` — Telegram handlers; holds per-`chat_id` pending-state dicts (`pending_ocr`, `pending_amount_edit`, `pending_dolar`, `pending_nl_confirm`, `pending_nl_pick`, …). Hybrid routing in `handle_message`: fast path for plain `concept amount`, else the intent layer
- `intent.py` — natural-language intent layer via Claude tool use; returns a structured result dict (`log`/`edit`/`category`/`subcategory`/`report`/`reply`). Injects taxonomy + the user's recent expenses + ART date; performs no Telegram I/O
- `sqlro.py` — read-only SQL executor (guardrails): `SELECT`/`WITH` only, single statement, `mode=ro` connection, statement timeout, row cap. Used by reports and edit-targeting
- `parser.py` — parses free-text into `{concept, amount}`; returns `None` if no valid amount
- `categorizer.py` — keyword matching (accent/case-insensitive); returns `(category_id, subcategory_id)`, both nullable. `normalize()` is reused for taxonomy dup-guarding
- `db.py` — all SQLite ops; `get_conn()` context manager auto-commits/rollbacks; `DB_PATH` set by `main.py`. `update_expense_fields()` is the parameterized, user-scoped UPDATE used by bot edits
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

## Known Gotchas

- **Argentine number format:** `.` is thousands, `,` is decimal. Test edge cases when touching `parser.py`.
- **`cloudflared` targets:** use service names, never `localhost` — it runs `network_mode: host` but Cloudflare's tunnel config references Docker service DNS.
- **Flask + bot in one process:** threading is a deliberate tradeoff. Don't split into separate services without explicit discussion.
- **Subcategory inference is silent:** no extra Telegram prompts after category assignment.
- **DNS staleness on long-running containers:** `resolv.conf` can go stale if the host network changes. Mitigated by `dns: [8.8.8.8, 1.1.1.1]` and healthcheck in `docker-compose.yml`. Symptom: `[Errno -3] Try again` in bot logs while container shows `Up`.
- **Whisper returns written numbers:** Whisper transcribes verbatim — "diez mil" stays as text. Claude (`audio.py`) normalizes them to digits before saving. If you bypass `audio.py` and use Whisper output directly, amounts will be `null`.
- **Always `git pull` locally before starting a Claude Code session** — CC builds from local disk, not from GitHub.
- **Dockerfile build context is the repo root** (not `gastos/`): `docker build -f gastos/Dockerfile .`

## Infrastructure Philosophy

The `docker-compose.yml` on the Pi (`/home/juanfino/docker-compose.yml`) is the **operational source of truth** — it is managed manually and may include services from multiple unrelated projects. The copy committed to this repo exists for **auditing and history only** and is not read directly by the Pi.

When CC modifies `docker-compose.yml` as part of a PR, the relevant changes must be manually applied to the Pi's copy. The repo copy should then be updated to match.

The Pi is intended to host multiple independent projects. A single global compose file on the host is preferred over per-project compose files to keep service management centralized.

## Active Backlog

- Pin `anthropic` version in `requirements.txt`
- Dynamic user color assignment (currently hardcoded for "Cele")
- Clean up / review `/api/weekly` endpoint
- Consolidate sparkline queries
- Migrate `seed.py` to use `db.get_conn()` (currently uses `sqlite3.connect()` directly)
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
