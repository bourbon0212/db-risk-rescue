"""Tests for pipelines/route_search.py (DATA_SPEC.md §5, §8 build sequence
step 6): direct legs, single-transfer journeys, the departure-time cutoff,
and the explicit exclusion of multi-transfer journeys.
"""

from datetime import datetime

import pytest

from data_loader import load_dataset
from models import Leg, MockDataset, Station, Transfer
from pipelines.route_search import find_candidate_routes

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
    assert routes[0].route_id == "RS_XFER_T4"


def test_transfer_departure_cutoff_uses_first_legs_departure(mock_dataset):
    """L7 (the first leg) departs 08:15; a cutoff just after that excludes the pair."""
    routes = find_candidate_routes(
        mock_dataset, "DE_MUC_HBF", "DE_MUC_OST", datetime(2026, 8, 23, 8, 16)
    )
    assert routes == []


# --- multi-transfer is explicitly out of scope ---------------------------------


def test_does_not_find_multi_transfer_journeys(mock_dataset):
    """Munich Hbf -> Berlin Hbf only connects via a 2-transfer chain
    (L4 -> T2 -> L5 -> T3 -> L6) in mock_data.json -- v1 must not surface it."""
    routes = find_candidate_routes(mock_dataset, "DE_MUC_HBF", "DE_BER_HBF", EARLY)
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
    assert {r.route_id for r in routes} == {"RS_DIRECT_L_DIRECT", "RS_XFER_TR1"}


# --- combined direct + transfer results, ordering -------------------------------


def test_direct_and_transfer_routes_are_both_returned_and_sorted(synthetic_dataset):
    routes = find_candidate_routes(synthetic_dataset, "A", "C", EARLY)
    assert [r.route_id for r in routes] == ["RS_XFER_TR1", "RS_DIRECT_L_DIRECT"]
    assert [r.scheduled_departure for r in routes] == [
        datetime(2026, 8, 23, 9, 0),
        datetime(2026, 8, 23, 10, 0),
    ]


def test_route_ids_are_unique(synthetic_dataset):
    routes = find_candidate_routes(synthetic_dataset, "A", "C", EARLY)
    route_ids = [r.route_id for r in routes]
    assert len(route_ids) == len(set(route_ids))
