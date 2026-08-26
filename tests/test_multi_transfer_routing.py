"""End-to-end test for the 2-transfer routing extension: route_search.py
finds a 3-leg/2-transfer Route, and engine.py's simulate_route() (verified
here, not just read) correctly chains delays/buffers/miss-probabilities
across both transfer points.

Uses a fully deterministic synthetic scenario (single-bucket delay
distributions, no randomness in outcome) specifically so the expected
result can be asserted exactly -- a much stronger check than "it doesn't
crash" for something as index-sensitive as chaining N legs/transfers.

Scenario: A -[always on-time]-> B -[always 60min late]-> C -[always on-time]-> D
  - Transfer 1 (A-B leg -> B-C leg), 10-minute buffer: leg 1 is always on
    time, so this transfer always HOLDS.
  - Transfer 2 (B-C leg -> C-D leg), 10-minute buffer: leg 2 is always 60
    minutes late, so this transfer always MISSES, forcing the "next
    periodic departure" resolution path (§3.2 Step 4) on the SECOND
    transfer specifically -- the case most likely to break if simulate_route
    had any hidden 1-transfer assumption.
"""

from datetime import datetime

import pytest

from engine import index_dataset, simulate_route
from models import Leg, Line, MockDataset, Station, Transfer
from pipelines.route_search import find_candidate_routes

EARLY = datetime(2026, 8, 23, 0, 0, 0)


@pytest.fixture
def chain_dataset() -> MockDataset:
    return MockDataset(
        stations=[Station(station_id=sid, name=sid) for sid in "ABCD"],
        lines=[
            Line(line_id="L_AB", type="RE", operator="Test"),
            Line(line_id="L_BC", type="RE", operator="Test"),
            Line(line_id="L_CD", type="RE", operator="Test"),
        ],
        legs=[
            Leg(
                leg_id="LEG_AB",
                line_id="L_AB",
                origin_station_id="A",
                destination_station_id="B",
                scheduled_departure=datetime(2026, 8, 23, 9, 0),
                scheduled_arrival=datetime(2026, 8, 23, 10, 0),
                delay_distribution_minutes={"0": 1.0},  # always on time
            ),
            Leg(
                leg_id="LEG_BC",
                line_id="L_BC",
                origin_station_id="B",
                destination_station_id="C",
                scheduled_departure=datetime(2026, 8, 23, 10, 10),
                scheduled_arrival=datetime(2026, 8, 23, 11, 0),
                delay_distribution_minutes={"60": 1.0},  # always 60 min late
            ),
            Leg(
                leg_id="LEG_CD",
                line_id="L_CD",
                origin_station_id="C",
                destination_station_id="D",
                scheduled_departure=datetime(2026, 8, 23, 11, 10),
                scheduled_arrival=datetime(2026, 8, 23, 12, 0),  # 50-minute leg duration
                delay_distribution_minutes={"0": 1.0},  # always on time
            ),
        ],
        transfers=[
            Transfer(
                transfer_id="TR_AB_BC",
                station_id="B",
                from_leg_id="LEG_AB",
                to_leg_id="LEG_BC",
                scheduled_buffer_minutes=10,
            ),
            Transfer(
                transfer_id="TR_BC_CD",
                station_id="C",
                from_leg_id="LEG_BC",
                to_leg_id="LEG_CD",
                scheduled_buffer_minutes=10,
            ),
        ],
        routes=[],
    )


def test_route_search_finds_the_two_transfer_chain(chain_dataset):
    routes = find_candidate_routes(chain_dataset, "A", "D", EARLY)
    assert len(routes) == 1
    route = routes[0]
    assert route.legs == ["LEG_AB", "LEG_BC", "LEG_CD"]
    assert route.transfers == ["TR_AB_BC", "TR_BC_CD"]


def test_simulate_route_runs_without_error_on_a_two_transfer_route(chain_dataset):
    route = find_candidate_routes(chain_dataset, "A", "D", EARLY)[0]
    legs_by_id, transfers_by_id, lines_by_id = index_dataset(chain_dataset)

    result = simulate_route(route, legs_by_id, transfers_by_id, lines_by_id, n_iterations=200)

    assert len(result.transfer_risks) == 2
    assert len(result.simulated_arrivals) == 200


def test_first_transfer_always_holds_second_always_misses(chain_dataset):
    """The core "does chaining work across two transfer points" check: the
    first transfer must resolve independently of, and correctly before,
    the second -- not share state, not be skipped, not be evaluated out of
    order."""
    route = find_candidate_routes(chain_dataset, "A", "D", EARLY)[0]
    legs_by_id, transfers_by_id, lines_by_id = index_dataset(chain_dataset)

    result = simulate_route(route, legs_by_id, transfers_by_id, lines_by_id, n_iterations=200)

    transfer_1, transfer_2 = result.transfer_risks
    assert transfer_1.transfer_id == "TR_AB_BC"
    assert transfer_2.transfer_id == "TR_BC_CD"

    # Analytic (CDF-based) and simulated miss rates must agree, since both
    # legs' distributions are single-bucket (no sampling variance possible).
    assert transfer_1.miss_probability == 0.0
    assert transfer_1.simulated_miss_rate == 0.0
    assert transfer_2.miss_probability == 1.0
    assert transfer_2.simulated_miss_rate == 1.0


def test_final_arrival_correctly_propagates_through_both_transfers(chain_dataset):
    """Fully deterministic scenario (see module docstring) -> every
    simulated iteration must land on the exact same expected arrival:
    leg 2's realized arrival (11:00 + 60min = 12:00) misses transfer 2's
    10:10-buffered connection at leg 3 (scheduled departure 11:10), so it
    resolves via the next periodic RE departure (60-min headway) ->
    12:10, plus leg 3's 50-minute duration and its own 0-minute delay
    -> 13:00.
    """
    route = find_candidate_routes(chain_dataset, "A", "D", EARLY)[0]
    legs_by_id, transfers_by_id, lines_by_id = index_dataset(chain_dataset)

    result = simulate_route(route, legs_by_id, transfers_by_id, lines_by_id, n_iterations=50)

    expected = datetime(2026, 8, 23, 13, 0)
    assert all(arrival == expected for arrival in result.simulated_arrivals)
    assert result.mean_eta == expected
    assert result.p85_eta == expected
    assert result.p90_eta == expected
