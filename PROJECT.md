# LightWeightExpenseTracker

Family expense tracker. Users send plain-text messages to a Telegram bot; the app parses, categorizes, and persists expenses to SQLite. A Flask dashboard provides monthly/annual visualizations, history, and configuration.

- **Version:** 1.8.1 (canonical source: `gastos/config.yaml`)
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
- Anthropic Vision API (`claude-haiku-4-5-20251001`) for OCR receipt scanning
- Docker (Alpine base), multi-arch (`linux/arm64`, `linux/amd64`)
- GitHub Actions → `ghcr.io/juanfino/lightweightexpensetracker` (public registry, auto-build on merge to `main`)
- Deploys are **manual**: `docker compose pull gastos && docker compose up -d gastos` on the Pi

## Key Features

- **Expense entry via Telegram:** plain text, e.g. `Supermercado 150000`
- **Auto-categorization:** keyword matching (two-level: category + subcategory). Silent inference — no extra prompts.
- **OCR receipt scanning:** send a photo; bot extracts `{comercio, monto, fecha}` via Anthropic Vision, prompts for confirmation before saving
- **Argentine number formatting:** `.` = thousands separator, `,` = decimal (e.g. `$5.580,00`). `_parse_monto()` handles both notations; `100.000` → 100000, `2.500,50` → 2500.5
- **Gastos Fijos:** recurring fixed expense tracking with monthly payment status and inline bot flow
- **USD/ARS exchange rate tracking:** `CambioDolar <monto_usd> <cotizacion>` command; dedicated dashboard page
- **Flask dashboard:** mobile-friendly, per-member filter, Chart.js visualizations (monthly, annual, weekly, by category), sortable history, inline edit
- **Users:** Juampi (active), Cele (configured, not yet onboarded)

## Module Responsibilities

- `main.py` — entrypoint; loads env config, initializes DB, schedules backup, starts Flask thread, starts bot polling
- `bot.py` — Telegram handlers; holds `pending_ocr` and `pending_amount_edit` dicts (keyed by `chat_id`)
- `parser.py` — parses free-text into `{concept, amount}`; returns `None` if no valid amount
- `categorizer.py` — keyword matching (accent/case-insensitive); returns `(category_id, subcategory_id)`, both nullable
- `db.py` — all SQLite ops; `get_conn()` context manager auto-commits/rollbacks; `DB_PATH` set by `main.py`
- `dashboard.py` — Flask app; UTC → Buenos Aires conversion for all display
- `ocr.py` — Anthropic SDK call; returns `{comercio, monto, fecha}`
- `backup.py` — sends DB file as Telegram document; called by scheduler and admin endpoint
- `seed.py` — idempotent `seed(conn)` run on every startup; handles schema migrations and default data

## Config

Environment variables only — no HA Supervisor dependency. On the Pi, loaded from `~/.env` via `env_file` in Compose.

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | Yes | Bot token |
| `USERS_JSON` | Yes | `[{"telegram_id": "...", "name": "..."}]` |
| `ANTHROPIC_API_KEY` | No | Enables OCR |
| `DB_PATH` | No | Default: `/data/gastos.db` |

## Known Gotchas

- **Argentine number format:** `.` is thousands, `,` is decimal. Test edge cases when touching `parser.py`.
- **`cloudflared` targets:** use service names, never `localhost` — it runs `network_mode: host` but Cloudflare's tunnel config references Docker service DNS.
- **Flask + bot in one process:** threading is a deliberate tradeoff. Don't split into separate services without explicit discussion.
- **Subcategory inference is silent:** no extra Telegram prompts after category assignment.
- **DNS staleness on long-running containers:** `resolv.conf` can go stale if the host network changes. Mitigated by `dns: [8.8.8.8, 1.1.1.1]` and healthcheck in `docker-compose.yml`. Symptom: `[Errno -3] Try again` in bot logs while container shows `Up`.
- **Always `git pull` locally before starting a Claude Code session** — CC builds from local disk, not from GitHub.
- **Dockerfile build context is the repo root** (not `gastos/`): `docker build -f gastos/Dockerfile .`

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
