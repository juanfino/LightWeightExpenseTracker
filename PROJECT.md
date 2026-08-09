# LightWeightExpenseTracker

Family expense tracker. Users send plain-text messages to a Telegram bot; the app parses, categorizes, and persists catalogue-backed currency amounts to PostgreSQL. A Flask dashboard provides monthly/annual visualizations, history, and configuration.

- **Version:** 7.17.0 (canonical source: `gastos/config.yaml`)
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

**Database:** PostgreSQL 17 with Alembic (current head: `0015`). Platform tables are `families`, `users`, `memberships`, `sessions`, `otp_codes`, `oauth_identities`, `invitations`, `telegram_link_tokens` and global `infrastructure_cost_settings`/`quotes`/`currencies`; `quotes` and `currencies` are curated installation-level content with no `family_id`, tenant RLS or CRUD UI. Only `families`, `users` and `memberships` carry their own RLS policies among the platform tables — `sessions`, `otp_codes`, `oauth_identities`, `invitations` and `telegram_link_tokens` have none, and rely solely on role-based `GRANT`s plus `gastos_superadmin` access for cross-identity reads. `currencies` seeds exactly ARS, USD, BRL and EUR with code, display symbol and decimal count; adding another supported currency is an installation-level insert. Tenant tables include `reports`, `expense_classifications`, `family_report_preferences` and `report_forecasts`; their monetary currency columns, `families.default_currency` and `report_forecasts.currency` use plain FKs to the global catalogue (eight such FKs in total as of migration `0015`, one per currency-bearing column). The default is owner-writable from `/familia` and changes future implicit input/report primacy only—existing rows retain their currency. Tenant rows carry `family_id NOT NULL` with forced RLS on transaction-local `app.family_id`; composite tenant foreign keys reject cross-family references. Normal and read-only roles remain subject to RLS; `gastos_superadmin` is the dedicated `BYPASSRLS` role used by `/superadmin` for cross-family reads. Amounts retain their native currency and timestamps are UTC; families store their display timezone.

**Money arithmetic and display:** psycopg `NUMERIC` values stay as Python `Decimal` throughout application arithmetic. `money.py` centralizes two-place storage arithmetic with `ROUND_HALF_UP`, exact server formatting, and the explicit conversion to JSON numbers at HTTP, report-persistence and LLM boundaries. Symbol and display precision come from currency metadata; thousands/decimal separators independently come from the reader convention (currently Rioplatense Spanish). Browser surfaces all use `static/money.js`, configured once from `base.html` with that catalogue and `es-AR`, instead of template-local helpers. Percentages, ratios and confidence remain deliberate non-money statistics; `ipc_series.value` retains its full `NUMERIC(20,12)` precision.

`telegram_link_tokens` stores only SHA-256 token hashes; links expire after 15 minutes and are single-use. `infrastructure_cost_settings` is installation-level metadata editable only by the superadmin role. `family_quota_overrides` and `system_errors` remain tenant-scoped and protected by forced RLS.

**Backup:** Daily at 21:00 ART via APScheduler — custom-format `pg_dump` uploaded to private Cloudflare R2 and remotely size-verified. R2 retains 90 days. Restore is SSH-only (`docs/RUNBOOK.md`) and was tested from production.

### Currency-operation schema (7.16.0)

`cambios_dolar` keeps its legacy table name but stores generic direction: `amount_given/currency_given` → `amount_received/currency_received`. `rate_received_per_given NUMERIC(30,18)` makes the rate convention explicit; both currencies are FK-backed, distinct, and all amounts/rates are positive. Migration `0014` maps historical sales to USD→ARS and purchases to ARS→USD, then removes stored `tipo`, `monto_usd`, `monto_ars` and `cotizacion`.

## Stack

- Python, Flask, PostgreSQL 17, psycopg 3, Alembic
- `python-telegram-bot` v20 (async, long polling)
- Anthropic API (`claude-haiku-4-5-20251001`) for OCR receipt scanning, voice/dollar extraction, and the natural-language intent layer (tool use / function calling); `claude-opus-4-8` (configurable, separate model tier) for the monthly report's classification and narration calls (structured JSON outputs, no tool use)
- The national open-data time-series API at `apis.datos.gob.ar` (IPC Nacional series) — free, unauthenticated, no API key. The app's first external dependency outside Anthropic/OpenAI; see **Monthly AI report** below for its failure mode
- Docker (Alpine base), multi-arch (`linux/arm64`, `linux/amd64`)
- GitHub Actions → `ghcr.io/juanfino/lightweightexpensetracker` (public registry, auto-build on merge to `main`, via `.github/workflows/docker-publish.yml`). A separate workflow, `.github/workflows/test.yml`, runs on every PR and every push to `main`: it spins up a real `postgres:17-alpine` service container, runs `python -m unittest discover -s gastos/tests -p "test_*.py"`, then `postgres_smoke.py` and `postgres_web_smoke.py` — it does not publish anything, it only gates
- Deploys are **manual**: `docker compose pull gastos && docker compose up -d gastos` on the Pi

## Key Features

- **Expense entry via Telegram:** plain text, e.g. `Supermercado 150000`
- **Natural-language intent layer:** conversational messages that aren't the plain `concept amount` form route through a Claude tool-use layer (`intent.py`) covering logging, editing, taxonomy management and read-only reports. Model-generated reads run through `sqlro.py`: one `SELECT`/`WITH`, PostgreSQL read-only role, tenant RLS, statement timeout and row cap. Mutations stay parameterized and Telegram users may edit only their own expenses.
- **Auto-categorization:** keyword matching (two-level: category + subcategory). Silent inference — no extra prompts.
- **OCR receipt scanning:** send a photo; bot extracts `{comercio, monto, fecha}` via Anthropic Vision, prompts for confirmation before saving
- **Voice expense entry:** send a voice note (e.g. "ferretería diez mil pesos"); bot transcribes with OpenAI Whisper, normalizes written numbers to digits via Claude, and prompts for confirmation before saving
- **Argentine number formatting:** `.` = thousands separator, `,` = decimal (e.g. `$5.580,00`). `_parse_monto()` handles both notations; `100.000` → 100000, `2.500,50` → 2500.5
- **Gastos Fijos:** recurring fixed expense tracking; `/fijos` shows the month's payment status with inline buttons to register a payment or search for one already logged. Detection runs downstream of expense creation on **every** input path (plain text, NL, voice, OCR, dashboard manual add) — not just the plain-text fast path — via a shared `fixed_matcher.py` (word-overlap ≥3 chars) so the fixed/variable split doesn't depend on how the user happened to log the expense. Linking always forces the expense's category/subcategory to the fixed expense's own, so a recurring bill can't drift category month to month. "✓ Ya lo pagué" searches already-logged, unlinked expenses for the period (concept overlap + category + amount proximity) and offers to link one instead of just flagging "paid" with no amount; explicitly declining still lets the user log the amount directly. The link (+ period) is an ordinary, editable field on the expense, alongside category/subcategory. The date is also editable everywhere expenses are (dashboard history row, `/editar ID fecha DD/MM/AAAA`, NL edit — `"el gasto 124 fue el 15 de junio"`); a date-only edit preserves the fixed-expense period it's linked to (period and date are independent — see 2.1.0). Registering a past-period payment (the dashboard's "+ Registrar pago"/"ya lo pagué" flow when browsing a month other than the current one) defaults the new expense's date within that period instead of today, and the picker is constrained to that month
- **Generic currency exchanges:** `exchange.py` extracts directional conversions such as USD→ARS or BRL→EUR behind a cheap verb+currency prefilter. Storage records amount/currency given, amount/currency received, and `rate_received_per_given`; buy/sell is derived only when one side is the family default. Legacy `CambioDolar <usd> <cotizacion>` remains a USD→ARS adapter. `/dolares` (visible as **Cambios**) provides generic history, CRUD, monthly summaries and pair-scoped charts.
- **Flask dashboard:** mobile-friendly, with a dashboard-only daily quote selected deterministically from the family's local date + id, per-member filter, Chart.js visualizations (monthly, annual, weekly, by category, last-6-months trend), sortable/filterable history with inline edit, full category/subcategory/keyword CRUD, fixed-expense CRUD and backup status/manual trigger. Restore is deliberately SSH-only. Visual identity uses the amber/orange design system across every screen — see **Screens** below.
- **Shared accounting period (7.12.0):** Dashboard, Movimientos, Ingresos, Fijos, Cambios and Resúmenes use the canonical `?period=YYYY-MM` query. An explicit URL wins over the unsigned, dedicated `gastos_period` preference cookie; without either, the default is the current month in `families.timezone`. Every link among those screens carries the period. Their single shared control is above page-local content and shows a calm amber stale-period treatment plus a direct return to the family-current month. Invalid or out-of-range values redirect silently to that current month. Movimientos' `year`/`month` and other filters remain local URL state: the shared period initializes them, but choosing “all” or another local range never rewrites the cookie/global period.
- **Monthly AI-generated report** (2.3.0): `/resumenes` — see dedicated section below
- **Per-family report narrative preferences (7.13.0):** any active member can edit the family's shared emphasis, tone, length, bounded focus and suggestion policy directly on `/resumenes`; they affect only the next generated narrative, never deterministic sections or existing append-only reports.
- **Next-month forecast (7.14.0):** every new report stores a deterministic forecast for the month immediately after its own period, using only facts through that period. `/resumenes` shows the frozen per-currency ranges and, when target-month expenses later exist, an inline predicted-versus-actual comparison.
- **Users:** Juampi and Cele, both onboarded and actively using the app
- **Family management:** `/familia` lets the owner generate/revoke seven-day single-use invitation links, rename the family, select any catalogue-backed default currency without converting history, logically remove members, transfer ownership, or delete the family with exact-name confirmation. Invitees join as members through Google or email OTP. `SUPERADMIN_EMAIL` is startup-only; no HTTP path can change the flag.
- **Telegram linking and LLM safeguards:** `/vincular-telegram` provides a 15-minute, one-use deep link and QR with live confirmation; unknown chats get linking help, groups are rejected, and unlinking lives in `/familia`. Routine AI calls default to 100/family/day, Resúmenes to 15 generations/family/month, and `llm_limits.py` permits at most two concurrent LLM calls per family. The superadmin may override either quota per family. Unhandled bot/web errors are written to the rotated container log; tenant-attributed failures are also retained for the panel and are not sent through the family bot.
- **Superadmin operations:** `/superadmin` shows cross-family adoption/activity, expense volume, LLM calls/cost by family/module/model/day, editable operating-cost assumptions, optional family quota overrides and recent web/Telegram/LLM failures. It does not provide impersonation, billing or business-data editing.
- **Portable exit path:** `/exportar` produces tenant-scoped RFC 4180 CSVs (UTF-8 BOM, ISO dates, spreadsheet-formula neutralization) and a complete ZIP including movements, fixed expenses, dollars, incomes, shopping and taxonomy.

## Monthly AI report

A retrospective on demand, not a schedule (scheduling/Telegram delivery are a follow-up). The governing rule: the model never does arithmetic. Every number is computed by `dossier.py` from SQL aggregation; the model only narrates and makes one bounded judgment call.

**Reports support N catalogue currencies end to end (7.17.0).** `dossier.py` derives `currencies` from codes actually used in the period plus the family default, ordered with the default first. Every block has the same totals, categories, contrasts, outliers, fixed state, taxonomy, registration coverage, variable expenses, history depth and partition, and no aggregate crosses currencies. `equivalence.items` is one reference-only valuation per non-default currency in the default currency, using only the family's current direct exchange, current reverse exchange, or latest direct pair operation within 12 months; unavailable pairs are explicit. The values never become expenses or feed totals, contrasts, partitions or forecasts. `inflation.py` declares which currencies have a series (currently only ARS), so all others carry `real_not_applicable`. The page keeps the default currency fully expanded and puts additional currencies into collapsed detail panels to stay legible at four or more, while client normalization continues to render both pre-7.7 single-currency rows and 7.7–7.16 fixed ARS/USD rows.

- **`dossier.py`** builds a deterministic snapshot for a period (cash-basis: grouped by the expense's own ART-adjusted date, not `fixed_expense_year`/`month` — that field is for the fixed-expense view specifically): totals, category breakdown, contrasts vs. prior month/3-mo avg/6-mo avg/same month last year (each individually present or absent depending on history depth, nominal **and** real), delta attribution by category, statistical outliers (per-category mean + 2·stdev, with the month's total shown with and without them), fixed-expense status per currency (including which are unpaid/unlinked this period — a USD fixed expense used to be invisible here, see the 7.7.0 fix below), dollars (both sides always; coverage ratio is what fraction of the month's *combined ARS-equivalent* spending — not ARS-only, since 7.7.0 — was covered by pesos obtained selling dollars), registration coverage per currency (explicitly worded as "who logged it," not spending share — see the gotcha below), taxonomy health, per-concept recurrence evidence (keyed by `currency:concept` since 7.7.0, so a peso "Hotel" and a dollar "Hotel" don't get merged), and hard facts (first-ever expense date, months of history available, now also per-currency) that both LLM calls use to calibrate confidence.
- **`inflation.py`** caches the IPC Nacional index (INDEC, via the `apis.datos.gob.ar` series API, series `148.3_INIVELNAL_DICI_M_26`) in `ipc_series`, used to deflate nominal contrasts to real terms (ARS only). INDEC publishes a month's index around mid-the-following-month, so the most recent month is usually missing — `refresh()` estimates *only that one* month by projecting the average month-over-month ratio of the last 3 published months, and overwrites the estimate with the real value once it's published. Never estimates more than one month out. If the API is unreachable, the report degrades to nominal-only and says so in the dossier — it never blocks report generation.
- **`report_ai.py`** makes exactly two Claude calls against `REPORT_ANTHROPIC_MODEL` (default `claude-opus-4-8`) using adaptive thinking and structured JSON outputs: (1) `classify_expenses()` labels every variable expense `recurring` or `exceptional`, using currency-scoped recurrence/history and never comparing scales across currencies; (2) `analyze()` narrates the full dossier under immutable N-currency rules. Every amount uses `currency_metadata`'s symbol, real figures are allowed only when supplied, equivalences are approximate/reference-only, and every material or unconvertible non-default spend is forced into headline and summary. Short-history calibration remains per currency. Family preferences compile only soft guidance beneath these rules. `prompt_version()` fingerprints the exact response-shaping dictionaries plus resolved preferences, and reports store the same snapshot.

Both report calls cross the explicit numeric JSON boundary immediately before serialization: monetary `Decimal` values arrive as JSON numbers, never strings, without changing either payload's structure.

- **`report.py`** orchestrates dossier → classify → analyze → persist, resolves the current family preferences once per generation, stores their JSON snapshot and includes them in `report_ai.prompt_version()`; legacy rows remain readable with no synthetic backfill. It also computes the separate data fingerprint (SHA256 of period-local facts only). `_build_partitions()` aggregates the classification call's per-expense labels into one recurring/exceptional split per currency, never mixing currencies.
- **`forecast.py`** computes forecasts without an LLM. The exact method id is `category_median_iqr_tail_v1`: per currency it uses at most the six most recent months with activity through the report cutoff; variable estimates require at least three. A category is habitual when it appears in strictly more than half of those months and its median monthly amount reaches 2% of the median monthly variable total. Historical category amounts are summarized with median and Q1–Q3. Everything outside habitual categories is summed into a monthly tail: its center is the median non-zero tail weighted by observed frequency, its lower bound is the all-month Q1 and its upper bound is the non-zero Q3, so a rare repair or present is not erased by a zero median. Fixed definitions that existed by the cutoff are the high-confidence bucket. Bucket ranges add to the displayed total range without crossing currencies.
- **Inflation in forecasts:** for any currency with a registered series (currently ARS), historical values are brought into target-month terms only when every observed month and the cutoff have index data. The target index is projected one month from up to three sequential ratios; a later actual target index is ignored. Missing inputs produce `real_unavailable`; currencies without a series produce `real_not_applicable`.
- **Persistence is append-only.** `reports` never gets an UPDATE — every generation or regeneration is a new row; the latest is displayed and the full history stays queryable. `preferences_json` preserves how that narrative was configured even after the shared `family_report_preferences` row changes. `report_forecasts` stores one insert-only row per report/currency with target, cutoff, method id and frozen bucket/category ranges under forced RLS plus a composite family/report FK. Existing reports have no forecast rows and render unchanged. Target actuals are read at display time only for backtesting; they never mutate the stored prediction. `expense_classifications` remain tied to the producing report.
- **Degrades gracefully.** If either LLM call fails, the report is still persisted with `llm_ok=0`; every deterministic currency block renders and only the narrative is missing.
- **`/resumenes?period=YYYY-MM`** (dashboard.py) shows the report for the shared accounting period; the former `/resumenes/YYYY-MM` deep link redirects to that canonical form. A period with no report shows a Generate button (synchronous — the two LLM calls take roughly 40-60s combined; `dashboard.py`'s Flask app runs with `threaded=True` specifically so this doesn't block other dashboard requests). Regenerate is deliberately understated (small text button, not primary) so it doesn't invite re-rolling until the report says something more pleasant.
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
| `/dashboard?period=YYYY-MM` | `index.html` | Dashboard: curated daily quote above the shared period selector, onboarding checklist for new families (self-dismisses on completion, or manually via its close button — only owners see the "invite someone" step), designed empty states, month total (+ vs. prior month), Gastos/Promedio diario/Top del mes strip, charts and per-member filter |
| `/history?period=YYYY-MM` | `history.html` | Full expense history, filterable (concept search, local month/year — each with an "all" option, category incl. uncategorized, subcategory scoped to the chosen category, fixed/variable status, user), active filters shown as removable chips, filter state reflected in the URL without changing the shared period, inline edit (date, concept, amount, category, subcategory, fixed-expense link), delete. **Nav label is "Movimientos"** (renamed from "Historial" in 2.5.1 to avoid confusion with "Fijos") — route and template name are unchanged |
| `/ingresos?period=YYYY-MM` | `incomes.html` | Tenant-scoped income history and CRUD for the shared period in catalogue currencies, with a separate fully administrable income taxonomy; members may mutate only their own rows |
| `/lista` | `shopping.html` | Shared family shopping list grouped by expense category, with free-text quantity, recent bought items, re-add and pending nav badge |
| `/exportar` | `export.html` | RFC 4180/BOM CSV exports per business dataset and complete ZIP exit path, all tenant-scoped |
| `/settings` | `settings.html` | Categories: create/edit/delete (name, icon, color); subcategories CRUD; keywords CRUD (add/edit/delete, category + optional subcategory) |
| `/fijos?period=YYYY-MM` | `fijos.html` | Fixed expenses: CRUD (name, amount, category), selected-period paid/pending status with progress bar, register-payment modal (amount + date, date constrained to the period being viewed), "ya lo pagué" candidate search to link an already-logged expense instead. Report questions use this canonical period link. |
| `/dolares?period=YYYY-MM` | `dolares.html` | **Cambios**: generic directional conversions, selected-period pair summary, history, pair-scoped rate/volume charts and add/edit/delete forms |
| `/resumenes?period=YYYY-MM` | `resumenes.html` | Monthly AI-generated N-currency report for the shared period; default currency primary, additional period currencies collapsible, and all three historical dossier shapes compatible. `/resumenes/YYYY-MM` redirects here. |
| `/config` | `config.html` | System: backup status + "Backup ahora"; restore is SSH-only. Superadmin-only (7.5.4) — a global full-DB backup trigger, not a per-family feature |
| `/familia` | `family.html` | Members, invitations, family rename, owner-only default-currency selection from the catalogue (never converts existing history), logical removal, ownership transfer, leave/delete actions |
| `/vincular-telegram` | `telegram_link.html` | Telegram deep link, desktop QR and live connected status |
| `/superadmin` | `superadmin.html` | Superadmin-only cross-family operational metrics, AI/cost analysis, quota overrides and recent failures |

All screens share the amber/orange design system (Plus Jakarta Sans, borderless cards with large radii, CSS-variable-driven Chart.js colors synced light/dark). The logo is the sole navigation entry for Dashboard. The desktop/mobile primary menu orders Movimientos, Ingresos, Fijos, Cambios and Resúmenes first, then visually separates Lista de compras as a non-accounting area; the avatar popup groups family administration, taxonomy, Telegram linking, export, system, superadmin access and logout.

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
| `/editar ID moneda VALOR` | Edit an expense's currency (catalogue codes only; refused if the expense is linked to a fixed expense) |
| `/recat CONCEPTO CATEGORÍA` | Bulk-reassign expenses matching a concept to a category |
| `/borrar ID` | Delete an expense |
| `/add_keyword PALABRA CATEGORÍA` | Add a keyword → category mapping |
| `/categorias` | List categories |
| `/nueva_categoria Nombre Emoji Color` | Create a category (emoji/color optional) |
| `/ayuda` | Full command + usage reference |
| `/start` | No args: private chats get a short "connect via the dashboard" nudge (or "already connected" if authorized), non-private chats get the "private chats only" message. `/start <token>` consumes a `/vincular-telegram` deep-link token and links the chat |
| `CambioDolar <usd> <cotizacion>` | Legacy explicit dollar-sale command |
| photo/document image | OCR ticket scan (`ocr.py`) → confirm before saving |
| voice note | Whisper transcription + Claude extraction (`audio.py`) → auto-save if confidence ≥ 0.9, else confirm |
| free-form text (anything not matched above) | Routed to the NL intent layer (`intent.py`) if it looks conversational — see **Natural-language intent layer** above |

## Module Responsibilities

- `main.py` — entrypoint; loads env config, initializes DB, schedules backup, starts Flask thread, starts bot polling
- `bot.py` — Telegram handlers; holds per-`chat_id` pending-state dicts (`pending_ocr`, `pending_amount_edit`, `pending_exchange`, `pending_nl_confirm`, `pending_nl_pick`, `pending_fixed_direct`, …). Hybrid routing in `handle_message`: fast path for plain `concept amount`, else the intent layer. `_maybe_offer_fixed_link()` is the single seam every expense-creation path calls after saving to offer a fixed-expense link
- `intent.py` — natural-language intent layer via Claude tool use; returns a structured result dict (`log`/`edit`/`category`/`subcategory`/`report`/`reply`). Injects taxonomy + the user's recent expenses + ART date; performs no Telegram I/O. `edit_expense`'s `changes` can include `fixed_expense` (link by name, or `"ninguno"` to unlink)
- `fixed_matcher.py` — matching heuristics shared by `bot.py` and `dashboard.py` so both surfaces agree on what counts as a match: `find_fixed_expense_matches` (new expense → fixed-expense definition, word-overlap) and `find_candidate_expenses` (fixed expense → already-logged unlinked expenses for a period, scored by word overlap + category + amount proximity). Also `expense_period()`, converting an expense's own UTC timestamp to a (year, month) in a given tz
- `sqlro.py` — PostgreSQL read-only executor: `SELECT`/`WITH` only, one statement, `gastos_readonly` role, tenant RLS, timeout and row cap
- `pgcompat.py` — small DB-API compatibility shim so the rest of the codebase can keep sqlite-style `?` placeholders, `Row`/`Cursor` objects and `lastrowid` while PostgreSQL (via `psycopg_pool.ConnectionPool`) is the only storage engine. `Row` preserves psycopg `Decimal` values instead of coercing `NUMERIC` to `float`. Owns the named connection pools (`pool()`/`current_pool()`/`select_pool()`) and the `contextvars`-based transaction-local `family_id`/`user_id` that `db.get_conn()` reads to set `app.family_id` for RLS
- `money.py` — central monetary boundary: parses/quantizes stored amounts to two decimal places with `ROUND_HALF_UP`, formats server-side amounts from currency metadata plus reader separators, rounds Decimal-derived statistics explicitly, and converts Decimal-bearing structures to JSON numbers only when data leaves Python; `static/money.js` is the corresponding shared browser formatter
- `llm_usage.py` — shared best-effort LLM cost/latency accounting: `record()` computes an estimated USD cost from a fixed per-model `MODEL_RATES` table (token-based for Anthropic models, per-audio-minute for `whisper-1`) and writes one row to `llm_calls` per call. Called by every module that makes an LLM/Whisper call (`ocr.py`, `audio.py`, `exchange.py`, `intent.py`, `report_ai.py`)
- `currency_detection.py` — shared code/symbol/colloquial detection and stripping; ambiguous `$`/“pesos” resolve to the family default and unknown ISO-like suffixes fail visibly
- `parser.py` — parses free-text into `{concept, amount, currency}` using the shared catalogue detector and family default; returns `None` if no valid amount
- `categorizer.py` — keyword matching (accent/case-insensitive); returns `(category_id, subcategory_id)`, both nullable. `normalize()` is reused for taxonomy dup-guarding and for the history screen's concept search
- `db.py` — raw PostgreSQL operations through psycopg pools. `get_conn()` applies the RLS-bound role and transaction-local tenant before each domain transaction; platform identity resolution and the superadmin metrics, overrides, cost settings, and error operations use the dedicated bypass role.
- `dashboard.py` — Flask app; authenticated product routes, superadmin guards/routes and UTC → Buenos Aires conversion for all display; injects the global currency catalogue, reader locale and current `families.default_currency` once through the base template context
- `auth.py` — opaque server-side sessions (resolve to an identity even without an active family membership, so a verified-but-family-less identity is durable), hashed OTPs, Resend, bare Google identity linking (never creates a family on its own), Turnstile/rate limits, invitation lifecycle, active/historical memberships, ownership transfer, family creation/joining for an already-identified user (`create_family_for_existing_user`, `accept_invitation` — the onboarding step), and the dashboard onboarding checklist's manual dismiss (`dismiss_onboarding`). Platform access uses transaction-local `gastos_superadmin`.
- `llm_limits.py` — per-family admission control for all Anthropic/OpenAI calls: defaults to 100 routine calls/day and 15 report generations/month, honors optional `family_quota_overrides`, and allows two concurrent calls; calendar limits use `families.timezone`.
- `ocr.py` — Anthropic SDK call; returns `{comercio, monto, fecha}`
- `audio.py` — Whisper transcription + Claude extraction; returns `[{concept, amount, confidence}]`
- `exchange.py` — natural-language directional exchange parsing (`looks_like_exchange` + `parse_exchange`), derived default-relative buy/sell labels and confidence-based auto-save
- `backup.py` — custom-format `pg_dump` to private R2 with remote size verification and 90-day lifecycle
- `export_data.py` — tenant-scoped RFC 4180 CSV and ZIP generation with UTF-8 BOM, family-local ISO timestamps, spreadsheet-formula neutralization, and no authentication identifiers
- `seed.py` — `create_family_defaults(conn, family_id)` creates generic taxonomy for a new family; schema changes are Alembic-only
- `dossier.py` — deterministic N-currency aggregation for the monthly report. It returns one same-shape block per period currency plus the family default and a top-level `equivalence.items` map of reference-only pair valuations from family exchange history.
- `inflation.py` — IPC Nacional fetch/cache/estimate/deflate (2.3.0), ARS only; `refresh()` hits `apis.datos.gob.ar`, `deflate()` converts a nominal amount between two periods' prices, returning `None` (not a silent nominal fallback) when an index is missing
- `forecast.py` — deterministic next-month forecast (7.14.0), no LLM involved; method id `category_median_iqr_tail_v1`. Per currency it combines active fixed definitions, habitual-category median/IQR estimates and a historical tail/IQR bucket (fixed-only below three months of history), applies inflation factors only where `inflation.py` declares a series, and persists one immutable row per report/currency — stored forecasts are never recomputed, and target-month actuals are read back read-only for the backtest comparison
- `report_ai.py` — the two Claude calls behind the monthly report: `classify_expenses()` (recurring/exceptional per variable expense, currency-scoped) and `analyze()` (N-currency narration with hard symbol, inflation, equivalence, materiality and short-history guarantees). Structured JSON outputs, adaptive thinking, `REPORT_ANTHROPIC_MODEL` (default `claude-opus-4-8`).
- `report_preferences.py` — canonical defaults, strict web validation and tolerant storage resolution for the family's shared narrative settings. Available emphasis keys correspond only to deterministic dossier/forecast structures; default preferences add no soft prompt guidance.
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
| `DATABASE_URL` | Yes | PostgreSQL connection URL, read directly by `pgcompat.pool()` |
| `POSTGRES_PASSWORD` | Yes | Password for the `postgres` container itself (`docker-compose.yml`'s `postgres` service); not read by the Python app, only by the `postgres` image and implicitly via `DATABASE_URL` |
| `R2_ENDPOINT`, `R2_BUCKET` | Yes | Cloudflare R2 backup destination (`backup.py`) |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Yes | Bucket-scoped R2 credentials (`backup.py`) |
| `DASHBOARD_PORT` | No | Default: `5000`. The Pi sets `8090` to free up 5000 for Frigate |
| `DB_POOL_<NAME>_MAX` | No | Default: `5`. Per-pool max connection count read by `pgcompat.pool(name)`, e.g. `DB_POOL_APP_MAX`; undocumented elsewhere and not currently set on the Pi |

## Known Gotchas

- **Monetary rounding is deliberately `ROUND_HALF_UP`:** every amount is quantized to two decimal places through `money.py`. Do not replace it with Python's built-in `round()` or Decimal's default `ROUND_HALF_EVEN` (banker's rounding); ties such as `2.345` must produce `2.35`.
- **Dashboard port:** the code's own default is 5000, but the Pi runs it on 8090 (`DASHBOARD_PORT` in `~/.env`) to leave 5000 free for Frigate. Don't assume 5000 is what's live on the Pi.
- **Argentine number format:** `.` is thousands, `,` is decimal. Test edge cases when touching `parser.py`.
- **`cloudflared` targets:** use service names, never `localhost` — it runs `network_mode: host` but Cloudflare's tunnel config references Docker service DNS.
- **Flask + bot in one process:** threading is a deliberate tradeoff. Don't split into separate services without explicit discussion.
- **Subcategory inference is silent:** no extra Telegram prompts after category assignment.
- **DNS staleness on long-running containers:** `resolv.conf` can go stale if the host network changes. Mitigated by `dns: [8.8.8.8, 1.1.1.1]` and healthcheck in `docker-compose.yml`. Symptom: `[Errno -3] Try again` in bot logs while container shows `Up`.
- **Whisper returns written numbers:** Whisper transcribes verbatim — "diez mil" stays as text. Claude (`audio.py`) normalizes them to digits before saving. If you bypass `audio.py` and use Whisper output directly, amounts will be `null`.
- **Always sync local git state before starting an agent session** — see AGENTS.md's sync/branch ritual. This applies to any coding agent, not just one tool: agents that work from a local checkout (Claude Code included) build from local disk, not by re-fetching from GitHub each turn.
- **Dockerfile build context is the repo root** (not `gastos/`): `docker build -f gastos/Dockerfile .`
- **2.0.0 fixed-expense migration is lossy by design:** old `fixed_expense_payments` rows with no linked expense (from the old "✓ Ya lo pagué" flag-only flow) had no amount to migrate and were dropped rather than fabricated from `estimated_amount`. Check the startup logs after upgrading a DB that predates 2.0.0 for the converted/dropped counts, and re-link any dropped months by hand via the new "ya lo pagué" candidate search.
- **Date-only writes are always stored at `03:00:00` UTC** (`create_expense_full`, `update_expense`, `update_expense_fields`, the fixed-expense "pay" flow) — that's exactly midnight ART, chosen so the stored UTC date and the ART-displayed date are always the same calendar day, at every month/year boundary, regardless of whether a query adjusts for the `-3h` offset or reads `created_at` raw (the codebase does both, inconsistently, elsewhere). Don't "improve" this by storing a different time-of-day without re-verifying that invariant.
- **The IPC time-series API is unauthenticated and has no SLA.** `inflation.refresh()` catches every failure and leaves the cache as-is — report generation never blocks on it, and the dossier explicitly flags `inflation_unavailable` so the model doesn't narrate un-deflated numbers as if they were real. If real IPC data looks stale on the dashboard, check `apis.datos.gob.ar` directly before assuming a code bug.
- **`fixed_expense_year`/`month` vs. cash-basis reporting are two different partitions, on purpose.** The monthly report groups by the expense's own ART date (`dossier.py`, `db.get_expenses_for_period_art`), not by the fixed-expense period fields — otherwise fixed + variable wouldn't sum to the report's own total. Don't switch the report's queries to the `fixed_expense_year`/`month` columns to "simplify" — that's a different, deliberately separate concept (see the 2.1.0 changelog entry on why the two are independent).
- **Stored forecasts are immutable by design (7.14.0).** `report_forecasts` rows are insert-only — `db.save_report_forecast()` only ever `INSERT`s, and the `gastos_app` role isn't even granted `UPDATE` on the table at the DB level. Target-month actuals shown next to a stored forecast are computed read-only at display time (`forecast.actuals()`) and never rewrite the frozen row. Don't "fix" a forecast after the fact by updating it — regenerating the *report* that produced it does not touch forecasts from other reports either.
- **The shared accounting period resolves in a fixed precedence order (7.12.0):** explicit `?period=YYYY-MM` in the URL, then the unsigned `gastos_period` cookie, then the family's local current month (`dashboard.py`'s `_prepare_period_context()`). An invalid or out-of-range value in either the URL or the cookie is silently discarded, not surfaced as an error — don't assume a bad period value produces a visible failure.
- **Changing `families.default_currency` is a display/default-input switch only, never a conversion.** `db.set_family_default_currency()` does exactly one `UPDATE families SET default_currency = ...` — it does not touch `expenses`, `incomes`, `fixed_expenses`, `cambios_dolar` or historical `reports` rows, and the `/familia` success message says so explicitly. It changes which currency new implicit input defaults to and which currency is the primary block of *future* reports; it never rewrites or reclassifies anything already stored.
- **`FLASK_ENV=development` or `TESTING=1` bypass Turnstile verification and email sending in `auth.py`.** These are dev/test-only escape hatches, not documented `.env` variables — never set either on the Pi's `~/.env`, or login/OTP verification silently stops being enforced.
- **The Postgres `strftime()` compat function (`migrations/0001`) already applies the ART timezone shift internally** — it does `value AT TIME ZONE 'America/Argentina/Buenos_Aires'` before formatting. Call it directly on `created_at`; wrapping it in an extra `datetime(created_at, '-3 hours')` (a leftover from an earlier SQLite-style query) double-applies the shift and silently misfiles any expense from the first three hours of ART on the 1st of a month into the previous month (found and fixed in 7.7.0, in `get_expenses_for_period_art`/`get_expenses_excluding_period`/`get_months_with_data`). The native `date(timestamptz)` function is different — it has no built-in TZ conversion (it truncates in the session's `TimeZone`, which is UTC), so `date(datetime(created_at, '-3 hours'))` elsewhere in `db.py` (e.g. `get_first_expense_date`) is correct as-is and should NOT be "simplified" to match.

## Infrastructure Philosophy

The `docker-compose.yml` on the Pi (`/home/juanfino/docker-compose.yml`) is the **operational source of truth** — it is managed manually and may include services from multiple unrelated projects. The copy committed to this repo exists for **auditing and history only** and is not read directly by the Pi.

When an agent modifies `docker-compose.yml` as part of a PR, the relevant changes must be manually applied to the Pi's copy. The repo copy should then be updated to match. This rule is tool-independent — it applies to any agent's PR, not only Claude Code's.

The Pi is intended to host multiple independent projects. A single global compose file on the host is preferred over per-project compose files to keep service management centralized.

## Active Backlog

- Clean up / review `/api/weekly` endpoint — still a standalone, unused-by-the-UI route (`dashboard.py`'s own comment confirms it's not surfaced anywhere in the templates)
- Automate Pi deployments via Tailscale (when warranted)

## Workflow & Conventions

These conventions apply regardless of which AI tool is doing the work — this project is now worked on by more than one coding agent, not exclusively Claude Code. Where a detail is genuinely specific to one tool, it's called out explicitly rather than stated as a general rule.

- **Division of labor:** a design/architecture assistant drafts prompts and decisions → a coding agent implements them (historically Claude Code; other agents now also work on this repo) → Juampi handles git operations, deploy, and reporting
- `config.yaml` is the canonical version source; `CHANGELOG.md` is the canonical change record — both must be updated at the end of every deployable session, tool-independent (see CLAUDE.md → Versioning)
- Version bumps: patch (`1.x.x`) for bugfixes, minor (`x.x.0`) for features, major for breaking config/DB schema changes
- Multiple related changes consolidated into a single agent session/PR — avoid noisy PR chains
- Merging PRs is a manual step by design, regardless of which agent opened them
- Conversations with the assistant happen in Spanish; code and agent prompts/instructions are in English
- `docker-compose.yml` in this repo is audit-only — see **Infrastructure Philosophy** above; this is a repo-wide rule, not tied to any one agent
- `categorizer.categorize()` always returns `(category_id, subcategory_id)` — all expense creation flows must pass both
- **Claude Code specific:** `CLAUDE.md` is auto-loaded as project context only by Claude Code (see AGENTS.md's documentation map); other agents rely on `AGENTS.md`/`PROJECT.md` being read explicitly or supplied as context
