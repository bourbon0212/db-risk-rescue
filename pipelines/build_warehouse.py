"""Orchestrator for the Phase 3 DuckDB warehouse (SPEC.md §4.3): wires
gtfs_ingest's date-agnostic parsers + calendar_ingest + delay_aggregation +
id_crosswalk into pipelines/warehouse_writer.py, producing
data/warehouse.duckdb.

Mirrors pipelines/build_dataset.py's two-build-path structure:
  - build_warehouse(): fixture/demo path, used by the smoke test and by
    __main__ as a fallback when no real download is present.
  - build_real_warehouse(): the real path, using the same downloaded
    data/raw/gtfs_fv_latest.zip / gtfs_rv_latest.zip / delays_*.parquet as
    build_dataset.build_real_dataset() -- but scoped with
    gtfs_scope.scope_gtfs_feed_multi_day() (keeps every calendar date,
    unlike build_real_dataset's single-service_date scope_gtfs_feed()) and
    ingested via gtfs_ingest's parse_trips/parse_leg_templates instead of
    parse_legs.

build_dataset.py, route_search.py, and data/real_dataset.json are untouched
by this module -- Phase 1/2's JSON path keeps working unmodified alongside
this one (SPEC.md §4.3).
"""

import tempfile
import zipfile
from pathlib import Path

import duckdb
import pandas as pd

from models import Line, Station
from pipelines.calendar_ingest import parse_calendar, parse_calendar_exceptions
from pipelines.delay_aggregation import DEFAULT_MIN_SAMPLES, build_delay_distributions
from pipelines.delay_mapping import load_piebro_delays
from pipelines.gtfs_ingest import (
    LINE_TYPES,
    LegTemplate,
    TransferTemplate,
    derive_transfer_templates,
    parse_leg_templates,
    parse_lines,
    parse_stations,
    parse_trips,
)
from pipelines.gtfs_scope import scope_gtfs_feed_multi_day
from pipelines.id_crosswalk import GTFS_STOP_ID_TO_STATION_ID, STATION_NAMES, to_station_id
from pipelines.warehouse_writer import write_warehouse

DEMO_GTFS_DIR = Path(__file__).parent.parent / "fixtures" / "gtfs_smoke"
RAW_DATA_DIR = Path(__file__).parent.parent / "data" / "raw"
REAL_GTFS_ZIPS = {
    "fv": RAW_DATA_DIR / "gtfs_fv_latest.zip",
    "rv": RAW_DATA_DIR / "gtfs_rv_latest.zip",
}

# Same synthetic-per-type samples build_dataset.py uses for the fixture/demo
# path -- see that module's docstring for why every line gets exactly 2
# samples (always below DEFAULT_MIN_SAMPLES, so every line falls back to a
# consistent pooled type-group distribution).
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
    """Same shape as build_dataset.demo_historical_delays() -- exercises
    both the well-sampled and fallback-pooling paths for every line in
    fixtures/gtfs_smoke/."""
    rows: list[dict] = []
    rows += _rows("ICE 15", "ICE", 0, 25) + _rows("ICE 15", "ICE", 15, 10)
    rows += _rows("IC 61", "IC", 5, 4)
    rows += _rows("RE 1", "RE", 0, 30)
    rows += _rows("RB 27", "RB", 5, 8)
    rows += _rows("S8", "S-Bahn", 0, 32)
    return pd.DataFrame(rows)


def _crosswalk_stations(stations: list[Station]) -> list[Station]:
    return [Station(station_id=to_station_id(s.station_id), name=s.name) for s in stations]


def _crosswalk_leg_templates(templates: list[LegTemplate]) -> list[LegTemplate]:
    return [
        LegTemplate(
            leg_id=t.leg_id,
            trip_id=t.trip_id,
            line_id=t.line_id,
            sequence_index=t.sequence_index,
            origin_station_id=to_station_id(t.origin_station_id),
            destination_station_id=to_station_id(t.destination_station_id),
            departure_seconds=t.departure_seconds,
            arrival_seconds=t.arrival_seconds,
        )
        for t in templates
    ]


def _crosswalk_transfer_templates(templates: list[TransferTemplate]) -> list[TransferTemplate]:
    return [
        TransferTemplate(
            transfer_id=t.transfer_id,
            station_id=to_station_id(t.station_id),
            from_leg_id=t.from_leg_id,
            to_leg_id=t.to_leg_id,
            from_trip_id=t.from_trip_id,
            to_trip_id=t.to_trip_id,
            buffer_minutes=t.buffer_minutes,
        )
        for t in templates
    ]


def _dedupe_leg_templates(templates: list[LegTemplate]) -> list[LegTemplate]:
    """Same real feed-quality quirk build_dataset._dedupe_legs guards
    against (two trip_ids for the same physical service) -- must run before
    derive_transfer_templates(), same reasoning as the JSON path."""
    seen_keys: set[tuple] = set()
    deduped: list[LegTemplate] = []
    for t in templates:
        key = (
            t.line_id,
            t.origin_station_id,
            t.destination_station_id,
            t.departure_seconds,
            t.arrival_seconds,
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(t)
    return deduped


def build_warehouse(
    conn: duckdb.DuckDBPyConnection,
    gtfs_dir: Path,
    historical_delays: pd.DataFrame,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> None:
    """Fixture/demo path: run the full Phase 3 pipeline against a small,
    already-final-station-id GTFS fixture (no crosswalk needed) and write
    the result into conn. Mirrors build_dataset.build_dataset()'s scope."""
    stations = parse_stations(gtfs_dir)
    lines = parse_lines(gtfs_dir)
    trips = parse_trips(gtfs_dir)
    leg_templates = parse_leg_templates(gtfs_dir)
    transfer_templates = derive_transfer_templates(leg_templates)
    calendar_rows = parse_calendar(gtfs_dir)
    calendar_exceptions = parse_calendar_exceptions(gtfs_dir)

    bad_types = [line.line_id for line in lines if line.type not in LINE_TYPES]
    if bad_types:
        raise ValueError(f"Lines with invalid type reached build_warehouse: {bad_types}")

    distributions = build_delay_distributions(historical_delays, min_samples=min_samples)
    missing = sorted({t.line_id for t in leg_templates} - set(distributions))
    if missing:
        raise ValueError(f"No delay distribution available for line_id(s): {missing}")

    stations = _crosswalk_stations(stations)
    leg_templates = _crosswalk_leg_templates(leg_templates)
    transfer_templates = _crosswalk_transfer_templates(transfer_templates)

    write_warehouse(
        conn,
        stations,
        lines,
        trips,
        leg_templates,
        transfer_templates,
        calendar_rows,
        calendar_exceptions,
        distributions,
    )


def _find_latest_delay_parquet(raw_dir: Path) -> Path | None:
    candidates = sorted(raw_dir.glob("delays_*.parquet"))
    return candidates[-1] if candidates else None


def build_real_warehouse(
    conn: duckdb.DuckDBPyConnection,
    raw_dir: Path = RAW_DATA_DIR,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> None:
    """Real-topology build: scope each downloaded GTFS.DE feed to
    DB-operated, corridor-touching routes/trips across the *entire* ingested
    calendar window (gtfs_scope.scope_gtfs_feed_multi_day -- no service_date,
    unlike build_dataset.build_real_dataset), ingest via gtfs_ingest's
    date-agnostic parsers, and fill in delay distributions from the real
    piebro archive if present, synthetic ones otherwise.
    """
    corridor_stop_ids = set(GTFS_STOP_ID_TO_STATION_ID)
    all_lines: list[Line] = []
    all_trips = []
    all_leg_templates: list[LegTemplate] = []
    all_calendar_rows = []
    all_calendar_exceptions = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for feed_key, zip_path in REAL_GTFS_ZIPS.items():
            extracted_dir = tmp_path / f"{feed_key}_extracted"
            scoped_dir = tmp_path / f"{feed_key}_scoped"
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(extracted_dir)

            scope_gtfs_feed_multi_day(extracted_dir, scoped_dir, corridor_stop_ids)
            all_lines += parse_lines(scoped_dir)
            all_trips += parse_trips(scoped_dir)
            all_leg_templates += parse_leg_templates(scoped_dir)
            all_calendar_rows += parse_calendar(scoped_dir)
            all_calendar_exceptions += parse_calendar_exceptions(scoped_dir)

    seen_line_ids: set[str] = set()
    lines: list[Line] = []
    for line in all_lines:
        if line.line_id in seen_line_ids:
            continue
        seen_line_ids.add(line.line_id)
        lines.append(line)

    bad_types = [line.line_id for line in lines if line.type not in LINE_TYPES]
    if bad_types:
        raise ValueError(f"Lines with invalid type reached build_real_warehouse: {bad_types}")

    # Keep only corridor-to-corridor legs, same as build_real_dataset -- a
    # kept trip's full sequence still includes non-corridor intermediate legs.
    corridor_leg_templates = [
        t
        for t in all_leg_templates
        if t.origin_station_id in corridor_stop_ids
        and t.destination_station_id in corridor_stop_ids
    ]
    corridor_leg_templates = _dedupe_leg_templates(corridor_leg_templates)
    transfer_templates = derive_transfer_templates(corridor_leg_templates)

    leg_templates = _crosswalk_leg_templates(corridor_leg_templates)
    transfer_templates = _crosswalk_transfer_templates(transfer_templates)

    used_line_ids = {t.line_id for t in leg_templates}
    lines = [line for line in lines if line.line_id in used_line_ids]

    used_trip_ids = {t.trip_id for t in leg_templates}
    trips = [t for t in all_trips if t.trip_id in used_trip_ids]
    used_service_ids = {t.service_id for t in trips}
    calendar_rows = [c for c in all_calendar_rows if c.service_id in used_service_ids]
    calendar_exceptions = [
        e for e in all_calendar_exceptions if e.service_id in used_service_ids
    ]

    stations = [Station(station_id=sid, name=name) for sid, name in STATION_NAMES.items()]

    delay_parquet_path = _find_latest_delay_parquet(raw_dir)
    if delay_parquet_path is not None:
        target_line_ids = {line.line_id for line in lines}
        historical = load_piebro_delays(delay_parquet_path, target_line_ids=target_line_ids)
        line_types = {line.line_id: line.type for line in lines}
        distributions = build_delay_distributions(
            historical, min_samples=min_samples, additional_line_types=line_types
        )
        print(
            f"Delay distributions built from {delay_parquet_path.name} "
            f"for {len(lines)} lines."
        )
    else:
        print(
            "\nNo data/raw/delays_*.parquet found -- delay distributions are synthetic "
            "(run `python -m pipelines.download_raw_data` to fetch a real month)."
        )
        rows: list[dict] = []
        for line in lines:
            for delay in _SYNTHETIC_DELAY_SAMPLES_BY_TYPE[line.type]:
                rows += _rows(line.line_id, line.type, delay, 1)
        distributions = build_delay_distributions(pd.DataFrame(rows), min_samples=min_samples)

    write_warehouse(
        conn,
        stations,
        lines,
        trips,
        leg_templates,
        transfer_templates,
        calendar_rows,
        calendar_exceptions,
        distributions,
    )


def main() -> None:
    import db

    conn = db.get_connection(read_only=False)
    try:
        have_real_feeds = all(path.exists() for path in REAL_GTFS_ZIPS.values())
        if have_real_feeds:
            print("Real GTFS.DE feeds found under data/raw/ -- building the warehouse from real topology.")
            build_real_warehouse(conn)
        else:
            print(
                "No downloaded GTFS.DE feeds under data/raw/ -- building the small fixture/"
                "demo warehouse instead. Run `python -m pipelines.download_raw_data` first "
                "for a real build."
            )
            build_warehouse(conn, DEMO_GTFS_DIR, demo_historical_delays())

        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "stations",
                "lines",
                "trips",
                "leg_templates",
                "transfer_templates",
                "service_calendar",
                "service_calendar_exceptions",
                "delay_distributions",
            )
        }
        window = conn.execute(
            "SELECT MIN(start_date), MAX(end_date) FROM service_calendar"
        ).fetchone()
        print(f"Wrote {db.WAREHOUSE_PATH} -- {counts}")
        print(f"Calendar window: {window[0]} .. {window[1]}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
