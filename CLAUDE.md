# DB Risk & Rescue - Developer Guidelines

## Role

You are an expert Python Developer and Data Scientist implementing the DB Delay Engine based strictly on `SPEC.md` (product/algorithm spec) and `DATA_SPEC.md` (data architecture spec).

## Tech Stack

- Python 3.11+
- Streamlit (UI)
- Pydantic (data validation — the Station/Line/Leg/Transfer/Route contract `engine.py` and `ui_components.py` consume, regardless of which backend produced the data)
- DuckDB (Phase 3 warehouse: date-agnostic topology + dynamic calendar filtering — see `DATA_SPEC.md` §9)
- pandas / pyarrow (GTFS + historical delay Parquet ingestion pipelines)
- pytest (unit testing)

## Current Architecture (as of Phase 3)

- **Query-time contract**: `models.py`'s Pydantic models (Station/Line/Leg/Transfer/Route, `SPEC.md` §2) are the one interface `engine.py`'s Monte Carlo simulation and `ui_components.py`'s rendering consume. This hasn't changed since Phase 1 — only what produces those objects has.
- **Three selectable data sources** in `app.py`'s sidebar, all still functional:
  - Mock — `mock_data.json`, hand-authored fixture data.
  - Snapshot — `data/real_dataset.json`, a GTFS.DE + piebro-delay pipeline output baked to one fixed calendar date.
  - Warehouse (default) — `data/warehouse.duckdb`, a DuckDB warehouse of date-agnostic `leg_templates`/`transfer_templates` plus GTFS `service_calendar`, so route search can be scoped to any date in the ingested calendar window at query time (`DATA_SPEC.md` §9).
- **Simulation engine**: `engine.py`'s Monte Carlo loop never touches a database — it consumes plain in-memory `Leg`/`Transfer` objects assembled once per search. Missed-connection re-routing (`precompute_fallback_plans`) is pre-computed once per transfer node before the simulation loop, giving each iteration an O(1) cache lookup on a miss instead of a fresh pathfinding search (`SPEC.md` §3.4).

## Core Directives

1. **Spec-Driven**: Always refer to `SPEC.md` for algorithm/UI logic and `DATA_SPEC.md` for data architecture. Do not over-engineer or implement features from either doc's "Future Extensions" section without an explicit go-ahead.
2. **Incremental Development**: Write small, modular files (see `pipelines/`). Never write one giant `app.py`.
3. **Test-First**: Before running Streamlit, ensure data models, pipeline stages, and algorithms have pytest coverage.
4. **Offline data only, real or fixture**: This project uses committed fixtures (`mock_data.json`, `fixtures/`) and offline-downloaded GTFS.DE/piebro archives (`data/raw/`, gitignored) — never live HAFAS polling, reverse-engineered APIs, or web scraping (`DATA_SPEC.md` §1's offline-only decision). Real-data pipelines (`pipelines/gtfs_ingest.py`, `pipelines/build_warehouse.py`, etc.) are expected and already exist — this directive is about avoiding *live* API calls, not about avoiding real data.
