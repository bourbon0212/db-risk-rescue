# DB Risk & Rescue — System Design Specification

**Phase:** System Design (pre-implementation)
**Date:** 2026-08-23 (revised same day — §2.6 added, §3.2 Step 4 amended)
**Status:** Consensus reached — ready for prototyping phase

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

## 4. UI Layout (Streamlit Dashboard)

### 4.1 Input Flow

Minimal-friction search: user selects **origin, destination, and departure time**. The app auto-generates candidate routes from the mock timetable data and immediately runs the Monte Carlo simulation with default parameters (N = 1,000 iterations) — no advanced/simulation-tuning panel in v1.

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
- **Best-alternative-route re-search on miss** — instead of assuming the next departure on the same line, re-run pathfinding across all available lines from the failed station.
- **Advanced simulation controls** in the UI — exposing iteration count, risk-aversion weighting, or minimum acceptable buffer to the user.
- **Full distribution histogram output** per route, instead of just mean + percentile band.

## 6. Next Phase

With this spec agreed, the next phase is prototyping: build the mock JSON dataset (stations, lines, legs, transfers, routes) conforming to §2, implement the simulation engine per §3, and build the Streamlit views per §4.
