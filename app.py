"""DB Risk & Rescue — Streamlit dashboard (SPEC.md Section 4)."""

import random

import streamlit as st

from data_loader import load_dataset
from engine import RouteSimulationResult, index_dataset, simulate_route
from ui_components import render_route_card, render_route_timeline

N_ITERATIONS = 1000
RNG_SEED = 42  # fixed seed keeps ETAs stable across Streamlit reruns

st.set_page_config(page_title="DB Risk & Rescue", page_icon="🚆", layout="wide")


@st.cache_data
def get_dataset():
    return load_dataset()


@st.cache_data(show_spinner="Running Monte Carlo simulation...")
def simulate_all_routes(n_iterations: int, seed: int) -> dict[str, RouteSimulationResult]:
    dataset = get_dataset()
    legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
    return {
        route.route_id: simulate_route(
            route, legs_by_id, transfers_by_id, lines_by_id,
            n_iterations=n_iterations, rng=random.Random(seed),
        )
        for route in dataset.routes
    }


dataset = get_dataset()
legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
stations_by_id = {s.station_id: s for s in dataset.stations}
results_by_route = simulate_all_routes(N_ITERATIONS, RNG_SEED)

st.title("🚆 DB Risk & Rescue")
st.caption(
    "Probability-aware trip planning — a True Expected Time of Arrival, "
    f"computed via {N_ITERATIONS:,}-iteration Monte Carlo simulation over historical delay behavior."
)

# --- §4.1 Input flow ---------------------------------------------------------
st.subheader("Plan your trip")

station_options = {s.station_id: s.name for s in dataset.stations}
station_ids = list(station_options)
default_route = dataset.routes[0]

col1, col2, col3 = st.columns(3)
origin_id = col1.selectbox(
    "Origin", options=station_ids, format_func=lambda sid: station_options[sid],
    index=station_ids.index(default_route.origin_station_id),
)
destination_id = col2.selectbox(
    "Destination", options=station_ids, format_func=lambda sid: station_options[sid],
    index=station_ids.index(default_route.destination_station_id),
)
departure_time = col3.time_input(
    "Departure at or after", value=default_route.scheduled_departure.time()
)

candidate_routes = [
    r
    for r in dataset.routes
    if r.origin_station_id == origin_id
    and r.destination_station_id == destination_id
    and r.scheduled_departure.time() >= departure_time
]

if not candidate_routes:
    st.info(
        "No candidate routes match that search in the mock timetable "
        "(v1 mock data only contains a handful of fixed legs). Showing all available routes instead."
    )
    candidate_routes = dataset.routes

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
