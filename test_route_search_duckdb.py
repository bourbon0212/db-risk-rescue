"""Tests for pipelines/route_search_duckdb.py (SPEC.md §6): the DuckDB-backed
sibling of pipelines/route_search.py's find_candidate_routes, plus the
dynamic-calendar-date filtering and legs_by_id/transfers_by_id mutation
contract that lets engine.py's precompute_fallback_plans/simulate_route
consume it exactly like the Phase 1/2 in-memory path (§6.3).

Builds a small synthetic warehouse by hand via pipelines/warehouse_writer.py
-- same station/leg/transfer shape as test_route_search.py's
synthetic_dataset fixture, translated into date-agnostic templates plus a
calendar, so the calendar-filtering behavior (the part with no Phase 1/2
equivalent) can be tested precisely.
"""

import random
from datetime import date, datetime

import duckdb
import pytest

from engine import index_dataset, precompute_fallback_plans, simulate_route
from models import Leg, Line, MockDataset, Station, Transfer
from pipelines.calendar_ingest import ServiceCalendarException, ServiceCalendarRow
from pipelines.gtfs_ingest import LegTemplate, TransferTemplate, TripRecord
from pipelines.route_search_duckdb import calendar_window, find_candidate_routes
from pipelines.warehouse_writer import create_schema, write_warehouse

MONDAY = date(2026, 8, 24)
SATURDAY = date(2026, 8, 29)
EARLY = datetime.combine(MONDAY, datetime.min.time())

_CAL_START, _CAL_END = date(2026, 1, 1), date(2026, 12, 31)


def _weekday_calendar(service_id: str) -> ServiceCalendarRow:
    return ServiceCalendarRow(
        service_id=service_id, monday=True, tuesday=True, wednesday=True, thursday=True,
        friday=True, saturday=False, sunday=False, start_date=_CAL_START, end_date=_CAL_END,
    )


def _weekend_calendar(service_id: str) -> ServiceCalendarRow:
    return ServiceCalendarRow(
        service_id=service_id, monday=False, tuesday=False, wednesday=False, thursday=False,
        friday=False, saturday=True, sunday=True, start_date=_CAL_START, end_date=_CAL_END,
    )


@pytest.fixture
def conn():
    connection = duckdb.connect(":memory:")
    create_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def synthetic_warehouse(conn) -> duckdb.DuckDBPyConnection:
    """A -> B -> C, plus a direct A -> C leg, mirroring
    test_route_search.py's synthetic_dataset -- but the direct leg only
    runs on WD (weekdays) and a second, otherwise-identical direct leg only
    runs on WEEKEND, so calendar-filtering tests have real signal."""
    stations = [
        Station(station_id="A", name="Station A"),
        Station(station_id="B", name="Station B"),
        Station(station_id="C", name="Station C"),
    ]
    lines = [
        Line(line_id="X", type="ICE", operator="DB Fernverkehr"),
        Line(line_id="Y", type="RE", operator="DB Regio"),
        Line(line_id="Z", type="RE", operator="DB Regio"),
    ]
    trips = [
        TripRecord(trip_id="T_DIRECT_WD", line_id="X", service_id="WD"),
        TripRecord(trip_id="T_DIRECT_WEEKEND", line_id="X", service_id="WEEKEND"),
        TripRecord(trip_id="T_A_B", line_id="Y", service_id="WD"),
        TripRecord(trip_id="T_B_C", line_id="Z", service_id="WD"),
    ]
    leg_templates = [
        LegTemplate(
            leg_id="L_DIRECT_WD", trip_id="T_DIRECT_WD", line_id="X", sequence_index=0,
            origin_station_id="A", destination_station_id="C",
            departure_seconds=10 * 3600, arrival_seconds=11 * 3600,
        ),
        LegTemplate(
            leg_id="L_DIRECT_WEEKEND", trip_id="T_DIRECT_WEEKEND", line_id="X", sequence_index=0,
            origin_station_id="A", destination_station_id="C",
            departure_seconds=10 * 3600, arrival_seconds=11 * 3600,
        ),
        LegTemplate(
            leg_id="L_A_B", trip_id="T_A_B", line_id="Y", sequence_index=0,
            origin_station_id="A", destination_station_id="B",
            departure_seconds=9 * 3600, arrival_seconds=9 * 3600 + 30 * 60,
        ),
        LegTemplate(
            leg_id="L_B_C", trip_id="T_B_C", line_id="Z", sequence_index=0,
            origin_station_id="B", destination_station_id="C",
            departure_seconds=9 * 3600 + 40 * 60, arrival_seconds=10 * 3600 + 30 * 60,
        ),
    ]
    transfer_templates = [
        TransferTemplate(
            transfer_id="TR1", station_id="B", from_leg_id="L_A_B", to_leg_id="L_B_C",
            from_trip_id="T_A_B", to_trip_id="T_B_C", buffer_minutes=10,
        )
    ]
    calendar_rows = [_weekday_calendar("WD"), _weekend_calendar("WEEKEND")]
    calendar_exceptions = [
        ServiceCalendarException(service_id="WD", date=date(2026, 8, 26), exception_type=2)
    ]
    distributions = {"X": {"0": 1.0}, "Y": {"0": 1.0}, "Z": {"0": 1.0}}

    write_warehouse(
        conn, stations, lines, trips, leg_templates, transfer_templates,
        calendar_rows, calendar_exceptions, distributions,
    )
    return conn


# --- direct legs + calendar filtering ----------------------------------------


def test_finds_direct_route_on_a_weekday(synthetic_warehouse):
    """A -> C on a weekday matches both the direct WD leg and the A->B->C
    transfer route (also WD) -- sorted by departure, the transfer route
    (09:00) comes before the direct one (10:00)."""
    legs_by_id, transfers_by_id = {}, {}
    routes = find_candidate_routes(
        synthetic_warehouse, "A", "C", EARLY, MONDAY, legs_by_id, transfers_by_id
    )
    assert [r.route_id for r in routes] == ["RS_XFER1_TR1", "RS_DIRECT_L_DIRECT_WD"]


def test_weekend_only_leg_is_excluded_on_a_weekday(synthetic_warehouse):
    legs_by_id, transfers_by_id = {}, {}
    routes = find_candidate_routes(
        synthetic_warehouse, "A", "C", EARLY, MONDAY, legs_by_id, transfers_by_id
    )
    assert "RS_DIRECT_L_DIRECT_WEEKEND" not in {r.route_id for r in routes}


def test_finds_weekend_only_leg_on_a_saturday(synthetic_warehouse):
    legs_by_id, transfers_by_id = {}, {}
    early_saturday = datetime.combine(SATURDAY, datetime.min.time())
    routes = find_candidate_routes(
        synthetic_warehouse, "A", "C", early_saturday, SATURDAY, legs_by_id, transfers_by_id
    )
    assert [r.route_id for r in routes] == ["RS_DIRECT_L_DIRECT_WEEKEND"]


def test_calendar_dates_exception_removes_a_normally_active_service(synthetic_warehouse):
    """calendar_dates.txt removes WD on 2026-08-26 -- the direct WD leg (and
    the WD-dependent transfer route) must both disappear that day even
    though WD's weekday pattern would normally cover a Wednesday."""
    legs_by_id, transfers_by_id = {}, {}
    exception_date = date(2026, 8, 26)
    early = datetime.combine(exception_date, datetime.min.time())
    routes = find_candidate_routes(
        synthetic_warehouse, "A", "C", early, exception_date, legs_by_id, transfers_by_id
    )
    assert routes == []


def test_same_origin_and_destination_returns_empty_list(synthetic_warehouse):
    legs_by_id, transfers_by_id = {}, {}
    routes = find_candidate_routes(
        synthetic_warehouse, "A", "A", EARLY, MONDAY, legs_by_id, transfers_by_id
    )
    assert routes == []


# --- departure-time cutoff ----------------------------------------------------


def test_departure_time_cutoff_is_inclusive(synthetic_warehouse):
    legs_by_id, transfers_by_id = {}, {}
    cutoff = datetime.combine(MONDAY, datetime.min.time()).replace(hour=10)
    routes = find_candidate_routes(
        synthetic_warehouse, "A", "C", cutoff, MONDAY, legs_by_id, transfers_by_id
    )
    assert [r.route_id for r in routes] == ["RS_DIRECT_L_DIRECT_WD"]


def test_departure_time_cutoff_excludes_earlier_legs(synthetic_warehouse):
    legs_by_id, transfers_by_id = {}, {}
    cutoff = datetime.combine(MONDAY, datetime.min.time()).replace(hour=10, minute=1)
    routes = find_candidate_routes(
        synthetic_warehouse, "A", "C", cutoff, MONDAY, legs_by_id, transfers_by_id
    )
    assert routes == []


# --- single-transfer + combined results --------------------------------------


def test_finds_single_transfer_route(synthetic_warehouse):
    legs_by_id, transfers_by_id = {}, {}
    routes = find_candidate_routes(
        synthetic_warehouse, "A", "C", EARLY, MONDAY, legs_by_id, transfers_by_id
    )
    route_ids = {r.route_id for r in routes}
    assert "RS_XFER1_TR1" in route_ids
    xfer_route = next(r for r in routes if r.route_id == "RS_XFER1_TR1")
    assert xfer_route.legs == ["L_A_B", "L_B_C"]
    assert xfer_route.transfers == ["TR1"]


def test_routes_are_sorted_by_departure(synthetic_warehouse):
    legs_by_id, transfers_by_id = {}, {}
    routes = find_candidate_routes(
        synthetic_warehouse, "A", "C", EARLY, MONDAY, legs_by_id, transfers_by_id
    )
    assert [r.scheduled_departure for r in routes] == sorted(r.scheduled_departure for r in routes)


# --- legs_by_id/transfers_by_id mutation contract (SPEC.md §6.3) ------------


def test_mutates_legs_and_transfers_dicts_in_place(synthetic_warehouse):
    legs_by_id, transfers_by_id = {}, {}
    find_candidate_routes(synthetic_warehouse, "A", "C", EARLY, MONDAY, legs_by_id, transfers_by_id)

    assert "L_A_B" in legs_by_id
    assert isinstance(legs_by_id["L_A_B"], Leg)
    assert legs_by_id["L_A_B"].scheduled_departure == datetime(2026, 8, 24, 9, 0)
    assert legs_by_id["L_A_B"].delay_distribution_minutes == {"0": 1.0}

    assert "TR1" in transfers_by_id
    assert isinstance(transfers_by_id["TR1"], Transfer)
    assert transfers_by_id["TR1"].scheduled_buffer_minutes == 10


def test_does_not_clobber_previously_populated_entries(synthetic_warehouse):
    """Successive calls (top-level search + per-transfer fallback searches
    in precompute_fallback_plans) accumulate into the same dicts rather than
    each call starting fresh."""
    legs_by_id, transfers_by_id = {}, {}
    find_candidate_routes(synthetic_warehouse, "A", "B", EARLY, MONDAY, legs_by_id, transfers_by_id)
    assert "L_A_B" in legs_by_id
    find_candidate_routes(synthetic_warehouse, "B", "C", EARLY, MONDAY, legs_by_id, transfers_by_id)
    assert "L_A_B" in legs_by_id  # still there
    assert "L_B_C" in legs_by_id  # newly added


# --- calendar_window -----------------------------------------------------------


def test_calendar_window_returns_min_and_max(synthetic_warehouse):
    window = calendar_window(synthetic_warehouse)
    assert window == (_CAL_START, _CAL_END)


# --- end-to-end with engine.py (SPEC.md §6.3) --------------------------------


def test_precompute_fallback_plans_and_simulate_route_work_against_duckdb(synthetic_warehouse):
    """Full integration: engine.py's precompute_fallback_plans/simulate_route,
    driven by a route_search_fn closure over route_search_duckdb, must
    behave exactly like the Phase 1/2 in-memory path -- proving §6.3's
    promise that the simulation core needs no changes to consume this
    backend."""
    legs_by_id, transfers_by_id = {}, {}
    routes = find_candidate_routes(
        synthetic_warehouse, "A", "C", EARLY, MONDAY, legs_by_id, transfers_by_id
    )
    route = next(r for r in routes if r.route_id == "RS_XFER1_TR1")

    lines_by_id = {
        row[0]: Line(line_id=row[0], type=row[1], operator=row[2])
        for row in synthetic_warehouse.execute("SELECT line_id, type, operator FROM lines").fetchall()
    }

    def route_search_fn(origin_id, destination_id, departure_time):
        return find_candidate_routes(
            synthetic_warehouse, origin_id, destination_id, departure_time, MONDAY,
            legs_by_id, transfers_by_id,
        )

    fallback_plans = precompute_fallback_plans(
        route, None, legs_by_id, transfers_by_id, route_search_fn=route_search_fn
    )
    # No fallback is expected here (the transfer holds -- 40 minute buffer,
    # zero-delay-only distribution) -- this asserts the wiring runs cleanly
    # end to end, not a specific fallback outcome.
    assert set(fallback_plans) == {"TR1"}

    result = simulate_route(
        route, legs_by_id, transfers_by_id, lines_by_id,
        n_iterations=50, rng=random.Random(1), fallback_plans=fallback_plans,
    )
    assert result.n_iterations == 50
    assert result.transfer_risks[0].transfer_id == "TR1"
    assert result.transfer_risks[0].simulated_miss_rate == 0.0
