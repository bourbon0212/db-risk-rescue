"""Delay-distribution aggregation from historical piebro delay records.

Per DATA_SPEC.md Section 4 and Section 8 (build sequence step 3). Input is a
pandas DataFrame of one row per historical leg-occurrence, already carrying
line_id/line_type (the id_crosswalk.py join happens upstream, in build
sequence step 4). Reading the actual monthly Parquet archive is a thin
pd.read_parquet wrapper deferred to build_dataset.py — this module only
processes already-loaded records.

Expected input columns:
    line_id              str  -- our Line.line_id
    line_type            str  -- one of gtfs_ingest.LINE_TYPES
    arrival_delay_minutes  float -- realized delay recorded at the
                                    destination station for that occurrence
"""

import pandas as pd

# Matches models.Leg.delay_distribution_minutes' bucket keys and mock_data.json.
BUCKET_BOUNDARIES = [0, 5, 15, 30, 60]
BUCKET_LABELS = [str(b) for b in BUCKET_BOUNDARIES]

# DATA_SPEC.md §7.2 — proposed starting threshold.
DEFAULT_MIN_SAMPLES = 30

# DATA_SPEC.md §4 step 5 — fallback pools ICE+IC together, RE+RB together,
# S-Bahn alone. This mirrors SPEC.md §2.6's service-frequency fallback shape.
FALLBACK_GROUPS: dict[str, str] = {
    "ICE": "ICE_IC",
    "IC": "ICE_IC",
    "RE": "RE_RB",
    "RB": "RE_RB",
    "S-Bahn": "S-Bahn",
}


def compute_realized_delay(df: pd.DataFrame) -> pd.DataFrame:
    """§4 step 2 (v1): realized leg delay = arrival delay at the destination
    station. Origin-side departure-delay joining is a later refinement."""
    df = df.copy()
    df["realized_delay_minutes"] = df["arrival_delay_minutes"]
    return df


def bucket_delay(delay_minutes: float) -> str:
    """Floor delay_minutes to the largest boundary not exceeding it (§4 step
    3): this is what makes engine.py's `int(bucket) > buffer` miss check
    line up with the bucket a given realized delay was filed under. Negative
    (early-arrival) delays clamp to the "0" bucket; anything at or above the
    top boundary collapses into it.
    """
    delay_minutes = max(delay_minutes, 0)
    bucket = BUCKET_BOUNDARIES[0]
    for boundary in BUCKET_BOUNDARIES:
        if boundary > delay_minutes:
            break
        bucket = boundary
    return str(bucket)


def _bucket_counts_by(df: pd.DataFrame, key: str) -> pd.DataFrame:
    counts = pd.crosstab(df[key], df["bucket"])
    return counts.reindex(columns=BUCKET_LABELS, fill_value=0)


def _normalize_counts(counts: pd.Series) -> dict[str, float]:
    total = counts.sum()
    if total == 0:
        raise ValueError("Cannot normalize a distribution with zero total occurrences")
    return {label: float(count) / float(total) for label, count in counts.items()}


def aggregate_delay_distributions(
    df: pd.DataFrame,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    additional_line_types: dict[str, str] | None = None,
) -> dict[str, dict[str, float]]:
    """§4 steps 3-6: bucket, count per line_id, apply the sample-threshold
    fallback to a pooled train-type distribution, and normalize each result
    to sum to exactly 1.0.

    df must already have realized_delay_minutes (see compute_realized_delay).

    additional_line_types (optional): line_id -> line_type for lines that
    must get a distribution even though they have ZERO rows of their own in
    df -- e.g. a real GTFS line with no matching historical records at all
    this month. Without this, such a line simply never appears in the
    output: the function otherwise only knows about line_ids actually
    present in df, and a wholly-absent line has no rows to infer its
    fallback group from. These lines always take the group-pooled fallback
    (their own total is 0, which is always < min_samples).
    """
    unknown_types = set(df["line_type"]) - set(FALLBACK_GROUPS)
    if unknown_types:
        raise ValueError(f"No fallback group defined for line_type(s): {sorted(unknown_types)}")

    df = df.copy()
    df["bucket"] = df["realized_delay_minutes"].apply(bucket_delay)
    df["fallback_group"] = df["line_type"].map(FALLBACK_GROUPS)

    line_counts = _bucket_counts_by(df, "line_id")
    group_counts = _bucket_counts_by(df, "fallback_group")
    line_totals = line_counts.sum(axis=1)
    line_to_group = df.groupby("line_id")["fallback_group"].first()

    extra_line_to_group: dict[str, str] = {}
    for line_id, line_type in (additional_line_types or {}).items():
        if line_id in line_totals.index:
            continue  # already covered by df's own rows
        if line_type not in FALLBACK_GROUPS:
            raise ValueError(f"No fallback group defined for line_type(s): ['{line_type}']")
        extra_line_to_group[line_id] = FALLBACK_GROUPS[line_type]

    distributions: dict[str, dict[str, float]] = {}
    for line_id, total in line_totals.items():
        if total >= min_samples:
            counts = line_counts.loc[line_id]
        else:
            counts = _group_counts_for(group_counts, line_to_group[line_id])
        distributions[line_id] = _normalize_counts(counts)

    for line_id, group in extra_line_to_group.items():
        distributions[line_id] = _normalize_counts(_group_counts_for(group_counts, group))

    return distributions


def _group_counts_for(group_counts: pd.DataFrame, group: str) -> pd.Series:
    if group not in group_counts.index:
        raise ValueError(f"No historical data available at all for fallback group {group!r}")
    return group_counts.loc[group]


def build_delay_distributions(
    df: pd.DataFrame,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    additional_line_types: dict[str, str] | None = None,
) -> dict[str, dict[str, float]]:
    """End-to-end: raw historical records -> {line_id: delay_distribution_minutes}."""
    return aggregate_delay_distributions(
        compute_realized_delay(df), min_samples=min_samples, additional_line_types=additional_line_types
    )
