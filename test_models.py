"""Validates mock_data.json against the Pydantic models in models.py."""

import json
from pathlib import Path

import pytest

from models import Leg, MockDataset

MOCK_DATA_PATH = Path(__file__).parent / "mock_data.json"


@pytest.fixture
def raw_data() -> dict:
    return json.loads(MOCK_DATA_PATH.read_text())


def test_mock_data_loads_and_validates(raw_data):
    dataset = MockDataset.model_validate(raw_data)
    assert len(dataset.stations) == 11
    assert len(dataset.lines) == 8
    assert len(dataset.legs) == 8
    assert len(dataset.transfers) == 4
    assert len(dataset.routes) == 4


def test_station_ids_are_unique(raw_data):
    dataset = MockDataset.model_validate(raw_data)
    station_ids = [s.station_id for s in dataset.stations]
    assert len(station_ids) == len(set(station_ids))


def test_leg_delay_distribution_sums_to_one(raw_data):
    dataset = MockDataset.model_validate(raw_data)
    for leg in dataset.legs:
        assert sum(leg.delay_distribution_minutes.values()) == pytest.approx(1.0)


def test_leg_rejects_distribution_not_summing_to_one():
    bad_leg = {
        "leg_id": "L_BAD",
        "line_id": "ICE_15",
        "origin_station_id": "DE_FRA_HBF",
        "destination_station_id": "DE_KOL_HBF",
        "scheduled_departure": "2026-08-23T09:02:00",
        "scheduled_arrival": "2026-08-23T10:14:00",
        "delay_distribution_minutes": {"0": 0.5, "5": 0.2},
    }
    with pytest.raises(ValueError):
        Leg.model_validate(bad_leg)


def test_transfer_references_valid_legs(raw_data):
    dataset = MockDataset.model_validate(raw_data)
    leg_ids = {leg.leg_id for leg in dataset.legs}
    for transfer in dataset.transfers:
        assert transfer.from_leg_id in leg_ids
        assert transfer.to_leg_id in leg_ids


def test_route_references_valid_legs_and_transfers(raw_data):
    dataset = MockDataset.model_validate(raw_data)
    leg_ids = {leg.leg_id for leg in dataset.legs}
    transfer_ids = {t.transfer_id for t in dataset.transfers}
    for route in dataset.routes:
        assert all(leg_id in leg_ids for leg_id in route.legs)
        assert all(t_id in transfer_ids for t_id in route.transfers)
