"""Tests for gtfs_ingest.py's corridor-aware leg builders (parse_corridor_legs/
parse_corridor_leg_templates), added to fix a real over-aggressive-filtering
bug: parse_legs/parse_leg_templates emit a leg per physically-consecutive stop
pair, so any trip with a non-corridor stop physically between two corridor
hubs produced legs where *neither* endpoint was a corridor station -- and the
old "keep only corridor-to-corridor legs" post-hoc filter then dropped all of
them, silently disconnecting the hubs instead of just modeling the extra
stop. fixtures/gtfs_corridor_gap/ has exactly that shape: trip T_GAP runs
A -> B -> C, where B is deliberately not a corridor station.
"""

from datetime import date, datetime

from pathlib import Path

from pipelines.gtfs_ingest import (
    parse_corridor_leg_templates,
    parse_corridor_legs,
    parse_leg_templates,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gtfs_corridor_gap"
SERVICE_DATE = date(2026, 8, 29)
CORRIDOR_STOP_IDS = {"DE_A", "DE_C"}  # DE_B is deliberately excluded


def test_old_adjacency_only_approach_loses_the_connection():
    """Documents the bug being fixed: parse_leg_templates() only ever
    produces A->B and B->C (physically-adjacent pairs), so a post-hoc
    "both endpoints must be a corridor station" filter keeps nothing at all
    for this trip -- A and C become completely disconnected even though the
    train visibly runs between them."""
    raw_templates = parse_leg_templates(FIXTURE_DIR)
    assert {t.leg_id for t in raw_templates} == {"T_GAP::0", "T_GAP::1"}

    survivors = [
        t
        for t in raw_templates
        if t.origin_station_id in CORRIDOR_STOP_IDS
        and t.destination_station_id in CORRIDOR_STOP_IDS
    ]
    assert survivors == []


def test_parse_corridor_leg_templates_skips_the_non_corridor_stop():
    templates = parse_corridor_leg_templates(FIXTURE_DIR, CORRIDOR_STOP_IDS)
    assert len(templates) == 1

    template = templates[0]
    assert template.leg_id == "T_GAP::0"
    assert template.trip_id == "T_GAP"
    assert template.origin_station_id == "DE_A"
    assert template.destination_station_id == "DE_C"
    assert template.departure_seconds == 8 * 3600
    assert template.arrival_seconds == 9 * 3600


def test_parse_corridor_legs_skips_the_non_corridor_stop():
    legs = parse_corridor_legs(FIXTURE_DIR, SERVICE_DATE, CORRIDOR_STOP_IDS)
    assert len(legs) == 1

    leg = legs[0]
    assert leg.leg_id == "T_GAP::0"
    assert leg.origin_station_id == "DE_A"
    assert leg.destination_station_id == "DE_C"
    assert leg.scheduled_departure == datetime(2026, 8, 29, 8, 0, 0)
    assert leg.scheduled_arrival == datetime(2026, 8, 29, 9, 0, 0)
