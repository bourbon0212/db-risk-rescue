"""DB Risk & Rescue — Streamlit dashboard: backend selection and caching
(SPEC.md §4), search/pagination flow and rendering (SPEC.md §5)."""

import random
import time
from datetime import date, datetime
from pathlib import Path

import duckdb
import streamlit as st

import db
from data_loader import MOCK_DATA_PATH, REAL_DATA_PATH, load_dataset
from engine import RouteSimulationResult, index_dataset, precompute_fallback_plans, simulate_route
from models import Leg, Line, MockDataset, Route, Station, Transfer
from pipelines import route_search_duckdb
from pipelines.route_filters import apply_sanity_filter
from pipelines.route_search import (
    build_route_search_indexes,
    find_candidate_routes,
)
from ui_components import (
    inject_global_styles,
    render_header_banner,
    render_route_card,
    render_search_card,
)

N_ITERATIONS = 1000
RNG_SEED = 42  # fixed seed keeps ETAs stable across Streamlit reruns

# Both sorts rank by arrival time (SPEC.md §5.2), differing only in which
# arrival they trust -- scheduled vs. risk-adjusted. Neither sorts on duration.
SORT_EARLIEST = "Earliest scheduled"
SORT_SAFEST = "Safest arrival"

st.set_page_config(page_title="DB Risk & Rescue", page_icon=":material/train:", layout="wide")
inject_global_styles()

# DATA_SPEC.md §6, §7 / SPEC.md §4, §5.1 — sidebar toggle for A/B comparing
# the small hand-authored Mock timetable, the single-date GTFS Snapshot
# pipeline output, and the dynamic-date DuckDB Warehouse. Warehouse is the
# default since it's the most complete dataset.
LABEL_MOCK = "Mock"
LABEL_SNAPSHOT = "Snapshot"
LABEL_WAREHOUSE = "Warehouse"
JSON_DATA_SOURCES = {LABEL_MOCK: MOCK_DATA_PATH, LABEL_SNAPSHOT: REAL_DATA_PATH}

with st.sidebar:
    st.caption("Data source")
    data_source_label = st.radio(
        "Data source", [LABEL_MOCK, LABEL_SNAPSHOT, LABEL_WAREHOUSE], index=2,
        label_visibility="collapsed",
    )

use_warehouse = data_source_label == LABEL_WAREHOUSE

if use_warehouse and not db.WAREHOUSE_PATH.exists():
    st.sidebar.warning(
        "data/warehouse.duckdb not found — run `python -m pipelines.build_warehouse` "
        "first. Falling back to data/mock_data.json for now."
    )
    use_warehouse = False
    data_source_label = LABEL_MOCK

if not use_warehouse:
    data_source_path = JSON_DATA_SOURCES[data_source_label]
    if data_source_path == REAL_DATA_PATH and not REAL_DATA_PATH.exists():
        st.sidebar.warning(
            "data/real_dataset.json not found — run `python -m pipelines.build_dataset` "
            "first. Falling back to data/mock_data.json for now."
        )
        data_source_path = MOCK_DATA_PATH


@st.cache_data
def get_dataset(path: Path) -> MockDataset:
    return load_dataset(path)


@st.cache_data
def get_search_indexes(path: Path) -> tuple[dict[str, Leg], dict[str, list[Transfer]]]:
    """legs_by_id/transfers_by_from_leg for `path`'s dataset, built once and
    cached per path (same pattern as get_dataset) -- shared by search_routes'
    top-level find_candidate_routes call and every simulate_one_route call's
    precompute_fallback_plans, instead of each of those rebuilding both
    lookup tables from scratch (SPEC.md §3.5)."""
    return build_route_search_indexes(get_dataset(path))


@st.cache_resource
def get_warehouse_connection() -> duckdb.DuckDBPyConnection:
    return db.get_connection(read_only=True)


@st.cache_data(show_spinner="Searching the timetable...")
def search_routes(
    path: Path, origin_id: str, destination_id: str, departure_time: datetime
) -> list[Route]:
    """Route search only -- no simulation. Cached independently of
    display_limit (unlike the old combined search_and_simulate) so a "Load
    more" click never re-runs the search, and kept separate from simulation
    so simulate_one_route (below) can cache per-route instead of per-batch."""
    dataset = get_dataset(path)
    search_indexes = get_search_indexes(path)
    routes = find_candidate_routes(
        dataset, origin_id, destination_id, departure_time, indexes=search_indexes
    )
    return apply_sanity_filter(routes)


@st.cache_data(show_spinner=False)
def simulate_one_route(
    path: Path,
    route_id: str,
    n_iterations: int,
    seed: int,
    _route: Route,
    _search_indexes: tuple[dict[str, Leg], dict[str, list[Transfer]]],
    _stations_by_id: dict[str, Station],
) -> RouteSimulationResult:
    """Simulation for exactly one route, cached per route_id rather than per
    display_limit batch so "Load more" only pays for newly-revealed routes
    (SPEC.md §4.3).

    Underscore-prefixed args are excluded from Streamlit's cache key: each is
    either derived from `path` (already in the key) or a convenience handle,
    never part of cache identity. `_search_indexes` is passed through to
    precompute_fallback_plans so fallback searches reuse it (SPEC.md §3.5)."""
    dataset = get_dataset(path)
    legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
    fallback_plans = precompute_fallback_plans(
        _route, dataset, legs_by_id, transfers_by_id,
        search_indexes=_search_indexes, stations_by_id=_stations_by_id,
    )
    return simulate_route(
        _route, legs_by_id, transfers_by_id, lines_by_id,
        n_iterations=n_iterations, rng=random.Random(seed), fallback_plans=fallback_plans,
        stations_by_id=_stations_by_id,
    )


@st.cache_data(show_spinner="Searching the timetable...")
def search_routes_warehouse(
    _conn: duckdb.DuckDBPyConnection,
    origin_id: str,
    destination_id: str,
    departure_time: datetime,
    service_date: date,
) -> tuple[list[Route], dict[str, Leg], dict[str, Transfer]]:
    """DATA_SPEC.md §6.3 — Warehouse counterpart to search_routes(). legs_by_id/
    transfers_by_id start empty and are grown in place by find_candidate_routes
    (SPEC.md §3.5) -- returned so simulate_one_route_warehouse can resolve
    each route's own legs/transfers without a whole-dataset load. Cached
    independently of display_limit, same reasoning as search_routes()."""
    legs_by_id: dict[str, Leg] = {}
    transfers_by_id: dict[str, Transfer] = {}
    candidate_routes = route_search_duckdb.find_candidate_routes(
        _conn, origin_id, destination_id, departure_time, service_date, legs_by_id, transfers_by_id
    )
    candidate_routes = apply_sanity_filter(candidate_routes)
    return candidate_routes, legs_by_id, transfers_by_id


@st.cache_data(show_spinner=False)
def get_lines_warehouse(_conn: duckdb.DuckDBPyConnection) -> dict[str, Line]:
    # lines table is small/static (DATA_SPEC.md §6.2) -- eager-loading all of it
    # needs no per-search scoping, unlike legs/transfers.
    return {
        row[0]: Line(line_id=row[0], type=row[1], operator=row[2])
        for row in _conn.execute("SELECT line_id, type, operator FROM lines").fetchall()
    }


@st.cache_data(show_spinner=False)
def simulate_one_route_warehouse(
    route_id: str,
    service_date: date,
    n_iterations: int,
    seed: int,
    _conn: duckdb.DuckDBPyConnection,
    _route: Route,
    _legs_by_id: dict[str, Leg],
    _transfers_by_id: dict[str, Transfer],
    _lines_by_id: dict[str, Line],
    _stations_by_id: dict[str, Station],
) -> RouteSimulationResult:
    """Warehouse-path sibling of simulate_one_route() -- same per-route cache
    key reasoning (route_id is a stable, globally-unique identifier of a
    physical route derived from its underlying trip-based leg ids, so it's
    deterministic and safe to key on directly). service_date is included in
    the key because, unlike the JSON path's single baked-in date, a fallback
    search's results genuinely depend on which calendar day it's run for.
    """
    def route_search_fn(o: str, d: str, t: datetime) -> list[Route]:
        return route_search_duckdb.find_candidate_routes(
            _conn, o, d, t, service_date, _legs_by_id, _transfers_by_id
        )

    fallback_plans = precompute_fallback_plans(
        _route, None, _legs_by_id, _transfers_by_id,
        route_search_fn=route_search_fn, stations_by_id=_stations_by_id,
    )
    return simulate_route(
        _route, _legs_by_id, _transfers_by_id, _lines_by_id,
        n_iterations=n_iterations, rng=random.Random(seed), fallback_plans=fallback_plans,
        stations_by_id=_stations_by_id,
    )


DISPLAY_LIMIT_STEP = 5
st.session_state.setdefault("display_limit", DISPLAY_LIMIT_STEP)


def _sync_display_limit(search_key: tuple) -> None:
    """Resets the "Load More" pagination back to DISPLAY_LIMIT_STEP whenever
    the search itself changes (station pair, date, or departure time) --
    otherwise a limit raised by a previous search's "Load more" clicks would
    carry over and silently simulate more routes than the new search needs."""
    if st.session_state.get("last_search_key") != search_key:
        st.session_state["last_search_key"] = search_key
        st.session_state.display_limit = DISPLAY_LIMIT_STEP


def _load_more_routes() -> None:
    st.session_state.display_limit += DISPLAY_LIMIT_STEP


render_header_banner(N_ITERATIONS)

# --- SPEC.md §5.1 Input flow --------------------------------------------------
if use_warehouse:
    conn = get_warehouse_connection()
    stations_by_id = {
        row[0]: Station(station_id=row[0], name=row[1], mct_minutes=row[2])
        for row in conn.execute("SELECT station_id, name, mct_minutes FROM stations").fetchall()
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
        sort_options=(SORT_EARLIEST, SORT_SAFEST),
        calendar_range=(calendar_min, calendar_max),
        default_date=calendar_min,
    )
    departure_datetime = datetime.combine(service_date, departure_time_of_day)
    _sync_display_limit((data_source_label, origin_id, destination_id, departure_datetime, service_date))

    t_search0 = time.perf_counter()
    candidate_routes, legs_by_id, transfers_by_id = search_routes_warehouse(
        conn, origin_id, destination_id, departure_datetime, service_date
    )
    lines_by_id = get_lines_warehouse(conn)
    t_search1 = time.perf_counter()
    print(
        f"[timing] find_candidate_routes (warehouse): {t_search1 - t_search0:.3f}s, "
        f"{len(candidate_routes)} candidates"
    )

    total_route_count = len(candidate_routes)
    sliced_routes = candidate_routes[: st.session_state.display_limit]

    t_sim0 = time.perf_counter()
    results_by_route = {
        route.route_id: simulate_one_route_warehouse(
            route.route_id, service_date, N_ITERATIONS, RNG_SEED,
            conn, route, legs_by_id, transfers_by_id, lines_by_id, stations_by_id,
        )
        for route in sliced_routes
    }
    t_sim1 = time.perf_counter()
    print(
        f"[timing] simulate+fallback for {len(sliced_routes)} routes (warehouse): "
        f"{t_sim1 - t_sim0:.3f}s"
    )
    candidate_routes = sliced_routes
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
        sort_options=(SORT_EARLIEST, SORT_SAFEST),
    )
    departure_datetime = datetime.combine(
        earliest_leg.scheduled_departure.date(), departure_time_of_day
    )
    _sync_display_limit((data_source_label, origin_id, destination_id, departure_datetime, None))

    t_search0 = time.perf_counter()
    candidate_routes = search_routes(data_source_path, origin_id, destination_id, departure_datetime)
    t_search1 = time.perf_counter()
    print(
        f"[timing] find_candidate_routes (json): {t_search1 - t_search0:.3f}s, "
        f"{len(candidate_routes)} candidates"
    )

    total_route_count = len(candidate_routes)
    sliced_routes = candidate_routes[: st.session_state.display_limit]

    search_indexes = get_search_indexes(data_source_path)
    t_sim0 = time.perf_counter()
    results_by_route = {
        route.route_id: simulate_one_route(
            data_source_path, route.route_id, N_ITERATIONS, RNG_SEED,
            route, search_indexes, stations_by_id,
        )
        for route in sliced_routes
    }
    t_sim1 = time.perf_counter()
    print(
        f"[timing] simulate+fallback for {len(sliced_routes)} routes (json): "
        f"{t_sim1 - t_sim0:.3f}s"
    )
    candidate_routes = sliced_routes

if not candidate_routes:
    st.info(
        "No routes with up to 2 transfers match that search. Try an earlier departure time "
        "or a different pair of stations."
    )
    st.stop()

# --- SPEC.md §5.2 Route comparison view -----------------------------------
# candidate_routes only ever holds the loaded (display_limit-sliced) routes --
# sorting, "Earliest scheduled" or "Safest arrival", ranks within what's
# loaded, not the full candidate pool. Simulating every unloaded route just
# to rank it correctly would defeat the point of pagination.
if sort_choice == SORT_EARLIEST:
    candidate_routes = sorted(candidate_routes, key=lambda r: r.scheduled_arrival)
else:
    candidate_routes = sorted(
        candidate_routes, key=lambda r: results_by_route[r.route_id].p85_eta
    )

for route in candidate_routes:
    result = results_by_route[route.route_id]
    render_route_card(route, result, stations_by_id, legs_by_id, transfers_by_id, lines_by_id)

if total_route_count > len(candidate_routes):
    with st.container(key="load_more_row"):
        st.caption(f"Showing {len(candidate_routes)} of {total_route_count} routes")
        st.button(
            "More",
            key="load_more_routes",
            on_click=_load_more_routes,
            width="stretch",
        )
