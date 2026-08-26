"""Tests for pipelines/gtfs_ingest.py against a small hand-built GTFS fixture.

Covers DATA_SPEC.md §3 steps 3-4 (Stations, Lines) and the §3.1 line-type
normalization hard requirement.
"""

from datetime import date, datetime
from pathlib import Path

import pytest

from pipelines.gtfs_ingest import (
    LINE_TYPES,
    MCT_MAJOR_HUB_MINUTES,
    MCT_STANDARD_MINUTES,
    _line_id_for_route,
    _normalize_line_type,
    classify_station_mct,
    derive_transfers,
    parse_legs,
    parse_lines,
    parse_stations,
)

FIXTURE_DIR = Path(__file__).parent.parent / "data" / "fixtures" / "gtfs_mini"
SERVICE_DATE = date(2026, 8, 23)


def test_parse_stations_keeps_only_parent_stations():
    stations = parse_stations(FIXTURE_DIR)
    station_ids = {s.station_id for s in stations}
    assert station_ids == {"DE_FRA_HBF", "DE_KOL_HBF", "DE_MUC_HBF"}


def test_parse_stations_maps_name():
    stations = parse_stations(FIXTURE_DIR)
    by_id = {s.station_id: s.name for s in stations}
    assert by_id["DE_FRA_HBF"] == "Frankfurt(Main) Hbf"
    assert by_id["DE_KOL_HBF"] == "Köln Hbf"


def test_parse_lines_returns_one_line_per_route():
    """line_id is derived from route_short_name, not the raw GTFS route_id,
    since it's displayed verbatim in ui_components.py's timeline."""
    lines = parse_lines(FIXTURE_DIR)
    line_ids = {line.line_id for line in lines}
    assert line_ids == {"ICE 15", "IC 61", "RE 1", "RB 27", "S8"}


def test_parse_lines_maps_operator_from_agency():
    lines = parse_lines(FIXTURE_DIR)
    by_id = {line.line_id: line.operator for line in lines}
    assert by_id["ICE 15"] == "DB Fernverkehr"
    assert by_id["RE 1"] == "DB Regio"


def test_parse_lines_normalizes_type_strings_exactly():
    """DATA_SPEC.md §3.1: types must match engine.py's SERVICE_FREQUENCY_MINUTES keys."""
    lines = parse_lines(FIXTURE_DIR)
    by_id = {line.line_id: line.type for line in lines}
    assert by_id["ICE 15"] == "ICE"
    assert by_id["IC 61"] == "IC"
    assert by_id["RE 1"] == "RE"
    assert by_id["RB 27"] == "RB"
    assert by_id["S8"] == "S-Bahn"


@pytest.mark.parametrize(
    "route_short_name,route_id,expected",
    [
        ("ICE 15", "ROUTE_ICE15", "ICE 15"),
        ("S8", "ROUTE_S8", "S8"),
        ("RE   1", "ROUTE_RE1", "RE 1"),  # collapses runs of internal whitespace
        ("", "ROUTE_WEIRD", "ROUTE_WEIRD"),  # blank short_name falls back to route_id
    ],
)
def test_line_id_for_route_prefers_display_friendly_short_name(
    route_short_name, route_id, expected
):
    assert _line_id_for_route(route_short_name, route_id) == expected


def test_parse_lines_all_types_are_valid_members():
    lines = parse_lines(FIXTURE_DIR)
    assert all(line.type in LINE_TYPES for line in lines)


@pytest.mark.parametrize(
    "route_short_name,route_type,expected",
    [
        ("ICE 15", "101", "ICE"),
        ("ice 91", "101", "ICE"),
        ("IC 61", "102", "IC"),
        ("EC 8", "102", "IC"),
        ("RE 1", "106", "RE"),
        ("RB 27", "106", "RB"),
        ("S8", "109", "S-Bahn"),
        ("s41", "109", "S-Bahn"),
        ("", "101", "ICE"),  # falls back to route_type when short_name is unusable
        ("", "109", "S-Bahn"),
    ],
)
def test_normalize_line_type_accepts_known_forms(route_short_name, route_type, expected):
    assert _normalize_line_type(route_short_name, route_type) == expected


def test_normalize_line_type_rejects_unrecognized_route():
    """§3.1: an unrecognized spelling must fail loudly, not reach engine.py."""
    with pytest.raises(ValueError):
        _normalize_line_type("Bus 42", "3")


def test_normalize_line_type_rejects_ambiguous_regional_without_short_name():
    """route_type 106 alone can't distinguish RE from RB (§3.1 note)."""
    with pytest.raises(ValueError):
        _normalize_line_type("", "106")


# --- parse_legs -----------------------------------------------------------


def test_parse_legs_walks_stop_times_in_sequence():
    """Trip T2 has 3 stops -> 2 consecutive-pair legs, in stop_sequence order."""
    legs = parse_legs(FIXTURE_DIR, SERVICE_DATE)
    t2_legs = sorted((leg for leg in legs if leg.leg_id.startswith("T2::")), key=lambda l: l.leg_id)
    assert len(t2_legs) == 2
    assert t2_legs[0].origin_station_id == "DE_FRA_HBF"
    assert t2_legs[0].destination_station_id == "DE_KOL_HBF"
    assert t2_legs[1].origin_station_id == "DE_KOL_HBF"
    assert t2_legs[1].destination_station_id == "DE_MUC_HBF"


def test_parse_legs_maps_platform_stops_to_parent_station():
    legs = parse_legs(FIXTURE_DIR, SERVICE_DATE)
    t1_leg = next(leg for leg in legs if leg.leg_id == "T1::0")
    assert t1_leg.origin_station_id == "DE_FRA_HBF"
    assert t1_leg.destination_station_id == "DE_KOL_HBF"


def test_parse_legs_sets_scheduled_times_from_feed():
    legs = parse_legs(FIXTURE_DIR, SERVICE_DATE)
    t1_leg = next(leg for leg in legs if leg.leg_id == "T1::0")
    assert t1_leg.scheduled_departure == datetime(2026, 8, 23, 9, 2, 0)
    assert t1_leg.scheduled_arrival == datetime(2026, 8, 23, 10, 14, 0)


def test_parse_legs_sets_platform_when_available():
    """T1::0 runs FRA (Gleis 7) -> KOL (Gleis 3) -- both endpoints in
    data/fixtures/gtfs_mini/stops.txt carry a platform_code."""
    legs = parse_legs(FIXTURE_DIR, SERVICE_DATE)
    t1_leg = next(leg for leg in legs if leg.leg_id == "T1::0")
    assert t1_leg.origin_platform == "7"
    assert t1_leg.destination_platform == "3"


def test_parse_legs_platform_is_none_when_unavailable():
    """T2::1 arrives at DE_MUC_HBF, which has no platform-level child stop
    in the fixture -- destination_platform must be None, not a placeholder."""
    legs = parse_legs(FIXTURE_DIR, SERVICE_DATE)
    t2_leg1 = next(leg for leg in legs if leg.leg_id == "T2::1")
    assert t2_leg1.origin_platform == "3"
    assert t2_leg1.destination_platform is None


def test_parse_legs_uses_placeholder_delay_distribution():
    legs = parse_legs(FIXTURE_DIR, SERVICE_DATE)
    for leg in legs:
        assert leg.delay_distribution_minutes == {"0": 1.0}


def test_parse_legs_total_count_matches_fixture():
    # T1: 2 stops -> 1 leg; T2: 3 stops -> 2 legs; T3/T4/T5: 2 stops -> 1 leg each.
    legs = parse_legs(FIXTURE_DIR, SERVICE_DATE)
    assert len(legs) == 6


# --- derive_transfers -------------------------------------------------------


def test_derive_transfers_finds_valid_cross_trip_transfer():
    """T1 arrives Köln 10:14, T3 departs Köln 10:25 -> an 11-minute transfer."""
    legs = parse_legs(FIXTURE_DIR, SERVICE_DATE)
    transfers = derive_transfers(legs)
    matches = [t for t in transfers if t.from_leg_id == "T1::0" and t.to_leg_id == "T3::0"]
    assert len(matches) == 1
    assert matches[0].station_id == "DE_KOL_HBF"
    assert matches[0].scheduled_buffer_minutes == 11


def test_derive_transfers_excludes_same_trip_continuation():
    """T2's own Köln arrival (08:00) -> Köln departure (08:05) is a 5-minute
    gap that would qualify by window alone, but it's the same trip continuing
    through the station, not a transfer."""
    legs = parse_legs(FIXTURE_DIR, SERVICE_DATE)
    transfers = derive_transfers(legs)
    assert not any(t.from_leg_id == "T2::0" and t.to_leg_id == "T2::1" for t in transfers)


def test_derive_transfers_excludes_below_min_window():
    """T4 departs Köln only 1 minute after T1 arrives -> below the 2-minute floor."""
    legs = parse_legs(FIXTURE_DIR, SERVICE_DATE)
    transfers = derive_transfers(legs)
    assert not any(t.from_leg_id == "T1::0" and t.to_leg_id == "T4::0" for t in transfers)


def test_derive_transfers_excludes_above_max_window():
    """T5 departs Köln 106 minutes after T1 arrives -> above the 60-minute ceiling."""
    legs = parse_legs(FIXTURE_DIR, SERVICE_DATE)
    transfers = derive_transfers(legs)
    assert not any(t.from_leg_id == "T1::0" and t.to_leg_id == "T5::0" for t in transfers)


def test_derive_transfers_total_count_matches_fixture():
    legs = parse_legs(FIXTURE_DIR, SERVICE_DATE)
    transfers = derive_transfers(legs)
    assert len(transfers) == 1


def test_derive_transfers_buffer_respects_custom_window():
    """Widening the window to include the 1-minute gap surfaces the T1->T4 pair."""
    legs = parse_legs(FIXTURE_DIR, SERVICE_DATE)
    transfers = derive_transfers(legs, min_window_minutes=1, max_window_minutes=60)
    assert any(t.from_leg_id == "T1::0" and t.to_leg_id == "T4::0" for t in transfers)


# --- end-to-end line-type normalization ------------------------------------


def test_line_type_normalization_end_to_end_via_legs():
    """§3.1, end-to-end: every Leg's line_id resolves to a Line whose type is
    an exact SERVICE_FREQUENCY_MINUTES key, for every line type in the feed."""
    lines_by_id = {line.line_id: line.type for line in parse_lines(FIXTURE_DIR)}
    legs = parse_legs(FIXTURE_DIR, SERVICE_DATE)

    seen_types = set()
    for leg in legs:
        assert leg.line_id in lines_by_id
        line_type = lines_by_id[leg.line_id]
        assert line_type in LINE_TYPES
        seen_types.add(line_type)

    assert seen_types == LINE_TYPES  # fixture exercises all five line types


# --- classify_station_mct ----------------------------------------------------


def test_classify_station_mct_returns_empty_for_no_touches():
    assert classify_station_mct([]) == {}


def test_classify_station_mct_uniform_distribution_all_qualify_as_major_hub():
    """Every station touched the same number of times: the 75th-percentile
    threshold equals every station's own count, so every station is >= the
    threshold and gets the major-hub tier -- there's no "below threshold"
    station in a perfectly uniform distribution."""
    pairs = [("A", "B"), ("C", "D")]
    result = classify_station_mct(pairs)
    assert set(result) == {"A", "B", "C", "D"}
    assert all(v == MCT_MAJOR_HUB_MINUTES for v in result.values())


def test_classify_station_mct_splits_a_graduated_distribution():
    """S_k gets 2*k touches via k self-pairs (k=1..12): a clean, tie-free
    spread of touch counts from 2 to 24. The 75th-percentile threshold falls
    at 18, so the top third (S_9..S_12, counts 18/20/22/24) qualify as major
    hubs and the bottom two-thirds (S_1..S_8) stay standard."""
    pairs = [(f"S_{k}", f"S_{k}") for k in range(1, 13) for _ in range(k)]
    result = classify_station_mct(pairs)
    for k in range(1, 9):
        assert result[f"S_{k}"] == MCT_STANDARD_MINUTES
    for k in range(9, 13):
        assert result[f"S_{k}"] == MCT_MAJOR_HUB_MINUTES


def test_classify_station_mct_every_station_gets_a_tier():
    pairs = [("A", "B"), ("B", "C"), ("C", "D")]
    result = classify_station_mct(pairs)
    assert set(result) == {"A", "B", "C", "D"}
    assert all(v in (MCT_STANDARD_MINUTES, MCT_MAJOR_HUB_MINUTES) for v in result.values())
