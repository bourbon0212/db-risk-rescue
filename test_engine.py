"""Tests for the Monte Carlo simulation engine (SPEC.md Section 3)."""

import json
import random
from datetime import datetime
from pathlib import Path

import pytest

from engine import (
    RouteSimulationResult,
    _next_periodic_departure,
    index_dataset,
    simulate_route,
    transfer_miss_probability,
)
from models import Leg, MockDataset, Transfer

MOCK_DATA_PATH = Path(__file__).parent / "mock_data.json"


@pytest.fixture
def dataset() -> MockDataset:
    return MockDataset.model_validate(json.loads(MOCK_DATA_PATH.read_text()))


@pytest.fixture
def routes_by_id(dataset) -> dict:
    return {r.route_id: r for r in dataset.routes}


# ---------------------------------------------------------------------------
# §3.1 — analytic per-transfer miss probability
# ---------------------------------------------------------------------------


def test_transfer_miss_probability_matches_spec_worked_example():
    leg = Leg(
        leg_id="L1",
        line_id="ICE_15",
        origin_station_id="DE_FRA_HBF",
        destination_station_id="DE_KOL_HBF",
        scheduled_departure="2026-08-23T09:02:00",
        scheduled_arrival="2026-08-23T10:14:00",
        delay_distribution_minutes={"0": 0.60, "5": 0.20, "15": 0.12, "30": 0.06, "60": 0.02},
    )
    transfer = Transfer(
        transfer_id="T1", station_id="DE_KOL_HBF", from_leg_id="L1", to_leg_id="L2",
        scheduled_buffer_minutes=12,
    )
    assert transfer_miss_probability(leg, transfer) == pytest.approx(0.20)


def test_transfer_miss_probability_for_risky_mock_transfer(dataset):
    legs_by_id, transfers_by_id, _ = index_dataset(dataset)
    leg_l2 = legs_by_id["L2"]
    transfer_t1 = transfers_by_id["T1"]
    # buffer=8; buckets > 8 are 15 (.30), 30 (.20), 60 (.15) => 0.65
    assert transfer_miss_probability(leg_l2, transfer_t1) == pytest.approx(0.65)


# ---------------------------------------------------------------------------
# §3.2 Step 4 — next periodic departure formula
# ---------------------------------------------------------------------------


def test_next_periodic_departure_rounds_up_to_next_headway_slot():
    scheduled_departure = datetime(2026, 8, 23, 9, 0, 0)
    # Realized arrival is 65 minutes after the anchor departure, headway 60min
    # => must wait for the *second* slot at +120min, not the first at +60min.
    realized_arrival = datetime(2026, 8, 23, 10, 5, 0)
    result = _next_periodic_departure(scheduled_departure, realized_arrival, 60)
    assert result == datetime(2026, 8, 23, 11, 0, 0)


def test_next_periodic_departure_exact_multiple_of_headway():
    scheduled_departure = datetime(2026, 8, 23, 9, 0, 0)
    realized_arrival = datetime(2026, 8, 23, 10, 0, 0)  # exactly +60min
    result = _next_periodic_departure(scheduled_departure, realized_arrival, 60)
    assert result == datetime(2026, 8, 23, 10, 0, 0)


# ---------------------------------------------------------------------------
# §3.2 / §3.3 — full Monte Carlo simulation
# ---------------------------------------------------------------------------


def test_simulate_route_single_leg_no_transfers(dataset, routes_by_id):
    legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
    route = routes_by_id["R1"]

    result = simulate_route(
        route, legs_by_id, transfers_by_id, lines_by_id,
        n_iterations=1000, rng=random.Random(42),
    )

    assert isinstance(result, RouteSimulationResult)
    assert result.transfer_risks == []
    assert len(result.simulated_arrivals) == 1000


def test_simulate_route_single_leg_eta_ordering_and_bounds(dataset, routes_by_id):
    from datetime import timedelta

    legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
    route = routes_by_id["R1"]
    leg = legs_by_id["L1"]

    result = simulate_route(
        route, legs_by_id, transfers_by_id, lines_by_id,
        n_iterations=1000, rng=random.Random(42),
    )

    assert leg.scheduled_arrival <= result.mean_eta
    assert result.mean_eta <= result.p85_eta <= result.p90_eta
    assert result.p90_eta <= leg.scheduled_arrival + timedelta(minutes=60)
    # Expected delay for L1's distribution: 0*.6+5*.2+15*.12+30*.06+60*.02 = 5.8min
    expected_mean = leg.scheduled_arrival + timedelta(minutes=5.8)
    assert abs((result.mean_eta - expected_mean).total_seconds()) < 90  # within 1.5min


def test_simulate_route_risky_transfer_produces_delays(dataset, routes_by_id):
    from datetime import timedelta

    legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
    route = routes_by_id["R2"]
    downstream_leg = legs_by_id["L3"]

    result = simulate_route(
        route, legs_by_id, transfers_by_id, lines_by_id,
        n_iterations=1000, rng=random.Random(7),
    )

    assert len(result.transfer_risks) == 1
    risk = result.transfer_risks[0]
    assert risk.transfer_id == "T1"
    assert risk.miss_probability == pytest.approx(0.65)
    # Simulated miss rate should converge near the analytic value.
    assert risk.simulated_miss_rate == pytest.approx(0.65, abs=0.05)

    assert result.mean_eta <= result.p85_eta <= result.p90_eta
    # A route with a 65%-chance-of-missed-connection should land well after
    # the naive scheduled arrival on average.
    assert result.mean_eta > downstream_leg.scheduled_arrival + timedelta(minutes=5)


def test_simulate_route_raises_on_leg_transfer_count_mismatch(dataset):
    legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
    from models import Route

    bad_route = Route(
        route_id="BAD",
        legs=["L2", "L3"],
        transfers=[],  # missing the required transfer
        origin_station_id="DE_STG_HBF",
        destination_station_id="DE_HEI_HBF",
        scheduled_departure="2026-08-23T09:10:00",
        scheduled_arrival="2026-08-23T10:26:00",
    )
    with pytest.raises(ValueError):
        simulate_route(bad_route, legs_by_id, transfers_by_id, lines_by_id, n_iterations=10)


def test_simulate_route_is_reproducible_with_seeded_rng(dataset, routes_by_id):
    legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
    route = routes_by_id["R2"]

    result_a = simulate_route(
        route, legs_by_id, transfers_by_id, lines_by_id,
        n_iterations=200, rng=random.Random(123),
    )
    result_b = simulate_route(
        route, legs_by_id, transfers_by_id, lines_by_id,
        n_iterations=200, rng=random.Random(123),
    )
    assert result_a.simulated_arrivals == result_b.simulated_arrivals
    assert result_a.mean_eta == result_b.mean_eta
