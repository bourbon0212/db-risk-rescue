# DB Risk & Rescue

Trip planning for German railways that answers the question a timetable can't: **will I actually make my connection, and what happens if I don't?**

Standard journey planners show you a scheduled arrival. This one runs a Monte Carlo simulation over historical delay data for every leg of your trip, then reports a **True ETA** — both the typical arrival and the "plan for this if you're unlucky" arrival — alongside a per-transfer risk read and a pre-computed backup plan for each connection that might fail.

Built on offline GTFS.DE timetable data and the [piebro/deutsche-bahn-data](https://huggingface.co/datasets/piebro/deutsche-bahn-data) historical delay archive. No live API calls.

---

## Quickstart

Requires **Python 3.11+**.

```bash
pip install -r requirements.txt
```

```bash
streamlit run app.py
```

The app opens on a search form: pick an origin, destination and departure time, and it returns ranked route cards showing predicted arrivals and transfer risk. Open **Details** on any card for the leg-by-leg itinerary.

### What data you get on a fresh clone

The sidebar has a **Data source** selector with three backends. Two work immediately; one needs a build step:

| Backend | Works after clone? | What it is |
|---|---|---|
| **Mock** | Yes | A small hand-authored fixture (11 stations). Fast, good for poking at the UI. |
| **Snapshot** | Yes | Real GTFS + delay data for a 33-station corridor, baked to one fixed date. |
| **Warehouse** | **No — needs building** | The same corridor, queryable for *any* date in a month-long window. The default selection. |

> **Heads-up on your first run.** Warehouse is selected by default but its database file is a build artifact and isn't in the repo. If it's missing the app shows a sidebar warning and quietly falls back to Mock — so if the network looks suspiciously tiny, that's why. Switch to **Snapshot** for real data with no build required.

To build the Warehouse backend yourself (downloads a few hundred MB of raw feeds):

```bash
python -m pipelines.download_raw_data
```

```bash
python -m pipelines.build_warehouse
```

### Running the tests

```bash
python -m pytest
```

---

## How it fits together

```
GTFS.DE timetable ─┐
                   ├─► pipelines/ ─► Pydantic contract ─► engine.py ─► ui_components.py
piebro delay data ─┘   (ingest,       (Station, Leg,      (Monte Carlo   (route cards,
                        crosswalk,     Transfer, Route)    simulation)    risk colours)
                        aggregate)
```

The **Pydantic contract** in `models.py` is the load-bearing idea. Everything upstream of it exists to produce those five object types; everything downstream consumes them and neither knows nor cares which backend produced them. That boundary is why the storage layer could be swapped from JSON files to a DuckDB warehouse without the simulation engine changing at all.

---

## Where to read next

Four specs, each owning one thing. Pick by what you're changing:

| I want to… | Read | Start at |
|---|---|---|
| Understand the risk maths | `SPEC.md` | §3 — there's a worked example at the top |
| Look up a threshold or constant | `SPEC.md` | §6 — every hardcoded number, in one table |
| Change colours, wording, or layout | `UIUX_SPEC.md` | §2 — the five-state risk system |
| Work on data ingestion or the warehouse | `DATA_SPEC.md` | §3 (ingestion) or §6 (warehouse schema) |
| Know why the project looks like this | `SPEC.md` | §7 — development phases |
| Find the known rough edges | `DATA_SPEC.md` §10, `SPEC.md` §8 | |

`CLAUDE.md` is configuration for AI coding agents, not human documentation — you can skip it.

**One naming convention worth knowing up front:** *Mock / Snapshot / Warehouse* are the three backends you can select at runtime. *Phase 1 / 2 / 3* are the project's development stages, which happen to have produced those backends in that order. The specs use both, deliberately — `SPEC.md` §4.2 maps between them.

---

## Project layout

```
app.py              Streamlit entry point — search flow, caching, pagination
engine.py           Monte Carlo simulation, risk scoring, fallback re-routing
models.py           The Pydantic contract everything is built around
ui_components.py    Route cards, risk colours, the expanded itinerary
data_loader.py      Loads the JSON backends
db.py               DuckDB connection helper for the Warehouse backend
pipelines/          Ingestion, delay aggregation, route search, warehouse build
fixtures/           Small GTFS fixtures used by the test suite
mock_data.json      The Mock backend's hand-authored dataset
```
