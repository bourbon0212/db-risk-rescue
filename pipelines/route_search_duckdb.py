"""Phase 3 DuckDB-backed sibling of pipelines/route_search.py (SPEC.md §4.3).

Same candidate-route algorithm and Route/Leg/Transfer contract as the
Phase 1/2 in-memory find_candidate_routes() -- direct, single-transfer, and
two-transfer journeys, sorted by scheduled_departure -- but every step's
candidate set comes from a small, origin/date-scoped DuckDB query against
the warehouse's date-agnostic leg_templates/transfer_templates instead of
scanning an in-memory MockDataset. pipelines/route_search.py itself is
untouched; this is an additive module, not a replacement.

Every Leg/Transfer object this module resolves from the warehouse is written
into the caller-supplied legs_by_id/transfers_by_id dicts (mutated in
place). That's what lets engine.py's simulate_route()/precompute_fallback_
plans() -- which look legs/transfers up by id from those same dicts,
unchanged -- work without ever loading the whole network into memory: the
dicts only ever contain whatever this module has actually touched across
however many searches (the top-level search plus each transfer's fallback
search) happened during one app.py request.
"""

from datetime import date, datetime, time

import duckdb

from models import Leg, Route, Transfer
from pipelines.calendar_ingest import WEEKDAY_COLUMNS
from pipelines.gtfs_ingest import _anchor_datetime

_ACTIVE_SERVICES_SQL = """
CREATE OR REPLACE TEMP TABLE _active_service_ids AS
WITH active_by_calendar AS (
    SELECT service_id FROM service_calendar
    WHERE {weekday_col} = TRUE AND start_date <= ? AND end_date >= ?
),
removed AS (
    SELECT service_id FROM service_calendar_exceptions
    WHERE date = ? AND exception_type = 2
),
added AS (
    SELECT service_id FROM service_calendar_exceptions
    WHERE date = ? AND exception_type = 1
)
SELECT service_id FROM active_by_calendar
WHERE service_id NOT IN (SELECT service_id FROM removed)
UNION
SELECT service_id FROM added
"""

_ORIGIN_LEGS_SQL = """
SELECT lt.leg_id, lt.line_id, lt.origin_station_id, lt.destination_station_id,
       lt.departure_seconds, lt.arrival_seconds
FROM leg_templates lt
JOIN trips t ON t.trip_id = lt.trip_id
WHERE lt.origin_station_id = ?
  AND t.service_id IN (SELECT service_id FROM _active_service_ids)
  AND lt.departure_seconds >= ?
ORDER BY lt.departure_seconds
"""

_TRANSFERS_FROM_LEG_SQL = """
SELECT tt.transfer_id, tt.station_id, tt.buffer_minutes,
       lt.leg_id, lt.line_id, lt.origin_station_id, lt.destination_station_id,
       lt.departure_seconds, lt.arrival_seconds
FROM transfer_templates tt
JOIN trips t_from ON t_from.trip_id = tt.from_trip_id
JOIN trips t_to ON t_to.trip_id = tt.to_trip_id
JOIN leg_templates lt ON lt.leg_id = tt.to_leg_id
WHERE tt.from_leg_id = ?
  AND t_from.service_id IN (SELECT service_id FROM _active_service_ids)
  AND t_to.service_id IN (SELECT service_id FROM _active_service_ids)
"""


def _resolve_active_service_ids(conn: duckdb.DuckDBPyConnection, service_date: date) -> None:
    weekday_col = WEEKDAY_COLUMNS[service_date.weekday()]
    sql = _ACTIVE_SERVICES_SQL.format(weekday_col=weekday_col)
    conn.execute(sql, [service_date, service_date, service_date, service_date])


class _DistributionCache:
    """Per-search-call cache of line_id -> delay_distribution_minutes, so a
    line_id used by several legs in the same search only hits the warehouse
    once. Distributions are date-independent (DATA_SPEC.md §4), so nothing
    here needs to vary by service_date."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn
        self._cache: dict[str, dict[str, float]] = {}

    def get(self, line_id: str) -> dict[str, float]:
        if line_id not in self._cache:
            rows = self._conn.execute(
                "SELECT bucket_minutes, probability FROM delay_distributions WHERE line_id = ?",
                [line_id],
            ).fetchall()
            self._cache[line_id] = {str(bucket): probability for bucket, probability in rows}
        return self._cache[line_id]


def _materialize_leg(
    row: tuple, service_date: date, distributions: _DistributionCache
) -> Leg:
    leg_id, line_id, origin_id, destination_id, departure_seconds, arrival_seconds = row
    return Leg(
        leg_id=leg_id,
        line_id=line_id,
        origin_station_id=origin_id,
        destination_station_id=destination_id,
        scheduled_departure=_anchor_datetime(departure_seconds, service_date),
        scheduled_arrival=_anchor_datetime(arrival_seconds, service_date),
        delay_distribution_minutes=distributions.get(line_id),
    )


def find_candidate_routes(
    conn: duckdb.DuckDBPyConnection,
    origin_id: str,
    destination_id: str,
    departure_time: datetime,
    service_date: date,
    legs_by_id: dict[str, Leg],
    transfers_by_id: dict[str, Transfer],
) -> list[Route]:
    """DuckDB-backed sibling of pipelines.route_search.find_candidate_routes.

    legs_by_id/transfers_by_id are mutated in place with every Leg/Transfer
    object this call resolves, so engine.py can look any of them up by id
    afterward exactly as it does for the Phase 1/2 in-memory dataset.
    """
    if origin_id == destination_id:
        return []

    _resolve_active_service_ids(conn, service_date)
    distributions = _DistributionCache(conn)

    cutoff_seconds = int((departure_time - datetime.combine(service_date, time.min)).total_seconds())

    routes: list[Route] = []

    origin_rows = conn.execute(_ORIGIN_LEGS_SQL, [origin_id, cutoff_seconds]).fetchall()
    for origin_row in origin_rows:
        leg_a = _materialize_leg(origin_row, service_date, distributions)
        legs_by_id[leg_a.leg_id] = leg_a

        if leg_a.destination_station_id == destination_id:
            routes.append(
                Route(
                    route_id=f"RS_DIRECT_{leg_a.leg_id}",
                    legs=[leg_a.leg_id],
                    transfers=[],
                    origin_station_id=origin_id,
                    destination_station_id=destination_id,
                    scheduled_departure=leg_a.scheduled_departure,
                    scheduled_arrival=leg_a.scheduled_arrival,
                )
            )

        transfer_1_rows = conn.execute(_TRANSFERS_FROM_LEG_SQL, [leg_a.leg_id]).fetchall()
        for t1_transfer_id, t1_station_id, t1_buffer, *leg_b_row in transfer_1_rows:
            leg_b = _materialize_leg(tuple(leg_b_row), service_date, distributions)
            legs_by_id[leg_b.leg_id] = leg_b
            transfer_1 = Transfer(
                transfer_id=t1_transfer_id,
                station_id=t1_station_id,
                from_leg_id=leg_a.leg_id,
                to_leg_id=leg_b.leg_id,
                scheduled_buffer_minutes=t1_buffer,
            )
            transfers_by_id[transfer_1.transfer_id] = transfer_1

            if leg_b.destination_station_id == destination_id:
                routes.append(
                    Route(
                        route_id=f"RS_XFER1_{transfer_1.transfer_id}",
                        legs=[leg_a.leg_id, leg_b.leg_id],
                        transfers=[transfer_1.transfer_id],
                        origin_station_id=origin_id,
                        destination_station_id=destination_id,
                        scheduled_departure=leg_a.scheduled_departure,
                        scheduled_arrival=leg_b.scheduled_arrival,
                    )
                )

            # A second transfer that lands back at the origin isn't a
            # journey anyone wants surfaced as a candidate route.
            if leg_b.destination_station_id == origin_id:
                continue

            transfer_2_rows = conn.execute(_TRANSFERS_FROM_LEG_SQL, [leg_b.leg_id]).fetchall()
            for t2_transfer_id, t2_station_id, t2_buffer, *leg_c_row in transfer_2_rows:
                leg_c = _materialize_leg(tuple(leg_c_row), service_date, distributions)
                if leg_c.destination_station_id != destination_id:
                    continue
                legs_by_id[leg_c.leg_id] = leg_c
                transfer_2 = Transfer(
                    transfer_id=t2_transfer_id,
                    station_id=t2_station_id,
                    from_leg_id=leg_b.leg_id,
                    to_leg_id=leg_c.leg_id,
                    scheduled_buffer_minutes=t2_buffer,
                )
                transfers_by_id[transfer_2.transfer_id] = transfer_2
                routes.append(
                    Route(
                        route_id=f"RS_XFER2_{transfer_1.transfer_id}_{transfer_2.transfer_id}",
                        legs=[leg_a.leg_id, leg_b.leg_id, leg_c.leg_id],
                        transfers=[transfer_1.transfer_id, transfer_2.transfer_id],
                        origin_station_id=origin_id,
                        destination_station_id=destination_id,
                        scheduled_departure=leg_a.scheduled_departure,
                        scheduled_arrival=leg_c.scheduled_arrival,
                    )
                )

    routes.sort(key=lambda r: r.scheduled_departure)
    return routes


def calendar_window(conn: duckdb.DuckDBPyConnection) -> tuple[date, date]:
    """Min/max date the ingested calendar.txt covers -- used to bound the
    Streamlit date picker (SPEC.md §5.1). calendar_dates.txt
    exceptions are deliberately not consulted here; they add/remove services
    within roughly that range rather than meaningfully extending it (v1
    simplification, consistent with the rest of this module's scope)."""
    row = conn.execute("SELECT MIN(start_date), MAX(end_date) FROM service_calendar").fetchone()
    return row[0], row[1]
