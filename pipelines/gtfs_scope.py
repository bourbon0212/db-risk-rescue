"""Corridor + DB-agency scoping for the real, national GTFS.DE feed.

Per DATA_SPEC.md §3 step 2. Filters a downloaded feed (fv_free or rv_free,
extracted from data/raw/*.zip) down to DB-operated routes whose type
normalizes to a gtfs_ingest.LINE_TYPES member and whose trips touch a
corridor station, writing a standalone scoped GTFS directory that
gtfs_ingest.py then consumes unchanged.

Note this module *excludes* an unrecognized line type where gtfs_ingest
*raises* on one -- deliberate, and explained in DATA_SPEC.md §3 step 2.
"""

import csv
from datetime import date as date_type
from pathlib import Path

from pipelines.gtfs_ingest import _normalize_line_type

_WEEKDAY_COLUMNS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Real DB entities in GTFS.DE's agency.txt use several regional-subsidiary
# names (e.g. "DB Regio AG Bayern", "DB Regio AG NRW", "DB Fernverkehr AG")
# rather than one fixed string, so this is a prefix match, not an exact one.
_DB_AGENCY_PREFIXES = ("DB Fernverkehr", "DB Regio")


def _is_db_agency(agency_name: str) -> bool:
    return agency_name.startswith(_DB_AGENCY_PREFIXES)


def _read_rows(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def _write_rows(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _active_service_ids(gtfs_dir: Path, service_date: date_type) -> set[str]:
    """Resolve which GTFS service_ids actually run on service_date.

    stop_times.txt only carries time-of-day, not dates -- calendar.txt (a
    weekday pattern + date range) plus calendar_dates.txt (single-date
    add/remove exceptions) is what GTFS uses to say which day a trip runs.
    Without this, every trip variant in the feed's rolling 30-day window
    (weekday timetable, weekend timetable, holiday exceptions, ...) would
    get stamped onto the one chosen service_date, producing duplicate/
    overlapping legs that don't reflect any single real day.
    """
    yyyymmdd = service_date.strftime("%Y%m%d")
    weekday_column = _WEEKDAY_COLUMNS[service_date.weekday()]

    active: set[str] = set()
    calendar_path = gtfs_dir / "calendar.txt"
    if calendar_path.exists():
        _, calendar_rows = _read_rows(calendar_path)
        for row in calendar_rows:
            if row.get(weekday_column, "0").strip() != "1":
                continue
            if row["start_date"] <= yyyymmdd <= row["end_date"]:
                active.add(row["service_id"])

    calendar_dates_path = gtfs_dir / "calendar_dates.txt"
    if calendar_dates_path.exists():
        _, exception_rows = _read_rows(calendar_dates_path)
        for row in exception_rows:
            if row["date"] != yyyymmdd:
                continue
            if row["exception_type"] == "1":
                active.add(row["service_id"])
            elif row["exception_type"] == "2":
                active.discard(row["service_id"])

    return active


def scope_gtfs_feed(
    gtfs_dir: Path, out_dir: Path, corridor_stop_ids: set[str], service_date: date_type
) -> None:
    """Filter gtfs_dir down to DB-operated, normalizable-type routes,
    trips that actually run on service_date, and trips that touch at least
    one id in corridor_stop_ids, writing the scoped feed to out_dir.

    corridor_stop_ids are raw GTFS stop_ids (id_crosswalk.py's keys) -- the
    real parent-station nodes for our corridor (DATA_SPEC.md §9.1).
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    agency_fields, agency_rows = _read_rows(gtfs_dir / "agency.txt")
    kept_agency_ids = {row["agency_id"] for row in agency_rows if _is_db_agency(row["agency_name"])}
    kept_agency_rows = [row for row in agency_rows if row["agency_id"] in kept_agency_ids]

    route_fields, route_rows = _read_rows(gtfs_dir / "routes.txt")
    kept_route_rows = []
    for row in route_rows:
        if row["agency_id"] not in kept_agency_ids:
            continue
        try:
            _normalize_line_type(row.get("route_short_name", ""), row.get("route_type", ""))
        except ValueError:
            continue
        kept_route_rows.append(row)
    kept_route_ids = {row["route_id"] for row in kept_route_rows}

    active_service_ids = _active_service_ids(gtfs_dir, service_date)
    trip_fields, trip_rows = _read_rows(gtfs_dir / "trips.txt")
    kept_trips_by_id = {
        row["trip_id"]: row
        for row in trip_rows
        if row["route_id"] in kept_route_ids and row["service_id"] in active_service_ids
    }

    stops_fields, stops_rows = _read_rows(gtfs_dir / "stops.txt")
    stop_to_parent = {
        row["stop_id"]: (row.get("parent_station", "").strip() or row["stop_id"])
        for row in stops_rows
    }

    stop_time_fields, stop_time_rows = _read_rows(gtfs_dir / "stop_times.txt")

    corridor_trip_ids: set[str] = set()
    for row in stop_time_rows:
        if row["trip_id"] not in kept_trips_by_id:
            continue
        if stop_to_parent.get(row["stop_id"], row["stop_id"]) in corridor_stop_ids:
            corridor_trip_ids.add(row["trip_id"])

    kept_stop_time_rows = [row for row in stop_time_rows if row["trip_id"] in corridor_trip_ids]
    kept_trip_rows = [row for tid, row in kept_trips_by_id.items() if tid in corridor_trip_ids]
    kept_used_route_ids = {row["route_id"] for row in kept_trip_rows}
    kept_route_rows = [row for row in kept_route_rows if row["route_id"] in kept_used_route_ids]
    kept_used_agency_ids = {row["agency_id"] for row in kept_route_rows}
    kept_agency_rows = [row for row in kept_agency_rows if row["agency_id"] in kept_used_agency_ids]

    _write_rows(out_dir / "agency.txt", agency_fields, kept_agency_rows)
    _write_rows(out_dir / "routes.txt", route_fields, kept_route_rows)
    _write_rows(out_dir / "trips.txt", trip_fields, kept_trip_rows)
    _write_rows(out_dir / "stop_times.txt", stop_time_fields, kept_stop_time_rows)
    # stops.txt is copied through whole (unfiltered): every stop_id referenced
    # by a kept trip's full stop sequence -- including non-corridor
    # intermediate stops -- must resolve in gtfs_ingest.parse_legs' stop-to-
    # station map, and the file is small enough (tens of thousands of rows)
    # that filtering it isn't worth the added complexity.
    _write_rows(out_dir / "stops.txt", stops_fields, stops_rows)


def scope_gtfs_feed_multi_day(gtfs_dir: Path, out_dir: Path, corridor_stop_ids: set[str]) -> None:
    """Warehouse-build sibling of scope_gtfs_feed() (DATA_SPEC.md §3 step 2): identical
    DB-agency / normalizable-type / corridor-touching filtering, but keeps
    every trip regardless of which service_id/date it belongs to -- no
    _active_service_ids() call, no service_date parameter at all. calendar.txt
    and calendar_dates.txt are copied through unfiltered (small files; some
    rows may reference service_ids no kept trip uses, which is harmless) so
    pipelines/calendar_ingest.py can parse the full calendar window
    downstream. This is what makes the warehouse build "dynamic calendar
    dates" rather than one date baked in at build time.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    agency_fields, agency_rows = _read_rows(gtfs_dir / "agency.txt")
    kept_agency_ids = {row["agency_id"] for row in agency_rows if _is_db_agency(row["agency_name"])}
    kept_agency_rows = [row for row in agency_rows if row["agency_id"] in kept_agency_ids]

    route_fields, route_rows = _read_rows(gtfs_dir / "routes.txt")
    kept_route_rows = []
    for row in route_rows:
        if row["agency_id"] not in kept_agency_ids:
            continue
        try:
            _normalize_line_type(row.get("route_short_name", ""), row.get("route_type", ""))
        except ValueError:
            continue
        kept_route_rows.append(row)
    kept_route_ids = {row["route_id"] for row in kept_route_rows}

    trip_fields, trip_rows = _read_rows(gtfs_dir / "trips.txt")
    kept_trips_by_id = {
        row["trip_id"]: row for row in trip_rows if row["route_id"] in kept_route_ids
    }

    stops_fields, stops_rows = _read_rows(gtfs_dir / "stops.txt")
    stop_to_parent = {
        row["stop_id"]: (row.get("parent_station", "").strip() or row["stop_id"])
        for row in stops_rows
    }

    stop_time_fields, stop_time_rows = _read_rows(gtfs_dir / "stop_times.txt")

    corridor_trip_ids: set[str] = set()
    for row in stop_time_rows:
        if row["trip_id"] not in kept_trips_by_id:
            continue
        if stop_to_parent.get(row["stop_id"], row["stop_id"]) in corridor_stop_ids:
            corridor_trip_ids.add(row["trip_id"])

    kept_stop_time_rows = [row for row in stop_time_rows if row["trip_id"] in corridor_trip_ids]
    kept_trip_rows = [row for tid, row in kept_trips_by_id.items() if tid in corridor_trip_ids]
    kept_used_route_ids = {row["route_id"] for row in kept_trip_rows}
    kept_route_rows = [row for row in kept_route_rows if row["route_id"] in kept_used_route_ids]
    kept_used_agency_ids = {row["agency_id"] for row in kept_route_rows}
    kept_agency_rows = [row for row in kept_agency_rows if row["agency_id"] in kept_used_agency_ids]

    _write_rows(out_dir / "agency.txt", agency_fields, kept_agency_rows)
    _write_rows(out_dir / "routes.txt", route_fields, kept_route_rows)
    _write_rows(out_dir / "trips.txt", trip_fields, kept_trip_rows)
    _write_rows(out_dir / "stop_times.txt", stop_time_fields, kept_stop_time_rows)
    _write_rows(out_dir / "stops.txt", stops_fields, stops_rows)

    for calendar_file in ("calendar.txt", "calendar_dates.txt"):
        src = gtfs_dir / calendar_file
        if src.exists():
            fields, rows = _read_rows(src)
            _write_rows(out_dir / calendar_file, fields, rows)
