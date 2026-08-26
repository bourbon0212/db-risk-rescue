# DB Risk & Rescue — Data Architecture Specification

**This document covers everything upstream of the engine:** how raw GTFS timetables and a historical delay archive become the `Station`/`Line`/`Leg`/`Transfer`/`Route` objects `engine.py` consumes. Read it if you're changing ingestion, the delay pipeline, route search, or the warehouse.

The quickest orientation is §2's component layout, then §3 (how a timetable becomes legs and transfers) and §4 (how delay history becomes probability distributions). §6 is the warehouse schema. New to the project? Start with [README.md](README.md).

Two ground rules hold throughout. **Offline only** — committed fixtures and periodically-downloaded archives, never live polling or reverse-engineered APIs. And **the Pydantic contract is non-negotiable**: every pipeline here must produce output that validates against `models.py` as it stands. If a pipeline's output doesn't fit, the pipeline is wrong, not the model — any genuine gap gets raised in §10 rather than patched around.

**Section map:** §1 Guiding Constraints & Scope · §2 Repository & Component Layout · §3 GTFS Topology Ingestion Pipeline · §4 Delay Distribution Pipeline · §5 Route Search & Candidate Generation · §6 DuckDB Warehouse Schema · §7 JSON Backends & Data Access Layer · §8 Build & Deployment Sequence · §9 Resolved Design Decisions · §10 Known Limitations.
**Status:** All three backends implemented and selectable in `app.py` — Mock and Snapshot (JSON, §7), Warehouse (DuckDB, §6). Backend ↔ phase mapping: `SPEC.md` §4.2.

## 1. Guiding Constraints & Scope

```python
def load_dataset(path: Path = MOCK_DATA_PATH) -> MockDataset:
    return MockDataset.model_validate(json.loads(path.read_text()))
```

No pipeline changes this signature or the Pydantic models — only which JSON file gets built and which path `load_dataset` points at. Phase 3 doesn't call `load_dataset()` at all (queried per-search rather than loaded whole, §6) but materializes the same `Leg`/`Transfer`/`Route` instances.

Offline-only: fixture files and periodically-downloaded GTFS.DE/piebro archives — never live HAFAS polling or scraping.

## 2. Component Layout

The data-path components and what each owns. `README.md` carries the full
repository tree (including the app layer, `tests/` and `design/`); this one
annotates only what this spec governs.

Throughout this doc a bare module name means `pipelines/`; anything living
elsewhere is written with its path.

```
pipelines/
  gtfs_ingest.py          # GTFS.DE static feed -> Station, Line, Leg/LegTemplate,
                          # Transfer/TransferTemplate, TripRecord, classify_station_mct(),
                          # platform_code capture (§3.3)
  calendar_ingest.py      # calendar.txt / calendar_dates.txt -> ServiceCalendarRow/Exception (§6)
  delay_aggregation.py    # piebro-shaped records -> delay_distribution_minutes per Leg (§4)
  delay_mapping.py        # piebro raw schema -> delay_aggregation.py's expected shape
  id_crosswalk.py         # GTFS stop_id <-> station_id mapping for the scoped corridor
  gtfs_scope.py           # DB-agency/corridor/line-type feed scoping — scope_gtfs_feed()
                          # (single service_date, §7) and scope_gtfs_feed_multi_day() (§6)
  build_dataset.py        # Snapshot orchestrator -> data/real_dataset.json
  build_warehouse.py      # Warehouse orchestrator -> data/warehouse.duckdb (§6)
  warehouse_writer.py     # DuckDB DDL + write logic (§6)
  download_raw_data.py    # fetches GTFS.DE zip(s) + piebro monthly Parquet into data/raw/
routing/                  # query-time, not build-time: imported by app.py and engine.py
                          # on every search, and by none of the pipelines above
  route_search.py         # in-memory candidate Route generation over a loaded MockDataset (§5)
  route_search_duckdb.py  # DuckDB-backed candidate Route generation, calendar-aware (§5, §6)
  route_filters.py        # Sanity Filter — prunes detour routes post-search (SPEC.md §3.7)
gtfs_time.py              # seconds-since-midnight <-> datetime, plus calendar.txt's
                          # weekday columns -- the vocabulary pipelines/ writes and
                          # routing/ reads, owned by neither (§3 step 5, §6)
db.py                     # DuckDB connection helper, mirrors data_loader.py (§7)
data_loader.py            # loads data/mock_data.json or data/real_dataset.json into a
                          # MockDataset -- the container, not the Mock backend (§7)
data/                     # every dataset lives here -- nothing data-shaped at the root
  mock_data.json          # Mock backend, hand-authored (committed)
  real_dataset.json       # Snapshot pipeline output (committed)
  warehouse.duckdb        # Warehouse pipeline output (gitignored; rebuild via
                          # `python -m pipelines.build_warehouse`)
  fixtures/               # small hand-built GTFS feeds (demo pipeline + tests)
  raw/                    # downloaded GTFS + piebro snapshots (gitignored)
```

**The build/query split.** `pipelines/` runs offline via `python -m`; `routing/`
runs inside a request, imported by `app.py` and `engine.py`. Nothing in
`pipelines/` imports `routing/`, which is what keeps the ingestion layer out of
the hot path.

`gtfs_time.py` exists because of that split. Leg times are stored date-agnostically
(§3 step 5), so *encoding* that form is an ingestion job and *decoding* it is a
query job — the representation belongs to neither package, but both must agree on
it exactly or legs come back with silently wrong times. It is a leaf module
(stdlib only) so either side can depend on it without a cycle. `WEEKDAY_COLUMNS`
lives there for the same reason: `gtfs_scope.py` and `routing/route_search_duckdb.py`
both index `calendar.txt`'s columns by `date.weekday()`.

## 3. GTFS Topology Ingestion Pipeline

**Input:** GTFS.DE static feed (`stops.txt`, `routes.txt`, `trips.txt`, `stop_times.txt`, `calendar.txt`, `calendar_dates.txt`).
**Tooling:** stdlib `csv.DictReader` — GTFS's flat-file structure didn't justify a third-party parsing library.

1. **Download & version.** `download_raw_data.py` fetches two GTFS.DE rail feeds — `fv_free` (ICE/IC/EC) and `rv_free` (RE/RB/S-Bahn) — plus the piebro monthly Parquet into `data/raw/` (gitignored). The two rail feeds together cover this app's five `LINE_TYPES` exactly; gtfs.de's alternative "Deutschland gesamt" feed adds every bus, tram and ferry in the country, all of which step 2 would discard anyway. The cost of this choice is that every downstream build merges two feeds and dedupes across them (§6.4, §7). Re-run per *Fahrplanwechsel* (quarterly) for GTFS, monthly for delay data.
2. **Scope the feed.** `gtfs_scope.py` filters to DB-operated route types and the corridor (§9.1), writing a standalone scoped GTFS directory that the ingestion functions consume unchanged — scoping is a stage before ingestion, not a variant of it. `scope_gtfs_feed()` further filters to one `service_date` (§7); `scope_gtfs_feed_multi_day()` keeps every `service_id`/date and copies calendar files unfiltered (§6).

   Note that an unrecognized line type is *dropped* here but *raises* during ingestion (§3.1). The two answer different questions. A national feed legitimately carries services this app never intended to model — international Nightjet/railjet, regional brands like `MEX12`, bare route numbers — so "is this in scope?" is settled by quiet exclusion. Once a line is in scope, "can we model it?" must fail the build rather than let a mislabelled type reach `engine.py`.
3. **Stations.** One GTFS parent-station `stop` → one `Station`. `stop_id` is the ingestion-time key; final `station_id` resolves through `id_crosswalk.py` (§9.1).
4. **Lines.** One GTFS `route` → one `Line`. `route_short_name`/`route_type` → `type` (exact strings, §3.1); `agency_name` → `operator`.
5. **Legs.** Walk each trip's `stop_times`; each consecutive stop pair → one leg. Two representations share the same time-parsing primitives:
   - `parse_legs(gtfs_dir, service_date) -> list[Leg]` — anchors times to one concrete `datetime`. Backs §7.
   - `parse_leg_templates(gtfs_dir) -> list[LegTemplate]` — stores seconds-since-midnight (GTFS-style, can exceed 86400) plus `trip_id`/`sequence_index`. Backs §6.

   `delay_distribution_minutes` is a placeholder (`{"0": 1.0}`) here — §4 overwrites it. `LegTemplate` has no such field; distributions are looked up by `line_id` at query time (§6.3).
6. **Transfers.** Not in GTFS — derived: for every station, (arriving leg, departing leg) pairs from different trips where the gap falls within the transfer window (`SPEC.md` §6). `scheduled_buffer_minutes = departure - arrival`. Same anchored/template split: `derive_transfers`/`derive_transfer_templates` (the latter also carries `from_trip_id`/`to_trip_id` so §6.3's calendar filter needs no join back to `trips`).

### 3.1 Line-type string consistency (hard requirement)

`engine.py` does an exact-string dict lookup (`SERVICE_FREQUENCY_MINUTES`, values: `SPEC.md` §6). Any other spelling (`"S"`, `"Ice"`, raw GTFS `route_type` codes) raises `ValueError` at simulation time. `_normalize_line_type()` normalizes to exactly five strings; both `build_dataset.py` and `build_warehouse.py` assert every emitted `Line.type` is a member of `gtfs_ingest.LINE_TYPES` before writing — fail the build, don't let a bad value reach `engine.py`.

### 3.2 Corridor-aware leg construction

The original leg builder turned every physically-consecutive stop pair into a leg, then filtered to keep only legs where *both* endpoints were corridor stations. Bug: any trip with a non-corridor stop between two corridor hubs — near-universal on long-distance runs — produced a leg with neither endpoint in-scope, silently discarded. Measured against the real feed: **84% of leg segments dropped**, **~1,000 of ~4,200 long-distance trips** fully disconnected.

Fix: `_walk_corridor_legs` (`parse_corridor_legs`/`parse_corridor_leg_templates`) reduces each trip's stops to just the ones touching a corridor station (order preserved), then builds legs between *consecutive corridor stops* — skipping non-corridor stops instead of being blocked by them. Not a whitelist expansion — the 33-station cap (§9.1) is unchanged, only which stop pairs become leg endpoints. Wired into both **real** build paths (`build_real_dataset`, `build_real_warehouse`), fully replacing the old post-hoc filter. The fixture/demo paths still call the plain adjacency parsers, which is safe because the committed fixtures contain only corridor stations — a non-corridor stop there would fail the crosswalk outright. Rebuild delta: `leg_templates` 6,620 → 12,372; trips ~3,205 → 7,348.

### 3.3 Minimum Connection Time (MCT) and platform capture

A data audit against the real feeds (`gtfs_fv_latest.zip`, `gtfs_rv_latest.zip`) found no `transfers.txt`/`min_transfer_time` in either, but a genuine `platform_code` column in `stops.txt` — real, and sparse but not negligible: 4 of the corridor's 33 stations carry it (Berlin Hbf, Berlin Südkreuz, Berlin Spandau, Halle(Saale)Hbf), covering ~7% of `leg_templates`.

- **`gtfs_ingest.classify_station_mct(station_touch_pairs)`** — touch-count percentile classifier (values and engine-side rationale: `SPEC.md` §3.6.1, §6). Run once per build, after crosswalking, by both `build_dataset.py`/`build_warehouse.py`; `_apply_station_mct()` attaches the result via `model_copy`.
- **Platform capture** — `_load_stop_to_platform_map(gtfs_dir)` reads `platform_code` for platform-level stops, keyed by the pre-collapse `stop_id`. All four leg parsers attach `origin_platform`/`destination_platform` from the same `stop_times` pass. A feed/fixture with no `platform_code` column degrades to `None` per leg, not an ingestion error.

Both pipelines run against the same `leg_templates`/`legs`, so `classify_station_mct` is computed **independently per build** — Phase 2 and Phase 3 outputs can classify the same station into different tiers if their touch-count distributions differ slightly. Not a bug, confirmed directly against both real outputs.

## 4. Delay Distribution Pipeline

**Input:** Monthly Parquet from `piebro/deutsche-bahn-data`.

1. **Download & cache** under `data/raw/delays_<month>.parquet`.
2. **Reconstruct leg-level delay** from `arrival_planned_time`/`arrival_change_time`, ignoring the archive's own `delay_in_min` column (§4.1 has the measurement that ruled it out). Rows with no real arrival event or a canceled arrival are dropped.
3. **Bucket** into the scheme in `SPEC.md` §6 — "closest boundary not exceeding it" (`bucket_delay`), matching how `engine.py` interprets buckets (`int(bucket) > buffer`).
4. **Aggregate per `line_id`** — empirical fraction per bucket.
5. **Fallback rule**: a `line_id` with fewer than the minimum-samples threshold (`SPEC.md` §6) falls back to a train-type-level aggregate (`delay_aggregation.FALLBACK_GROUPS`) — mirrors `SPEC.md` §2.6's service-frequency fallback shape. A line the archive has *no* rows for at all is covered by the same rule via `additional_line_types` (its own count is 0, so it always pools): without it such a line would be absent from the output entirely and fail the build's "no delay distribution for line_id(s)" check.
6. **Normalize** to sum to 1.0 — enforced for free by `models.py`'s validator the moment a `Leg` is constructed.

Entirely date-independent — a delay distribution is a historical aggregate, not tied to a calendar date. This is why the DuckDB warehouse (§6) only needed to make *topology* dynamic; `delay_distributions` needed no date dimension at all.

### 4.1 Reading the piebro schema

> *Background — skip unless you're modifying `delay_mapping.py`.* The evidence behind step 2's column choices.

The archive's columns don't mean what their names suggest. Both findings below were measured against the downloaded ~14M-row Parquet rather than taken from the dataset card, and together they're what `delay_mapping.py` exists to translate.

**`delay_in_min` is not arrival delay.** For rows with a real arrival event it matches `arrival_change_time − arrival_planned_time` only ~58% of the time, but matches *departure* delay ~93% of the time — 100% for origin-station rows, which have no arrival at all. Step 2 needs arrival delay specifically, so the pipeline derives it from the timestamp pair and ignores the column outright.

**Line identity lives in a different column depending on train type.** For ICE/IC/EC/ECE, `line_number` is null and `train_number` carries the real nationally-unique number (`ICE` + `615` → GTFS `ICE 615`). For RE/RB/S it inverts: `train_number` is an internal per-run id matching nothing in GTFS, and the identity is `line_number` — already type-prefixed in ~91–99.8% of rows (`RE5`, `RB44`, `S12`), the remainder being bare digits or other operators' brands (`MEX12`, `FEX`, `RS7`) that scoping excludes anyway. Because regional codes are short and reused, this is also the root of §10's uniqueness caveat. `train_type` needs a smaller adjustment: `"S"` rather than `"S-Bahn"`, with `EC`/`ECE` folding into `IC` — the same normalization `gtfs_ingest.py` applies (§3.1).

## 5. Route Search & Candidate Generation

`app.py` doesn't filter a curated list — `routing/route_search.py` (§7) and `routing/route_search_duckdb.py` (§6) generate candidate `Route` objects on demand from `(origin, destination, departure_time)`, plus `service_date` for the DuckDB backend. Same shape both ways:

- Direct legs, departing at or after the requested time.
- Single-transfer: leg A, a transfer within the window, leg B.
- Two-transfer: leg A, transfer, leg B, transfer, leg C (extended from a 1-transfer cap once some real pairs, e.g. Berlin↔Leipzig, needed the extra hop).

Not in scope: 3+ transfers, full graph pathfinding, or recursive re-routing beyond `SPEC.md` §3.4's one level. `engine.py`'s `simulate_route()` chains generically over however many legs/transfers a `Route` has, so neither search module needed to touch the simulation core.

Both backends track visited stations while extending a candidate and stop as soon as a leg reaches the destination, guaranteeing every returned `Route` is a simple path — no station repeats, and nothing that leaves the destination only to return to it later (e.g. Reutlingen → Stuttgart → Heidelberg → Stuttgart instead of the direct Reutlingen → Stuttgart leg).

- `routing/route_search.py::find_candidate_routes(dataset, ...)` — in-memory, backs §7.
- `routing/route_search_duckdb.py::find_candidate_routes(conn, ..., service_date, legs_by_id, transfers_by_id)` — one small SQL query per step, scoped to origin + active `service_id`s (§6.3); resolved objects written into caller-supplied dicts in place, so only what's actually touched loads into memory. Backs §6.

## 6. DuckDB Warehouse Schema

### 6.1 Design goal

Store topology **date-agnostically** — one row per template leg/transfer regardless of calendar span — and resolve which are running on a given date as SQL at query time, instead of baking one date's answer in at build time (§7's approach). Delay distributions need no date dimension at all (§4).

The payoff is that a full month of calendar (current build: 2026-08-22..2026-09-21) lives in one ~55MB file rather than a JSON snapshot per day. That file size tracks the size of the network — `transfer_templates` dominates it (§10) — and not the length of the calendar window, which is precisely what date-agnostic storage buys: ingesting a longer window costs almost nothing.

### 6.2 Tables

| Table | Columns | Notes |
|---|---|---|
| `stations` | `station_id PK, name, mct_minutes` | Static. `mct_minutes` (default 5) is a classification, not feed data (§3.3) |
| `lines` | `line_id PK, type, operator` | Static, loaded eagerly |
| `trips` | `trip_id PK, line_id, service_id` | One row per GTFS trip |
| `leg_templates` | `leg_id PK, trip_id, line_id, sequence_index, origin_station_id, destination_station_id, departure_seconds, arrival_seconds, origin_platform, destination_platform` | Times are seconds-since-midnight-of-service-day (§3 step 5) |
| `transfer_templates` | `transfer_id PK, station_id, from_leg_id, to_leg_id, from_trip_id, to_trip_id, buffer_minutes` | Precomputed at build time (§3 step 6); trip ids denormalized so §6.3's calendar filter needs no join |
| `service_calendar` | `service_id, monday..sunday, start_date, end_date` | Mirrors `calendar.txt` |
| `service_calendar_exceptions` | `service_id, date, exception_type` | Mirrors `calendar_dates.txt` (1=add, 2=remove) |
| `delay_distributions` | `line_id, bucket_minutes, probability` | Long format, date-independent (§4) |

Four indexes cover exactly the columns §6.3's per-hop queries filter on: `leg_templates(origin_station_id)`, `transfer_templates(from_leg_id)`, `trips(service_id)`, and `delay_distributions(line_id)`. They're what make §6.3.1's "this was query *count*, not query cost" diagnosis true — without them, batching alone wouldn't have been enough.

`warehouse_writer.py` owns the DDL (`create_schema`) and write path (`write_warehouse`, clears existing rows first — idempotent rebuild).

### 6.3 Dynamic date filtering at query time

`route_search_duckdb.find_candidate_routes(conn, origin_id, destination_id, departure_time, service_date, legs_by_id, transfers_by_id)`, per search:

1. **Resolve active `service_id`s** for `service_date` (`_resolve_active_service_ids`): weekday-column match against `service_calendar`'s date range, unioned with same-date additions and minus same-date removals from `service_calendar_exceptions`.
2. **Materialize origin legs**: `leg_templates` joined to `trips`, filtered to origin + active service + `departure_seconds >= cutoff`. Each row → a concrete `Leg` (seconds → datetime, distribution looked up and cached per-call by `line_id`).
3. **Walk transfers**, same 2-hop shape as §5, candidates from `transfer_templates` joined to `trips` twice (both trip ids requiring active service).
4. Every resolved `Leg`/`Transfer` is written into the caller's dicts as a side effect, so `simulate_route()`/`precompute_fallback_plans()` never need the whole network in memory (`SPEC.md` §3.5).

`calendar_window(conn)` returns `(MIN(start_date), MAX(end_date))`, bounding `app.py`'s date picker.

#### 6.3.1 Batched, not per-item, hop queries

Steps 2–3 issue **one query per hop** (`= ANY(?)` over every leg id still in play), not one per candidate leg; `_DistributionCache.preload(line_ids)` batches the same way.

Load-bearing, not a micro-optimization: the corridor-aware leg fix (§3.2) grew `transfer_templates` to ~285K rows, exposing a latent N+1 pattern. Per-item: **584 DuckDB round trips** for one Leipzig→Munich search (~5s overhead — column indexing was fine, this was query *count*; `precompute_fallback_plans` calling this twice per route made a 5-route batch 30s+). Batched: **≤6–7 queries total**, regardless of graph richness. Full search + 5-route simulate: ~30s → ~2.15s, measured.

### 6.4 Build path

`build_warehouse.py` mirrors `build_dataset.py`'s two-path structure:

- `build_warehouse(conn, gtfs_dir, historical_delays)` — fixture/demo path, `data/fixtures/gtfs_smoke/`.
- `build_real_warehouse(conn, raw_dir)` — real path: `scope_gtfs_feed_multi_day()` (every calendar day survives), date-agnostic parsers (§3 steps 5–6), cross-feed dedup, corridor-to-corridor filter, crosswalk, delay distributions from real piebro data (synthetic per-type fallback otherwise).

Both call `warehouse_writer.write_warehouse()`. `python -m pipelines.build_warehouse` runs whichever path has real GTFS zips under `data/raw/`, prints row counts and the resolved calendar window.

## 7. JSON Backends & Data Access Layer (Mock & Snapshot)

**Loading (`data_loader.py`):**

```python
MOCK_DATA_PATH = Path(__file__).parent / "data" / "mock_data.json"
REAL_DATA_PATH = Path(__file__).parent / "data" / "real_dataset.json"

def load_dataset(path: Path = MOCK_DATA_PATH) -> MockDataset:
    return MockDataset.model_validate(json.loads(path.read_text()))
```

`app.py`'s sidebar radio picks which path `get_dataset()` loads. `data/mock_data.json` and its test coverage stay untouched by every downstream pipeline change.

**Building `data/real_dataset.json`:** `build_dataset.py` is a one-off/periodic script, not invoked at request time. Mirroring §6.4's warehouse path, it scopes *both* downloaded feeds (fv + rv) to one `service_date` (§3 step 2), ingests each via the corridor-aware anchored parser (§3.2), dedupes cross-feed duplicate lines and legs, crosswalks station ids, joins delay distributions (§4), and writes a validated `MockDataset`. Leg dedup must run before `derive_transfers` — deduping afterward would leave transfers pointing at a discarded twin's `leg_id`.

**DuckDB path (`db.py`)**: Phase 3 counterpart — see §6.

## 8. Build & Deployment Sequence

**Phase 2** (still the build path for `data/real_dataset.json`):

1. `gtfs_ingest.py`: Stations + Lines (tested against `data/fixtures/gtfs_mini/`).
2. `gtfs_ingest.py`: Legs + Transfers, plus §3.1's line-type normalization test coverage.
3. `delay_aggregation.py`: bucket + normalize + fallback, tested against a synthetic delay DataFrame.
4. `id_crosswalk.py`: `stop_id` ↔ station identifier mapping.
5. `build_dataset.py`: wires 1–4, `MockDataset.model_validate()`, writes `data/real_dataset.json`.
6. `routing/route_search.py` + `app.py` sidebar toggle: smoke-tested against the Snapshot dataset alongside Mock.

**Phase 3** (added once Phase 2 was stable):

7. `calendar_ingest.py` + date-agnostic `parse_trips`/`parse_leg_templates`/`derive_transfer_templates`, tested for parity against the anchored parsers on the same fixture.
8. `warehouse_writer.py` + `build_warehouse.py`: wires GTFS + calendar + delay pipeline into `data/warehouse.duckdb`; `routing/route_search_duckdb.py` + date picker, smoke-tested alongside Phase 1/2.

Nothing in `models.py`, `engine.py`, `ui_components.py`, or Phase 1/2 tests needed to change at either step. Full phase narrative/rationale: `SPEC.md` §7.

## 9. Resolved Design Decisions

Originally open questions, kept here so the reasoning stays visible for future revisits.

### 9.1 Geographic/service scope

Expanded past the original 11-station mock mirror to a 33-station "Golden 35" corridor (`id_crosswalk.py`) covering major ICE hubs, interchange points, and targeted connector stations — enough network for real routing complexity (multi-hop journeys, real transfer density) rather than disconnected point-to-point legs. Every `stop_id` in the crosswalk was looked up against the downloaded fv/rv feeds, not guessed.

The two paragraphs below are *background* — skip them unless you're editing the crosswalk itself.

**Split stations map many `stop_id`s to one `station_id`.** DELFI models multi-level stations as separate top-level parents rather than one node with sub-areas, so a single logical station often has two GTFS nodes — Frankfurt, Stuttgart, Leipzig and München each split surface/tunnel (`tief`/`oben`), Hamburg splits long-distance from S-Bahn, and Erfurt and Kassel-Wilhelmshöhe each carry a small secondary node. Name search alone finds these unreliably: Hamburg's S-Bahn node is `HBF/Kirchenallee` and München's is `Hauptbahnhof (U, Tram)` — neither contains its own city name. They were identified by tracing which parent `stop_id` the station-visit records actually use.

**Connector stations.** Three were added to reach stations one hop outside the corridor. Dresden-Neustadt (unlocking Dresden Hbf) and Reutlingen Hbf (unlocking Tübingen Hbf) both work, confirmed against the built warehouse. Freilassing does *not* unlock Berchtesgaden Hbf despite looking like the obvious junction — the real branch runs through 8 intermediate stops, none of them in the corridor. Freilassing is kept as a useful junction regardless, but Berchtesgaden Hbf remains the corridor's only station with no legs at all; reaching it would mean adopting most of that branch line, disproportionate for one low-traffic terminus.

One caveat when adding future connectors: München Hbf ↔ Marienplatz was recorded here as unreachable, blocked by non-corridor Karlsplatz between them, until the corridor-aware leg builder (§3.2) closed the gap. Physical adjacency is therefore no longer the test of whether a connector helps — adjacency in the *corridor-filtered* stop sequence is.

### 9.2 Minimum sample threshold

30 historical occurrences (`delay_aggregation.DEFAULT_MIN_SAMPLES`), unchanged across both builds.

### 9.3 Delay attribution

Destination-arrival delay only, derived from the timestamp pair rather than the archive's `delay_in_min` column (§4.1). A full origin-departure join was considered and dropped: the simpler approach turned out to also be the more accurate one.

### 9.4 Transfer-window bounds

2–60 minutes, the originally recommended values, unchanged. Enforced once at ingestion (§3 step 6): a gap outside that range simply never becomes a Transfer, so route search (§5) inherits the constraint without re-checking it.

### 9.5 Minimum Connection Time source

Since GTFS.DE carries no per-station `min_transfer_time` (§3.3), MCT is a rule-based touch-count classifier, not a data field. Hand-curating known-hub station IDs was considered and rejected: touch-count already correlates with station size and generalizes to any future corridor expansion without manual upkeep, at the cost of being a proxy rather than real geometry (§10).

## 10. Known Limitations

- **Post-midnight cross-day lookback is not implemented.** A search only considers `service_date`'s own active services — a trip whose `service_id` belongs to the previous date but spills past midnight isn't considered. Deferred deliberately for v1.
- **Transfer-template growth is quadratic** in (arrivals × departures) per station: the current corridor turns ~12,372 `leg_templates` into ~285K `transfer_templates`. Batched queries (§6.3.1) keep that fast at this scale, but the growth curve is the real ceiling — a materially larger corridor or a higher transfer cap would need RAPTOR/CSA rather than enumerating transfer pairs up front (`SPEC.md` §8.2).
- **Regional `line_number` codes aren't guaranteed nationally unique** the way ICE/IC numbers are — two unrelated regional lines could in principle share a code. Filtering to only the `line_id`s a build needs bounds the blast radius but doesn't eliminate it.
- **The 2-transfer cap** is a deliberate ceiling, not an architectural limit — raising it wasn't needed once connector stations (§9.1) closed most previously-unreachable pairs at 2 hops.
- **Displayed train identifiers are route-level, not trip-level.** `Leg.line_id` comes from GTFS `route_short_name` — the finest granularity the static feed provides; `trips.txt` carries no `trip_short_name`. A single route can span hundreds of trips in the current build (`S6`: 595, `ICE 41`: 177), so two different physical trains can render with the identical label. Display-only — `derive_transfers` still keys eligibility off `trip_id`, so risk logic is never confused, only what's shown on screen is coarser. Not fixable without a richer feed than GTFS.DE's static export offers.
- **No walking/interchange concept.** `Transfer` (`SPEC.md` §2.4) is train-to-train only: two stations are connected if and only if a scheduled leg runs between them. Where such a leg exists this is invisible — Köln Hbf ↔ Köln Messe/Deutz, once cited here as unreachable, now has ~1,050 legs since the corridor-aware fix (§3.2). The gap only bites where no leg exists but a short walk would do, which real DB Navigator covers by silently expanding a search to nearby stations. Closing it needs a station-adjacency/walking-time model, not just a data fix.
- **MCT is a touch-count proxy, not real platform geometry.** Only two tiers (5/10 min), with no notion of whether a specific below-threshold transfer is a same-platform hop or a long concourse walk. `SPEC.md` §3.6.2's gradient floor exists specifically to soften the consequences of this proxy being wrong; improving the classification itself needs real platform-distance data no available source provides.
- **Platform info is uneven, not absent.** Only 4 corridor stations carry `platform_code` (§3.3), and the UI needs *both* ends of a transfer to have one, so just ~3% of transfers display a platform pair. The practical effect is inconsistency rather than a missing feature: a Berlin-heavy itinerary may show platforms at several transfers while another route shows none.
- **Route search can surface near-duplicate candidates** that converge on an identical remaining itinerary — routes departing at different times via different early legs can arrive at the exact same scheduled time. Not rare: one Köln Hbf → Frankfurt(Main) Hbf search returned 102 candidates in which 9 arrival times were each shared by several routes, the worst cluster holding 16. Legitimate when the differing early leg carries a meaningfully different risk profile; reads as redundant cards when it doesn't. No dedup logic exists; not yet triaged.
