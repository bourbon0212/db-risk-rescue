"""DDL + write logic for the Phase 3 DuckDB warehouse (SPEC.md §4.3).

Table shapes mirror the schema agreed in the Phase 3 design plan: topology
is stored date-agnostic (leg_templates/transfer_templates, seconds-since-
midnight instead of concrete datetimes) so the row count doesn't multiply
with the size of the ingested calendar window; delay_distributions stays
long-format and date-independent, matching how DATA_SPEC.md §4 already
aggregates it (per line_id, no date dimension). Concrete Leg/Transfer
Pydantic objects are only ever materialized at query time, in
pipelines/route_search_duckdb.py -- this module never touches models.py.
"""

import duckdb

from models import Line, Station
from pipelines.calendar_ingest import ServiceCalendarException, ServiceCalendarRow
from pipelines.gtfs_ingest import LegTemplate, TransferTemplate, TripRecord

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stations (
    station_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS lines (
    line_id VARCHAR PRIMARY KEY,
    type VARCHAR NOT NULL,
    operator VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS trips (
    trip_id VARCHAR PRIMARY KEY,
    line_id VARCHAR NOT NULL,
    service_id VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS leg_templates (
    leg_id VARCHAR PRIMARY KEY,
    trip_id VARCHAR NOT NULL,
    line_id VARCHAR NOT NULL,
    sequence_index INTEGER NOT NULL,
    origin_station_id VARCHAR NOT NULL,
    destination_station_id VARCHAR NOT NULL,
    departure_seconds INTEGER NOT NULL,
    arrival_seconds INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS transfer_templates (
    transfer_id VARCHAR PRIMARY KEY,
    station_id VARCHAR NOT NULL,
    from_leg_id VARCHAR NOT NULL,
    to_leg_id VARCHAR NOT NULL,
    from_trip_id VARCHAR NOT NULL,
    to_trip_id VARCHAR NOT NULL,
    buffer_minutes INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS service_calendar (
    service_id VARCHAR NOT NULL,
    monday BOOLEAN NOT NULL,
    tuesday BOOLEAN NOT NULL,
    wednesday BOOLEAN NOT NULL,
    thursday BOOLEAN NOT NULL,
    friday BOOLEAN NOT NULL,
    saturday BOOLEAN NOT NULL,
    sunday BOOLEAN NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS service_calendar_exceptions (
    service_id VARCHAR NOT NULL,
    date DATE NOT NULL,
    exception_type TINYINT NOT NULL
);

CREATE TABLE IF NOT EXISTS delay_distributions (
    line_id VARCHAR NOT NULL,
    bucket_minutes INTEGER NOT NULL,
    probability DOUBLE NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leg_templates_origin ON leg_templates (origin_station_id);
CREATE INDEX IF NOT EXISTS idx_transfer_templates_from_leg ON transfer_templates (from_leg_id);
CREATE INDEX IF NOT EXISTS idx_trips_service_id ON trips (service_id);
CREATE INDEX IF NOT EXISTS idx_delay_distributions_line_id ON delay_distributions (line_id);
"""

_TABLES_IN_DEPENDENCY_ORDER = [
    "delay_distributions",
    "service_calendar_exceptions",
    "service_calendar",
    "transfer_templates",
    "leg_templates",
    "trips",
    "lines",
    "stations",
]


def create_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(SCHEMA_SQL)


def clear_warehouse(conn: duckdb.DuckDBPyConnection) -> None:
    """Truncate every table so write_warehouse() can be called against a
    warehouse file left over from a previous build without duplicating rows."""
    create_schema(conn)
    for table in _TABLES_IN_DEPENDENCY_ORDER:
        conn.execute(f"DELETE FROM {table}")


def _executemany(conn: duckdb.DuckDBPyConnection, sql: str, rows: list[tuple]) -> None:
    """duckdb's executemany() rejects an empty parameter list outright
    (unlike a plain no-op INSERT) -- fixtures/warehouses with an empty table
    (e.g. no calendar.txt in the source feed) are legitimate, so skip
    silently rather than let that raise."""
    if rows:
        conn.executemany(sql, rows)


def write_warehouse(
    conn: duckdb.DuckDBPyConnection,
    stations: list[Station],
    lines: list[Line],
    trips: list[TripRecord],
    leg_templates: list[LegTemplate],
    transfer_templates: list[TransferTemplate],
    calendar_rows: list[ServiceCalendarRow],
    calendar_exceptions: list[ServiceCalendarException],
    delay_distributions: dict[str, dict[str, float]],
) -> None:
    """Write an already-parsed/crosswalked set of Phase 3 warehouse rows.
    Clears existing rows first, so this function is safe to re-run against
    the same warehouse file on a rebuild."""
    clear_warehouse(conn)

    _executemany(
        conn,
        "INSERT INTO stations VALUES (?, ?)",
        [(s.station_id, s.name) for s in stations],
    )
    _executemany(
        conn,
        "INSERT INTO lines VALUES (?, ?, ?)",
        [(l.line_id, l.type, l.operator) for l in lines],
    )
    _executemany(
        conn,
        "INSERT INTO trips VALUES (?, ?, ?)",
        [(t.trip_id, t.line_id, t.service_id) for t in trips],
    )
    _executemany(
        conn,
        "INSERT INTO leg_templates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                lt.leg_id,
                lt.trip_id,
                lt.line_id,
                lt.sequence_index,
                lt.origin_station_id,
                lt.destination_station_id,
                lt.departure_seconds,
                lt.arrival_seconds,
            )
            for lt in leg_templates
        ],
    )
    _executemany(
        conn,
        "INSERT INTO transfer_templates VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                tt.transfer_id,
                tt.station_id,
                tt.from_leg_id,
                tt.to_leg_id,
                tt.from_trip_id,
                tt.to_trip_id,
                tt.buffer_minutes,
            )
            for tt in transfer_templates
        ],
    )
    _executemany(
        conn,
        "INSERT INTO service_calendar VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                c.service_id,
                c.monday,
                c.tuesday,
                c.wednesday,
                c.thursday,
                c.friday,
                c.saturday,
                c.sunday,
                c.start_date,
                c.end_date,
            )
            for c in calendar_rows
        ],
    )
    _executemany(
        conn,
        "INSERT INTO service_calendar_exceptions VALUES (?, ?, ?)",
        [(e.service_id, e.date, e.exception_type) for e in calendar_exceptions],
    )
    _executemany(
        conn,
        "INSERT INTO delay_distributions VALUES (?, ?, ?)",
        [
            (line_id, int(bucket), probability)
            for line_id, distribution in delay_distributions.items()
            for bucket, probability in distribution.items()
        ],
    )
