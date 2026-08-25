"""GTFS.DE static feed ingestion into Station/Line/Leg/Transfer models.

Per DATA_SPEC.md §3 and §8 (build sequence steps 1-2).
"""

import csv
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from models import Leg, Line, Station, Transfer

# Exact strings engine.py's SERVICE_FREQUENCY_MINUTES keys on (DATA_SPEC.md §3.1).
LINE_TYPES = frozenset({"ICE", "IC", "RE", "RB", "S-Bahn"})

# GTFS "extended route types" fallback, used only when route_short_name
# doesn't carry a recognizable prefix. Extended route 106 ("Regional Rail
# Service") does not distinguish RE from RB, so it has no fallback entry —
# that split can only come from route_short_name.
_ROUTE_TYPE_FALLBACK = {
    "101": "ICE",  # High Speed Rail Service
    "102": "IC",  # Long Distance Trains
    "103": "IC",  # Inter Regional Rail Service
    "109": "S-Bahn",  # Suburban Railway
}

# SPEC.md §3.6.1's station-tier Minimum Connection Time classification. Neither real
# GTFS.DE feed (gtfs_fv_latest.zip, gtfs_rv_latest.zip) ships a transfers.txt
# or any other per-station min_transfer_time -- confirmed by listing both
# archives directly -- so MCT here is a rule-based proxy, not feed data.
MCT_STANDARD_MINUTES = 5
MCT_MAJOR_HUB_MINUTES = 10
# Stations at or above this percentile of leg-endpoint touch-count get the
# major-hub MCT; the rest get the standard one.
_MAJOR_HUB_TOUCH_PERCENTILE = 75


def classify_station_mct(station_touch_pairs: Iterable[tuple[str, str]]) -> dict[str, int]:
    """Station-tier MCT classifier, built from trip-touch counts.

    `station_touch_pairs` is any iterable of (origin_station_id,
    destination_station_id) pairs -- one per Leg or LegTemplate -- so the
    same classifier serves both the Snapshot (Leg) and Warehouse (LegTemplate)
    build paths without depending on either type. How many leg endpoints (as
    either origin or destination) touch a station is a cheap, data-driven
    stand-in for its interchange complexity/platform-walk distance in the
    absence of any real per-station signal. Stations at or above
    _MAJOR_HUB_TOUCH_PERCENTILE of that distribution get MCT_MAJOR_HUB_MINUTES;
    everyone else gets MCT_STANDARD_MINUTES.
    """
    touch_counts: dict[str, int] = defaultdict(int)
    for origin_id, destination_id in station_touch_pairs:
        touch_counts[origin_id] += 1
        touch_counts[destination_id] += 1

    if not touch_counts:
        return {}

    sorted_counts = sorted(touch_counts.values())
    threshold_idx = math.ceil(_MAJOR_HUB_TOUCH_PERCENTILE / 100 * len(sorted_counts)) - 1
    threshold = sorted_counts[min(max(threshold_idx, 0), len(sorted_counts) - 1)]

    return {
        station_id: MCT_MAJOR_HUB_MINUTES if count >= threshold else MCT_STANDARD_MINUTES
        for station_id, count in touch_counts.items()
    }


def _normalize_line_type(route_short_name: str, route_type: str) -> str:
    """Normalize a GTFS route to one of LINE_TYPES, or raise ValueError.

    route_short_name is authoritative (it's the only signal that can tell RE
    apart from RB); route_type is a fallback for the rare row with no usable
    short name. Anything neither can resolve fails loudly, per DATA_SPEC.md
    §3.1: a bad line type must never reach engine.py at simulation time.
    """
    prefix = route_short_name.strip().upper()
    if prefix.startswith("ICE"):
        return "ICE"
    if prefix.startswith("IC") or prefix.startswith("EC"):
        return "IC"
    if prefix.startswith("RE"):
        return "RE"
    if prefix.startswith("RB"):
        return "RB"
    if len(prefix) >= 2 and prefix[0] == "S" and prefix[1].isdigit():
        return "S-Bahn"

    fallback = _ROUTE_TYPE_FALLBACK.get(route_type.strip())
    if fallback is not None:
        return fallback

    raise ValueError(
        "Cannot normalize GTFS route to a known line type: "
        f"route_short_name={route_short_name!r}, route_type={route_type!r}"
    )


def parse_stations(gtfs_dir: Path) -> list[Station]:
    """One GTFS parent station (location_type=1) -> one Station.

    Individual platform/stop rows (location_type 0 or blank) are skipped;
    DATA_SPEC.md §3 step 3 calls for the parent station only.
    """
    stations = []
    with (gtfs_dir / "stops.txt").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("location_type", "").strip() != "1":
                continue
            stations.append(Station(station_id=row["stop_id"], name=row["stop_name"]))
    return stations


def _line_id_for_route(route_short_name: str, route_id: str) -> str:
    """Derive the human-readable Line.line_id we expose from a GTFS route.

    Leg.line_id is what ui_components.py's timeline prints verbatim, so it
    needs to be display-friendly -- space-separated ("ICE 15", "RE 1"),
    matching DB Navigator / platform-display conventions, not the opaque raw
    GTFS route_id ("ROUTE_ICE15" or similar internal feed identifier) and
    not underscore-joined. Falls back to route_id only if route_short_name
    is blank. Any internal whitespace is collapsed to a single space.
    """
    short_name = route_short_name.strip()
    return " ".join(short_name.split()) if short_name else route_id


def _load_route_line_ids(gtfs_dir: Path) -> dict[str, str]:
    """Map every GTFS route_id to its display-friendly line_id, so
    parse_lines and parse_legs agree on the same Line.line_id / Leg.line_id
    values instead of drifting apart."""
    mapping: dict[str, str] = {}
    with (gtfs_dir / "routes.txt").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            mapping[row["route_id"]] = _line_id_for_route(
                row.get("route_short_name", ""), row["route_id"]
            )
    return mapping


def parse_lines(gtfs_dir: Path) -> list[Line]:
    """One GTFS route -> one Line, with type normalized per §3.1."""
    agency_names: dict[str, str] = {}
    agency_path = gtfs_dir / "agency.txt"
    if agency_path.exists():
        with agency_path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                agency_names[row["agency_id"]] = row["agency_name"]

    lines = []
    with (gtfs_dir / "routes.txt").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            line_type = _normalize_line_type(
                row.get("route_short_name", ""), row.get("route_type", "")
            )
            agency_id = row.get("agency_id", "")
            operator = agency_names.get(agency_id, agency_id)
            line_id = _line_id_for_route(row.get("route_short_name", ""), row["route_id"])
            lines.append(Line(line_id=line_id, type=line_type, operator=operator))
    return lines


def _seconds_since_midnight(time_str: str) -> int:
    """Parse a GTFS HH:MM:SS time (hours may exceed 23 for post-midnight
    trips) into seconds since midnight of its nominal service day -- the
    date-agnostic form leg_templates store (DATA_SPEC.md §3 step 5), shared
    with _parse_gtfs_time below so the anchored and template-based parsers
    can't drift apart on how they read the same column."""
    hours, minutes, seconds = (int(x) for x in time_str.strip().split(":"))
    return hours * 3600 + minutes * 60 + seconds


def _anchor_datetime(seconds_since_midnight: int, service_date: date) -> datetime:
    """Turn a date-agnostic seconds-since-midnight offset into a concrete
    datetime anchored on service_date -- the inverse of _seconds_since_midnight."""
    midnight = datetime.combine(service_date, datetime.min.time())
    return midnight + timedelta(seconds=seconds_since_midnight)


def _parse_gtfs_time(time_str: str, service_date: date) -> datetime:
    """Convert a GTFS HH:MM:SS time (hours may exceed 23 for post-midnight
    trips) into a full datetime anchored on service_date."""
    return _anchor_datetime(_seconds_since_midnight(time_str), service_date)


def _load_stop_to_station_map(gtfs_dir: Path) -> dict[str, str]:
    """Map every GTFS stop_id to its parent station's stop_id.

    A stop with no parent_station is its own station (mirrors parse_stations'
    treatment of location_type=1 rows).
    """
    mapping: dict[str, str] = {}
    with (gtfs_dir / "stops.txt").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            parent = row.get("parent_station", "").strip()
            mapping[row["stop_id"]] = parent if parent else row["stop_id"]
    return mapping


def _load_stop_to_platform_map(gtfs_dir: Path) -> dict[str, str]:
    """Map every GTFS stop_id to its platform_code, "" if blank or the
    column is absent entirely (older/smaller fixtures).

    Real GTFS.DE coverage is sparse and uneven -- only a handful of this
    corridor's stations carry it (DATA_SPEC.md §3.3), so most legs end up
    with no platform on one or both ends. That's the real data, not a
    parsing gap; see _platform_or_none.
    """
    mapping: dict[str, str] = {}
    with (gtfs_dir / "stops.txt").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            mapping[row["stop_id"]] = row.get("platform_code", "").strip()
    return mapping


def _platform_or_none(stop_to_platform: dict[str, str], stop_id: str) -> str | None:
    return stop_to_platform.get(stop_id, "").strip() or None


def parse_legs(gtfs_dir: Path, service_date: date) -> list[Leg]:
    """Walk each trip's stop_times in sequence, turning every consecutive
    stop pair into a Leg (DATA_SPEC.md §3 step 5).

    delay_distribution_minutes is a placeholder here; Pipeline 2
    (delay_aggregation.py) overwrites it with real empirical distributions.
    """
    stop_to_station = _load_stop_to_station_map(gtfs_dir)
    stop_to_platform = _load_stop_to_platform_map(gtfs_dir)
    route_line_ids = _load_route_line_ids(gtfs_dir)

    trip_to_line: dict[str, str] = {}
    with (gtfs_dir / "trips.txt").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            trip_to_line[row["trip_id"]] = route_line_ids[row["route_id"]]

    stop_times_by_trip: dict[str, list[dict]] = defaultdict(list)
    with (gtfs_dir / "stop_times.txt").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            stop_times_by_trip[row["trip_id"]].append(row)

    legs = []
    for trip_id, rows in stop_times_by_trip.items():
        rows.sort(key=lambda r: int(r["stop_sequence"]))
        line_id = trip_to_line[trip_id]
        for i in range(len(rows) - 1):
            origin_row, dest_row = rows[i], rows[i + 1]
            legs.append(
                Leg(
                    leg_id=f"{trip_id}::{i}",
                    line_id=line_id,
                    origin_station_id=stop_to_station[origin_row["stop_id"]],
                    destination_station_id=stop_to_station[dest_row["stop_id"]],
                    scheduled_departure=_parse_gtfs_time(
                        origin_row["departure_time"], service_date
                    ),
                    scheduled_arrival=_parse_gtfs_time(dest_row["arrival_time"], service_date),
                    delay_distribution_minutes={"0": 1.0},
                    origin_platform=_platform_or_none(stop_to_platform, origin_row["stop_id"]),
                    destination_platform=_platform_or_none(stop_to_platform, dest_row["stop_id"]),
                )
            )
    return legs


@dataclass(frozen=True)
class TripRecord:
    """One GTFS trip, stripped down to the fields the date-agnostic templates
    need to resolve calendar membership (DATA_SPEC.md §6.3)."""

    trip_id: str
    line_id: str
    service_id: str


@dataclass(frozen=True)
class LegTemplate:
    """Date-agnostic counterpart to Leg (DATA_SPEC.md §6.2): one row per
    consecutive stop pair, same as parse_legs() produces, but with
    departure/arrival stored as seconds-since-midnight-of-service-day
    instead of a concrete datetime, so the same row serves every date the
    parent trip's service_id is active on -- no per-day row multiplication."""

    leg_id: str
    trip_id: str
    line_id: str
    sequence_index: int
    origin_station_id: str
    destination_station_id: str
    departure_seconds: int
    arrival_seconds: int
    origin_platform: str | None = None
    destination_platform: str | None = None


@dataclass(frozen=True)
class TransferTemplate:
    """Date-agnostic counterpart to Transfer (DATA_SPEC.md §6.2). from_trip_id/
    to_trip_id are denormalized from the parent LegTemplates so a query-time
    calendar filter (both trips' service_ids active on the queried date)
    needs no join back to `trips`."""

    transfer_id: str
    station_id: str
    from_leg_id: str
    to_leg_id: str
    from_trip_id: str
    to_trip_id: str
    buffer_minutes: int


def parse_trips(gtfs_dir: Path) -> list[TripRecord]:
    """One GTFS trip -> one TripRecord (trip_id, line_id, service_id)."""
    route_line_ids = _load_route_line_ids(gtfs_dir)
    trips = []
    with (gtfs_dir / "trips.txt").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            trips.append(
                TripRecord(
                    trip_id=row["trip_id"],
                    line_id=route_line_ids[row["route_id"]],
                    service_id=row["service_id"],
                )
            )
    return trips


def parse_leg_templates(gtfs_dir: Path) -> list[LegTemplate]:
    """Date-agnostic sibling of parse_legs(): walks each trip's stop_times in
    sequence exactly the same way, but emits LegTemplate rows (seconds-since-
    midnight) instead of Leg rows anchored to one service_date. Used by
    the Warehouse build (pipelines/build_warehouse.py); parse_legs() still
    backs the Snapshot single-date JSON build.
    """
    stop_to_station = _load_stop_to_station_map(gtfs_dir)
    stop_to_platform = _load_stop_to_platform_map(gtfs_dir)
    route_line_ids = _load_route_line_ids(gtfs_dir)

    trip_to_line: dict[str, str] = {}
    with (gtfs_dir / "trips.txt").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            trip_to_line[row["trip_id"]] = route_line_ids[row["route_id"]]

    stop_times_by_trip: dict[str, list[dict]] = defaultdict(list)
    with (gtfs_dir / "stop_times.txt").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            stop_times_by_trip[row["trip_id"]].append(row)

    templates = []
    for trip_id, rows in stop_times_by_trip.items():
        rows.sort(key=lambda r: int(r["stop_sequence"]))
        line_id = trip_to_line[trip_id]
        for i in range(len(rows) - 1):
            origin_row, dest_row = rows[i], rows[i + 1]
            templates.append(
                LegTemplate(
                    leg_id=f"{trip_id}::{i}",
                    trip_id=trip_id,
                    line_id=line_id,
                    sequence_index=i,
                    origin_station_id=stop_to_station[origin_row["stop_id"]],
                    destination_station_id=stop_to_station[dest_row["stop_id"]],
                    departure_seconds=_seconds_since_midnight(origin_row["departure_time"]),
                    arrival_seconds=_seconds_since_midnight(dest_row["arrival_time"]),
                    origin_platform=_platform_or_none(stop_to_platform, origin_row["stop_id"]),
                    destination_platform=_platform_or_none(stop_to_platform, dest_row["stop_id"]),
                )
            )
    return templates


@dataclass(frozen=True)
class _CorridorLegRow:
    """One corridor-to-corridor hop within a trip, resolved but not yet
    turned into a Leg/LegTemplate -- shared intermediate result for
    parse_corridor_legs/parse_corridor_leg_templates below."""

    trip_id: str
    line_id: str
    sequence_index: int
    origin_station_id: str
    destination_station_id: str
    origin_departure_time: str
    destination_arrival_time: str
    origin_platform: str | None
    destination_platform: str | None


def _walk_corridor_legs(gtfs_dir: Path, corridor_stop_ids: set[str]) -> list[_CorridorLegRow]:
    """For every trip, reduce its stop_sequence-sorted stop_times down to
    just the stops touching a corridor station (order preserved), then pair
    up each consecutive corridor stop with the next.

    Walking the *corridor-filtered* subsequence rather than physically
    adjacent stop pairs is what keeps hub-to-hub connections alive when a
    trip calls at non-corridor stops in between. It fixes a bug that silently
    dropped most of the long-distance graph -- DATA_SPEC.md §3.2.
    """
    stop_to_station = _load_stop_to_station_map(gtfs_dir)
    stop_to_platform = _load_stop_to_platform_map(gtfs_dir)
    route_line_ids = _load_route_line_ids(gtfs_dir)

    trip_to_line: dict[str, str] = {}
    with (gtfs_dir / "trips.txt").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            trip_to_line[row["trip_id"]] = route_line_ids[row["route_id"]]

    stop_times_by_trip: dict[str, list[dict]] = defaultdict(list)
    with (gtfs_dir / "stop_times.txt").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            stop_times_by_trip[row["trip_id"]].append(row)

    corridor_rows: list[_CorridorLegRow] = []
    for trip_id, rows in stop_times_by_trip.items():
        rows.sort(key=lambda r: int(r["stop_sequence"]))
        line_id = trip_to_line[trip_id]
        touching_corridor = [r for r in rows if stop_to_station[r["stop_id"]] in corridor_stop_ids]
        for i in range(len(touching_corridor) - 1):
            origin_row, dest_row = touching_corridor[i], touching_corridor[i + 1]
            corridor_rows.append(
                _CorridorLegRow(
                    trip_id=trip_id,
                    line_id=line_id,
                    sequence_index=i,
                    origin_station_id=stop_to_station[origin_row["stop_id"]],
                    destination_station_id=stop_to_station[dest_row["stop_id"]],
                    origin_departure_time=origin_row["departure_time"],
                    destination_arrival_time=dest_row["arrival_time"],
                    origin_platform=_platform_or_none(stop_to_platform, origin_row["stop_id"]),
                    destination_platform=_platform_or_none(stop_to_platform, dest_row["stop_id"]),
                )
            )
    return corridor_rows


def parse_corridor_legs(gtfs_dir: Path, service_date: date, corridor_stop_ids: set[str]) -> list[Leg]:
    """Corridor-aware sibling of parse_legs() (Snapshot JSON build path) --
    see _walk_corridor_legs for why this exists instead of parse_legs() plus
    a post-hoc corridor-to-corridor filter."""
    return [
        Leg(
            leg_id=f"{r.trip_id}::{r.sequence_index}",
            line_id=r.line_id,
            origin_station_id=r.origin_station_id,
            destination_station_id=r.destination_station_id,
            scheduled_departure=_parse_gtfs_time(r.origin_departure_time, service_date),
            scheduled_arrival=_parse_gtfs_time(r.destination_arrival_time, service_date),
            delay_distribution_minutes={"0": 1.0},
            origin_platform=r.origin_platform,
            destination_platform=r.destination_platform,
        )
        for r in _walk_corridor_legs(gtfs_dir, corridor_stop_ids)
    ]


def parse_corridor_leg_templates(gtfs_dir: Path, corridor_stop_ids: set[str]) -> list[LegTemplate]:
    """Corridor-aware sibling of parse_leg_templates() (Warehouse build path)
    -- see _walk_corridor_legs for why this exists instead of
    parse_leg_templates() plus a post-hoc corridor-to-corridor filter."""
    return [
        LegTemplate(
            leg_id=f"{r.trip_id}::{r.sequence_index}",
            trip_id=r.trip_id,
            line_id=r.line_id,
            sequence_index=r.sequence_index,
            origin_station_id=r.origin_station_id,
            destination_station_id=r.destination_station_id,
            departure_seconds=_seconds_since_midnight(r.origin_departure_time),
            arrival_seconds=_seconds_since_midnight(r.destination_arrival_time),
            origin_platform=r.origin_platform,
            destination_platform=r.destination_platform,
        )
        for r in _walk_corridor_legs(gtfs_dir, corridor_stop_ids)
    ]


def derive_transfer_templates(
    leg_templates: list[LegTemplate], min_window_minutes: int = 2, max_window_minutes: int = 60
) -> list[TransferTemplate]:
    """Date-agnostic sibling of derive_transfers(): same station-matching and
    window logic, operating on seconds-of-day gaps instead of datetime gaps,
    computed once regardless of how many calendar days the templates cover.
    Whether a given TransferTemplate is actually usable on a specific query
    date (i.e. both trips' service_ids are active that day) is resolved at
    route-search time (pipelines/route_search_duckdb.py), not here.
    """
    arrivals_by_station: dict[str, list[LegTemplate]] = defaultdict(list)
    departures_by_station: dict[str, list[LegTemplate]] = defaultdict(list)
    for leg in leg_templates:
        arrivals_by_station[leg.destination_station_id].append(leg)
        departures_by_station[leg.origin_station_id].append(leg)

    transfers = []
    for station_id, arriving_legs in arrivals_by_station.items():
        for arriving in arriving_legs:
            for departing in departures_by_station.get(station_id, []):
                if arriving.trip_id == departing.trip_id:
                    continue
                buffer_minutes = (departing.departure_seconds - arriving.arrival_seconds) // 60
                if min_window_minutes <= buffer_minutes <= max_window_minutes:
                    transfers.append(
                        TransferTemplate(
                            transfer_id=f"TR_{arriving.leg_id}__{departing.leg_id}",
                            station_id=station_id,
                            from_leg_id=arriving.leg_id,
                            to_leg_id=departing.leg_id,
                            from_trip_id=arriving.trip_id,
                            to_trip_id=departing.trip_id,
                            buffer_minutes=buffer_minutes,
                        )
                    )
    return transfers


def _trip_id_of(leg_id: str) -> str:
    """Recover the trip_id encoded in a parse_legs-produced leg_id."""
    return leg_id.rsplit("::", 1)[0]


def derive_transfers(
    legs: list[Leg], min_window_minutes: int = 2, max_window_minutes: int = 60
) -> list[Transfer]:
    """Derive Transfers from arriving/departing leg pairs at the same station
    whose gap falls within [min_window_minutes, max_window_minutes]
    (DATA_SPEC.md §3 step 6, §9.4).

    A departing leg that's just the same trip continuing through the station
    is not a transfer, even if its gap happens to fall in the window.
    """
    arrivals_by_station: dict[str, list[Leg]] = defaultdict(list)
    departures_by_station: dict[str, list[Leg]] = defaultdict(list)
    for leg in legs:
        arrivals_by_station[leg.destination_station_id].append(leg)
        departures_by_station[leg.origin_station_id].append(leg)

    transfers = []
    for station_id, arriving_legs in arrivals_by_station.items():
        for arriving in arriving_legs:
            for departing in departures_by_station.get(station_id, []):
                if _trip_id_of(arriving.leg_id) == _trip_id_of(departing.leg_id):
                    continue
                buffer_minutes = int(
                    (departing.scheduled_departure - arriving.scheduled_arrival).total_seconds()
                    // 60
                )
                if min_window_minutes <= buffer_minutes <= max_window_minutes:
                    transfers.append(
                        Transfer(
                            transfer_id=f"TR_{arriving.leg_id}__{departing.leg_id}",
                            station_id=station_id,
                            from_leg_id=arriving.leg_id,
                            to_leg_id=departing.leg_id,
                            scheduled_buffer_minutes=buffer_minutes,
                        )
                    )
    return transfers
