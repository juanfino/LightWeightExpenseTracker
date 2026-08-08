# Currency Handling — Current-State Inventory

Factual, code-derived snapshot of how currency is stored, moved, formatted, and
hardcoded across the app today. Written as input to a design decision (generalizing
from ARS/USD to arbitrary N-currency support) — it does not change any behavior,
schema, or code. Where something couldn't be confirmed with certainty, it's marked
explicitly rather than inferred.

Scope: `gastos/app/*.py`, `gastos/app/templates/*.html`, `gastos/migrations/versions/*.py`.

---

## 1. Storage

### 1.1 Every table with a currency column

| Table | Column | Type | Default | Constraint | Nullable |
|---|---|---|---|---|---|
| `expenses` | `currency` | `TEXT` | `'ARS'` | `CHECK (currency IN ('ARS', 'USD'))` | `NOT NULL` |
| `fixed_expenses` | `currency` | `TEXT` | `'ARS'` | `CHECK (currency IN ('ARS', 'USD'))` | `NOT NULL` |
| `incomes` | `currency` | `TEXT` | `'ARS'` | `CHECK (currency IN ('ARS', 'USD'))`, named `ck_incomes_currency` | `NOT NULL` |
| `expense_classifications` | `currency` | `TEXT` | `'ARS'` | `CHECK (currency IN ('ARS', 'USD'))` | `NOT NULL` |
| `families` | `currency` | `TEXT` | `'ARS'` | `CHECK (currency IN ('ARS', 'USD'))` | `NOT NULL` |

All five constraints are defined identically: a free-text `TEXT` column with a
two-value `CHECK`, not a native PostgreSQL `ENUM` type. Source: `migrations/versions/
0001_initial_postgres.py` (`expenses`, `fixed_expenses`, `expense_classifications`),
`0002_tenancy.py` (`families.currency`), `0006_incomes.py` (`incomes.currency`).

`cambios_dolar` (the dollar-exchange table) has **no `currency` column at all** — see
§2, it's structurally ARS/USD-specific rather than currency-parameterized.

### 1.2 `families.currency` is a dead column

`families` has had a `currency TEXT NOT NULL DEFAULT 'ARS' CHECK (currency IN ('ARS',
'USD'))` column since `0002_tenancy.py` (the tenancy migration). Grepping the entire
`app/` tree for any `SELECT` that reads it, any Python code that references
`family["currency"]`/`f["currency"]`/`families.currency`, or any write to it beyond the
migration's own `server_default`, turns up **nothing** — no query in `db.py` selects
it (the only `SELECT ... FROM families` queries fetch `id`, `timezone`, or use
`EXISTS`), no code path sets it, and no UI exposes it. It appears to be a column that
was added anticipating a per-family currency preference that was never wired up to
anything. This is worth confirming with `git log -p` / `git blame` on that column if
its original intent matters for the design (not done as part of this inventory, since
it's a documentation/inventory task, not a git-archaeology one).

### 1.3 SQL type for monetary amounts, per table

| Table | Amount column(s) | Alembic type | Precision loss risk |
|---|---|---|---|
| `expenses` | `amount` | `NUMERIC(14, 2)` | None — fixed-point decimal |
| `fixed_expenses` | `estimated_amount` | `NUMERIC(14, 2)` | None |
| `cambios_dolar` | `monto_usd`, `cotizacion`, `monto_ars` | `NUMERIC(14, 2)` | None |
| `incomes` | `amount` | `NUMERIC(14, 2)` | None |
| `expense_classifications` | `amount` | `NUMERIC(14, 2)` | None |
| `ipc_series` | `value` | `NUMERIC(20, 12)` | None (not a monetary amount — CPI index value) |

All monetary columns use `NUMERIC(14, 2)` at the Postgres level — no `FLOAT`/`REAL`/
`DOUBLE PRECISION` for money anywhere in the live (Alembic-applied) schema, so no
binary-floating-point precision loss at the storage layer.

**However**, `pgcompat.py`'s `Row` class (the DB-API-compatibility wrapper every query
result passes through) does this on every row it constructs:

```python
self._values = tuple(float(value) if isinstance(value, Decimal) else value for value in values)
```

Every `NUMERIC` column — which `psycopg` would otherwise hand back as a Python
`Decimal` — is coerced to a Python `float` the moment it's read out of the database.
So while storage itself is exact, **every amount the application ever computes with
in Python (dossier aggregation, report classification payloads, dashboard totals,
Telegram message formatting) is IEEE-754 double-precision, not `Decimal`.** This is a
deliberate simplification (there's no `Decimal`-aware arithmetic anywhere in `db.py`,
`dossier.py`, `bot.py`, or `dashboard.py`), not an oversight caught in this pass — but
it does mean float rounding artifacts (e.g. `0.1 + 0.2`-style errors) are structurally
possible in any derived total, and would need to be considered if amount precision
requirements tighten for additional currencies (e.g. currencies with more than 2
decimal places, or very large magnitudes).

`SCHEMA` (the large triple-quoted SQL string near the top of `db.py`, lines ~44–146)
is **dead code** — it's a legacy SQLite-era schema definition (`INTEGER PRIMARY KEY
AUTOINCREMENT`, `REAL` amount columns, `DATETIME DEFAULT CURRENT_TIMESTAMP`,
`sqlite_master` references elsewhere in the file) that is never executed. The actual
schema is applied exclusively via Alembic (`db.init_db()` calls
`command.upgrade(alembic_cfg, "head")`); nothing in the codebase calls `conn.execute
(SCHEMA)` or similar. Anyone reading `SCHEMA` for "what type is `amount`" would be
misled into thinking it's `REAL` — it is `NUMERIC(14,2)` in the live database. Flagged
here since it's directly relevant to "what type are amounts" but not fixed, since
`SCHEMA`'s removal is a code change outside this session's documentation-only scope.

### 1.4 Exchange rate storage direction

`cambios_dolar.cotizacion` stores "pesos per one US dollar" — confirmed both by
`dolar.py`'s prompt (`"La 'cotizacion' es el precio en pesos de UN dólar"`) and by
`db.registrar_cambio()`'s insert, which also derives and stores `monto_ars` from
`monto_usd * cotizacion`. So the rate is unidirectional and ARS-denominated by
construction: there's no `from_currency`/`to_currency` pair, just an implicit
"USD → ARS" reading baked into the column semantics.

---

## 2. Exchange operations

### 2.1 Schema — `cambios_dolar`

```sql
CREATE TABLE cambios_dolar (
    id         SERIAL PRIMARY KEY,
    fecha      TIMESTAMPTZ NOT NULL,
    monto_usd  NUMERIC(14,2) NOT NULL,
    cotizacion NUMERIC(14,2) NOT NULL,
    monto_ars  NUMERIC(14,2) NOT NULL,
    usuario    TEXT NOT NULL,
    tipo       TEXT NOT NULL DEFAULT 'venta'
)
```
(`0001_initial_postgres.py`; `tipo` was added by a later `_migrate_cambios_tipo()`-style
change captured directly in the initial Postgres migration's column list — i.e. by the
time of the Postgres migration it was already part of the base table.)

This is **structurally dollar-specific, not a generic currency-pair table**: the column
names themselves (`monto_usd`, `monto_ars`) encode which two currencies are involved.
There is no `currency_from`/`currency_to` pair, no generic `amount_a`/`amount_b`, and no
`family`/installation-level currency-pair reference. Generalizing this table to a third
currency (e.g. EUR) would require either a schema change (new columns or a pivot to a
generic pair model) or treating every new currency as implicitly "vs. ARS" by adding
more `monto_<code>` columns — neither is a drop-in extension of the current shape.

### 2.2 Buy vs. sell direction

`tipo` is a free-text `TEXT` column (no `CHECK` constraint restricting it, unlike the
currency columns in §1.1), conventionally holding `'venta'` (sell — the family sold
USD, obtaining ARS) or `'compra'` (buy — the family bought USD, spending ARS). Default
is `'venta'`. Nothing in the schema itself enforces the two-value set; the constraint is
implicit in application code (`dolar.py`'s prompt tells the model to return exactly
`"venta"` or `"compra"`; `dashboard.py`'s buy/sell toggle UI presumably writes one of
the two — not independently re-verified against every write path as part of this
inventory).

### 2.3 Every read/write site

**Writes:**
- `db.registrar_cambio(fecha, monto_usd, cotizacion, usuario, tipo="venta")` —
  computes `monto_ars = monto_usd * cotizacion` and inserts. Called from:
  - `bot.py` — the legacy `CambioDolar <usd> <cotizacion>` command (always `tipo="venta"`)
  - `bot.py` — natural-language dollar operations via `dolar.parse_dolar()` (`pending_dolar` confirm/auto-save flow)
  - `dashboard.py` — `POST /api/cambios/add` (the web "+ Agregar cambio" form added in 7.10.0)
- `db.update_cambio(cambio_id, fecha, monto_usd, cotizacion, tipo=None)` — recomputes
  `monto_ars`, used by `dashboard.py`'s `PUT /api/cambios/<id>`.
- `db.delete_cambio(cambio_id)` — used by `dashboard.py`'s `DELETE /api/cambios/<id>`.

**Reads:**
- `db.get_cambios_resumen_mes(year, month)` — monthly summary, used by `dashboard.py`.
- `db.get_cambios_resumen_mes_by_tipo(year, month)` — same, split by `tipo`; consumed
  by `dossier.py`'s `_build_dollars()` and `_build_equivalence()`.
- `db.get_cambios_historial(limit=50)` — history list, `GET /api/cambios/historial`.
- `db.get_cambios_por_mes(months=12)` — monthly-aggregate series, `GET
  /api/cambios/por_mes`.
- `db.get_cambios_cotizacion_historica()` — full `(fecha, cotizacion)` series for the
  dashboard's historical-rate chart, `GET /api/cambios/cotizacion_historica`.
- `db.get_latest_cotizacion_upto(year, month, lookback_months=12)` — most recent rate
  at or before a period, used by `dossier.py`'s `_build_equivalence()` fallback chain
  (see §4.2).
- `db.get_cambios_for_period(year, month)` — used by `report.py`'s `fingerprint()` to
  include dollar operations in the report's local-facts hash.

---

## 3. Hardcoding

Grouped by file. "What would change for a third currency" is noted per group rather
than per line, since the pattern repeats.

### 3.1 Python — canonical currency list

- **`db.py`** (lines ~15–18): `SUPPORTED_CURRENCIES = ("ARS", "USD")`,
  `DEFAULT_CURRENCY = "ARS"`, and `normalize_currency(currency)` — the single
  choke-point that validates/normalizes a currency string, raising `ValueError` for
  anything outside `SUPPORTED_CURRENCIES`. This is the most centralized hardcoding
  point in the codebase and the natural place a third currency would first be added.
  Called from `dashboard.py` (multiple API routes) and `intent.py` (edit-expense
  currency changes).

### 3.2 Python — parsing / detection

- **`parser.py`**: `_USD_RE = re.compile(r"(?:(?<!\w)(?:usd|u\$s|us\$)(?!\w)|\bd[oó]lares?\b)", re.IGNORECASE)`
  is the only currency signal the fast-path parser looks for; anything not matching
  falls through to the hardcoded `"ARS"` default (`currency = "USD" if _USD_RE.search(text) else "ARS"`).
  A third currency needs its own regex and its own branch here — the current structure
  is binary (matches USD-pattern → USD, else → ARS), not a lookup over a currency list.
- **`dolar.py`**: `_DOLAR_RE = re.compile(r"\b(d[oó]lar(?:es)?|usd|u\$s|u\$d)\b", re.IGNORECASE)`
  is the cheap prefilter (`looks_like_dolar()`) gating the whole dollar-operation LLM
  call — structurally assumes exactly one non-ARS currency ("dollar operations"), not
  "currency-exchange operations in general."
- **`intent.py`**: tool-use JSON schemas hardcode `"currency": {"type": "string", "enum": ["ARS", "USD"]}`
  in three separate tool definitions — `log_income` (line ~59), `log_expense` (line
  ~102), and `edit_expense`'s nested `changes` object (line ~137). The system
  prompt text also hardcodes the rule in prose: `"Monedas: ARS es el valor por defecto.
  Si el mensaje dice USD, US$, U$S o dólares, usá USD."` and `"currency es ARS o USD"`
  in the SQL-schema description shown to the model for read-only reports. A third
  currency needs a new schema enum value in three places plus new prose in two, all in
  the same file.
- **`audio.py`**: the Whisper-priming prompt is hardcoded ARS/USD
  (`_WHISPER_PROMPT = "Gastos en pesos argentinos o dólares: ..."`), and the
  extraction prompt explicitly instructs `"detectá USD cuando diga 'dólares', 'USD',
  'US$' o 'U$S'; si no se aclara, usá ARS."` — same binary-default pattern as `parser.py`.
- **`ocr.py`**: no currency handling at all. Per `bot.py`'s own comment (line ~438,
  `data["currency"] = "ARS"  # OCR is deliberately ARS-first; the confirmation can
  correct it.`), every OCR-extracted expense is hardcoded to `ARS` regardless of what
  the receipt actually shows, on the assumption the user will correct it via the
  post-OCR confirmation flow if wrong. `ocr.py`'s own extraction prompt (not
  reproduced here — see the file directly) asks only for `{comercio, monto, fecha}`,
  no currency field.

### 3.3 Python — formatting / display

- **`bot.py`** (lines ~72–98): `fmt_amount(amount, currency="ARS")` is the single
  Telegram-side formatting choke point — `prefix = "U$S " if currency == "USD" else
  "$"`, then a hand-rolled thousands-dot/decimal-comma formatter (Argentine
  convention, not locale-driven — no `locale` module or `babel` usage anywhere in the
  codebase). There is also a **separate, redundant** `fmt_usd(amount)` function
  immediately below it (line ~85) that duplicates `fmt_amount(amount, "USD")`'s logic
  verbatim and produces an identical string; it does have one live caller (the dollar-
  operation confirmation message, line ~1843) rather than being fully dead, but the
  duplication itself — two functions computing the same formatted string — is not
  fixed here since it's a code change outside this session's scope, but worth flagging
  (see PR description). `_separate_totals()` (line ~96) hardcodes iterating `("ARS", "USD")` to build a
  per-currency total string.
- **`dashboard.py`**: `other_currency = "USD" if currency == "ARS" else "ARS"` appears
  twice (lines ~729, ~767) — a binary toggle, not an N-way selector, used wherever the
  UI needs "the other currency" (e.g. building a link/filter for the currency not
  currently shown).

### 3.4 SQL / migrations

- Every `CHECK (currency IN ('ARS', 'USD'))` constraint listed in §1.1 is a literal SQL
  hardcoding — adding a third currency means an `ALTER TABLE ... DROP CONSTRAINT` +
  `ADD CONSTRAINT` (or a full `ENUM` type migration) on five separate tables.
- `pgcompat.py`'s `_sql()` compatibility shim itself has no currency-specific logic —
  not a hardcoding site.

### 3.5 Jinja templates / inline JS

Every template below independently hardcodes the ARS/USD binary — there is no shared
JS formatting module (no static `.js` files in the project at all; every script is
inline in the Jinja templates).

| Template | Hardcoded pattern |
|---|---|
| `dolares.html` | `fmtPeso`/`fmtDolar`-style local helpers using literal `"$"` / `"U$S "` prefixes and `.toLocaleString("es-AR")`; buy/sell toggle relabels fields based on a hardcoded two-way branch |
| `fijos.html` | `const prefix = currency === 'USD' ? 'U\$S ' : '\$';` then `.toLocaleString("es-AR")` |
| `index.html` (dashboard) | Same `prefix = (currency \|\| activeCurrency) === 'USD' ? 'U\$S ' : '\$'` pattern |
| `history.html` | Same `prefix = currency === 'USD' ? 'U\$S ' : '\$'` pattern |
| `resumenes.html` | Same pattern in `fmtAmount(n, currency)`; additionally the entire rendering pipeline (`renderHero`, `renderKinds`, `normalizeDossier`) is structurally binary — e.g. `const hasUsd = !!(usd.base && usd.base.count)` singles out exactly one "other" currency to conditionally show alongside ARS, not an N-currency loop |
| `incomes.html` | Its own inline `money = (n,c) => (c==='USD' ? 'US\$ ' : '\$ ') + ...` — note this one uses the prefix `"US$ "` (space after US, no `$` between U and S), **inconsistent** with every other template's `"U$S "` — a pre-existing display inconsistency, not introduced or fixed here |
| `export.html` | One `USD` literal occurrence (dataset/label text, not re-verified in depth) |
| `superadmin.html` | Two `USD` occurrences — cost-assumption labels for the Anthropic/OpenAI unit-rate settings (these are actual API billing currency labels, arguably a different kind of "USD" than the app's transactional currency — not part of the expense-tracking currency model) |

### 3.6 Number formatting — locale vs. hardcoded

Every formatting site found (§3.3, §3.5) uses either a hand-rolled thousands-dot/
decimal-comma algorithm (`bot.py`) or `Number.prototype.toLocaleString("es-AR")`
(every template). Both are **hardcoded to Argentine convention** — no site derives
the separator/symbol/decimal-place convention from the currency itself (e.g. USD
would conventionally use `,`/`.`  in an `en-US` locale) or from any user/family
setting. `"es-AR"` is a literal string in every template that formats a number. There
is no per-currency or per-locale formatting configuration anywhere in the codebase.
Decimal places are always 2 when non-whole (`minimumFractionDigits: 2,
maximumFractionDigits: 2` / Python's `:,.2f`), never currency-dependent.

---

## 4. Report/dossier layer

### 4.1 Which currency blocks `dossier.py` builds

`build_dossier()` hardcodes exactly two blocks via a module-level constant:
`_CURRENCIES = ("ARS", "USD")` (`dossier.py`, line ~40), then builds
`dossier["currencies"]["ARS"]` and `dossier["currencies"]["USD"]` as two explicit,
separately-called `_build_currency_block(...)` invocations — **not** a loop over a
dynamically-derived set of currencies actually present in the data. A currency block
is always present for both ARS and USD even if a family has zero expenses in one of
them (e.g. `usd.base.count == 0`); the *page* (`resumenes.html`) is what conditionally
hides the USD half via `hasUsd = !!(usd.base && usd.base.count)`, not the dossier
itself. `apply_ipc=True` is hardcoded for the ARS call and `apply_ipc=False` for the
USD call — currency-to-IPC-applicability is a per-call boolean literal, not derived
from any per-currency metadata.

### 4.2 The `equivalence` rate-resolution chain

From `dossier._build_equivalence()`, in order, first match wins:

1. **This month's own sale rate** (`by_tipo["venta"]["cotizacion_promedio"]`) if the
   family recorded at least one `venta` (dollar sale) operation this period →
   `rate_source = "ventas_mes"`.
2. **This month's own purchase rate** (`by_tipo["compra"]["cotizacion_promedio"]`) if
   no sales but at least one `compra` (dollar purchase) this period →
   `rate_source = "compras_mes"`.
3. **The most recent recorded rate within the last 12 months**
   (`db.get_latest_cotizacion_upto(year, month, lookback_months=12)`), regardless of
   `tipo` → `rate_source = "mes_anterior"` (the source-string name is a slight
   misnomer — it's "most recent within 12 months," not necessarily last month; the
   `rate_period` returned alongside it reflects the actual period the rate came from).
4. **Unavailable** — `rate = None`, `available = False` — if none of the above found
   anything (no dollar operations ever, or none within the lookback window).

When available, `usd_total_in_ars = round(usd_total * rate, 2)`,
`combined_ars_equivalent = round(ars_total + usd_total_in_ars, 2)`, and
`usd_share_pct = round(usd_total_in_ars / combined * 100, 1)`. This value is
explicitly documented in code and in the LLM system prompt as reference-only — never
summed into a real total, contrast, or partition (see `docs/REPORT_PROMPTS.md` §3.1
for the prompt-level guarantee).

`dossier._build_dollars()`'s `coverage_ratio` has its own fallback: if `equivalence`
is available (or there's no USD spend to begin with), the denominator is the combined
ARS-equivalent total; if USD spend exists but no rate could be found, it falls back to
an ARS-only denominator rather than silently dropping USD spend from the picture — and
`coverage_basis` is set to a human-readable string describing which basis was used.

### 4.3 `inflation.py` — ARS-locked

`inflation.py` fetches and caches exactly one series: INDEC's national CPI
(`_SERIES_ID = "148.3_INIVELNAL_DICI_M_26"`), which is an Argentine-peso-denominated
index by definition — there is no concept of "which currency" in `inflation.py`'s own
code, it's implicitly ARS because IPC Nacional only makes sense for ARS. `deflate()`
takes a nominal amount and two periods and returns a real (inflation-adjusted) amount
in the target period's prices — it has no currency parameter at all.

For a non-ARS currency, `dossier.py` never calls `inflation.deflate()` at all (the
`apply_ipc=False` branch in `_build_contrasts()` skips straight to marking every
contrast entry `real_not_applicable: True`). So the answer to "what does a non-ARS
currency produce today" is: **nothing tries to inflation-adjust it** — it's a
structural skip, not a failed/degraded computation. A hypothetical third currency
would need the same treatment (no attempt at IPC deflation) unless a
currency-specific inflation index were introduced and wired into `inflation.py`
(which currently has no concept of "which series for which currency" — it's a single
hardcoded series).

---

## 5. Input layer

### 5.1 Telegram fast path (`parser.py`)

Binary regex match on `_USD_RE` (see §3.2) → `"USD"` if matched, else the hardcoded
default `"ARS"`. No LLM call, no currency list lookup — see §3.2 for the exact regex.

### 5.2 Natural-language intent layer (`intent.py`)

The model is given an explicit enum (`["ARS", "USD"]`) in the tool-use schema for
every currency-bearing field (`log_expense.currency`, `edit_expense`'s nested
`changes.currency`, and the currency field in the read-report tool's args), plus prose
instructions in the system prompt (`"ARS es el valor por defecto. Si el mensaje dice
USD, US$, U$S o dólares, usá USD."`). The model's returned currency value then passes
through `db.normalize_currency()` before being persisted (`bot.py` lines ~428, ~449;
`intent.py` line ~469) — so even if the model somehow returned something outside the
enum, `normalize_currency()` would raise `ValueError` rather than silently accept it
(not independently traced whether that `ValueError` is caught gracefully by the
calling `bot.py` handler or propagates as an unhandled exception — not confirmed as
part of this inventory).

### 5.3 Voice (`audio.py`)

The Claude extraction call is prompted to detect `"USD"` vs. default `"ARS"` from the
transcribed text using the same words as everywhere else (`"dólares", "USD", "US$",
"U$S"`); the returned value is then defensively re-normalized in code:
`"currency": "USD" if str(item.get("currency", "ARS")).upper() == "USD" else "ARS"`
(`audio.py` line ~113) — another explicit binary collapse, not a lookup against
`SUPPORTED_CURRENCIES`.

### 5.4 OCR (`ocr.py`)

No currency detection at all — every OCR-extracted expense is hardcoded to `"ARS"` by
`bot.py` before it's ever shown to the user for confirmation (see §3.2). If the
receipt is actually in a foreign currency, nothing in the OCR pipeline would surface
that — the user would need to notice and manually correct the currency in the
post-OCR confirmation flow (which does offer an ARS/USD toggle — see `bot.py` line
~459, `InlineKeyboardButton("U$S USD", callback_data="ocr:currency:USD")` — but again,
binary, not N-way).

### 5.5 The dollar-detection gate (`dolar.looks_like_dolar`)

Cheap keyword prefilter (`_DOLAR_RE`, see §3.2) that gates whether the (separate,
more expensive) dollar-exchange-operation LLM call runs at all, checked before the
normal expense-logging path in both `bot.py`'s `handle_message` (text) and
`handle_voice` (audio). It matches only dollar-related words — structurally, "is this
a dollar operation," not "is this a currency-exchange operation for any pair."

### 5.6 What happens if a user writes an amount in an unrecognized currency

Concretely, e.g. `"Hotel 200 EUR"` or `"Café 5 GBP"`:

- **Telegram fast path** (`parser.py`): `_USD_RE` doesn't match `"EUR"`/`"GBP"`, so the
  message is parsed as `{concept: "Hotel 200 Eur", amount: ..., currency: "ARS"}` (the
  currency word isn't recognized as a currency marker at all, so it's swallowed into
  the concept text rather than stripped — since only the USD regex is stripped from
  the text before concept extraction). **Silent misparse**: the amount is saved as ARS
  with no indication anything was off, and the literal currency code ends up as part
  of the expense's concept string.
- **Natural-language / voice paths**: the model is instructed to pick `ARS` or `USD`
  with no escape hatch — there's no `"unknown"`/`"other"` option in either schema. A
  model faced with "200 EUR" would most likely emit `ARS` (the stated default) since
  neither enum value is correct, but this is model behavior under prompt instructions,
  not a code-level guarantee — the exact behavior for an out-of-vocabulary currency
  was not (and cannot be, without an actual API call) verified as part of this
  static-inventory pass.
- **OCR**: always `ARS`, unconditionally, regardless of what the receipt shows (§3.2).
- **No path in the codebase can reject an expense for "currency not supported"** — the
  `normalize_currency()` `ValueError` only fires if a caller passes a currency string
  outside `("ARS", "USD")` explicitly; none of the input-detection code paths above
  ever produce such a string in the first place, since each of them independently
  collapses to one of the two known values before persistence.

---

## 6. Constraints relevant to a currencies reference table

### 6.1 RLS / FK patterns that would be affected

Every tenant-scoped table in this codebase carries `family_id NOT NULL` plus a forced
RLS policy of the shape `family_id = NULLIF(current_setting('app.family_id', true),
'')::integer` (see `migrations/versions/0002_tenancy.py`'s `DOMAIN_TABLES` loop, and
the equivalent per-table policy creation in `0006_incomes.py`, `0007_shopping_list.py`,
`0008_superadmin_panel.py`), plus composite foreign keys of the shape
`(family_id, referenced_id) → (family_id, id)` wherever one tenant table references
another (e.g. `fk_expenses_family_category`, `fk_expenses_family_fixed`) — this is the
pattern that "composite tenant foreign keys reject cross-family references" in
`CLAUDE.md`/`PROJECT.md` refers to.

A new `currencies` table would only need this treatment if it were **tenant-scoped**
(each family defines its own currency list). If a `currency` column on an existing
tenant table (`expenses`, `fixed_expenses`, `incomes`, `expense_classifications`)
were changed from a free-text `CHECK` to a foreign key into a `currencies` table, that
FK would need to be a composite `(family_id, currency_code) → (family_id, code)` FK
— matching the existing pattern for `category_id`/`subcategory_id` references — if
`currencies` is tenant-scoped, or a plain FK if it's global.

### 6.2 Existing precedent: global vs. tenant-scoped tables

The codebase's only genuinely **installation-level (global, no `family_id`)** business
table is `infrastructure_cost_settings` (`0008_superadmin_panel.py`) — manually
maintained, superadmin-only, no RLS `family_id` policy, accessed only via the
dedicated `gastos_superadmin` `BYPASSRLS` role. `families` and `users` are
platform-level tables that predate tenancy and aren't RLS-scoped to `family_id`
themselves (they define the tenant boundary, not use it) — a different category again.

Notably, **`ipc_series` — despite being genuinely global, shared economic data
identical for every family — was made tenant-scoped** in `0002_tenancy.py`: it has a
composite primary key `(family_id, year, month)` and the same per-family RLS policy as
every other domain table. This means every family currently caches its own duplicate
copy of the same national CPI series, refreshed independently by whichever family's
report generation happens to trigger `inflation.refresh()` first each cycle. This is
existing precedent that the "global vs. tenant-scoped" pattern in this codebase isn't
applied strictly by "is the data conceptually shared" — it's closer to "everything
defaults to tenant-scoped unless there's a specific reason (like superadmin-only
access) not to." A `currencies` reference table, if introduced, most closely resembles
`infrastructure_cost_settings` in spirit (a small, rarely-changing, conceptually
global list) but the `ipc_series` precedent means "shared data" doesn't automatically
guarantee it would be *built* as global — that would be a deliberate design choice to
make, not something the existing pattern decides for you.

### 6.3 Unique indexes / other constraints touching currency

No table has a unique index that includes a `currency` column (e.g. there's no
`UNIQUE (family_id, concept, currency)` anywhere) — currency is purely a descriptive/
filtering column today, never part of an identity constraint. This means introducing
a `currencies` reference table wouldn't need to touch any existing uniqueness
guarantee, only the `CHECK` constraints listed in §1.1 and (optionally) the FK
relationships described in §6.1.
