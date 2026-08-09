# Monthly Report — Prompt & Rendering Inventory

Factual, code-derived description of the two LLM calls behind `/resumenes`, the
per-family compilation layer for the narrative call, and how the page turns their output
into UI. It traces `report_ai.py`, `report_preferences.py`, `report.py`, `dossier.py`,
`forecast.py` and `templates/resumenes.html`, including the deterministic forecast that
may be supplied to narration.

---

## 1. The two calls

Both live in `report_ai.py`, are orchestrated by `report.py`'s `generate_report()`, and
talk only to the Anthropic API — neither does DB, Telegram or Flask I/O.

| | Call 1 — `classify_expenses()` | Call 2 — `analyze()` |
|---|---|---|
| Purpose | Label each variable (non-fixed) expense `recurring` or `exceptional`, per currency | Narrate the full dossier into headline/summary/findings/questions |
| Model | `report_ai.model_name()` → `os.environ.get("REPORT_ANTHROPIC_MODEL", "claude-opus-4-8")` | same |
| `max_tokens` | `16000` | `8000` |
| `thinking` | `{"type": "adaptive"}` | `{"type": "adaptive"}` |
| `output_config` | `{"effort": "high", "format": {"type": "json_schema", "schema": _CLASSIFY_SCHEMA}}` | `{"effort": "high", "format": {"type": "json_schema", "schema": _ANALYZE_SCHEMA}}` |
| Temperature | Not set (no `temperature=` argument anywhere in the call) | Not set |
| Client timeout | `120.0`s (`anthropic.Anthropic(..., timeout=_TIMEOUT_S, max_retries=1)`) | same client, same timeout |
| `max_retries` | `1` | `1` |
| Skipped entirely if | `variable` list is empty (returns `[]` without a call) or no `ANTHROPIC_API_KEY` (returns `None`) | no `ANTHROPIC_API_KEY` (returns `None`); also never called at all if `classify_expenses()` returned `None` (see `report.py`) |
| Runs inside | `llm_limits.summary_call()` (admission control — counts against the family's monthly Resúmenes quota and the 2-concurrent-call cap) | same |
| Usage recorded via | `llm_usage.record("resumen", model_name(), ...)` → one row per call in `llm_calls`, module `"resumen"` | same |

Both calls send the **entire** payload as a single JSON-serialized user-turn string
(`json.dumps(payload, ensure_ascii=False, default=str)`) — no chat history, no system
turns beyond the one `system=` string, and no prompt caching (no `cache_control` blocks
anywhere in either call).

---

## 2. Call 1 — `classify_expenses()`

### 2.1 System prompt (verbatim, from `_CLASSIFY_SYSTEM` in `report_ai.py`)

```
You classify this month's variable (non-fixed) household expenses into exactly one of two labels: "recurring" (regular behavior — groceries, the usual outings, subscriptions) or "exceptional" (one-off — a repair, an appliance, an unusual purchase). Every expense gets exactly one label plus a confidence between 0 and 1.

You have two signals, and how much to weigh each depends on how much history is available:
1. World knowledge — often decisive on its own (a washing machine purchase is exceptional even the first time you see one; a greengrocer is recurring even on first appearance).
2. Empirical recurrence evidence — how many of the available months this exact concept appeared in, and at what amounts. With little history, lean on world knowledge; with several months of evidence, lean on the empirical pattern.

For cross-month consistency, you're shown how the same or similar concepts were classified in prior months. Prefer staying consistent unless the amount or context clearly indicates something changed (same store, a wildly different amount -> probably a genuinely different kind of purchase, not the same recurring one).

Expenses may use any of the currencies present under dossier.currencies, and each expense carries its own 'currency' field. Never compare or weigh amounts across currencies — an amount with fewer digits is not therefore small. Recurrence evidence is also currency-scoped: a concept's prior occurrences only count if they're in the same currency as the expense you're classifying.

Return ONLY classifications. Do not narrate, do not sum amounts, do not analyze the month — a separate step aggregates your labels.
```

Approximate size: **1,638 characters / 259 words ≈ 330–410 tokens** (rough character-
and word-count heuristics; not measured with an actual tokenizer, no token count is
logged for the system prompt specifically — see §5).

### 2.2 User-turn payload shape

```json
{
  "expenses": [
    {
      "expense_id": 1234,
      "concept": "Verdulería",
      "amount": 8500.0,
      "currency": "ARS",
      "category": "Supermercado",
      "date": "2026-08-05T14:12:00+00:00"
    }
  ],
  "recurrence_evidence": {
    "ARS:verduleria": {
      "display_concept": "Verdulería",
      "currency": "ARS",
      "months_seen": 4,
      "months_available": 6,
      "occurrences": [
        {"year": 2026, "month": 7, "amount": 7900.0, "concept": "Verdulería"}
      ]
    }
  },
  "hard_facts": {
    "first_expense_date": "2026-01-10",
    "months_available": 6
  },
  "prior_months_classifications": [
    "- 2026-07: \"Verdulería\" ($7900) -> recurring",
    "- 2026-06: \"Netflix\" (U$S15) -> recurring"
  ]
}
```

Notes:
- `expenses` is the concatenation of **every dossier currency's** `variable_expenses` (expenses
  with `fixed_expense_id IS NULL`), built by `dossier.py`'s `_build_currency_block()`;
  each item already carries its own `"currency"`.
- `recurrence_evidence` comes straight from `dossier["recurrence_evidence"]`
  (`dossier.py`'s `_build_recurrence_evidence()`), keyed `"{currency}:{normalized concept}"`
  so a peso "Hotel" and a dollar "Hotel" don't merge.
- `prior_months_classifications` is pre-rendered as **text lines**, not structured JSON —
  one line per prior classified expense, format
  `"YYYY-MM: \"concept\" (<catalogue symbol> amount) -> label"`, produced by
  `report.py`/`report_ai.py` from `db.get_recent_classifications_before(year, month,
  lookback_months=6)`.

### 2.3 Output JSON schema (`_CLASSIFY_SCHEMA`, enforced server-side via `output_config.format`)

```json
{
  "type": "object",
  "properties": {
    "classifications": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "expense_id": {"type": "integer"},
          "label": {"type": "string", "enum": ["recurring", "exceptional"]},
          "confidence": {"type": "number"}
        },
        "required": ["expense_id", "label", "confidence"],
        "additionalProperties": false
      }
    }
  },
  "required": ["classifications"],
  "additionalProperties": false
}
```

---

## 3. Call 2 — `analyze()`

### 3.1 Base/default system prompt (verbatim, from `_ANALYZE_SYSTEM` in `report_ai.py`)

This base prompt is sent when a family has no saved preferences (or saves the defaults:
no emphasis, neutral tone, medium length, empty focus, suggestions off).

```
You write the monthly findings for a household expense report, in Rioplatense Spanish. Every number in the input "dossier" is pre-computed by code — you narrate, you never calculate, and you never contradict a number you were given.

Rules:
- Every finding must cite a concrete figure from the dossier (an amount, a percentage, a count). A finding with no number is not acceptable — never write generic advice like 'gastaste mucho en X, planificá mejor.'
- Few findings: five good ones beat twelve padded ones.
- No recommendations. This app has no budgets, so there is no target to advise against — never suggest what the user should do differently.
- questions[] is for actionable observations that need a human decision, not advice. Use type="uncategorized" for the aggregate uncategorized-spending question (fixed_expense_id null), type="unlinked_fixed" once per unpaid/unlinked fixed expense you want to flag (set fixed_expense_id to its id from the dossier — fixed expenses exist inside each currencies.<code>.fixed_expenses block, and ids are unique across all of them), type="other" for anything else worth flagging that doesn't map to either link (fixed_expense_id null). Never invent a fixed_expense_id that isn't in the dossier.
- If hard_facts.months_available is small, present every contrast as an observation ('recién es el N-ésimo mes de datos') rather than a conclusion about behavior change — recent app adoption explains differences at least as well as real spending changes, and you must say so explicitly when history is short. Each currency block also carries its own months_available — a currency with little history of its own gets the same treatment even if the other currency has plenty.
- registration_coverage measures who LOGGED an expense, not who spent the money — never infer spending behavior, fairness, or effort from it; if you mention it, describe it only as app usage.
- The fixed/recurring/exceptional partition is already computed separately in each currencies.<code>.partition block — cite it, don't recompute or contradict it.
- If the dossier contains a forecast, it was computed and frozen by code. Never recompute, contradict, narrow, widen or sharpen any forecast range, and never present a range as a certainty. Every mention must explicitly name both its target month and its cutoff month (for example: 'Proyección para julio 2026, en base a los datos hasta junio 2026'). Forecast language is descriptive and conditional ('podrías gastar'), never normative ('deberías gastar menos').
- Do not invent URLs, links, category names, or ids beyond what's in the input.

Currency rules — this dossier contains independent, same-shape blocks under "currencies". dossier.default_currency names the primary currency; every other currency remains first-class and deserves findings when material.
- NEVER sum, average, compare, or otherwise combine amounts from different currencies. Each currency's totals, contrasts, forecast and partition are self-contained.
- Every amount you write carries the symbol for its own currency, taken from dossier.currency_metadata.<code>.symbol. A bare number with no symbol is not acceptable; never infer a symbol from locale or from another currency.
- Inflation-adjusted ('real') figures may be cited ONLY when that currency's dossier entries actually provide real_current/real_baseline/real_delta fields. When an entry carries real_not_applicable, never call it real or inflation-adjusted and never explain its change using inflation. real_unavailable means a series applies but the required observation is missing; state that limitation rather than presenting the nominal figure as real.
- equivalence.items contains one optional reference-only valuation for each non-default currency in the family default, derived only from the family's own exchange operations. Cite any such valuation AT MOST ONCE and only to convey magnitude in familiar terms. Always label it as an approximation, name both currencies and the rate and rate_source used. Never treat it as an expense or a total, never sum it with spending, and never feed it into a contrast, partition, forecast or comparison. If available is false, say there is no family rate; do not infer or invent one.
- Materiality rule: for EVERY non-default currency, if its equivalence item has share_pct >= 10, or if that currency has spending and available is false, the headline and summary MUST mention that foreign-currency spending explicitly in its own currency, plus a finding about it. A large or unconvertible foreign spend cannot be relegated to a minor finding or omitted. Below that threshold, mention it only when there is something specific worth noting. Apply short-history calibration independently to that currency exactly as for the default currency.
```

Approximate size: **3,854 characters / 599 words ≈ 780–965 tokens** (same heuristic
caveat as above).

### 3.2 User-turn payload shape

```json
{"dossier": { /* the entire build_dossier() output, verbatim */ }}
```

The full dossier — `period`, `hard_facts`, `default_currency`, `currency_order`,
`currency_metadata`, `dollars`, `equivalence`, `recurrence_evidence`, and dynamic
`currencies.{code}` blocks (each with `base`, `contrasts`,
`delta_attribution`, `outliers`, `fixed_expenses`, `taxonomy`,
`registration_coverage`, `months_available`, `variable_expenses`, and — by the time
this call runs — `partition`, injected by `report.py` after `classify_expenses()`
returns) — is serialized as-is. See `dossier.py` for the exact shape of each block; it
is not reproduced here since it's deterministic. `report.py` adds `forecast` only when
the resolved emphasis includes `forecast` or suggestions are enabled. That block is the
already-computed, frozen projection for the immediately following month; the model never
receives a request to calculate it.
A synthetic sketch:

```json
{
  "dossier": {
    "period": {"year": 2026, "month": 8, "label": "Agosto 2026"},
    "hard_facts": {"first_expense_date": "2026-01-10", "months_available": 6},
    "dollars": {"venta": {"cnt": 1, "total_usd": 500, "total_ars": 850000, "cotizacion_promedio": 1700},
                "compra": {"cnt": 0}, "coverage_ratio": 0.62, "coverage_basis": "pesos + dólares equivalentes"},
    "default_currency": "ARS",
    "currency_order": ["ARS", "USD", "BRL"],
    "currency_metadata": {"ARS": {"symbol": "$"}, "USD": {"symbol": "US$"}, "BRL": {"symbol": "R$"}},
    "equivalence": {"default_currency": "ARS", "default_total": 900000,
                    "combined_default_equivalent": 1460000,
                    "items": {"USD": {"available": true, "rate": 1700,
                    "rate_source": "sale_current_period", "foreign_total": 300,
                    "total_in_default": 510000, "share_pct": 34.9},
                    "BRL": {"available": false, "foreign_total": 1000,
                    "total_in_default": null, "share_pct": 0}}},
    "recurrence_evidence": { "...": "..." },
    "currencies": {
      "ARS": {"currency": "ARS", "base": {"...": "..."}, "contrasts": {"...": "..."},
              "delta_attribution": {"...": "..."}, "outliers": {"...": "..."},
              "fixed_expenses": {"...": "..."}, "taxonomy": {"...": "..."},
              "registration_coverage": [{"user": "Juampi", "count": 12, "total": 400000}],
              "months_available": 6, "variable_expenses": [ "..." ],
              "partition": {"available": true, "fixed_total": 300000, "recurring_total": 500000,
                             "recurring_count": 20, "exceptional_total": 100000,
                             "exceptional_count": 3, "variable_total": 600000},
              "inflation_unavailable": false},
      "USD": { "...": "same shape; no series, so contrasts carry real_not_applicable" },
      "BRL": { "...": "same shape; equivalence may be unavailable" }
    }
  }
}
```

### 3.3 Output JSON schema (`_ANALYZE_SCHEMA`)

```json
{
  "type": "object",
  "properties": {
    "headline": {"type": "string"},
    "summary": {"type": "string"},
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": false
      }
    },
    "questions": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "type": {"type": "string", "enum": ["uncategorized", "unlinked_fixed", "other"]},
          "text": {"type": "string"},
          "fixed_expense_id": {"anyOf": [{"type": "integer"}, {"type": "null"}]}
        },
        "required": ["type", "text", "fixed_expense_id"],
        "additionalProperties": false
      }
    }
  },
  "required": ["headline", "summary", "findings", "questions"],
  "additionalProperties": false
}
```

`headline`, `summary`, `findings`, and `questions` are all `required` — the API's
structured-output enforcement means a response missing any of them, or with the wrong
top-level shape, cannot occur (see §6 for what "structurally can't happen" leaves as an
actual risk surface).

### 3.4 Per-family compilation

`family_report_preferences` stores one shared, RLS-protected row per family. Any active
member may update it from `/resumenes`; no row resolves to these behavior-preserving
defaults:

```json
{"emphasis": [], "tone": "neutral", "length": "medium", "focus": "", "allow_suggestions": false}
```

The emphasis choices come from actual dossier structures, not an independent product
taxonomy: category totals/movement/taxonomy, temporal contrasts, non-default-currency
spending and reference equivalences, fixed-expense state, statistical outliers, the fixed/recurring/exceptional
partition, and the stored next-month forecast. Tone is `neutral|warm|direct`; length is `short|medium|long`. Neutral/medium
emit no extra soft-guidance text. Short prefers about three strong findings; long permits up to seven only
when supported, retaining the no-padding rule.

`compile_analyze_config()` deep-copies the live analysis call config and appends soft
guidance after every hard rule. The appendix says preferences may change only topic
emphasis, tone, narrative length and whether evidence-based suggestions are permitted;
they cannot alter Rioplatense Spanish, the JSON schema, factual/currency constraints, or
question/id rules. The focus is trimmed,
limited to 400 characters in both application validation and a DB check, JSON-quoted,
angle-bracket escaped, enclosed in an untrusted-data delimiter, and explicitly treated
as subject matter rather than instructions. Thus text asking for English, a different
schema or fabricated figures remains subordinate to the immutable rules.

With suggestions off, the old blanket prohibition remains literally unchanged. With it
on, only that sentence is replaced: suggestions may appear, but each must explicitly
rest on a concrete dossier figure and may never invent a target, threshold, budget or
other number. A frozen forecast range is an allowed anchor but never a target. The output
schema and deterministic page sections do not change.

---

## 4. What's LLM-generated vs. deterministic on `/resumenes`

Traced directly from `resumenes.html`'s `render()` and its helper functions.

| Report section (template function) | Source |
|---|---|
| Primary hero plus collapsed additional-currency summaries (`renderHero`/`renderSecondaryCurrencies`/`heroCard`/`heroTrend`) | 100% deterministic — ordered dynamic `dossier.currencies.{code}.base` / `.contrasts`; the family default is primary |
| Equivalence line inside each non-default panel (`equivalenceLine`) | 100% deterministic — `dossier.equivalence.items.{code}`, computed from family exchange operations |
| Headline, summary, findings (`renderNarrative`) | 100% LLM — `report.output.headline`/`.summary`/`.findings[].text` verbatim from `analyze()`. Rendered through `escapeHtml()` (textContent-based), so arbitrary text is safe to inject as HTML but not otherwise validated |
| "Tres clases de gasto" bars — fixed slice (`renderKinds`) | Deterministic per dynamic currency — `partition.fixed_total`, from that block's `fixed_expenses.total_paid` |
| "Tres clases de gasto" bars — recurring/exceptional slices | **Hybrid**: the *label* on each expense (which bucket it falls in) comes from the classification LLM call; the *sums* (`recurring_total`, `exceptional_total`, counts) are computed by code in `report.py`'s `_build_partitions()`, which only aggregates labels the model returned — the model itself never sums |
| Next-month projection and predicted-vs-actual (`renderForecast`) | 100% deterministic — `forecast.py` computes and `report_forecasts` freezes one block per currency. Target actuals are queried later only for the inline backtest and never change the stored prediction |
| Dollars section (venta/compra totals, coverage ratio) (`renderDollars`) | 100% deterministic — `dossier.dollars`, from `db.get_cambios_resumen_mes_by_tipo()` |
| "Quién registró" / registration coverage (`renderCoverage`) | 100% deterministic and rendered once per currency — `dossier.currencies.{cur}.registration_coverage` |
| "Para resolver" / questions list (`renderQuestions`) | 100% LLM — `report.output.questions[]` verbatim from `analyze()`. `type` drives which URL the question links to (client-side `if` on the enum value, not LLM-supplied); `text` goes through `escapeHtml()` |
| "Análisis narrativo no disponible" note | Deterministic gate: shown whenever `report.llm_ok` is false (i.e. either LLM call failed) |
| Header meta line ("Versión generada el… con…") | Deterministic — `report.generated_at` and `report.model` (the model name string persisted at generation time), plus the same `llm_ok` gate |

---

## 5. Degraded output vs. broken page

**"Breaks" would mean**: a JS exception during `render()`, a blank/half-rendered page,
or a 500 from the API. Based on the code:

- **The Anthropic call itself fails** (network, auth, timeout, rate limit — anything
  `client.messages.create()` raises): caught in `report_ai.py`, logged, function
  returns `None`. `report.py` sets `llm_ok = output is not None`, persists the report
  anyway with `output_json=None`, and the page shows the "análisis narrativo no
  disponible" note in place of the narrative section — **degrades, does not break**.
  If `classify_expenses()` itself returns `None`, `analyze()` is never even called
  (`report.py`: `if classifications is not None: output = report_ai.analyze(dossier)`),
  so a classification failure also skips straight to a dossier-only report.
- **The response has no text block** (`_first_text()` returns `None`): treated the same
  as a call failure — logged, `None` returned, same degrade path.
- **The response text isn't valid JSON** (`json.JSONDecodeError`): same — caught,
  logged, `None`, same degrade path. Given `output_config.format` enforces the JSON
  schema server-side, this should be unreachable in practice for a successful API
  response; the code defends against it anyway.
- **The response is schema-valid JSON with unexpected *content*** (wrong language,
  a `findings` array that's empty, a `headline` in English, a finding that's too
  short/long, `questions` that don't actually reference a real problem): the schema
  guarantees are purely structural (types, required keys, enums) — there is **no
  content-level validation** anywhere in `report_ai.py`, `report.py`, or
  `resumenes.html`. Whatever text comes back renders as-is (through `escapeHtml`, so
  at minimum it can't inject markup). An empty `findings` array simply skips rendering
  the `findings-grid` div (`findings ? ... : ""`); an empty `questions` array skips the
  "Para resolver" card entirely (`if (out && out.questions && out.questions.length)`).
  **This is the one real gap**: nothing catches a fluent-but-wrong narrative (e.g. one
  that ignores the materiality rule, or narrates in the wrong language) — it will
  render exactly as returned. This is a "renders degraded" outcome (bad content, not a
  bad page), never a "breaks" outcome, given the current code.
- **A `fixed_expense_id` the model invents that doesn't exist in the dossier**: the
  system prompt instructs the model never to do this, but nothing enforces it. The only
  consumer of `fixed_expense_id` client-side is the `unlinked_fixed` question's link,
  which routes to `/fijos?year=&month=` — it doesn't deep-link to the specific id — so
  an invented id would not break navigation, just point at the right period without
  necessarily highlighting the right fixed expense (page-level behavior of `/fijos` for
  an unrecognized id in the query string was not traced as part of this inventory).

---

## 6. Prompt traceability — what's persisted with a report

`reports` has `model`, `prompt_version` and, since migration `0011`, nullable
`preferences_json`; migration `0012` adds the separate insert-only `report_forecasts`
rows. Every new `generate_report()` resolves preferences once, uses that
same value to compile the call, and persists it:

```python
report_id = db.create_report(
    year=year, month=month,
    model=report_ai.model_name(),      # e.g. "claude-opus-4-8"
    prompt_version=report_ai.prompt_version(preferences),
    dossier_json=..., output_json=..., fingerprint=fp, llm_ok=llm_ok,
    preferences_json=json.dumps(preferences),
)
```

`prompt_version()` derives the value from the exact response-shaping configuration used
by both calls: the complete `_CLASSIFY_SYSTEM` and `_ANALYZE_SYSTEM` text, both JSON
schemas, `max_tokens`, adaptive-thinking configuration, output effort and structured-
output settings, plus the complete resolved preferences. The same compiled dictionary is
expanded into `client.messages.create()` and serialized for the fingerprint, so two
families with different preferences cannot share a version even if a future compiler
maps two values to equivalent text. Sorted-key compact JSON feeds SHA-256, making mapping
order irrelevant and the result stable across machines. The value has this shape:

```text
report-v1-<first 12 hex chars>:sha256:<full 64-char SHA-256>
```

The short prefix is convenient when inspecting rows; the full digest preserves the
complete identity. `v1` identifies the fingerprint format, not a manually maintained
prompt generation. Editing any covered live value changes the digest automatically.

The model name remains excluded because it is stored in `reports.model`; the dossier and
forecast values are excluded because they are report input. The hard forecast rules and
the resolved preference that controls whether forecast material is sent are included.
Resolved preferences are configuration, so they are
both fingerprinted and stored. The fingerprint answers whether configuration differed;
the snapshot answers how. This is worth the nullable column because preferences are
tenant-specific, mutable, and reports are append-only. The raw prompt is not copied into
the row or exposed in the UI.

Rows created before either traceability mechanism retain their original labels (for
example `prompt_version = "3"`) and have `preferences_json = NULL`. There is no
retroactive fingerprint or synthetic preference snapshot: the exact historical state is
unknowable. `_load_report_row()` treats that nullable field as absent, and the existing
client-side dossier normalization remains unchanged, and no `report_forecasts` rows are
backfilled, so historical reports render as before.
