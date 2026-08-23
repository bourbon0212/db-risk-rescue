"""Tests for pipelines/gtfs_scope.py against a small hand-built "national
feed" fixture (fixtures/gtfs_national_sample/) covering the exclusion
reasons a real DB feed needs (DATA_SPEC.md §3 step 2): non-DB agency, a DB
route whose type doesn't normalize, a DB route that never touches the
corridor, and a trip whose calendar doesn't cover the requested date --
plus confirming the scoped output is consumable by gtfs_ingest.py's
existing, unmodified parse functions.
"""

import csv
import tempfile
from datetime import date
from pathlib import Path

from pipelines.gtfs_ingest import parse_legs, parse_lines, parse_stations
from pipelines.gtfs_scope import scope_gtfs_feed

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gtfs_national_sample"
CORRIDOR_STOP_IDS = {"CORR_A", "CORR_B"}

TUESDAY = date(2026, 8, 25)  # WD (weekday) service active, WEEKEND service not
SATURDAY = date(2026, 8, 29)  # WEEKEND service active, WD not
HOLIDAY_EXCEPTION_DATE = date(2026, 8, 27)  # a Thursday where calendar_dates.txt removes WD


def _read_ids(path: Path, column: str) -> set[str]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {row[column] for row in csv.DictReader(f)}


def test_scope_gtfs_feed_keeps_only_corridor_touching_db_routes(tmp_path):
    scope_gtfs_feed(FIXTURE_DIR, tmp_path, CORRIDOR_STOP_IDS, TUESDAY)

    route_ids = _read_ids(tmp_path / "routes.txt", "route_id")
    assert route_ids == {"ROUTE_A", "ROUTE_D"}


def test_scope_gtfs_feed_excludes_non_db_agency(tmp_path):
    """ROUTE_B (ÖBB, agency_id 2) touches the corridor but isn't DB-operated."""
    scope_gtfs_feed(FIXTURE_DIR, tmp_path, CORRIDOR_STOP_IDS, TUESDAY)
    route_ids = _read_ids(tmp_path / "routes.txt", "route_id")
    assert "ROUTE_B" not in route_ids


def test_scope_gtfs_feed_excludes_unnormalizable_type():
    """ROUTE_C (DB Fernverkehr, "NJ 420" Nightjet) touches the corridor and
    is DB-operated, but Nightjet isn't one of gtfs_ingest.LINE_TYPES."""
    with tempfile.TemporaryDirectory() as tmp:
        scope_gtfs_feed(FIXTURE_DIR, Path(tmp), CORRIDOR_STOP_IDS, TUESDAY)
        route_ids = _read_ids(Path(tmp) / "routes.txt", "route_id")
    assert "ROUTE_C" not in route_ids


def test_scope_gtfs_feed_excludes_trips_never_touching_corridor(tmp_path):
    """ROUTE_E is DB + normalizable but its only trip (T_FAR) never visits
    CORR_A or CORR_B."""
    scope_gtfs_feed(FIXTURE_DIR, tmp_path, CORRIDOR_STOP_IDS, TUESDAY)
    route_ids = _read_ids(tmp_path / "routes.txt", "route_id")
    trip_ids = _read_ids(tmp_path / "trips.txt", "trip_id")
    assert "ROUTE_E" not in route_ids
    assert "T_FAR" not in trip_ids


def test_scope_gtfs_feed_keeps_full_trip_sequence_not_just_corridor_stops(tmp_path):
    """T_RE touches CORR_B then continues to OTHER_C -- both stop_times rows
    must survive so gtfs_ingest.parse_legs can walk the real sequence."""
    scope_gtfs_feed(FIXTURE_DIR, tmp_path, CORRIDOR_STOP_IDS, TUESDAY)
    with (tmp_path / "stop_times.txt").open(encoding="utf-8-sig", newline="") as f:
        rows = [row for row in csv.DictReader(f) if row["trip_id"] == "T_RE"]
    assert len(rows) == 2
    assert {row["stop_id"] for row in rows} == {"CORR_B_PLAT", "OTHER_C_PLAT"}


def test_scope_gtfs_feed_copies_stops_unfiltered(tmp_path):
    """stops.txt is deliberately not filtered, so parse_legs never KeyErrors
    resolving a non-corridor intermediate stop."""
    scope_gtfs_feed(FIXTURE_DIR, tmp_path, CORRIDOR_STOP_IDS, TUESDAY)
    scoped_stop_ids = _read_ids(tmp_path / "stops.txt", "stop_id")
    original_stop_ids = _read_ids(FIXTURE_DIR / "stops.txt", "stop_id")
    assert scoped_stop_ids == original_stop_ids


# --- calendar / service-date resolution -----------------------------------


def test_scope_gtfs_feed_excludes_trips_not_running_on_the_weekday(tmp_path):
    """T_ICE_WEEKEND (service_id WEEKEND, sat/sun only) must not appear on a
    Tuesday, even though its route (ROUTE_A) and corridor stops match."""
    scope_gtfs_feed(FIXTURE_DIR, tmp_path, CORRIDOR_STOP_IDS, TUESDAY)
    trip_ids = _read_ids(tmp_path / "trips.txt", "trip_id")
    assert "T_ICE_WEEKEND" not in trip_ids
    assert "T_ICE" in trip_ids  # the WD (weekday) trip on the same route still runs


def test_scope_gtfs_feed_includes_weekend_only_trip_on_a_saturday(tmp_path):
    scope_gtfs_feed(FIXTURE_DIR, tmp_path, CORRIDOR_STOP_IDS, SATURDAY)
    trip_ids = _read_ids(tmp_path / "trips.txt", "trip_id")
    assert "T_ICE_WEEKEND" in trip_ids
    assert "T_ICE" not in trip_ids  # WD doesn't run on Saturday


def test_calendar_dates_exception_removes_a_normally_active_service(tmp_path):
    """calendar_dates.txt removes service WD on 2026-08-27 even though it's
    a Thursday (normally a WD day) -- a real holiday-schedule scenario."""
    scope_gtfs_feed(FIXTURE_DIR, tmp_path, CORRIDOR_STOP_IDS, HOLIDAY_EXCEPTION_DATE)
    trip_ids = _read_ids(tmp_path / "trips.txt", "trip_id")
    assert trip_ids == set()  # WEEKEND doesn't run Thursdays either -- nothing is active


# --- consumed by gtfs_ingest.py's existing functions, unmodified ----------


def test_scoped_output_is_consumable_by_existing_gtfs_ingest_functions(tmp_path):
    """The whole point of scoping first: gtfs_ingest.py's parse_* functions
    run completely unmodified against the scoped directory."""
    scope_gtfs_feed(FIXTURE_DIR, tmp_path, CORRIDOR_STOP_IDS, TUESDAY)

    # stops.txt is deliberately copied whole (see test_scope_gtfs_feed_copies_
    # stops_unfiltered), so parse_stations() still sees every location_type=1
    # station from the original fixture -- including ones no kept trip
    # touches (OTHER_D). That's exactly why the real build assembles Station
    # objects from the crosswalk/mock_data.json directly rather than calling
    # parse_stations() on a scoped feed.
    stations = parse_stations(tmp_path)
    station_ids = {s.station_id for s in stations}
    assert station_ids == {"CORR_A", "CORR_B", "OTHER_C", "OTHER_D"}

    lines = parse_lines(tmp_path)
    assert {line.line_id for line in lines} == {"ICE 100", "RE 5"}

    legs = parse_legs(tmp_path, TUESDAY)
    leg_pairs = {(leg.origin_station_id, leg.destination_station_id) for leg in legs}
    assert leg_pairs == {("CORR_A", "CORR_B"), ("CORR_B", "OTHER_C")}
