"""Tests for pipelines/delay_aggregation.py against synthetic in-memory
historical delay records (DATA_SPEC.md §4, §8 build sequence step 3).
"""

import pandas as pd
import pytest

from pipelines.delay_aggregation import (
    BUCKET_LABELS,
    DEFAULT_MIN_SAMPLES,
    aggregate_delay_distributions,
    bucket_delay,
    build_delay_distributions,
    compute_realized_delay,
)


def _rows(line_id: str, line_type: str, delay_minutes: float, count: int) -> list[dict]:
    return [
        {"line_id": line_id, "line_type": line_type, "arrival_delay_minutes": delay_minutes}
        for _ in range(count)
    ]


@pytest.fixture
def raw_records() -> pd.DataFrame:
    """A synthetic dataset covering both the well-sampled and fallback paths
    across all three DATA_SPEC.md §4 step 5 pooling groups.

    - ICE_100: 40 occurrences (>= threshold) -> uses its own distribution.
    - IC_200: 5 occurrences (< threshold) -> falls back to the pooled
      ICE_IC group (ICE_100 + IC_200).
    - RE_300: 35 occurrences (>= threshold) -> uses its own distribution.
    - RB_400: 10 occurrences (< threshold) -> falls back to the pooled
      RE_RB group (RE_300 + RB_400).
    - S_1, S_2: 8 and 12 occurrences (both < threshold), with deliberately
      different raw distributions -> both fall back to the *same* pooled
      S-Bahn group and so end up with identical distributions.
    """
    rows = []
    rows += _rows("ICE_100", "ICE", 0, 20)
    rows += _rows("ICE_100", "ICE", 5, 10)
    rows += _rows("ICE_100", "ICE", 20, 6)
    rows += _rows("ICE_100", "ICE", 45, 3)
    rows += _rows("ICE_100", "ICE", 90, 1)

    rows += _rows("IC_200", "IC", 0, 2)
    rows += _rows("IC_200", "IC", 5, 1)
    rows += _rows("IC_200", "IC", 20, 1)
    rows += _rows("IC_200", "IC", 45, 1)

    rows += _rows("RE_300", "RE", 0, 35)

    rows += _rows("RB_400", "RB", 5, 10)

    rows += _rows("S_1", "S-Bahn", 0, 8)
    rows += _rows("S_2", "S-Bahn", 5, 12)

    return pd.DataFrame(rows)


# --- bucket_delay -----------------------------------------------------------


@pytest.mark.parametrize(
    "delay_minutes,expected_bucket",
    [
        (-5, "0"),
        (0, "0"),
        (4.9, "0"),
        (5, "5"),
        (14, "5"),
        (15, "15"),
        (29, "15"),
        (30, "30"),
        (59, "30"),
        (60, "60"),
        (500, "60"),
    ],
)
def test_bucket_delay_floors_to_boundary_not_exceeding(delay_minutes, expected_bucket):
    assert bucket_delay(delay_minutes) == expected_bucket


# --- compute_realized_delay ---------------------------------------------------


def test_compute_realized_delay_uses_destination_arrival_delay():
    df = pd.DataFrame({"arrival_delay_minutes": [0, 12, 45]})
    result = compute_realized_delay(df)
    assert result["realized_delay_minutes"].tolist() == [0, 12, 45]


def test_compute_realized_delay_does_not_mutate_input():
    df = pd.DataFrame({"arrival_delay_minutes": [0, 12, 45]})
    compute_realized_delay(df)
    assert "realized_delay_minutes" not in df.columns


# --- aggregate_delay_distributions: well-sampled lines use their own data ---


def test_well_sampled_line_uses_its_own_distribution():
    df = compute_realized_delay(
        pd.DataFrame(_rows("ICE_100", "ICE", 0, 20) + _rows("ICE_100", "ICE", 90, 20))
    )
    distributions = aggregate_delay_distributions(df)
    assert distributions["ICE_100"] == {"0": 0.5, "5": 0.0, "15": 0.0, "30": 0.0, "60": 0.5}


def test_aggregate_matches_hand_computed_distribution(raw_records):
    df = compute_realized_delay(raw_records)
    distributions = aggregate_delay_distributions(df)
    assert distributions["ICE_100"] == pytest.approx(
        {"0": 20 / 40, "5": 10 / 40, "15": 6 / 40, "30": 3 / 40, "60": 1 / 40}
    )
    assert distributions["RE_300"] == {"0": 1.0, "5": 0.0, "15": 0.0, "30": 0.0, "60": 0.0}


# --- aggregate_delay_distributions: fallback pooling ------------------------


def test_undersampled_line_falls_back_to_pooled_ice_ic_group(raw_records):
    df = compute_realized_delay(raw_records)
    distributions = aggregate_delay_distributions(df)
    # Pooled ICE_100 (40) + IC_200 (5) = 45 total occurrences.
    expected = {"0": 22 / 45, "5": 11 / 45, "15": 7 / 45, "30": 4 / 45, "60": 1 / 45}
    assert distributions["IC_200"] == pytest.approx(expected)


def test_undersampled_line_falls_back_to_pooled_re_rb_group(raw_records):
    df = compute_realized_delay(raw_records)
    distributions = aggregate_delay_distributions(df)
    # Pooled RE_300 (35) + RB_400 (10) = 45 total occurrences.
    expected = {"0": 35 / 45, "5": 10 / 45, "15": 0.0, "30": 0.0, "60": 0.0}
    assert distributions["RB_400"] == pytest.approx(expected)


def test_both_undersampled_s_bahn_lines_get_the_same_pooled_distribution(raw_records):
    """S_1 and S_2 have very different raw delay patterns individually, but
    both are under threshold, so both must resolve to the identical pooled
    S-Bahn distribution rather than their own data."""
    df = compute_realized_delay(raw_records)
    distributions = aggregate_delay_distributions(df)
    expected = {"0": 8 / 20, "5": 12 / 20, "15": 0.0, "30": 0.0, "60": 0.0}
    assert distributions["S_1"] == pytest.approx(expected)
    assert distributions["S_2"] == pytest.approx(expected)
    assert distributions["S_1"] == distributions["S_2"]


def test_fallback_threshold_is_configurable(raw_records):
    """Lowering min_samples below RB_400's count (10) should let it use its
    own (single-bucket) distribution instead of the pooled one."""
    df = compute_realized_delay(raw_records)
    distributions = aggregate_delay_distributions(df, min_samples=10)
    assert distributions["RB_400"] == {"0": 0.0, "5": 1.0, "15": 0.0, "30": 0.0, "60": 0.0}


def test_default_min_samples_is_thirty():
    assert DEFAULT_MIN_SAMPLES == 30


# --- additional_line_types: coverage for lines with zero rows of their own -----


def test_additional_line_types_gets_the_pooled_group_fallback(raw_records):
    """A real GTFS line with zero matching historical rows this month must
    still get a distribution -- the same pooled one its group's other
    under-threshold members get."""
    df = compute_realized_delay(raw_records)
    distributions = aggregate_delay_distributions(df, additional_line_types={"ICE_999": "ICE"})
    assert distributions["ICE_999"] == distributions["IC_200"]  # both ICE_IC-pooled


def test_additional_line_types_does_not_override_a_line_with_its_own_data(raw_records):
    """If the line_id already has rows in df, additional_line_types must not
    force it onto the pooled fallback."""
    df = compute_realized_delay(raw_records)
    distributions = aggregate_delay_distributions(df, additional_line_types={"ICE_100": "ICE"})
    assert distributions["ICE_100"] == pytest.approx(
        {"0": 20 / 40, "5": 10 / 40, "15": 6 / 40, "30": 3 / 40, "60": 1 / 40}
    )


def test_additional_line_types_rejects_unknown_line_type(raw_records):
    df = compute_realized_delay(raw_records)
    with pytest.raises(ValueError):
        aggregate_delay_distributions(df, additional_line_types={"BUS_1": "Bus"})


def test_additional_line_types_raises_when_its_whole_group_has_no_data():
    """A group with literally zero rows anywhere can't produce a fallback --
    this must fail loudly, not silently omit the line."""
    df = compute_realized_delay(pd.DataFrame(_rows("S_1", "S-Bahn", 0, 5)))
    with pytest.raises(ValueError):
        aggregate_delay_distributions(df, additional_line_types={"RE_999": "RE"})


# --- normalization ------------------------------------------------------------


def test_every_distribution_sums_to_exactly_one(raw_records):
    df = compute_realized_delay(raw_records)
    distributions = aggregate_delay_distributions(df)
    for line_id, dist in distributions.items():
        assert sum(dist.values()) == pytest.approx(1.0), line_id


def test_every_distribution_has_all_five_bucket_keys(raw_records):
    df = compute_realized_delay(raw_records)
    distributions = aggregate_delay_distributions(df)
    for line_id, dist in distributions.items():
        assert set(dist.keys()) == set(BUCKET_LABELS), line_id


# --- error handling -----------------------------------------------------------


def test_unknown_line_type_raises():
    df = compute_realized_delay(pd.DataFrame(_rows("BUS_1", "Bus", 0, 5)))
    with pytest.raises(ValueError):
        aggregate_delay_distributions(df)


# --- end-to-end ----------------------------------------------------------------


def test_build_delay_distributions_end_to_end_matches_manual_pipeline(raw_records):
    end_to_end = build_delay_distributions(raw_records)
    manual = aggregate_delay_distributions(compute_realized_delay(raw_records))
    assert end_to_end == manual
    assert set(end_to_end.keys()) == {"ICE_100", "IC_200", "RE_300", "RB_400", "S_1", "S_2"}
