# UI/UX Specification — DB Risk & Rescue

**Status:** Finalized design system, pending implementation
**Source of truth mockup:** `route_card_redesign_v7.html` (iteration 7 of 7 — see §5 for the full decision trail)
**Companion documents:** `SPEC.md` (system architecture & risk-engine logic), `DATA_SPEC.md` (data schema)
**Scope:** This document governs everything the user sees and touches in the Streamlit app — the search form, the route comparison list, and the expanded itinerary. It does not cover the Monte Carlo engine, routing logic, or data pipeline; those are `SPEC.md`'s domain. Where a visual decision depends on a value the engine produces (e.g. risk-band thresholds), this document names the value and defers the computation to `engine.py` / `ui_components.py::classify_risk`.

---

## 1. Design Philosophy & Principles

### 1.1 Clean, minimalist, consumer-grade

The app is styled as a companion to — not a departure from — the tools a German rail traveler already uses daily. Every layout decision was checked against actual DB Navigator screenshots rather than invented in the abstract: the horizontal train bar, the swap button, the toggle-style sort control, and the tabular timetable are all deliberate echoes of that reference, adapted to carry information DB Navigator doesn't have (predicted arrival, transfer-miss probability). Apple Maps informs the restraint on iconography and color: information is carried by typography, spacing, and a small, consistent color vocabulary — never by decoration.

Concretely, this means:

- No skeuomorphism, no gradients-for-their-own-sake, no drop shadows beyond a subtle 1–2px card lift.
- Generous whitespace over dense information packing. A route card should be scannable in under two seconds.
- Every visual element earns its place by encoding a specific piece of data. If a component doesn't map to a value the engine produces, it doesn't belong on the card.

### 1.2 Strict "No Emojis" policy

No emoji or pictographic icon appears anywhere in the app shell — not ⚠️ for warnings, not 🚄 for trains, not ✅/🚨 for risk states. This was a deliberate, repeated correction during design review specifically because emoji read as informal and inconsistent with a data-science risk tool's credibility.

In place of emoji, every visual cue is built from one of three primitives:

| Primitive | Used for |
|---|---|
| **Typography** | Action-first risk wording (`Tight connection`) instead of an icon + vague label |
| **Geometry** | Filled/hollow circles for itinerary stops, solid/dashed lines for travel/transfer segments, a CSS triangle for the disclosure caret |
| **Color fields** | Soft background fills and left-edge strips carry risk-band meaning; category chips carry train-type meaning via solid fill, not a train pictogram |

This rule applies to every future component too: if a new UI need seems to call for an icon, the default answer is "find the typographic or geometric equivalent first," not "pick an emoji."

### 1.3 Action-oriented risk terminology

Numeric risk percentages are necessary but not sufficient — "20%" alone doesn't tell a rushed traveler whether that's fine or alarming. Every risk band therefore pairs a percentage with an action-first phrase that tells the user what to *do* with the number, not just what it *is*:

| Band | Phrase | Rationale |
|---|---|---|
| Low | `Safe connection` | Confirms the connection is comfortable — no anxiety-inducing language for the common case. |
| Medium | `Tight connection` | Signals attentiveness without alarm — this is a "keep an eye on it" band, not a "you're in trouble" one. |
| High | `Miss likely` | Deliberately blunt. This band exists specifically to override an over-optimistic reading of a scheduled time, so the wording must not soften the message. |

Earlier drafts used "Low risk / Medium risk / High risk" as standalone labels; this was rejected as *dangerously ambiguous* — a user skimming for "high" could easily misread it as "high chance of making the connection." The current wording removes that failure mode entirely: there is no reading of "Miss likely (38% risk)" that could be mistaken for reassurance. See §5 for the full rejection history.

The percentage always accompanies the phrase — never one without the other — and the phrase is always followed by the numeric buffer in minutes, so the full pattern is:

```
<Action phrase> (<X%> risk) · <Y> min
```

The buffer number carries no unit label of its own (no literal word "buffer") — "min" is sufficient once it follows a risk phrase that's already about a connection, and dropping the word keeps the line shorter without losing meaning. The buffer figure is also visually lighter (`font-weight: 400`) than the risk phrase (`font-weight: 700`) that precedes it — the phrase is the thing to read first, the minutes are supporting detail, not a second headline.

No station name, no extra clause, no second sentence. If the station needs identifying, it already appears immediately above the transfer bar in the itinerary — repeating it here was cut during review as pure redundancy.

---

## 2. Color Palette & Design Tokens

All tokens below are CSS custom properties in the reference mockup and should be lifted verbatim into `ui_components.py`'s color constants (see §4.4 for the reconciliation against the current, pre-redesign implementation).

### 2.1 Base tokens

| Token | Hex | Usage |
|---|---|---|
| `--ink` | `#161a20` | Primary text, headline numbers |
| `--muted` | `#68707c` | Secondary text (durations, labels, captions) |
| `--faint` | `#9aa1ab` | Tertiary text, dividers, disabled/inactive states |
| `--line` | `#e3e5e9` | Borders, hairline dividers |
| `--card-bg` | `#ffffff` | Card and search-panel background |
| `--page-bg` | `#f2f3f5` | App background behind all cards |
| `--db-red` | `#eb0016` | Brand accent only (header banner) — never used for risk signaling, to keep it visually distinct from the High-risk band |

### 2.2 Train category chips

Corrected against the official DB Navigator reference screenshot mid-review (an earlier draft had RE/RB wrong — see §5.4) and now considered final:

| Category | Background | Text | Border |
|---|---|---|---|
| **ICE / IC / EC** | `#33383f` (dark grey) | `#ffffff` (white) | none |
| **RE / RB** | `#d3d6db` (light grey) | `#20232a` (near-black) | `1px solid #b9bdc4` |
| **S-Bahn / S** | `#0f8a3f` (DB green) | `#ffffff` (white) | none |
| **U-Bahn** *(not yet in v7 mockup — carried forward from the pre-redesign token set, needs a design pass)* | `#003090` | `#ffffff` | none |

The RE/RB chip is the one category light enough to disappear against a white card, which is why it alone carries a 1px border — every other chip has enough contrast against `--card-bg` without one.

Chips render at two sizes from the same three category classes:

- **Large** (`.chip`, min-height 34px) — the collapsed-card horizontal train bar.
- **Small** (`.chip` + `.chip-sm` modifier, auto height, ~10.5px text) — inline next to a station name inside the expanded timetable.

### 2.3 Risk-level color bands

Each band is a soft fill + a saturated border/text accent, never a solid saturated fill (which would fight with the card's own white background and read as more alarming than intended, especially for Low/Medium):

| Band | Fill | Border / accent text | Used for |
|---|---|---|---|
| **Low / Safe** | `#eaf7f0` | `#1f9d55` (border) · `#116b38` (text) | Transfer bar background + left-edge card strip when the route's worst transfer is Low |
| **Medium / Tight** | `#fff5e3` | `#d98c1f` (border) · `#8a5300` (text) | Same, for Medium |
| **High / Risky** | `#fdeceb` | `#d63a30` (border) · `#a3231b` (text) | Same, for High |

The **left-edge card strip** (4px, `border-left`) is the single risk-color signal at the collapsed level — it is present whether the card is collapsed or expanded, and does not disappear when Details is opened (an earlier draft removed it on expand, which read as a jarring "double border → no border" flicker; see §5.5). The **transfer bar** inside the expanded itinerary carries its own soft-fill background but *no* left border of its own — one colored accent (the card edge) plus one soft-fill block (the transfer bar) is the maximum color signal on screen at any moment, never two competing borders.

A direct (0-transfer) route has no risk band to show and gets a neutral edge (`var(--line)`, i.e. no visible color) — there is nothing to flag, so nothing is flagged.

### 2.4 Card & container styling

| Property | Value |
|---|---|
| Card border radius | `10px` (route cards) / `12px` (search card — one step rounder to read as the "primary" container) |
| Card border | `1px solid var(--line)` |
| Card shadow | `0 1px 2px rgba(20,20,30,.04)` (route cards) / `0 2px 6px rgba(20,20,30,.06)` (search card, slightly more lift as the page's focal element) |
| Card left-edge risk strip | `4px solid`, color per §2.3 |
| Inter-card spacing | `12–14px` margin-bottom |
| Page container max-width | `1050px`, centered (`margin: 0 auto`) — applied to Streamlit's main block container so the app reads as a focused column instead of stretching edge-to-edge on ultra-wide monitors. The sidebar is unaffected; only the main content area is capped. |

---

## 3. Component Architecture

### 3.1 Search card — "Plan your trip"

A single bordered, shadowed container (§2.4) replacing four bare `st.columns()` widgets with no visual grouping. Internal structure:

```
┌─────────────────────────────────────────────────────────┐
│  Plan your trip                                          │
│                                                            │
│  ┌─────────────────┐   ⇄   ┌─────────────────┐           │
│  │ Origin           │       │ Destination      │           │
│  │ Frankfurt(Main)…▾│       │ Köln Hbf        ▾│           │
│  └─────────────────┘       └─────────────────┘           │
│  ─────────────────────────────────────────────────       │
│  ┌────────────────────┐  ┌────────────────────┐          │
│  │ Date                │  │ Departure at or aft │          │
│  │ 2026/08/24          │  │ 00:00               │          │
│  └────────────────────┘  └────────────────────┘          │
│  ─────────────────────────────────────────────────       │
│  Sort by                                                  │
│  ┌───────────────────────┐┌───────────────────────┐      │
│  │ Fastest scheduled      ││ Safest arrival   │      │
│  └───────────────────────┘└───────────────────────┘      │
└─────────────────────────────────────────────────────────┘
```

**Primary row — station inputs with swap.** A 3-column grid (`1fr 44px 1fr`) with `align-items: end`, so the circular swap button — 36px, white fill, 1px border, subtle shadow — lines up with the *bottom edge of the input boxes*, not the field labels above them. This matters: an earlier pass centered the button against the whole field including its label, which visually floated it half a row too high.

Streamlit's own `vertical_alignment="bottom"` column parameter drives this, but two CSS-level corrections were needed to actually deliver "36px circle precisely centered against the input box" rather than something close to it:

- A plain `height: 36px` on the button loses to Streamlit's own button `min-height` (which resolves larger), so the rule must carry `!important` on `width`, `height`, *and* `min-height` together — otherwise the button silently stretches into a 36×40px oval instead of a circle.
- `vertical_alignment="bottom"` lands the button's column flush against the *bottom* of the 40px input box (a `36px` button in a `40px` slot, bottom-flush, sits with all 4px of slack at the top — 2px too low to read as centered). A `margin-bottom: 2px` on the button nudges it up from flush-bottom to true center within that 40px box height, without disturbing the bottom-alignment mechanism that correctly excludes the field labels in the first place. An earlier attempt instead forced `display:flex` directly on the button's `[data-testid="stColumn"]` to re-implement centering from scratch — that backfired, because the forced flex container shrank to exactly the button's own height (36px) with no slack left to center *within*, so `align-items:center` had nothing to act on. The 2px nudge builds on the alignment Streamlit already gets right, rather than replacing it.

**Divider.** A single hairline (`1px solid var(--line)`) separates each of the card's three rows — no heavier visual break is needed anywhere in this card.

**Second row — Date and Departure time only.** A `1fr 1fr` grid holding just these two fields. Date only renders when the active data source has a real calendar (the DuckDB warehouse phase); the two JSON-backed phases have no calendar concept and simply omit that field, leaving Departure time alone at full card width. Sort used to share this row too, but three fields — one of them a two-option pill — left Date and Departure time visibly cramped and the pill undersized; giving this row exactly two fields is what actually fixes that, not further padding tweaks.

**Third row — Sort by, on its own.** A single full-width segmented pill (`st.segmented_control(..., width="stretch")`), dark-fill active state (`var(--ink)` background / white text) rather than a Streamlit radio's bare dots, mapping onto the same two values the backend already accepts: `"Fastest scheduled"` / `"Safest arrival"`. Giving Sort its own row — rather than a cramped third column — is what lets it stretch to a comfortably-sized full-width control instead of the undersized pill the three-field version produced. It sits below its own divider, exactly like the primary→second-row divider above it, so the card reads as three clearly separated, equally-weighted sections rather than one dense block.

Streamlit's segmented-control pill still renders at a shorter natural height (32px) than the Date/Departure-time input boxes (40px) one row up; the pill's own `min-height` is forced to 40px so it doesn't look visually lighter than the fields above it, even though it no longer needs to share a baseline with them in the same row.

**Card bottom padding.** The card's own bottom padding is `20px` (matching the `18px` top padding, not the earlier `6px` that let the last row's content crowd the card's rounded bottom edge) — with Sort now the last element in the card, this is what keeps the segmented pill from touching the card boundary.

Every field in this card maps 1:1 to an existing backend input — origin station ID, destination station ID, optional service date, departure time, sort order. Nothing here is decorative; the redesign is entirely about grouping and hierarchy, not new capability.

### 3.2 Route cards — collapsed view

```
┌──────────────────────────────────────────────────────────┐
│ 21:20 – 02:19                 ┌───────────────────────┐   │
│ 4h 59m · 1 transfer           │      PREDICTED ARRIVAL │   │
│                                │ Expected 02:43 +24m ┊ Safest 03:19 +60m │
│                                └───────────────────────┘   │
│ [────── ICE 22 ──────][── ICE 42 ──────────────────────]  │
│ Frankfurt(Main)Hbf                             Köln Hbf   │
│ ─────────────────────────────────────────────────────────│
│                        Details ▾                          │
└──────────────────────────────────────────────────────────┘
```

**Hero header.** Left side: scheduled departure–arrival as one bold, tabular-numeral time range, with duration and transfer count as a small muted line beneath — purely factual, sourced straight from the timetable, never adjusted for predicted delay. Right side: the Predicted Arrival panel (§3.3).

**Train bar.** A flexbox row of category-colored chips (§2.2), one per leg, each `flex`-sized roughly proportional to that leg's share of total travel time (with an enforced minimum width so a short leg's chip stays legible). Between legs at a transfer point, a short dashed gap — reusing the same dashed = "not currently on a train" convention as the itinerary's connector lines (§3.3) — stands in for what would otherwise be a walking pictogram. This bar is the single most direct lift from the DB Navigator reference: full-width, dominant, immediately under the hero header.

**Station row.** Origin and destination names, left/right-aligned beneath the train bar.

**Details toggle.** Centered, muted, with a small CSS-triangle caret — see §4 for the underlying `<details>`/`<summary>` mechanism.

### 3.3 Predicted Arrival panel

A single bordered box (not two separate floating numbers) grouping the two values a Monte Carlo engine actually adds over a static timetable:

- **Expected** (mean of the simulated arrival distribution) — de-emphasized: `--muted` colored, weight 500, 13px. This is context, not the headline.
- **Safest** (P85 of the simulated distribution) — emphasized: `--ink` colored, weight 800, 17px, and its `Safest` label itself is bumped to `--ink`/weight 700 too (`Expected`'s label stays the ordinary muted/600 caption style). Separated from Expected by a thin dashed rule.

The two are deliberately *not* equal-weight siblings. Safest (P85) is the number this app exists to surface — it's the one figure a rushed traveler should actually plan around — so it reads as the headline recommendation: bolder, larger, darker. Expected (the mean) is useful context for judging spread but isn't the number to act on, so it recedes rather than competing for attention. Grouping them still lets a user read the *spread* at a glance (e.g. Expected +24m vs. Safest +60m signals real uncertainty; Expected +9m vs. Safest +15m signals a fairly reliable leg) — which is the actual value proposition of running simulation instead of showing one static number — but the typography now makes clear which of the two numbers is the recommendation and which is supporting detail. Scheduled time is deliberately *not* repeated in this panel — it already anchors the hero header on the opposite side of the card, so the reading order is a clean left-to-right progression: **Scheduled** (hero, factual) → **Expected** (panel, muted context) → **Safest** (panel, bold recommendation).

**Layout is responsive to available width, not fixed.** By default Expected and Safest sit side-by-side, divided by a vertical dashed rule — since stacking them vertically on a normally-wide route card leaves the box taller than its content needs and reads as wasted space. Only under a narrow viewport (≤480px, effectively mobile) does the panel fall back to a vertical stack with a horizontal dashed rule between rows, because there isn't enough width left after the card's own padding to keep two value blocks legible side-by-side. The breakpoint is a plain CSS media query on viewport width, not a container query against the card itself — simpler to reason about and to test, and the card's width already tracks the viewport closely enough (especially now that the page itself is capped at 1050px, §2.4) that the distinction rarely matters in practice.

**Each row is a single nowrap line — name, value, and delta never break independently of each other.** The first side-by-side pass stacked each row's label above its value+delta (`Expected` on one line, `02:43 +24m` on the next), which meant the value+delta pair — itself two adjacent inline spans with no `white-space` rule — could still wrap onto two lines whenever the panel's `auto`-sized grid track came out narrower than expected (e.g. a route with a 3-digit delta like `+139m`), producing a ragged, uneven box. The fix collapses each row to one flex line (`flex-direction: row`, `white-space: nowrap` on the row and on the value/delta spans individually) so `Expected 21:15 +139m` and `Safest 23:11 +255m` each render as one unbreakable unit; the panel's width then comes purely from that content (the fixed `min-width: 158px` floor was removed as unnecessary now that nowrap does the real work), which is also why the panel no longer needs a generous minimum width to avoid looking cramped.

Neither value uses strikethrough. Strikethrough time formatting is reserved (by DB's own convention and by web convention generally) for *live, real-time delay* — a train that has already departed and is now known to be running late. This app's numbers are pre-departure forecasts from a simulation, not live tracking data, and using strikethrough would misrepresent that distinction to the user.

### 3.4 Expanded itinerary — CSS Grid timetable

The expanded Details view is one continuous CSS Grid (`grid-template-columns: 42px 20px 1fr` — time / connector / station), not a series of independently-positioned elements. This is a structural requirement, not a cosmetic preference: an earlier draft used a separate grid per leg with the transfer bar as a sibling `<div>` positioned via a hand-tuned `margin-left`, and that offset silently drifted out of alignment whenever row heights changed. Putting every row — including the transfer bar — through the *same* grid instance, in the *same* column definition, makes misalignment structurally impossible rather than something to keep re-checking by eye.

**Columns:**

1. **Time** — tabular-numeral, right-aligned within its column, one per stop.
2. **Connector** — a vertical line built from flexbox (a dot at the top of each row plus a line that fills whatever space remains in that row), *not* absolutely-positioned pixel offsets. The flexbox approach is what lets the connector correctly reach through a transfer-bar row even though that row is taller than a normal stop row — a fixed-offset version breaks the moment row heights aren't uniform.
3. **Station / content** — station name + inline train-category chip (small size, §2.2) for a leg's origin stop; station name alone for an arrival/transfer stop; the transfer warning bar itself for the transfer row.

**Dot states:**

- **Filled, solid ink** — a scheduled stop on the leg currently being ridden (origin or a through-stop).
- **Hollow ring** — an arrival at a transfer/interchange point, or the final destination.

**Line states:**

- **Solid** — actively riding a train between two stops.
- **Dashed** — the transfer/waiting gap between an arrival and the next departure. This begins at the hollow dot, continues through the transfer-bar row (which has no dot of its own, just a connector segment passing through), and ends where the next solid dot begins.

**Transfer warning bar.** Occupies the *station* column of its own grid row — a real grid cell, not a positioned overlay — so it inherits the grid's alignment automatically. It carries a soft background fill per its risk band (§2.3) and no left border of its own (the card's edge strip is the only border-based risk signal — see §2.3's rationale). Content is the single-line wording pattern from §1.3: `<Action phrase> (<X%> risk) · <Y> min`.

---

## 4. Technical Implementation Notes

### 4.1 `<details>` / `<summary>` over `st.expander`

The Details/Hide-details toggle on every route card must be implemented as native HTML, not Streamlit's built-in expander:

```html
<details class="itin-details">
  <summary>
    <span class="lbl-closed">Details</span>
    <span class="lbl-open">Hide details</span>
    <div class="caret"></div>
  </summary>
  <!-- expanded itinerary grid (§3.4) -->
</details>
```

Rendered via a single `st.markdown(card_html, unsafe_allow_html=True)` call per route card, where `card_html` is one assembled string containing the whole card — hero header, predictions panel, train bar, station row, and the `<details>` block.

**Why this, specifically:**

- `st.markdown(..., unsafe_allow_html=True)` injects HTML directly into Streamlit's own page DOM (unlike `st.components.v1.html`, which sandboxes content inside an iframe), so a native `<details>` element behaves exactly as it would on any other page — no special handling needed.
- Toggling `<details>` is pure client-side DOM state. It triggers **no Streamlit rerun** — no server round-trip, no risk of scroll position or other widgets' state jumping when a user opens one card's details. An `st.expander` or any `st.session_state`-driven toggle reruns the entire script on every open/close, which is unnecessary cost for something purely presentational.
- `st.expander` renders its own container with Streamlit's own padding, border, and header chrome, which cannot be fully restyled without targeting internal DOM classes Streamlit does not guarantee to keep stable across versions. `<details>` hands full control of the header row to this spec's CSS instead.

**Known, accepted limitation:** native `<details>` expands instantly with no built-in smooth height animation (cross-browser CSS-only techniques for animating `<details>` height remain inconsistent). This is accepted as-is — the snap feels closer to DB Navigator's own accordions than a slow slide would, and avoiding extra JS keeps the implementation inside a single `unsafe_allow_html` string.

### 4.2 Required CSS overrides

Two overrides are required to suppress the browser's default disclosure marker and drive the custom caret instead:

```css
.itin-details > summary { list-style: none; cursor: pointer; }
.itin-details > summary::-webkit-details-marker { display: none; }  /* Chrome/Safari */
.itin-details > summary::marker { content: ""; }                     /* Firefox */

.itin-details > summary .caret {
  width: 0; height: 0;
  border-left: 4px solid transparent;
  border-right: 4px solid transparent;
  border-top: 5px solid var(--faint);
  transition: transform .15s ease;
}
.itin-details[open] > summary .caret { transform: rotate(180deg); }

.itin-details > summary .lbl-open   { display: none; }
.itin-details[open] > summary .lbl-closed { display: none; }
.itin-details[open] > summary .lbl-open   { display: inline; }
```

The `[open]` attribute selector — not JavaScript — drives both the caret rotation and the "Details" ↔ "Hide details" label swap.

### 4.3 Station swap control (search card)

The `⇄` button must mutate `st.session_state` for both selectbox keys *before* those selectboxes are instantiated in the same script run, which is the standard, documented Streamlit pattern for widget state mutation via a button:

```python
if st.button("⇄", key="swap_stations"):
    st.session_state["origin_select"], st.session_state["destination_select"] = (
        st.session_state.get("destination_select", default_destination_id),
        st.session_state.get("origin_select", default_origin_id),
    )

origin_id = st.selectbox("Origin", options=station_ids, index=..., key="origin_select")
destination_id = st.selectbox("Destination", options=station_ids, index=..., key="destination_select")
```

Column placement in `st.columns([5, 1, 5])` can be declared in any order in code — Streamlit lays out each column's contents independently, so the swap button's column can be written first or last without affecting the left/right visual position of Origin and Destination.

### 4.4 Reconciliation against the current implementation

`ui_components.py` predates this spec and does not yet match it. The following changes are required when implementation begins (tracked here so this document stays the actionable checklist it's meant to be, not just a picture):

| Area | Current state | Target (this spec) |
|---|---|---|
| Emoji | `⚠️` in the transfer bar, `🚄` in leg headers, `🚆` in the header banner | Remove all three per §1.2 |
| Risk wording | `"{X}% risk of miss"` + separate `"Transfer at {station}"` clause + a 3-item color legend below the itinerary | Single line per §1.3; legend removed as redundant once wording is self-explanatory |
| RE/RB chip color | `#6C757D` medium grey (was incorrectly modeled on a wrong reference at one point) | `#d3d6db` light grey / `#20232a` text / 1px border, per §2.2 |
| Search inputs | Four bare `st.columns()`, no visual grouping, no swap control | Bordered search card per §3.1 |
| Predicted Arrival | One grey caption line: `"Scheduled: X · Expected: Y · Safest: Z"` | Grouped panel per §3.3, with Scheduled staying in the hero header instead |
| Details toggle | `st.expander("Details")` | Native `<details>`/`<summary>` per §4.1 |
| Card left-edge risk strip | Not present — `st.container(border=True)` gives a uniform, uncolored border | 4px colored strip per §2.3, present in both collapsed and expanded states |
| Timetable layout | `st.markdown` with independent `<div>` rows, transfer bar positioned via margin | Single continuous CSS Grid per §3.4 |

Until this checklist is worked through, treat the running app as *not yet reflecting this spec* — this document describes the target, not the current build.

---

## 5. Design History & Rejected Alternatives

Kept for context — several of the rules above exist specifically *because* an earlier approach was tried and didn't hold up. Future changes to this spec should check whether they're about to reintroduce one of these.

1. **Standalone risk-color legend.** An early draft included a 3-item Low/Medium/High color-dot legend beneath every expanded itinerary. Removed once the risk wording became self-explanatory (§1.3) — a legend explaining colors nobody needs explained is pure clutter.
2. **Ambiguous "Low/Medium/High risk" labels.** Rejected as a standalone label (without an action phrase) because "High risk" alone doesn't specify risk *of what* — a skimming reader could misread it as high chance of success. Action-first phrasing (§1.3) closes that gap.
3. **RE/RB chip colored red.** An early pass modeled RE/RB as a red-filled chip. Checked against an actual DB Navigator screenshot and corrected to light grey / black text (§2.2) — red was simply wrong, not a stylistic choice.
4. **Live-delay strikethrough for Scheduled vs. Expected.** An early hero-header design struck through the scheduled time next to the predicted one, mimicking how DB Navigator shows a train that's *currently* running late. Rejected because this app's numbers are pre-departure Monte Carlo forecasts, not live tracking — strikethrough would misrepresent a prediction as a confirmed delay. Resolved by giving Expected/Safest their own grouped panel (§3.3) instead of overlaying them on the scheduled time.
5. **Left-edge risk strip removed on expand.** An early version dropped the card's colored left border specifically when Details was opened, on the theory that the internal transfer bar's own color made the outer border redundant. In practice this produced a jarring color-then-no-color flicker on every expand/collapse. Resolved by making the left-edge strip permanent (§2.3) and instead removing the transfer bar's *own* left border, so exactly one color signal is visible at any given time rather than zero or two.
6. **Horizontal train bar removed in favor of per-stop chips only.** One iteration dropped the full-width train bar entirely, reasoning that the expanded timetable's inline chips made it redundant. Reintroduced after review — the bar is what a collapsed (unopened) card uses to communicate "how many legs, roughly how long each" at a glance, which the timetable can't do since it's hidden until Details is opened.
7. **Absolutely-positioned timetable connectors and transfer-bar margins.** Superseded by the single-grid approach in §3.4 after repeated alignment bugs — see §4 for why the grid-based approach is now a hard requirement, not a preference.
8. **Swap button vertical alignment — three passes to get right.** (a) The first implementation trusted `vertical_alignment="bottom"` on the station-row columns alone; the button's own `height: 36px` lost to Streamlit's internal button `min-height`, stretching it into a 36×40px oval that no longer read as centered. (b) The next pass fixed the oval (`!important` on `width`/`height`/`min-height`) but also forced `display:flex; align-items:flex-end` directly on the button's `[data-testid="stColumn"]`, reasoning that column-level alignment shouldn't be trusted to reach through the button's nested wrapper markup — this produced a correctly-shaped, bottom-flush button, but "flush with the bottom of the box" isn't the same as "centered against the box," and a forced-flex column that shrinks to exactly its content's height leaves no slack for `align-items:center` to act on if that's tried instead. (c) The final fix drops the column-level override entirely — Streamlit's native bottom-alignment was never the problem, it correctly excludes the field labels — and adds a precise `margin-bottom: 2px` on the button itself, nudging a 36px circle up from flush-bottom-in-a-40px-box to truly centered within it. The lesson across all three passes: fix the *specific* pixel gap with the smallest correction that closes it, rather than re-implementing a layout mechanism (like column alignment) that was already behaving correctly.
9. **Unbounded page width on wide monitors.** Pre-§2.4-fix, the app used Streamlit's `layout="wide"` with no cap, so on an ultra-wide monitor the search card and route cards stretched edge-to-edge — readable but visually thin and un-app-like. A `max-width: 1050px` cap on the main block container, centered, was added without changing `layout="wide"` itself (still wanted for the sidebar/main-content split); only the content column's own width is capped.
10. **Date, Departure time, and Sort crammed into one three-field row.** The original secondary row put all three fields in a `1fr 1fr 1.4fr` grid. With Sort as a two-option segmented pill sharing that row, none of the three fields had enough width to breathe — Date and Departure time felt cramped and Sort's pill rendered undersized. Resolved by splitting into two rows (§3.1): Date + Departure time keep a dedicated `1fr 1fr` row, and Sort moves to its own full-width row below a second divider. This is a "give it its own row," not a "shrink the padding further" fix — the row was structurally too crowded no matter how the spacing tokens were tuned.
11. **Predicted Arrival rows stacking label-over-value within each side-by-side column.** The first side-by-side pass (entry after §3.3 first shipped) put `Expected`/`Safest` above their own `value + delta` pair within each column. That inner pair had no `white-space: nowrap`, so a wide delta (`+139m`, `+255m`) could still wrap onto a second line inside an already-narrow `auto`-sized panel, producing a ragged box despite the side-by-side fix. Resolved by making each row one nowrap flex line — name, value, and delta together — so the row's content, not a fixed minimum width, determines the panel's size.
12. **Expected and Safest rendered at equal type weight.** Once the wrapping bug (#11) was fixed, both sides still used the same `font-weight: 700` / `color: var(--ink)` for their values — only `font-size` (13px vs. 16px) distinguished them, which read as "two same-importance numbers, one slightly bigger" rather than "one recommendation, one supporting figure." Since Safest (P85) is the number the app is actually built to recommend, its typography was pushed further up (17px / weight 800 / `--ink`, plus its own name label promoted to `--ink`/weight 700) while Expected was pushed down (13px / weight 500 / `--muted`) — turning a size-only distinction into a clear emphasized-vs-context hierarchy.
13. **"buffer" as a literal label on the transfer warning bar.** The original pattern (§1.3) rendered the buffer minutes as `<Y>m buffer` — e.g. `40m buffer`. Once the risk phrase itself already frames the line as being about a connection (`Tight connection (20% risk) · 40m buffer`), the word "buffer" is redundant with that framing and the abbreviated unit (`m`) reads as terser than necessary for a figure that isn't competing for space elsewhere on the bar. Simplified to `<Y> min` (e.g. `40 min`), with the figure's `font-weight` set explicitly to `400` (rather than left to inherit) so it stays visibly lighter than the risk phrase's `font-weight: 700` regardless of any future change to `.transfer-bar`'s own default weight.

---

*This document is the frontend counterpart to `SPEC.md`. Any UI change that isn't a pure bugfix should update this file in the same review as the code change.*
