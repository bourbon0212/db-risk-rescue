"""Tests for pipelines/id_crosswalk.py (DATA_SPEC.md §8 build sequence step 4)."""

import json
from pathlib import Path

import pytest

from pipelines.id_crosswalk import GTFS_STOP_ID_TO_STATION_ID, STATION_NAMES, to_station_id

MOCK_DATA_PATH = Path(__file__).parent.parent / "mock_data.json"


def test_to_station_id_maps_known_stations():
    assert to_station_id("176697") == "DE_FRA_HBF"  # Frankfurt (Main) Hauptbahnhof
    assert to_station_id("517455") == "DE_KOL_HBF"  # Koeln Hbf
    assert to_station_id("183027") == "DE_MUC_MAR"  # Marienplatz


def test_to_station_id_raises_for_unmapped_stop():
    with pytest.raises(ValueError):
        to_station_id("GTFS_SOME_UNSCOPED_STOP")


def test_crosswalk_is_a_superset_of_the_original_mock_corridor():
    """The corridor has grown past DATA_SPEC.md §9.1's original "mirror
    mock_data.json exactly" scope (per its own "expand coverage later"
    note) -- it must still cover every original station, just not only them."""
    mock_station_ids = {
        s["station_id"] for s in json.loads(MOCK_DATA_PATH.read_text())["stations"]
    }
    corridor_station_ids = set(GTFS_STOP_ID_TO_STATION_ID.values())
    assert mock_station_ids <= corridor_station_ids
    assert len(corridor_station_ids) > len(mock_station_ids)


def test_golden_corridor_has_approximately_thirty_to_thirty_five_stations():
    assert 30 <= len(set(GTFS_STOP_ID_TO_STATION_ID.values())) <= 35


def test_crosswalk_is_legitimately_many_to_one_for_split_stations():
    """Frankfurt, Stuttgart, Leipzig, Hamburg, Erfurt, and Kassel-
    Wilhelmshöhe each span more than one real GTFS parent-station node
    (surface/tief levels, or a separate S-Bahn node) that must resolve to
    the same station_id."""
    values = list(GTFS_STOP_ID_TO_STATION_ID.values())
    assert len(values) > len(set(values))
    assert to_station_id("176697") == to_station_id("335920") == "DE_FRA_HBF"
    assert to_station_id("668361") == to_station_id("362545") == "DE_STG_HBF"
    assert to_station_id("53188") == to_station_id("601768") == "DE_LEI_HBF"
    assert to_station_id("428519") == to_station_id("52456") == "DE_HAM_HBF"
    assert to_station_id("416646") == to_station_id("166299") == "DE_ERF_HBF"


def test_newly_added_hub_stations_are_mapped():
    """Spot-check the explicitly requested hub/routing stations that were
    missing before this expansion."""
    assert to_station_id("416646") == "DE_ERF_HBF"  # Erfurt Hbf
    assert to_station_id("531677") == "DE_HAL_HBF"  # Halle (Saale) Hbf
    assert to_station_id("19112") == "DE_KAS_WIL"  # Kassel-Wilhelmshöhe


def test_station_names_covers_exactly_the_crosswalks_station_ids():
    assert set(STATION_NAMES) == set(GTFS_STOP_ID_TO_STATION_ID.values())


def test_station_names_are_unique():
    names = list(STATION_NAMES.values())
    assert len(names) == len(set(names))


def test_original_eleven_station_names_are_unchanged():
    """The original corridor's display names must stay stable across the
    expansion -- app.py users shouldn't see a station they know rename itself."""
    original_names = {
        s["station_id"]: s["name"] for s in json.loads(MOCK_DATA_PATH.read_text())["stations"]
    }
    for station_id, name in original_names.items():
        assert STATION_NAMES[station_id] == name
