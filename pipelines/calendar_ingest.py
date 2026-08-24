"""GTFS calendar.txt / calendar_dates.txt ingestion for Phase 3's dynamic
calendar (SPEC.md §4.3).

Kept deliberately separate from gtfs_ingest.py's topology parsing: these two
files are the only place "which date is this?" enters the pipeline, and
build_warehouse.py stores them as plain rows (pipelines/warehouse_writer.py)
rather than collapsing them to one service_date at build time the way
gtfs_scope.py's single-day scope_gtfs_feed() still does for the Phase 1/2
JSON path. Active-service resolution for a given query date happens as SQL
at route-search time (pipelines/route_search_duckdb.py), not here -- this
module only parses the raw calendar rows.
"""

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

WEEKDAY_COLUMNS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass(frozen=True)
class ServiceCalendarRow:
    """One calendar.txt row: a weekday pattern active over [start_date, end_date]."""

    service_id: str
    monday: bool
    tuesday: bool
    wednesday: bool
    thursday: bool
    friday: bool
    saturday: bool
    sunday: bool
    start_date: date
    end_date: date


@dataclass(frozen=True)
class ServiceCalendarException:
    """One calendar_dates.txt row: exception_type 1 adds service_id on date,
    2 removes it, even outside (or overriding) its calendar.txt pattern."""

    service_id: str
    date: date
    exception_type: int


def _parse_gtfs_date(yyyymmdd: str) -> date:
    yyyymmdd = yyyymmdd.strip()
    return date(int(yyyymmdd[0:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))


def parse_calendar(gtfs_dir: Path) -> list[ServiceCalendarRow]:
    """Parse calendar.txt. Returns an empty list if the feed has none (some
    GTFS feeds encode service dates entirely via calendar_dates.txt)."""
    path = gtfs_dir / "calendar.txt"
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(
                ServiceCalendarRow(
                    service_id=row["service_id"],
                    monday=row["monday"].strip() == "1",
                    tuesday=row["tuesday"].strip() == "1",
                    wednesday=row["wednesday"].strip() == "1",
                    thursday=row["thursday"].strip() == "1",
                    friday=row["friday"].strip() == "1",
                    saturday=row["saturday"].strip() == "1",
                    sunday=row["sunday"].strip() == "1",
                    start_date=_parse_gtfs_date(row["start_date"]),
                    end_date=_parse_gtfs_date(row["end_date"]),
                )
            )
    return rows


def parse_calendar_exceptions(gtfs_dir: Path) -> list[ServiceCalendarException]:
    """Parse calendar_dates.txt. Returns an empty list if the feed has none."""
    path = gtfs_dir / "calendar_dates.txt"
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(
                ServiceCalendarException(
                    service_id=row["service_id"],
                    date=_parse_gtfs_date(row["date"]),
                    exception_type=int(row["exception_type"].strip()),
                )
            )
    return rows
