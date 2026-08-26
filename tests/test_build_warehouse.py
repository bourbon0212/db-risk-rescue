"""Smoke test for pipelines/build_warehouse.py (DATA_SPEC.md §6.4): runs the fixture/
demo path against fixtures/gtfs_smoke/ (same fixture and synthetic delay
data as test_build_dataset.py's build_dataset() smoke test) and checks the
written DuckDB warehouse end to end.
"""

import duckdb
import pytest

from pipelines.build_warehouse import build_warehouse, demo_historical_delays
from pipelines.build_dataset import DEMO_GTFS_DIR
from pipelines.gtfs_ingest import LINE_TYPES


@pytest.fixture(scope="module")
def conn() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    build_warehouse(connection, DEMO_GTFS_DIR, demo_historical_delays())
    yield connection
    connection.close()


def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_stations_written(conn):
    assert _count(conn, "stations") == 3


def test_lines_written_with_valid_types(conn):
    assert _count(conn, "lines") == 5
    types = {row[0] for row in conn.execute("SELECT DISTINCT type FROM lines").fetchall()}
    assert types <= LINE_TYPES


def test_leg_templates_written(conn):
    # Same count as test_build_dataset's anchored build: T1(1) + T2(2) +
    # T3(1) + T4(1) + T5(1) = 6.
    assert _count(conn, "leg_templates") == 6


def test_transfer_templates_written(conn):
    assert _count(conn, "transfer_templates") == 1
    row = conn.execute(
        "SELECT station_id, buffer_minutes FROM transfer_templates"
    ).fetchone()
    assert row == ("DE_KOL_HBF", 11)


def test_trips_written(conn):
    assert _count(conn, "trips") == 5


def test_delay_distributions_normalize_to_one(conn):
    rows = conn.execute(
        "SELECT line_id, SUM(probability) FROM delay_distributions GROUP BY line_id"
    ).fetchall()
    assert len(rows) == 5
    for line_id, total in rows:
        assert total == pytest.approx(1.0)


def test_well_sampled_line_keeps_its_own_distribution(conn):
    rows = conn.execute(
        "SELECT bucket_minutes, probability FROM delay_distributions WHERE line_id = 'ICE 15' "
        "ORDER BY bucket_minutes"
    ).fetchall()
    by_bucket = dict(rows)
    assert by_bucket[0] == pytest.approx(25 / 35)
    assert by_bucket[15] == pytest.approx(10 / 35)


def test_calendar_tables_empty_for_fixture_without_calendar_files(conn):
    """fixtures/gtfs_smoke/ has no calendar.txt -- this only exercises the
    write path with an empty calendar, not query-time date resolution
    (covered separately by test_route_search_duckdb.py's hand-built
    warehouse)."""
    assert _count(conn, "service_calendar") == 0
    assert _count(conn, "service_calendar_exceptions") == 0


def test_rebuilding_does_not_duplicate_rows(conn):
    """write_warehouse() clears existing rows first, so calling
    build_warehouse() again against the same connection is idempotent."""
    build_warehouse(conn, DEMO_GTFS_DIR, demo_historical_delays())
    assert _count(conn, "stations") == 3
    assert _count(conn, "leg_templates") == 6


def test_stations_get_a_classified_mct(conn):
    """Every station gets a tier-classified mct_minutes (5 or 10) rather
    than a null/missing value -- fixtures/gtfs_smoke/ is too small for a
    meaningful hub/standard split, but every row must still be a real int."""
    rows = conn.execute("SELECT mct_minutes FROM stations").fetchall()
    assert len(rows) == 3
    assert all(row[0] in (5, 10) for row in rows)


def test_leg_templates_platform_columns_exist_and_are_nullable(conn):
    """fixtures/gtfs_smoke/stops.txt has no platform_code column at all --
    the write path must still succeed, with every platform value NULL
    rather than the build raising."""
    rows = conn.execute("SELECT origin_platform, destination_platform FROM leg_templates").fetchall()
    assert len(rows) == 6
    assert all(origin is None and dest is None for origin, dest in rows)
