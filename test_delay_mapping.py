"""Tests for pipelines/delay_mapping.py against a small synthetic DataFrame
shaped like piebro/deutsche-bahn-data's real schema (confirmed against the
downloaded Parquet file, not the dataset-card preview -- see the module
docstring for specifics)."""

import pandas as pd
import pytest

from pipelines.delay_mapping import PIEBRO_TRAIN_TYPE_TO_LINE_TYPE, map_piebro_records


def _row(
    train_type,
    train_number=None,
    line_number=None,
    arrival_planned="2026-07-15T10:00:00",
    arrival_change="2026-07-15T10:00:00",
    arrival_is_canceled=False,
    delay_in_min=0,
):
    return {
        "train_type": train_type,
        "train_number": train_number,
        "line_number": line_number,
        "arrival_planned_time": pd.Timestamp(arrival_planned) if arrival_planned else pd.NaT,
        "arrival_change_time": pd.Timestamp(arrival_change) if arrival_change else pd.NaT,
        "arrival_is_canceled": arrival_is_canceled,
        "delay_in_min": delay_in_min,  # present in real data; must NOT be trusted (see docstring)
    }


def test_long_distance_line_id_is_type_space_number():
    df = pd.DataFrame(
        [
            _row("ICE", train_number="615"),
            _row("IC", train_number="2385"),
            _row("EC", train_number="459"),
            _row("ECE", train_number="20"),
        ]
    )
    mapped = map_piebro_records(df)
    assert set(mapped["line_id"]) == {"ICE 615", "IC 2385", "EC 459", "ECE 20"}


def test_long_distance_line_type_folds_ec_and_ece_into_ic():
    df = pd.DataFrame([_row("EC", train_number="459"), _row("ECE", train_number="20")])
    mapped = map_piebro_records(df)
    assert set(mapped["line_type"]) == {"IC"}


def test_regional_line_id_uses_line_number_when_already_prefixed():
    df = pd.DataFrame(
        [
            _row("RE", train_number="4249", line_number="RE5"),
            _row("RB", train_number="15926", line_number="RB44"),
        ]
    )
    mapped = map_piebro_records(df)
    assert set(mapped["line_id"]) == {"RE5", "RB44"}


def test_regional_line_id_prepends_type_when_line_number_is_bare():
    """Some rows (observed for S-Bahn especially) have a bare-digit
    line_number with no type prefix at all."""
    df = pd.DataFrame(
        [
            _row("S", train_number="8348", line_number="3"),
            _row("RE", train_number="1", line_number="12"),
        ]
    )
    mapped = map_piebro_records(df)
    assert set(mapped["line_id"]) == {"S3", "RE12"}


def test_regional_line_type_maps_s_to_s_bahn():
    df = pd.DataFrame([_row("S", train_number="8348", line_number="S8")])
    mapped = map_piebro_records(df)
    assert mapped["line_type"].tolist() == ["S-Bahn"]


def test_unsupported_train_type_is_dropped():
    df = pd.DataFrame(
        [_row("Bus", train_number="1"), _row("MEX", line_number="MEX12"), _row("ICE", train_number="1")]
    )
    mapped = map_piebro_records(df)
    assert len(mapped) == 1
    assert mapped["line_id"].iloc[0] == "ICE 1"


def test_row_with_no_arrival_event_is_dropped():
    """The train's own origin station has no arrival_planned/change time."""
    df = pd.DataFrame(
        [
            _row("ICE", train_number="1", arrival_planned=None, arrival_change=None),
            _row("ICE", train_number="2"),
        ]
    )
    mapped = map_piebro_records(df)
    assert len(mapped) == 1
    assert mapped["line_id"].iloc[0] == "ICE 2"


def test_canceled_arrival_is_dropped():
    df = pd.DataFrame(
        [
            _row("ICE", train_number="1", arrival_is_canceled=True),
            _row("ICE", train_number="2", arrival_is_canceled=False),
        ]
    )
    mapped = map_piebro_records(df)
    assert len(mapped) == 1
    assert mapped["line_id"].iloc[0] == "ICE 2"


def test_arrival_delay_computed_from_timestamps_not_delay_in_min():
    """delay_in_min is present in real data but is NOT reliably arrival
    delay (see module docstring) -- it must be ignored entirely."""
    df = pd.DataFrame(
        [
            _row(
                "ICE",
                train_number="1",
                arrival_planned="2026-07-15T10:00:00",
                arrival_change="2026-07-15T10:12:00",
                delay_in_min=999,  # deliberately wrong / must be ignored
            )
        ]
    )
    mapped = map_piebro_records(df)
    assert mapped["arrival_delay_minutes"].iloc[0] == pytest.approx(12.0)
    assert "delay_in_min" not in mapped.columns


def test_target_line_ids_filters_output():
    df = pd.DataFrame(
        [_row("ICE", train_number="1"), _row("ICE", train_number="2"), _row("RE", line_number="RE5")]
    )
    mapped = map_piebro_records(df, target_line_ids={"ICE 1"})
    assert mapped["line_id"].tolist() == ["ICE 1"]


def test_mapped_output_has_exactly_the_expected_columns():
    df = pd.DataFrame([_row("ICE", train_number="1")])
    mapped = map_piebro_records(df)
    assert set(mapped.columns) == {"line_id", "line_type", "arrival_delay_minutes"}


def test_piebro_train_type_to_line_type_covers_exactly_our_five_gtfs_types():
    from pipelines.gtfs_ingest import LINE_TYPES

    assert set(PIEBRO_TRAIN_TYPE_TO_LINE_TYPE.values()) == set(LINE_TYPES)
