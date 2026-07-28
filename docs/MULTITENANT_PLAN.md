# LightWeightExpenseTracker — Multi-Tenant Migration Plan

**Status:** All planned phases complete
**Owner:** Juampi
**Target repo path:** `docs/MULTITENANT_PLAN.md`
**Last updated by:** Codex — 2026-07-28

---

## 0. How to use this document

This plan was executed by **multiple different AI coding agents** in sequential
sessions on the same repository. Phases 0–8 are complete; this document is now
the historical record and architectural rationale. `PROJECT.md` is the
canonical source for the current product state after this roadmap.

### Historical session protocol

**During roadmap execution, every session started by:**

1. Read this file in full.
2. Read `PROJECT.md`.
3. Read the **Status Ledger** (§1). No phase remains; new work is ordinary
   maintenance or a separately approved roadmap, not an implicit Phase 9.
4. Read the **Handoff Notes** of the previous phase.
5. Confirm the phase's scope with the user before writing code.

**For historical phase work, the end-of-session protocol was:**

1. Update the **Status Ledger** (§1) — phase status, date, agent name.
2. Fill in the phase's **Handoff Notes** — what was done, what was deliberately *not* done, anything surprising found in the code, and anything the next agent must know.
3. Update the **Screen Inventory** (§4) if any screen was added, removed, or materially changed.
4. Update `CHANGELOG.md`.
5. Bump the version in `gastos/config.yaml`.
6. Update `PROJECT.md` if modules, routes, env vars, DB tables, or bot commands changed.
7. Update the "Last updated by" line at the top of this file.

All phases now satisfy those closeout requirements. Future bugfixes should use
the normal repository process in `AGENTS.md`; they must not reopen or renumber
this plan unless the user explicitly creates a new roadmap.

### Concurrency rules

- **One phase at a time. One agent at a time.** Never run two agents on the same phase in parallel.
- One branch per phase: `feat/mt-p<N>-<slug>` (e.g. `feat/mt-p1-postgres`).
- **Always `git pull` locally before starting a session.** Agents build worktrees from local disk state, not from GitHub.
- The PR must be created and merged before the session is closed. Merging is manual, by Juampi.
- If an agent believes a phase's scope is wrong, it must say so and stop — not silently re-scope.

### Scope discipline

Each phase has an explicit **Out of scope** list. Those items are not oversights; they are deferred on purpose. An agent must not implement them "while it's in there." If an agent sees something that looks broken but is out of scope, it records it in the Handoff Notes and moves on.

---

## 1. Status Ledger

| # | Phase | Status | Branch | Agent | Date |
|---|---|---|---|---|---|
| 0 | Ground truth | DONE | `feat/mt-p0-ground-truth` | Claude Code (Sonnet 5) | 2026-07-24 |
| 1 | PostgreSQL + Alembic + async safety | DONE | `feat/mt-p1-postgres` | Codex | 2026-07-25 |
| 2 | Tenancy (single family, no auth yet) | DONE | `feat/mt-p2-tenancy` | Codex | 2026-07-25 |
| 3 | Identity & authentication | DONE | `feat/mt-p3-auth` | Codex | 2026-07-27 |
| 4 | Invitations, members, superadmin flag | DONE | `feat/mt-p4-family-members` | Codex | 2026-07-27 |
| 5 | Telegram linking + quotas | DONE | `feat/mt-p5-telegram-quotas` | Codex | 2026-07-27 |
| 6 | Self-service onboarding polish | DONE | `feat/mt-p6-onboarding` | Codex | 2026-07-27 |
| 7 | New features (incomes, shopping list, CSV export) | DONE | `feat/p7a-incomes`, `feat/p7b-shopping-list`, `feat/p7c-data-export` | Codex | 2026-07-28 |
| 8 | Superadmin panel | DONE | `feat/mt-p8-superadmin` | Codex | 2026-07-28 |

Status values: `NOT STARTED` / `IN PROGRESS` / `BLOCKED` / `DONE`.

**Fede (first external user) can be onboarded after Phase 6 is DONE. Not before.**

### Final outcome

The roadmap is fully delivered in production code through version **7.5.1**:

- Phases 0–2 established ground truth, PostgreSQL/Alembic and database-enforced
  tenant isolation.
- Phases 3–6 delivered application auth, family lifecycle, Telegram linking,
  quotas and self-service onboarding.
- Phase 7 delivered incomes, the shared shopping list and a complete portable
  export path.
- Phase 8 delivered cross-family operational visibility without weakening RLS.

There is no Phase 9 in this plan. The items in §8 are deliberately deferred
product ideas, not incomplete acceptance criteria.

---

## 2. Invariants

These rules apply to every phase from Phase 2 onward. They are not negotiable and every agent must verify its work against them.

### I1 — Tenant isolation is enforced at the database, not in application code

Every domain table carries `family_id NOT NULL` and has a PostgreSQL **Row-Level Security** policy filtering on the session variable `app.family_id`. Application code sets that variable once per request/message; it does not hand-write `WHERE family_id = ...` as its primary defense.

Rationale: the LLM writes SQL in `intent.py` / `sqlro.py`. A prompt instruction to include a filter is not a security control. RLS makes cross-family reads physically impossible regardless of what the model emits.

### I2 — Tenant resolution happens in exactly one place per entry point

- Web: one function, called by a `before_request` hook. Nothing downstream looks up `family_id` again.
- Bot: one resolution in the `handle_message` entry point, carried in context.
- Never re-derive `family_id` inside a handler, a query builder, or a template.

### I3 — Tables that do NOT carry `family_id`

`families`, `users`, `memberships`, `sessions`, `otp_codes`, `oauth_identities`, `invitations`, `telegram_link_tokens`, `infrastructure_cost_settings`, `alembic_version`.

Everything else — every domain table, present and future — carries it. Including `llm_calls`.

### I4 — Superadmin privilege is never writable over HTTP

The `is_superadmin` column on `users` is read by the app and **never written by any HTTP endpoint**. It is set exclusively by the `SUPERADMIN_EMAIL` bootstrap on startup. Any endpoint that accepts a dict of user fields must use an explicit allow-list that excludes it.

### I5 — Superadmin reads use a separate database role

RLS blocks cross-family reads by design. The superadmin panel needs cross-family reads. This is solved with a **dedicated PostgreSQL role with `BYPASSRLS`**, used only by superadmin queries. RLS policies are never weakened to make the panel work.

### I6 — The bot must never stall globally

Priority order, from best to worst acceptable outcome:
1. The bot never stalls.
2. If it stalls, it stalls for one user only.
3. If it stalls, it stalls for one family only.
4. **Never acceptable:** one family's activity degrades the bot for other families.

Enforced by: `concurrent_updates` enabled, all DB calls dispatched to a thread pool executor, separate connection pools for bot and web, `statement_timeout` set globally, and a per-family concurrency semaphore on LLM calls.

### I7 — Migrations are versioned

All schema changes go through Alembic. `seed.py` no longer performs schema migrations. Its remaining job is seeding **default taxonomy for a newly created family**, which runs at family creation, not at process startup.

### I8 — No feature ships without a tenant isolation test

Any new table or query added from Phase 2 onward must be covered by the isolation test suite (§3).

### I9 — Cloudflare Access stayed ON until Phase 3 was verified

Phase 3 built the application-owned authentication behind Cloudflare Access,
verified it in production, and only then removed the `expenses` Access
application. The Cloudflare Tunnel and Turnstile remain active. Every private
route is now protected by the application's deny-by-default session middleware;
there was no window in which the site was exposed without authentication.

### I10 — Money and dates

Amounts are `NUMERIC(14,2)`, never floats. Timestamps are stored as UTC (`TIMESTAMPTZ`). Display timezone comes from `families.timezone` (default `America/Argentina/Buenos_Aires`), never from a hardcoded constant.

---

## 3. Tenant isolation test suite

Introduced in Phase 2. This is a **hard gate**: Phase 2 is not DONE without it passing.

The suite:

1. Creates two families (A and B), each with users, categories, subcategories, keywords, expenses, fixed expenses, dollar operations.
2. For every read path in the application, asserts that a request/message in the context of family A returns **zero** rows belonging to family B. Paths covered:
   - Every dashboard route and `/api/*` endpoint.
   - Every function in `db.py` that reads.
   - `sqlro.py` executing SQL, including deliberately hostile SQL: `SELECT * FROM expenses` with no filter, `... WHERE family_id = 2`, `... WHERE 1=1`, cross joins, subqueries against other tables, CTEs.
   - The full `intent.py` path with a report question.
   - The summary/"Resúmenes" feature's context assembly.
   - CSV export endpoints (Phase 7).
3. For every write path, asserts a user in family A cannot create, edit, or delete a row belonging to family B — including by passing an explicit ID from family B.
4. Asserts a Telegram user may only edit **their own** expenses (existing rule, must survive the migration).

Any agent adding a table or a query from Phase 2 onward extends this suite in the same PR.

---

## 4. Screen Inventory

> Initially verified in Phase 0 and updated through Phase 8. The application now
> has the original seven product pages plus auth/legal/family/linking surfaces,
> the three Phase 7 pages and the Phase 8 superadmin panel. `PROJECT.md` carries
> the canonical current route inventory.

### Current (verified)

| Route | Template | Purpose | Status |
|---|---|---|---|
| `/` | `landing.html` | Public product landing: explains the first-expense workflow, family sharing and tenant privacy; authenticated users continue to `/dashboard` | complete |
| `/login`, `/registro` | `login.html`, `register.html` | Google OAuth or six-digit email OTP; registration creates a family | verified in production |
| `/privacy`, `/terms` | `privacy.html`, `terms.html` | Public legal pages for OAuth publication; Spanish aliases remain available | public; Google OAuth published |
| `/dashboard` | `index.html` | Dashboard: self-dismissing onboarding checklist, designed empty states, month total, KPI strip, Top 3, charts, income totals and per-member filter | complete |
| `/history` | `history.html` | Movements list — **nav label is "Movimientos"** (route/template name unchanged since the rename); filters (concept, month/year, category/subcategory, fixed/variable, user), inline edit, "Agregar gasto" modal (user + date fields, currency selector, subcategory picker) | verified |
| `/fijos` | `fijos.html` | Fixed expenses: CRUD, monthly paid/pending status, register-payment modal, "ya lo pagué" candidate search | verified |
| `/dolares` | `dolares.html` | USD/ARS operations: history, monthly summary, historical-rate chart | verified |
| `/resumenes`, `/resumenes/<period>` | `resumenes.html` | Monthly AI-generated report (2.3.0) — uses `claude-opus-4-8` by default (`REPORT_ANTHROPIC_MODEL`), separate from the Haiku model used elsewhere; see PROJECT.md → Monthly AI report for the full prompt/cost breakdown | verified |
| `/settings` | `settings.html` | Categories / subcategories / keywords CRUD | verified |
| `/ingresos` | `incomes.html` | Tenant-scoped ARS/USD income history, filters, CRUD and independent taxonomy | Phase 7a complete |
| `/lista` | `shopping.html` | Shared shopping list grouped by category, pending/recent-bought flows and nav count | Phase 7b complete |
| `/exportar` | `export.html` | Individual portable CSVs plus complete ZIP exit path | Phase 7c complete |
| `/config` | `config.html` | Backup status + "Backup ahora"; restore is SSH-only | verified |
| `/unirme/<token>` | `join_family.html` | Public single-use invitation acceptance through Google or email OTP | Phase 4 |
| `/familia` | `family.html` | Members, invitations, rename, logical removal, ownership transfer, leave/delete | Phase 4 |
| `/vincular-telegram` | `telegram_link.html` | One-tap Telegram deep link + QR + live connected state | Phase 5 |
| `/superadmin` | `superadmin.html` | Cross-family activity, expense/LLM metrics, operating costs, quota overrides and recent errors | Phase 8 complete; popup fix 7.5.1 |

### To be added

No screens remain in the current plan.

### To be modified

No screen changes remain in the current plan. The cross-currency net balance
originally sketched for Phase 7 was deliberately rejected in favor of separate
ARS/USD income totals; restore was removed from HTTP entirely in Phase 1.

---

## 5. Final data model

### Platform tables (no `family_id`)

```
families(id, name, timezone default 'America/Argentina/Buenos_Aires',
         currency default 'ARS', created_at, created_by_user_id)

users(id, email UNIQUE, name, is_superadmin bool default false,
      telegram_id UNIQUE NULL, color, created_at, last_login_at)

memberships(id, user_id, family_id, role 'owner'|'member', active,
            deactivated_at, created_at,
            UNIQUE(user_id) WHERE active)
```

> The partial `UNIQUE(user_id) WHERE active` index enforces one active family
> per person while retaining historical membership rows referenced by expenses.
> Future simultaneous multi-family support remains a matter of dropping this
> index and adding context selection. See §8 for why it is deferred.

```
oauth_identities(id, user_id, provider 'google', provider_user_id, created_at)
otp_codes(id, email, code_hash, expires_at, attempts, consumed_at, created_at)
sessions(id, user_id, token_hash, expires_at, created_at, user_agent, ip)
invitations(id, family_id, token_hash, role, created_by_user_id,
            expires_at, consumed_at, consumed_by_user_id, revoked_at, created_at)
telegram_link_tokens(id, user_id, token_hash, expires_at, consumed_at, created_at)

infrastructure_cost_settings(provider PK, unit_label, unit_rate_usd,
                             monthly_volume, notes, updated_at)
```

### Domain tables (all carry `family_id NOT NULL` + RLS)

Existing: `categories`, `subcategories`, `keywords`, `expenses`, `fixed_expenses`, `cambios_dolar`, `ipc_series`, `reports`, `expense_classifications`

Added by the roadmap: `llm_calls`, `incomes`, `income_categories`,
`shopping_items`, `family_quota_overrides`, `system_errors`

Phase 8 adds tenant-scoped `family_quota_overrides` and `system_errors`.
Global `infrastructure_cost_settings` is administrative installation metadata,
not family domain data, and is accessible only through `gastos_superadmin`.

```
family_quota_overrides(family_id PK, routine_daily_limit NULL,
                       summary_monthly_limit NULL, updated_at)

system_errors(id, family_id, user_id NULL, source, error_type,
              message, details NULL, created_at)
```

```
llm_calls(id, family_id, user_id NULL, created_at, module,
          model, tokens_in, tokens_out, cost_usd_estimate,
          latency_ms, success bool, error_text NULL)
```

`module` values: `intent`, `ocr`, `audio_whisper`, `audio_extract`, `dolar`, `resumen`.

### Data ownership rule

Expenses, incomes and all domain rows belong to the **family**, not to the user who created them. Removing a member does **not** delete their rows — the rows keep the user reference and the member's name remains visible. `users` rows are never hard-deleted while referenced; deleting a family cascades all its domain data.

---

## Phase 0 — Ground truth

**Goal:** every later phase stands on an accurate map. No behavior changes except one safety fix.

### Scope

1. **Resync `PROJECT.md` with the actual repository.** Every route, template, module, bot command, env var, DB table and column. Special attention to the undocumented "Resúmenes" feature and the "Movimientos" screen.
2. **Fill in §4 of this document** (Screen Inventory → Current) with the verified reality.
3. **Produce `docs/SQL_INVENTORY.md`:** every raw SQL statement in the codebase, listed as `file → function → tables touched → read|write`. This becomes the checklist for Phase 2, and the estimate for Phase 1.
4. **Document the Resúmenes feature precisely:** which model, what exactly gets put in the prompt, how much data, how it's triggered, roughly what it costs per call.
5. **Disable the Telegram database backup broadcast.** Today `backup.py` sends the entire `gastos.db` to every configured user daily at 21:00. With more than one family in the database that is a daily data leak to every user. Replace with a local-only dump for now; proper backup lands in Phase 1.

### Out of scope

Any refactor. Any schema change. Any new feature.

### Acceptance criteria

- A fresh reader of `PROJECT.md` can describe every screen and bot command correctly.
- `docs/SQL_INVENTORY.md` exists and is complete.
- No process sends the database file to any Telegram chat.

### Handoff Notes

**Done:**
- `PROJECT.md` re-verified against the live code (routes in `dashboard.py`, bot commands in `bot.py`, schema in `db.py`, env vars in `main.py`). It was already close to accurate — the only real staleness found was the version number (said 2.4.0, code was at 2.5.2) and the `/history` route's table not mentioning the "Movimientos" nav-label rename (2.5.1). Fixed both, plus expanded the Monthly AI report section with the exact per-call payload shape and a cost estimate.
- §4 Screen Inventory in this document replaced with verified reality: 7 web pages, all routes/templates confirmed by grep against `dashboard.py`. The "Resúmenes undocumented" and "six vs seven screens" concerns in the old provisional table turned out to be already resolved in `PROJECT.md` before this session — only the nav-label note was missing.
- `docs/SQL_INVENTORY.md` created: every function with raw SQL in `db.py` (87 functions) and `sqlro.py` (3), with tables touched, read/write, and porting notes (SQLite date functions, f-string dynamic SQL, `PRAGMA table_info` migrations, upserts). Extraction was done by a sub-agent and spot-checked against source (3 functions + all of `sqlro.py`) before trusting it — all checks matched exactly.
- Resúmenes feature documented precisely in `PROJECT.md`: exact JSON payload for both Claude calls (`classify_expenses` and `analyze`), model/effort/max_tokens per call, and a **cost estimate** (not a measurement — `response.usage` isn't logged anywhere yet) of ~$0.10–0.20 per generation at Opus 4.8 pricing.
- Telegram DB backup broadcast disabled. `backup.py`'s `send_db_backup()` (sent the file to every configured user via `bot.send_document`) replaced with `create_local_backup()`: copies `gastos.db` into `<DB_PATH dir>/backups/gastos_<UTC timestamp>.db`, prunes anything older than 7 days on every run. Applies to both the 21:00 ART scheduled job (`main.py`) and the manual `/admin/backup-now` endpoint (`dashboard.py`) — confirmed with Juampi that both should stop using Telegram, not just the automatic one. `LAST_BACKUP_PATH`/`/api/backup-status` behavior is unchanged (still tracks the last successful backup timestamp, now meaning "last local dump" instead of "last Telegram send"). User-visible copy updated in `config.html` (status pill + the "sends automatically" description) since it explicitly said "por Telegram" before. Verified locally with a throwaway temp DB: first backup created, an artificially-aged file got pruned on the next run, no exceptions.

**Deliberately not done (out of scope for Phase 0, per its own rules):**
- No retention *configuration* was added (7 days is hardcoded in `backup.py`'s `RETENTION_DAYS`) — proper backup strategy (off-device, R2, tested restore) is explicitly Phase 1 scope.
- Did not touch `gastos/DOCS.md` — grepped it for "backup" and found zero mentions, so no update was needed there (it never described the Telegram-send behavior to end users).

**Nothing surprising found** — `PROJECT.md` was in noticeably better shape than the plan's own §4 assumed (written before someone had apparently already resynced it, or the "six screens" note was simply stale by the time this session ran). The SQL inventory didn't turn up anything alarming for Phase 1 either — the SQLite-specific surface (date functions, `mode=ro` guardrails in `sqlro.py`) is exactly where the plan already expected the risk to concentrate.

**For the next agent (Phase 1 — PostgreSQL + Alembic + async safety):**
- Start from `docs/SQL_INVENTORY.md` — it's the checklist this phase's scope item 2–4 already anticipates. The "SQLite-specific date functions" section (~25 of 87 functions) is the biggest single porting surface; `sqlro.py`'s guardrails need a *different* mechanism entirely in Postgres (read-only role + `statement_timeout`), not a line-by-line translation.
- `backup.py` no longer touches Telegram at all — when Phase 1 adds real off-device backups (`pg_dump` to R2), it can either extend `backup.py` or replace it; there's no Telegram coupling left to unwind.
- One pre-existing oddity noticed in passing, not touched: the repo has two git remotes (`origin` and `LightWeightExpenseTracker`) pointing at the same URL; the second one's remote-tracking ref was stale (23 commits behind) at session start. Harmless, but worth knowing if `git status` ever looks confusing again.

---

## Phase 1 — PostgreSQL + Alembic + async safety

**Goal:** same application, same single user, different database engine, with the concurrency foundations in place.

### Scope

1. **Add a `postgres` service** to the Pi's `docker-compose.yml` (manually applied on the Pi; repo copy updated to match — see Infrastructure Philosophy in `PROJECT.md`). Volume at `~/postgres-data`. `shared_buffers` ~256MB is plenty at this scale.
2. **Introduce Alembic.** Initial migration creates the full schema. SQLAlchemy is added as a dependency for Alembic only — the application keeps its raw-SQL style in `db.py`.
3. **Port `db.py` from `sqlite3` to `psycopg` (v3).** Known translation points:
   - Placeholders `?` → `%s`
   - `AUTOINCREMENT` → `GENERATED BY DEFAULT AS IDENTITY`
   - `INSERT OR IGNORE` → `ON CONFLICT DO NOTHING`
   - `strftime(...)` → `to_char(...)` / `date_trunc(...)`
   - `REAL` money columns → `NUMERIC(14,2)`
   - Timestamps → `TIMESTAMPTZ`, stored UTC
   - Boolean columns stored as integers → real `boolean`
4. **Port `sqlro.py`** guardrails to Postgres: `SELECT`/`WITH` only, single statement, a genuinely read-only connection (read-only transaction + a role with no write grants), `statement_timeout`, row cap.
5. **Rewrite the SQL dialect instructions in `intent.py`'s prompt.** It currently teaches the model SQLite syntax. Date functions in particular will silently produce wrong results, not errors. This is the highest-risk item in the phase.
6. **Data migration script:** SQLite → Postgres, preserving IDs, with a verification step (row counts and checksums per table).
7. **Bot concurrency foundations** (invariant I6):
   - Enable `concurrent_updates` in `python-telegram-bot`.
   - Route every DB call from bot handlers through a thread pool executor so nothing blocking touches the event loop.
   - Separate connection pools for bot and dashboard.
   - Global `statement_timeout`.
8. **Real backups:** daily `pg_dump`, compressed, uploaded off-device (Cloudflare R2 is effectively free at this scale). Restore procedure documented in `docs/RUNBOOK.md`.

### Out of scope

Multi-tenancy. Auth. Any UI change. Converting the app to async.

### Acceptance criteria

- Every screen and every bot command behaves identically to before, verified against the Phase 0 inventory.
- Every report question that worked before still returns the same numbers.
- Row counts match between the old SQLite file and Postgres for every table.
- `alembic upgrade head` on an empty database produces the correct schema.
- A backup exists off-device and a restore has been performed at least once into a scratch database.

### Handoff Notes

Completed on `feat/mt-p1-postgres` plus the production cutover. PostgreSQL 17 Compose service, Alembic
schema, psycopg pools, dialect port, read-only reporting role, SQLite migration
script, PostgreSQL CI and smoke tests are implemented. A real copy of the Pi's
SQLite data migrated with matching row counts and SHA-256 checksums. The R2
bucket has a 90-day lifecycle; a dump was uploaded, downloaded and restored
into `r2_restore_check` on 2026-07-25. The bad first credential was revoked
only after the replacement passed that restore test. Web restore was removed;
restore is SSH-only (`docs/RUNBOOK.md`). PR #61 was merged and production was
cut over on 2026-07-25. The final
SQLite snapshot (290 expenses) is preserved both on the Pi and in R2. The
production R2 dump was restored into a scratch database with matching counts;
dashboard returned 200, Telegram `getMe` passed, and both production
containers are healthy. Temporary resources were removed.

---

## Phase 2 — Tenancy (single family, no auth yet)

**Goal:** the database is multi-tenant and provably isolated, while Juampi is still the only tenant and Cloudflare Access is still the front door. This is the phase where mistakes are cheap — take advantage of that.

### Scope

1. **Alembic migration:** create `families`, `users`, `memberships`; add `family_id` to every domain table; create `llm_calls`.
2. **Backfill:** create family 1 ("Familia Finochietto"), migrate `USERS_JSON` into `users` + `memberships`, set `family_id = 1` everywhere.
3. **Enable RLS** on every domain table with a policy on `current_setting('app.family_id')`. Create the application role (RLS applies) and the superadmin role (`BYPASSRLS`).
4. **Tenant resolution in exactly one place per entry point** (invariant I2). Web: `before_request`. Bot: `handle_message` entry.
5. **Split `seed.py`:** schema migration responsibilities move to Alembic; the taxonomy seed becomes `create_family_defaults(family_id)`, called at family creation.
6. **Define the base taxonomy** for new families — generic categories, subcategories and keywords. **Do not export Juampi's learned keywords**; they are family-specific by design. Base list in §9.
7. **Instrument every LLM call** into `llm_calls` (record only; enforcement comes in Phase 5).
8. **Build the tenant isolation test suite** (§3) and make it pass.

### Out of scope

Login, registration, invitations, any UI for families. `USERS_JSON` still drives who the bot recognizes.

### Acceptance criteria

- The isolation test suite passes, including the hostile-SQL cases.
- A second test family can be created and populated with no code changes.
- Every existing feature still works for family 1.
- `llm_calls` accumulates rows during normal usage with plausible cost estimates.

### Handoff Notes

Completed through PRs
[#63](https://github.com/juanfino/LightWeightExpenseTracker/pull/63) and
[#64](https://github.com/juanfino/LightWeightExpenseTracker/pull/64), merged on
2026-07-25 and deployed manually to the Pi by Juampi.

**Done:**
- Alembic creates `families`, `memberships` and `llm_calls`, backfills “Familia
  Finochietto”, and assigns every existing domain row to family 1.
- Forced RLS covers all domain data and tenant-safe platform reads. Composite tenant
  foreign keys reject cross-family references. Application, read-only and dedicated
  `BYPASSRLS` superadmin roles are separated.
- Request/update context uses `ContextVar` plus transaction-local settings, preventing
  pooled connections from retaining another tenant.
- `seed.py` now only creates generic per-family taxonomy, excluding learned keywords.
- Intent, OCR, Whisper, audio extraction, dollar parsing and both Resúmenes calls write
  best-effort telemetry to `llm_calls`.
- Full local suite passed against disposable PostgreSQL 17: 15 tests, including two
  families, hostile SQL, cross-family reads/writes/references and forced-RLS coverage.
- GitHub Actions on the final `main` merge passed both Tests and Docker Publish. The
  red check attached to PR #64 itself was only a transient Docker Hub timeout before
  tests started; the post-merge `main` run completed successfully.
- Production verification by Juampi: Alembic `0002` deployed, dashboard and Telegram
  smoke-tested, PostgreSQL timestamp rendering fix verified, and a real AI flow
  successfully wrote tenant-scoped `llm_calls` telemetry.

**Deliberately not done:** auth, registration, invitations, family UI or quotas.
`USERS_JSON` and Cloudflare Access remain in place, as scoped.

**For the next agent (Phase 3):** Cloudflare Access is still enabled and `USERS_JSON`
still identifies Telegram users, intentionally. Build and verify application auth behind
Access before disabling it as the final Phase 3 step.

---

## Phase 3 — Identity & authentication

**Goal:** own login, built and verified *behind* Cloudflare Access.

### Scope

1. **Sessions:** server-side sessions table, secure httpOnly cookies, CSRF protection on all mutating endpoints.
2. **Google OAuth** (Authlib), scopes `email` and `profile` only.
3. **Email OTP** — six digits, 10-minute expiry, single use, max 5 attempts, hashed at rest. Chosen over clickable magic links because Gmail's in-app browser opens links in a context that does not share cookies with the tab the user started in, stranding them mid-flow.
4. **Resend integration** for transactional email. Verify `juampifinochietto.com` via DNS records in Cloudflare. Free tier covers 3,000 emails/month.
5. **Cloudflare Turnstile** on registration and OTP request — invisible in the common case, free, unlimited.
6. **Rate limiting** per IP and per email on registration and OTP endpoints. Turnstile stops bots; rate limiting stops a real person hammering the button and burning the email quota. Both are needed.
7. **Registration flow:** create user → create family → seed default taxonomy → land on dashboard.
8. **Authorization on every endpoint.** Audit every route: authenticated + family-scoped by default; explicit allow-list for public routes. Note that `/config`'s restore endpoint is currently unauthenticated behind Access — see Open Decision D1.
9. **Public pages required for Google OAuth verification:** landing page, privacy policy, terms of service. Without these Google shows an unverified-app warning screen, which is fatal to the self-service goal. Basic scopes do not require Google's heavy security review, but the app must be published and self-certified.
10. **Turn Cloudflare Access off** — last step of the phase, only after everything above is verified working while Access is still on.

### Out of scope

Invitations. Members management. Apple Sign-In (see §8).

### Acceptance criteria

- A brand-new account can be created via Google and via email OTP, in an incognito window, from a phone.
- Every endpoint returns 401/403 when unauthenticated. Verified by an automated test that enumerates routes.
- Google's OAuth consent screen shows no unverified-app warning.
- Cloudflare Access is disabled and the app is still secure.

### Handoff Notes

**Phase 3 completed in production on 2026-07-27. Phase 4 can start from
`main`.**

- Alembic `0003` adds `sessions`, `otp_codes` and `oauth_identities`, makes
  Telegram optional for web-created users, and adds `users.last_login_at`.
- Authenticated sessions use opaque 48-byte tokens; only SHA-256 hashes are
  stored. Cookies are Secure/HttpOnly/SameSite=Lax. CSRF is tied to the
  server-side session and automatically attached to all existing mutating
  `fetch` calls.
- `dashboard.py` resolves the browser session, user and family once in
  `before_request`; every private route is deny-by-default. Public routes are
  an explicit allow-list. Platform identity queries select
  `gastos_superadmin` with `SET LOCAL ROLE` so privilege cannot leak through a
  pooled connection.
- Email OTP uses Resend, six digits, 10-minute expiry, single use, five
  attempts, hashed at rest. Resend's sender domain was verified through
  Cloudflare DNS. Google uses Authlib with the basic identity scopes
  `openid`, `email` and `profile`; the consent app is External/In production.
  Version 5.0.1 adds `prompt=select_account` because production testing showed
  that Google otherwise silently reused the browser's active account. Both
  registration paths use Turnstile and in-process per-IP/email rate limits.
- Registration creates user → family → owner membership → generic taxonomy.
  `AUTH_BOOTSTRAP_EMAIL` attaches the existing family-1 owner to web auth only
  when its email is still NULL.
- Added landing, login, registration, OTP, privacy and terms templates, plus
  the authenticated user/family menu and logout.
- Final disposable PostgreSQL 17 verification: migration `0003`, 22 tests,
  schema smoke and 33 web smoke routes passed. Tests
  cover route enumeration, unauthenticated denial, CSRF, hashed/revoked
  sessions, OTP limits/single use, and full email registration through seeded
  family creation, Turnstile's canonical `siteverify` call and Google's
  account-selection prompt.
- The GitHub UI exposes one `postgres` check, but that job intentionally
  contains the whole gate: unit/integration tests, PostgreSQL schema smoke and
  web route smoke. Its first Phase 3 run caught a test-only coupling:
  registration expected `TESTING=1` to bypass Turnstile, while CI did not set
  it. The registration test now mocks Turnstile explicitly and the separate
  Turnstile contract test remains intact.
- Production was deployed to the Raspberry Pi with all Phase 3 environment
  variables. Google login, application logout/private-route behavior and the
  anonymous entry flow were exercised. Seeing Cloudflare Access in incognito
  before cutover was expected: it proved the temporary guard was still in
  place.
- After verification, only the Cloudflare Zero Trust Access application named
  `expenses` was deleted. DNS, Cloudflare Tunnel and Turnstile were deliberately
  retained. Anonymous visitors now reach the application's own landing/login.
- Final shipped version is 5.0.1. PR #66 delivered Phase 3 and its CI fix; PR
  #67 delivered the forced Google account selector. Both were merged before
  this closeout.

**Deliberately deferred to later phases:** invitations, family/member
management, Telegram account linking, quotas, onboarding polish, Apple
Sign-In and the superadmin UI. The in-process rate limiter is sufficient for
the current single-instance Pi deployment; a distributed limiter would only
be needed if the web tier becomes multi-process or multi-instance.

---

## Phase 4 — Invitations, members, superadmin flag

**Goal:** the family owner can bring their partner in without Juampi being involved.

### Scope

1. **Invitation links.** Owner clicks "Invitar a alguien" → the app generates a signed token URL (`/unirme/<token>`, 7-day expiry, single use) → owner copies it and sends it via WhatsApp. No email infrastructure needed for this path.
2. **`/unirme/<token>` landing:** shows "Te invitaron a unirte a **Familia X**" with Google / email options. On completion the user joins the existing family and never sees the create-family screen.
3. **Edge case — user already belongs to a family:** clear error, "Ya pertenecés a *Familia Y*. Tenés que salir de ahí primero." The partial unique index on active memberships is the database backstop.
4. **`/familia` screen:** members list with roles, generate/copy/revoke invitation link, remove a member, rename the family, transfer ownership, leave family, delete family (with cascade and a typed confirmation).
5. **Removing a member does not delete their data** — their expenses stay with the family and their name remains visible.
6. **Superadmin flag:** `users.is_superadmin`, never writable over HTTP (invariant I4), bootstrapped from `SUPERADMIN_EMAIL` on startup.

### Out of scope

Email-delivered invitations (link-copy is enough). Multi-family membership. The superadmin panel itself.

### Acceptance criteria

- Two accounts in one family, created entirely through the UI, with no manual database work.
- An invitation link cannot be reused, cannot be used after expiry, and cannot be used by someone already in a family.
- Removing a member preserves their expenses.
- No HTTP request can set `is_superadmin`, verified by a test that attempts it directly.

### Handoff Notes

**Implemented on `feat/mt-p4-family-members` (version 6.0.0):**

- Alembic `0004` adds `users.is_superadmin`, the `invitations` platform
  table, invitation-aware OTPs and historical membership state. The original
  `UNIQUE(user_id)` became a partial unique index over active memberships:
  removed members retain the exact `(family_id, user_id)` row referenced by
  their expenses but may later create or join one new active family.
- `/familia` is available to every active member. Only the owner can create
  or revoke seven-day, one-use invitation links, rename the family, remove a
  member, transfer ownership or delete the family. Members can leave. Owner
  removal is blocked; ownership must be transferred first. Destructive family
  deletion requires the exact, case-sensitive family name and cascades domain
  data.
- `/unirme/<token>` is public and supports Google or six-digit email OTP.
  Invitations always grant `member`; expired, revoked, consumed, or
  already-affiliated accounts are rejected. Token hashes, never raw tokens,
  are stored. Raw links are displayed only immediately after creation.
- Logical removal revokes the member's web sessions and makes Telegram
  resolution ignore that user, while all historical expenses and the user's
  visible name remain intact. Startup `USERS_JSON` synchronization never
  reactivates a historical membership.
- `SUPERADMIN_EMAIL` is now required at startup. It is the sole source of
  `is_superadmin`; HTTP inputs use explicit action handlers and cannot mutate
  the flag.
- Legacy Telegram entries may carry an optional `email`. The sync fills only a
  NULL email matched by `telegram_id`, refuses conflicts/overwrites, and avoids
  duplicate web users. For production, add Cele's confirmed email to her
  existing `USERS_JSON` entry before deploying 6.0.0; she is already an active
  `member` of Familia Finochietto and then signs in normally rather than
  consuming an invitation.
- Verification used a disposable PostgreSQL 17 container: 30 unit/integration
  tests passed, including end-to-end UI email invitation acceptance, single
  use/expiry/revocation, active-family conflict, historical expense retention,
  ownership transfer, exact-name deletion, non-owner denial and attempted
  HTTP superadmin mutation. PostgreSQL and all-route web smoke tests also
  passed.
- Production cutover completed on 2026-07-27 after PR #69 merged. The Pi is
  running migration `0004`; the `gastos` container is healthy with zero
  restarts; authenticated `/familia` requests return 200. Database checks
  confirmed exactly one superadmin, exactly one user for Cele's confirmed
  email and exactly one active membership for that identity.
- The first Phase 4 restart exposed a configuration mismatch:
  `SUPERADMIN_EMAIL` did not equal the email already attached to the owner, so
  `_bootstrap_superadmin()` correctly failed closed. The resulting container
  restart loop left port 8090 unavailable and Cloudflare showed a 502 Host
  Error. Recovery was to make `SUPERADMIN_EMAIL` exactly equal to the existing
  `AUTH_BOOTSTRAP_EMAIL` and recreate the `gastos` container. This is now
  documented in `docs/RUNBOOK.md`.

**Deliberately not done:** Telegram self-service linking, quotas, email-sent
invitations, simultaneous multi-family membership and the superadmin panel;
these remain in later phases.

**For Phase 5:** active membership is now part of both web-session and
Telegram identity resolution. Telegram linking should create/link identity
without bypassing that rule, and an unlinked or inactive chat must receive the
friendly linking response defined by Phase 5.

---

## Phase 5 — Telegram linking + quotas

**Goal:** connecting Telegram requires no explanation and no understanding of what a bot is.

### Scope

1. **Deep link flow.** The web shows a button "Conectar mi Telegram" pointing at `https://t.me/<bot>?start=<token>`. Tapping it opens Telegram (app if installed, web if not) directly in the bot chat with a single **INICIAR** button. Tapping that sends `/start <token>`. The user never types or sees a code.
2. **QR code** beside the button for desktop users.
3. **Live confirmation:** the web page polls and flips to "✅ Telegram conectado" on its own. No refresh instruction.
4. **Bot welcome message** after linking: a short greeting plus one concrete thing to try — `Supermercado 15000`.
5. **Unlinked chats:** any message from an unknown `chat_id` gets a friendly reply explaining how to link, with the dashboard URL. Never silence, never a stack trace.
6. **Group chats:** politely rejected with an explanation. Supporting group chats properly is a separate future feature.
7. **Unlinking** from `/familia`.
8. **Per-family LLM quotas**, enforced against `llm_calls`:
   - Daily quota for routine calls (intent, OCR, voice) — generous, e.g. 200/day. No legitimate user will ever reach it.
   - Separate quota for Resúmenes (Opus/Sonnet): 15 generations/family/month, with the remaining balance shown before generation (D3).
   - Friendly Spanish message when exhausted, telling the user when it resets.
9. **Per-family concurrency semaphore on LLM calls** (invariant I6). Whisper and summary calls take seconds; without a per-family cap one family can occupy every worker.
10. **Error reporting:** unhandled exceptions log `family_id` and `user_id`, and send a message with the traceback to Juampi's Telegram. Cheap, and it turns "che, no me anda" into something diagnosable.

### Out of scope

Group chat support. Multiple Telegram accounts per user.

### Acceptance criteria

- A person who has never used a Telegram bot can go from the dashboard to a logged expense with no verbal instructions.
- Messages from unlinked chats always get a helpful reply.
- Exceeding a quota produces a clear message and does not break anything.
- With one family deliberately saturating LLM calls, another family's messages are answered normally. This must be tested, not assumed.

### Handoff Notes

**Implemented on `feat/mt-p5-telegram-quotas` (version 7.0.0):**

- Alembic `0005` adds platform table `telegram_link_tokens`. Only token hashes
  are stored; links expire after 15 minutes, are single-use, and issuing a new
  link invalidates older pending links for that user.
- `/vincular-telegram` renders a `t.me/<bot>?start=<token>` button and local QR,
  then polls `/api/telegram-link/status` every two seconds. `/start <token>`
  atomically links the private chat and sends a welcome plus
  `Supermercado 15000`. `/familia` shows connected state and supports unlinking.
- Unknown or inactive chats always receive the dashboard linking URL. Group
  messages stop before all other handlers and receive a private-chat
  explanation. Multiple Telegram accounts per user and group support remain
  deliberately out of scope.
- `llm_limits.py` enforces 100 routine calls per family/day, 15 report
  generations per family/month, and two concurrent LLM calls per family.
  Calendar boundaries use `families.timezone`; direct deterministic expense
  entry remains available after routine quota exhaustion. Resúmenes shows the
  remaining monthly balance before generation.
- All Anthropic and OpenAI call sites use the shared per-family semaphore.
  Unhandled bot and web exceptions log `family_id`/`user_id` and send the
  traceback to the Telegram account linked to the sole superadmin.
- Verification used a disposable PostgreSQL 17 container. Clean migrations
  through `0005` succeeded and all 34 unit/integration/isolation tests passed,
  including single-use/conflicting Telegram identities, quota exhaustion and
  deliberate saturation of one family while another entered immediately.

**Production requirement:** add `TELEGRAM_BOT_USERNAME` (without `@`) to
`~/.env` before deploying 7.0.0. `PUBLIC_DASHBOARD_URL` is optional and defaults
to the current production URL.

**Deliberately not done:** Telegram group support, multiple Telegram accounts
per user, simultaneous multi-family membership, email-sent invitations and the
superadmin panel.

**For Phase 6:** Telegram linking is now a complete optional onboarding step.
The Phase 6 checklist can query the existing linked state; it should not create
a second linking flow.

---

## Phase 6 — Self-service onboarding polish

**Goal:** Fede receives a link and nothing else. **This is the phase that satisfies the project's primary requirement.**

### Scope

1. **Designed empty states for every screen.** A brand-new family currently sees empty charts, "$0" and empty tables — which reads as *broken*, not as *new*. Each screen needs a short sentence explaining what goes there and a button that does the obvious next thing.
2. **Onboarding checklist card** at the top of the dashboard, disappearing on its own when complete:
   - ✓ Crear cuenta
   - Cargar tu primer gasto
   - Conectar Telegram (opcional)
   - Invitar a alguien a tu grupo
3. **Public landing page** for logged-out visitors: what the app does, how to start.
4. **Contextual help** where concepts are non-obvious: what a keyword does, what a fixed expense is, what the Resúmenes feature costs.
5. **Full mobile pass.** Fede will mostly be on a phone.
6. **End-to-end walkthrough as a new user**, in an incognito window, on a phone, with no prior knowledge. Every point of hesitation gets fixed.

### Out of scope

A category-selection wizard (deferred — base taxonomy is enough for now).

### Acceptance criteria

- A person unfamiliar with the app reaches a logged first expense without asking a single question.
- No screen ever looks broken when empty.

### Handoff Notes

**Implemented on `feat/mt-p6-onboarding` (version 7.1.0):**

- The dashboard now derives a four-step onboarding checklist from real tenant
  state: account exists, at least one expense exists, the current member linked
  Telegram, and the family has either another active member or an invitation.
  It disappears automatically when every step is complete. Telegram remains
  explicitly optional and reuses the Phase 5 linking screen.
- The first-expense action opens the existing web expense form directly.
  Dashboard charts and the Movements, Fixed expenses, Dollars, Summaries and
  Family screens now explain empty data and provide the relevant next action.
- The public landing explains the first expense path, shared family view and
  tenant privacy boundary. Contextual help explains categorization words,
  recurring expenses and the 15-per-month AI summary allowance.
- A 390x844 mobile walkthrough covered every private page. There is no
  horizontal overflow; the user header no longer collides with the mobile
  menu; the first-expense deep link was fixed after the walkthrough exposed
  that URL filter synchronization removed its intent too early.
- Disposable PostgreSQL 17 verification passed all 36
  unit/integration/isolation tests, including new checks for checklist content,
  self-dismissal and landing guidance.

**Deliberately not done:** no taxonomy wizard, second Telegram linking flow,
group chat support or Phase 7 feature work.

**For Phase 7:** onboarding is complete and Fede can be invited after the
remaining production-readiness checklist below is confirmed by Juampi.

---

## 🎯 Fede onboards here

Before sending the link:

- [x] Off-device backups verified with an actual restore
- [x] Isolation test suite green
- [ ] Error alerts arriving at Juampi's Telegram
- [x] Quotas active
- [ ] Privacy policy and terms published
- [ ] An honest conversation: this runs on a Raspberry Pi in my living room; if the power goes out it goes down; treat it as a beta

This checklist is an **operational launch checklist**, not unfinished roadmap
scope. Privacy/terms routes and error-alert code exist; unchecked items mean
real-world delivery/communication was not independently confirmed in the phase
handoffs.

---

## Phase 7 — New features

Three independent features. They can be split across three sessions or three agents, in any order, but each is a separate PR.

### 7a — Incomes

```
income_categories(id, family_id, name, icon, color)
incomes(id, family_id, user_id, concept, amount NUMERIC(14,2),
        currency, income_category_id NULL, date, created_at)
```

- Own taxonomy, separate from expense categories. Base list: Sueldo, Freelance / Honorarios, Alquiler, Venta, Reintegro, Intereses / Inversiones, Regalo, Otros.
- `/ingresos` screen: list, filters, add/edit/delete — mirrors the movements screen.
- Dashboard: monthly income totals in ARS and USD, shown separately. A cross-currency balance was deliberately rejected because it is misleading for a family paid in USD that spends mostly in ARS; a future cash-flow view may incorporate recorded currency exchanges.
- Bot: a `log_income` tool in the intent layer. The plain `concepto monto` fast path stays expense-only — it must not become ambiguous. A prefix (`Ingreso: sueldo 1500000`) serves as the fast path for income.
- CSV export included.

**Out of scope:** recurring incomes (the monthly salary analogue of fixed expenses). Note it as a future feature.

### 7b — Shopping list

```
shopping_items(id, family_id, name, quantity TEXT NULL,
               category_id NULL,
               status 'pending'|'bought',
               created_by_user_id, created_at,
               bought_by_user_id NULL, bought_at NULL)
```

- **Bot tools:** `add_shopping_item(name, quantity?)`, `mark_shopping_item_bought(name)`, `list_shopping_items()`.
- **Category inference reuses `categorizer.py`** — the same keyword table already maps "detergente" → Supermercado and "banana" → Verdulería. No new inference logic, and the list improves as the family's keywords improve.
- **Disambiguation rule, to be stated explicitly in the tool descriptions:** a message with an amount is an expense; a message about something missing or needed, with no amount, is a shopping list item. `"falta detergente"` → list. `"detergente 5000"` → expense. `"compré el detergente"` → mark bought.
- `_needs_intent` must escalate messages containing: falta, faltan, comprar, necesito, necesitamos, anotá/anota, agregá/agrega a la lista, lista.
- **`/lista` screen:** items **grouped by category**, which is the actual value — the list arrives sorted by aisle/shop. Checkbox to mark bought, inline add, "limpiar comprados", pending-count badge in the nav.
- Shared across the family; both members see the same list.
- Bought items are hidden from the main view but retained 30 days for quick re-adding.

**Out of scope:** recurring items, units and quantities as structured data, price estimates, automatic linking to expenses.

### 7c — CSV export

- Endpoints per dataset: movimientos, dólares, ingresos, lista de compras, taxonomía (categories + subcategories + keywords). Plus a "descargar todo" ZIP.
- **RFC 4180: comma-separated, quoted fields, CRLF.** UTF-8 **with BOM** so Excel renders accents and eñes correctly. ISO 8601 dates. Decimal point, no thousands separator.
- A one-line hint on the export screen for users importing into Spanish-locale Excel (use "Datos → Desde texto/CSV" and pick comma).
- Every export query is family-scoped and covered by the isolation test suite. This is exactly the kind of endpoint where a slip leaks everything.
- The taxonomy is included even though it seems low-value: it makes the export a **real exit path**. Together with "delete my family and all its data," it is what makes trusting Juampi with the data reasonable.

### Handoff Notes

**7a implemented on `feat/p7a-incomes` (version 7.2.0):**

- Independent income taxonomy and income rows are tenant-scoped with forced RLS and composite family foreign keys.
- `/ingresos` provides filters, add/edit/delete and full category administration; members may mutate only their own income rows.
- The Dashboard shows monthly ARS and USD income totals separately. It intentionally does not invent a cross-currency balance.
- Telegram keeps plain `concept amount` expense-only, adds the deterministic `Ingreso:` prefix and supports unmistakable natural-language income phrases through `log_income`.

**7b implemented on `feat/p7b-shopping-list` (version 7.3.1):**

- Shared `/lista` groups pending items by the existing taxonomy, supports free-text quantities and retains bought items for 30 days with re-add.
- Duplicate pending names are normalized and consolidated; every family member can operate the shared list.
- Telegram tools add, complete and list items with the explicit amount-versus-missing-item rule.
- Version 7.3.1 fixes idempotent income-category seeding when the migration already populated the base taxonomy.

**7c implemented on `feat/p7c-data-export` (version 7.4.0):**

- `/exportar` offers individual RFC 4180, UTF-8 BOM CSVs for movements, fixed expenses, dollars, incomes, shopping and taxonomy, plus a dated ZIP.
- Timestamps are ISO 8601 in the family's timezone; member names are included without email or Telegram identifiers.
- Formula-like cells are neutralized. Every read remains under RLS and an adversarial test asserts another family's rows never enter an export.

---

## Phase 8 — Superadmin panel

**Goal:** Juampi can see what the system is doing and what it costs.

### Scope

- Uses the `BYPASSRLS` role (invariant I5) — RLS policies are never weakened.
- Metrics: families, users per family, active users (last 7 / 30 days), expenses logged per family.
- LLM usage: calls and estimated cost per family, per module, per model, over time. Enough to answer "who is expensive and why."
- Infrastructure cost summary: Anthropic + OpenAI + Resend + R2, manually entered rates against measured volume.
- Quota status per family, with a manual override.
- Recent errors.

### Out of scope

Impersonation. Editing another family's data. Billing.

### Handoff Notes

**Implemented on `feat/mt-p8-superadmin` (version 7.5.0), with popup clipping
fixed in `fix/account-menu-clipping` (version 7.5.1):**

- `/superadmin` is visible only to `users.is_superadmin` and reads cross-family
  metrics exclusively with the existing `gastos_superadmin` `BYPASSRLS` role.
  It covers families/users/activity, expense volume, LLM calls and estimated
  cost per family/module/model/day, operating-cost assumptions and recent
  web/Telegram/LLM failures.
- `family_quota_overrides` keeps optional routine/report limits under forced
  RLS; `llm_limits.py` reads them in the current family context and falls back
  to 100 daily routine calls and 15 monthly summaries.
- Unhandled tenant-attributed web and bot exceptions are persisted in
  `system_errors` as well as logged/notified. The panel does not impersonate
  users, edit another family's business data or implement billing.
- The primary header now contains only daily product areas. Dashboard is
  reached exclusively through the logo; the avatar popup groups Family,
  Categories, Telegram, Export, System, Superadmin and logout. At 390 px the
  header and popup have no horizontal overflow.
- Verification: clean migrations through `0008`, 51 unit/integration/isolation
  tests, schema smoke, all-route web smoke and desktop/mobile browser QA.

**Deliberately not done:** impersonation, business-data editing and billing,
matching the phase's explicit out-of-scope list.

**Additional scope delivered with Phase 8:**

- Navigation was simplified even though it was not part of the original panel
  bullet list: Dashboard moved to the logo-only entry point and account/admin
  actions moved into the avatar popup.
- Web and Telegram exceptions gained durable tenant-scoped storage in
  `system_errors`; the original scope only required a recent-errors view.
- Infrastructure assumptions are explicitly separated from measured
  `llm_calls` cost so manual figures cannot silently replace telemetry.
- Formula-safe exports, duplicate-shopping consolidation and the deterministic
  `Ingreso:` fast path are Phase 7 hardening delivered beyond the terse
  high-level feature bullets.

---

## 8. Deferred decisions, and why

| Deferred | Why | Reopen when |
|---|---|---|
| **Multi-family membership** | Manageable on the web (family switcher in the header), but unsolvable cleanly on the bot: a `chat_id` is a person, not a context. Asking "which family?" kills the fast path; a sticky active family fails *silently* — set once, forgotten, three weeks of expenses in the wrong family. And the intent layer injects recent expenses into the prompt, so an ambiguous family means ambiguous context, which is a leak risk rather than a UX annoyance. The partial unique index over active memberships means reopening this is dropping an index, not reshaping the schema. | A concrete case appears: separated parents, an accountant, a shared house |
| **`tenant_id` instead of `family_id`** | In this app the tenant *is* the family, one to one, with no possible distinction. The table is `families`; a `tenant_id` column pointing at `families.id` forces a permanent mental translation, and the UI says "grupo familiar" everywhere. Because resolution is encapsulated in one place (I2), renaming later is an Alembic `ALTER` plus a find-and-replace. | The tenant boundary stops being a family — e.g. an organization containing several families, or one family with two separate spaces (home and a small business) |
| **Apple Sign-In** | Requires the Apple Developer Program (USD 99/yr), a client secret that is a JWT you sign yourself and that expires every 6 months (so rotation must be automated), plus "Hide My Email" edge cases. Not justified for a user who has already said yes. | Real demand from users who won't use Google or email |
| **Onboarding wizard for taxonomy** | Base list is enough. A wizard is real design work. | Multiple families complain the defaults don't fit |
| **Recurring incomes** | Fixed-expense analogue; not needed for the first income use case | Someone asks |
| **Telegram group chat support** | Genuinely nice, genuinely a separate feature | After Phase 6 |
| **Automated Pi deploys via Tailscale** | Manual deploy is fine at this frequency | Deploy frequency justifies it |

---

## 9. Base taxonomy for new families

Finalized in Phase 2. It is generic, Argentina-oriented and deliberately small
— families extend it themselves, and `add_keyword` learns per family.

**Expense categories (with representative subcategories):**

- Hogar — Supermercado, Verdulería, Carnicería, Limpieza, Servicios
- Transporte — Nafta, Transporte público, Estacionamiento, Mantenimiento
- Salud — Farmacia, Consultas, Obra social
- Educación — Cuotas, Materiales
- Ocio — Salidas, Streaming, Viajes
- Cuidado personal — Peluquería, Cosmética
- Ropa
- Mascotas
- Impuestos y servicios
- Gastos generales

**Keywords:** a small generic set only (supermercado, nafta, farmacia, …). **Juampi's learned keywords are not exported** — categorization is family-specific by design. A haircut is Cuidado Personal in one household and Gastos Generales in another.

**Income categories:** Sueldo, Freelance / Honorarios, Alquiler, Venta, Reintegro, Intereses / Inversiones, Regalo, Otros.

---

## 10. Recorded decisions

| # | Decision | Status |
|---|---|---|
| D1 | **Restore endpoint.** Removed from the web; SSH-only `pg_restore`, with scratch restore first. | Decided in Phase 1 |
| D2 | **Income taxonomy** — own `income_categories` table, separate from expenses | Decided and implemented in Phase 7a |
| D3 | **Resúmenes quota** — 15/family/month, with remaining balance shown before generation | Decided in Phase 5 |
| D4 | **Off-device backup target** — private Cloudflare R2 bucket, 90-day lifecycle | Decided in Phase 1 |

---

## 11. Timeline (historical)

The roadmap ran from 2026-07-24 through 2026-07-28. Phases 0–2 carried the main
technical risk because the PostgreSQL port touched every query and RLS had to
contain both handwritten and model-generated SQL. `docs/SQL_INVENTORY.md`
turned that surface into a concrete checklist.

Phases 3–6 then delivered identity, family lifecycle, Telegram linking and the
self-service experience. Phase 6 satisfied the primary “no tengo que explicar
nada” requirement. Phases 7–8 completed the new product surfaces and
operational administration. Version 7.5.1 is the final roadmap release.
