# DB Risk & Rescue — System Design Specification

**Phase:** Phase 3 implemented and shipped (commit `f0d4da2`)
**Date:** 2026-08-23 (revised same day — §2.6 added, §3.2 Step 4 amended); revised 2026-08-24 — §3.4 added, promoting "best-alternative-route re-search on miss" from §5 into v1 scope now that the 2-transfer route search (DATA_SPEC.md §5, §8) gives it real candidate routes to search over; revised 2026-08-24 (later same day) — §6 added, bringing "DuckDB Migration" and "Dynamic Calendar Dates" into scope as Phase 3; revised 2026-08-24 (Phase 3 complete) — §6 reworded from design plan to as-built summary, §7 repurposed as a milestones retrospective, §5 folded in Phase 4 candidates.
**Status:** Phases 1–3 built, tested, and merged to `main`. See "Milestones at a glance" below and §7 for detail; §5 for what's next.

**Milestones at a glance** (detail in §7): Phase 1 shipped the mock-data prototype (§2 data contract, §3 algorithm, §4 UI). Phase 2 replaced the mock timetable with a real GTFS.DE + piebro-delay pipeline (DATA_SPEC.md), still baked to one fixed calendar date. Phase 3 (§6) migrated storage to a DuckDB warehouse of date-agnostic templates (DATA_SPEC.md §9), so the UI date picker can scope a search to any date in the ingested GTFS calendar window — all three backends remain selectable side by side in `app.py`.

## 1. Objective

A Streamlit application for German railway (DB) trip planning that goes beyond shortest-path routing by quantifying the risk of missed connections and surfacing a probability-aware "True Expected Time of Arrival" (True ETA), computed via Monte Carlo simulation over historical delay behavior.

## 2. Data Model (Backend-Agnostic Contract)

The shapes below are `models.py`'s Pydantic contract — the *only* interface `engine.py`'s simulation and `ui_components.py`'s rendering consume. As of Phase 3, three interchangeable backends produce these exact objects at query time (a hand-authored JSON fixture, a GTFS-pipeline JSON snapshot, and a DuckDB warehouse queried per search — see DATA_SPEC.md §9); a backend that can't produce this shape is wrong, not the model. The name "Mock Data JSON Schema" below is historical — Phase 1's mock JSON file happens to serialize exactly this shape, which is why the field-level examples are still shown as JSON.

### 2.1 Station

```json
{
  "station_id": "DE_FRA_HBF",
  "name": "Frankfurt(Main)Hbf"
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

A transfer connects the arrival of leg N to the departure of leg N+1 at a shared station. **v1 uses scheduled buffer only** — no separate Minimum Connection Time layer, no platform-change flag. (See §5, Future Extensions.)

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

v1 mock data contains only the specific legs referenced by routes — there is no full timetable to query for "what's the next train." To resolve missed connections (§3.2 Step 4) without authoring a full timetable, service frequency is modeled as a **static lookup keyed by each Line's existing `type` field**, not as a new per-line or per-line-instance field:

| Line type | Assumed headway (minutes) |
|---|---|
| ICE / IC | 60 |
| RE / RB | 60 |
| S-Bahn | 20 |

This table is a fixed constant in the simulation engine (not part of the mock JSON files), keeping mock-data authoring effort unchanged while still giving the algorithm a realistic, tunable notion of "how often does this line run." Values are placeholders to be sanity-checked against real DB frequencies during prototyping.

## 3. Core Algorithm: Delay Propagation & Risk Scoring

### 3.1 Missed-Connection Probability (per transfer)

Direct CDF lookup against the upstream leg's delay distribution:

```
P(miss | transfer T) = P(delay_from_leg > T.scheduled_buffer_minutes)
                      = sum of delay_distribution_minutes[bucket]
                        for every bucket > T.scheduled_buffer_minutes
```

Example: buffer = 12 min, upstream leg's distribution `{"0": .60, "5": .20, "15": .12, "30": .06, "60": .02}` → `P(miss) = .12 + .06 + .02 = 0.20`.

This value is what drives the risk color-coding on the UI timeline (§4.2).

### 3.2 Monte Carlo Simulation (per route, per run)

For each route, run N iterations (default N = 1,000). Each iteration:

1. For every leg in the route, independently sample a realized delay from that leg's `delay_distribution_minutes` (independent per-leg sampling for v1 — no cross-leg correlation; see §5).
2. Walk the route leg-by-leg. At each transfer, compare realized arrival time of the upstream leg against the downstream leg's scheduled departure.
3. **If the connection holds** (realized arrival + buffer check passes): carry the realized delay forward and continue.
4. **If the connection is missed**: resolve by computing the **next periodic departure of the downstream leg's line**, since no full timetable exists to query (§2.6). Treat the downstream leg's own `scheduled_departure` as an anchor slot, and its line `type` as the key into the Service Frequency Reference Table (§2.6) to get headway `F`. The next available departure is:

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

Both values, plus the per-transfer miss probabilities from §3.1, are what the UI renders (§4).

### 3.4 Dynamic Re-routing on Miss (v1.1)

When §3.2 Step 4 triggers (a transfer is missed), rather than always waiting for the next periodic departure of the *same* downstream line, first search for a better alternative: the best candidate route from the missed transfer's station to the journey's final destination, using the existing route search (DATA_SPEC.md §5, §8) subject to a **reduced remaining transfer budget** (the app-wide 2-transfer cap, minus the transfers already used reaching the missed connection). If such a route exists, the simulation switches onto it for the remainder of that iteration; if none exists, it falls back to the original §3.2 Step 4 same-line-headway wait unchanged.

This route search is **not** re-run per Monte Carlo iteration — it is prohibitively expensive at N=1,000+ iterations. Instead, for a given route, the best fallback (if any) is **pre-computed once per transfer node before the simulation loop starts**, and each iteration performs only an O(1) cache lookup on miss.

Scope boundary: this is one level of re-routing. If a transfer *within* a fallback route is itself missed, that nested miss resolves via the original same-line-headway wait (§3.2 Step 4), not a second re-routing search — unbounded recursive re-pathfinding remains deferred (§5).

## 4. UI Layout (Streamlit Dashboard)

### 4.1 Input Flow

Minimal-friction search: user selects **origin, destination, and departure time**. The app auto-generates candidate routes from the mock timetable data and immediately runs the Monte Carlo simulation with default parameters (N = 1,000 iterations) — no advanced/simulation-tuning panel in v1.

As of Phase 3 (§6), the input flow gains a fourth field — **service date** — constrained to the GTFS feed's published calendar window (§6.2). Candidate-route generation and the Monte Carlo simulation are both scoped to whichever calendar date the user picks, in addition to origin/destination/time.

### 4.2 Route Comparison View

A **ranked card list** of candidate routes (typically 3–5), each card showing:

- Scheduled departure/arrival and duration
- Mean True ETA
- P85 True ETA (risk-adjusted)
- Number of transfers

Cards are sortable/orderable so the user can compare "fastest scheduled" against "safest / lowest-risk" at a glance — similar to a flight-search results list.

### 4.3 Route Detail View

Selecting a card opens a **horizontal timeline**: leg → transfer → leg → transfer → ..., left to right. Each transfer node is color-coded by its `P(miss)` from §3.1 (e.g. green < 10%, yellow 10–30%, red > 30% — exact thresholds to be tuned during prototyping). This gives an at-a-glance risk story for that specific journey, complementing the aggregate card-level stats from §4.2.

## 5. Future Extensions (Phase 4+, explicitly out of scope today)

Carried forward from the original v1 design consensus, plus Phase 4 candidates identified once Phase 3 was in production:

- **Correlated delay sampling** — same-physical-train carryover between legs, and/or regional "bad day" latent factors shared across legs on the same network.
- **Minimum Connection Time (MCT) and platform-aware transfer buffer times** — MCT as a station-level property distinct from scheduled buffer (to flag connections risky even with zero delay, e.g. tight platform changes at large hubs), and/or a lighter platform-change flag as a cheaper proxy. GTFS's `stop_times.txt` carries platform-level stop assignments the current `Transfer.scheduled_buffer_minutes` model discards; using them is the natural next step once MCT is prioritized.
- **Unbounded recursive re-routing** — §3.4 covers exactly one re-routing search per missed transfer; a miss *within* a fallback route still resolves via the same-line-headway wait rather than searching again.
- **Advanced simulation controls** in the UI — exposing iteration count, risk-aversion weighting, or minimum acceptable buffer to the user.
- **Full distribution histogram output** per route, instead of just mean + percentile band.
- **Multi-day/multi-date batch simulation** — comparing risk across several dates in one view, now that Phase 3 makes any single date queryable.
- **Live/real-time GTFS-RT integration** — DATA_SPEC.md's offline-only decision (§1) stands for now; this would revisit it.
- **RAPTOR- or CSA-style routing** to replace the current 2-transfer, precomputed-transfer-template search (`pipelines/route_search.py` / `pipelines/route_search_duckdb.py`, DATA_SPEC.md §5). The transfer-template approach is quadratic-ish in arrivals × departures per station and already produces ~53k transfer templates from ~4k legs on the Golden 35 corridor (DATA_SPEC.md §10) — a bigger corridor or a higher transfer cap needs a real time-expanded or round-based algorithm instead of enumerating transfer pairs up front.
- **Backend/frontend decoupling** — extract the simulation engine behind a FastAPI service, with a React/Vite PWA frontend replacing the current Streamlit UI (§4). Streamlit's per-rerun execution model is a good fit for prototyping and demoing, but a dedicated API + SPA frontend would be needed for real concurrent-user load, mobile installability, and decoupled deploys of the algorithm vs. the UI.

## 6. Phase 3: Database Engine Upgrade & Dynamic Calendar Dates (completed)

Added to scope 2026-08-24, implemented and merged the same day (commit `f0d4da2`). Phase 2 (DATA_SPEC.md) replaced the hand-authored mock timetable with a real GTFS.DE + piebro-delay pipeline, but still wrote its output as one JSON file scoped to a single, fixed `service_date` baked in at build time (DATA_SPEC.md §3 step 1, §8 step 5). Phase 3 removed that single-date constraint by migrating to a DuckDB-backed store, so a multi-day GTFS calendar window doesn't have to be loaded into memory (or re-baked into a fresh multi-megabyte JSON file per day) to support it. Full schema and query-time mechanics: DATA_SPEC.md §9.

### 6.1 Objective (met)

Let the user pick **any date within the ingested GTFS calendar window** and get a route search + Monte Carlo simulation scoped to that date's actual active services, while keeping the app's request-time memory footprint and the Monte Carlo hot loop's performance the same as the old single-date JSON build. Confirmed against the real corridor: a full month (2026-08-22 .. 2026-09-21) ingests into a 9MB warehouse file, and different dates genuinely surface different route topologies (e.g. a Saturday-only direct Frankfurt–Köln service that doesn't exist on weekdays).

### 6.2 Scope (as built)

1. **DuckDB migration** — a new `pipelines/build_warehouse.py` orchestrator (sibling to, not a replacement of, `pipelines/build_dataset.py`) writes topology and delay data in date-agnostic form to `data/warehouse.duckdb`, queried per-search rather than loaded whole. `pipelines/build_dataset.py` and `data/real_dataset.json` are untouched — the Phase 2 JSON path keeps working unmodified alongside the new one.
2. **Dynamic calendar** — GTFS `calendar.txt` (weekday pattern + date range) and `calendar_dates.txt` (single-date exceptions), parsed by the new `pipelines/calendar_ingest.py`, are ingested and preserved as queryable data instead of being collapsed into one date at build time the way `pipelines/gtfs_scope.py`'s single-date `scope_gtfs_feed` still does for the Phase 2 path. Service-date resolution is a query-time SQL operation (`pipelines/route_search_duckdb.py`), re-run per search.
3. **UI date picker** — `app.py` gained a date input, constrained to the GTFS feed's covered date range (`route_search_duckdb.calendar_window`), alongside the existing origin/destination/time inputs (§4.1).
4. **Data access layer** — the new `pipelines/route_search_duckdb.py` queries the DuckDB store dynamically per selected date instead of filtering an in-memory `dataset.legs`/`dataset.transfers` list. `models.py` (Station/Line/Leg/Transfer/Route) and `engine.py`'s simulation logic remained the query-time contract and needed **no changes** to their core algorithm — `engine.py` only gained one additive `route_search_fn` parameter on `precompute_fallback_plans` so it can be pointed at either backend, precisely the same "don't touch the simulation core" boundary DATA_SPEC.md §1 established for Phase 2's JSON pipeline.

### 6.3 Non-negotiable constraints (held)

- The O(1)-per-iteration fallback-plan cache (§3.4) stays O(1): fallback search still runs once per transfer node before the Monte Carlo loop, never inside it, whether that one-time search queries an in-memory list or DuckDB. Verified by `test_route_search_duckdb.py`'s end-to-end `precompute_fallback_plans` + `simulate_route` integration test.
- `engine.py`'s Monte Carlo hot loop (`simulate_route`'s per-iteration sampling) never touches the database — it consumes plain in-memory `Leg`/`Transfer` objects assembled once per search, populated by `route_search_duckdb.find_candidate_routes` mutating caller-supplied dicts in place rather than the whole network being loaded up front.
- `models.py`'s Pydantic contract (§2) did not change shape; Phase 3 changed *where data comes from*, not the objects `engine.py`/`ui_components.py` consume.

### 6.4 Out of scope for Phase 3 (still deferred — see §5)

Everything in §5 remains deferred. Specifically still out of scope after Phase 3: multi-day/multi-date batch simulation, live/real-time GTFS-RT integration (DATA_SPEC.md's offline-only decision stands), any change to the 2-transfer route-search cap (DATA_SPEC.md §5), and post-midnight cross-day service lookback for the calendar query (a date's search only considers that date's own active services — DATA_SPEC.md §10).

## 7. Development Milestones

### 7.1 Phase 1 — Mock-data prototype

Built the mock JSON dataset (`mock_data.json`: stations, lines, legs, transfers, routes) conforming to §2, the simulation engine per §3, and the Streamlit views per §4. This is the baseline the Pydantic contract (§2) and Monte Carlo algorithm (§3) were designed against, and both have stayed algorithmically unchanged through Phases 2 and 3.

### 7.2 Phase 2 — Real GTFS.DE + piebro-delay pipeline (DATA_SPEC.md)

Replaced the hand-authored mock timetable with a real data pipeline: GTFS.DE static feed ingestion (`pipelines/gtfs_ingest.py`), historical delay bucketing from the piebro archive (`pipelines/delay_aggregation.py`, `pipelines/delay_mapping.py`), an ID crosswalk expanding the corridor to a 33-station "Golden 35" network (`pipelines/id_crosswalk.py`), and on-demand candidate-route search extended to 2 transfers (`pipelines/route_search.py`). Output: `data/real_dataset.json`, one Pydantic-validated snapshot per build, scoped to a single fixed calendar date. Still a fully supported data source in `app.py`.

### 7.3 Phase 3 — DuckDB warehouse & dynamic calendar dates (§6, DATA_SPEC.md §9)

Migrated the storage backend to a DuckDB warehouse of date-agnostic `leg_templates`/`transfer_templates` plus GTFS calendar data, so route search and simulation can be scoped to **any date in the ingested calendar window** at query time instead of one date baked in at build time — while keeping `engine.py`'s Monte Carlo hot loop and O(1) fallback-plan cache (§3.4) untouched. Added the UI date picker (§4.1), a third selectable data source in `app.py`, and `requirements.txt` for reproducible installs. Full detail: §6, DATA_SPEC.md §9.

### 7.4 What's next

See §5 for the current Phase 4+ candidate list (backend/frontend decoupling, RAPTOR/CSA routing, platform-aware transfer buffers, and the rest).
