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

| Band (displayed color) | Phrase | Rationale |
|---|---|---|
| Low (Green) | `Safe connection` | Confirms the connection is comfortable — no anxiety-inducing language for the common case. |
| Medium (Yellow), genuine | `Tight connection` | Signals attentiveness without alarm — this is a "keep an eye on it" band, not a "you're in trouble" one. |
| Medium (Yellow), Impact Override | `Recoverable miss` | A base-Red transfer (`P(miss)` > 30%, `SPEC.md` §3.1) downgraded to Yellow because its precomputed fallback impact is ≤15 min (`SPEC.md` §5.3). Deliberately *not* the same phrase as genuine Medium: the miss probability really is high, and the wording says so plainly ("miss") rather than pretending the connection is merely tight — "recoverable" is what explains the yellow instead of red, not a softened read of the odds. |
| High (Red) | `Miss likely` | Deliberately blunt. Reserved for base-Red transfers where the Impact Override does *not* apply — a real, costly miss, not just a high-probability one. |

Earlier drafts used "Low risk / Medium risk / High risk" as standalone labels; this was rejected as *dangerously ambiguous* — a user skimming for "high" could easily misread it as "high chance of making the connection." The current wording removes that failure mode entirely: there is no reading of "Miss likely (38% risk)" that could be mistaken for reassurance, and "Recoverable miss (37% risk) · arrives 08:51 if missed" doesn't read as "this is fine, ignore it" either — it says a miss is likely *and* names the reason it's not urgent, in one line.

**The trailing figure's meaning splits along the base probability band, not the displayed color.** `Safe connection` and `Tight connection` — both base-Low/Medium — are followed by the transfer's scheduled buffer (`Transfer.scheduled_buffer_minutes`, `SPEC.md` §2.4), an unlabeled, unsigned shape:

```
<Action phrase> (<X%> risk) · <Y> min
```

The connection is expected to hold in both bands, so "how much slack does it have" is the relevant question, and the buffer answers it directly.

**Any base-Red transfer — `Miss likely` *and* a downgraded `Recoverable miss` alike — shows the fallback's arrival clock time instead:**

```
Miss likely (<X%> risk) · arrives <HH:MM> if missed
Recoverable miss (<X%> risk) · arrives <HH:MM> if missed
```

where `<HH:MM>` is the fallback route's own absolute arrival (`route.scheduled_arrival + impact_minutes`, `SPEC.md` §3.4/§5.3). The reasoning is the same for both: once base probability is Red, the connection is *expected* to fail, so "how much slack does it have" stops being the useful question — a base-Red transfer's own buffer is by definition tight, so printing it just restates "yes, this is risky" without saying what happens next. What actually matters at that point is the consequence, which the Impact Override's yes/no doesn't change — it only decides whether that consequence is cheap enough to soften the color, not whether it's the relevant number to show. An earlier draft applied this only to `Recoverable miss`, leaving `Miss likely` on the plain buffer figure; caught in review because the same "the buffer is uninformative once probability is Red" argument that justified the fix for `Recoverable miss` applies just as much, if not more, to a transfer that *isn't* recoverable (§5, history #19).

Three earlier drafts of this figure tried relative deltas instead of an absolute time — first a bare `· 12 min` reusing the Safe/Tight shape exactly, then `· +12 min if missed` / `· on time if missed` once that was found indistinguishable from a scheduled buffer — and all three left a question a relative number can't answer on its own: *relative to what?* (The route's own Scheduled Arrival — not the Monte Carlo Expected or Safest figures shown elsewhere on the same card, which is a real, easy thing to guess wrong.) An absolute clock time sidesteps the question entirely: it's self-evidently comparable to the Scheduled Arrival already printed in the card header, with nothing left to infer. It also collapses the old zero/negative special case for free — a harmless (`impact_minutes = 0`) fallback shows the *exact same* clock time as the header's Scheduled Arrival, reading as "no cost" without dedicated wording like the old "on time." See §5, history entries #16–#19 for the full trail.

The figure carries no separate unit label and stays visually lighter (`font-weight: 400`) than the risk phrase (`font-weight: 700`) that precedes it, in every band — the phrase is still the thing to read first, the figure is supporting detail either way.

No station name, no extra clause, no second sentence. If the station needs identifying, it already appears immediately above the transfer bar in the itinerary — repeating it here was cut during review as pure redundancy.

### 1.4 MCT-driven wording: five phrases, no separate MCT text

`SPEC.md` §3.6 enforces a station's Minimum Connection Time (MCT) as a gradient floor on a transfer's miss probability, independent of that transfer's delay-distribution history — a 0-minute "connection" at a 10-minute-MCT hub is flagged as near-certain even when the upstream line has never once run late. This section governs how that floor's *effect* — not its cause — reaches the passenger. It never does, in words: no MCT-specific caption, no acronym, no second clause. `TransferRisk.below_mct` only ever does one of two things, gated by a single test — **does knowing the cause change what the passenger should do?**

| `below_mct` effect | Where it applies | Test that justifies it |
|---|---|---|
| **Band floor** — a below-MCT connection can never display as `Safe connection`; it's folded into `Tight connection` instead, silently, with the same plain `<Y> min` figure a genuinely medium-risk connection gets | Low tier only | No — "don't dawdle" is the right action either way, so no distinct wording is needed |
| **Phrase fork** — a below-MCT connection with no rescuing fallback reads as `Unrealistic transfer`, not `Miss likely` | High tier, no Impact Override only | Yes — whether an on-time train is enough to save you genuinely differs by cause |

Two things follow directly from the "does it change the action" test:

**The Impact Override still wins over the phrase fork.** A below-MCT transfer with a fallback plan cheap enough to trigger the Override (`SPEC.md` §5.3, ≤15 min) keeps the reassuring `Recoverable miss` phrasing exactly as it would without MCT involved — unforked, no caption, no MCT mention anywhere. The practical fact ("a cheap backup exists") outranks the diagnostic one ("this specific connection is physically implausible") whenever both are true — see §5, history #20–#22.

**The band floor never gets its own words, only at the low tier.** A connection just 1 minute short of MCT reads as plain `Tight connection (<X%> risk) · <Y> min` — identical in every visible way to a genuinely medium-risk connection with no MCT involvement at all. This is deliberate, not a missed opportunity: an earlier draft appended a `below <N> min MCT` caption to every band this floor touched, and a later draft tried a dedicated `Rushed connection` phrase for exactly this case. Both were cut — see §5, history #22 for why.

`Unrealistic transfer` deliberately reuses the exact Red color band and figure format (§2.3's High/Risky tokens, and the same fallback arrival-clock-time figure as `Miss likely`, §1.3) — only the headline phrase differs. No new color, no new figure format, no new emoji (§1.2 still applies) — the wording alone carries the distinction, and only where the distinction is actionable.

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
| **Low / Safe** | `#eaf7f0` | `#1f9d55` (border) · `#116b38` (text) | Transfer bar background when that transfer's Local Risk band is Green; left-edge card strip when the route's Global Health band is Green |
| **Medium / Tight** | `#fff5e3` | `#d98c1f` (border) · `#8a5300` (text) | Same, for Yellow |
| **High / Risky** | `#fdeceb` | `#d63a30` (border) · `#a3231b` (text) | Same, for Red |

**Red covers two distinct causes, one color.** The High/Risky band's headline phrase is not always `Miss likely` — a transfer flagged by the MCT gradient floor with no rescuing fallback reads as `Unrealistic transfer` instead (§1.4, `SPEC.md` §3.6). The fill/border tokens above are identical either way; only the headline text differs.

**Two independent metrics, one shared palette.** Under the Impact-Weighted UI Thresholds rules, the transfer bar and the card edge strip no longer derive from the same value. The transfer bar reflects **Local Risk** — that specific transfer's base miss probability, downgraded from Red to Yellow when the precomputed fallback impact is ≤15 min (`SPEC.md` §5.3, §3.4). The card edge strip reflects **Global Health** — the route's P85 penalty against fixed 30/60-minute thresholds (`SPEC.md` §5.2), entirely independent of any individual transfer's probability. Both use the same three-color palette and fill/border tokens for visual consistency, but a card can legitimately show a Green edge with a Yellow transfer inside it (a cheap fallback already covers the miss) or a Yellow edge with every transfer Green (ordinary delay variance alone pushes P85 past 30 min) — neither is a bug.

The **left-edge card strip** (4px, `border-left`) is the single risk-color signal at the collapsed level — it is present whether the card is collapsed or expanded, and does not disappear when Details is opened (an earlier draft removed it on expand, which read as a jarring "double border → no border" flicker; see §5.5). The **transfer bar** inside the expanded itinerary carries its own soft-fill background but *no* left border of its own — one colored accent (the card edge) plus one soft-fill block (the transfer bar) is the maximum color signal on screen at any moment, never two competing borders.

**Behavior change — direct routes are no longer exempted.** Because Global Health is computed purely from the P85 penalty and ignores transfer count entirely, a single-leg (0-transfer) route with meaningful schedule-level delay variance now gets colored — a direct ICE with a fat delay-bucket tail can legitimately show Yellow or even Red. This reverses the earlier rule, which tied the edge strip to worst-transfer risk and had nothing to color on a transfer-free route, so it fell back to a neutral edge (`var(--line)`). See §5.14 for the history of this change.

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
│ Frankfurt(Main) Hbf                            Köln Hbf   │
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
14. **Card left-edge strip tied to worst-transfer probability.** The original design colored the left-edge strip by the route's worst individual transfer risk band, with direct (0-transfer) routes exempted with a neutral edge since there was no transfer to grade. Replaced by the Impact-Weighted UI Thresholds update: the edge strip now reflects Global Health, computed purely from the P85 penalty (`SPEC.md` §5.2) independent of transfer count or individual transfer probabilities — including direct routes, which are no longer exempted (§2.3). Driven by a data-grounded threshold sweep over real sampled routes showing that (a) ordinary schedule delay variance alone routinely pushes P85 past any probability-derived threshold tight enough to be useful at the transfer level, so reusing that threshold for the card edge over-flagged safe routes, and (b) 37% of individually Red (>30% miss) transfers turned out to have a real fallback route costing ≤15 minutes — a "harmless miss" the pure-probability transfer strip couldn't distinguish from a genuinely costly one. Splitting into two independently-thresholded metrics (Local Risk with an Impact Override, §5.3; Global Health on P85 penalty, §5.2) fixed both problems without touching the underlying Monte Carlo or fallback-search logic.
15. **Reusing "Tight connection" for Impact-Overridden transfers.** The first pass at wiring the Impact Override (#14) into the transfer-strip wording just let a downgraded transfer fall through to the existing Medium phrase, so a >30%-probability transfer with a cheap fallback would render as `Tight connection (37% risk) · 12 min` — identical wording to a genuine 15%-probability Medium transfer, with the buffer-minutes figure in a slot readers already associate with "time available," not "cost if missed." Caught in review specifically because the percentage and the phrase told two different stories with nothing bridging them: a rushed reader has no way to tell "this is comfortably tight" apart from "this is statistically a likely miss, but here's why it's not urgent" — exactly the ambiguity §1.3's original Low/Medium/High rewrite was supposed to have eliminated. Fixed by giving the override its own phrase, `Recoverable miss` (§1.3), and swapping what the trailing minutes figure means for that band specifically — the precomputed fallback impact (`impact_minutes`) instead of the scheduled buffer — so the line reads as one coherent claim: high probability, named as a miss, with the number that explains why it's shown yellow instead of red.
16. **A bare `0 min` on the Impact Override band.** First live test against real corridor data surfaced `Recoverable miss (38% risk) · 0 min` — a transfer whose fallback plan happened to land at exactly the route's original scheduled arrival, giving `impact_minutes = 0`. Flagged in review as reading like a rendering bug rather than the actual best case the Impact Override exists to surface (a real, likely miss that costs literally nothing). The other three bands never hit this ambiguity because a scheduled buffer is always a positive count — `impact_minutes` uniquely can land at or below zero. First fix: replace `impact_minutes ≤ 0` with the word `on time`, reusing the phrase `_delay_label` already uses elsewhere in the app for the same condition. Superseded by #17 below once the deeper issue surfaced.
17. **`Recoverable miss`'s figure sharing its exact format with the buffer-minutes bands.** Once `0 min` (#16) was fixed, a follow-up read of the actually-rendered card raised a broader question: even a well-behaved positive number, `Recoverable miss (38% risk) · 12 min`, is formatted identically to `Tight connection (15% risk) · 6 min` — same shape, same unit, same position — despite meaning the opposite kind of thing (fallback cost vs. scheduled slack). Nothing in the line itself signals the switch; recovering the correct meaning required already having read `SPEC.md` §3.4/§5.3. Combined a `+` prefix (borrowing the Predicted Arrival panel's own delta vocabulary, `+6m`/`+15m`) with a spelled-out `if missed` suffix: `+<Y> min if missed` (or `on time if missed` at the §1.3 floor). Superseded by #18 below once this still left an unanswered question.
18. **A relative delta can't answer "relative to what?" on its own.** Testing #17's fix (`Recoverable miss (38% risk) · +12 min if missed`) prompted the obvious next question: is that 12 minutes measured against the route's Scheduled Arrival, the Expected (mean) prediction, or the Safest (P85) prediction — all three of which appear elsewhere on the same card? The phrase gave no way to tell without already knowing the spec (it's the Scheduled Arrival, per `SPEC.md` §3.4/§5.3 — a pure timetable-vs-timetable comparison, deliberately not tied to the per-iteration Monte Carlo values so the fallback lookup stays O(1)). No amount of wording tweaking closes that gap for a *relative* number, since "relative to what" is exactly the information a delta omits by construction. Replaced the delta with the fallback's own absolute arrival clock time instead (`route.scheduled_arrival + impact_minutes`, formatted `HH:MM`) — self-evidently comparable to the Scheduled Arrival already printed in the card header, with nothing left to infer. This also retired the `on time` special case from #16/#17 for free: a 0-minute impact now shows the identical clock time as the header's own Scheduled Arrival, which reads as "no cost" without dedicated wording.
19. **The arrival-clock-time fix (#18) was applied only to `Recoverable miss`, leaving `Miss likely` on the plain buffer figure.** Caught in review by tracing the *reasoning* behind #16–#18 rather than just the wording: the argument that a base-Red transfer's buffer is uninformative ("by definition tight, so it'd just restate 'this is risky'") is a property of the base probability band being Red, not of the Impact Override having fired. `Miss likely` is base-Red by definition — the Impact Override's absence is exactly what keeps it Red — so the same argument applies to it at least as strongly, and arguably more so, since it's the one band describing a miss that genuinely *isn't* recoverable. Fixed by re-gating the figure's format on the base probability band (`classify_risk(miss_probability) == "high"`) instead of on `is_override`: both `Miss likely` and a downgraded `Recoverable miss` now show `arrives <HH:MM> if missed`; only genuine `Safe connection`/`Tight connection` (base Low/Medium) keep the buffer. The renamed helper (`_fallback_arrival_label`, formerly `_override_impact_label`) reflects that it's no longer specific to the override case.
20. **Minimum Connection Time (MCT): hard veto vs. gradient floor.** Once the base route's own transfers were found to bypass MCT entirely (a 3-minute transfer at a 10-minute-MCT hub could render as `Safe connection` purely because the line was historically punctual — `SPEC.md` §3.6 didn't exist yet at that point), three enforcement strategies were weighed: (a) lowering the hub/standard MCT thresholds themselves (10→5, 5→3) to make a hard veto less aggressive, (b) a soft/gradient miss-probability floor instead of a hard veto, or (c) shipping the original 5/10 thresholds with a literal `miss_probability = 1.0` veto and documenting the resulting false-positive risk as a known v1 limitation. (a) was rejected: the station-tier MCT classifier (`gtfs_ingest.classify_station_mct`, `SPEC.md` §3.6.1) is already only a trip-touch-count proxy for interchange complexity, not real platform geometry, so shrinking the threshold doesn't add information — it just moves which transfers get caught. (c) was rejected because it reintroduces, at 100% certainty instead of a softer number, exactly the "kill a perfectly good cross-platform transfer" failure mode a hard threshold already risks — compounding two layers of uncertainty (an approximate MCT value, treated as a binary fact) into a maximally confident, possibly-wrong claim. (b) was chosen, but refined beyond a flat floor (e.g. a flat 85%) into a *linear gradient* scaling from 0 (buffer at MCT) to 0.95 (buffer at/below 0 minutes, `SPEC.md` §3.6.2): a flat floor right at the MCT boundary reintroduces the same cliff-edge problem the hard veto had, just at a lower height — a buffer 1 minute short of MCT would jump straight to 85%, identical to a buffer of 0 minutes. The gradient means a buffer barely under MCT barely moves the risk, while one far under dominates it. The floor was additionally required to be enforced *inside* `simulate_route`'s own per-iteration loop (via an extra Bernoulli draw, `SPEC.md` §3.6.3), not just applied to the displayed `miss_probability`, once patching only the analytic number was recognized as leaving the simulated ETA telling a different story than the risk badge beside it (e.g. `Expected: on time` next to `Unrealistic transfer (95% risk)`).
21. **`Unrealistic transfer` vs. reusing `Miss likely`/`Recoverable miss` for MCT-driven risk.** Once the gradient floor (#20) could push a transfer's `miss_probability` into the base-High band independent of delay history, the question became whether the existing phrases (§1.3) still fit that cause. Reusing `Miss likely` verbatim was rejected: it would tell a rushed reader "the train is often late here," when the real reason is "even a perfectly on-time train doesn't leave enough physical time" — a materially different, more actionable fact, since no amount of the train running on schedule fixes it. A new, distinct phrase (`Unrealistic transfer`, §1.4) was added for exactly the base-High-and-not-rescued case, gated on `TransferRisk.below_mct` — engine-computed, not re-derived in the UI. The existing Impact Override (§1.3, `SPEC.md` §5.3) was *not* forked into a second, MCT-aware version: a below-MCT transfer whose fallback plan still lands within the Override's existing 15-minute threshold keeps reading as `Recoverable miss` — the practical, reassuring truth ("a cheap backup exists") outranks the diagnostic one ("this specific connection is physically implausible") whenever both are true at once. This entry originally paired the phrase fork with a standalone `below <N> min MCT` caption on every band the floor touched; that caption was cut in #22 below.
22. **Cutting the `below <N> min MCT` caption, and rejecting a dedicated `Rushed connection` phrase, in favor of one test: does the cause change the action?** Live review of #20/#21 surfaced two problems the caption itself created. First, `Safe connection · below 10 min MCT` read as self-contradictory — the headline said relax, the caption said warning, on the very same line. Second, `MCT` is raw engineering jargon appearing in passenger-facing text for the first time in this app, breaking both of §1.2's rules at once (no jargon; the wording alone should be self-explanatory, no legend needed) and §1.3's explicit "no extra clause, no second sentence" rule. A same-shaped replacement phrase, `Rushed connection`, was tried next for the Low/Medium tier — but caught as too close to `Tight connection` for a rushed reader to reliably tell apart, and worse, the same headline would need to render in two different colors (Green or Yellow) depending on the underlying statistical band, undermining "one phrase, one meaning" just as much as the jargon had. A further idea — replacing the plain buffer figure with a comparison (`8 of 10 min`) for exactly this case, mirroring how `Recoverable miss` already swaps its trailing figure to resolve a similar number/phrase mismatch (#17) — was also rejected: unlike `arrives <HH:MM> if missed`, which is self-explanatory on its own, `8 of 10 min` requires the reader to already know what the "10" refers to, which nothing on the card supplies. Cutting through all three attempts: the real question was never "how do we word this," it was "does the cause change what the passenger should do." At the High tier it does (#21's phrase fork stands). At the Low/Medium tier it doesn't — hurrying is hurrying regardless of whether the cause is delay history or station size — so no new phrase, no caption, and no comparison figure survive there. The floor instead moved one level lower, from the *probability* (#20, still in effect) to the *displayed band itself*: a below-MCT connection can never show as `Safe connection`, full stop, landing on the plain, unmarked `Tight connection` a genuinely medium-risk connection already gets. Net effect: back down to the same five phrases as before this round of MCT work began, with no jargon and no bolted-on clause anywhere.

---

*This document is the frontend counterpart to `SPEC.md`. Any UI change that isn't a pure bugfix should update this file in the same review as the code change.*
