"""DB Risk & Rescue — Streamlit dashboard (SPEC.md Section 4)."""

import random
from datetime import date, datetime
from pathlib import Path

import duckdb
import streamlit as st

import db
from data_loader import MOCK_DATA_PATH, REAL_DATA_PATH, load_dataset
from engine import RouteSimulationResult, index_dataset, precompute_fallback_plans, simulate_route
from models import Leg, Line, MockDataset, Route, Station, Transfer
from pipelines import route_search_duckdb
from pipelines.route_search import find_candidate_routes
from ui_components import (
    inject_global_styles,
    render_header_banner,
    render_route_card,
    render_search_card,
)

N_ITERATIONS = 1000
RNG_SEED = 42  # fixed seed keeps ETAs stable across Streamlit reruns

SORT_FASTEST = "Fastest scheduled"
SORT_SAFEST = "Safest arrival"

st.set_page_config(page_title="DB Risk & Rescue", page_icon=":material/train:", layout="wide")
inject_global_styles()

# DATA_SPEC.md §6, §7 / SPEC.md §4, §5.1 — sidebar toggle for A/B comparing
# the Phase 1 mock timetable, the Phase 2 GTFS/JSON pipeline output, and the
# Phase 3 DuckDB warehouse (dynamic calendar dates) during development.
LABEL_MOCK = "Phase 1 — mock_data.json"
LABEL_REAL = "Phase 2 — real_dataset.json (GTFS pipeline)"
LABEL_WAREHOUSE = "Phase 3 — warehouse.duckdb (dynamic dates)"
JSON_DATA_SOURCES = {LABEL_MOCK: MOCK_DATA_PATH, LABEL_REAL: REAL_DATA_PATH}

with st.sidebar:
    st.caption("Data source")
    data_source_label = st.radio(
        "Data source", [LABEL_MOCK, LABEL_REAL, LABEL_WAREHOUSE], index=0,
        label_visibility="collapsed",
    )

use_warehouse = data_source_label == LABEL_WAREHOUSE

if use_warehouse and not db.WAREHOUSE_PATH.exists():
    st.sidebar.warning(
        "data/warehouse.duckdb not found — run `python -m pipelines.build_warehouse` "
        "first. Falling back to mock_data.json for now."
    )
    use_warehouse = False
    data_source_label = LABEL_MOCK

if not use_warehouse:
    data_source_path = JSON_DATA_SOURCES[data_source_label]
    if data_source_path == REAL_DATA_PATH and not REAL_DATA_PATH.exists():
        st.sidebar.warning(
            "data/real_dataset.json not found — run `python -m pipelines.build_dataset` "
            "first. Falling back to mock_data.json for now."
        )
        data_source_path = MOCK_DATA_PATH


@st.cache_data
def get_dataset(path: Path) -> MockDataset:
    return load_dataset(path)


@st.cache_resource
def get_warehouse_connection() -> duckdb.DuckDBPyConnection:
    return db.get_connection(read_only=True)


@st.cache_data(show_spinner="Searching the timetable and running Monte Carlo simulation...")
def search_and_simulate(
    path: Path,
    origin_id: str,
    destination_id: str,
    departure_time: datetime,
    n_iterations: int,
    seed: int,
) -> tuple[list[Route], dict[str, RouteSimulationResult]]:
    dataset = get_dataset(path)
    legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
    candidate_routes = find_candidate_routes(dataset, origin_id, destination_id, departure_time)
    results = {
        route.route_id: simulate_route(
            route, legs_by_id, transfers_by_id, lines_by_id,
            n_iterations=n_iterations, rng=random.Random(seed),
            fallback_plans=precompute_fallback_plans(route, dataset, legs_by_id, transfers_by_id),
        )
        for route in candidate_routes
    }
    return candidate_routes, results


@st.cache_data(show_spinner="Searching the timetable and running Monte Carlo simulation...")
def search_and_simulate_warehouse(
    _conn: duckdb.DuckDBPyConnection,
    origin_id: str,
    destination_id: str,
    departure_time: datetime,
    service_date: date,
    n_iterations: int,
    seed: int,
) -> tuple[
    list[Route], dict[str, RouteSimulationResult], dict[str, Leg], dict[str, Transfer], dict[str, Line]
]:
    """SPEC.md §4.3 — Phase 3 counterpart to search_and_simulate(), sourced
    from the DuckDB warehouse and scoped to service_date. legs_by_id/
    transfers_by_id start empty and are grown in place by every
    route_search_duckdb call this makes (the top-level search plus one
    fallback search per transfer node) -- unlike the JSON path, there's no
    whole-dataset index_dataset() call, so only whatever this search
    actually touches ever ends up in memory (SPEC.md §3.5).
    """
    # lines table is small/static (SPEC.md §4.3) -- eager-loading all of it
    # needs no per-search scoping, unlike legs/transfers.
    lines_by_id = {
        row[0]: Line(line_id=row[0], type=row[1], operator=row[2])
        for row in _conn.execute("SELECT line_id, type, operator FROM lines").fetchall()
    }

    legs_by_id: dict[str, Leg] = {}
    transfers_by_id: dict[str, Transfer] = {}

    candidate_routes = route_search_duckdb.find_candidate_routes(
        _conn, origin_id, destination_id, departure_time, service_date, legs_by_id, transfers_by_id
    )

    def route_search_fn(o: str, d: str, t: datetime) -> list[Route]:
        return route_search_duckdb.find_candidate_routes(
            _conn, o, d, t, service_date, legs_by_id, transfers_by_id
        )

    results = {
        route.route_id: simulate_route(
            route, legs_by_id, transfers_by_id, lines_by_id,
            n_iterations=n_iterations, rng=random.Random(seed),
            fallback_plans=precompute_fallback_plans(
                route, None, legs_by_id, transfers_by_id, route_search_fn=route_search_fn
            ),
        )
        for route in candidate_routes
    }
    return candidate_routes, results, legs_by_id, transfers_by_id, lines_by_id


render_header_banner(N_ITERATIONS)

# --- §4.1 Input flow ---------------------------------------------------------
if use_warehouse:
    conn = get_warehouse_connection()
    stations_by_id = {
        row[0]: Station(station_id=row[0], name=row[1])
        for row in conn.execute("SELECT station_id, name FROM stations").fetchall()
    }
    if not stations_by_id:
        st.warning("The warehouse has no stations to search.")
        st.stop()
    calendar_min, calendar_max = route_search_duckdb.calendar_window(conn)
    if calendar_min is None:
        st.warning("The warehouse has no calendar data to search.")
        st.stop()

    station_ids = list(stations_by_id)
    default_origin_id = station_ids[0]
    default_destination_id = station_ids[1] if len(station_ids) > 1 else station_ids[0]

    origin_id, destination_id, service_date, departure_time_of_day, sort_choice = render_search_card(
        station_ids, stations_by_id, default_origin_id, default_destination_id,
        default_departure_time=datetime.min.time(),
        sort_options=(SORT_FASTEST, SORT_SAFEST),
        calendar_range=(calendar_min, calendar_max),
        default_date=calendar_min,
    )
    departure_datetime = datetime.combine(service_date, departure_time_of_day)

    candidate_routes, results_by_route, legs_by_id, transfers_by_id, lines_by_id = search_and_simulate_warehouse(
        conn, origin_id, destination_id, departure_datetime, service_date, N_ITERATIONS, RNG_SEED
    )
else:
    dataset = get_dataset(data_source_path)
    legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
    stations_by_id = {s.station_id: s for s in dataset.stations}

    if not dataset.legs:
        st.warning("The selected dataset has no legs to search.")
        st.stop()

    station_ids = list(stations_by_id)
    earliest_leg = min(dataset.legs, key=lambda leg: leg.scheduled_departure)
    default_origin_id = earliest_leg.origin_station_id
    default_destination_id = next(
        (sid for sid in station_ids if sid != default_origin_id), default_origin_id
    )

    origin_id, destination_id, _service_date, departure_time_of_day, sort_choice = render_search_card(
        station_ids, stations_by_id, default_origin_id, default_destination_id,
        default_departure_time=earliest_leg.scheduled_departure.time(),
        sort_options=(SORT_FASTEST, SORT_SAFEST),
    )
    departure_datetime = datetime.combine(
        earliest_leg.scheduled_departure.date(), departure_time_of_day
    )

    candidate_routes, results_by_route = search_and_simulate(
        data_source_path, origin_id, destination_id, departure_datetime, N_ITERATIONS, RNG_SEED
    )

if not candidate_routes:
    st.info(
        "No routes with up to 2 transfers match that search. Try an earlier departure time "
        "or a different pair of stations."
    )
    st.stop()

# --- §4.2 Route comparison view ----------------------------------------------
if sort_choice == SORT_FASTEST:
    candidate_routes = sorted(candidate_routes, key=lambda r: r.scheduled_arrival)
else:
    candidate_routes = sorted(
        candidate_routes, key=lambda r: results_by_route[r.route_id].p85_eta
    )

for route in candidate_routes:
    result = results_by_route[route.route_id]
    render_route_card(route, result, stations_by_id, legs_by_id, transfers_by_id, lines_by_id)
