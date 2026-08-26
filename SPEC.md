# DB Risk & Rescue — System Design Specification

**This is the primary spec: what the system computes and why.** Read it if you're touching the risk maths, the simulation, or the search logic. If you just need a threshold value, jump straight to §6 — every constant in the app is tabulated there. New to the project? Start with [README.md](README.md); §3 opens with a worked example that's the fastest way into the risk model.

Data pipelines and storage live in `DATA_SPEC.md`; colours, wording and layout live in `UIUX_SPEC.md`.

**Section map:** §1 Objective · §2 Core Data Models · §3 Engine · §4 Storage · §5 UI Spec · §6 Core Thresholds & Constants · §7 Development History & Phases · §8 Limitations & Future Work
**Status:** Phases 1–3 built, tested, merged to `main`.

## 1. Objective & System Overview

A Streamlit app for German railway (DB) trip planning that quantifies missed-connection risk and surfaces a probability-aware **True Expected Time of Arrival (True ETA)**, computed via Monte Carlo simulation over historical delay behavior.

Four layers, each with its own section:

- **Data model** (§2) — a backend-agnostic Pydantic contract (Station/Line/Leg/Transfer/Route).
- **Engine** (§3) — risk scoring + Monte Carlo simulation over that contract, with O(1) dynamic re-routing on a missed connection.
- **Storage** (§4) — three interchangeable backends producing the §2 contract. Full detail: `DATA_SPEC.md`.
- **UI** (§5) — the Streamlit dashboard. Full visual/wording detail: `UIUX_SPEC.md`.

## 2. Core Data Models (Pydantic Contracts)

`models.py`'s Pydantic contract is the *only* interface `engine.py` and `ui_components.py` consume. All storage backends (§4) produce these exact objects at query time.

The five types below travel together in a `MockDataset` — the whole-dataset container `data_loader.py` returns and `routing/route_search.py` searches over. **Despite the name, it is not the Mock backend**: all three backends produce one. The name dates from Phase 1, when the Mock fixture was the only source.

### 2.1 Station

```json
{"station_id": "DE_FRA_HBF", "name": "Frankfurt(Main) Hbf", "mct_minutes": 5}
```

`mct_minutes` (default 5) is the station's Minimum Connection Time, assigned by station-tier classification at ingestion (§3.6.1, values in §6) — not feed data. Lives on Station, not Transfer, since it's a property of the physical station, not of any specific pair of legs through it.

### 2.2 Line

```json
{"line_id": "ICE_15", "type": "ICE", "operator": "DB Fernverkehr"}
```

### 2.3 Leg

One uninterrupted ride on one train. Delay is modeled as an **empirical probability distribution over discrete delay buckets** (minutes late) — matches how DB publishes punctuality stats, and is directly samplable without curve-fitting.

```json
{
  "leg_id": "L1", "line_id": "ICE_15",
  "origin_station_id": "DE_FRA_HBF", "destination_station_id": "DE_KOL_HBF",
  "scheduled_departure": "2026-08-23T09:02:00", "scheduled_arrival": "2026-08-23T10:14:00",
  "delay_distribution_minutes": {"0": 0.60, "5": 0.20, "15": 0.12, "30": 0.06, "60": 0.02},
  "origin_platform": "7", "destination_platform": "3"
}
```

Bucket probabilities must sum to 1.0. A realized delay is filed under the largest boundary not exceeding it, so bucket `15` holds delays of 15–29 minutes (`DATA_SPEC.md` §4). §3.1 reads the buckets above a transfer's buffer as a tail sum.

`origin_platform`/`destination_platform` (nullable) carry GTFS `platform_code` where the feed has one — real but uneven coverage, concentrated in a few corridor stations (`DATA_SPEC.md` §3.3). `ui_components.py` renders a platform pair only when both legs of a transfer have one; otherwise hidden, not placeholder text.

### 2.4 Transfer

Connects leg N's arrival to leg N+1's departure at a shared station.

```json
{"transfer_id": "T1", "station_id": "DE_KOL_HBF", "from_leg_id": "L1", "to_leg_id": "L2", "scheduled_buffer_minutes": 12}
```

`scheduled_buffer_minutes` = `L2.scheduled_departure - L1.scheduled_arrival`, stored not recomputed. It's a pure schedule fact — Minimum Connection Time is deliberately *not* a field here; it's applied downstream from Station (§3.6), not baked into the transfer.

### 2.5 Route

```json
{
  "route_id": "R1", "legs": ["L1", "L2"], "transfers": ["T1"],
  "origin_station_id": "DE_FRA_HBF", "destination_station_id": "DE_BER_HBF",
  "scheduled_departure": "2026-08-23T09:02:00", "scheduled_arrival": "2026-08-23T13:40:00"
}
```

Single-leg routes have an empty `transfers` array.

### 2.6 Service Frequency Reference Table

No full timetable to query "what's the next train" — service frequency is a static lookup keyed by `Line.type` (values: §6), used to resolve missed connections (§3.2 step 4). Fixed constant in the engine (`engine.SERVICE_FREQUENCY_MINUTES`), not derived from data — placeholders sanity-checked against real DB frequencies, not empirically fit.

## 3. Routing & Monte Carlo Simulation Engine

**Start here: one transfer, end to end.** The subsections below derive each piece separately. This is all of them applied to a single real transfer from the current warehouse — Köln → München on 2026-08-24, changing at Mannheim Hbf.

| | Value | Where it comes from |
|---|---|---|
| Scheduled buffer | 26 min | The timetable: next departure − this arrival (§2.4) |
| Station MCT | 10 min | Mannheim is a major hub, so the 10-minute tier (§3.6.1) |
| Upstream delay history | ICE 43: `{0: .37, 5: .25, 15: .17, 30: .14, 60: .07}` | Aggregated from the delay archive (`DATA_SPEC.md` §4) |

Now the five steps (§5.3):

1. **Raw probability** — sum the buckets that exceed the 26-minute buffer: `0.14 + 0.07 = 0.21`. A 21% chance the ICE arrives too late to make this connection.
2. **MCT floor** — the buffer (26) comfortably clears Mannheim's MCT (10), so no floor applies. Had it been a 4-minute buffer, §3.6.2 would have raised the effective probability regardless of how punctual ICE 43 is.
3. **Band** — 21% falls in 10–30%, so: Yellow.
4. **Impact Override** — only ever rescues Red transfers, so it doesn't apply here.
5. **Phrase** — Yellow with no override reads **`Tight connection (21% risk) · 26 min`** (`UIUX_SPEC.md` §2.2).

Meanwhile the simulation runs 1,000 iterations over the whole route and reports a P85 arrival 90 minutes past schedule — which lands this route's *card edge* in the Red band (§5.2), even though its only transfer is Yellow. That divergence is the design working as intended: the transfer is probably fine, but when it isn't, the next München train is a long wait. **Local Risk answers "which connection should I watch"; Global Health answers "how bad is this route's tail."**

### 3.1 Missed-Connection Probability (per transfer)

Direct CDF lookup against the upstream leg's delay distribution:

```
P(miss | transfer T) = P(delay_from_leg > T.scheduled_buffer_minutes)
                      = sum of delay_distribution_minutes[bucket] for every bucket > buffer
```

Example: buffer=12, distribution `{0:.60, 5:.20, 15:.12, 30:.06, 60:.02}` → `P(miss) = .12+.06+.02 = 0.20`. Drives the risk color-coding on the UI timeline (§5.3, `UIUX_SPEC.md` §2).

### 3.2 Monte Carlo Simulation (per route, per run)

N iterations (default: §6). Each iteration:

1. Independently sample a realized delay per leg from its `delay_distribution_minutes` (no cross-leg correlation — §8.1).
2. Walk leg-by-leg; at each transfer, compare realized upstream arrival against the downstream leg's scheduled departure.
3. **Connection holds**: carry the realized delay forward.
4. **Connection missed**: try dynamic re-routing (§3.4) first; if no fallback, compute the next periodic departure of the downstream line using its `type`'s headway `F` (§2.6, §6):
   ```
   next_departure = leg.scheduled_departure + F * ceil((realized_arrival_time - leg.scheduled_departure) / F)
   ```
   Added wait is added to the running arrival time; the walk continues from `next_departure` (freshly sampled).
5. Record the final realized arrival time.

### 3.3 True ETA (output per route)

After N iterations: **Mean True ETA** (typical expectation) and **P85/P90 True ETA** (plan-for-this-if-unlucky). These, plus per-transfer miss probabilities (§3.1), feed the UI (§5).

Each transfer also reports a **`simulated_miss_rate`** — the fraction of iterations that actually missed it, counted only on the route's own transfers. Nothing renders it; it exists as the empirical check on §3.6.3's math, since it should converge to `max(analytic, mct_floor)`, and the test suite asserts exactly that.

### 3.4 Dynamic Re-routing on Miss (Fallback Caching)

When §3.2 step 4 triggers, the simulation looks for a better alternative from the missed station to the final destination, switching onto it for the rest of that iteration. If no candidate survives, it falls back to the same-line-headway wait unchanged.

**The search runs once per transfer node, never per iteration.** `engine.precompute_fallback_plans` resolves every transfer's best alternative before the Monte Carlo loop starts, so a miss costs an O(1) cache lookup rather than a fresh pathfinding query — the difference between one search and N=1,000 of them.

A candidate must clear all three filters:

| Filter | Rule | Why |
|---|---|---|
| Transfer budget | `len(candidate.transfers) ≤ MAX_TOTAL_TRANSFERS − (i + 1)` for the transfer at index `i` | Boarding the fallback is itself a transfer, so it consumes one of the app-wide cap's slots (§6) alongside the `i` already used getting here |
| Not the missed leg | The candidate can't start by boarding the very leg just missed | That train has already departed without the passenger |
| Station MCT | Upstream leg's scheduled arrival → candidate's first departure must be ≥ the station's `mct_minutes` | A physically implausible dash is never *offered* as a rescue |

MCT is a **hard veto** here, deliberately not §3.6.2's gradient floor: discarding one candidate among many is a different decision from scoring a transfer that must be displayed, so there's no need to soften it. Among survivors the earliest-arriving candidate wins, with `route_id` as a deterministic tie-break.

Scope: one level of re-routing. A miss *within* a fallback route resolves via headway wait, not a second search (§8.2).

**UI Impact Override input.** The same `FallbackPlan` feeds the UI's Impact Override (`UIUX_SPEC.md` §2): `impact_minutes = fallback_plan.route.scheduled_arrival - route.scheduled_arrival` when a plan exists; otherwise falls back to the headway-wait figure from step 4. Reading it for the UI is the same O(1) lookup the simulation already performs — no new computation.

### 3.5 Backend-Agnostic Fallback Search & the O(1) Guarantee

`precompute_fallback_plans` takes an optional `route_search_fn(origin_id, destination_id, departure_time) -> list[Route]`; omitting it defaults to an in-memory search over a loaded `MockDataset`. This let the DuckDB backend (§4) plug in without touching `precompute_fallback_plans` or `simulate_route`'s hot loop:

- §3.4's O(1) guarantee survives the swap, because it depends on *how many times* the search is called — bounded by (candidate routes × transfers per route), independent of N — not on what answers it. A DuckDB query is slower than a list scan, but it runs the same number of times.
- `simulate_route`'s per-iteration sampling never touches a database either way.
- Verified end-to-end by `tests/test_route_search_duckdb.py`.

For the JSON backend, `precompute_fallback_plans` also accepts prebuilt `search_indexes` (`routing.route_search.build_route_search_indexes`), reused across the top-level search and every fallback sub-search instead of rebuilding lookup tables from scratch each time — same O(1)-per-call-count discipline, applied to index cost instead of query count.

### 3.6 Minimum Connection Time (MCT) & the Gradient Risk Floor

§2.4's `scheduled_buffer_minutes` says nothing about whether a human can physically make a transfer even on time — a 3-minute change at a station with distant platforms is unrealistic regardless of punctuality. Left unaddressed, a below-MCT transfer at a punctual line's station could render as `Safe connection` since §3.1 never looks at the station at all.

#### 3.6.1 Station-tier MCT (the "5/10 minute rule")

Every Station carries `mct_minutes` (§2.1), assigned at ingestion by `pipelines.gtfs_ingest.classify_station_mct`. Neither real GTFS.DE feed ships a `transfers.txt`/`min_transfer_time` (confirmed against both archives) — this is a rule-based proxy, not feed data:

- Count leg endpoints (origin or destination) touching each station — a stand-in for interchange complexity in the absence of platform geometry.
- Stations at/above the 75th percentile of that distribution: **major hub**, `mct_minutes` = §6's hub value.
- Everyone else: `mct_minutes` = §6's standard value (also the Station default, e.g. `data/mock_data.json`).

A proposal to lower these thresholds was rejected: the touch-count classifier is already only a proxy, so shrinking the threshold discards the one signal it has without fixing the actual problem — which §3.6.2 addresses instead.

#### 3.6.2 Gradient risk floor, not a hard veto

A below-MCT transfer isn't rejected outright — the proxy is approximate, so treating "below MCT" as a binary impossibility would kill genuinely fine cross-platform transfers on a coarse hub/standard split. `engine.mct_violation_floor(buffer_minutes, mct_minutes)` scales *linearly* with how far under MCT the buffer is:

```
deficit_fraction = clamp((mct_minutes - buffer_minutes) / mct_minutes, 0, 1)
mct_floor         = deficit_fraction * MCT_VIOLATION_MAX_FLOOR   # value: §6
```

Buffer at/above MCT → no floor. Buffer 1 min short of a 10-min hub MCT → floor ≈0.095 (barely nudges risk). Buffer at/below 0 → floor caps at §6's max (never 1.0 — leaves room for a genuine cross-platform sprint the model can't rule out).

Effective miss probability everywhere downstream = `max(analytic_miss_probability, mct_floor)` — the floor only ever raises risk, never lowers it. A flat floor was considered and rejected: it reintroduces the same cliff-edge problem at a lower height (1 minute short of MCT would jump straight to the flat value, same as 0 minutes short).

#### 3.6.3 Monte Carlo integration

Enforced *inside* `simulate_route`'s per-iteration loop, not as a display-time patch — so the simulated ETA and the displayed risk can never contradict each other. Precomputed once per transfer before the loop:

```
extra_fail_probability = 0                                          if mct_floor <= analytic_miss_probability
                                                                      or analytic_miss_probability >= 1.0
                        = (mct_floor - analytic_miss_probability)
                          / (1 - analytic_miss_probability)          otherwise
```

chosen so `P(overall miss) = analytic + (1-analytic) * extra_fail_probability = max(analytic, mct_floor)` exactly. Per iteration, even when the schedule check says the connection holds, a Bernoulli draw against `extra_fail_probability` can still force a miss (the train was on time, but the walk itself wasn't physically possible).

Applies only to the route's own transfers, never inside a fallback plan. Zero extra cost when `extra_fail_probability = 0` — true for every caller that omits `stations_by_id`, keeping prior behavior bit-for-bit reproducible.

UI wording for MCT-affected transfers: `UIUX_SPEC.md` §2 — this section covers only the probability math, not how it displays.

### 3.7 Sanity Filter (Suboptimal-Path Pruning)

Route search finds every mathematically valid path with no notion of whether it's a *sane* choice — surfacing multi-hour detours (e.g. a 6h51m 2-transfer Köln→Frankfurt via Hannover) alongside a 90-minute direct train, wasting Monte Carlo compute on routes no one would take.

`routing.route_filters.apply_sanity_filter(routes, max_duration_ratio, max_additional_minutes)` (values: §6) drops any candidate whose scheduled duration exceeds the *tighter* of two bounds against the *fastest* duration in that search result — a ratio bound and a flat-minutes bound, both relative to what was actually found. Applied once to the top-level result, before display-limit slicing (§5.1) and before simulation.

**Why two bounds and not one.** *(Background — the evidence behind §6's two values. Skip unless you're retuning them.)* A pure ratio shipped first, then a sweep over every connected station pair showed why it isn't enough. Detour explosion is overwhelmingly a *short*-trip problem: pairs whose fastest route is under 60 min had a median worst-case ratio of 15.2x and reached 458x, while pairs over 240 min stayed tame (median 1.4x, none even reaching 2.5x) because the 2-transfer cap and real corridor connectivity bound how convoluted a long route can get.

That tameness is the trap. On a long trip a genuinely unacceptable detour barely registers as a ratio — Nürnberg Hbf → Hannover Hbf (fastest 4h00m) has a legitimate alternative cluster up to 6h36m and a separate detour cluster at 8h03m–10h03m, yet the whole detour cluster sits between 2.01x and 2.51x, so a 2.5x cap caught only the worst of seven. Six hours of avoidable travel is obviously unreasonable in absolute terms and unremarkable in relative ones, which is what the flat +150 min ceiling catches. It costs nothing on short trips, where the ratio is already the tighter bound: a re-sweep confirmed zero additional drops in the under-60-min bucket.

Not applied inside `find_candidate_routes` itself, and not wired into fallback search (§3.4/§3.5): that search already picks the single earliest-*arriving* candidate, and its pool can legitimately span a much wider time window than one search page — comparing raw duration risks discarding the soonest-arriving fallback for a merely shorter one departing hours later.

## 4. Storage & Data Access Architecture

Three interchangeable backends produce the §2 contract; `app.py`'s sidebar picks between them. Full schema/pipeline detail: `DATA_SPEC.md`.

### 4.1 Query-Time Contract & Backend Boundary

Every backend's job ends at producing `Station`/`Line`/`Leg`/`Transfer`/`Route` objects — `engine.py` and `ui_components.py` never branch on which backend is active. This boundary is what let Phase 3 replace the entire storage layer without touching the simulation core or UI rendering.

### 4.2 The Three Backends

The three names are *runtime* labels — what you pick in the sidebar. They line up one-to-one with the development phases that produced them (§7), which is why both vocabularies appear throughout the specs:

| Backend | Built in | Storage | Dates it can answer for | Detail |
|---|---|---|---|---|
| **Mock** | Phase 1 | `data/mock_data.json`, loaded whole | Whatever the fixture hardcodes | `DATA_SPEC.md` §7 |
| **Snapshot** | Phase 2 | `data/real_dataset.json`, loaded whole | One, fixed at build time | `DATA_SPEC.md` §7 |
| **Warehouse** *(default)* | Phase 3 | `data/warehouse.duckdb`, queried per search | Any date in the ingested window | `DATA_SPEC.md` §6 |

Reading the table top to bottom is also the project's arc: a hand-authored fixture, then real data pinned to one day, then real data queryable by date.

**Missing-data degradation.** `data/mock_data.json` and `data/real_dataset.json` are both committed, so Mock and Snapshot work on a fresh clone; only `data/warehouse.duckdb` is a gitignored build output. If the selected backend's file is absent, `app.py` shows a sidebar warning naming the build command and degrades to the best backend still on disk rather than erroring: **Warehouse → Snapshot → Mock**. This matters on a fresh clone or deploy, where Warehouse is the default selection but its file is never present — the app serves Snapshot there, real corridor data pinned to one date, and only reaches Mock if `data/real_dataset.json` is missing too. Degrading to the *best* available backend rather than the smallest is deliberate: on Streamlit Community Cloud the Mock fixture's default station pair returns no routes at all, so falling straight to it made a deploy look broken. A deploy can also skip the ladder entirely: when a `WAREHOUSE_URL` secret is set, `warehouse_fetch.ensure_warehouse()` downloads the missing file once before the fallback runs, and only its reason for failing — no secret, a dead URL, a body that isn't a database — reaches the sidebar warning (`DATA_SPEC.md` §8.3).

### 4.3 Streamlit Caching Strategy

Route search and Monte Carlo simulation are cached as **two separate stages** — `search_routes*` (search only) and `simulate_one_route*` (one route's simulation), both `@st.cache_data`.

The original combined cache was keyed on `display_limit` (§5.1's pagination window), so every "Load more" re-simulated the entire growing prefix. Splitting fixes this:

- `search_routes*` has no `display_limit` param — never re-run by pagination.
- `simulate_one_route*` is keyed on `route_id` (stable, derived from leg/transfer ids) plus `n_iterations`/`seed`/dataset path (or `service_date` for the warehouse). A "Load more" revealing routes 6–10 only computes those 5.
- Arguments that aren't part of cache identity (`_route`, `_search_indexes`, etc.) use a leading underscore so Streamlit excludes them from the hash.
- `app.py` times both stages with `time.perf_counter()`, printed to terminal.

Measured (Warehouse backend, real corridor): ~1.3s to 20 loaded routes, then ~0.9s per further "Load more" — flat per-click cost.

## 5. Streamlit UI Specification

Full visual/wording spec: `UIUX_SPEC.md`. This section covers input/output *logic* only.

### 5.1 Input Flow

User selects origin, destination, departure time; the DuckDB backend adds a service-date field (bounded to the ingested calendar window). N=§6 iterations, no tuning panel.

Routes paginate via a `display_limit` session-state counter (step: §6): the app simulates/shows only the first `display_limit` routes, with "Load more" adding one step. Resets on any search-parameter change. Routes are sliced to `display_limit` **before** the Monte Carlo loop, not just before rendering — simulation, not search, is the expensive part (§4.3). Everything downstream, sorting included (§5.2), therefore sees only the loaded batch.

### 5.2 Route Comparison View

A ranked card list (typically 3–5), each showing scheduled departure/arrival + duration, Mean True ETA, P85 True ETA, transfer count.

**Sorting.** Two options, both ranking by *arrival time* and differing only in which arrival they trust: **Earliest scheduled** sorts on `Route.scheduled_arrival` (the timetable's answer), **Safest arrival** on `p85_eta` (the risk-adjusted one). Neither sorts on duration — a later-departing, shorter trip can rank below an earlier-departing, longer one — and the labels say "earliest" rather than "fastest" precisely so they don't imply otherwise. Ranking against unsimulated routes would mean simulating them, defeating pagination, so sorting is scoped to the loaded batch (§5.1).

**Global Health** (card left-edge strip, `UIUX_SPEC.md` §3.4) is driven **solely by the P85 penalty** — `p85_penalty_minutes = P85 True ETA − Scheduled Arrival` — deliberately ignoring individual transfer probabilities. Bands: §6. It applies uniformly regardless of transfer count, including 0-transfer routes.

Global Health and Local Risk (§5.3) answer different questions — "how much slack does this route have overall" versus "which transfer should I watch" — and are computed from unrelated inputs, so they disagree routinely. An all-Green transfer timeline under a Yellow card edge is expected, not a bug: ordinary per-leg delay variance alone pushes P85 into the 20–30 minute range without any transfer being risky.

### 5.3 Route Detail View

A horizontal timeline (leg → transfer → leg → ...), each transfer node classified by **Local Risk** — five steps from raw probability to final phrase:

| Step | What happens | Defined in |
|---|---|---|
| 1. Raw probability | `P(miss) = P(delay > buffer)` | §3.1 |
| 2. MCT floor | Gradient floor added, never lowers the number | §3.6.2 |
| 3. Band the result | Green/Yellow/Red thresholds | §6 |
| 4. Impact Override | Red + cheap fallback (`impact_minutes` ≤ threshold, §6) displays Yellow | §3.4 |
| 5. Pick the phrase | Exact colors/phrases/conditions | `UIUX_SPEC.md` §2 |

Local Risk is per-transfer and independent of the card's edge color, which is per-route (§5.2).

## 6. Core Thresholds & Constants Reference

Every hardcoded constant in the app, in one place. If a value changes, update it here and cross-check every section above that cites it.

**Simulation**

| Constant | Value | Code location | Rationale |
|---|---|---|---|
| Monte Carlo iterations (N) | 1,000 | `app.py N_ITERATIONS` | Balances precision vs. per-search latency; no UI control (§5.1) |
| RNG seed | 42 | `app.py RNG_SEED` | Fixed so a route's ETAs stay stable across Streamlit reruns instead of drifting on every interaction; also part of the simulation cache key (§4.3) |
| Service frequency headway | ICE/IC 60min · RE/RB 60min · S-Bahn 20min | `engine.SERVICE_FREQUENCY_MINUTES` | Sanity-checked against real DB frequencies, not empirically fit (§2.6) |
| Max total transfers | 2 | `engine.MAX_TOTAL_TRANSFERS`, `route_search*.py` | Raised from 1 once the network needed it for some real pairs; deliberate ceiling, not a hard limit |

**Risk Classification (Local Risk, per transfer)**

| Constant | Value | Code location | Rationale |
|---|---|---|---|
| Risk band thresholds | Green <10% · Yellow 10–30% · Red >30% | `ui_components.RISK_LOW_MAX_PROBABILITY` / `RISK_MEDIUM_MAX_PROBABILITY` | Boundaries are exclusive at the low end, inclusive at the medium end (§5.3 step 3) |
| MCT station tiers | Major hub: 10 min · Standard: 5 min | `gtfs_ingest.classify_station_mct` | 75th-percentile touch-count split (§3.6.1) |
| MCT floor cap | 0.95 (`MCT_VIOLATION_MAX_FLOOR`) | `engine.py` | Never 1.0 — leaves room for a genuine cross-platform sprint (§3.6.2) |
| Impact Override threshold | `impact_minutes` ≤ 15 min | `ui_components.py` | Separates costly misses from cheaply-recoverable ones (§3.4, §5.3 step 4) |

**Risk Classification (Global Health, per route)**

| Constant | Value | Code location | Rationale |
|---|---|---|---|
| P85 penalty bands | Green ≤30min · Yellow 30–60min · Red >60min | `ui_components.py` | Calibrated against ordinary single-leg delay variance, not a "zero risk ever looks risky" assumption (§5.2) |

**Route Search & Filtering**

| Constant | Value | Code location | Rationale |
|---|---|---|---|
| Sanity filter ratio | 2.5x fastest duration | `route_filters.apply_sanity_filter` | Tighter of two bounds (§3.7) |
| Sanity filter flat ceiling | +150 min over fastest | `route_filters.apply_sanity_filter` | Closes the long-haul gap a pure ratio misses (§3.7) |
| Derived-transfer window | 2–60 minutes | `gtfs_ingest.derive_transfers` / `derive_transfer_templates` | Applied once at ingestion; route search consumes the resulting transfers and re-filters nothing (`DATA_SPEC.md` §9.4) |
| Display pagination step | 5 routes | `app.py DISPLAY_LIMIT_STEP` | §5.1 |

**Data Pipeline** (full detail: `DATA_SPEC.md`)

| Constant | Value | Code location | Rationale |
|---|---|---|---|
| Min. historical samples | 30 | `delay_aggregation.DEFAULT_MIN_SAMPLES` | Below this, fall back to train-type-level aggregate (`DATA_SPEC.md` §9.2) |
| Delay bucket scheme | 0, 5, 15, 30, 60 min | `delay_aggregation.bucket_delay` | Matches `engine.py`'s bucket interpretation (`DATA_SPEC.md` §4) |
| Corridor size | 33 stations ("Golden 35") | `pipelines/id_crosswalk.py` | `DATA_SPEC.md` §9.1 |

**Deployment**

| Constant | Value | Code location | Rationale |
|---|---|---|---|
| Warehouse URL secret name | `WAREHOUSE_URL` | `warehouse_fetch.SECRET_KEY` | Where a deploy fetches the gitignored warehouse from (`DATA_SPEC.md` §8.3) |
| Warehouse download timeout | 30 s | `warehouse_fetch.DOWNLOAD_TIMEOUT_SECONDS` | Per-read, not whole-transfer: a slow 58 MB fetch is fine, a stalled one isn't |
| Warehouse download chunk | 1 MiB | `warehouse_fetch.CHUNK_BYTES` | Same size `download_raw_data.py` streams its GTFS/Parquet downloads at |

## 7. Development History & Phases

| Phase | Shipped | Backend (§4.2) | Dataset |
|---|---|---|---|
| 1 — Mock prototype | Pydantic contract, MC engine, Streamlit UI | Mock | `data/mock_data.json`, hand-authored |
| 2 — Real pipeline | GTFS.DE + piebro ingestion, 33-station corridor, 2-transfer search | Snapshot | `data/real_dataset.json`, one fixed date |
| 3 — DuckDB warehouse | Date-agnostic templates + dynamic calendar resolution | Warehouse | `data/warehouse.duckdb`, full month window |
| 3.1 — Corridor & query hardening | Corridor-aware leg fix, N+1 query fix, cache/pagination split | Varies per fix (below) | Same warehouse (+ Snapshot's `data/real_dataset.json`, rebuilt) |
| 3.2 — MCT & platform capture | Station-tier MCT + gradient floor, platform capture | All three | Engine + UI wording addition |

**Phase 1.** Built the Mock dataset, engine, and UI together — the baseline the Pydantic contract and MC algorithm were designed against. Phases 2 and 3 swapped the storage layer beneath that contract without changing it; the only later engine change was Phase 3.2's MCT floor.

**Phase 2.** Replaced the Mock timetable with real GTFS.DE ingestion + piebro historical delay bucketing, an ID crosswalk expanding to the 33-station corridor, and search extended to 2 transfers. Output: one Pydantic-validated snapshot per build.

**Phase 3.** Migrated storage to a DuckDB warehouse of date-agnostic templates + GTFS calendar data, so any date in the ingested window is queryable at query time instead of baked in at build time — MC hot loop and O(1) fallback cache untouched. Added the UI date picker and a third data source. A full month (2026-08-22 .. 2026-09-21) ingests into a single warehouse file; different dates genuinely surface different route topologies. (Its size grew to ~55MB in Phase 3.1 when the corridor-aware leg fix roughly doubled the leg count — see `DATA_SPEC.md` §6.1.)

**Phase 3.1 — Corridor & query hardening.** Real-feed testing surfaced a data-completeness bug and, once fixed, the performance issue it had been masking:

- **Corridor-aware leg construction** *(Snapshot + Warehouse — both GTFS-ingestion build paths; Mock is hand-authored and untouched)*: any trip with a non-corridor stop between two corridor hubs was silently dropped instead of modeled with an extra stop — recovered ~1,000 previously-disconnected long-distance trips.
- **N+1 query fix** *(Warehouse: DuckDB query batching; Mock + Snapshot: a parallel index-reuse fix in the JSON search path, §3.5)*: the richer graph exposed a per-leg/per-line query pattern; batching cut one Warehouse search from 584 DuckDB round trips to 7.
- **Caching/pagination split** *(all three backends — generic in `app.py`, §4.3)*: "Load more" now only pays for newly-revealed routes.

Combined: a Leipzig→Munich search that found nothing before 09:27 now finds 07:26; a full search + 5-route simulate went from ~30s to ~2.15s.

**Phase 3.2 — MCT & platform capture.** A data audit against the real GTFS.DE feeds found no `transfers.txt`/`min_transfer_time` (motivating §3.6's station-tier proxy + gradient floor) and a genuine but sparse `platform_code` column (motivating §2.3's platform fields, hidden gracefully when absent).

## 8. Limitations & Future Work

### 8.1 Known Engine Limitations

- **Independent per-leg delay sampling** — no same-train carryover or shared "bad day" correlation between legs.
- **One level of re-routing on miss** (§3.4) — a miss *within* a fallback route resolves via headway wait, not a second search.
- **2-transfer cap** (§6) — closes most real corridor pairs but isn't a hard architectural limit.
- **MCT is a touch-count proxy**, not real platform geometry — data-side detail: `DATA_SPEC.md` §10.

### 8.2 Future Roadmap (Phase 4+)

- **Correlated delay sampling** — same-train carryover and/or regional "bad day" latent factors.
- **MCT proxy refinement** — real platform-to-platform distance data if it ever becomes available; also extend MCT checking to transfers *within* a fallback route (currently unchecked).
- **Unbounded recursive re-routing** — beyond §3.4's one level.
- **Advanced simulation controls** in the UI — iteration count, risk-aversion weighting, minimum buffer.
- **Full distribution histogram** output, not just mean + percentile.
- **Multi-day/multi-date batch simulation**, comparing risk across dates in one view.
- **Live/real-time GTFS-RT integration** — would revisit the offline-only decision (`DATA_SPEC.md` §1).
- **RAPTOR/CSA-style routing** to replace the transfer-template search, which is quadratic-ish in arrivals × departures per station (`DATA_SPEC.md` §10).
- **Backend/frontend decoupling** — FastAPI service + React/Vite PWA, for real concurrent load and mobile installability.
- **Post-midnight cross-day service lookback** — a date's search currently only considers that date's own active services (`DATA_SPEC.md` §10).
