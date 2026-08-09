# Currency Handling — Current State

Code-derived snapshot after the 7.17.0 end-to-end N-currency work.

## 1. Reference data and validation

`currencies` is global installation-level reference data. It has no `family_id`, tenant
RLS policy or CRUD UI. It is the same class of curated data as `quotes` and
`infrastructure_cost_settings`; unlike `ipc_series`, it is not duplicated per family.

| code | symbol | decimal_places |
|---|---|---:|
| ARS | `$` | 2 |
| USD | `US$` | 2 |
| BRL | `R$` | 2 |
| EUR | `€` | 2 |

Those are the only seeded rows. Adding another supported currency is an insert rather
than a schema or application-code change. `code` is the primary key and must be
uppercase; `decimal_places` is constrained to 0–6.

`db.refresh_currencies()` loads the catalogue after Alembic runs.
`SUPPORTED_CURRENCIES`, `get_currencies()`, `get_currency()` and
`normalize_currency()` all derive from that loaded data; there is no Python literal
tuple of accepted codes. Validation defaults legacy callers to ARS, normalizes case and
whitespace, accepts the four seeded codes, and rejects unknown codes.

All seeded currencies are available to every family. There is no enabled-currencies
join or per-family switch.

## 2. Storage and integrity

Migration `0013_currency_foundations` replaces the historical binary check on each of
these columns with a plain foreign key to `currencies(code)`:

| table | column | FK |
|---|---|---|
| `expenses` | `currency` | `fk_expenses_currency` |
| `fixed_expenses` | `currency` | `fk_fixed_expenses_currency` |
| `incomes` | `currency` | `fk_incomes_currency` |
| `expense_classifications` | `currency` | `fk_expense_classifications_currency` |
| `families` | `default_currency` | `fk_families_default_currency` |

A simple FK is intentional: `currencies` is global, so the composite
`(family_id, referenced_id)` pattern used for tenant-owned references is neither
needed nor correct here. Existing ARS/USD values remain unchanged during the migration,
and PostgreSQL still rejects an unknown currency even if application validation is
bypassed.

All monetary storage remains `NUMERIC(14,2)`. psycopg returns `Decimal`; writes pass
through `money.amount()` and its `ROUND_HALF_UP` cents policy. The currency display
precision is separate metadata: it can correctly render a zero-decimal currency even
though the current four rows all specify two display places. Supporting a future
currency requiring more than two stored fractional digits would additionally require a
storage-precision migration.

Migration `0015` replaces `report_forecasts.currency`'s historical ARS/USD check with
`fk_report_forecasts_currency`, so persisted forecasts accept every catalogue row.

## 3. Family default

`families.currency` was renamed to `families.default_currency`. It means “assume this
currency when input omitted one,” not “the family's only currency.”

The web read path is live:

- the Dashboard currency selection initializes from it;
- add/edit expense forms use it when no currency is supplied;
- new fixed-expense and income forms use it;
- the corresponding POST validation defaults omitted currency from the family row.

The owner can select any catalogue currency from `/familia`. Members can see the value
but cannot change it. The UI and success message state explicitly that this is not a
conversion: existing expenses, incomes, fixed expenses and append-only reports keep
their stored currencies. The new default applies to future implicit input and becomes
the primary block of future reports.

## 4. Formatting

Formatting has one implementation per runtime:

- `money.format_amount()` is the server/Telegram path. It stays on `Decimal`, quantizes
  with the shared `ROUND_HALF_UP` policy and never converts display money through
  binary float.
- `static/money.js` is the browser path. `base.html` configures it once with the
  catalogue, `default_currency` and reader locale; templates no longer carry their own
  `toLocaleString("es-AR")` helpers.

The inputs are three independent axes:

1. symbol comes from the currency row;
2. maximum fractional digits come from `decimal_places`;
3. thousands and decimal separators come from the reader.

The reader convention remains Rioplatense Spanish (`es-AR`) globally. Thus USD 5580.50
renders as `US$ 5.580,50`, not with US separators. A future family/user locale has one
server profile and one base-template configuration point to replace.

Whole amounts keep the existing compact display without forced `,00`. Fractional
amounts show the currency's declared precision. The intended visible normalization is
that every USD surface, including Telegram and the dashboard hero, now uses `US$`
instead of `U$S`.

## 5. Input layer

`currency_detection.py` is the shared cheap detector. It reads codes and symbols from
the loaded catalogue and centralizes colloquial aliases. Text, voice and the intent tool
schemas all accept the current catalogue; adding a catalogue row makes the new code
available without editing those schemas. Explicit ISO codes and specific symbols win.
Ambiguous `$` and generic “pesos” resolve to `families.default_currency`, as does an
omitted currency, on every surface.

`parser.py` strips the detected marker, so `Hotel 200 EUR` becomes concept `Hotel`,
amount EUR 200. An unknown all-caps currency suffix such as `Hotel 200 XYZ` raises a
visible correction instead of becoming concept text. The exchange prefilter additionally
requires an exchange verb or arrow; an ordinary foreign-currency expense does not spend
an LLM call. OCR remains default-first, but its correction keyboard is built from every
catalogue row.

## 6. Exchange operations

Migration `0014` keeps the legacy `cambios_dolar` table name while replacing its payload:

| column | meaning |
|---|---|
| `amount_given` / `currency_given` | money the family handed over |
| `amount_received` / `currency_received` | money the family obtained |
| `rate_received_per_given` | units received for one unit given |

Both currency columns reference `currencies(code)`, the currencies must differ and all
three numeric values must be positive. Historical sales migrate as USD→ARS; purchases as
ARS→USD. Their old amount sides remain exact and their familiar ARS-per-USD rate remains
recoverable unchanged. Stored `tipo`, `monto_usd`, `monto_ars` and `cotizacion` are gone.

Buy/sell is display-only: giving a non-default currency and receiving the default is a
sale; the reverse is a purchase. A conversion such as BRL→EUR has no buy/sell label. The
historical-rate endpoint requires a pair and direction, preventing unrelated series from
being plotted together.

## 7. Dossier, reports and forecast

`dossier.py` derives its ordered currency set from currencies used by expenses in the
period plus `families.default_currency`; the default is always first and preserves a
coherent empty-period shape. `report.py` partitions exactly that set and `forecast.py`
forecasts it. Every block remains same-shape and currency-scoped.

`inflation.has_series(code)` is the explicit capability boundary. The module still
acquires exactly one series—Argentina's national CPI for ARS. ARS can expose real
figures (or `real_unavailable` when observations are missing); all other currencies
expose `real_not_applicable` and are never silently narrated as inflation-adjusted.

`equivalence.items` values every non-default period currency in the family default.
For each direct pair, the fallback is current-period foreign→default, current-period
default→foreign, latest operation in either direction within 12 months, then explicit
unavailable. Rates come only from the family's own operations; there is no indirect
path and no external FX source. These values are reference-only and never feed totals,
contrasts, partitions or forecasts. Legacy ARS/USD aliases remain in newly generated
dossiers so numeric regression comparisons retain their 7.7–7.16 contract.

`resumenes.html` renders the default currency as the expanded primary report and puts
additional active currencies in collapsed detail panels containing their hero,
partition, forecast, equivalence and registration coverage. This avoids a wall at four
currencies. Client normalization handles all three persisted shapes: pre-7.7
single-currency, 7.7–7.16 fixed ARS/USD, and 7.17+ dynamic N-currency.

The main dashboard's summary remains a separate presentation and is not changed here.

## 8. Verification contract

Regression coverage asserts:

- seeded codes normalize and unknown codes fail;
- BRL amounts round-trip PostgreSQL as exact `Decimal`;
- migrations `0013` and `0014` preserve existing currency and exchange values;
- the seven currency FKs reject an unknown code;
- family-default web forms and omitted POST currency use `default_currency`;
- server and client format USD with Argentine separators and `US$`;
- a synthetic zero-decimal currency rounds and formats correctly;
- EUR/default/unknown-marker detection agrees across the shared input contract;
- BRL→EUR round-trips as exact `Decimal`, has no buy/sell label and stays out of USD→ARS charts;
- existing unit tests, the exchange-migration smoke and both PostgreSQL smokes run against a scratch database.
