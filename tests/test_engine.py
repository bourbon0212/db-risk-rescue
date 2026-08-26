"""Tests for the Monte Carlo simulation engine (SPEC.md Section 3)."""

import json
import random
from datetime import datetime
from pathlib import Path

import pytest

from engine import (
    MCT_VIOLATION_MAX_FLOOR,
    SERVICE_FREQUENCY_MINUTES,
    RouteSimulationResult,
    _mct_extra_fail_probability,
    _next_periodic_departure,
    index_dataset,
    mct_violation_floor,
    precompute_fallback_plans,
    simulate_route,
    transfer_impact_minutes,
    transfer_miss_probability,
)
from models import Leg, Line, MockDataset, Route, Station, Transfer
from pipelines.route_search import find_candidate_routes

MOCK_DATA_PATH = Path(__file__).parent.parent / "data" / "mock_data.json"


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


# ---------------------------------------------------------------------------
# SPEC.md §3.4 — dynamic re-routing on miss
# ---------------------------------------------------------------------------


@pytest.fixture
def dynamic_fallback_dataset() -> MockDataset:
    """A -> B -> C, one transfer at B. AB's delay distribution guarantees a
    90-minute delay, so the transfer is missed every iteration. BC_ALT is a
    genuinely different, faster leg from B to C than the route's own BC_ORIG
    continuation -- the alternative precompute_fallback_plans should find
    and simulate_route should adopt."""
    return MockDataset(
        stations=[Station(station_id=s, name=s) for s in ("A", "B", "C")],
        lines=[
            Line(line_id="LN_AB", type="RE", operator="Test"),
            Line(line_id="LN_BC_ORIG", type="RE", operator="Test"),
            Line(line_id="LN_BC_ALT", type="ICE", operator="Test"),
        ],
        legs=[
            Leg(
                leg_id="AB", line_id="LN_AB",
                origin_station_id="A", destination_station_id="B",
                scheduled_departure=datetime(2026, 8, 23, 9, 0),
                scheduled_arrival=datetime(2026, 8, 23, 9, 30),
                delay_distribution_minutes={"90": 1.0},
            ),
            Leg(
                leg_id="BC_ORIG", line_id="LN_BC_ORIG",
                origin_station_id="B", destination_station_id="C",
                scheduled_departure=datetime(2026, 8, 23, 9, 35),
                scheduled_arrival=datetime(2026, 8, 23, 10, 15),
                delay_distribution_minutes={"0": 1.0},
            ),
            Leg(
                leg_id="BC_ALT", line_id="LN_BC_ALT",
                origin_station_id="B", destination_station_id="C",
                scheduled_departure=datetime(2026, 8, 23, 9, 40),
                scheduled_arrival=datetime(2026, 8, 23, 10, 0),
                delay_distribution_minutes={"0": 1.0},
            ),
        ],
        transfers=[
            Transfer(
                transfer_id="T_MISS", station_id="B",
                from_leg_id="AB", to_leg_id="BC_ORIG",
                scheduled_buffer_minutes=5,
            ),
        ],
        routes=[
            Route(
                route_id="RT", legs=["AB", "BC_ORIG"], transfers=["T_MISS"],
                origin_station_id="A", destination_station_id="C",
                scheduled_departure=datetime(2026, 8, 23, 9, 0),
                scheduled_arrival=datetime(2026, 8, 23, 10, 15),
            ),
        ],
    )


def test_precompute_fallback_plans_picks_the_faster_alternative(dynamic_fallback_dataset):
    dataset = dynamic_fallback_dataset
    legs_by_id, transfers_by_id, _ = index_dataset(dataset)
    route = dataset.routes[0]

    plans = precompute_fallback_plans(route, dataset, legs_by_id, transfers_by_id)

    plan = plans["T_MISS"]
    assert plan is not None
    assert [leg.leg_id for leg in plan.legs] == ["BC_ALT"]
    assert plan.transfers == []


def test_precompute_fallback_plans_excludes_the_just_missed_leg_itself(dynamic_fallback_dataset):
    """BC_ORIG (the leg that was just missed) also technically satisfies a
    raw "B -> C departing at/after 09:35" search, since it departs exactly
    at the floor -- it must never be offered back as its own fallback."""
    dataset = dynamic_fallback_dataset
    legs_by_id, transfers_by_id, _ = index_dataset(dataset)
    route = dataset.routes[0]

    plan = precompute_fallback_plans(route, dataset, legs_by_id, transfers_by_id)["T_MISS"]

    assert all(leg.leg_id != "BC_ORIG" for leg in plan.legs)


def test_precompute_fallback_plans_rejects_candidate_below_station_mct(dynamic_fallback_dataset):
    """BC_ALT's entry connection at B is a 10-minute buffer (AB's scheduled
    arrival 9:30 -> BC_ALT's departure 9:40) -- normally the fallback picked
    by test_precompute_fallback_plans_picks_the_faster_alternative above.
    With B's MCT set above that buffer, SPEC.md §3.6's MCT enforcement must
    reject it instead of offering a physically-implausible dash, leaving no
    surviving candidate (BC_ORIG, the only other option, is excluded by the
    just-missed-leg rule regardless)."""
    dataset = dynamic_fallback_dataset
    legs_by_id, transfers_by_id, _ = index_dataset(dataset)
    route = dataset.routes[0]
    stations_by_id = {
        "A": Station(station_id="A", name="A"),
        "B": Station(station_id="B", name="B", mct_minutes=15),
        "C": Station(station_id="C", name="C"),
    }

    plans = precompute_fallback_plans(
        route, dataset, legs_by_id, transfers_by_id, stations_by_id=stations_by_id
    )

    assert plans["T_MISS"] is None


def test_precompute_fallback_plans_keeps_candidate_when_mct_satisfied(dynamic_fallback_dataset):
    """Same fixture, but with B's MCT at or below the 10-minute buffer --
    the candidate must still be picked, proving stations_by_id doesn't
    change behavior when the connection already clears the MCT."""
    dataset = dynamic_fallback_dataset
    legs_by_id, transfers_by_id, _ = index_dataset(dataset)
    route = dataset.routes[0]
    stations_by_id = {s: Station(station_id=s, name=s, mct_minutes=5) for s in ("A", "B", "C")}

    plans = precompute_fallback_plans(
        route, dataset, legs_by_id, transfers_by_id, stations_by_id=stations_by_id
    )

    plan = plans["T_MISS"]
    assert plan is not None
    assert [leg.leg_id for leg in plan.legs] == ["BC_ALT"]


def test_precompute_fallback_plans_no_mct_enforcement_when_station_unknown(dynamic_fallback_dataset):
    """A station_id absent from stations_by_id (e.g. a partial lookup)
    degrades to no enforcement for that transfer, not a crash."""
    dataset = dynamic_fallback_dataset
    legs_by_id, transfers_by_id, _ = index_dataset(dataset)
    route = dataset.routes[0]

    plans = precompute_fallback_plans(
        route, dataset, legs_by_id, transfers_by_id, stations_by_id={}
    )

    assert plans["T_MISS"] is not None


def test_simulate_route_missed_transfer_adopts_dynamic_fallback_eta(dynamic_fallback_dataset):
    dataset = dynamic_fallback_dataset
    legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
    route = dataset.routes[0]
    plans = precompute_fallback_plans(route, dataset, legs_by_id, transfers_by_id)

    result = simulate_route(
        route, legs_by_id, transfers_by_id, lines_by_id,
        n_iterations=5, rng=random.Random(1), fallback_plans=plans,
    )

    # The 90-minute delay on AB guarantees T_MISS is missed every iteration,
    # so every arrival should land on the fallback route's deterministic
    # (0-delay) arrival, not the static same-line-headway wait.
    assert result.transfer_risks[0].simulated_miss_rate == 1.0
    assert all(a == datetime(2026, 8, 23, 10, 0) for a in result.simulated_arrivals)


def test_simulate_route_without_fallback_plans_keeps_static_headway_behavior(dynamic_fallback_dataset):
    """Same guaranteed miss, but omitting fallback_plans (the default)
    must reproduce the original §3.2 Step 4 behavior exactly."""
    dataset = dynamic_fallback_dataset
    legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
    route = dataset.routes[0]

    result = simulate_route(
        route, legs_by_id, transfers_by_id, lines_by_id,
        n_iterations=5, rng=random.Random(1),
    )

    # anchor=09:35, realized_arrival=11:00, headway=60 -> next_departure=11:35;
    # +40min leg duration, +0 fresh delay -> 12:15 (well after the dynamic
    # fallback's 10:00, proving the two code paths genuinely diverge).
    assert all(a == datetime(2026, 8, 23, 12, 15) for a in result.simulated_arrivals)


def test_mock_data_transfer_with_no_alternative_route_falls_back_to_none(dataset):
    """R2/T1 in mock_data.json has no other leg departing DE_MAN_HBF, so its
    only "candidate" is L3 itself -- excluded by the just-missed-leg rule --
    proving precompute degrades to None (and thus static behavior) rather
    than crashing or fabricating a fallback where none really exists."""
    legs_by_id, transfers_by_id, _ = index_dataset(dataset)
    route = next(r for r in dataset.routes if r.route_id == "R2")

    plans = precompute_fallback_plans(route, dataset, legs_by_id, transfers_by_id)

    assert plans["T1"] is None


@pytest.fixture
def reduced_budget_dataset() -> MockDataset:
    """A -> B -> C -> D, two transfers (T_AB_BC at B, T_BC_CD at C). B has a
    1-transfer alternative to D (via Y) that T_AB_BC's remaining budget
    (2 - 1 = 1) allows; C's only 1-transfer alternative to D (via X) exceeds
    T_BC_CD's remaining budget (2 - 2 = 0), which must resolve to None even
    though the same shape of alternative is real and findable in the graph."""
    stations = [Station(station_id=s, name=s) for s in ("A", "B", "C", "D", "X", "Y")]
    lines = [Line(line_id=f"LN_{i}", type="RE", operator="Test") for i in range(7)]
    legs = [
        Leg(
            leg_id="AB", line_id="LN_0",
            origin_station_id="A", destination_station_id="B",
            scheduled_departure=datetime(2026, 8, 23, 9, 0),
            scheduled_arrival=datetime(2026, 8, 23, 9, 30),
            delay_distribution_minutes={"90": 1.0},
        ),
        Leg(
            leg_id="BC", line_id="LN_1",
            origin_station_id="B", destination_station_id="C",
            scheduled_departure=datetime(2026, 8, 23, 9, 35),
            scheduled_arrival=datetime(2026, 8, 23, 10, 0),
            delay_distribution_minutes={"0": 1.0},
        ),
        Leg(
            leg_id="CD_ORIG", line_id="LN_2",
            origin_station_id="C", destination_station_id="D",
            scheduled_departure=datetime(2026, 8, 23, 10, 5),
            scheduled_arrival=datetime(2026, 8, 23, 12, 0),
            delay_distribution_minutes={"0": 1.0},
        ),
        Leg(
            leg_id="BY", line_id="LN_3",
            origin_station_id="B", destination_station_id="Y",
            scheduled_departure=datetime(2026, 8, 23, 9, 40),
            scheduled_arrival=datetime(2026, 8, 23, 9, 50),
            delay_distribution_minutes={"0": 1.0},
        ),
        Leg(
            leg_id="YD", line_id="LN_4",
            origin_station_id="Y", destination_station_id="D",
            scheduled_departure=datetime(2026, 8, 23, 9, 55),
            scheduled_arrival=datetime(2026, 8, 23, 10, 10),
            delay_distribution_minutes={"0": 1.0},
        ),
        Leg(
            leg_id="CX", line_id="LN_5",
            origin_station_id="C", destination_station_id="X",
            scheduled_departure=datetime(2026, 8, 23, 10, 10),
            scheduled_arrival=datetime(2026, 8, 23, 10, 20),
            delay_distribution_minutes={"0": 1.0},
        ),
        Leg(
            leg_id="XD", line_id="LN_6",
            origin_station_id="X", destination_station_id="D",
            scheduled_departure=datetime(2026, 8, 23, 10, 25),
            scheduled_arrival=datetime(2026, 8, 23, 10, 40),
            delay_distribution_minutes={"0": 1.0},
        ),
    ]
    transfers = [
        Transfer(transfer_id="T_AB_BC", station_id="B", from_leg_id="AB", to_leg_id="BC", scheduled_buffer_minutes=5),
        Transfer(transfer_id="T_BC_CD", station_id="C", from_leg_id="BC", to_leg_id="CD_ORIG", scheduled_buffer_minutes=5),
        Transfer(transfer_id="T_BY_YD", station_id="Y", from_leg_id="BY", to_leg_id="YD", scheduled_buffer_minutes=5),
        Transfer(transfer_id="T_CX_XD", station_id="X", from_leg_id="CX", to_leg_id="XD", scheduled_buffer_minutes=5),
    ]
    route = Route(
        route_id="RT2", legs=["AB", "BC", "CD_ORIG"], transfers=["T_AB_BC", "T_BC_CD"],
        origin_station_id="A", destination_station_id="D",
        scheduled_departure=datetime(2026, 8, 23, 9, 0),
        scheduled_arrival=datetime(2026, 8, 23, 12, 0),
    )
    return MockDataset(stations=stations, lines=lines, legs=legs, transfers=transfers, routes=[route])


def test_precompute_fallback_plans_respects_reduced_transfer_budget(reduced_budget_dataset):
    dataset = reduced_budget_dataset
    legs_by_id, transfers_by_id, _ = index_dataset(dataset)
    route = dataset.routes[0]

    # Sanity check: both 1-transfer alternatives are real, findable routes
    # in the raw graph search -- their absence from the precomputed plans
    # below is due to the budget rule, not because they don't exist.
    from_b = find_candidate_routes(dataset, "B", "D", legs_by_id["BC"].scheduled_departure)
    from_c = find_candidate_routes(dataset, "C", "D", legs_by_id["CD_ORIG"].scheduled_departure)
    assert any(r.legs == ["BY", "YD"] for r in from_b)
    assert any(r.legs == ["CX", "XD"] for r in from_c)

    plans = precompute_fallback_plans(route, dataset, legs_by_id, transfers_by_id)

    # Index 0 (T_AB_BC): remaining budget = 2 - 1 = 1 -- the 1-transfer
    # B -> Y -> D alternative is within budget and gets adopted.
    plan_0 = plans["T_AB_BC"]
    assert plan_0 is not None
    assert [leg.leg_id for leg in plan_0.legs] == ["BY", "YD"]

    # Index 1 (T_BC_CD): remaining budget = 2 - 2 = 0 -- the 1-transfer
    # C -> X -> D alternative exceeds the budget and must be excluded, and
    # the only direct candidate is the just-missed leg itself, so no
    # fallback survives.
    assert plans["T_BC_CD"] is None


# ---------------------------------------------------------------------------
# SPEC.md §3.4 / §5.3 — transfer_impact_minutes (Impact-Weighted UI Thresholds)
# ---------------------------------------------------------------------------


def test_transfer_impact_minutes_uses_fallback_plan_when_available(dynamic_fallback_dataset):
    dataset = dynamic_fallback_dataset
    legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
    route = dataset.routes[0]
    plans = precompute_fallback_plans(route, dataset, legs_by_id, transfers_by_id)

    transfer = transfers_by_id["T_MISS"]
    downstream_leg = legs_by_id["BC_ORIG"]
    impact = transfer_impact_minutes(transfer, downstream_leg, route, lines_by_id, plans)

    # Fallback (BC_ALT) arrives 10:00; the route's own scheduled arrival is
    # 10:15 -- the fallback beats the original schedule by 15 minutes, so the
    # impact is negative (the "some fallback plans even arrive earlier" case
    # from the threshold analysis).
    assert impact == pytest.approx(-15.0)


def test_transfer_impact_minutes_falls_back_to_headway_when_no_plan(dataset):
    """R2/T1 in mock_data.json has no surviving fallback candidate (see
    test_mock_data_transfer_with_no_alternative_route_falls_back_to_none) --
    the impact must fall back to L3's line-type headway (RE -> 60min)."""
    legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
    route = next(r for r in dataset.routes if r.route_id == "R2")
    plans = precompute_fallback_plans(route, dataset, legs_by_id, transfers_by_id)
    assert plans["T1"] is None

    transfer = transfers_by_id["T1"]
    downstream_leg = legs_by_id["L3"]
    impact = transfer_impact_minutes(transfer, downstream_leg, route, lines_by_id, plans)

    assert impact == pytest.approx(60.0)


def test_transfer_impact_minutes_without_fallback_plans_arg_uses_headway(dataset):
    """Passing fallback_plans=None outright (not just an all-None dict) must
    behave identically -- no crash on a missing .get()."""
    legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
    route = next(r for r in dataset.routes if r.route_id == "R2")
    transfer = transfers_by_id["T1"]
    downstream_leg = legs_by_id["L3"]

    impact = transfer_impact_minutes(transfer, downstream_leg, route, lines_by_id, None)

    assert impact == pytest.approx(float(SERVICE_FREQUENCY_MINUTES["RE"]))


# ---------------------------------------------------------------------------
# SPEC.md §5.2 / §5.3 — RouteSimulationResult.p85_penalty_minutes and
# TransferRisk.impact_minutes, as exposed by simulate_route
# ---------------------------------------------------------------------------


def test_simulate_route_exposes_p85_penalty_minutes(dataset, routes_by_id):
    legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
    route = routes_by_id["R1"]

    result = simulate_route(
        route, legs_by_id, transfers_by_id, lines_by_id,
        n_iterations=500, rng=random.Random(3),
    )

    expected = (result.p85_eta - route.scheduled_arrival).total_seconds() / 60.0
    assert result.p85_penalty_minutes == pytest.approx(expected)


# ---------------------------------------------------------------------------
# SPEC.md §3.6.2 — MCT gradient floor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "buffer_minutes,mct_minutes,expected",
    [
        (10, 10, 0.0),   # at MCT -- no floor
        (11, 10, 0.0),   # above MCT -- no floor
        (9, 10, 0.095),  # 1 minute short -- barely nudges
        (5, 10, 0.475),  # halfway short -- half the max floor
        (0, 10, MCT_VIOLATION_MAX_FLOOR),  # 0-minute "connection" -- full floor
        (-5, 10, MCT_VIOLATION_MAX_FLOOR),  # negative buffer clamps to the same max, no higher
    ],
)
def test_mct_violation_floor_scales_linearly_with_deficit(buffer_minutes, mct_minutes, expected):
    assert mct_violation_floor(buffer_minutes, mct_minutes) == pytest.approx(expected)


def test_mct_violation_floor_never_exceeds_max_floor():
    assert mct_violation_floor(-1000, 10) == pytest.approx(MCT_VIOLATION_MAX_FLOOR)


@pytest.mark.parametrize(
    "analytic,floor,expected",
    [
        (0.5, 0.3, 0.0),    # floor doesn't bind -- analytic already exceeds it
        (1.0, 0.9, 0.0),    # already-certain miss -- nothing left to force
        (0.2, 0.8, 0.75),   # floor binds -- (0.8-0.2)/(1-0.2)
        (0.0, 0.95, 0.95),  # no natural risk at all -- extra-fail rate equals the floor itself
    ],
)
def test_mct_extra_fail_probability(analytic, floor, expected):
    assert _mct_extra_fail_probability(analytic, floor) == pytest.approx(expected)


@pytest.fixture
def zero_buffer_mct_fixture():
    """A -> B -> C, one transfer at B with a 0-minute scheduled buffer and a
    delay distribution that's *never* late -- analytic miss probability is
    exactly 0, isolating the MCT floor's own contribution from any
    statistical delay risk."""
    leg_ab = Leg(
        leg_id="AB", line_id="LN", origin_station_id="A", destination_station_id="B",
        scheduled_departure=datetime(2026, 8, 23, 9, 0), scheduled_arrival=datetime(2026, 8, 23, 9, 30),
        delay_distribution_minutes={"0": 1.0},
    )
    leg_bc = Leg(
        leg_id="BC", line_id="LN", origin_station_id="B", destination_station_id="C",
        scheduled_departure=datetime(2026, 8, 23, 9, 30), scheduled_arrival=datetime(2026, 8, 23, 10, 0),
        delay_distribution_minutes={"0": 1.0},
    )
    legs_by_id = {"AB": leg_ab, "BC": leg_bc}
    transfer = Transfer(
        transfer_id="T1", station_id="B", from_leg_id="AB", to_leg_id="BC", scheduled_buffer_minutes=0,
    )
    transfers_by_id = {"T1": transfer}
    lines_by_id = {"LN": Line(line_id="LN", type="RE", operator="Test")}
    route = Route(
        route_id="RT", legs=["AB", "BC"], transfers=["T1"],
        origin_station_id="A", destination_station_id="C",
        scheduled_departure=datetime(2026, 8, 23, 9, 0), scheduled_arrival=datetime(2026, 8, 23, 10, 0),
    )
    return route, legs_by_id, transfers_by_id, lines_by_id


def test_simulate_route_applies_mct_floor_when_stations_by_id_given(zero_buffer_mct_fixture):
    """Station B is a major hub (mct_minutes=10) but the transfer's buffer
    is 0 -- deficit_fraction=1.0, so the floor is the full
    MCT_VIOLATION_MAX_FLOOR. Since analytic risk is 0 here, effective
    miss_probability must equal the floor exactly, below_mct must be True,
    and enough iterations must show the simulated rate converging to it --
    proving the Monte Carlo loop, not just the displayed number, respects it."""
    route, legs_by_id, transfers_by_id, lines_by_id = zero_buffer_mct_fixture
    stations_by_id = {"B": Station(station_id="B", name="B", mct_minutes=10)}

    result = simulate_route(
        route, legs_by_id, transfers_by_id, lines_by_id,
        n_iterations=3000, rng=random.Random(42), stations_by_id=stations_by_id,
    )

    risk = result.transfer_risks[0]
    assert risk.below_mct is True
    assert risk.miss_probability == pytest.approx(MCT_VIOLATION_MAX_FLOOR)
    assert risk.simulated_miss_rate == pytest.approx(MCT_VIOLATION_MAX_FLOOR, abs=0.03)


def test_simulate_route_without_stations_by_id_ignores_mct_entirely(zero_buffer_mct_fixture):
    """Omitting stations_by_id (the default) must be a strict no-op: same
    0-minute buffer, but with no station data the analytic miss probability
    (0.0, this leg is never late) is what's reported and simulated --
    proving every pre-existing (no stations_by_id) caller is unaffected."""
    route, legs_by_id, transfers_by_id, lines_by_id = zero_buffer_mct_fixture

    result = simulate_route(
        route, legs_by_id, transfers_by_id, lines_by_id,
        n_iterations=200, rng=random.Random(42),
    )

    risk = result.transfer_risks[0]
    assert risk.below_mct is False
    assert risk.miss_probability == pytest.approx(0.0)
    assert risk.simulated_miss_rate == pytest.approx(0.0)


def test_simulate_route_mct_floor_is_reproducible_with_seeded_rng(zero_buffer_mct_fixture):
    route, legs_by_id, transfers_by_id, lines_by_id = zero_buffer_mct_fixture
    stations_by_id = {"B": Station(station_id="B", name="B", mct_minutes=10)}

    result_a = simulate_route(
        route, legs_by_id, transfers_by_id, lines_by_id,
        n_iterations=200, rng=random.Random(123), stations_by_id=stations_by_id,
    )
    result_b = simulate_route(
        route, legs_by_id, transfers_by_id, lines_by_id,
        n_iterations=200, rng=random.Random(123), stations_by_id=stations_by_id,
    )
    assert result_a.simulated_arrivals == result_b.simulated_arrivals


def test_simulate_route_transfer_risk_exposes_impact_minutes(dynamic_fallback_dataset):
    dataset = dynamic_fallback_dataset
    legs_by_id, transfers_by_id, lines_by_id = index_dataset(dataset)
    route = dataset.routes[0]
    plans = precompute_fallback_plans(route, dataset, legs_by_id, transfers_by_id)

    result = simulate_route(
        route, legs_by_id, transfers_by_id, lines_by_id,
        n_iterations=5, rng=random.Random(1), fallback_plans=plans,
    )

    assert result.transfer_risks[0].impact_minutes == pytest.approx(-15.0)
