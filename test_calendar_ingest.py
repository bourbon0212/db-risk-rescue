"""Tests for pipelines/calendar_ingest.py (SPEC.md §6.2): parsing
calendar.txt/calendar_dates.txt into plain rows, against the same
fixtures/gtfs_national_sample/ fixture test_gtfs_scope.py already uses for
its WD/WEEKEND + holiday-exception calendar scenarios.
"""

from datetime import date
from pathlib import Path

from pipelines.calendar_ingest import parse_calendar, parse_calendar_exceptions

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gtfs_national_sample"
NO_CALENDAR_DIR = Path(__file__).parent / "fixtures" / "gtfs_smoke"


def test_parse_calendar_reads_weekday_pattern_and_range():
    rows = parse_calendar(FIXTURE_DIR)
    by_id = {r.service_id: r for r in rows}

    wd = by_id["WD"]
    assert wd.monday and wd.tuesday and wd.wednesday and wd.thursday and wd.friday
    assert not wd.saturday and not wd.sunday
    assert wd.start_date == date(2026, 1, 1)
    assert wd.end_date == date(2026, 12, 31)

    weekend = by_id["WEEKEND"]
    assert weekend.saturday and weekend.sunday
    assert not weekend.monday


def test_parse_calendar_returns_empty_list_when_file_absent():
    assert parse_calendar(NO_CALENDAR_DIR) == []


def test_parse_calendar_exceptions_reads_removal():
    rows = parse_calendar_exceptions(FIXTURE_DIR)
    assert len(rows) == 1
    assert rows[0].service_id == "WD"
    assert rows[0].date == date(2026, 8, 27)
    assert rows[0].exception_type == 2


def test_parse_calendar_exceptions_returns_empty_list_when_file_absent():
    assert parse_calendar_exceptions(NO_CALENDAR_DIR) == []
