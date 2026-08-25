"""Maps piebro/deutsche-bahn-data's raw schema onto our GTFS-derived
line_id/line_type, so delay_aggregation.py's existing bucketing/fallback
logic (completely unchanged) can run against real historical delay records.

The archive's columns don't mean what their names suggest -- `delay_in_min`
isn't arrival delay, and line identity lives in a different column for
long-distance vs. regional trains. DATA_SPEC.md §4.1 has the measurements
behind both; the mapping tables below implement them.

Regional codes aren't nationally unique, so `target_line_ids` bounds (but
doesn't eliminate) the collision risk -- DATA_SPEC.md §10.
"""

from pathlib import Path

import pandas as pd

PIEBRO_TRAIN_TYPE_TO_LINE_TYPE: dict[str, str] = {
    "ICE": "ICE",
    "IC": "IC",
    "EC": "IC",
    "ECE": "IC",
    "RE": "RE",
    "RB": "RB",
    "S": "S-Bahn",
}

_LONG_DISTANCE_TYPES = ("ICE", "IC", "EC", "ECE")
_REGIONAL_TYPES = ("RE", "RB", "S")

_PARQUET_COLUMNS = [
    "train_type",
    "train_number",
    "line_number",
    "arrival_planned_time",
    "arrival_change_time",
    "arrival_is_canceled",
]


def _line_ids_for_long_distance(df: pd.DataFrame) -> pd.Series:
    return df["train_type"] + " " + df["train_number"].astype(str)


def _line_ids_for_regional(df: pd.DataFrame) -> pd.Series:
    """line_number already carries the type prefix in the vast majority of
    rows ("RE5", "RB44", "S12"); str.startswith() needs a fixed scalar
    prefix per call, so each of the three regional types is handled as its
    own boolean mask rather than one vectorized pass across all three.
    """
    result = pd.Series(index=df.index, dtype="object")
    for train_type in _REGIONAL_TYPES:
        mask = df["train_type"] == train_type
        line_number = df.loc[mask, "line_number"].astype(str).str.strip()
        already_prefixed = line_number.str.upper().str.startswith(train_type)
        result.loc[mask] = line_number.where(already_prefixed, train_type + line_number)
    return result


def map_piebro_records(df: pd.DataFrame, target_line_ids: set[str] | None = None) -> pd.DataFrame:
    """Translate raw piebro rows into delay_aggregation.py's expected shape:
    line_id, line_type, arrival_delay_minutes.

    Rows are dropped (not raised on) when: train_type isn't one of our five
    supported types (piebro covers every operator/brand nationwide -- the
    DB-only, five-type scoping DATA_SPEC.md §3 step 2 already applies to
    our GTFS side applies here too), there's no real arrival event at that
    station (the train's own origin), or the arrival was canceled.

    If target_line_ids is given, rows are further restricted to just those
    line_ids -- both to bound the regional-code collision risk described in
    the module docstring, and because there's no reason to build
    distributions for lines our real dataset doesn't have.
    """
    df = df[df["train_type"].isin(PIEBRO_TRAIN_TYPE_TO_LINE_TYPE)]
    df = df[df["arrival_planned_time"].notna() & df["arrival_change_time"].notna()]
    df = df[df["arrival_is_canceled"] != True]  # noqa: E712 (nullable bool column)

    line_type = df["train_type"].map(PIEBRO_TRAIN_TYPE_TO_LINE_TYPE)

    line_id = pd.Series(index=df.index, dtype="object")
    long_distance_mask = df["train_type"].isin(_LONG_DISTANCE_TYPES)
    line_id[long_distance_mask] = _line_ids_for_long_distance(df[long_distance_mask])
    regional_mask = df["train_type"].isin(_REGIONAL_TYPES)
    line_id[regional_mask] = _line_ids_for_regional(df[regional_mask])

    arrival_delay_minutes = (
        df["arrival_change_time"] - df["arrival_planned_time"]
    ).dt.total_seconds() / 60

    mapped = pd.DataFrame(
        {
            "line_id": line_id,
            "line_type": line_type,
            "arrival_delay_minutes": arrival_delay_minutes,
        }
    )
    mapped = mapped.dropna(subset=["line_id", "line_type", "arrival_delay_minutes"])

    if target_line_ids is not None:
        mapped = mapped[mapped["line_id"].isin(target_line_ids)]

    return mapped.reset_index(drop=True)


def load_piebro_delays(
    parquet_path: Path, target_line_ids: set[str] | None = None
) -> pd.DataFrame:
    """Read one piebro monthly Parquet file and map it via map_piebro_records()."""
    df = pd.read_parquet(parquet_path, columns=_PARQUET_COLUMNS)
    return map_piebro_records(df, target_line_ids=target_line_ids)
