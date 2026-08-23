"""Tests for pipelines/id_crosswalk.py (DATA_SPEC.md §8 build sequence step 4)."""

import json
from pathlib import Path

import pytest

from pipelines.id_crosswalk import GTFS_STOP_ID_TO_STATION_ID, to_station_id

MOCK_DATA_PATH = Path(__file__).parent / "mock_data.json"


def test_to_station_id_maps_known_stations():
    assert to_station_id("176697") == "DE_FRA_HBF"  # Frankfurt (Main) Hauptbahnhof
    assert to_station_id("517455") == "DE_KOL_HBF"  # Koeln Hbf
    assert to_station_id("183027") == "DE_MUC_MAR"  # Marienplatz


def test_to_station_id_raises_for_unmapped_stop():
    with pytest.raises(ValueError):
        to_station_id("GTFS_SOME_UNSCOPED_STOP")


def test_crosswalk_mirrors_mock_data_station_set():
    """DATA_SPEC.md §7.1: Phase 2's first build should mirror mock_data.json's
    existing 11-station corridor exactly."""
    mock_station_ids = {
        s["station_id"] for s in json.loads(MOCK_DATA_PATH.read_text())["stations"]
    }
    assert set(GTFS_STOP_ID_TO_STATION_ID.values()) == mock_station_ids


def test_crosswalk_is_legitimately_many_to_one_for_split_stations():
    """Frankfurt, Stuttgart, and Leipzig Hbf each span more than one real
    GTFS parent-station node (surface/tief levels) that must resolve to the
    same station_id."""
    values = list(GTFS_STOP_ID_TO_STATION_ID.values())
    assert len(values) > len(set(values))
    assert to_station_id("176697") == to_station_id("335920") == "DE_FRA_HBF"
    assert to_station_id("668361") == to_station_id("362545") == "DE_STG_HBF"
    assert to_station_id("53188") == to_station_id("601768") == "DE_LEI_HBF"
