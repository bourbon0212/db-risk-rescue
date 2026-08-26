"""Tests for routing/route_search.py (DATA_SPEC.md §5, §8 build sequence
step 6, extended to 2 transfers): direct legs, single- and two-transfer
journeys, the departure-time cutoff, and the still-excluded 3+-transfer case.
"""

from datetime import datetime

import pytest

from data_loader import load_dataset
from models import Leg, MockDataset, Station, Transfer
from routing.route_search import find_candidate_routes

EARLY = datetime(2026, 8, 23, 0, 0, 0)


@pytest.fixture
def mock_dataset() -> MockDataset:
    return load_dataset()


@pytest.fixture
def synthetic_dataset() -> MockDataset:
    """A -> B -> C with both a direct A->C leg and a single-transfer A->B->C
    path, so both branches of find_candidate_routes can be exercised together."""
    return MockDataset(
        stations=[
            Station(station_id="A", name="Station A"),
            Station(station_id="B", name="Station B"),
            Station(station_id="C", name="Station C"),
        ],
        lines=[],
        legs=[
            Leg(
                leg_id="L_DIRECT",
                line_id="X",
                origin_station_id="A",
                destination_station_id="C",
                scheduled_departure=datetime(2026, 8, 23, 10, 0),
                scheduled_arrival=datetime(2026, 8, 23, 11, 0),
                delay_distribution_minutes={"0": 1.0},
            ),
            Leg(
                leg_id="L_A_B",
                line_id="Y",
                origin_station_id="A",
                destination_station_id="B",
                scheduled_departure=datetime(2026, 8, 23, 9, 0),
                scheduled_arrival=datetime(2026, 8, 23, 9, 30),
                delay_distribution_minutes={"0": 1.0},
            ),
            Leg(
                leg_id="L_B_C",
                line_id="Z",
                origin_station_id="B",
                destination_station_id="C",
                scheduled_departure=datetime(2026, 8, 23, 9, 40),
                scheduled_arrival=datetime(2026, 8, 23, 10, 30),
                delay_distribution_minutes={"0": 1.0},
            ),
        ],
        transfers=[
            Transfer(
                transfer_id="TR1",
                station_id="B",
                from_leg_id="L_A_B",
                to_leg_id="L_B_C",
                scheduled_buffer_minutes=10,
            )
        ],
        routes=[],
    )


# --- direct legs --------------------------------------------------------------


def test_finds_direct_route(mock_dataset):
    routes = find_candidate_routes(mock_dataset, "DE_FRA_HBF", "DE_KOL_HBF", EARLY)
    assert len(routes) == 1
    route = routes[0]
    assert route.legs == ["L1"]
    assert route.transfers == []
    assert route.origin_station_id == "DE_FRA_HBF"
    assert route.destination_station_id == "DE_KOL_HBF"
    assert route.scheduled_departure == datetime(2026, 8, 23, 9, 2)
    assert route.scheduled_arrival == datetime(2026, 8, 23, 10, 14)


def test_no_match_returns_empty_list(mock_dataset):
    routes = find_candidate_routes(mock_dataset, "DE_STG_HBF", "DE_BER_HBF", EARLY)
    assert routes == []


def test_same_origin_and_destination_returns_empty_list(mock_dataset):
    """A same-station "round trip" (e.g. Frankfurt -> ... -> Frankfurt via a
    transfer) shouldn't be surfaced as a candidate route."""
    routes = find_candidate_routes(mock_dataset, "DE_FRA_HBF", "DE_FRA_HBF", EARLY)
    assert routes == []


# --- departure-time cutoff ------------------------------------------------------


def test_departure_time_cutoff_is_inclusive(mock_dataset):
    """L1 departs exactly 09:02:00 -- a cutoff of exactly that time must still match."""
    routes = find_candidate_routes(
        mock_dataset, "DE_FRA_HBF", "DE_KOL_HBF", datetime(2026, 8, 23, 9, 2, 0)
    )
    assert len(routes) == 1


def test_departure_time_cutoff_excludes_earlier_legs(mock_dataset):
    routes = find_candidate_routes(
        mock_dataset, "DE_FRA_HBF", "DE_KOL_HBF", datetime(2026, 8, 23, 9, 3, 0)
    )
    assert routes == []


# --- single-transfer journeys --------------------------------------------------


def test_finds_single_transfer_route(mock_dataset):
    routes = find_candidate_routes(mock_dataset, "DE_MUC_HBF", "DE_MUC_OST", EARLY)
    assert len(routes) == 1
    route = routes[0]
    assert route.legs == ["L7", "L8"]
    assert route.transfers == ["T4"]
    assert route.origin_station_id == "DE_MUC_HBF"
    assert route.destination_station_id == "DE_MUC_OST"
    assert route.scheduled_departure == datetime(2026, 8, 23, 8, 15)
    assert route.scheduled_arrival == datetime(2026, 8, 23, 8, 29)


def test_single_transfer_route_id_references_the_transfer(mock_dataset):
    routes = find_candidate_routes(mock_dataset, "DE_MUC_HBF", "DE_MUC_OST", EARLY)
    assert routes[0].route_id == "RS_XFER1_T4"


def test_transfer_departure_cutoff_uses_first_legs_departure(mock_dataset):
    """L7 (the first leg) departs 08:15; a cutoff just after that excludes the pair."""
    routes = find_candidate_routes(
        mock_dataset, "DE_MUC_HBF", "DE_MUC_OST", datetime(2026, 8, 23, 8, 16)
    )
    assert routes == []


# --- two-transfer journeys -------------------------------------------------


def test_finds_two_transfer_route(mock_dataset):
    """Munich Hbf -> Berlin Hbf connects via a 2-transfer chain
    (L4 -> T2 -> L5 -> T3 -> L6) in mock_data.json -- this is exactly the
    "one extra hop" case the 2-transfer extension was added to unlock."""
    routes = find_candidate_routes(mock_dataset, "DE_MUC_HBF", "DE_BER_HBF", EARLY)
    assert len(routes) == 1
    route = routes[0]
    assert route.legs == ["L4", "L5", "L6"]
    assert route.transfers == ["T2", "T3"]
    assert route.route_id == "RS_XFER2_T2_T3"
    assert route.origin_station_id == "DE_MUC_HBF"
    assert route.destination_station_id == "DE_BER_HBF"
    assert route.scheduled_departure == datetime(2026, 8, 23, 7, 5)
    assert route.scheduled_arrival == datetime(2026, 8, 23, 12, 25)


def test_two_transfer_departure_cutoff_uses_first_legs_departure(mock_dataset):
    """L4 (the first leg) departs 07:05; a cutoff just after that excludes
    the whole 3-leg chain."""
    routes = find_candidate_routes(
        mock_dataset, "DE_MUC_HBF", "DE_BER_HBF", datetime(2026, 8, 23, 7, 6)
    )
    assert routes == []


# --- 3+ transfers is still explicitly out of scope --------------------------


@pytest.fixture
def four_leg_chain_dataset() -> MockDataset:
    """A -> B -> C -> D -> E, needing 3 transfers end to end -- long enough
    to prove the 2-transfer cap actually holds instead of silently chaining
    forever."""
    stations = [Station(station_id=sid, name=sid) for sid in "ABCDE"]
    pairs = [("A", "B"), ("B", "C"), ("C", "D"), ("D", "E")]
    legs = [
        Leg(
            leg_id=f"L_{a}_{b}",
            line_id=f"LINE_{a}{b}",
            origin_station_id=a,
            destination_station_id=b,
            scheduled_departure=datetime(2026, 8, 23, 9 + i, 0),
            scheduled_arrival=datetime(2026, 8, 23, 9 + i, 30),
            delay_distribution_minutes={"0": 1.0},
        )
        for i, (a, b) in enumerate(pairs)
    ]
    transfers = [
        Transfer(
            transfer_id=f"TR_{a}{b}_{b}{c}",
            station_id=b,
            from_leg_id=f"L_{a}_{b}",
            to_leg_id=f"L_{b}_{c}",
            scheduled_buffer_minutes=10,
        )
        for (a, b), (b2, c) in zip(pairs, pairs[1:])
    ]
    return MockDataset(stations=stations, lines=[], legs=legs, transfers=transfers, routes=[])


def test_finds_two_transfer_route_within_a_longer_chain(four_leg_chain_dataset):
    """A -> D needs exactly 2 transfers (3 legs) -- within the cap."""
    routes = find_candidate_routes(four_leg_chain_dataset, "A", "D", EARLY)
    assert len(routes) == 1
    assert routes[0].legs == ["L_A_B", "L_B_C", "L_C_D"]
    assert routes[0].transfers == ["TR_AB_BC", "TR_BC_CD"]


def test_does_not_find_three_transfer_journeys(four_leg_chain_dataset):
    """A -> E needs 3 transfers (4 legs) -- past the cap, must not appear."""
    routes = find_candidate_routes(four_leg_chain_dataset, "A", "E", EARLY)
    assert routes == []


def test_dangling_transfer_reference_is_skipped_not_raised(synthetic_dataset):
    """A Transfer pointing at a leg_id that doesn't exist must be ignored
    rather than crash the search."""
    broken_transfer = Transfer(
        transfer_id="TR_BROKEN",
        station_id="B",
        from_leg_id="DOES_NOT_EXIST",
        to_leg_id="L_B_C",
        scheduled_buffer_minutes=5,
    )
    dataset = synthetic_dataset.model_copy(
        update={"transfers": [*synthetic_dataset.transfers, broken_transfer]}
    )
    routes = find_candidate_routes(dataset, "A", "C", EARLY)
    assert {r.route_id for r in routes} == {"RS_DIRECT_L_DIRECT", "RS_XFER1_TR1"}


# --- combined direct + transfer results, ordering -------------------------------


def test_direct_and_transfer_routes_are_both_returned_and_sorted(synthetic_dataset):
    routes = find_candidate_routes(synthetic_dataset, "A", "C", EARLY)
    assert [r.route_id for r in routes] == ["RS_XFER1_TR1", "RS_DIRECT_L_DIRECT"]
    assert [r.scheduled_departure for r in routes] == [
        datetime(2026, 8, 23, 9, 0),
        datetime(2026, 8, 23, 10, 0),
    ]


def test_route_ids_are_unique(synthetic_dataset):
    routes = find_candidate_routes(synthetic_dataset, "A", "C", EARLY)
    route_ids = [r.route_id for r in routes]
    assert len(route_ids) == len(set(route_ids))


# --- no cycles / no overshooting the destination ----------------------------


@pytest.fixture
def overshoot_dataset() -> MockDataset:
    """A -> B directly reaches the destination (B); B -> C -> B is a
    dangling loop hanging off the destination station. Mirrors a real bug:
    Reutlingen -> Stuttgart (direct) -> Heidelberg -> Stuttgart was surfaced
    as a bogus 2-transfer "route" that leaves the destination and comes
    back to it, instead of the search stopping once it arrives."""
    return MockDataset(
        stations=[
            Station(station_id="A", name="Station A"),
            Station(station_id="B", name="Station B"),
            Station(station_id="C", name="Station C"),
        ],
        lines=[],
        legs=[
            Leg(
                leg_id="L_A_B",
                line_id="X",
                origin_station_id="A",
                destination_station_id="B",
                scheduled_departure=datetime(2026, 8, 23, 7, 0),
                scheduled_arrival=datetime(2026, 8, 23, 7, 30),
                delay_distribution_minutes={"0": 1.0},
            ),
            Leg(
                leg_id="L_B_C",
                line_id="Y",
                origin_station_id="B",
                destination_station_id="C",
                scheduled_departure=datetime(2026, 8, 23, 8, 0),
                scheduled_arrival=datetime(2026, 8, 23, 8, 30),
                delay_distribution_minutes={"0": 1.0},
            ),
            Leg(
                leg_id="L_C_B",
                line_id="Y",
                origin_station_id="C",
                destination_station_id="B",
                scheduled_departure=datetime(2026, 8, 23, 9, 0),
                scheduled_arrival=datetime(2026, 8, 23, 9, 30),
                delay_distribution_minutes={"0": 1.0},
            ),
        ],
        transfers=[
            Transfer(
                transfer_id="TR_AB_BC",
                station_id="B",
                from_leg_id="L_A_B",
                to_leg_id="L_B_C",
                scheduled_buffer_minutes=30,
            ),
            Transfer(
                transfer_id="TR_BC_CB",
                station_id="C",
                from_leg_id="L_B_C",
                to_leg_id="L_C_B",
                scheduled_buffer_minutes=30,
            ),
        ],
        routes=[],
    )


def test_does_not_produce_a_cycle_through_the_destination(overshoot_dataset):
    """A -> B must return exactly the direct route -- not also the bogus
    2-transfer A -> B -> C -> B "overshoot" that leaves the destination
    station and comes back to it."""
    routes = find_candidate_routes(overshoot_dataset, "A", "B", EARLY)
    assert [r.route_id for r in routes] == ["RS_DIRECT_L_A_B"]


def test_no_returned_route_revisits_a_station(overshoot_dataset):
    """General invariant: every candidate route's station sequence (origin,
    each transfer station, destination) must have no duplicates -- every
    route is a simple path, never a cycle."""
    legs_by_id = {leg.leg_id: leg for leg in overshoot_dataset.legs}
    routes = find_candidate_routes(overshoot_dataset, "A", "B", EARLY)
    assert routes  # sanity check the fixture actually exercises the search
    for route in routes:
        legs = [legs_by_id[leg_id] for leg_id in route.legs]
        stations = [legs[0].origin_station_id] + [leg.destination_station_id for leg in legs]
        assert len(stations) == len(set(stations)), f"{route.route_id} revisits a station: {stations}"
