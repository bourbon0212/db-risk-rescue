# DB Risk & Rescue — Data Architecture Specification

**Companion to:** `SPEC.md` (product/algorithm spec) — this document covers how data gets from source files into `engine.py`'s Pydantic input contract (`SPEC.md` §2), across three generations of storage backend. `SPEC.md` §4 is the high-level architecture summary; this document is the full detail behind it.

**Status:** Phase 2 (JSON pipeline, §3–§5, §7–§9) and Phase 3 (DuckDB warehouse, §6) are both implemented and remain selectable data sources in `app.py`.

**Scope:** Offline-only. No live polling, no HAFAS or other reverse-engineered sources — a deliberate project decision carried unchanged from Phase 2 into Phase 3.

**Section map:** §1 Guiding Constraints & Scope · §2 Repository & Component Layout · §3 GTFS Topology Ingestion Pipeline · §4 Delay Distribution Pipeline · §5 Route Search & Candidate Generation · §6 DuckDB Warehouse Schema (Phase 3) · §7 JSON Backends & Data Access Layer (Phase 1/2) · §8 Build & Deployment Sequence · §9 Resolved Design Decisions · §10 Known Limitations.

This spec describes how `mock_data.json` (Phase 1) gets replaced by real, then dynamically-queryable, data — while leaving `models.py`, `engine.py`, `ui_components.py`, and `app.py`'s core rendering untouched. Every pipeline below produces output that validates against (or, for Phase 3, materializes into) the existing `MockDataset`/`Leg`/`Transfer`/`Route` Pydantic models — that contract is the hard boundary every section of this document is built around.

## 1. Guiding Constraints & Scope

`data_loader.load_dataset()` has the signature:

```python
def load_dataset(path: Path = MOCK_DATA_PATH) -> MockDataset:
    return MockDataset.model_validate(json.loads(path.read_text()))
```

No pipeline changes this signature or `MockDataset`/`Station`/`Line`/`Leg`/`Transfer`/`Route` — only what JSON file gets built and which path `load_dataset` points at. Phase 3 goes further and doesn't call `load_dataset()` at all for its backend, since a DuckDB warehouse is queried per-search rather than loaded whole (§6) — but the *objects* it materializes (`db.py` + `pipelines/route_search_duckdb.py`) are exactly the same `Leg`/`Transfer`/`Route` Pydantic instances `load_dataset()` produces. If a pipeline's output can't validate against (or materialize into) these models as they exist today, the pipeline is wrong, not the model — any schema gap gets raised as an open item (§10), not patched around silently.

This project is **offline-only**: fixture files and periodically-downloaded GTFS.DE/piebro archives, never live HAFAS polling or other reverse-engineered APIs.

## 2. Repository & Component Layout

```
pipelines/
  gtfs_ingest.py         # GTFS.DE static feed -> Station, Line, Leg (topology, anchored),
                          # LegTemplate (topology, date-agnostic), Transfer/TransferTemplate, TripRecord
  calendar_ingest.py     # GTFS calendar.txt / calendar_dates.txt -> ServiceCalendarRow /
                          # ServiceCalendarException (§6)
  delay_aggregation.py   # piebro-shaped records -> delay_distribution_minutes per Leg (§4)
  delay_mapping.py       # piebro raw schema -> delay_aggregation.py's expected input shape
  id_crosswalk.py        # GTFS stop_id <-> station_id mapping for the scoped corridor
  gtfs_scope.py           # DB-agency / corridor / line-type feed scoping — scope_gtfs_feed()
                          # (single service_date, §7) and scope_gtfs_feed_multi_day()
                          # (every service_id, §6)
  route_search.py         # in-memory candidate Route generation over a loaded MockDataset (§5)
  route_search_duckdb.py  # DuckDB-backed candidate Route generation, dynamic-calendar-aware (§5, §6)
  build_dataset.py        # Phase 2 orchestrator -> validated MockDataset -> data/real_dataset.json
  build_warehouse.py      # Phase 3 orchestrator -> data/warehouse.duckdb (§6)
  warehouse_writer.py     # DuckDB DDL + write logic (§6)
  download_raw_data.py    # fetches GTFS.DE zip(s) + piebro monthly Parquet into data/raw/
db.py                     # DuckDB connection helper — mirrors data_loader.py, for the warehouse backend (§7)
data_loader.py            # loads mock_data.json / real_dataset.json into a validated MockDataset (§7)
data/
  raw/                    # downloaded GTFS feed + piebro parquet snapshots (gitignored)
  real_dataset.json       # Phase 2 pipeline output — mock_data.json is left untouched
  warehouse.duckdb        # Phase 3 pipeline output (gitignored — rebuild with
                          # `python -m pipelines.build_warehouse`)
```

## 3. GTFS Topology Ingestion Pipeline

**Input:** GTFS.DE static feed (`stops.txt`, `routes.txt`, `trips.txt`, `stop_times.txt`, `calendar.txt`, `calendar_dates.txt`).
**Tooling:** the Python standard library `csv` module (`csv.DictReader`) directly — no third-party GTFS parsing library. GTFS's flat-file structure is simple enough that stdlib `csv` plus a handful of purpose-built join/normalize functions in `gtfs_ingest.py` was less code and fewer dependencies than adopting a general-purpose library.

Steps:

1. **Download & version.** `pipelines/download_raw_data.py` fetches the current GTFS.DE zip(s) and the piebro monthly delay Parquet into `data/raw/` (gitignored). Re-run this roughly every *Fahrplanwechsel* (quarterly) for GTFS, monthly for delay data — not on every app run.
2. **Scope the feed before parsing anything else.** The national feed covers every bus, tram, and train in Germany. `pipelines/gtfs_scope.py` filters to DB-operated `route_type`/`agency` entries only (ICE/IC/EC, RE/RB, S-Bahn) and further scopes to the corridor (§9.1). Two variants exist: `scope_gtfs_feed()` additionally filters trips down to one `service_date` (§7's single-date JSON build); `scope_gtfs_feed_multi_day()` keeps every trip regardless of `service_id`/date and copies `calendar.txt`/`calendar_dates.txt` through unfiltered (§6's warehouse build) — everything else about the filtering (agency, route type, corridor-touching) is identical between the two.
3. **Stations.** One GTFS `stop` (parent station, not individual platform stops) → one `Station`. `stop_id` is the ingestion-time key; the final `station_id` is resolved through `pipelines/id_crosswalk.py` (§9.1, §8 step 4).
4. **Lines.** One GTFS `route` → one `Line`. `route_short_name`/`route_type` maps to the existing `type` field (`ICE`/`IC`/`RE`/`RB`/`S-Bahn` — matching the exact strings `engine.py`'s `SERVICE_FREQUENCY_MINUTES` keys on, see §3.1). `agency_name` maps to `operator`.
5. **Legs.** For each `trip`, walk `stop_times` in sequence; each consecutive stop pair becomes one leg. Two representations of the same walk exist, sharing the same underlying time-parsing primitives (`gtfs_ingest._seconds_since_midnight` / `_anchor_datetime`):
   - `parse_legs(gtfs_dir, service_date) -> list[Leg]` — anchors `scheduled_departure`/`scheduled_arrival` to one concrete `datetime` on `service_date`. Backs the §7 JSON build.
   - `parse_leg_templates(gtfs_dir) -> list[LegTemplate]` — stores `departure_seconds`/`arrival_seconds` (seconds since midnight of the trip's nominal service day, GTFS-style so post-midnight trips can exceed 86400) instead of a concrete datetime, plus `trip_id`/`sequence_index`. One row regardless of how many calendar days the trip's `service_id` covers. Backs the §6 warehouse build.

   `delay_distribution_minutes` (Leg) is left as a placeholder (`{"0": 1.0}`) here — Pipeline 2 (§4) overwrites it. `LegTemplate` has no such field; distributions are looked up by `line_id` at query time instead (§6.3).
6. **Transfers.** Not present in GTFS at all — derived: for every station, find (arriving leg, departing leg) pairs from *different* trips where the destination/origin station matches and the gap falls within a configurable window (default 2–60 minutes, §9.4). `scheduled_buffer_minutes = departure - arrival`. Two representations, same relationship as step 5: `derive_transfers(legs) -> list[Transfer]` (anchored) and `derive_transfer_templates(leg_templates) -> list[TransferTemplate]` (date-agnostic, also carrying `from_trip_id`/`to_trip_id` so §6.3's query-time calendar filter needs no join back to `trips`).

### 3.1 Line-type string consistency (hard requirement)

`engine.py` does an exact-string dict lookup:

```python
SERVICE_FREQUENCY_MINUTES = {"ICE": 60, "IC": 60, "RE": 60, "RB": 60, "S-Bahn": 20}
```

If `gtfs_ingest.py` emits any other spelling (`"S"`, `"S-BAHN"`, `"Ice"`, GTFS's raw integer `route_type` codes, etc.), `get_headway_minutes()` raises `ValueError` at simulation time and the whole route blows up. `_normalize_line_type()` normalizes DB's GTFS route-type/route-short-name values to exactly these five strings, and both `build_dataset.py` and `build_warehouse.py`'s validation passes assert every emitted `Line.type` is a member of `gtfs_ingest.LINE_TYPES` before writing output — fail the build, don't let a bad line type reach `engine.py` at runtime.

## 4. Delay Distribution Pipeline

**Input:** Monthly Parquet files from the `piebro/deutsche-bahn-data` archive.

Steps:

1. **Download & cache** the relevant monthly Parquet files under `data/raw/delays_<month>.parquet` (`pipelines/download_raw_data.py`).
2. **Reconstruct leg-level delay.** `pipelines/delay_mapping.py` computes realized arrival delay directly from `arrival_planned_time`/`arrival_change_time` (not the raw `delay_in_min` column, which turned out to be departure-delay-biased and unreliable as an arrival-delay proxy) for the destination station of each historical leg occurrence. Rows with no real arrival event (the train's own origin) or a canceled arrival are dropped.
3. **Bucket** each realized delay into the scheme `0, 5, 15, 30, 60` minutes — "closest boundary not exceeding it" (`delay_aggregation.bucket_delay`), matching how `engine.py`'s `transfer_miss_probability` interprets buckets (`int(bucket) > buffer`).
4. **Aggregate per `line_id`.** Count historical occurrences per line, compute the empirical fraction in each bucket.
5. **Apply the fallback rule:** a `line_id` with fewer than `DEFAULT_MIN_SAMPLES` (= 30, §9.2) historical occurrences falls back to a **train-type-level** aggregate distribution (all ICE+IC pooled, all RE+RB pooled, S-Bahn alone — `delay_aggregation.FALLBACK_GROUPS`). Mirrors `SPEC.md` §2.6's service-frequency fallback shape.
6. **Normalize** each final distribution so its values sum to exactly 1.0 within floating-point tolerance — `models.py`'s `probabilities_sum_to_one` validator enforces this for free the moment a `Leg` is constructed from it; `pipelines/warehouse_writer.py` writes the same normalized values into the §6.2 `delay_distributions` table with no extra validation needed there either.

This pipeline is entirely **date-independent** — a delay distribution is an aggregate over historical occurrences, not tied to any single calendar date — which is exactly why the DuckDB warehouse (§6) only had to make *topology* (which legs/transfers exist) dynamic; `delay_distributions` needed no date dimension at all.

## 5. Route Search & Candidate Generation

`app.py` doesn't filter a hand-curated route list — `pipelines/route_search.py` (§7) and `pipelines/route_search_duckdb.py` (§6) both generate candidate `Route` objects on demand from `(origin_station_id, destination_station_id, departure_time)`, extended for the DuckDB backend with a `service_date`. Both implement the same shape, deliberately modest and symmetric with the rest of this app's "don't over-engineer" ethos:

- Direct legs (no transfer) between origin and destination, departing at or after the requested time.
- Single-transfer journeys: leg A into any station, transfer within the derived-transfer window (§3 step 6), leg B onward to the destination.
- Two-transfer journeys: leg A, a transfer, leg B, a second transfer, leg C into the destination. (Extended from an original 1-transfer cap once the network grew large enough — e.g. Berlin Hbf ↔ Leipzig Hbf — for some real station pairs to need the extra hop; see `pipelines/route_search.py`'s module docstring.)

Still explicitly **not** in scope: 3+ transfer journeys, full graph pathfinding/Dijkstra/RAPTOR/CSA, or a recursive "best alternative on miss" search beyond the one level Monte Carlo already does (`SPEC.md` §3.4, §7). `engine.py`'s `simulate_route()` chains delays/buffers/miss-probabilities generically over however many legs and transfers a `Route` has, so neither module needed to touch the simulation core.

- `pipelines/route_search.py::find_candidate_routes(dataset, ...)` — in-memory, over an already-loaded `MockDataset`. Backs the §7 JSON data sources.
- `pipelines/route_search_duckdb.py::find_candidate_routes(conn, ..., service_date, legs_by_id, transfers_by_id)` — each step is a small SQL query against the warehouse, scoped to origin station + the active `service_id`s for `service_date` (§6.3). Resolved `Leg`/`Transfer` objects are written into the caller-supplied dicts in place, so only whatever a given search actually touches (the top-level search, plus one fallback search per transfer node from `engine.py`'s `precompute_fallback_plans`) ever loads into memory — never the whole network. Backs the §6 DuckDB data source.

## 6. DuckDB Warehouse Schema (Phase 3)

### 6.1 Design goal

Store topology **date-agnostically** — one row per template leg/transfer regardless of how many calendar days the ingested GTFS feed covers — and resolve which ones are actually running on a given search's date at query time, as SQL, rather than baking one date's answer in at build time (as §7's JSON build does). Delay distributions need no date dimension at all (§4). This is what lets a full month of calendar (the current real build covers 2026-08-22 .. 2026-09-21) live in a 9MB file instead of one JSON snapshot per day.

### 6.2 Tables

| Table | Columns | Notes |
|---|---|---|
| `stations` | `station_id PK, name` | Static |
| `lines` | `line_id PK, type, operator` | Static, small — loaded eagerly at query time, unlike legs/transfers |
| `trips` | `trip_id PK, line_id, service_id` | One row per GTFS trip |
| `leg_templates` | `leg_id PK, trip_id, line_id, sequence_index, origin_station_id, destination_station_id, departure_seconds, arrival_seconds` | Times are seconds-since-midnight-of-service-day, not a concrete datetime (§3 step 5) |
| `transfer_templates` | `transfer_id PK, station_id, from_leg_id, to_leg_id, from_trip_id, to_trip_id, buffer_minutes` | Precomputed once at build time from time-of-day gaps (§3 step 6); `from_trip_id`/`to_trip_id` are denormalized so the calendar filter (§6.3) needs no join back to `trips` |
| `service_calendar` | `service_id, monday..sunday, start_date, end_date` | Direct mirror of `calendar.txt` |
| `service_calendar_exceptions` | `service_id, date, exception_type` | Direct mirror of `calendar_dates.txt` (1 = add, 2 = remove) |
| `delay_distributions` | `line_id, bucket_minutes, probability` | Long format, date-independent (§4) |

`pipelines/warehouse_writer.py` owns the DDL (`create_schema`) and the write path (`write_warehouse`, which clears existing rows first so rebuilding an existing warehouse file is idempotent).

### 6.3 Dynamic date filtering at query time

`route_search_duckdb.find_candidate_routes(conn, origin_id, destination_id, departure_time, service_date, legs_by_id, transfers_by_id)`, per search:

1. **Resolve active `service_id`s** for `service_date` into a session-temp table (`_resolve_active_service_ids`): weekday-column match against `service_calendar`'s date range, unioned with same-date `exception_type=1` additions and excluding same-date `exception_type=2` removals from `service_calendar_exceptions`. Re-run once per `find_candidate_routes` call.
2. **Materialize origin legs**: `leg_templates` joined to `trips`, filtered to `origin_station_id = ?` and an active `service_id`, with `departure_seconds >= cutoff_seconds` (the requested departure time expressed as seconds since midnight of `service_date`). Each row becomes a concrete `Leg` (seconds → `datetime` via `gtfs_ingest._anchor_datetime`, distribution looked up from `delay_distributions` and cached per-call by `line_id`).
3. **Walk transfers** the same 2-hop shape §5 describes (leg A → transfer → leg B → transfer → leg C), with each transfer step's candidates coming from `transfer_templates` joined to `trips` twice (`from_trip_id` and `to_trip_id`), both required to have an active `service_id` — the algorithm shape is identical to `route_search.py`'s in-memory version; only where each step's rows come from differs.
4. Every resolved `Leg`/`Transfer` is written into the caller-supplied `legs_by_id`/`transfers_by_id` dicts as a side effect, so `engine.py`'s `simulate_route()`/`precompute_fallback_plans()` — which look objects up by id from those same dicts, completely unmodified — never need the whole network in memory, only whatever this and any prior calls in the same search actually touched (`SPEC.md` §3.5).

`route_search_duckdb.calendar_window(conn)` returns `(MIN(start_date), MAX(end_date))` from `service_calendar`, used to bound `app.py`'s date picker (`SPEC.md` §5.1).

### 6.4 Build path

`pipelines/build_warehouse.py` mirrors `build_dataset.py`'s two-path structure:

- `build_warehouse(conn, gtfs_dir, historical_delays)` — fixture/demo path against `fixtures/gtfs_smoke/`, same crosswalk step as `build_dataset.build_dataset()`.
- `build_real_warehouse(conn, raw_dir)` — real path: scopes each downloaded feed with `scope_gtfs_feed_multi_day()` (§3 step 2, no `service_date` — every calendar day survives), ingests via the date-agnostic parsers (§3 steps 5–6), dedupes cross-feed duplicate legs the same way `build_dataset.py` does, keeps only corridor-to-corridor legs, crosswalks station ids, and fills delay distributions from the real piebro archive if `data/raw/delays_*.parquet` exists (synthetic per-type samples otherwise, same fallback `build_dataset.py` uses).

Both call `pipelines/warehouse_writer.write_warehouse()` to persist the result. `python -m pipelines.build_warehouse` runs whichever path has real GTFS zips present under `data/raw/`, prints row counts and the resolved calendar window on success.

## 7. JSON Backends & Data Access Layer (Phase 1/2)

**Loading (`data_loader.py`), unchanged since Phase 2:**

```python
MOCK_DATA_PATH = Path(__file__).parent / "mock_data.json"
REAL_DATA_PATH = Path(__file__).parent / "data" / "real_dataset.json"

def load_dataset(path: Path = MOCK_DATA_PATH) -> MockDataset:
    return MockDataset.model_validate(json.loads(path.read_text()))
```

`app.py`'s sidebar radio picks which path `get_dataset()` loads. `mock_data.json` and its test coverage (`test_models.py`, `test_engine.py`) stay untouched by every downstream pipeline change — the real pipelines are additive, not a replacement.

**Building `data/real_dataset.json`:** `pipelines/build_dataset.py` is a one-off/periodic script (run manually or on a schedule), not something `data_loader.py` or `app.py` invoke at request time. It scopes the downloaded GTFS feed to one `service_date` (§3 step 2, `scope_gtfs_feed`), ingests via the anchored parsers (§3 step 5), joins delay distributions (§4), and writes a validated `MockDataset` to `data/real_dataset.json`.

**DuckDB path (`db.py`)** is the Phase 3 counterpart to this section — see §6.

## 8. Build & Deployment Sequence

Phase 2 (steps 1–6, small independently testable milestones, still the build path for `data/real_dataset.json`):

1. `gtfs_ingest.py`: Stations + Lines, tested against `fixtures/gtfs_mini/`.
2. `gtfs_ingest.py`: Legs + Transfers, tested the same way; the line-type normalization from §3.1 has dedicated test coverage.
3. `delay_aggregation.py`: bucket + normalize + fallback logic, tested against a synthetic delay DataFrame with known expected output distributions.
4. `id_crosswalk.py`: GTFS `stop_id` ↔ station identifier mapping for the scoped corridor.
5. `build_dataset.py`: wires 1–4 together, runs `MockDataset.model_validate()`, writes `data/real_dataset.json`.
6. `route_search.py` + the `app.py` sidebar toggle: smoke-tested manually against the real dataset in Streamlit alongside the still-working mock-data path.

Phase 3 (steps 7–8, added once Phase 2 was stable — see §6 for the full schema):

7. `calendar_ingest.py` + `gtfs_ingest.py`'s date-agnostic `parse_trips`/`parse_leg_templates`/`derive_transfer_templates`, tested for parity against the anchored parsers on the same fixture (re-anchoring a `LegTemplate` onto a fixed date must reproduce exactly what `parse_legs` produces for that date).
8. `warehouse_writer.py` + `build_warehouse.py`: wires GTFS ingestion, calendar ingestion, and the delay pipeline (§4, unchanged) into `data/warehouse.duckdb`; `route_search_duckdb.py` + `app.py`'s date picker, smoke-tested against the real warehouse alongside the still-working Phase 1/2 paths.

Nothing in `models.py`, `engine.py`, `ui_components.py`, or the Phase 1/2 test files needed to change at any point across either build sequence.

## 9. Resolved Design Decisions

Originally flagged as open questions per the "raise it, don't silently pick" consensus pattern used for `SPEC.md`. All four are now resolved; kept here (rather than deleted) so the *reasoning* stays visible for future revisits.

### 9.1 Geographic/service scope

Resolved: expanded well past the original 11-station mock-data mirror to a 33-station "Golden 35" corridor (`pipelines/id_crosswalk.py`) covering the country's major ICE hubs, interchange points, and a handful of targeted "connector" stations added specifically to unlock otherwise one-hop-away stations. See that module's docstring for the station-by-station reasoning (including the known remaining gap: München Hbf ↔ München Marienplatz still isn't GTFS-adjacent in the current corridor).

### 9.2 Minimum sample threshold

Resolved: 30 historical occurrences (`delay_aggregation.DEFAULT_MIN_SAMPLES`), used unchanged for both the Phase 2 and Phase 3 builds.

### 9.3 Delay attribution

Resolved: destination-arrival delay only (not a full origin-departure join), computed directly from `arrival_planned_time`/`arrival_change_time` rather than trusting the raw `delay_in_min` column (§4 step 2) — the simpler approach turned out to also be the more reliable one once the raw column's departure-delay bias was found.

### 9.4 Transfer-window bounds

Resolved: 2–60 minutes for both derived-transfer generation (§3 step 6) and route-search's own transfer window (§5), the originally recommended values, unchanged since.

## 10. Known Limitations

- **Post-midnight cross-day lookback is not implemented.** A departure cutoff early in the morning does not consider a trip whose `service_id` belongs to the *previous* calendar date but whose times spill past midnight into the queried date — a search only considers `service_date`'s own active services (`SPEC.md` §7). Deferred deliberately rather than adding lookback logic for a v1.
- **Transfer-template growth.** `derive_transfer_templates`/`derive_transfers` is effectively quadratic in (arrivals × departures) per station — the current Golden 35 corridor build produces **~53,600 `transfer_templates` from ~4,000 `leg_templates`**. Fine at this scale (DuckDB handles it easily), but a materially larger corridor or a higher transfer cap would need a real time-expanded or round-based routing algorithm (RAPTOR/CSA) instead of enumerating transfer pairs up front — tracked as a Phase 4+ candidate in `SPEC.md` §7.
- **Regional `line_number` codes are not guaranteed nationally unique** the way ICE/IC train numbers are (`pipelines/delay_mapping.py`'s module docstring) — two unrelated regional lines in different parts of Germany could in principle share a short code. Filtering to only the `line_id`s a given build actually needs bounds the blast radius but doesn't eliminate it.
- **The 2-transfer cap** (`engine.py.MAX_TOTAL_TRANSFERS`, `pipelines/route_search.py`/`route_search_duckdb.py`) is a deliberate ceiling, not a hard architectural limit — raising it is possible but was not needed once the corridor's connector stations (§9.1) closed most previously-unreachable pairs at 2 hops.
