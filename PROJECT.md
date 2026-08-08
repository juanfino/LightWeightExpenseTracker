# LightWeightExpenseTracker

Family expense tracker. Users send plain-text messages to a Telegram bot; the app parses, categorizes, and persists ARS/USD expenses to PostgreSQL. A Flask dashboard provides monthly/annual visualizations, history, and configuration.

- **Version:** 7.10.0 (canonical source: `gastos/config.yaml`)
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

**Database:** PostgreSQL 17 with Alembic (current head: `0009`). Platform tables are `families`, `users`, `memberships`, `sessions`, `otp_codes`, `oauth_identities`, `invitations`, `telegram_link_tokens` and global `infrastructure_cost_settings`; removing a member keeps an inactive historical membership, while a partial unique index permits only one active family per user. `memberships.onboarding_dismissed_at` (migration `0009`) lets a member manually hide the dashboard's onboarding checklist before completing it; scoped to the membership, not the user, so a new family starts fresh. Tenant tables are `categories`, `subcategories`, `keywords`, `expenses`, `fixed_expenses`, `income_categories`, `incomes`, `shopping_items`, `cambios_dolar`, `ipc_series`, `reports`, `expense_classifications`, `llm_calls`, `family_quota_overrides` and `system_errors`. Tenant rows carry `family_id NOT NULL` with forced RLS on transaction-local `app.family_id`; composite tenant foreign keys reject cross-family references. Normal and read-only roles remain subject to RLS; `gastos_superadmin` is the dedicated `BYPASSRLS` role and is the only role used by `/superadmin` for cross-family reads. Amounts retain native ARS/USD and timestamps are UTC; families store their display timezone.

`telegram_link_tokens` stores only SHA-256 token hashes; links expire after 15 minutes and are single-use. `infrastructure_cost_settings` is installation-level metadata editable only by the superadmin role. `family_quota_overrides` and `system_errors` remain tenant-scoped and protected by forced RLS.

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
- **Flask dashboard:** mobile-friendly, per-member filter, Chart.js visualizations (monthly, annual, weekly, by category, last-6-months trend), sortable/filterable history with inline edit, full category/subcategory/keyword CRUD, fixed-expense CRUD and backup status/manual trigger. Restore is deliberately SSH-only. Visual identity uses the amber/orange design system across every screen — see **Screens** below.
- **Monthly AI-generated report** (2.3.0): `/resumenes` — see dedicated section below
- **Users:** Juampi and Cele, both onboarded and actively using the app
- **Family management:** `/familia` lets the owner generate/revoke seven-day single-use invitation links, rename the family, logically remove members, transfer ownership, or delete the family with exact-name confirmation. Invitees join as members through Google or email OTP. `SUPERADMIN_EMAIL` is startup-only; no HTTP path can change the flag.
- **Telegram linking and LLM safeguards:** `/vincular-telegram` provides a 15-minute, one-use deep link and QR with live confirmation; unknown chats get linking help, groups are rejected, and unlinking lives in `/familia`. Routine AI calls default to 100/family/day, Resúmenes to 15 generations/family/month, and `llm_limits.py` permits at most two concurrent LLM calls per family. The superadmin may override either quota per family. Unhandled bot/web errors are written to the rotated container log; tenant-attributed failures are also retained for the panel and are not sent through the family bot.
- **Superadmin operations:** `/superadmin` shows cross-family adoption/activity, expense volume, LLM calls/cost by family/module/model/day, editable operating-cost assumptions, optional family quota overrides and recent web/Telegram/LLM failures. It does not provide impersonation, billing or business-data editing.
- **Portable exit path:** `/exportar` produces tenant-scoped RFC 4180 CSVs (UTF-8 BOM, ISO dates, spreadsheet-formula neutralization) and a complete ZIP including movements, fixed expenses, dollars, incomes, shopping and taxonomy.

## Monthly AI report

A retrospective on demand, not a schedule (scheduling/Telegram delivery are a follow-up). The governing rule: the model never does arithmetic. Every number is computed by `dossier.py` from SQL aggregation; the model only narrates and makes one bounded judgment call.

**ARS and USD are two first-class, parallel currency blocks (7.7.0), not a pesos report with a USD footnote.** `dossier.py`'s `build_dossier()` returns `currencies.{ARS,USD}`, each the same shape — totals, category breakdown, contrasts, delta attribution, outliers, fixed-expense status, taxonomy, registration coverage, variable expenses, months-of-history, and (once `report.py` classifies expenses) the fixed/recurring/exceptional partition — and every one of those aggregates is currency-scoped: nothing ever sums an ARS amount with a USD one. The single exception is the top-level `equivalence` block: a reference-only ARS-equivalent of the month's USD spending at the family's own dollar rate (this month's own sale rate → this month's own purchase rate → the most recent rate recorded within 12 months → unavailable), used only to convey magnitude in the hero card and, at most once, in the narrative — never as a total, never fed into a contrast or the dollar-coverage denominator's own currency split. IPC deflation (`contrasts.*.real_*`) only applies to ARS; USD contrast entries carry `real_not_applicable` instead — a different signal from `real_unavailable` (ARS with no IPC data for that period). `resumenes.html` renders both currency blocks at equal visual weight (dual hero, one "tres clases de gasto" card per currency with USD spend present, one "quién registró" row per currency) and normalizes reports generated before 7.7.0 (single ARS-only shape, `usd_expenses` as a bare total) client-side so old reports keep rendering.

- **`dossier.py`** builds a deterministic snapshot for a period (cash-basis: grouped by the expense's own ART-adjusted date, not `fixed_expense_year`/`month` — that field is for the fixed-expense view specifically): totals, category breakdown, contrasts vs. prior month/3-mo avg/6-mo avg/same month last year (each individually present or absent depending on history depth, nominal **and** real), delta attribution by category, statistical outliers (per-category mean + 2·stdev, with the month's total shown with and without them), fixed-expense status per currency (including which are unpaid/unlinked this period — a USD fixed expense used to be invisible here, see the 7.7.0 fix below), dollars (both sides always; coverage ratio is what fraction of the month's *combined ARS-equivalent* spending — not ARS-only, since 7.7.0 — was covered by pesos obtained selling dollars), registration coverage per currency (explicitly worded as "who logged it," not spending share — see the gotcha below), taxonomy health, per-concept recurrence evidence (keyed by `currency:concept` since 7.7.0, so a peso "Hotel" and a dollar "Hotel" don't get merged), and hard facts (first-ever expense date, months of history available, now also per-currency) that both LLM calls use to calibrate confidence.
- **`inflation.py`** caches the IPC Nacional index (INDEC, via the `apis.datos.gob.ar` series API, series `148.3_INIVELNAL_DICI_M_26`) in `ipc_series`, used to deflate nominal contrasts to real terms (ARS only). INDEC publishes a month's index around mid-the-following-month, so the most recent month is usually missing — `refresh()` estimates *only that one* month by projecting the average month-over-month ratio of the last 3 published months, and overwrites the estimate with the real value once it's published. Never estimates more than one month out. If the API is unreachable, the report degrades to nominal-only and says so in the dossier — it never blocks report generation.
- **`report_ai.py`** makes exactly two Claude calls, both against `REPORT_ANTHROPIC_MODEL` (default `claude-opus-4-8` — a stronger/pricier tier than the Haiku used elsewhere, deliberately: this runs a handful of times a year and quality matters far more than cost) using adaptive thinking (`output_config.effort: "high"`) + structured JSON outputs (`output_config.format`, `json_schema`, no tool use): (1) `classify_expenses()` — system prompt instructs the model to label each of the month's *variable* (non-fixed) expenses, across both currencies, as `"recurring"` or `"exceptional"` with a confidence, weighing world knowledge against the dossier's empirical recurrence evidence and prior-month classifications for cross-month consistency, and explicitly never comparing amounts across currencies; the user-turn payload is `{expenses: <ARS + USD variable_expenses, each carrying its own currency>, recurrence_evidence, hard_facts, prior_months_classifications}` (the last one rendered as one text line per prior period: `"YYYY-MM: \"concept\" ($amount|U$S amount) -> label"`, from `db.get_recent_classifications_before()`, default 6-month lookback); `max_tokens=16000`. Returns `None` (not a partial result) on any failure, which short-circuits the second call. (2) `analyze()` — system prompt instructs headline/summary/findings (each required to cite a concrete dossier figure with an explicit currency symbol; no recommendations section, since the app has no budgets to advise against)/questions (tagged by type — `uncategorized` / `unlinked_fixed` / `other` — so the dashboard, not the model, builds the actual link; `unlinked_fixed` ids are unique across both currency blocks); the user-turn payload is `{dossier}` (the per-currency `partition` is already embedded in `dossier.currencies.{ARS,USD}.partition` by the time this call runs); `max_tokens=8000`. A materiality rule forces the headline/summary to mention USD spending explicitly when `equivalence.usd_share_pct >= 10` or when there's USD spend with no rate available to convert it — the case that motivated 7.7.0 (a large one-off USD trip expense used to be relegated to a small footnote section with no narrative mention at all). Both calls send the *entire* dossier/payload as a single JSON-serialized user turn — no chat history and no prompt caching. Usage metadata, estimated USD cost, latency and success/error are persisted in tenant-scoped `llm_calls`; `/superadmin` aggregates that measured telemetry by family, module, model and day.

- **`report.py`** orchestrates dossier → classify → analyze → persist, and computes the fingerprint (SHA256 of the period's *local* facts only — its expenses' id/amount/category/subcategory/user/date/fixed-link and dollar operations — deliberately excluding derived values like averages, so re-fingerprinting an unchanged period always reproduces the same hash even months later). The fingerprint isn't consumed yet — it's computed now so a drift badge landing in a follow-up PR has a baseline for every report generated from 2.3.0 on. `_build_partitions()` (renamed from `_build_partition` in 7.7.0) aggregates the classification call's per-expense labels into one recurring/exceptional split per currency, never mixing an ARS expense into the USD partition or vice versa.
- **Persistence is append-only.** `reports` never gets an UPDATE — every generation or regeneration is a new row; the latest (by `generated_at`) is what's displayed, but the full history stays queryable. `expense_classifications` rows are tied to the `reports.id` that produced them, for audit and for building the cross-month-consistency context on the next classification call.
- **Degrades gracefully.** If either LLM call fails (bad key, network, malformed output despite the schema), the report is still persisted with `llm_ok=0` — the dossier's fixed sections (all the numbers, both currencies) render regardless; only the narrative layer is missing, and the page says so explicitly rather than showing a blank state.
- **`/resumenes`** (dashboard.py) shows the most recent report with a month selector; `/resumenes/YYYY-MM` is the deep link. A period with no report shows a Generate button (synchronous — the two LLM calls take roughly 40-60s combined; `dashboard.py`'s Flask app runs with `threaded=True` specifically so this doesn't block other dashboard requests). Regenerate is deliberately understated (small text button, not primary) so it doesn't invite re-rolling until the report says something more pleasant.
- **7.7.0 fixes, found writing tests for the currency-parity work:** a USD fixed expense used to be invisible in the report — `_build_fixed_expenses()` had a `currency="ARS"` default no caller overrode, so it never appeared in the fixed total, the fixed/recurring/exceptional partition, or the "unpaid fixed expense" question the model can raise. Separately, `db.get_expenses_for_period_art()`/`get_expenses_excluding_period()`/`get_months_with_data()` were applying the ART (UTC-3) adjustment *twice* — once explicitly via `datetime(created_at, '-3 hours')`, once implicitly inside the Postgres compat shim's `strftime()` (which already does `AT TIME ZONE 'America/Argentina/Buenos_Aires'`, see `migrations/0001`) — silently misfiling any expense from the first three hours of ART on the 1st of a month into the previous month's report. Both predate 7.7.0 and are unrelated to the currency-parity feature; fixed alongside it because they were caught by its test suite and directly undermined the numbers the report depends on.

## Screens

**Telegram** is the primary input surface — no separate "screens," just chat plus inline keyboards (category picker, edit/confirm buttons, fixed-expense payment buttons, OCR/voice confirmation, NL edit candidate picker).

**Web application** (Flask, public auth/legal pages, one identity-only-but-no-family screen, plus 13 private product/administration pages):

| Route | Template | Purpose |
|---|---|---|
| `/` | `landing.html` | Public product landing: chat-bubble explainer of the natural-language/OCR/voice input, family sharing and privacy boundary; authenticated users continue to the dashboard (or onboarding, see below) |
| `/login` (`/registro` redirects here) | `login.html` | **Single identity entry screen** — Google OAuth (always shows the account selector) or email OTP, no name/family fields and exactly one Turnstile widget. Same response for known and unknown emails (no account-enumeration signal). Verifying identity never creates a family by itself |
| `/onboarding` | `onboarding.html` | **Step 2**, reached only by an authenticated identity with no active family membership — a real, durable state that survives closing the tab. Creates a new family (name + family name) or, when a pending invitation cookie is present, joins it (name only, family name never shown as editable). Anyone who already has a membership is redirected away from this route from any URL; the inverse is also enforced |
| `/privacy`, `/terms` | `privacy.html`, `terms.html` | Public legal pages required for OAuth publication (`/privacidad` and `/terminos` remain aliases) |
| `/unirme/<token>` | `join_family.html` | Public invitation landing — validates the token and hands the visitor to `/login` via a signed, session-independent cookie (survives the Google OAuth round trip and a closed tab); the actual join happens at `/onboarding`, which re-validates the invitation server-side again |
| `/dashboard` | `index.html` | Dashboard: onboarding checklist for new families (self-dismisses on completion, or manually via its close button — only owners see the "invite someone" step), designed empty states, month total (+ vs. prior month), Gastos/Promedio diario/Top del mes strip, charts and per-member filter |
| `/history` | `history.html` | Full expense history, filterable (concept search, month/year — each with an "all" option, category incl. uncategorized, subcategory scoped to the chosen category, fixed/variable status, user), active filters shown as removable chips, filter state reflected in the URL, inline edit (date, concept, amount, category, subcategory, fixed-expense link), delete. **Nav label is "Movimientos"** (renamed from "Historial" in 2.5.1 to avoid confusion with "Fijos") — route and template name are unchanged, only the visible label moved |
| `/ingresos` | `incomes.html` | Tenant-scoped income history and CRUD in ARS/USD, with a separate fully administrable income taxonomy; members may mutate only their own rows |
| `/lista` | `shopping.html` | Shared family shopping list grouped by expense category, with free-text quantity, recent bought items, re-add and pending nav badge |
| `/exportar` | `export.html` | RFC 4180/BOM CSV exports per business dataset and complete ZIP exit path, all tenant-scoped |
| `/settings` | `settings.html` | Categories: create/edit/delete (name, icon, color); subcategories CRUD; keywords CRUD (add/edit/delete, category + optional subcategory) |
| `/fijos` | `fijos.html` | Fixed expenses: CRUD (name, amount, category), any month's paid/pending status with progress bar, register-payment modal (amount + date, date constrained to the period being viewed), "ya lo pagué" candidate search to link an already-logged expense instead. As of 2.3.0, accepts `?year=&month=` to open a specific period directly (used by the monthly report's "unlinked fixed expense" question links) |
| `/dolares` | `dolares.html` | USD/ARS operations: history, monthly summary, historical-rate chart, add/edit/delete an operation — "+ Agregar cambio" opens a buy/sell toggle that relabels the amount/rate/ARS fields per direction |
| `/resumenes` | `resumenes.html` | Monthly AI-generated report (2.3.0; ARS/USD currency parity in 7.7.0) — see dedicated section above. Most recent report with a month selector; `/resumenes/YYYY-MM` deep link; Generate button when a period has none; understated Regenerate always available |
| `/config` | `config.html` | System: backup status + "Backup ahora"; restore is SSH-only. Superadmin-only (7.5.4) — a global full-DB backup trigger, not a per-family feature |
| `/familia` | `family.html` | Members, invitations, family rename, logical removal, ownership transfer, leave/delete actions |
| `/vincular-telegram` | `telegram_link.html` | Telegram deep link, desktop QR and live connected status |
| `/superadmin` | `superadmin.html` | Superadmin-only cross-family operational metrics, AI/cost analysis, quota overrides and recent failures |

All screens share the amber/orange design system (Plus Jakarta Sans, borderless cards with large radii, CSS-variable-driven Chart.js colors synced light/dark). The logo is the sole navigation entry for Dashboard. The desktop/mobile primary menu contains daily product areas; the avatar popup groups family administration, taxonomy, Telegram linking, export, system, superadmin access and logout.

## Superadmin operations

`/superadmin` requires both a valid application session and
`users.is_superadmin=true`. That flag comes only from the startup
`SUPERADMIN_EMAIL` bootstrap; no HTTP input can grant it.

- **Cross-family read boundary:** `db.get_superadmin_dashboard()` opens a
  separate connection and runs `SET LOCAL ROLE gastos_superadmin`. The role has
  `BYPASSRLS`; tenant policies are never relaxed for the application roles.
- **Adoption/activity:** families, active memberships, `last_login_at` activity
  over 7/30 days, total expenses and expenses created in the current month.
- **LLM telemetry:** last-30-day calls, failures and estimated USD cost per
  family; module/model token and cost breakdown; daily call/cost trend.
- **Quota overrides:** `family_quota_overrides` stores optional positive
  routine-daily and summary-monthly limits. A missing row/value falls back to
  100 and 15 respectively. Runtime admission still happens inside the current
  tenant context.
- **Operating-cost assumptions:** `infrastructure_cost_settings` stores a
  manually maintained unit label, USD rate, monthly volume and note for
  Anthropic, OpenAI, Resend and Cloudflare R2. These are planning assumptions;
  measured LLM cost continues to come from `llm_calls` and is not silently
  replaced or double-counted.
- **Recent failures:** tenant-attributed unhandled web/Telegram exceptions are
  stored in `system_errors`; failed model calls come from `llm_calls`. Failures
  before a family can be resolved are still logged to container stdout but cannot be
  inserted into a tenant table.
- **Mutations:** quota and cost changes require superadmin plus CSRF. There are
  intentionally no endpoints for impersonation, billing or editing another
  family's expenses/incomes/settings.

Phase 8 also shipped a navigation cleanup beyond the original panel scope:
daily product areas remain in the main header, Dashboard is reached through the
logo, appearance selector, and administration/export/logout live in the avatar popup. Version 7.6.0
fixes ancestor overflow clipping so that popup can render below the sticky
header.

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
- `db.py` — raw PostgreSQL operations through psycopg pools. `get_conn()` applies the RLS-bound role and transaction-local tenant before each domain transaction; platform identity resolution and the superadmin metrics, overrides, cost settings, and error operations use the dedicated bypass role.
- `dashboard.py` — Flask app; authenticated product routes, superadmin guards/routes and UTC → Buenos Aires conversion for all display
- `auth.py` — opaque server-side sessions (resolve to an identity even without an active family membership, so a verified-but-family-less identity is durable), hashed OTPs, Resend, bare Google identity linking (never creates a family on its own), Turnstile/rate limits, invitation lifecycle, active/historical memberships, ownership transfer, family creation/joining for an already-identified user (`create_family_for_existing_user`, `accept_invitation` — the onboarding step), and the dashboard onboarding checklist's manual dismiss (`dismiss_onboarding`). Platform access uses transaction-local `gastos_superadmin`.
- `llm_limits.py` — per-family admission control for all Anthropic/OpenAI calls: defaults to 100 routine calls/day and 15 report generations/month, honors optional `family_quota_overrides`, and allows two concurrent calls; calendar limits use `families.timezone`.
- `ocr.py` — Anthropic SDK call; returns `{comercio, monto, fecha}`
- `audio.py` — Whisper transcription + Claude extraction; returns `[{concept, amount, confidence}]`
- `dolar.py` — natural-language USD buy/sell parsing (`looks_like_dolar` gate + `parse_dolar`); confidence-based auto-save
- `backup.py` — custom-format `pg_dump` to private R2 with remote size verification and 90-day lifecycle
- `export_data.py` — tenant-scoped RFC 4180 CSV and ZIP generation with UTF-8 BOM, family-local ISO timestamps, spreadsheet-formula neutralization, and no authentication identifiers
- `seed.py` — `create_family_defaults(conn, family_id)` creates generic taxonomy for a new family; schema changes are Alembic-only
- `dossier.py` — deterministic aggregation for the monthly report (2.3.0; ARS/USD currency parity in 7.7.0); no LLM involved. `build_dossier(year, month)` reads via `db.py` (ART-adjusted cash-basis queries) plus `inflation.py`, returns `currencies.{ARS,USD}` as two parallel same-shape blocks (totals, contrasts, delta attribution, outliers, fixed-expense status, taxonomy, registration coverage, hard-facts) plus a top-level reference-only `equivalence` (USD spend valued at the family's own dollar rate — never summed into any total)
- `inflation.py` — IPC Nacional fetch/cache/estimate/deflate (2.3.0), ARS only; `refresh()` hits `apis.datos.gob.ar`, `deflate()` converts a nominal amount between two periods' prices, returning `None` (not a silent nominal fallback) when an index is missing
- `report_ai.py` — the two Claude calls behind the monthly report (2.3.0; currency-parity prompt rules in 7.7.0): `classify_expenses()` (recurring/exceptional per variable expense, both currencies) and `analyze()` (narration; every cited amount must carry its currency symbol, and must mention USD spend explicitly once it's material). Structured JSON outputs, adaptive thinking, `REPORT_ANTHROPIC_MODEL` (default `claude-opus-4-8`) — separate model config from the Haiku extraction calls elsewhere. No DB/Telegram/Flask I/O
- `report.py` — orchestrates dossier → classify → analyze → persist for the monthly report (2.3.0); computes the append-only-friendly `fingerprint()` (period-local facts only, no derived values). `_build_partitions()` (7.7.0) produces one recurring/exceptional split per currency. Degrades to a dossier-only report (`llm_ok=0`) if either LLM call fails rather than losing the generation entirely

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
- **The Postgres `strftime()` compat function (`migrations/0001`) already applies the ART timezone shift internally** — it does `value AT TIME ZONE 'America/Argentina/Buenos_Aires'` before formatting. Call it directly on `created_at`; wrapping it in an extra `datetime(created_at, '-3 hours')` (a leftover from an earlier SQLite-style query) double-applies the shift and silently misfiles any expense from the first three hours of ART on the 1st of a month into the previous month (found and fixed in 7.7.0, in `get_expenses_for_period_art`/`get_expenses_excluding_period`/`get_months_with_data`). The native `date(timestamptz)` function is different — it has no built-in TZ conversion (it truncates in the session's `TimeZone`, which is UTC), so `date(datetime(created_at, '-3 hours'))` elsewhere in `db.py` (e.g. `get_first_expense_date`) is correct as-is and should NOT be "simplified" to match.

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
