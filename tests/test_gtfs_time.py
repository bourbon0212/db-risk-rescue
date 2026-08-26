"""Tests for gtfs_time.py -- the GTFS temporal vocabulary that pipelines/
encodes and routing/ decodes.

The round-trip property is the load-bearing one: it's what lets a single
date-agnostic warehouse row be read back correctly on every service date its
calendar covers (DATA_SPEC.md §3 step 5, §6). If these two drift apart, legs
come back with silently wrong times rather than an error.
"""

from datetime import date, datetime

from gtfs_time import (
    WEEKDAY_COLUMNS,
    anchor_datetime,
    parse_gtfs_time,
    seconds_since_midnight,
)

SERVICE_DATE = date(2026, 8, 23)


# --- seconds_since_midnight ---------------------------------------------------


def test_seconds_since_midnight_parses_hms():
    assert seconds_since_midnight("09:02:00") == 9 * 3600 + 2 * 60


def test_seconds_since_midnight_handles_post_midnight_hours():
    assert seconds_since_midnight("25:15:00") == 25 * 3600 + 15 * 60


# --- anchor_datetime ----------------------------------------------------------


def test_anchor_datetime_is_the_inverse():
    seconds = seconds_since_midnight("09:02:00")
    assert anchor_datetime(seconds, SERVICE_DATE) == datetime(2026, 8, 23, 9, 2, 0)


def test_anchor_datetime_carries_post_midnight_hours_into_the_next_day():
    """GTFS hours past 23 belong to the same nominal service day but a later
    calendar day -- a 25:15 departure on the 23rd runs at 01:15 on the 24th."""
    seconds = seconds_since_midnight("25:15:00")
    assert anchor_datetime(seconds, SERVICE_DATE) == datetime(2026, 8, 24, 1, 15, 0)


def test_round_trip_holds_across_the_full_gtfs_range():
    for time_str in ["00:00:00", "09:02:00", "23:59:59", "25:15:00", "27:45:30"]:
        anchored = anchor_datetime(seconds_since_midnight(time_str), SERVICE_DATE)
        assert anchored == parse_gtfs_time(time_str, SERVICE_DATE)


# --- parse_gtfs_time ----------------------------------------------------------


def test_parse_gtfs_time_composes_the_two_primitives():
    assert parse_gtfs_time("09:02:00", SERVICE_DATE) == datetime(2026, 8, 23, 9, 2, 0)


# --- WEEKDAY_COLUMNS ----------------------------------------------------------


def test_weekday_columns_are_indexed_by_datetime_weekday():
    """Both callers index this list with date.weekday(), so the order is the
    contract, not just the contents."""
    # 2026-08-24 is a Monday.
    for offset, expected in enumerate(WEEKDAY_COLUMNS):
        day = date(2026, 8, 24 + offset)
        assert WEEKDAY_COLUMNS[day.weekday()] == expected


def test_weekday_columns_match_the_gtfs_calendar_column_names():
    assert WEEKDAY_COLUMNS == [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
