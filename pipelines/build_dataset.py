"""Orchestrator: wires gtfs_ingest + delay_aggregation + id_crosswalk into a
validated MockDataset and writes it to data/real_dataset.json.

Per DATA_SPEC.md §7 and §8 step 5. This is a one-off/periodic
script (run manually or on a schedule), not something data_loader.py or
app.py invoke at request time.

Two build paths live here:
  - build_dataset(): the small fixture/demo path from Step 5, unchanged --
    used by __main__ as a fallback when no real download is present, and by
    test_build_dataset.py's smoke test.
  - build_real_dataset(): the real path, added once data/raw/gtfs_fv_latest.zip
    and gtfs_rv_latest.zip (from pipelines/download_raw_data.py) exist. It
    scopes each feed to DB-operated, corridor-touching routes/trips
    (pipelines/gtfs_scope.py) before handing them to gtfs_ingest.py's
    unmodified parse_lines/parse_legs. If a data/raw/delays_*.parquet file
    (from pipelines/download_raw_data.py) is present, delay distributions
    are built from the real piebro archive via pipelines/delay_mapping.py;
    otherwise it falls back to the same synthetic per-type samples as
    before, so the script still runs end-to-end without the ~600MB parquet
    download.
"""

import tempfile
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

from models import Leg, Line, MockDataset, Station, Transfer
from pipelines.delay_aggregation import DEFAULT_MIN_SAMPLES, build_delay_distributions
from pipelines.delay_mapping import load_piebro_delays
from pipelines.gtfs_ingest import LINE_TYPES, derive_transfers, parse_legs, parse_lines, parse_stations
from pipelines.gtfs_scope import scope_gtfs_feed
from pipelines.id_crosswalk import GTFS_STOP_ID_TO_STATION_ID, STATION_NAMES, to_station_id

DEMO_GTFS_DIR = Path(__file__).parent.parent / "fixtures" / "gtfs_smoke"
DEMO_SERVICE_DATE = date(2026, 8, 23)
DEFAULT_OUTPUT_PATH = Path(__file__).parent.parent / "data" / "real_dataset.json"

RAW_DATA_DIR = Path(__file__).parent.parent / "data" / "raw"
REAL_GTFS_ZIPS = {
    "fv": RAW_DATA_DIR / "gtfs_fv_latest.zip",
    "rv": RAW_DATA_DIR / "gtfs_rv_latest.zip",
}

# Synthetic per-line-type delay samples for the real-topology build (no real
# delay data joined yet -- see module docstring). Two rows per real line_id,
# deliberately below delay_aggregation.DEFAULT_MIN_SAMPLES, so every line
# always falls back to its pooled type-group distribution (ICE+IC, RE+RB,
# S-Bahn) and every line within a group shares one consistent, clearly-
# synthetic distribution rather than one line randomly qualifying for
# "well sampled" treatment by chance.
_SYNTHETIC_DELAY_SAMPLES_BY_TYPE: dict[str, list[float]] = {
    "ICE": [0, 20],
    "IC": [0, 20],
    "RE": [0, 8],
    "RB": [0, 8],
    "S-Bahn": [0, 6],
}


def _rows(line_id: str, line_type: str, delay_minutes: float, count: int) -> list[dict]:
    return [
        {"line_id": line_id, "line_type": line_type, "arrival_delay_minutes": delay_minutes}
        for _ in range(count)
    ]


def demo_historical_delays() -> pd.DataFrame:
    """Synthetic stand-in for the piebro Parquet archive (DATA_SPEC.md §4
    step 1), sized to exercise both the well-sampled and fallback-pooling
    paths for every line in fixtures/gtfs_smoke/.
    """
    rows: list[dict] = []
    rows += _rows("ICE 15", "ICE", 0, 25) + _rows("ICE 15", "ICE", 15, 10)  # well-sampled
    rows += _rows("IC 61", "IC", 5, 4)  # under threshold -> falls back to ICE_IC pool
    rows += _rows("RE 1", "RE", 0, 30)  # well-sampled
    rows += _rows("RB 27", "RB", 5, 8)  # under threshold -> falls back to RE_RB pool
    rows += _rows("S8", "S-Bahn", 0, 32)  # well-sampled
    return pd.DataFrame(rows)


def _crosswalk_stations(stations: list[Station]) -> list[Station]:
    return [Station(station_id=to_station_id(s.station_id), name=s.name) for s in stations]


def _crosswalk_legs(legs: list[Leg]) -> list[Leg]:
    return [
        leg.model_copy(
            update={
                "origin_station_id": to_station_id(leg.origin_station_id),
                "destination_station_id": to_station_id(leg.destination_station_id),
            }
        )
        for leg in legs
    ]


def _crosswalk_transfers(transfers: list[Transfer]) -> list[Transfer]:
    return [
        transfer.model_copy(update={"station_id": to_station_id(transfer.station_id)})
        for transfer in transfers
    ]


def _apply_crosswalk(
    stations: list[Station], legs: list[Leg], transfers: list[Transfer]
) -> tuple[list[Station], list[Leg], list[Transfer]]:
    """Translate every raw GTFS stop_id-derived station_id to our station_id."""
    return _crosswalk_stations(stations), _crosswalk_legs(legs), _crosswalk_transfers(transfers)


def _dedupe_legs(legs: list[Leg]) -> list[Leg]:
    """Drop legs that are exact duplicates of an earlier one (same line,
    stations, and times). GTFS.DE's feed occasionally carries two different
    trip_ids for the exact same physical service -- a real feed-quality
    quirk, not something this build causes. This must run before
    derive_transfers(), not after: deduping afterward would leave one
    twin's leg gone while transfers still reference its leg_id.
    """
    seen_keys: set[tuple] = set()
    deduped: list[Leg] = []
    for leg in legs:
        key = (
            leg.line_id,
            leg.origin_station_id,
            leg.destination_station_id,
            leg.scheduled_departure,
            leg.scheduled_arrival,
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(leg)
    return deduped


def build_dataset(
    gtfs_dir: Path,
    historical_delays: pd.DataFrame,
    service_date: date,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> MockDataset:
    """Run the full Phase 2 pipeline and return a validated MockDataset."""
    stations = parse_stations(gtfs_dir)
    lines = parse_lines(gtfs_dir)
    legs = parse_legs(gtfs_dir, service_date)
    transfers = derive_transfers(legs)

    # DATA_SPEC.md §3.1 hard requirement: fail the build, don't let a bad
    # line type reach engine.py, even though parse_lines already normalizes
    # at parse time -- this is the dedicated pre-write assertion the spec
    # calls for.
    bad_types = [line.line_id for line in lines if line.type not in LINE_TYPES]
    if bad_types:
        raise ValueError(f"Lines with invalid type reached build_dataset: {bad_types}")

    stations, legs, transfers = _apply_crosswalk(stations, legs, transfers)

    distributions = build_delay_distributions(historical_delays, min_samples=min_samples)
    missing = sorted({leg.line_id for leg in legs} - set(distributions))
    if missing:
        raise ValueError(f"No delay distribution available for line_id(s): {missing}")
    legs = [
        leg.model_copy(update={"delay_distribution_minutes": distributions[leg.line_id]})
        for leg in legs
    ]

    assembled = {
        "stations": [s.model_dump(mode="json") for s in stations],
        "lines": [line.model_dump(mode="json") for line in lines],
        "legs": [leg.model_dump(mode="json") for leg in legs],
        "transfers": [t.model_dump(mode="json") for t in transfers],
        "routes": [],
    }
    return MockDataset.model_validate(assembled)


def _synthetic_delays_for_lines(lines: list[Line]) -> pd.DataFrame:
    """Build the synthetic historical-delay DataFrame for a real-topology
    build. See _SYNTHETIC_DELAY_SAMPLES_BY_TYPE's comment for why every
    line gets exactly 2 samples."""
    rows: list[dict] = []
    for line in lines:
        for delay in _SYNTHETIC_DELAY_SAMPLES_BY_TYPE[line.type]:
            rows += _rows(line.line_id, line.type, delay, 1)
    return pd.DataFrame(rows)


def _find_latest_delay_parquet(raw_dir: Path) -> Path | None:
    """Monthly files are named delays_YYYY-MM.parquet, so sorting names
    picks the most recent month among whatever's been downloaded."""
    candidates = sorted(raw_dir.glob("delays_*.parquet"))
    return candidates[-1] if candidates else None


def _print_match_report(lines: list[Line], historical: pd.DataFrame, min_samples: int) -> None:
    own_counts = historical["line_id"].value_counts()
    matched = sorted(line.line_id for line in lines if own_counts.get(line.line_id, 0) >= min_samples)
    fallback = sorted(line.line_id for line in lines if own_counts.get(line.line_id, 0) < min_samples)
    print(
        f"\nMatch report: {len(matched)}/{len(lines)} lines matched their own historical "
        f"data (>= {min_samples} samples); {len(fallback)} fell back to the pooled "
        "type-group average."
    )
    if matched:
        print(f"  matched:  {', '.join(matched)}")
    if fallback:
        print(f"  fallback: {', '.join(fallback)}")


def build_real_dataset(
    raw_dir: Path = RAW_DATA_DIR,
    service_date: date | None = None,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> MockDataset:
    """Real-topology build (DATA_SPEC.md §3 steps 1-6): extract the
    downloaded GTFS.DE feeds, scope each to DB-operated/corridor-touching
    routes and trips running on service_date, ingest via gtfs_ingest.py
    unchanged, and fill in delay distributions -- real ones (via
    pipelines/delay_mapping.py) if a data/raw/delays_*.parquet is present,
    synthetic ones otherwise.
    """
    if service_date is None:
        service_date = date.today()

    corridor_stop_ids = set(GTFS_STOP_ID_TO_STATION_ID)
    all_lines: list[Line] = []
    all_legs: list[Leg] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for feed_key, zip_path in REAL_GTFS_ZIPS.items():
            extracted_dir = tmp_path / f"{feed_key}_extracted"
            scoped_dir = tmp_path / f"{feed_key}_scoped"
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extracted_dir)

            scope_gtfs_feed(extracted_dir, scoped_dir, corridor_stop_ids, service_date)
            all_lines += parse_lines(scoped_dir)
            all_legs += parse_legs(scoped_dir, service_date)

    # fv (long-distance) and rv (regional) are disjoint route categories in
    # gtfs.de's split, so line_id shouldn't collide across them -- dedupe by
    # line_id defensively anyway, keeping the first occurrence.
    seen_line_ids: set[str] = set()
    lines: list[Line] = []
    for line in all_lines:
        if line.line_id in seen_line_ids:
            continue
        seen_line_ids.add(line.line_id)
        lines.append(line)

    bad_types = [line.line_id for line in lines if line.type not in LINE_TYPES]
    if bad_types:
        raise ValueError(f"Lines with invalid type reached build_real_dataset: {bad_types}")

    # Keep only corridor-to-corridor legs -- a kept trip's full sequence
    # (see gtfs_scope.py) still includes non-corridor intermediate legs.
    corridor_legs = [
        leg
        for leg in all_legs
        if leg.origin_station_id in corridor_stop_ids
        and leg.destination_station_id in corridor_stop_ids
    ]

    corridor_legs = _dedupe_legs(corridor_legs)
    transfers = derive_transfers(corridor_legs)
    legs, transfers = _crosswalk_legs(corridor_legs), _crosswalk_transfers(transfers)

    used_line_ids = {leg.line_id for leg in legs}
    lines = [line for line in lines if line.line_id in used_line_ids]

    stations = [Station(station_id=sid, name=name) for sid, name in STATION_NAMES.items()]

    delay_parquet_path = _find_latest_delay_parquet(raw_dir)
    if delay_parquet_path is not None:
        target_line_ids = {line.line_id for line in lines}
        historical = load_piebro_delays(delay_parquet_path, target_line_ids=target_line_ids)
        line_types = {line.line_id: line.type for line in lines}
        distributions = build_delay_distributions(
            historical, min_samples=min_samples, additional_line_types=line_types
        )
        _print_match_report(lines, historical, min_samples)
    else:
        print(
            "\nNo data/raw/delays_*.parquet found -- delay distributions are synthetic "
            "(run `python -m pipelines.download_raw_data` to fetch a real month)."
        )
        distributions = build_delay_distributions(
            _synthetic_delays_for_lines(lines), min_samples=min_samples
        )

    legs = [
        leg.model_copy(update={"delay_distribution_minutes": distributions[leg.line_id]})
        for leg in legs
    ]

    assembled = {
        "stations": [s.model_dump(mode="json") for s in stations],
        "lines": [line.model_dump(mode="json") for line in lines],
        "legs": [leg.model_dump(mode="json") for leg in legs],
        "transfers": [t.model_dump(mode="json") for t in transfers],
        "routes": [],
    }
    return MockDataset.model_validate(assembled)


def write_dataset(dataset: MockDataset, output_path: Path = DEFAULT_OUTPUT_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(dataset.model_dump_json(indent=2))
    return output_path


def main() -> None:
    have_real_feeds = all(path.exists() for path in REAL_GTFS_ZIPS.values())
    if have_real_feeds:
        print("Real GTFS.DE feeds found under data/raw/ -- building from real topology.")
        dataset = build_real_dataset()
    else:
        print(
            "No downloaded GTFS.DE feeds under data/raw/ -- building the small fixture/"
            "demo dataset instead. Run `python -m pipelines.download_raw_data` first "
            "for a real build."
        )
        dataset = build_dataset(DEMO_GTFS_DIR, demo_historical_delays(), DEMO_SERVICE_DATE)

    output_path = write_dataset(dataset)
    print(
        f"Wrote {output_path} -- {len(dataset.stations)} stations, "
        f"{len(dataset.lines)} lines, {len(dataset.legs)} legs, "
        f"{len(dataset.transfers)} transfers."
    )


if __name__ == "__main__":
    main()
