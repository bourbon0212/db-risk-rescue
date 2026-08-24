"""Tests for pipelines/gtfs_ingest.py's Phase 3 date-agnostic parsers
(SPEC.md §6.2): parse_trips, parse_leg_templates, derive_transfer_templates,
and the _seconds_since_midnight/_anchor_datetime primitives they share with
the existing anchored parse_legs/derive_transfers. Uses the same
fixtures/gtfs_mini/ fixture as test_gtfs_ingest.py so results can be checked
for exact parity with the already-trusted anchored output.
"""

from datetime import date, datetime
from pathlib import Path

from pipelines.gtfs_ingest import (
    _anchor_datetime,
    _seconds_since_midnight,
    derive_transfer_templates,
    derive_transfers,
    parse_leg_templates,
    parse_legs,
    parse_trips,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gtfs_mini"
SERVICE_DATE = date(2026, 8, 23)


# --- time-parsing primitives -------------------------------------------------


def test_seconds_since_midnight_parses_hms():
    assert _seconds_since_midnight("09:02:00") == 9 * 3600 + 2 * 60


def test_seconds_since_midnight_handles_post_midnight_hours():
    assert _seconds_since_midnight("25:15:00") == 25 * 3600 + 15 * 60


def test_anchor_datetime_is_the_inverse():
    seconds = _seconds_since_midnight("09:02:00")
    assert _anchor_datetime(seconds, SERVICE_DATE) == datetime(2026, 8, 23, 9, 2, 0)


# --- parse_trips --------------------------------------------------------------


def test_parse_trips_maps_trip_to_line_and_service():
    trips = parse_trips(FIXTURE_DIR)
    by_id = {t.trip_id: t for t in trips}
    assert by_id["T1"].line_id == "ICE 15"
    assert by_id["T1"].service_id == "WD"
    assert len(trips) == 5  # T1..T5


# --- parse_leg_templates: parity with parse_legs -----------------------------


def test_leg_templates_match_anchored_legs_when_re_anchored():
    """Materializing every LegTemplate back onto SERVICE_DATE must reproduce
    exactly what the already-trusted parse_legs(FIXTURE_DIR, SERVICE_DATE)
    produces -- the whole point of the template design is that it's a
    lossless, date-agnostic re-encoding of the same information."""
    templates = parse_leg_templates(FIXTURE_DIR)
    legs = parse_legs(FIXTURE_DIR, SERVICE_DATE)

    templates_by_id = {t.leg_id: t for t in templates}
    assert set(templates_by_id) == {leg.leg_id for leg in legs}

    for leg in legs:
        template = templates_by_id[leg.leg_id]
        assert template.line_id == leg.line_id
        assert template.origin_station_id == leg.origin_station_id
        assert template.destination_station_id == leg.destination_station_id
        assert _anchor_datetime(template.departure_seconds, SERVICE_DATE) == leg.scheduled_departure
        assert _anchor_datetime(template.arrival_seconds, SERVICE_DATE) == leg.scheduled_arrival


def test_leg_templates_carry_trip_id_and_sequence_index():
    templates = parse_leg_templates(FIXTURE_DIR)
    t2_templates = sorted(
        (t for t in templates if t.trip_id == "T2"), key=lambda t: t.sequence_index
    )
    assert len(t2_templates) == 2
    assert [t.sequence_index for t in t2_templates] == [0, 1]
    assert [t.leg_id for t in t2_templates] == ["T2::0", "T2::1"]


# --- derive_transfer_templates: parity with derive_transfers -----------------


def test_transfer_templates_match_derived_transfers():
    templates = parse_leg_templates(FIXTURE_DIR)
    transfer_templates = derive_transfer_templates(templates)

    legs = parse_legs(FIXTURE_DIR, SERVICE_DATE)
    transfers = derive_transfers(legs)

    assert len(transfer_templates) == len(transfers) == 1
    tt, t = transfer_templates[0], transfers[0]
    assert tt.from_leg_id == t.from_leg_id == "T1::0"
    assert tt.to_leg_id == t.to_leg_id == "T3::0"
    assert tt.station_id == t.station_id == "DE_KOL_HBF"
    assert tt.buffer_minutes == t.scheduled_buffer_minutes == 11


def test_transfer_templates_carry_parent_trip_ids():
    templates = parse_leg_templates(FIXTURE_DIR)
    transfer_templates = derive_transfer_templates(templates)
    tt = transfer_templates[0]
    assert tt.from_trip_id == "T1"
    assert tt.to_trip_id == "T3"


def test_transfer_templates_exclude_same_trip_continuation():
    templates = parse_leg_templates(FIXTURE_DIR)
    transfer_templates = derive_transfer_templates(templates)
    assert not any(t.from_leg_id == "T2::0" and t.to_leg_id == "T2::1" for t in transfer_templates)


def test_transfer_templates_respect_custom_window():
    templates = parse_leg_templates(FIXTURE_DIR)
    transfer_templates = derive_transfer_templates(
        templates, min_window_minutes=1, max_window_minutes=60
    )
    assert any(t.from_leg_id == "T1::0" and t.to_leg_id == "T4::0" for t in transfer_templates)
