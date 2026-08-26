# DB Risk & Rescue — AI Agent Configuration

> **Human contributors: start with [README.md](README.md).** This file is instructions for AI coding agents working in this repo — it's written in the second person, addressed to the agent. It's not an onboarding guide, and it deliberately duplicates nothing from the specs.

## Role

You are an expert Python Developer and Data Scientist implementing the DB Delay Engine based strictly on `SPEC.md` (product/algorithm spec), `DATA_SPEC.md` (data architecture spec), and `UIUX_SPEC.md` (UI/UX design spec).

## Tech Stack

- Python 3.11+
- Streamlit (UI)
- Pydantic (data validation — the Station/Line/Leg/Transfer/Route contract `engine.py` and `ui_components.py` consume, regardless of which backend produced the data)
- DuckDB (the Warehouse backend, added in Phase 3: date-agnostic topology + dynamic calendar filtering — see `DATA_SPEC.md` §6)
- pandas / pyarrow (GTFS + historical delay Parquet ingestion pipelines)
- pytest (unit testing)

## Architecture

Don't restate it here — it lives in the specs and would drift if mirrored:
the Pydantic contract (`SPEC.md` §2), the three backends (§4.2), the
simulation engine and its O(1) fallback cache (§3.4), and the phase history
(§7). Read those before changing anything they govern.

## Core Directives

1. **Spec-Driven**: Refer to `SPEC.md` for algorithm/engine logic (including `SPEC.md` §6 for every hardcoded threshold/constant), `DATA_SPEC.md` for data architecture, and `UIUX_SPEC.md` for exact UI colors/phrases/component structure. Do not over-engineer or implement features from any doc's "Future Extensions"/"Roadmap" section without an explicit go-ahead.
2. **Incremental Development**: Write small, modular files (see `pipelines/` and `routing/`). Never write one giant `app.py`.
3. **Test-First**: Before running Streamlit, ensure data models, pipeline stages, and algorithms have pytest coverage.
4. **Offline data only, real or fixture**: This project uses committed fixtures (`data/mock_data.json`, `data/fixtures/`) and offline-downloaded GTFS.DE/piebro archives (`data/raw/`, gitignored) — never live HAFAS polling, reverse-engineered APIs, or web scraping (`DATA_SPEC.md` §1's offline-only decision). Real-data pipelines (`pipelines/gtfs_ingest.py`, `pipelines/build_warehouse.py`, etc.) are expected and already exist — this directive is about avoiding *live* API calls, not about avoiding real data.
