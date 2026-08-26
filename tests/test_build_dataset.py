"""Smoke test for pipelines/build_dataset.py (DATA_SPEC.md §8 build sequence
step 5): runs the full pipeline against fixtures/gtfs_smoke/ and a synthetic
historical-delay DataFrame, and checks the assembled MockDataset end to end.
"""

import json
from datetime import datetime

import pytest

from models import Leg, MockDataset
from pipelines.build_dataset import (
    DEMO_GTFS_DIR,
    DEMO_SERVICE_DATE,
    _dedupe_legs,
    _find_latest_delay_parquet,
    build_dataset,
    demo_historical_delays,
    write_dataset,
)
from pipelines.gtfs_ingest import LINE_TYPES

FIXTURE_DIR = __import__("pathlib").Path(__file__).parent.parent / "fixtures" / "gtfs_mini"


@pytest.fixture(scope="module")
def dataset() -> MockDataset:
    return build_dataset(DEMO_GTFS_DIR, demo_historical_delays(), DEMO_SERVICE_DATE)


def test_build_dataset_returns_a_valid_mockdataset(dataset):
    assert isinstance(dataset, MockDataset)


def test_stations_are_crosswalked_to_final_station_ids(dataset):
    station_ids = {s.station_id for s in dataset.stations}
    assert station_ids == {"DE_FRA_HBF", "DE_KOL_HBF", "DE_MUC_HBF"}


def test_lines_all_have_valid_normalized_types(dataset):
    assert len(dataset.lines) == 5
    assert all(line.type in LINE_TYPES for line in dataset.lines)


def test_legs_reference_crosswalked_station_ids(dataset):
    assert len(dataset.legs) == 6
    station_ids = {s.station_id for s in dataset.stations}
    for leg in dataset.legs:
        assert leg.origin_station_id in station_ids
        assert leg.destination_station_id in station_ids


def test_transfers_are_derived_and_crosswalked(dataset):
    assert len(dataset.transfers) == 1
    transfer = dataset.transfers[0]
    assert transfer.station_id == "DE_KOL_HBF"
    assert transfer.scheduled_buffer_minutes == 11


def test_well_sampled_lines_keep_their_own_distribution(dataset):
    legs_by_line = {leg.line_id: leg for leg in dataset.legs}
    ice15 = legs_by_line["ICE 15"].delay_distribution_minutes
    assert ice15 == pytest.approx({"0": 25 / 35, "5": 0.0, "15": 10 / 35, "30": 0.0, "60": 0.0})
    re1 = legs_by_line["RE 1"].delay_distribution_minutes
    assert re1 == {"0": 1.0, "5": 0.0, "15": 0.0, "30": 0.0, "60": 0.0}


def test_undersampled_lines_get_the_pooled_fallback_distribution(dataset):
    legs_by_line = {leg.line_id: leg for leg in dataset.legs}
    ic61 = legs_by_line["IC 61"].delay_distribution_minutes
    assert ic61 == pytest.approx({"0": 25 / 39, "5": 4 / 39, "15": 10 / 39, "30": 0.0, "60": 0.0})
    rb27 = legs_by_line["RB 27"].delay_distribution_minutes
    assert rb27 == pytest.approx({"0": 30 / 38, "5": 8 / 38, "15": 0.0, "30": 0.0, "60": 0.0})


def test_every_leg_distribution_sums_to_one(dataset):
    for leg in dataset.legs:
        assert sum(leg.delay_distribution_minutes.values()) == pytest.approx(1.0)


def test_routes_are_empty_until_route_search_is_built(dataset):
    """Route generation is DATA_SPEC.md build-sequence step 6, not yet built."""
    assert dataset.routes == []


def test_build_dataset_raises_for_a_feed_outside_the_crosswalked_corridor():
    """fixtures/gtfs_mini/ uses raw stop_ids ("DE_FRA_HBF", ...) that are
    final-form ids, not the GTFS_PLACEHOLDER_* keys id_crosswalk knows about
    -- so running it through build_dataset must fail loudly, not silently
    pass unmapped ids through."""
    with pytest.raises(ValueError):
        build_dataset(FIXTURE_DIR, demo_historical_delays(), DEMO_SERVICE_DATE)


def test_write_dataset_produces_a_file_that_round_trips(dataset, tmp_path):
    output_path = tmp_path / "real_dataset.json"
    write_dataset(dataset, output_path)
    assert output_path.exists()
    reloaded = MockDataset.model_validate(json.loads(output_path.read_text()))
    assert len(reloaded.legs) == len(dataset.legs)


# --- _find_latest_delay_parquet ---------------------------------------------


def test_find_latest_delay_parquet_picks_the_most_recent_month(tmp_path):
    (tmp_path / "delays_2026-05.parquet").touch()
    (tmp_path / "delays_2026-07.parquet").touch()
    (tmp_path / "delays_2026-06.parquet").touch()
    assert _find_latest_delay_parquet(tmp_path).name == "delays_2026-07.parquet"


def test_find_latest_delay_parquet_returns_none_when_absent(tmp_path):
    assert _find_latest_delay_parquet(tmp_path) is None


# --- _dedupe_legs ------------------------------------------------------------


def _leg(leg_id, line_id="L1", origin="A", dest="B", dep="09:00", arr="10:00"):
    return Leg(
        leg_id=leg_id,
        line_id=line_id,
        origin_station_id=origin,
        destination_station_id=dest,
        scheduled_departure=datetime.fromisoformat(f"2026-08-24T{dep}"),
        scheduled_arrival=datetime.fromisoformat(f"2026-08-24T{arr}"),
        delay_distribution_minutes={"0": 1.0},
    )


def test_dedupe_legs_drops_exact_duplicates_from_different_trip_ids():
    """Same line/stations/times but different leg_id (i.e. different raw
    trip_id) -- the real GTFS.DE duplicate-trip-record quirk."""
    legs = [_leg("1023960::10"), _leg("590484::7")]
    deduped = _dedupe_legs(legs)
    assert len(deduped) == 1
    assert deduped[0].leg_id == "1023960::10"  # keeps the first occurrence


def test_dedupe_legs_keeps_legs_that_differ_in_any_field():
    legs = [
        _leg("1", line_id="L1"),
        _leg("2", line_id="L2"),  # different line
        _leg("3", origin="C"),  # different origin
        _leg("4", dep="11:00"),  # different departure time
    ]
    assert len(_dedupe_legs(legs)) == 4
