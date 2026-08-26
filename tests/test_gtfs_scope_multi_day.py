"""Tests for pipelines/gtfs_scope.py's scope_gtfs_feed_multi_day
(DATA_SPEC.md §3 step 2): same DB-agency/normalizable-type/corridor-touching
filtering as scope_gtfs_feed, but with no service_date -- every service_id's
trips survive, and calendar.txt/calendar_dates.txt are copied through so the
Warehouse build can resolve active services per query date instead of one
date baked in at build time. Uses the same data/fixtures/gtfs_national_sample/
fixture as test_gtfs_scope.py.
"""

import csv
from pathlib import Path

from pipelines.gtfs_scope import scope_gtfs_feed_multi_day

FIXTURE_DIR = Path(__file__).parent.parent / "data" / "fixtures" / "gtfs_national_sample"
CORRIDOR_STOP_IDS = {"CORR_A", "CORR_B"}


def _read_ids(path: Path, column: str) -> set[str]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {row[column] for row in csv.DictReader(f)}


def test_keeps_only_corridor_touching_db_routes(tmp_path):
    scope_gtfs_feed_multi_day(FIXTURE_DIR, tmp_path, CORRIDOR_STOP_IDS)
    route_ids = _read_ids(tmp_path / "routes.txt", "route_id")
    assert route_ids == {"ROUTE_A", "ROUTE_D"}


def test_excludes_non_db_agency_and_unnormalizable_type(tmp_path):
    scope_gtfs_feed_multi_day(FIXTURE_DIR, tmp_path, CORRIDOR_STOP_IDS)
    route_ids = _read_ids(tmp_path / "routes.txt", "route_id")
    assert "ROUTE_B" not in route_ids  # ÖBB, non-DB
    assert "ROUTE_C" not in route_ids  # Nightjet, unnormalizable type


def test_keeps_trips_from_every_service_id_regardless_of_date(tmp_path):
    """The whole point: unlike scope_gtfs_feed, both the weekday trip and
    the weekend-only trip survive -- there's no service_date to filter by."""
    scope_gtfs_feed_multi_day(FIXTURE_DIR, tmp_path, CORRIDOR_STOP_IDS)
    trip_ids = _read_ids(tmp_path / "trips.txt", "trip_id")
    assert "T_ICE" in trip_ids
    assert "T_ICE_WEEKEND" in trip_ids


def test_excludes_trips_never_touching_corridor(tmp_path):
    scope_gtfs_feed_multi_day(FIXTURE_DIR, tmp_path, CORRIDOR_STOP_IDS)
    trip_ids = _read_ids(tmp_path / "trips.txt", "trip_id")
    assert "T_FAR" not in trip_ids


def test_copies_calendar_files_through(tmp_path):
    scope_gtfs_feed_multi_day(FIXTURE_DIR, tmp_path, CORRIDOR_STOP_IDS)
    calendar_service_ids = _read_ids(tmp_path / "calendar.txt", "service_id")
    assert calendar_service_ids == {"WD", "WEEKEND"}
    exception_service_ids = _read_ids(tmp_path / "calendar_dates.txt", "service_id")
    assert exception_service_ids == {"WD"}


def test_copies_stops_unfiltered(tmp_path):
    scope_gtfs_feed_multi_day(FIXTURE_DIR, tmp_path, CORRIDOR_STOP_IDS)
    scoped_stop_ids = _read_ids(tmp_path / "stops.txt", "stop_id")
    original_stop_ids = _read_ids(FIXTURE_DIR / "stops.txt", "stop_id")
    assert scoped_stop_ids == original_stop_ids
