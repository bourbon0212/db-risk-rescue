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
- requests (offline archive downloads in `pipelines/`, and the deploy-time warehouse fetch — `DATA_SPEC.md` §8.3)
- pytest (unit testing)

Runtime dependencies live in `requirements.txt` (what a deploy installs);
`requirements-dev.txt` adds pytest and the pipelines' tqdm. Install the dev
file when working in this repo.

## Architecture

Don't restate it here — it lives in the specs and would drift if mirrored:
the Pydantic contract (`SPEC.md` §2), the three backends (§4.2), the
simulation engine and its O(1) fallback cache (§3.4), and the phase history
(§7). Read those before changing anything they govern.

**Before creating a new module, read `DATA_SPEC.md` §2** — it defines where
code goes (root = app-level, `pipelines/` = offline build, `routing/` =
query-time) and works the rule through the one case that looks ambiguous.
Keep Streamlit imports out of any module that doesn't need them: exactly
three have one (`app.py`, `ui_components.py`, `warehouse_fetch.py`), and the
last is split so its download half stays testable without a Streamlit
runtime. `models.py`, `engine.py`, `data_loader.py`, `db.py` and
`gtfs_time.py` must stay framework-free.

## Core Directives

1. **Spec-Driven**: Refer to `SPEC.md` for algorithm/engine logic (including `SPEC.md` §6 for every hardcoded threshold/constant), `DATA_SPEC.md` for data architecture, and `UIUX_SPEC.md` for exact UI colors/phrases/component structure. Do not over-engineer or implement features from any doc's "Future Extensions"/"Roadmap" section without an explicit go-ahead.
2. **Incremental Development**: Write small, modular files (see `pipelines/` and `routing/`). Never write one giant `app.py`.
3. **Test-First**: Before running Streamlit, ensure data models, pipeline stages, and algorithms have pytest coverage.
4. **Offline data only, real or fixture**: This project uses committed fixtures (`data/mock_data.json`, `data/fixtures/`) and offline-downloaded GTFS.DE/piebro archives (`data/raw/`, gitignored) — never live HAFAS polling, reverse-engineered APIs, or web scraping (`DATA_SPEC.md` §1's offline-only decision). Real-data pipelines (`pipelines/gtfs_ingest.py`, `pipelines/build_warehouse.py`, etc.) are expected and already exist — this directive is about avoiding *live* API calls, not about avoiding real data.

   `warehouse_fetch.py` is not an exception to this, despite making an HTTPS request while the app is running. It fetches one pre-built static file (`DATA_SPEC.md` §8.3) that a pipeline produced offline hours or weeks earlier; no timetable or delay figure is ever queried live, and the result is byte-identical to the file you'd build locally. Adding a network call that reads *transport data* at request time is still off-limits and would need an explicit go-ahead.
