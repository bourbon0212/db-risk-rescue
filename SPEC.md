# DB Risk & Rescue — System Design Specification

**Phase:** System Design (pre-implementation)
**Date:** 2026-08-23 (revised same day — §2.6 added, §3.2 Step 4 amended); revised 2026-08-24 — §3.4 added, promoting "best-alternative-route re-search on miss" from §5 into v1 scope now that the 2-transfer route search (DATA_SPEC.md §5, §8) gives it real candidate routes to search over; revised 2026-08-24 (later same day) — §6 added, bringing "DuckDB Migration" and "Dynamic Calendar Dates" into scope as Phase 3.
**Status:** Consensus reached — Phase 3 (§6) design plan pending approval before implementation

## 1. Objective

A Streamlit application for German railway (DB) trip planning that goes beyond shortest-path routing by quantifying the risk of missed connections and surfacing a probability-aware "True Expected Time of Arrival" (True ETA), computed via Monte Carlo simulation over historical delay behavior.

## 2. Data Model (Mock Data JSON Schema)

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

## 5. Future Extensions (explicitly out of scope for v1)

These were raised and deliberately deferred during design consensus, to keep v1 buildable and legible:

- **Correlated delay sampling** — same-physical-train carryover between legs, and/or regional "bad day" latent factors shared across legs on the same network.
- **Minimum Connection Time (MCT)** as a station-level property distinct from scheduled buffer, to flag connections that are risky even with zero delay (e.g. tight platform changes at large hubs).
- **Platform-change flag** on transfers as a cheaper proxy for MCT.
- **Unbounded recursive re-routing** — §3.4 (added 2026-08-24) covers exactly one re-routing search per missed transfer; a miss *within* a fallback route still resolves via the same-line-headway wait rather than searching again.
- **Advanced simulation controls** in the UI — exposing iteration count, risk-aversion weighting, or minimum acceptable buffer to the user.
- **Full distribution histogram output** per route, instead of just mean + percentile band.

## 6. Phase 3: Database Engine Upgrade & Dynamic Calendar Dates (in scope)

Added 2026-08-24. Phase 2 (DATA_SPEC.md) replaced the hand-authored mock timetable with a real GTFS.DE + piebro-delay pipeline, but still wrote its output as one JSON file scoped to a single, fixed `service_date` baked in at build time (DATA_SPEC.md §3 step 1, §8 step 5). Phase 3 removes that single-date constraint and migrates the storage backend so a multi-day GTFS calendar window doesn't have to be loaded into memory (or re-baked into a fresh multi-megabyte JSON file per day) to support it.

### 6.1 Objective

Let the user pick **any date within the ingested GTFS calendar window** and get a route search + Monte Carlo simulation scoped to that date's actual active services, while keeping the app's request-time memory footprint and the Monte Carlo hot loop's performance the same as today's single-date JSON build.

### 6.2 Scope

1. **DuckDB migration** — `pipelines/build_dataset.py`'s ETL output moves from a single `data/real_dataset.json` (one Pydantic-validated snapshot, one fixed date) to a DuckDB-backed store that holds topology and delay data in date-agnostic form, queried per-search rather than loaded whole.
2. **Dynamic calendar** — GTFS `calendar.txt` (weekday pattern + date range) and `calendar_dates.txt` (single-date exceptions) are ingested and preserved as queryable data, not collapsed into one date at build time the way `pipelines/gtfs_scope.py`'s `_active_service_ids` currently does. Service-date resolution becomes a query-time operation.
3. **UI date picker** — `app.py` gains a date input, constrained to the GTFS feed's covered date range, alongside the existing origin/destination/time inputs (§4.1).
4. **Data access layer** — `route_search.py` and `app.py` query the DuckDB store dynamically per selected date instead of filtering an in-memory `dataset.legs`/`dataset.transfers` list, while `models.py` (Station/Line/Leg/Transfer/Route) and `engine.py`'s simulation logic remain the query-time contract and stay unchanged — precisely the same "don't touch the simulation core" boundary DATA_SPEC.md §1 established for Phase 2's JSON pipeline.

### 6.3 Non-negotiable constraints carried over from Phase 1/2

- The O(1)-per-iteration fallback-plan cache (§3.4) must stay O(1): fallback search still runs once per transfer node before the Monte Carlo loop, never inside it. Whether that one-time search queries an in-memory list or DuckDB, it happens the same number of times as today.
- `engine.py`'s Monte Carlo hot loop (`simulate_route`'s per-iteration sampling) must not touch the database at all — it consumes plain in-memory `Leg`/`Transfer` objects assembled once per search, exactly as it does today.
- `models.py`'s Pydantic contract (§2) does not change shape; Phase 3 changes *where data comes from*, not the objects `engine.py`/`ui_components.py` consume.

### 6.4 Out of scope for Phase 3

Everything in §5 remains deferred, unchanged by this phase. Also explicitly deferred here: multi-day/multi-date batch simulation (comparing risk across several dates at once), live/real-time GTFS-RT integration (DATA_SPEC.md's offline-only decision stands), and any change to the 2-transfer route-search cap (DATA_SPEC.md §5).

## 7. Next Phase

With this spec agreed, the next phase is prototyping: build the mock JSON dataset (stations, lines, legs, transfers, routes) conforming to §2, implement the simulation engine per §3, and build the Streamlit views per §4.

Phase 2 (DATA_SPEC.md) and Phase 3 (§6, pending a detailed architecture/schema design plan) extend this same core without altering §2's data contract or §3's algorithm.
