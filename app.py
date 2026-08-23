"""DB Risk & Rescue — Streamlit dashboard (SPEC.md Section 4)."""

import random
from datetime import datetime
from pathlib import Path

import streamlit as st

from data_loader import MOCK_DATA_PATH, REAL_DATA_PATH, load_dataset
from engine import RouteSimulationResult, index_dataset, simulate_route
from models import MockDataset, Route
from pipelines.route_search import find_candidate_routes
from ui_components import render_header_banner, render_route_card, render_route_timeline

N_ITERATIONS = 1000
RNG_SEED = 42  # fixed seed keeps ETAs stable across Streamlit reruns

st.set_page_config(page_title="DB Risk & Rescue", page_icon="🚆", layout="wide")

# DATA_SPEC.md §6 — sidebar toggle for A/B comparing the Phase 1 mock
# timetable against the Phase 2 GTFS pipeline output during development.
DATA_SOURCES = {
    "Phase 1 — mock_data.json": MOCK_DATA_PATH,
    "Phase 2 — real_dataset.json (GTFS pipeline)": REAL_DATA_PATH,
}
with st.sidebar:
    st.caption("Data source")
    data_source_label = st.radio(
        "Data source", list(DATA_SOURCES), index=0, label_visibility="collapsed"
    )
data_source_path = DATA_SOURCES[data_source_label]

if data_source_path == REAL_DATA_PATH and not REAL_DATA_PATH.exists():
    st.sidebar.warning(
        "data/real_dataset.json not found — run `python -m pipelines.build_dataset` "
        "first. Falling back to mock_data.json for now."
    )
    data_source_path = MOCK_DATA_PATH


@st.cache_data
def get_dataset(path: Path) -> MockDataset:
    return load_dataset(path)


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
        )
        for route in candidate_routes
    }
    return candidate_routes, results


dataset = get_dataset(data_source_path)
legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
stations_by_id = {s.station_id: s for s in dataset.stations}

render_header_banner(N_ITERATIONS)

# --- §4.1 Input flow ---------------------------------------------------------
st.subheader("Plan your trip")

if not dataset.legs:
    st.warning("The selected dataset has no legs to search.")
    st.stop()

station_options = {s.station_id: s.name for s in dataset.stations}
station_ids = list(station_options)
earliest_leg = min(dataset.legs, key=lambda leg: leg.scheduled_departure)
default_origin_id = earliest_leg.origin_station_id
default_destination_id = next(
    (sid for sid in station_ids if sid != default_origin_id), default_origin_id
)

col1, col2, col3 = st.columns(3)
origin_id = col1.selectbox(
    "Origin", options=station_ids, format_func=lambda sid: station_options[sid],
    index=station_ids.index(default_origin_id),
)
destination_id = col2.selectbox(
    "Destination", options=station_ids, format_func=lambda sid: station_options[sid],
    index=station_ids.index(default_destination_id),
)
departure_time_of_day = col3.time_input(
    "Departure at or after", value=earliest_leg.scheduled_departure.time()
)
departure_datetime = datetime.combine(
    earliest_leg.scheduled_departure.date(), departure_time_of_day
)

candidate_routes, results_by_route = search_and_simulate(
    data_source_path, origin_id, destination_id, departure_datetime, N_ITERATIONS, RNG_SEED
)

if not candidate_routes:
    st.info(
        "No direct or single-transfer routes match that search (v1 route search doesn't "
        "chain multiple transfers). Try an earlier departure time or a different pair of stations."
    )
    st.stop()

# --- §4.2 Route comparison view ----------------------------------------------
st.subheader("Candidate routes")

sort_choice = st.radio(
    "Sort by", ["Fastest scheduled", "Safest (lowest P85 risk)"], horizontal=True
)
if sort_choice == "Fastest scheduled":
    candidate_routes = sorted(candidate_routes, key=lambda r: r.scheduled_arrival)
else:
    candidate_routes = sorted(
        candidate_routes, key=lambda r: results_by_route[r.route_id].p85_eta
    )

if (
    "selected_route_id" not in st.session_state
    or st.session_state.selected_route_id not in {r.route_id for r in candidate_routes}
):
    st.session_state.selected_route_id = candidate_routes[0].route_id

for route in candidate_routes:
    result = results_by_route[route.route_id]
    is_selected = route.route_id == st.session_state.selected_route_id
    if render_route_card(route, result, stations_by_id, is_selected=is_selected):
        st.session_state.selected_route_id = route.route_id
        st.rerun()

# --- §4.3 Route detail view ---------------------------------------------------
st.subheader("Route detail")

selected_route = next(
    r for r in candidate_routes if r.route_id == st.session_state.selected_route_id
)
selected_result = results_by_route[selected_route.route_id]

render_route_timeline(
    selected_route, legs_by_id, transfers_by_id, stations_by_id, selected_result.transfer_risks
)
