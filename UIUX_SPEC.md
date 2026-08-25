# UI/UX Specification — DB Risk & Rescue

**This document governs everything the user sees and touches** — the search form, the route cards, the expanded itinerary. Read it if you're changing colours, wording, or layout.

If you only read one section, make it **§2**: the five-state risk system is where most UI decisions actually get made, and its mapping table is the single source of truth for which phrase, colour and figure a transfer displays. §3 holds the design tokens, §4 the component structure. New to the project? Start with [README.md](README.md).

This doc names engine-produced values but never computes them — thresholds and maths belong to `SPEC.md` (§6 for constants). `design_mock.html` is the reference mockup the visual system was built from.

**Section map:** §1 Design Philosophy & Principles · §2 The Five-State Risk System · §3 Color Palette & Design Tokens · §4 Component Architecture · §5 Technical Implementation Notes · §6 Design History Log.
**Status:** Implemented in `ui_components.py`; §5.4 confirms parity against the running code.

## 1. Design Philosophy & Principles

### 1.1 Clean, minimalist, consumer-grade

Styled as a companion to the tools a German rail traveler already uses — the horizontal train bar, swap button, toggle-style sort, and tabular timetable are deliberate echoes of DB Navigator, adapted to carry information it doesn't have (predicted arrival, transfer-miss probability). Apple Maps informs the restraint on iconography and color: information is carried by typography, spacing, and a small consistent color vocabulary — never decoration.

- No skeuomorphism, no gradients-for-their-own-sake, no drop shadows beyond a subtle 1–2px card lift.
- Generous whitespace over dense packing — a route card should be scannable in under two seconds.
- Every visual element earns its place by encoding a specific data value.

### 1.2 Strict "No Emojis" policy

No emoji anywhere in the app shell — not for warnings, trains, or risk states. A deliberate, repeated correction during design review: emoji read as informal and undermine a data-science tool's credibility. Three primitives replace them:

| Primitive | Used for |
|---|---|
| Typography | Action-first risk wording instead of icon + label |
| Geometry | Filled/hollow circles for stops, solid/dashed lines for segments, a CSS triangle for the disclosure caret |
| Color fields | Soft background fills / left-edge strips for risk; solid-fill chips for train-type |

**The one sanctioned exception is Material Symbols**, Streamlit's built-in monochrome icon set, used only where neither text nor geometry can do the job: the station-swap button, whose entire meaning is a directional affordance (§5.3), and the browser tab icon (`st.set_page_config(page_icon=...)`), which lives outside the rendered page and cannot be typography. Both inherit the surrounding text color and carry no color meaning of their own, so they never compete with the risk palette the way an emoji would. Nothing inside a route card or transfer bar uses one.

Applies to every future component: the default answer to a new UI need is "find the typographic or geometric equivalent," not "pick an emoji."

## 2. The Five-State Risk System

### 2.1 Where this fits

A transfer's displayed state is the last step of `SPEC.md` §5.3's five-step pipeline: raw miss probability → MCT floor → band → Impact Override → phrase. Steps 1–4 are engine math (`SPEC.md` §3; thresholds in `SPEC.md` §6); step 5 — the phrase, color, and trailing figure a passenger actually sees — is defined here in §2.2.

### 2.2 Master mapping table

The complete state → color → phrase → condition → figure mapping. This is the single source of truth — `SPEC.md` doesn't restate it.

| State | Badge color | Phrase | Condition | Trailing figure |
|---|---|---|---|---|
| Safe | Green — fill `#eaf7ee` / border `#2e7d32` / text `#1c6b2c` | **Safe connection** | Base band Low, buffer ≥ station MCT | `· <Y> min` (scheduled buffer) |
| Tight (genuine) | Yellow — fill `#fff5e3` / border `#d98c1f` / text `#8a5300` | **Tight connection** | Base band Medium | `· <Y> min` (scheduled buffer) |
| Tight (MCT floor) | Yellow (same tokens) | **Tight connection** | Base band Low, but buffer < station MCT — floor bumps the displayed band Low→Medium | `· <Y> min` (scheduled buffer) — identical wording to genuine Medium; the cause is never named |
| Recoverable miss | Yellow (same tokens) | **Recoverable miss** | Base band High, Impact Override fires (fallback impact ≤ threshold) | `arrives <HH:MM> if missed` |
| Miss likely | Red — fill `#fdeceb` / border `#d63a30` / text `#a3231b` | **Miss likely** | Base band High, no Override, not MCT-driven | `arrives <HH:MM> if missed` |
| Unrealistic transfer | Red (same tokens) | **Unrealistic transfer** | Base band High, no Override, buffer < station MCT | `arrives <HH:MM> if missed` |

Every phrase is action-first, never a bare risk level: there is no reading of "Miss likely (38% risk)" that could be mistaken for reassurance (§6 #2).

**One line, no exceptions:** `<Action phrase> (<X%> risk) · <trailing figure>`. No station name, no extra clause, no second sentence — the station already appears above the transfer bar in the itinerary.

### 2.3 Trailing-figure rules

The figure's meaning splits along the **base probability band**, not the displayed color:

- **Base Low/Medium** (Safe, both Tight variants): the transfer is expected to hold, so "how much slack" is the relevant question — shows the scheduled buffer.
- **Base High** (Recoverable miss, Miss likely, Unrealistic transfer): the connection is *expected* to fail, so its buffer is uninformative — shows the fallback's absolute arrival clock time (`route.scheduled_arrival + impact_minutes`) instead, directly comparable to the Scheduled Arrival in the card header (§6 #16).

The figure carries no unit label beyond `min`/the clock format, and stays visually lighter (`font-weight: 400`) than the risk phrase (`700`) in every band.

### 2.4 Local Risk vs. Global Health

Two independent metrics sharing the §3.3 color palette:

- **Local Risk** (transfer bar, §2.2 above) — this specific transfer's miss probability, with the Impact Override.
- **Global Health** (card left-edge strip, §3.4) — the route's P85 penalty against fixed minute thresholds (`SPEC.md` §5.2, §6), ignoring transfer count and individual probabilities entirely.

A card can legitimately show an all-Green transfer timeline with a Yellow edge (ordinary delay variance alone routinely pushes P85 past the Green band) or the reverse (a cheap fallback covers an otherwise-Red transfer) — neither is a bug. Direct (0-transfer) routes are colored by Global Health like any other route, not exempted.

## 3. Color Palette & Design Tokens

CSS custom properties in the reference mockup, lifted verbatim into `ui_components.py`'s color constants (verified: §5.4).

### 3.1 Base tokens

| Token | Hex | Usage |
|---|---|---|
| `--ink` | `#161a20` | Primary text, headline numbers |
| `--muted` | `#68707c` | Secondary text |
| `--faint` | `#9aa1ab` | Tertiary text, dividers, disabled states |
| `--line` | `#e3e5e9` | Borders, hairline dividers |
| `--card-bg` | `#ffffff` | Card / search-panel background |
| `--page-bg` | `#f2f3f5` | App background |
| `--db-red` | `#eb0016` | Brand accent only (header banner) — never risk signaling, to stay visually distinct from the High-risk band |

These tokens theme the custom-rendered cards. Streamlit's own native widget chrome — selectbox focus rings, default buttons, sliders — sits outside `inject_global_styles()`'s reach, so `.streamlit/config.toml` themes it separately with matching values: `primaryColor = "#EB0016"` (`--db-red`), `backgroundColor = "#F2F3F5"` (`--page-bg`), `secondaryBackgroundColor = "#F1F3F5"`, `textColor = "#212529"`. Keep the two in sync when changing either.

### 3.2 Train category chips

Corrected against an actual DB Navigator reference mid-review (§6 #3):

| Category | Background | Text | Border |
|---|---|---|---|
| ICE / IC / EC | `#33383f` | `#ffffff` | none |
| RE / RB | `#d3d6db` | `#20232a` | `1px solid #b9bdc4` (the only chip light enough to need one) |
| S-Bahn / S | `#0f8a3f` | `#ffffff` | none |

These three cover every category that can reach the UI: ingestion normalizes each line to one of five `gtfs_ingest.LINE_TYPES` (ICE/IC/RE/RB/S-Bahn) and fails the build otherwise (`DATA_SPEC.md` §3.1), so no other type exists downstream. An unrecognized type would fall back to the ICE chip. A U-Bahn chip from the pre-redesign token set was dropped for exactly this reason — nothing could ever render it.

Two sizes from the same classes: **Large** (`.chip`, min-height 34px) for the collapsed-card train bar; **Small** (`.chip.chip-sm`, ~10.5px text) inline in the expanded timetable.

### 3.3 Risk-level tokens

Defined once in §2.2 — this section doesn't repeat the hex values. Each band is a soft fill + saturated border/text accent, never a solid saturated fill (would read as more alarming than intended, especially Low/Medium). Red covers two causes (Miss likely / Unrealistic transfer) with identical tokens — only the phrase differs.

The **left-edge card strip** (4px `border-left`) is the single risk-color signal at the collapsed level — present in both collapsed and expanded states, never removed on expand (§6 #5). The **transfer bar** carries its own soft-fill background but no left border of its own — one accent border (card edge) plus one soft-fill block (transfer bar) is the maximum color signal on screen at once.

### 3.4 Card & container styling

| Property | Value |
|---|---|
| Card border radius | `10px` (route cards) / `12px` (search card) |
| Card border | `1px solid var(--line)` |
| Card shadow | `0 1px 2px rgba(20,20,30,.04)` (route) / `0 2px 6px rgba(20,20,30,.06)` (search) |
| Card left-edge risk strip | `4px solid`, color per §2.2 |
| Inter-card spacing | `12–14px` margin-bottom |
| Page container max-width | `1050px`, centered — applied to Streamlit's main block container only (sidebar unaffected), so wide monitors don't stretch the app edge-to-edge |

## 4. Component Architecture

### 4.1 Search card — "Plan your trip"

A single bordered container (§3.4) replacing four bare `st.columns()` with no visual grouping:

```
┌─────────────────────────────────────────────────────────┐
│  Plan your trip                                          │
│  ┌─────────────────┐   ⇄   ┌─────────────────┐           │
│  │ Origin            │       │ Destination      │           │
│  └─────────────────┘       └─────────────────┘           │
│  ─────────────────────────────────────────────────       │
│  ┌────────────────────┐  ┌────────────────────┐          │
│  │ Date                │  │ Departure at/after  │          │
│  └────────────────────┘  └────────────────────┘          │
│  ─────────────────────────────────────────────────       │
│  Sort by  [ Earliest scheduled ][ Safest arrival ]        │
└─────────────────────────────────────────────────────────┘
```

**Row 1 — station inputs + swap.** `st.columns([5, 1, 5], vertical_alignment="bottom")` — a narrow middle column for the swap button, bottom-aligned so the circular 36px button lines up with the input boxes' bottom edge, not the labels above. (The mockup expressed this as a `1fr 44px 1fr` CSS grid; Streamlit columns are the shipped equivalent.) Two CSS corrections were needed beyond Streamlit's own alignment (full detail: §5.3, §6 #8): `!important` on `width`/`height`/`min-height` together (a plain `height` loses to Streamlit's button `min-height`), and a `margin-bottom: 2px` nudge to true-center the button within its 40px-tall slot.

**Row 2 — Date + Departure time.** `st.columns([1, 1])`, equal halves. Date only renders when the active backend has a real calendar (Warehouse); the JSON backends omit it, leaving Departure time at full width.

**Row 3 — Sort by**, alone on its own row (moved off Row 2 once three fields there left everything cramped, §6 #10). Full-width `st.segmented_control`, dark-fill active state, forced `min-height: 40px` to match the input rows' height.

Every field maps 1:1 to an existing backend input — nothing here is decorative.

### 4.2 Route cards — collapsed view

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

**Hero header.** Left: scheduled departure–arrival as one bold tabular-numeral range, duration + transfer count beneath in muted text — purely factual, never delay-adjusted. A 0-transfer route reads `Direct` rather than "0 transfers". Right: the Predicted Arrival panel (§4.3).

**Train bar.** One category-colored chip (§3.2) per leg, flex-sized to that leg's share of travel time (minimum width enforced for legibility). A short dashed gap at each transfer point reuses the solid/dashed convention from §4.4, standing in for a walking pictogram. The single most direct DB Navigator lift — full-width, dominant, directly under the hero header.

**Station row.** Origin/destination names, left/right-aligned beneath the train bar.

**Details toggle.** Centered, muted, CSS-triangle caret — mechanism: §5.1.

### 4.3 Predicted Arrival panel

One bordered box grouping the two values a Monte Carlo engine adds over a static timetable:

- **Expected** (mean) — de-emphasized: `--muted`, weight 500, 13px. Context, not the headline.
- **Safest** (P85) — emphasized: `--ink`, weight 800, 17px; its label is also promoted to `--ink`/700. This is the number the app is built to recommend, so it reads as the headline (§6 #12).

Separated by a thin dashed rule. Reading order across the card: **Scheduled** (hero, factual) → **Expected** (panel, muted) → **Safest** (panel, bold). Each value carries a small delta against Scheduled Arrival (`+24m`), shown as `on time` when the prediction lands at or before schedule rather than as a negative number.

**Responsive layout, not fixed.** Side-by-side by default (vertical dashed divider); falls back to a vertical stack (horizontal divider) only under ≤480px, where a plain CSS media query is simpler to reason about than a container query and the card already tracks viewport width closely.

**Each row is one nowrap flex line** — name, value, and delta never break independently (§6 #11), so a 3-digit delta (`+139m`) can't force an uneven wrap.

No strikethrough anywhere — reserved by convention for live, real-time delay; these are pre-departure forecasts, and strikethrough would misrepresent that (§6 #4).

### 4.4 Expanded itinerary — CSS Grid timetable

One continuous CSS Grid (`grid-template-columns: 42px 20px 1fr` — time / connector / station) for every row, including the transfer bar — not independently-positioned elements (an earlier margin-based approach drifted out of alignment whenever row heights changed, §6 #7).

**Columns:** (1) Time — tabular-numeral, right-aligned. (2) Connector — flexbox-built vertical line (dot + fill-remaining-space line), not pixel offsets, so it correctly reaches through a taller transfer-bar row. (3) Station/content — name + inline chip for a leg's origin, name alone for arrival/transfer, the transfer warning bar for the transfer row.

**Dot states:** filled solid ink = a scheduled stop on the current leg; hollow ring = arrival at a transfer point or final destination.

**Line states:** solid = actively riding; dashed = the transfer/waiting gap, from the hollow dot through the transfer-bar row to the next solid dot.

**Transfer warning bar.** Its own grid row's station-column cell (not a positioned overlay) — inherits grid alignment automatically. Soft background fill per §2.2, no border of its own (§3.3). Content: the one-line pattern from §2.2/§2.3.

## 5. Technical Implementation Notes

### 5.1 `<details>`/`<summary>` over `st.expander`

```html
<details class="itin-details">
  <summary>
    <span class="lbl-closed">Details</span>
    <span class="lbl-open">Hide details</span>
    <div class="caret"></div>
  </summary>
  <!-- expanded itinerary grid, §4.4 -->
</details>
```

Rendered via one `st.markdown(card_html, unsafe_allow_html=True)` call per card.

**Why:** `unsafe_allow_html` injects into Streamlit's own DOM (unlike `st.components.v1.html`'s iframe sandbox), so native `<details>` behaves normally. Toggling is pure client-side state — **no Streamlit rerun**, unlike `st.expander` or a session-state toggle, which reruns the whole script on every open/close. `st.expander` also can't be fully restyled without targeting unstable internal DOM classes.

**Accepted limitation:** no built-in smooth height animation (cross-browser CSS-only `<details>` animation remains inconsistent). The instant snap is accepted as closer to DB Navigator's own accordions than a slow slide, and keeps the implementation inside one HTML string.

### 5.2 Required CSS overrides

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

The `[open]` attribute selector — not JavaScript — drives both the caret rotation and the label swap.

### 5.3 Station swap control

Mutates `st.session_state` for both selectbox keys via the button's `on_click` callback, which Streamlit runs before the script reruns and the selectboxes redraw — so by the time they're re-instantiated, the swapped values are already in state:

```python
def _swap_stations() -> None:
    st.session_state.search_origin_id, st.session_state.search_destination_id = (
        st.session_state.search_destination_id,
        st.session_state.search_origin_id,
    )

origin_id = st.selectbox("Origin", options=station_ids, key="search_origin_id")
st.button("", icon=":material/swap_horiz:", key="swap_stations", on_click=_swap_stations)
destination_id = st.selectbox("Destination", options=station_ids, key="search_destination_id")
```

The button's own label is empty — the swap glyph is a Material Symbol icon (`swap_horiz`), not the "⇄" text glyph, since text-glyph baselines vary by font/OS and can look off-center even when the button's box measures centered (§4.1). Column order in `st.columns([5, 1, 5])` can be written in any order in code — Streamlit lays out each column's contents independently.

### 5.4 Implementation status

`ui_components.py` was rewritten to match this spec (the DB Navigator-style rebuild) and stays in sync with it: no emoji anywhere in the app shell (§1.2); the single-line five-phrase risk wording with no color legend (§2.2); correct RE/RB chip colors (§3.2); the bordered, grouped "Plan your trip" search card with swap control (§4.1); the grouped Predicted Arrival panel (§4.3); native `<details>`/`<summary>`, not `st.expander` (§5.1); the colored left-edge card strip, present in both collapsed and expanded states (§3.3); the single continuous CSS Grid timetable (§4.4); and the off-white `--page-bg` app background behind white cards (§3.1), applied both natively via `.streamlit/config.toml`'s `backgroundColor` and via CSS on `[data-testid="stApp"]`/`[data-testid="stMain"]` — all confirmed present in the running code as of this revision.

## 6. Design History Log

> **Non-normative — nothing here describes current behaviour.** This is a record of approaches that were tried and rejected, kept so they don't get retried. For what the UI actually does today, see §1–§5. Skip this section on a first read.

Several rules above exist *because* an earlier approach didn't hold up; check this list before reintroducing one.

1. **Standalone risk-color legend** — removed once wording became self-explanatory (§2.2); a legend explaining self-evident colors is clutter.
2. **Bare "Low/Medium/High risk" labels** — rejected as ambiguous ("High" misreadable as high chance of success); replaced by action-first phrasing (§2.2).
3. **RE/RB chip colored red** — simply wrong; corrected against an actual DB Navigator screenshot (§3.2).
4. **Live-delay strikethrough on Scheduled vs. Expected** — rejected: this app shows pre-departure forecasts, not live tracking, and strikethrough would misrepresent that. Resolved via the grouped Predicted Arrival panel (§4.3).
5. **Left-edge strip removed on expand** — produced a jarring color-then-no-color flicker. Made permanent instead; the transfer bar lost its own border so exactly one color signal shows at a time (§3.3).
6. **Horizontal train bar dropped for per-stop chips only** — reintroduced; it's the only "how many legs, how long each" signal a *collapsed* card has.
7. **Absolutely-positioned timetable connectors/margins** — replaced by one continuous CSS Grid (§4.4) after repeated alignment bugs.
8. **Swap button vertical alignment, three passes.** (a) Plain `height:36px` lost to Streamlit's button `min-height`, producing a 36×40px oval. (b) Fixed the oval but also force-flexed the column, which left no slack for centering. (c) Dropped the column override (Streamlit's bottom-alignment was already correct) and added a precise `margin-bottom: 2px` on the button. Lesson: fix the specific pixel gap, don't re-implement a mechanism that already works.
9. **Unbounded page width on wide monitors** — capped main content at 1050px, centered, without touching `layout="wide"` (sidebar unaffected).
10. **Date/Departure/Sort crammed into one three-field row** — Date and Departure felt cramped and the Sort pill rendered undersized no matter how padding was tuned. Fixed by giving Sort its own full-width row (§4.1).
11. **Predicted Arrival rows wrapping mid-value** — a wide delta (`+139m`) could still break onto a second line inside an `auto`-sized column. Fixed with `white-space: nowrap` on each row as a unit (§4.3).
12. **Expected/Safest at equal type weight** — read as "two same-importance numbers," not "recommendation vs. context." Safest (the number to act on) pushed to 17px/800/`--ink`; Expected pushed down to 13px/500/`--muted` (§4.3).
13. **"buffer" spelled out on the transfer bar** (`40m buffer`) — redundant once the risk phrase already frames the line as a connection. Simplified to `40 min` (§2.3).
14. **Card edge strip tied to worst-transfer probability** — replaced by Global Health on P85 penalty (§2.4), independent of transfer count. A threshold sweep over real routes found ordinary delay variance alone routinely exceeds any transfer-level threshold tight enough to be useful, and 37% of individually-Red transfers had a real fallback costing ≤15 min — a "harmless miss" the pure-probability strip couldn't distinguish from a costly one.
15. **Impact-Overridden transfers reusing "Tight connection"** — identical wording to a genuinely low-risk transfer, with nothing bridging the mismatch between the number and the phrase. Given its own phrase, `Recoverable miss` (§2.2).
16. **The Red-band trailing figure, four iterations.** Bare buffer minutes (indistinguishable from Safe/Tight) → a literal `0 min` edge case read as a rendering bug → a relative delta (`+12 min if missed`) left "relative to what?" unanswered → settled on the fallback's absolute arrival clock time (§2.3), applied to *both* Red phrases (Miss likely and Recoverable miss) once it was clear the underlying argument — "the buffer is uninformative once probability is Red" — is a property of the band, not of the Override.
17. **MCT enforcement: hard veto vs. gradient floor.** A hard veto (`miss_probability = 1.0`) was rejected: it compounds an already-approximate signal (touch-count MCT) into false certainty. A flat floor was rejected: it reintroduces the hard veto's cliff edge at a lower height. Settled on a linear gradient (`SPEC.md` §3.6.2). Its wording went through a standalone `below <N> min MCT` caption (self-contradictory next to `Safe connection`, and raw jargon) and a dedicated `Rushed connection` phrase (too easily confused with `Tight connection`, and would need to render in two different colors) — both cut in favor of one test: **does the cause change the action?** Yes at the High tier → phrase fork (`Unrealistic transfer`, §2.2). No at the Low/Medium tier → silent absorption into `Tight connection`, no caption, no new phrase.

---

*This document is the frontend counterpart to `SPEC.md`. Any UI change that isn't a pure bugfix should update this file in the same review as the code change.*
