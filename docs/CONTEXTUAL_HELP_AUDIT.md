# Contextual Help Audit — Ready for External Users?

Audit produced ahead of onboarding the first external (non-builder) families. Scope: every
authenticated screen, the public entry path, and the Telegram bot. Method: read every template
in `gastos/app/templates/`, the relevant sections of `bot.py`/`dashboard.py`/`intent.py`, plus
`PROJECT.md`, `README.md`, `gastos/DOCS.md` and `gastos/CHANGELOG.md`, then classified every
concept a first-time user meets as either something they already own from real life (a **domain
concept** — needs no help) or something this app invented (a **system mechanic** — the candidate
for contextual help).

This document produces no code change. It is the input for the implementation batches in §5.

---

## 1. Top 5 concepts most likely to make a new user misunderstand their own data

Ranked by (a) how silently the misunderstanding produces wrong data or a wrong belief about the
family's money, and (b) how many screens/flows the gap touches.

### 1. Fixed-expense link, and why its "period" is independent of the expense's date
**Where:** [history.html](../gastos/app/templates/history.html) (`Gasto fijo` column + inline-edit
select, no copy at all), [fijos.html](../gastos/app/templates/fijos.html) (`Registrar pago` and
`¿Ya lo pagaste?` modals), `bot.py`'s `_maybe_offer_fixed_link()` (fixed-link offer buttons).
**Mechanic:** linking an expense to a fixed expense (a) forces the expense's category/subcategory
to the fixed expense's own, overriding whatever was picked, and (b) marks a specific
year/month as "paid" — a field independent from the expense's own date. Editing only the date
later does not move which period is marked paid.
**What a user assumes vs. reality:** a user assumes "linking" is just a label, or that the period
a payment counts for is whatever date they put on the expense. Neither is true.
**Consequence:** a rent payment dated for the 28th linked from `/fijos` while browsing a past
month, or a category silently overwritten after linking, reads as **silent wrong data** — nothing
errors, the numbers are just not what the user expected. It's also a **dead end**: `/fijos` still
shows "unpaid" for a month the user is sure they paid, with no visible reason why.
**Fix type:** help text (inline microcopy at the point of linking) — the underlying two-field
design is deliberate and correct (see `PROJECT.md`'s 2.1.0 note), so this is not a rename/redesign
candidate.

### 2. Picking a category via Telegram's inline keyboard silently teaches a keyword
**Where:** `bot.py`, the `c:` callback handler (category-picker confirmation for an
uncategorized expense).
**Mechanic:** the exact concept text just gets `categorizer.normalize()`-ed and written to
`keywords` as a new mapping (`db.add_keyword`) the instant a category button is tapped. Nothing
in that confirmation message says this happened.
**What a user assumes vs. reality:** the user assumes they just categorized *this* expense.
In reality every future expense whose concept contains that word will now auto-categorize the
same way, silently, with no confirmation (per the documented "Subcategory inference is silent"
gotcha, which applies to category learning too).
**Consequence:** weeks later, an unrelated expense (e.g. "Carrefour Express — nafta" landing in
Vehículos because "nafta" was once mapped there) gets miscategorized with no visible cause —
**silent wrong data**, and the only way to find the root cause today is to already know to check
Categorías → Palabras para categorizar.
**Fix type:** help text — one line appended to the same confirmation message, at the exact
moment the keyword is created. The mechanic itself is good design (this is the entire point of
`categorizer.py`); it just needs to stop being invisible.
**Note:** on the *web* side, this exact mechanic is already well explained — see the "already
good" pattern in §3, Categorías.

### 3. "Quién registró" / the `Usuario` column is who logged the expense, not who paid for it
**Where:** [history.html](../gastos/app/templates/history.html) `Usuario` column and filter,
[index.html](../gastos/app/templates/index.html) "Ver gastos de:" filter, Telegram's `/semana`,
`/hoy` and per-expense user tags.
**Mechanic:** every expense records whoever's Telegram/web session created the entry — not
whoever the money came from or who benefits. In a household with more than one person, these
routinely diverge (one partner tends to log everything; a purchase paid by one person is logged
by whoever noticed the receipt first).
**What a user assumes vs. reality:** in a shared-money product, a "by person" breakdown reads as
"who spent/owes what." It answers "who used the app," full stop.
**Consequence:** this is the one entry with a real emotional/financial stake — a family drawing
conclusions about fairness or reimbursement from a number that means something else entirely.
**Fix type:** help text — reuse the disclaimer that already exists in exactly one place (see
below) and propagate it to the other three.
**Note:** `resumenes.html`'s "Quién registró" block *already* carries this exact disclaimer
verbatim: *"Cuenta quién cargó cada gasto en la app, no quién lo pagó — no es un reparto de
gastos."* That sentence should travel to History and the dashboard user filter, not be
reinvented.

### 4. Creating a family doesn't say that every member sees every expense
**Where:** [onboarding.html](../gastos/app/templates/onboarding.html), the "Creá tu espacio
familiar" branch (family creator).
**Mechanic:** there is no per-member privacy inside a family — every active member sees every
expense, income, fixed expense and exchange, regardless of who logged it.
**What a user assumes vs. reality:** many personal-finance apps have some notion of private vs.
shared entries; a first-time creator has no way to know this one doesn't, until they've already
logged something they'd have preferred private.
**Consequence:** **not reversible** — an uncomfortable disclosure of personal spending, discovered
only after the fact.
**Fix type:** help text — one sentence at family creation. Note the asymmetry already in the
codebase: `join_family.html` *does* say this to an invitee ("Vas a compartir los gastos y la
configuración de esta familia"), but the person creating the family from scratch never sees the
equivalent sentence.

### 5. `/ayuda` documents roughly half of what the bot can actually do
**Where:** `bot.py`, `cmd_ayuda()`.
**Mechanic:** `/ayuda` is the bot's only on-demand, comprehensive reference. It currently lists:
plain-text logging, voice, currency-exchange NL, five read-only commands, edit/delete, keywords,
and category creation. It never mentions: sending a photo of a ticket (OCR), the natural-language
layer for anything *other* than currency exchange (logging, editing, taxonomy, read-only
reports — the feature covering phrases like "anotame 100 lucas" or "cuánto gastó Cele en comida
en marzo"), income logging (`Ingreso: concepto monto` or "cobré..."), or the shopping list
phrases ("falta detergente", "qué falta comprar").
**What a user assumes vs. reality:** a first-time external user with zero verbal explanation will
read `/ayuda` once, conclude the bot only understands the documented command syntax, and never
discover OCR, conversational logging, incomes-by-chat or the shopping list at all.
**Consequence:** dead end — not wrong data, but permanently unused capability, for the exact
audience this help screen exists to onboard.
**Fix type:** help text (on-demand disclosure) — `/ayuda` needs to become complete, not
redesigned; it's the right pattern in the wrong (incomplete) content.

---

## 2. Help pattern vocabulary

The app already uses five recognizable patterns. The goal below is to name them, say when each
applies, and route every finding in §3 to one of them — not invent a sixth per finding.

| Pattern | What it looks like today | Use when | Don't use when |
|---|---|---|---|
| **A. Empty-state explainer** | Icon + short title + one-line copy + a CTA button. E.g. `fijos.html`'s "Empezá por un gasto que se repite" / "Alquiler, internet o una cuota: cargalo una vez y cada mes vas a ver si ya está pagado." | The screen has zero data. This *is* the first-contact surface for a new family — treat it as primary copy, not a filler message. | The screen has data. Never show alongside populated content. |
| **B. Inline microcopy** | One line directly under/beside the control it clarifies, stating scope or a disclaimer in plain language. E.g. `resumenes.html`'s equivalence line ("Es sólo referencia: no se suma a ningún gasto ni mezcla monedas") or `family.html`'s default-currency note ("No convierte nada: los gastos... conservan su moneda para siempre"). | A specific label/control has a non-obvious scope, consequence, or exception, and the user needs to know it *every time*, not just once. | The concept is genuinely obvious from the label — don't restate the label ("Fecha: la fecha del gasto"). |
| **C. First-use explainer** | A dedicated block, conditioned on "user hasn't done this yet," explaining a concept from zero with a concrete example. E.g. `telegram_link.html`'s "¿Qué es esto?" card (`{% if not connected %}`) or the landing page's before/after `log-showcase`. | The concept requires background the user may not have at all (what a Telegram bot is; what a currency exchange record is for), and showing it forever would be clutter once they've done it once. | The user will encounter this every single session going forward — that belongs in pattern B, not a one-time block that then never explains anything again. |
| **D. On-demand disclosure** | A surface the user opens deliberately, expected to be comprehensive. `/ayuda` in Telegram; the "Preferencias del relato" toggle panel. | Reference material that doesn't need to be shown by default but must be complete when opened — a partial on-demand disclosure is worse than none, because it reads as authoritative. | Don't use for anything the user needs to know *before* acting (that's B or C) — by definition nobody sees D until they go looking for it. |
| **E. In-context quota/limit status** | The exact number and what it costs, stated right where the action is taken. `resumenes.html`: "Cada generación usa 1 de las 15 disponibles por mes; te quedan 12." `bot.py`'s daily-quota-exhausted message. | Any consumable limit (LLM quota, invitation expiry) — always at the decision point, never on a separate status page only. | — |

Every finding in §3 is tagged with one of **A/B/C/D/E**, or flagged `REDESIGN` when no pattern
fits (see §4).

---

## 3. Full inventory, by screen

Legend: **Domain** = user already owns this concept; **Mechanic** = this app invented it.
Severity: 🔴 high (wrong data / dead end / not reversible), 🟡 medium (confusion, recoverable),
⚪ low (cosmetic, or already well handled — listed for completeness/reuse).

### Public entry path

| Concept | Type | Assumption vs. reality | Consequence | Sev. | Pattern |
|---|---|---|---|---|---|
| Landing's before/after log-showcase (voice/text/photo → categorized entry) | Mechanic | Already well done — a genuinely new user learns the input model from the landing page alone. | — | ⚪ | **C** (reuse as reference) |
| `/login` — single identity screen, no name/family fields | Domain-ish | Self-explanatory; explicitly avoids the multi-account-enumeration bug from 7.8.0. | — | ⚪ | — |
| `/onboarding` create-family: no privacy disclosure | Mechanic | See Top-5 #4. | 🔴 not reversible | 🔴 | **C** |
| `/onboarding` join-family: *does* disclose sharing ("Vas a compartir los gastos...") | Mechanic | Already good — the model to copy for the create-family branch. | — | ⚪ | **C** (reuse) |
| `/unirme/<token>` invitation landing | Domain-ish | Clear. | — | ⚪ | — |

### Dashboard (`index.html`)

| Concept | Type | Assumption vs. reality | Consequence | Sev. | Pattern |
|---|---|---|---|---|---|
| Onboarding checklist ("Primeros pasos") | Mechanic | Already good: explicit, dismissible, owner-only invite step. | — | ⚪ | **A/C** (reuse) |
| "Moneda:" selector next to the hero total | Mechanic | Looks like a display/conversion toggle; it's actually a **scope** switch — totals, charts and everything below are that currency's data only, never combined. A family with a second currency who flips it will see the total crash to near-zero and may read that as "my data disappeared," not "I'm looking at a different, smaller slice." | Silent impression of missing data | 🟡 | **B** |
| "Ver gastos de:" per-user filter | Mechanic | See Top-5 #3 — same "logged by" ≠ "spent by" gap, undisclosed here. | Misread financial data | 🔴 | **B** (propagate from Resúmenes) |
| Empty states (doughnut/bar/months charts, "Este mes todavía está vacío") | Mechanic | Already good, consistent copy across charts. | — | ⚪ | **A** (reuse) |
| Fijos widget on the dashboard | Mechanic | Shows paid/unpaid without re-explaining the link/period mechanic — acceptable, since Fijos itself is one click away and should carry the explanation (see Fijos section). | — | 🟡 | — |
| Daily quote | Domain-ish | Decorative, no action riding on it; no help needed. | — | ⚪ | — |
| Shared period control (`_period_control.html`) | Mechanic | The control gives no indication that changing it here also changes Movimientos/Ingresos/Fijos/Cambios/Resúmenes, nor that Movimientos' own year/month filters are a separate, local thing. | Cosmetic confusion at worst — behavior is a pleasant surprise, not a harmful one. | 🟡 | **B** (tooltip on the control) |

### Movimientos (`history.html`)

| Concept | Type | Assumption vs. reality | Consequence | Sev. | Pattern |
|---|---|---|---|---|---|
| `Gasto fijo` column + inline-edit link select | Mechanic | See Top-5 #1. Zero copy anywhere near this control. | 🔴 silent wrong category/period | 🔴 | **B** |
| `Usuario` column/filter | Mechanic | See Top-5 #3. | 🔴 misread data | 🔴 | **B** |
| Filter chips + "Todos" as an explicit option (2.2.0) | Mechanic | Already good — the exact problem it fixed (silent zero-result searches) is gone, and active filters are visible and removable. | — | ⚪ | **B** (reuse) |
| Inline create-category/subcategory from the Add-expense modal | Mechanic | Self-explanatory: a "+Crear" affordance inside a select is a common enough UI pattern that no copy is needed. | — | ⚪ | — |
| `add-currency` select in "Agregar gasto" | Domain-ish | Clear given the amount field right next to it. | — | ⚪ | — |
| Fixed-link offer after saving (`confirm()` dialog, "💡 ... coincide con tu gasto fijo...") | Mechanic | The dialog itself is a clear yes/no worked example; it just doesn't warn that saying yes forces the category. Low severity since it's one click to undo via the same select. | 🟡 | 🟡 | **B** (one added clause) |

### Ingresos (`incomes.html`)

| Concept | Type | Assumption vs. reality | Consequence | Sev. | Pattern |
|---|---|---|---|---|---|
| "Por moneda, sin conversiones ni balances artificiales" | Mechanic | Already good — states the no-mixing rule up front, right under the page title. | — | ⚪ | **B** (reuse) |
| "Ingresos ARS del mes" / "Ingresos USD del mes" summary tiles | Mechanic/bug | These two tiles are hardcoded to ARS and USD. The rest of the app (expenses, Resúmenes) generalized to N catalogue currencies in 7.15–7.17; this screen's own summary strip did not. A family whose default currency is BRL or EUR gets an incomplete monthly total here with no indication anything is missing. | **Silent wrong/incomplete numbers** | 🔴 | `REDESIGN` (code fix, not copy — see §4) |
| Per-person income ownership rule ("cada persona puede editar/eliminar únicamente sus propios ingresos") | Mechanic | Not stated anywhere in the UI; a member trying to edit someone else's income row gets a plain `alert()` with the 403 message only after attempting it. | Minor friction, recoverable | 🟡 | **B** |

### Fijos (`fijos.html`)

| Concept | Type | Assumption vs. reality | Consequence | Sev. | Pattern |
|---|---|---|---|---|---|
| Empty state ("Empezá por un gasto que se repite...") | Mechanic | Already good — explains the domain concept (recurring bill) in concrete examples. | — | ⚪ | **A** (reuse) |
| `Registrar pago` modal | Mechanic | See Top-5 #1 — creates a new expense dated within the viewed period and marks it paid; nothing in the modal says a new expense is being created at all (vs. "marking" something that already exists). | 🔴 confusion about what just got created | 🔴 | **B** |
| `¿Ya lo pagaste?` candidate search | Mechanic | Reasonably self-explanatory once candidates appear (worked example: pick one or say none), but doesn't say that picking one will also change that expense's category to the fixed expense's own. | 🟡 | 🟡 | **B** |
| "Estado del mes" progress bar | Domain-ish | Clear. | — | ⚪ | — |

### Cambios (`dolares.html`)

| Concept | Type | Assumption vs. reality | Consequence | Sev. | Pattern |
|---|---|---|---|---|---|
| The concept of a "cambio" itself (a currency conversion, not a purchase) | Mechanic | Zero explanation anywhere on the page of what recording a "cambio" is *for* (it never appears as an expense, and it quietly feeds the "reference equivalence" numbers in Resúmenes). A first-time user has no way to guess this connection exists. | 🟡 mostly cosmetic; downstream effect is reference-only | 🟡 | **C** (reuse the `telegram_link.html` "¿Qué es esto?" pattern) |
| "Tasa (recibida por entregada)" field label | Mechanic | Exposes the internal `rate_received_per_given` convention almost verbatim; a user has to do the division in their head to sanity-check it. | 🟡 | 🟡 | `REDESIGN` (naming) — see §4 |
| "Lectura" column header | Mechanic | Cryptic on its own; it resolves to a derived buy/sell label only when one side is the family default. | 🟡 | ⚪ | `REDESIGN` (naming) |
| Empty state | Mechanic | Reasonable, if terse. | — | ⚪ | **A** (reuse) |

### Resúmenes (`resumenes.html`)

This screen is the best-documented in the app and should be the template for the rest, not a
target for new copy. Listed for completeness and to mark exactly what's worth propagating.

| Concept | Type | Status | Sev. | Pattern |
|---|---|---|---|---|
| Recurring/exceptional classification, labeled "Fijo — evento / Recurrente — comportamiento / Excepcional — ruido" | Mechanic | Already good — plain-language relabeling of a classification a user could never guess otherwise. | ⚪ | **B** (reuse the label style elsewhere) |
| Reference equivalence between currencies | Mechanic | Already good — explicit "no se suma a ningún gasto ni mezcla monedas" every time it appears, including the "sin cambio propio" no-data case. | ⚪ | **B** (reuse) |
| "Quién registró" disclaimer | Mechanic | Already good — see Top-5 #3; this is the sentence to copy elsewhere, not to rewrite. | ⚪ | **B** (reuse) |
| Forecast card (buckets, confidence levels, inflation notes) | Mechanic | Thorough almost to a fault — four confidence tiers, per-category stability tags and an inflation-methodology note all appear by default. This is the one place in the app where *more* explanation isn't the fix. | 🟡 | `REDESIGN` (progressive disclosure) — see §4 |
| "Preferencias del relato" panel | Mechanic | Already good — explicitly scopes itself ("Los totales, gráficos y demás datos de la página no cambian") and states persistence ("El que estás viendo queda intacto"). | ⚪ | **B/D** (reuse) |
| Report-generation quota | Mechanic | Already good — exact remaining count shown right where "Generar" is clicked. | ⚪ | **E** (reuse) |

### Categorías (`settings.html`)

| Concept | Type | Status | Sev. | Pattern |
|---|---|---|---|---|
| Keywords ("Palabras para categorizar") | Mechanic | Already good on the web: "Una palabra le enseña a la app dónde guardar gastos parecidos. Por ejemplo, 'carrefour' puede ir siempre a Hogar → Supermercado." Plus a matching empty state. This is the canonical concept the brief called out — and it's already solved *here*. The gap is that this same mechanic is invisible when it happens **inside the bot** (see Top-5 #2). | ⚪ (web) / 🔴 (bot) | **B** (reuse text; extend trigger point) |
| Category vs. subcategory as a two-level hierarchy | Domain-ish | Grocery-store-aisle-shaped concept; self-explanatory from the UI itself (a subcategory list nested under a category). | ⚪ | — |

### Familia (`family.html`)

| Concept | Type | Status | Sev. | Pattern |
|---|---|---|---|---|
| Default currency change scope | Mechanic | Already good — two-line explanation directly under the heading, including the "no convierte nada" guarantee. | ⚪ | **B** (reuse) |
| Invitation link (one-time, 7-day expiry) | Mechanic | Already good — "Funciona una sola vez y vence en 7 días. Copialo ahora: después no podemos volver a mostrarlo." | ⚪ | **B** (reuse) |
| Member removal keeps their historical expenses visible | Mechanic | Already good — "Tus gastos quedan en la familia aunque salgas." | ⚪ | **B** (reuse) |
| Family creation not disclosing shared visibility | Mechanic | See Top-5 #4 (the gap is on `/onboarding`, not here — `/familia` itself is fine). | 🔴 | **C** |

### Vincular Telegram (`telegram_link.html`)

| Concept | Type | Status | Sev. | Pattern |
|---|---|---|---|---|
| What Telegram/a bot even is, before connecting | Mechanic | Already excellent — the single best first-use explainer in the app, assumes zero background ("Telegram es una app de mensajería gratuita, como WhatsApp. Un bot es un contacto automático..."). This is the reference example for pattern **C**. | ⚪ | **C** (reuse everywhere a first-time concept needs introducing) |

### Exportar (`export.html`)

| Concept | Type | Status | Sev. | Pattern |
|---|---|---|---|---|
| What's in each CSV / the ZIP | Domain-ish | Already clear, one line per dataset. | ⚪ | **B** (reuse) |
| Excel/regional-settings CSV import note | Mechanic (tooling quirk, not app-invented) | Already good, practical and specific. | ⚪ | **B** (reuse) |

### Lista de compras (`shopping.html`)

| Concept | Type | Status | Sev. | Pattern |
|---|---|---|---|---|
| Shared, family-wide list | Domain-ish | Already stated ("Compartida por toda la familia..."). | ⚪ | **B** (reuse) |
| Not an accounting surface (no money tracked here) | Mechanic | Implied only by nav placement (visually separated per PROJECT.md); never stated in-page. Low priority — the absence of any amount field on the whole screen already makes this fairly self-evident. | ⚪ | — |

### Sistema / Superadmin (`config.html`, `superadmin.html`)

Both are internal-operator surfaces (superadmin-gated since 7.5.4), not part of the external-user
onboarding path. Superadmin is already thoroughly captioned (`help-copy` under nearly every
section). No findings scoped to external-user readiness here.

### Telegram bot

| Concept | Type | Assumption vs. reality | Consequence | Sev. | Pattern |
|---|---|---|---|---|---|
| `/ayuda` completeness | Mechanic | See Top-5 #5. | 🔴 unused capability | 🔴 | **D** |
| Silent keyword learning on category pick | Mechanic | See Top-5 #2. | 🔴 silent miscategorization later | 🔴 | **B** |
| Auto-save on high-confidence voice/exchange | Mechanic | Already reasonably handled: the saved-expense message is visually identical to a manual save but *always* ships with an edit keyboard, and the confidence rule itself is documented in `/ayuda` ("Si es claro, se registra solo; si no, te pido confirmar."). Low residual risk since nothing is unrecoverable. | ⚪ | **D** (reuse, already covered) |
| OCR ticket confirmation | Mechanic | Already a clean worked example — shows extracted fields, asks yes/no before saving. | ⚪ | (worked example, reuse) |
| Daily/monthly LLM quota exhausted message | Mechanic | Already good — states exactly when it resets and that the deterministic fast path still works. | ⚪ | **E** (reuse) |
| `/gastos` monthly summary hardcoded to ARS + USD | Mechanic/bug | Same class of gap as the Ingresos tiles above — `cmd_gastos` explicitly queries only `"ARS"` and `"USD"`, so a family using BRL/EUR gets an incomplete `/gastos` reply with no indication of what's missing. | 🔴 silent incomplete numbers | 🔴 | `REDESIGN` (code fix) — see §4 |
| Argentine number notation (`.` thousands, `,` decimal) in free text | Domain-ish, locale-specific | Only genuinely ambiguous for a non-Rioplatense-Spanish user; the parser already disambiguates the common edge cases (3-digit-after-separator heuristic). Documented in `DOCS.md` but never surfaced inside the bot itself. | 🟡 low residual risk given the existing heuristic | ⚪ | Not prioritized — heuristic already does the work |

---

## 4. Concepts where the answer is rename/redesign, not help text

Per the brief: a concept that needs a paragraph may need a better name or default instead. Four
cases surfaced here:

1. **Forecast card density (Resúmenes).** Four confidence tiers, per-category variance tags, and
   an inflation-methodology sentence all render unconditionally. Nothing here is *wrong* — it's
   the single most thoroughly-explained mechanic in the app — but a first-time user gets the full
   analyst view by default. **Redesign:** show the headline range and one plain sentence
   ("Incluye lo fijo, lo habitual y una reserva para imprevistos") collapsed by default, with the
   bucket breakdown behind a "Ver el detalle" disclosure. This turns an over-explained mechanic
   into an on-demand one (pattern D) instead of writing more inline copy (pattern B) on top of an
   already-dense card.

2. **"Tasa (recibida por entregada)" label (Cambios).** No copy fixes a label that requires doing
   a mental division to verify. **Redesign:** display the computed rate as a live worked example
   next to the field as amounts are typed — e.g. "1 USD = $1.450" — rather than a static label
   describing a ratio direction.

3. **"Lectura" column header (Cambios).** Same screen, same instinct: the column resolves to a
   derived buy/sell label. **Redesign:** rename to something concrete like "Operación," or drop
   the abstraction and just show the two currencies with an arrow (already used elsewhere in the
   same screen's "Entregada"/"Recibida" labels) instead of a synthesized reading.

4. **"Moneda:" label reused for two different mechanics on the Dashboard.** The same word
   currently labels both a **view-scope filter** (Dashboard hero) and **data-entry currency
   pickers** (Agregar gasto, Fijos, Ingresos, Cambios forms) — this is a naming collision, not
   just a missing explanation. **Redesign:** rename the Dashboard hero control to something that
   reads as a viewport, e.g. "Ver en:", and reserve "Moneda" for the fields where the user is
   actually choosing what currency a new record is in.

The two hardcoded-currency aggregation gaps (Ingresos summary tiles, Telegram `/gastos`) found
during this audit are **not** help-text or naming issues — they're incomplete N-currency rollout
from the 7.15–7.17 series, producing genuinely missing numbers for any non-ARS/USD family. They
belong in a follow-up implementation ticket, not this document's copy batches.

---

## 5. Proposed implementation batches

Each batch is a coherent, independently shippable session. Copy below is final Rioplatense
Spanish, ready to drop in — no placeholders.

### Batch 1 — Blocking external onboarding

**1a. Rewrite `/ayuda` to cover the whole surface (pattern D).**
Add, in `bot.py`'s `cmd_ayuda()`, sections currently missing entirely:
```
📸 <b>FOTO DE UN TICKET</b>
   Mandá la foto y te muestro comercio, monto y fecha antes de guardar.

💬 <b>HABLAME COMO QUIERAS</b>
   No hace falta usar los comandos de arriba: "anotame 100 lucas en el súper",
   "el gasto 124 fueron 40000", "cuánto gastó Cele en comida en marzo".

💵 <b>INGRESOS</b>
   <code>Ingreso: sueldo 500000</code> o "cobré 300 mil de un cliente".

🛒 <b>LISTA DE COMPRAS</b>
   "falta detergente", "compré el detergente", "qué falta comprar".
```

**1b. Disclose shared family visibility at creation (pattern C).**
In `onboarding.html`, create-family branch, under the existing intro paragraph:
> Vas a compartir gastos, ingresos y lista de compras con quienes invites — no hay una vista
> privada dentro de la familia.

**1c. Explain the fixed-expense link at the two points it's created (pattern B).**
In `history.html`, above or beside the `Gasto fijo` filter/column:
> Vincular un gasto a un fijo lo cuenta como su pago de ese mes y le copia la categoría del fijo.

In `fijos.html`'s `Registrar pago` modal, under the title:
> Esto va a crear un gasto nuevo para este período y marcarlo como pagado.

### Batch 2 — High impact, not blocking

**2a. Acknowledge silent keyword learning when picking a category via Telegram (pattern B).**
In `bot.py`'s `c:` callback handler, append one line to the existing confirmation message:
> 🧠 <i>Los próximos gastos parecidos a "{concept}" van a esta categoría solo.</i>

**2b. Propagate the "logged by ≠ paid by" disclaimer (pattern B, reuse Resúmenes' wording).**
In `history.html`, near the `Usuario` filter label:
> Quién lo cargó en la app, no necesariamente quién lo pagó.

In `index.html`, near "Ver gastos de:":
> (same sentence, shortened if space requires) "Quién lo cargó, no quién lo pagó."

### Batch 3 — Medium

**3a. Dashboard currency selector scope (pattern B).**
Next to the "Moneda:" hero selector:
> Cada moneda se calcula por separado — nunca se suman entre sí.

**3b. Cambios first-use explainer (pattern C, reuse `telegram_link.html`'s structure).**
Conditioned on an empty history, above the form:
> Acá registrás cuando cambiás una moneda por otra — por ejemplo, vendiste dólares y recibiste
> pesos. No es un gasto: es una conversión, y sus datos alimentan el equivalente de referencia
> que ves en Resúmenes.

### Batch 4 — Lower priority / polish

- Shared-period tooltip on `_period_control.html`: "Este período se comparte con Movimientos,
  Ingresos, Fijos, Cambios y Resúmenes."
- Forecast card progressive disclosure (redesign, §4.1).
- "Tasa (recibida por entregada)" and "Lectura" label redesigns (§4.2, §4.3).
- Rename Dashboard's "Moneda:" to "Ver en:" (§4.4).
- File a follow-up ticket for the two hardcoded ARS/USD aggregation gaps (Ingresos summary
  tiles, Telegram `/gastos`) — a code fix, not a copy batch.

None of batch 4 blocks external onboarding; batches 1–3 meaningfully reduce the odds of a new
family misreading their own money in the first weeks.
