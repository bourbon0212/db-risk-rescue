"""Warehouse-backed sibling of routing/route_search.py (DATA_SPEC.md §5, §6).

Same candidate-route algorithm and Route/Leg/Transfer contract as the
Mock/Snapshot in-memory find_candidate_routes() -- direct, single-transfer, and
two-transfer journeys, sorted by scheduled_departure -- but every step's
candidate set comes from a small, origin/date-scoped DuckDB query against
the warehouse's date-agnostic leg_templates/transfer_templates instead of
scanning an in-memory MockDataset.

Resolved Leg/Transfer objects are written into the caller's
legs_by_id/transfers_by_id dicts as a side effect -- the mechanism that keeps
engine.py working without ever loading the whole network into memory
(`DATA_SPEC.md` §6.3 step 4, `SPEC.md` §3.5).
"""

from collections import defaultdict
from datetime import date, datetime, time

import duckdb

from gtfs_time import WEEKDAY_COLUMNS, anchor_datetime
from models import Leg, Route, Transfer

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
       lt.departure_seconds, lt.arrival_seconds, lt.origin_platform, lt.destination_platform
FROM leg_templates lt
JOIN trips t ON t.trip_id = lt.trip_id
WHERE lt.origin_station_id = ?
  AND t.service_id IN (SELECT service_id FROM _active_service_ids)
  AND lt.departure_seconds >= ?
ORDER BY lt.departure_seconds
"""

_TRANSFERS_FROM_LEGS_SQL = """
SELECT tt.from_leg_id, tt.transfer_id, tt.station_id, tt.buffer_minutes,
       lt.leg_id, lt.line_id, lt.origin_station_id, lt.destination_station_id,
       lt.departure_seconds, lt.arrival_seconds, lt.origin_platform, lt.destination_platform
FROM transfer_templates tt
JOIN trips t_from ON t_from.trip_id = tt.from_trip_id
JOIN trips t_to ON t_to.trip_id = tt.to_trip_id
JOIN leg_templates lt ON lt.leg_id = tt.to_leg_id
WHERE tt.from_leg_id = ANY(?)
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

    def preload(self, line_ids: set[str]) -> None:
        """Batch-fetches every line_id not already cached in ONE query,
        instead of paying a separate query per distinct line the first time
        each is seen -- same one-query-per-item pattern this module's leg/
        transfer lookups were fixed to avoid (see find_candidate_routes)."""
        missing = [line_id for line_id in line_ids if line_id not in self._cache]
        if not missing:
            return
        grouped: dict[str, dict[str, float]] = defaultdict(dict)
        rows = self._conn.execute(
            "SELECT line_id, bucket_minutes, probability FROM delay_distributions WHERE line_id = ANY(?)",
            [missing],
        ).fetchall()
        for line_id, bucket, probability in rows:
            grouped[line_id][str(bucket)] = probability
        for line_id in missing:
            self._cache[line_id] = grouped.get(line_id, {})

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
    (
        leg_id, line_id, origin_id, destination_id, departure_seconds, arrival_seconds,
        origin_platform, destination_platform,
    ) = row
    return Leg(
        leg_id=leg_id,
        line_id=line_id,
        origin_station_id=origin_id,
        destination_station_id=destination_id,
        scheduled_departure=anchor_datetime(departure_seconds, service_date),
        scheduled_arrival=anchor_datetime(arrival_seconds, service_date),
        delay_distribution_minutes=distributions.get(line_id),
        origin_platform=origin_platform,
        destination_platform=destination_platform,
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
    """DuckDB-backed sibling of routing.route_search.find_candidate_routes.

    legs_by_id/transfers_by_id are mutated in place with every Leg/Transfer
    object this call resolves, so engine.py can look any of them up by id
    afterward exactly as it does for the Mock/Snapshot in-memory dataset.

    Each hop's transfer candidates -- and each hop's delay_distributions
    lookups (`_DistributionCache.preload`) -- are fetched with ONE batched
    query across every leg/line still in play (`= ANY(?)`) rather than one
    query per leg or per line. An earlier per-item-per-query version measured
    584 individual round trips for a single well-connected search (~5s of
    pure Python<->DuckDB call overhead, not query cost: the filtered columns
    are already indexed). Batching turns that into at most 6 queries total
    per call regardless of how richly connected the graph is.
    """
    if origin_id == destination_id:
        return []

    _resolve_active_service_ids(conn, service_date)
    distributions = _DistributionCache(conn)

    cutoff_seconds = int((departure_time - datetime.combine(service_date, time.min)).total_seconds())

    routes: list[Route] = []

    origin_rows = conn.execute(_ORIGIN_LEGS_SQL, [origin_id, cutoff_seconds]).fetchall()
    distributions.preload({row[1] for row in origin_rows})

    # leg_a_id -> (leg_a, stations visited by the time leg_a lands) for every
    # first-hop leg that neither loops back to the origin nor already
    # reaches the destination (both handled inline, below).
    continuing_leg_a: dict[str, tuple[Leg, set[str]]] = {}
    for origin_row in origin_rows:
        leg_a = _materialize_leg(origin_row, service_date, distributions)
        legs_by_id[leg_a.leg_id] = leg_a

        station_1 = leg_a.destination_station_id
        if station_1 == origin_id:
            # A leg that loops back to the origin makes no progress --
            # not a journey anyone wants surfaced as a candidate route.
            continue

        if station_1 == destination_id:
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
            # Already at the destination: any further transfer from here
            # could only leave and eventually come back to it -- a cycle
            # through the destination rather than a distinct route. Nothing
            # past this point can be legitimate, so stop extending leg_a
            # (and skip the transfer_1 lookup entirely).
            continue

        # Stations visited so far on this path -- any leg landing back on
        # one of these would be a cycle; every candidate must be a simple path.
        continuing_leg_a[leg_a.leg_id] = (leg_a, {origin_id, station_1})

    if not continuing_leg_a:
        routes.sort(key=lambda r: r.scheduled_departure)
        return routes

    transfer_1_rows = conn.execute(_TRANSFERS_FROM_LEGS_SQL, [list(continuing_leg_a)]).fetchall()
    distributions.preload({row[5] for row in transfer_1_rows})
    transfer_1_by_leg_a: dict[str, list[tuple]] = defaultdict(list)
    for row in transfer_1_rows:
        transfer_1_by_leg_a[row[0]].append(row[1:])

    # (leg_a_id, transfer_1, leg_b) for every pairing that neither cycles nor
    # already reaches the destination (also handled inline) -- these are
    # what still need a transfer_2 lookup, batched the same way below.
    continuing_paths: list[tuple[str, Transfer, Leg]] = []
    leg_b_ids_needed: set[str] = set()

    for leg_a_id, (leg_a, visited) in continuing_leg_a.items():
        for t1_transfer_id, t1_station_id, t1_buffer, *leg_b_row in transfer_1_by_leg_a.get(leg_a_id, []):
            leg_b = _materialize_leg(tuple(leg_b_row), service_date, distributions)

            station_2 = leg_b.destination_station_id
            if station_2 in visited:
                # Revisits the origin or leg_a's arrival station: a cycle.
                continue

            legs_by_id[leg_b.leg_id] = leg_b
            transfer_1 = Transfer(
                transfer_id=t1_transfer_id,
                station_id=t1_station_id,
                from_leg_id=leg_a_id,
                to_leg_id=leg_b.leg_id,
                scheduled_buffer_minutes=t1_buffer,
            )
            transfers_by_id[transfer_1.transfer_id] = transfer_1

            if station_2 == destination_id:
                routes.append(
                    Route(
                        route_id=f"RS_XFER1_{transfer_1.transfer_id}",
                        legs=[leg_a_id, leg_b.leg_id],
                        transfers=[transfer_1.transfer_id],
                        origin_station_id=origin_id,
                        destination_station_id=destination_id,
                        scheduled_departure=leg_a.scheduled_departure,
                        scheduled_arrival=leg_b.scheduled_arrival,
                    )
                )
                # Same reasoning as the direct-route case above: already
                # arrived, so stop extending leg_b (skip the transfer_2
                # lookup) instead of exploring a second transfer that could
                # only cycle back through the destination.
                continue

            continuing_paths.append((leg_a_id, transfer_1, leg_b))
            leg_b_ids_needed.add(leg_b.leg_id)

    if continuing_paths:
        transfer_2_rows = conn.execute(_TRANSFERS_FROM_LEGS_SQL, [list(leg_b_ids_needed)]).fetchall()
        distributions.preload({row[5] for row in transfer_2_rows})
        transfer_2_by_leg_b: dict[str, list[tuple]] = defaultdict(list)
        for row in transfer_2_rows:
            transfer_2_by_leg_b[row[0]].append(row[1:])

        for leg_a_id, transfer_1, leg_b in continuing_paths:
            leg_a = continuing_leg_a[leg_a_id][0]
            for t2_transfer_id, t2_station_id, t2_buffer, *leg_c_row in transfer_2_by_leg_b.get(leg_b.leg_id, []):
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
                        legs=[leg_a_id, leg_b.leg_id, leg_c.leg_id],
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
