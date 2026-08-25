# DB Risk & Rescue — System Design Specification

**Status:** Phases 1–3 built, tested, and merged to `main`. Structured by system concern (data model → engine → storage → UI), with phase-by-phase history kept separately in §6. See §6 for what shipped when, §7 for what's next.

**Section map:** §1 Objective & System Overview · §2 Core Data Models · §3 Routing & Monte Carlo Simulation Engine · §4 Storage & Data Access Architecture · §5 Streamlit UI Specifications · §6 Milestones Retrospective · §7 Future Roadmap & Extensions (Phase 4+).

## 1. Objective & System Overview

A Streamlit application for German railway (DB) trip planning that goes beyond shortest-path routing by quantifying the risk of missed connections and surfacing a probability-aware "True Expected Time of Arrival" (True ETA), computed via Monte Carlo simulation over historical delay behavior.

The system has three layers, each covered by its own section below:

- **Data model** (§2) — a backend-agnostic Pydantic contract (Station/Line/Leg/Transfer/Route) that everything else is built against.
- **Engine** (§3) — risk scoring and Monte Carlo simulation over that contract, including O(1) dynamic re-routing on a missed connection.
- **Storage** (§4) — three interchangeable backends that produce the §2 contract: a hand-authored JSON fixture, a GTFS-pipeline JSON snapshot, and a DuckDB warehouse queryable by any date. Full schema/pipeline detail lives in `DATA_SPEC.md`.
- **UI** (§5) — the Streamlit dashboard that ties the above together.

## 2. Core Data Models (Pydantic Contracts)

The shapes below are `models.py`'s Pydantic contract — the *only* interface `engine.py`'s simulation and `ui_components.py`'s rendering consume. All three storage backends (§4) produce these exact objects at query time; a backend that can't produce this shape is wrong, not the model.

### 2.1 Station

```json
{
  "station_id": "DE_FRA_HBF",
  "name": "Frankfurt(Main) Hbf"
}
```

### 2.2 Line

```json
{
  "line_id": "ICE_15",
  "type": "ICE",
  "operator": "DB Fernverkehr"
}
```

### 2.3 Leg

A leg is one uninterrupted ride on one train. Delay behavior is modeled as an **empirical probability distribution over discrete delay buckets** (minutes late), reflecting how DB historical punctuality stats are typically published, and directly samplable in Monte Carlo without curve-fitting.

```json
{
  "leg_id": "L1",
  "line_id": "ICE_15",
  "origin_station_id": "DE_FRA_HBF",
  "destination_station_id": "DE_KOL_HBF",
  "scheduled_departure": "2026-08-23T09:02:00",
  "scheduled_arrival": "2026-08-23T10:14:00",
  "delay_distribution_minutes": {
    "0": 0.60,
    "5": 0.20,
    "15": 0.12,
    "30": 0.06,
    "60": 0.02
  }
}
```

Constraint: bucket probabilities for a leg must sum to 1.0. Buckets represent "delay is *at least* this many minutes" in cumulative-from-zero terms when read as a CDF (see §3.1).

### 2.4 Transfer

A transfer connects the arrival of leg N to the departure of leg N+1 at a shared station. **Scheduled buffer only** — no separate Minimum Connection Time layer, no platform-change flag (§7).

```json
{
  "transfer_id": "T1",
  "station_id": "DE_KOL_HBF",
  "from_leg_id": "L1",
  "to_leg_id": "L2",
  "scheduled_buffer_minutes": 12
}
```

`scheduled_buffer_minutes` is derived from the timetable: `L2.scheduled_departure - L1.scheduled_arrival`, expressed in minutes, and stored directly rather than recomputed at runtime.

### 2.5 Route

A route is an ordered sequence of legs and the transfers between them, representing one candidate journey option returned to the UI.

```json
{
  "route_id": "R1",
  "legs": ["L1", "L2"],
  "transfers": ["T1"],
  "origin_station_id": "DE_FRA_HBF",
  "destination_station_id": "DE_BER_HBF",
  "scheduled_departure": "2026-08-23T09:02:00",
  "scheduled_arrival": "2026-08-23T13:40:00"
}
```

Single-leg routes (no transfer) simply have an empty `transfers` array.

### 2.6 Service Frequency Reference Table

There is no full timetable to query for "what's the next train" — even Phase 3's DuckDB warehouse (§4.3) only knows about legs that actually exist in the ingested feed, not a generic frequency model. To resolve missed connections (§3.2 Step 4), service frequency is modeled as a **static lookup keyed by each Line's existing `type` field**, not as a new per-line or per-line-instance field:

| Line type | Assumed headway (minutes) |
|---|---|
| ICE / IC | 60 |
| RE / RB | 60 |
| S-Bahn | 20 |

This table is a fixed constant in the simulation engine (`engine.SERVICE_FREQUENCY_MINUTES`), not derived from any data source, keeping the algorithm's "how often does this line run" notion simple and tunable independent of which backend produced the route. Values are placeholders, sanity-checked against real DB frequencies but not empirically fit.

## 3. Routing & Monte Carlo Simulation Engine

### 3.1 Missed-Connection Probability (per transfer)

Direct CDF lookup against the upstream leg's delay distribution:

```
P(miss | transfer T) = P(delay_from_leg > T.scheduled_buffer_minutes)
                      = sum of delay_distribution_minutes[bucket]
                        for every bucket > T.scheduled_buffer_minutes
```

Example: buffer = 12 min, upstream leg's distribution `{"0": .60, "5": .20, "15": .12, "30": .06, "60": .02}` → `P(miss) = .12 + .06 + .02 = 0.20`.

This value is what drives the risk color-coding on the UI timeline (§5.3).

### 3.2 Monte Carlo Simulation (per route, per run)

For each route, run N iterations (default N = 1,000). Each iteration:

1. For every leg in the route, independently sample a realized delay from that leg's `delay_distribution_minutes` (independent per-leg sampling — no cross-leg correlation; see §7).
2. Walk the route leg-by-leg. At each transfer, compare realized arrival time of the upstream leg against the downstream leg's scheduled departure.
3. **If the connection holds** (realized arrival + buffer check passes): carry the realized delay forward and continue.
4. **If the connection is missed**: first try dynamic re-routing (§3.4); if no fallback route is available, resolve by computing the **next periodic departure of the downstream leg's line**. Treat the downstream leg's own `scheduled_departure` as an anchor slot, and its line `type` as the key into the Service Frequency Reference Table (§2.6) to get headway `F`. The next available departure is:

   ```
   next_departure = leg.scheduled_departure + F * ceil(
       (realized_arrival_time - leg.scheduled_departure) / F
   )
   ```

   The added wait (`next_departure - realized_arrival_time`) is added to the running arrival time, and the walk continues from `next_departure` (the replacement departure's own delay distribution is then sampled fresh for the remainder of that leg).
5. Record the final realized arrival time for that iteration.

### 3.3 True Expected ETA (output per route)

After N iterations, report:

- **Mean True ETA** — average of all simulated final arrival times. The "typical" expectation.
- **P85 / P90 True ETA** — the 85th/90th percentile of simulated final arrival times. The "plan for this if you're unlucky" figure.

Both values, plus the per-transfer miss probabilities from §3.1, are what the UI renders (§5).

### 3.4 Dynamic Re-routing on Miss (Fallback Caching)

When §3.2 Step 4 triggers (a transfer is missed), rather than always waiting for the next periodic departure of the *same* downstream line, first search for a better alternative: the best candidate route from the missed transfer's station to the journey's final destination, using the route search (`DATA_SPEC.md` §5) subject to a **reduced remaining transfer budget** (the app-wide 2-transfer cap, minus the transfers already used reaching the missed connection). If such a route exists, the simulation switches onto it for the remainder of that iteration; if none exists, it falls back to the same-line-headway wait (§3.2 Step 4) unchanged.

This route search is **not** re-run per Monte Carlo iteration — it is prohibitively expensive at N=1,000+ iterations. Instead, for a given route, the best fallback (if any) is **pre-computed once per transfer node before the simulation loop starts** (`engine.precompute_fallback_plans`), and each iteration performs only an O(1) cache lookup on miss.

Scope boundary: this is one level of re-routing. If a transfer *within* a fallback route is itself missed, that nested miss resolves via the same-line-headway wait (§3.2 Step 4), not a second re-routing search — unbounded recursive re-pathfinding remains deferred (§7).

**Impact-Weighted UI Override input.** The same precomputed `FallbackPlan` also feeds the UI's Local Risk Impact Override (§5.3): for a given transfer node, `impact_minutes = fallback_plan.route.scheduled_arrival - route.scheduled_arrival` when `precompute_fallback_plans` found a plan; when it returned `None` (no candidate route survived the remaining-transfer-budget filter), the override falls back to the same-line-headway wait computed in §3.2 Step 4 instead. Because the fallback plan is already cached before the simulation loop starts, reading it for the UI costs the same O(1) lookup the simulation itself performs on a miss — the override adds no new computation, only a new consumer of an existing cache.

### 3.5 Backend-Agnostic Fallback Search & the O(1) Guarantee

`precompute_fallback_plans` (§3.4) doesn't know or care which storage backend (§4) answers its route search — it takes an optional `route_search_fn(origin_id, destination_id, departure_time) -> list[Route]` callback; omitting it falls back to the default in-memory search over a loaded `MockDataset`. This is what let the DuckDB backend (§4.3) plug in without changing `precompute_fallback_plans`'s control flow, or `simulate_route`'s hot loop, at all:

- The O(1)-per-iteration guarantee holds regardless of backend: fallback search still runs exactly once per transfer node before the Monte Carlo loop, never inside it. Whether that one-time call queries an in-memory list or DuckDB changes its cost, not its *count* — the number of calls is bounded by (candidate routes × transfers per route), independent of N.
- `simulate_route`'s per-iteration sampling never touches a database — it consumes plain in-memory `Leg`/`Transfer` objects assembled once per search (§4.1).
- Verified end-to-end by `test_route_search_duckdb.py`'s integration test, which drives `precompute_fallback_plans` + `simulate_route` against a DuckDB-backed `route_search_fn` and checks the same outputs the in-memory path produces.

For the JSON backend specifically, `precompute_fallback_plans` also accepts an optional `search_indexes` — a `pipelines.route_search.build_route_search_indexes(dataset)` result (`legs_by_id`/`transfers_by_from_leg`) — passed straight through to its default `find_candidate_routes` call. Without it, every fallback sub-search rebuilt both lookup tables from scratch over the full dataset; a 5-route batch with 2 transfers each did that ~11 times over `real_dataset.json`'s ~6,000 legs / ~69,000 transfers. `app.py` builds these indexes once per search batch (`get_search_indexes`, cached per dataset path) and reuses them for the top-level search and every fallback sub-search — an O(1)-per-call-count optimization same as the batching above, just for the JSON path's index cost instead of the DuckDB path's query count.

## 4. Storage & Data Access Architecture

Three interchangeable backends produce the §2 contract; `app.py`'s sidebar picks between them. Full schema and pipeline detail: `DATA_SPEC.md`.

### 4.1 Query-Time Contract & Backend Boundary

Every backend's job ends at producing `Station`/`Line`/`Leg`/`Transfer`/`Route` objects (§2) — `engine.py` and `ui_components.py` never branch on which backend is active. This boundary is what let Phase 3 replace the entire storage layer (§4.3) without touching the simulation core (§3) or the UI rendering (§5).

### 4.2 JSON Backends (Phase 1 & 2)

- **`mock_data.json`** — hand-authored fixture data, loaded whole via `data_loader.load_dataset()`.
- **`data/real_dataset.json`** — a GTFS.DE + piebro-delay pipeline output (`pipelines/build_dataset.py`), also loaded whole, scoped to a single fixed calendar date baked in at build time.

Both validate directly against `MockDataset` (§2) and are read with the same `load_dataset(path)` call; `app.py` only picks which path. See `DATA_SPEC.md` §7.

### 4.3 DuckDB Warehouse Backend (Phase 3)

`data/warehouse.duckdb`, built by `pipelines/build_warehouse.py`, removes Phase 2's single-fixed-date constraint:

1. **Date-agnostic templates** — topology (`leg_templates`/`transfer_templates`) is stored independent of any specific calendar date (seconds-since-midnight, not a concrete datetime), so the row count doesn't multiply with the size of the ingested calendar window. One warehouse build (currently covering a full month) replaces what would otherwise be one JSON snapshot per day.
2. **Dynamic calendar resolution** — GTFS `calendar.txt`/`calendar_dates.txt` are ingested and preserved as queryable data; which `service_id`s are active is resolved as SQL at query time (`pipelines/route_search_duckdb.py`), not collapsed into one date at build time.
3. **Scoped, incremental loading** — `route_search_duckdb.find_candidate_routes(conn, ..., service_date, legs_by_id, transfers_by_id)` queries per search (origin + active service_ids for the date) and writes resolved objects into the caller's dicts in place, so only the network actually touched by a search — the top-level query plus each transfer's fallback search (§3.5) — ever loads into memory.

The UI date picker (§5.1) is bounded to `route_search_duckdb.calendar_window()`'s min/max. Full table schema: `DATA_SPEC.md` §6.

### 4.4 Streamlit Caching Strategy

Route search and Monte Carlo simulation are cached as **two separate stages**, not one combined unit — `search_routes`/`search_routes_warehouse` (route search only) and `simulate_one_route`/`simulate_one_route_warehouse` (simulation for exactly one route). Both are `@st.cache_data`.

This split exists because the original combined cache (`search_and_simulate`/`search_and_simulate_warehouse`) was keyed on `display_limit` (§5.1's pagination window), so every "Load more" click changed the cache key and re-simulated the **entire growing prefix** — revealing routes 6–10 re-ran the simulation for routes 1–5 as well. Splitting the cache means:

- `search_routes*` has no `display_limit` param at all, so the search itself is never re-run by a "Load more" click.
- `simulate_one_route*` is keyed on `route_id` (plus `n_iterations`/`seed`/the dataset path, or `service_date` for the warehouse path — a fallback sub-search's results genuinely depend on which calendar day it runs for). `route_id` is a stable, deterministic identifier for a given search's route, derived from its underlying leg/transfer ids, so it alone is a sufficient cache key on its own; a "Load more" click that reveals routes 6–10 only ever computes those 5 — routes 1–5 are pure cache hits, not re-simulated alongside them.
- `Route`/index-tuple arguments that aren't part of the cache identity (`_route`, `_search_indexes`, `_legs_by_id`, etc.) are passed with a leading underscore so Streamlit excludes them from the cache-key hash — they're there to avoid recomputing values already in hand, not to change what counts as a cache hit ([Streamlit caching docs](https://docs.streamlit.io/develop/concepts/architecture/caching)).
- `app.py` wraps both stages in `time.perf_counter()` and prints the elapsed time to the terminal, so a future regression in either stage is visible without re-profiling by hand.

Measured end-to-end (warehouse backend, real corridor data): ~1.3s to reach 20 loaded routes, then ~0.9s per further "Load more" click — a flat per-click cost, not a growing total.

## 5. Streamlit UI Specifications

### 5.1 Input Flow

Minimal-friction search: user selects **origin, destination, and departure time**; the DuckDB backend (§4.3) additionally exposes a **service date** field, constrained to the GTFS feed's ingested calendar window. The app auto-generates candidate routes from whichever backend is selected and runs the Monte Carlo simulation with default parameters (N = 1,000 iterations) — no advanced/simulation-tuning panel.

Candidate routes are paginated via a `display_limit` session-state counter (`DISPLAY_LIMIT_STEP = 5`): the app simulates and shows only the first `display_limit` routes, with a "Load more" control that adds 5 more. `display_limit` resets to 5 whenever the search itself changes (origin, destination, departure time, or — warehouse backend — service date). Critically, routes are sliced to `display_limit` **before** the Monte Carlo loop runs, not just before rendering — simulation, not the route search, is the expensive part (§4.4), so slicing earlier is what actually saves the work rather than just hiding it. Sort/ranking within the results list operates only over the currently-loaded batch, not the full candidate pool, since ranking against not-yet-simulated routes would mean simulating them anyway and defeat pagination's purpose.

### 5.2 Route Comparison View

A **ranked card list** of candidate routes (typically 3–5), each card showing:

- Scheduled departure/arrival and duration
- Mean True ETA
- P85 True ETA (risk-adjusted)
- Number of transfers

Cards are sortable/orderable so the user can compare "fastest scheduled" against "safest / lowest-risk" at a glance — similar to a flight-search results list.

**Global Health (card left-edge strip).** Each card additionally carries a 4px left-edge color strip (`UIUX_SPEC.md` §2.3) signaling overall route safety, driven **solely by the P85 penalty** — `p85_penalty_minutes = P85 True ETA (§3.3) − Scheduled Arrival` — and deliberately ignoring individual transfer miss probabilities:

| Band | P85 penalty | Rationale |
|---|---|---|
| Green (Safe) | ≤ 30 min | Within normal single-leg delay variance; a route can land here even with every transfer Green, purely from ordinary delay-bucket noise |
| Yellow (Risky) | 30–60 min | About one recoverable missed connection / one Service Frequency headway cycle (§2.6) |
| Red (Danger) | > 60 min | Compounding misses, or a fallback that itself costs real time |

This is a distinct signal from the Local Risk classification (§5.3): Global Health answers "how much slack does this route have overall," while Local Risk answers "which specific transfer should I watch." A route with an all-Green transfer timeline can still show a Yellow card edge — that's expected, not a bug: ordinary per-leg delay variance alone routinely pushes P85 into the 20–30 minute range, so the 30/60 split is calibrated against that baseline rather than against a "no risk should ever look risky" assumption. Global Health also applies uniformly regardless of transfer count — a direct (0-transfer) route is colored by its own P85 penalty like any other route, not exempted (see `UIUX_SPEC.md` §2.3 for the resulting change from the pre-Impact-Weighted-Thresholds behavior).

### 5.3 Route Detail View

Selecting a card opens a **horizontal timeline**: leg → transfer → leg → transfer → ..., left to right. Each transfer node is color-coded by a two-layer **Local Risk** classification — a base probability band with an impact-aware override, rather than probability alone:

1. **Base Probability Band** — `P(miss)` from §3.1: Green < 10%, Yellow 10–30%, Red > 30%.
2. **Impact Override** — if the base band is Red, but the transfer's precomputed impact (`impact_minutes`, §3.4) is **≤ 15 min**, the displayed band downgrades to Yellow. The underlying miss probability shown in the label is unchanged — only the color/phrase (`UIUX_SPEC.md` §1.3) softens, reflecting that a fast, already-known alternative exists rather than a real disruption.

| Impact (`impact_minutes`) | Displayed band when base is Red |
|---|---|
| ≤ 15 min | Yellow (override) |
| > 15 min | Red (unchanged) |

This exists to separate genuinely fatal transfers (a long, costly wait) from statistically-red-but-practically-harmless ones (a fast reroute, or a fallback that even arrives early, already covers the miss) — without it, the transfer strip flags every >30%-miss connection identically regardless of how bad actually missing it would be.

This gives an at-a-glance risk story for that specific journey, complementing the Global Health signal from §5.2.

**Implementation status:** both rules are wired in — `engine.py` exposes `impact_minutes` (`TransferRisk`) and `p85_penalty_minutes` (`RouteSimulationResult`) alongside the existing simulation outputs, and `ui_components.py` consumes them for the transfer-strip and card-edge coloring. One refinement beyond the rules as originally specified here: any base-Red transfer — not just one downgraded by the Impact Override — renders its trailing figure as the fallback's absolute arrival clock time rather than the scheduled buffer, since a base-Red transfer's buffer is uninformative regardless of whether the override fires (`UIUX_SPEC.md` §1.3, §5 history #16–#19).

## 6. Milestones Retrospective

### 6.1 Phase 1 — Mock-data prototype

Built the mock JSON dataset (`mock_data.json`: stations, lines, legs, transfers, routes) conforming to §2, the simulation engine per §3, and the Streamlit views per §5. This is the baseline the Pydantic contract and Monte Carlo algorithm were designed against, and both have stayed algorithmically unchanged through Phases 2 and 3.

### 6.2 Phase 2 — Real GTFS.DE + piebro-delay pipeline

Replaced the hand-authored mock timetable with a real data pipeline: GTFS.DE static feed ingestion, historical delay bucketing from the piebro archive, an ID crosswalk expanding the corridor to a 33-station "Golden 35" network, and on-demand candidate-route search extended to 2 transfers. Output: `data/real_dataset.json` (§4.2), one Pydantic-validated snapshot per build, scoped to a single fixed calendar date. Full detail: `DATA_SPEC.md` §3–§5, §7–§9.

### 6.3 Phase 3 — DuckDB warehouse & dynamic calendar dates

Migrated the storage backend to a DuckDB warehouse of date-agnostic templates plus GTFS calendar data (§4.3), so route search and simulation can be scoped to **any date in the ingested calendar window** at query time instead of one date baked in at build time — while keeping the Monte Carlo hot loop and O(1) fallback-plan cache (§3.4–§3.5) untouched. Added the UI date picker (§5.1), a third selectable data source, and `requirements.txt` for reproducible installs. Confirmed against the real corridor: a full month (2026-08-22 .. 2026-09-21) ingests into a 9MB warehouse file, and different dates genuinely surface different route topologies (e.g. a Saturday-only direct Frankfurt–Köln service that doesn't exist on weekdays). Full detail: `DATA_SPEC.md` §6.

### 6.4 Post-Phase-3 hardening — corridor connectivity & query performance

Real-feed testing against the Phase 3 warehouse surfaced a data-completeness bug and, once fixed, the performance issues it had been masking. Three fixes landed together:

- **Corridor-aware leg construction** (`DATA_SPEC.md` §3.2): fixed a bug where any trip with a non-corridor stop between two corridor hubs — near-universal on long-distance runs — silently lost that connection instead of being modeled with an extra stop. Recovered ~1,000 previously-disconnected long-distance trips from the real feed without expanding the 33-station corridor whitelist (`DATA_SPEC.md` §9.1).
- **N+1 query fix** (`DATA_SPEC.md` §6.3.1, §3.5 above): the richer graph the leg fix produced exposed a latent per-leg/per-line query pattern in `route_search_duckdb.find_candidate_routes`; batching to one query per hop cut a single search from 584 DuckDB round trips to 7, plus a matching index-reuse fix for the JSON backend (§3.5).
- **Caching/pagination split** (§4.4, §5.1): replaced a single combined search+simulate cache keyed on the pagination window with separate route-search and per-route simulation caches, so "Load more" only ever pays for newly-revealed routes instead of re-simulating everything already loaded.

Combined effect, measured against the real corridor: a Leipzig→Munich search that previously surfaced nothing before 09:27 now finds a 07:26 departure, and a full search + 5-route simulate went from ~30s to ~2.15s.

### 6.5 What's next

See §7 for the current Phase 4+ candidate list.

## 7. Future Roadmap & Extensions (Phase 4+)

Carried forward from the original design consensus, plus candidates identified once Phase 3 was in production:

- **Correlated delay sampling** — same-physical-train carryover between legs, and/or regional "bad day" latent factors shared across legs on the same network.
- **Minimum Connection Time (MCT) and platform-aware transfer buffer times** — MCT as a station-level property distinct from scheduled buffer (to flag connections risky even with zero delay, e.g. tight platform changes at large hubs), and/or a lighter platform-change flag as a cheaper proxy. GTFS's `stop_times.txt` carries platform-level stop assignments the current `Transfer.scheduled_buffer_minutes` model discards; using them is the natural next step once MCT is prioritized.
- **Unbounded recursive re-routing** — §3.4 covers exactly one re-routing search per missed transfer; a miss *within* a fallback route still resolves via the same-line-headway wait rather than searching again.
- **Advanced simulation controls** in the UI — exposing iteration count, risk-aversion weighting, or minimum acceptable buffer to the user.
- **Full distribution histogram output** per route, instead of just mean + percentile band.
- **Multi-day/multi-date batch simulation** — comparing risk across several dates in one view, now that Phase 3 makes any single date queryable.
- **Live/real-time GTFS-RT integration** — `DATA_SPEC.md`'s offline-only decision (§1) stands for now; this would revisit it.
- **RAPTOR- or CSA-style routing** to replace the current 2-transfer, precomputed-transfer-template search (`DATA_SPEC.md` §5). The transfer-template approach is quadratic-ish in arrivals × departures per station and already produces ~53k transfer templates from ~4k legs on the Golden 35 corridor (`DATA_SPEC.md` §10) — a bigger corridor or a higher transfer cap needs a real time-expanded or round-based algorithm instead of enumerating transfer pairs up front.
- **Backend/frontend decoupling** — extract the simulation engine behind a FastAPI service, with a React/Vite PWA frontend replacing the current Streamlit UI (§5). Streamlit's per-rerun execution model is a good fit for prototyping and demoing, but a dedicated API + SPA frontend would be needed for real concurrent-user load, mobile installability, and decoupled deploys of the algorithm vs. the UI.
- **Post-midnight cross-day service lookback** for the calendar query (§4.3) — a date's search currently only considers that date's own active services (`DATA_SPEC.md` §10).
