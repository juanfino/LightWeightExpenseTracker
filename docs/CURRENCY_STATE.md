# Currency Handling — Current State

Code-derived snapshot after the 7.15.0 currency-foundations work. This document
separates the generalized foundation that exists now from the deliberately binary
input/report layers that remain for later work.

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

### Known later constraint

`report_forecasts.currency` still has `ck_forecast_currency` limited to ARS/USD. The
forecast and dossier remain a fixed pair by explicit scope; migration `0013` does not
pretend that layer is generalized.

## 3. Family default

`families.currency` was renamed to `families.default_currency`. It means “assume this
currency when input omitted one,” not “the family's only currency.”

The web read path is live:

- the Dashboard currency selection initializes from it;
- add/edit expense forms use it when no currency is supplied;
- new fixed-expense and income forms use it;
- the corresponding POST validation defaults omitted currency from the family row.

There is intentionally no writer or settings UI yet. Every existing and newly created
family therefore keeps the database default ARS. A writer must wait until currency
detection, exchange operations and reporting are generalized, otherwise a BRL default
would produce a half-working application.

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

## 5. Input layer — deliberately still binary

The storage and web form catalogue accept four currencies, but automatic detection is
not generalized in this change:

- `parser.py` recognizes USD markers, otherwise ARS;
- the three tool schemas and prose in `intent.py` still enumerate ARS/USD;
- `audio.py` still extracts USD vs. ARS;
- OCR still starts at ARS and exposes the existing ARS/USD correction;
- `dolar.py` remains a USD-operation gate.

Consequently a Telegram message such as `Hotel 200 EUR` is not yet safe currency
detection: the deterministic path can still treat it as ARS and include `EUR` in the
concept. Users can record BRL/EUR through the generalized web forms; Telegram detection
is follow-up work.

## 6. Exchange operations — deliberately dollar-specific

`cambios_dolar` remains structurally ARS/USD-specific:

- columns are `monto_usd`, `cotizacion` (ARS per USD) and `monto_ars`;
- `tipo` is `venta`/`compra` by application convention;
- bot and web reads/writes still describe dollar purchases and sales.

It is not a generic from/to currency-pair table and was not changed in 7.15.0.

## 7. Dossier, reports and forecast — deliberately fixed pair

`dossier.py` still builds exactly `currencies.ARS` and `currencies.USD`. It preserves
parallel, same-shape blocks and never sums currencies; the reference-only
`equivalence` block remains USD→ARS using `cambios_dolar`. IPC applies only to ARS.

`resumenes.html` still renders the binary pair, `report.py` partitions only those two,
`forecast.py` forecasts them, `report_forecasts` constrains them, and report prompts
still describe `$`/`U$S`. These are known, explicit follow-ups, not accidental claims of
end-to-end N-currency reporting.

The dashboard's current “other currency” summary also remains an ARS/USD-era binary
presentation. Currency-scoped queries and storage accept the catalogue, but a dynamic
multi-block dashboard/report presentation is separate work.

## 8. Verification contract

Regression coverage asserts:

- seeded codes normalize and unknown codes fail;
- BRL amounts round-trip PostgreSQL as exact `Decimal`;
- migration `0013` applies from the previous head with existing values preserved;
- the five FKs reject an unknown code;
- family-default web forms and omitted POST currency use `default_currency`;
- server and client format USD with Argentine separators and `US$`;
- a synthetic zero-decimal currency rounds and formats correctly;
- existing unit tests and both PostgreSQL smoke tests run against a scratch database.
