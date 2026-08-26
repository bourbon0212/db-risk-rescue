# DB Risk & Rescue

Trip planning for German railways that answers the question a timetable can't: **will I actually make my connection, and what happens if I don't?**

Standard journey planners show you a scheduled arrival. This one runs a Monte Carlo simulation over historical delay data for every leg of your trip, then reports a **True ETA** — both the typical arrival and the "plan for this if you're unlucky" arrival — alongside a per-transfer risk read and a pre-computed backup plan for each connection that might fail.

**[Try it live → db-risk-rescue.streamlit.app](https://db-risk-rescue.streamlit.app/)** — the deployed app, running the full 33-station Warehouse backend over a month-long date window.

Built on offline GTFS.DE timetable data and the [piebro/deutsche-bahn-data](https://huggingface.co/datasets/piebro/deutsche-bahn-data) historical delay archive. No live departure-board APIs and no scraping — every timetable and delay figure comes from an archive downloaded ahead of time.

![The app showing a Frankfurt to Köln search: a "Plan your trip" panel above ranked route cards, each with scheduled times, an Expected and Safest predicted arrival, and a coloured left edge indicating overall route risk.](assets/screenshot.png)

*Each card carries two predictions — **Expected** (typical) and **Safest** (the 85th percentile) — plus a coloured left edge summarising the route's overall risk. The three direct trains are green; the one-transfer route at the bottom is red because its Safest arrival lands 63 minutes past schedule.*

---

## Quickstart

Requires **Python 3.11+**.

```bash
pip install -r requirements.txt
```

```bash
streamlit run app.py
```

`requirements.txt` is what the app itself needs. To run the tests or the data
pipelines, install `requirements-dev.txt` instead — it pulls in the runtime
file and adds the rest.

The app opens on a search form: pick an origin, destination and departure time, and it returns ranked route cards showing predicted arrivals and transfer risk. Open **Details** on any card for the leg-by-leg itinerary.

### What data you get on a fresh clone

The sidebar has a **Data source** selector with three backends. Two work immediately; one needs a build step:

| Backend | Works after clone? | What it is |
|---|---|---|
| **Mock** | Yes | A small hand-authored fixture (11 stations). Fast, good for poking at the UI. |
| **Snapshot** | Yes | Real GTFS + delay data for a 33-station corridor, baked to one fixed date. |
| **Warehouse** | **No — build or download it** | The same corridor, queryable for *any* date in a month-long window. The default selection. |

> **Heads-up on your first run.** Warehouse is selected by default, but its database file is a build artifact and isn't in the repo. When it's missing the app says so in the sidebar and drops to **Snapshot** — still real data, just pinned to one date. Build or download the warehouse when you want to search other dates.

To build the Warehouse backend yourself (needs `requirements-dev.txt`, and
downloads a few hundred MB of raw feeds):

```bash
python -m pipelines.download_raw_data
```

```bash
python -m pipelines.build_warehouse
```

Or skip the build entirely — the finished database is published as a GitHub
Release asset (~58 MB), so you can drop it straight into `data/`:

```bash
curl -L -o data/warehouse.duckdb https://github.com/bourbon0212/db-risk-rescue/releases/download/warehouse-2026-08-22/warehouse.duckdb
```

### Running the tests

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest
```

### Deploying your own copy

A deployed app can't build anything and can't carry a 58 MB binary through
git, so it fetches that same release asset at startup: set a `WAREHOUSE_URL`
secret to the asset's URL and `warehouse_fetch.py` downloads it once per
container, after which the Warehouse backend behaves exactly as it does
locally. With no secret set — or a dead one — the app degrades to
**Snapshot** and says why in the sidebar, so a misconfigured deploy still
serves real data rather than an error page.

The full recipe (publishing the release, the `share.streamlit.io` fields,
the Python version, what each failure mode looks like) is `DATA_SPEC.md`
§8.3.

---

## How it fits together

```
GTFS.DE timetable ─┐
                   ├─► pipelines/ ─► Pydantic contract ─► routing/ ─► engine.py ─► ui_components.py
piebro delay data ─┘   (ingest,      (Station, Leg,       (routes)    (Monte Carlo (route cards,
                       crosswalk,    Transfer, Route)                 simulation)  risk colours)
                       aggregate)
```

The **Pydantic contract** in `models.py` is the load-bearing idea. Everything upstream of it exists to produce those five object types; everything downstream consumes them and neither knows nor cares which backend produced them. That boundary is why the storage layer could be swapped from JSON files to a DuckDB warehouse without the simulation engine changing at all.

---

## Where to read next

Three specs, each owning one thing. Pick by what you're changing:

| I want to… | Read | Start at |
|---|---|---|
| Understand the risk maths | `SPEC.md` | §3 — there's a worked example at the top |
| Look up a threshold or constant | `SPEC.md` | §6 — every hardcoded number, in one table |
| Change colours, wording, or layout | `UIUX_SPEC.md` | §2 — the five-state risk system |
| Work on data ingestion or the warehouse | `DATA_SPEC.md` | §3 (ingestion) or §6 (warehouse schema) |
| Know why the project looks like this | `SPEC.md` | §7 — development phases |
| Deploy it, or change how the warehouse is hosted | `DATA_SPEC.md` | §8.3 — release asset + `WAREHOUSE_URL` |
| Find the known rough edges | `DATA_SPEC.md` §10, `SPEC.md` §8 | |

`CLAUDE.md` is configuration for AI coding agents, not human documentation — you can skip it.

**Two naming conventions worth knowing up front.**

*Mock / Snapshot / Warehouse* are the three backends you can select at runtime. *Phase 1 / 2 / 3* are the project's development stages, which happen to have produced those backends in that order. The specs use both, deliberately — `SPEC.md` §4.2 maps between them.

And one wart: the Pydantic class holding a loaded dataset is called **`MockDataset`**, but it is not the Mock backend — all three backends produce one. The name dates from Phase 1, when the Mock fixture was the only source.

---

## Project layout

The root holds the application modules, the docs, and three config files —
nothing else. Datasets live in `data/`, tests in `tests/`, the design reference
in `design/`, and README imagery in `assets/`.

```
app.py                  Streamlit entry point — search flow, caching, pagination
engine.py               Monte Carlo simulation, risk scoring, fallback re-routing
models.py               The Pydantic contract everything is built around
ui_components.py        Route cards, risk colours, the expanded itinerary
data_loader.py          Loads the JSON backends (Mock, Snapshot)
db.py                   DuckDB connection helper for the Warehouse backend
warehouse_fetch.py      Fetches the Warehouse database on a deploy, which can't build it
gtfs_time.py            GTFS time/calendar helpers shared by pipelines/ and routing/
pytest.ini              Puts the repo root on sys.path so tests/ can import the above
requirements.txt        Pinned runtime dependencies — what a deploy installs
requirements-dev.txt    The above plus pytest and the pipelines' tqdm

pipelines/              Offline ingestion, delay aggregation, warehouse build (python -m)
routing/                Candidate route search and filtering, on every request
tests/                  The pytest suite

data/
  mock_data.json     Mock backend — hand-authored, committed
  real_dataset.json  Snapshot backend — pipeline output, committed
  warehouse.duckdb   Warehouse backend — pipeline output, gitignored
  fixtures/          Small GTFS feeds for the demo pipeline and the test suite
  raw/               Downloaded GTFS.DE / piebro archives, gitignored

design/
  design_mock.html   The reference mockup UIUX_SPEC.md's visual system came from

assets/
  screenshot.png     The screenshot at the top of this README

.streamlit/
  config.toml        Native Streamlit widget theme (UIUX_SPEC.md §3 owns the values)
  secrets.toml       WAREHOUSE_URL, if you set one locally — gitignored, never committed
```

**Where a new module goes.** The root is for app-level modules — anything
`app.py` imports directly to run a request. Offline build code goes in
`pipelines/` (run with `python -m`), query-time route search in `routing/`.
That split is why `warehouse_fetch.py` sits at the root: it runs at startup
inside the app, not as a build step. Three root modules import Streamlit —
`app.py`, `ui_components.py` and `warehouse_fetch.py`; the rest (`models.py`,
`engine.py`, `data_loader.py`, `db.py`, `gtfs_time.py`) stay framework-free
so they're testable without a Streamlit runtime. `warehouse_fetch.py` keeps
its own download half on that side of the line for the same reason, which is
why it's split in two.

The three files at the top of `data/` are the three backends the sidebar radio
switches between (`SPEC.md` §4.2). Only the first two are in git — see
"Missing-data degradation" in `SPEC.md` §4.2 for what happens on a fresh clone,
and `DATA_SPEC.md` §8.3 for how a deploy gets the third.

The three specs (`SPEC.md`, `DATA_SPEC.md`, `UIUX_SPEC.md`) stay at the root
next to this README, where they're easiest to find, as does `CLAUDE.md` —
which is agent configuration rather than a spec.
