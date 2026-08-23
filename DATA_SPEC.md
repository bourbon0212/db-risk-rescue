# DB Risk & Rescue — Phase 2 Data Pipeline Engineering Spec

**Phase:** Engineering design (pre-implementation)
**Date:** 2026-08-23
**Status:** Draft for discussion — companion to `SPEC.md` and `PHASE2_DATA_SOURCES.md`
**Scope:** Offline-only. No live polling, no HAFAS/reverse-engineered sources (per decision in `PHASE2_DATA_SOURCES.md` §3).

This spec describes how to replace `mock_data.json` with real data while leaving `models.py`, `engine.py`, `ui_components.py`, and `app.py` untouched. Every pipeline below produces output that validates against the existing `MockDataset` Pydantic model — that contract is the hard boundary the rest of this document is built around.

## 1. Guiding Constraint

`data_loader.load_dataset()` currently has the signature:

```python
def load_dataset(path: Path = MOCK_DATA_PATH) -> MockDataset:
    return MockDataset.model_validate(json.loads(path.read_text()))
```

Phase 2 does **not** change this signature or `MockDataset`/`Station`/`Line`/`Leg`/`Transfer`/`Route`. It changes what JSON file gets built and, optionally, which path `load_dataset` points at. If a pipeline output can't validate against these models as they exist today, the pipeline is wrong, not the model — any schema gap gets raised as an open question (§7), not patched around silently.

## 2. New Component Layout

Per `CLAUDE.md`'s "small, modular files" directive, Phase 2 work lives in a new `pipelines/` package, not inside `data_loader.py` itself:

```
pipelines/
  gtfs_ingest.py        # GTFS.DE static feed -> Station, Line, Leg (topology only), Transfer
  delay_aggregation.py  # piebro Parquet archive -> delay_distribution_minutes per Leg
  id_crosswalk.py        # GTFS stop_id <-> EVA/IBNR <-> station_id mapping
  route_search.py        # on-demand candidate Route generation (see §5)
  build_dataset.py       # orchestrator: runs the above, assembles + validates a MockDataset, writes JSON
data/
  raw/                    # downloaded GTFS feed + piebro parquet snapshots (gitignored)
  real_dataset.json       # pipeline output — a new file, mock_data.json is left untouched
```

`data_loader.py` itself only gains a way to point at `data/real_dataset.json` instead of `mock_data.json` (§6) — it does not gain pipeline logic.

## 3. Pipeline 1 — Topology from GTFS.DE

**Input:** GTFS.DE static feed (`stops.txt`, `routes.txt`, `trips.txt`, `stop_times.txt`, `calendar.txt`).
**Tooling:** `gtfs-kit` or `partridge` for parsing (add to project dependencies).

Steps:

1. **Download & version.** Fetch the current GTFS.DE zip, store under `data/raw/gtfs_<date>.zip`. Re-run this step each *Fahrplanwechsel* (roughly quarterly) — not on every app run.
2. **Scope the feed before parsing anything else.** The national feed covers every bus, tram, and train in Germany — far more than this app needs. Filter to DB-operated `route_type`/`agency` entries only (ICE/IC/EC, RE/RB, S-Bahn), and further scope to a specific corridor for the initial build. Recommendation: mirror the station set already in `mock_data.json` (Frankfurt–Köln–Stuttgart–Mannheim–Heidelberg–München–Nürnberg–Leipzig–Berlin, plus Munich's S-Bahn) so Phase 2 output is directly comparable to Phase 1's mock data. This is an open question — see §7.1.
3. **Stations.** One GTFS `stop` (parent station, not individual platform stops) → one `Station`. `stop_id` becomes the ingestion-time key; final `station_id` is resolved through the crosswalk (§4).
4. **Lines.** One GTFS `route` → one `Line`. `route_short_name`/`route_type` maps to your existing `type` field (`ICE`/`IC`/`RE`/`RB`/`S-Bahn` — matching the exact strings `engine.py`'s `SERVICE_FREQUENCY_MINUTES` already keys on, see §3.1 below). `agency_name` maps to `operator`.
5. **Legs.** For each `trip`, walk `stop_times` in sequence; each consecutive stop pair becomes one `Leg`, with `scheduled_departure`/`scheduled_arrival` taken directly from the feed. `delay_distribution_minutes` is left as a placeholder here (e.g. `{"0": 1.0}`) — Pipeline 2 overwrites it.
6. **Transfers.** Not present in GTFS at all — derive them: for every station, find (arriving leg, departing leg) pairs where the destination/origin station matches and the gap is within a configurable window (e.g. 2–60 minutes). `scheduled_buffer_minutes = departure - arrival`, exactly as `SPEC.md` §2.4 already specifies. This is the same logic v1 used manually; Phase 2 just automates it across the whole scoped feed instead of four hand-picked transfers.

### 3.1 Line-type string consistency (hard requirement)

`engine.py` does an exact-string dict lookup:

```python
SERVICE_FREQUENCY_MINUTES = {"ICE": 60, "IC": 60, "RE": 60, "RB": 60, "S-Bahn": 20}
```

If `gtfs_ingest.py` emits any other spelling (`"S"`, `"S-BAHN"`, `"Ice"`, GTFS's raw integer `route_type` codes, etc.), `get_headway_minutes()` raises `ValueError` at simulation time and the whole route blows up. The ingestion step must normalize DB's GTFS route-type/route-short-name values to exactly these five strings, and `build_dataset.py`'s validation pass (§6) must assert every emitted `Line.type` is a member of that set before writing output — fail the build, don't let a bad line type reach `engine.py` at runtime.

## 4. Pipeline 2 — Delay Distributions from piebro/deutsche-bahn-data

**Input:** Monthly Parquet files from the `piebro/deutsche-bahn-data` archive (Hugging Face or GitHub release).

Steps:

1. **Download & cache** the relevant monthly Parquet files under `data/raw/delays_<month>.parquet`.
2. **Reconstruct leg-level delay** from the per-station-visit records: for a given train number + date, join its arrival-delay-at-destination-station and departure-delay-at-origin-station rows to get one realized delay figure per historical occurrence of a leg (or, more simply for v1 of this pipeline: use arrival delay at the destination station as the leg's realized delay — origin-side departure delay can be a §7 refinement, not a blocker).
3. **Bucket** each realized delay into the existing scheme: `0, 5, 15, 30, 60` minutes (i.e. "closest bucket not exceeding" or "first bucket boundary the delay falls under" — must match how `transfer_miss_probability` in `engine.py` interprets buckets: `int(bucket) > buffer`. Use the same bucket boundaries already in `mock_data.json` so no engine changes are needed).
4. **Aggregate per `line_id`.** Count historical occurrences per line, compute the empirical fraction in each bucket.
5. **Apply the fallback rule** (per the agreed Phase 2 approach): if a `line_id` has fewer than a minimum sample threshold (recommend **30** historical occurrences as a starting point — an explicit open question, §7.2), fall back to a **train-type-level** aggregate distribution instead (pool all ICE/IC legs together, all RE/RB legs together, all S-Bahn legs together). This mirrors the exact fallback pattern `SPEC.md` §2.6 already established for service frequency — same shape of decision, same place in the pipeline.
6. **Normalize** each final distribution so its values sum to exactly 1.0 within floating-point tolerance — `models.py`'s existing `probabilities_sum_to_one` validator on `Leg` enforces this for free the moment `build_dataset.py` tries to construct the model, so this pipeline gets a correctness check with zero new validation code.

## 5. Route (Candidate Journey) Generation — a required new piece, not in `SPEC.md` today

This is the one place where real data breaks a v1 assumption rather than just filling in a placeholder, so it's called out on its own.

`app.py` currently filters `dataset.routes` — a small, hand-curated list of 4 journeys baked directly into `mock_data.json`. `SPEC.md` §2.6 explicitly notes v1 has "no full timetable to query for 'what's the next train.'" Real GTFS data removes that constraint — and also removes the option of hand-authoring every possible journey, since a scoped corridor alone will have hundreds of legs per day.

Phase 2 therefore needs a `route_search.py` module that generates candidate `Route` objects on demand, given `(origin_station_id, destination_station_id, departure_time)` — the same three inputs `app.py`'s UI already collects. Recommended v1-of-this-feature scope, deliberately modest and symmetric with the rest of this app's "don't over-engineer" ethos:

- Direct legs (no transfer) between origin and destination, departing at or after the requested time.
- Single-transfer journeys: leg A into any station, transfer within the derived-transfer window (§3, step 6), leg B onward to the destination.
- Explicitly **not** in scope for Phase 2: multi-transfer (2+) journeys, full graph pathfinding/Dijkstra, or "best alternative on miss" re-routing — all of those are already listed as deferred in `SPEC.md` §5, and nothing about moving to real data changes that.

This module produces `Route` and `Transfer` objects using the same Pydantic models — `engine.py`'s `simulate_route()` needs no changes, since it already just consumes whatever `Route`/`Leg`/`Transfer` objects it's handed. `app.py`'s query loop (currently `[r for r in dataset.routes if ...]`) changes from filtering a static list to calling `route_search.find_candidate_routes(...)` — a small, contained edit, not a rewrite.

## 6. `data_loader.py` Changes

Minimal and additive:

```python
MOCK_DATA_PATH = Path(__file__).parent / "mock_data.json"
REAL_DATA_PATH = Path(__file__).parent / "data" / "real_dataset.json"

def load_dataset(path: Path = MOCK_DATA_PATH) -> MockDataset:
    return MockDataset.model_validate(json.loads(path.read_text()))
```

`app.py`'s `get_dataset()` picks which path to load (e.g. via an env var or a Streamlit sidebar toggle for easy A/B comparison during development) — `load_dataset()` itself doesn't need branching logic, since it already accepts a `path` argument. `mock_data.json` and its existing test coverage (`test_models.py`, `test_engine.py`) are left completely untouched, so Phase 1's test suite keeps passing throughout Phase 2 development — the real pipeline is additive, not a replacement, until it's proven out.

`build_dataset.py` (the orchestrator in `pipelines/`) is what actually calls `MockDataset.model_validate(...)` on the assembled real data and writes `data/real_dataset.json` — it is a one-off/periodic build script (run manually or on a schedule when the GTFS feed or delay archive updates), not something `data_loader.py` or `app.py` invoke at request time.

## 7. Open Questions

These mirror the consensus-driven pattern used to finalize `SPEC.md` — flagging them rather than silently picking an answer:

1. **Geographic/service scope for the first real build.** Recommended: mirror the existing mock-data station set (the Frankfurt–Köln–Stuttgart–München–Berlin corridor plus Munich S-Bahn) so Phase 2 output is a direct, comparable upgrade of Phase 1's routes rather than an entirely new surface to validate against. Expand coverage later once the pipeline is trusted.
2. **Minimum sample threshold for the line-level → train-type fallback.** Proposed starting value: 30 historical occurrences per line. Too low risks noisy per-line distributions; too high means most lines never get line-specific data at all in the first cut.
3. **Delay attribution: destination-arrival delay only, vs. joining origin-departure and destination-arrival.** The simpler destination-only approach (§4, step 2) is enough to unblock Phase 2; the fuller join is a refinement, not a blocker — confirm whether it's worth the extra complexity now or later.
4. **Transfer-window bounds for auto-derived transfers** (§3, step 6) and **route-search's own transfer window** (§5) — recommend starting at 2–60 minutes (below 2 is unrealistic to make on foot at most stations; above 60 stops looking like a "connection" and starts looking like an independent onward trip), tunable once real data is in hand.

## 8. Suggested Build Sequence

Small, independently testable milestones, per `CLAUDE.md`'s incremental-development and test-first directives:

1. `gtfs_ingest.py`: Stations + Lines only, with unit tests against a small hand-built GTFS fixture (not the full national feed).
2. `gtfs_ingest.py`: Legs + Transfers, tested the same way; assert the line-type normalization from §3.1 with a dedicated test.
3. `delay_aggregation.py`: bucket + normalize + fallback logic, tested against a small synthetic Parquet fixture with known expected output distributions.
4. `id_crosswalk.py`: GTFS `stop_id` ↔ station identifier mapping for the scoped station set.
5. `build_dataset.py`: wires 1–4 together, runs `MockDataset.model_validate()` on the result, writes `data/real_dataset.json`. First success criterion: this file validates with zero schema errors.
6. `route_search.py` + the small `app.py` edit described in §5, smoke-tested manually against the real dataset in Streamlit alongside the still-working mock-data path.

Nothing in `models.py`, `engine.py`, `ui_components.py`, or the existing test files needs to change at any point in this sequence.
