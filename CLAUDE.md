# DB Risk & Rescue - Developer Guidelines

## Role

You are an expert Python Developer and Data Scientist implementing the DB Delay Engine based strictly on `SPEC.md` (product/algorithm spec), `DATA_SPEC.md` (data architecture spec), and `UIUX_SPEC.md` (UI/UX design spec).

## Tech Stack

- Python 3.11+
- Streamlit (UI)
- Pydantic (data validation — the Station/Line/Leg/Transfer/Route contract `engine.py` and `ui_components.py` consume, regardless of which backend produced the data)
- DuckDB (Phase 3 warehouse: date-agnostic topology + dynamic calendar filtering — see `DATA_SPEC.md` §6)
- pandas / pyarrow (GTFS + historical delay Parquet ingestion pipelines)
- pytest (unit testing)

## Current Architecture (as of Phase 3.2 — `SPEC.md` §7)

- **Query-time contract**: `models.py`'s Pydantic models (Station/Line/Leg/Transfer/Route, `SPEC.md` §2) are the one interface `engine.py`'s Monte Carlo simulation and `ui_components.py`'s rendering consume. Unchanged since Phase 1 — only what produces those objects has.
- **Three selectable data sources** in `app.py`'s sidebar (`SPEC.md` §4.2): Mock (`mock_data.json`), Snapshot (`data/real_dataset.json`, one fixed date), Warehouse (default, `data/warehouse.duckdb`, any date in the ingested calendar window).
- **Simulation engine**: `engine.py`'s Monte Carlo loop never touches a database — it consumes plain in-memory `Leg`/`Transfer` objects assembled once per search. Missed-connection re-routing (`precompute_fallback_plans`) is pre-computed once per transfer node before the simulation loop, giving each iteration an O(1) cache lookup on a miss (`SPEC.md` §3.4).

## Core Directives

1. **Spec-Driven**: Refer to `SPEC.md` for algorithm/engine logic (including `SPEC.md` §6 for every hardcoded threshold/constant), `DATA_SPEC.md` for data architecture, and `UIUX_SPEC.md` for exact UI colors/phrases/component structure. Do not over-engineer or implement features from any doc's "Future Extensions"/"Roadmap" section without an explicit go-ahead.
2. **Incremental Development**: Write small, modular files (see `pipelines/`). Never write one giant `app.py`.
3. **Test-First**: Before running Streamlit, ensure data models, pipeline stages, and algorithms have pytest coverage.
4. **Offline data only, real or fixture**: This project uses committed fixtures (`mock_data.json`, `fixtures/`) and offline-downloaded GTFS.DE/piebro archives (`data/raw/`, gitignored) — never live HAFAS polling, reverse-engineered APIs, or web scraping (`DATA_SPEC.md` §1's offline-only decision). Real-data pipelines (`pipelines/gtfs_ingest.py`, `pipelines/build_warehouse.py`, etc.) are expected and already exist — this directive is about avoiding *live* API calls, not about avoiding real data.
