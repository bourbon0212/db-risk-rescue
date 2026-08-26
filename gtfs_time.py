"""GTFS temporal vocabulary shared by `pipelines/` and `routing/`.

Leg times are stored date-agnostically -- as seconds since midnight of the
nominal service day (`DATA_SPEC.md` §3 step 5, §6) -- so that one warehouse row
serves every date its calendar is active for. *Encoding* that form is an
ingestion job (`pipelines/gtfs_ingest.py`) and *decoding* it is a query job
(`routing/route_search_duckdb.py`), which left the pair with no natural owner:
before this module they lived in the ingest layer, and the query layer reached
across packages for a private name to get at them.

Neither side owns these, and both must agree on them, so they live here.
Same reasoning for `WEEKDAY_COLUMNS`, which was previously declared twice --
once in `calendar_ingest.py` (which never used it) and again, privately, in
`gtfs_scope.py`.

Deliberately a leaf: stdlib only, no first-party imports, so any module in any
package can depend on it without a cycle.
"""

from datetime import date, datetime, timedelta

# calendar.txt's service-day columns in datetime.weekday() order, so that
# WEEKDAY_COLUMNS[some_date.weekday()] names the column governing that date.
WEEKDAY_COLUMNS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def seconds_since_midnight(time_str: str) -> int:
    """Parse a GTFS HH:MM:SS time (hours may exceed 23 for post-midnight
    trips) into seconds since midnight of its nominal service day -- the
    date-agnostic form leg_templates store (`DATA_SPEC.md` §3 step 5), shared
    with parse_gtfs_time below so the anchored and template-based parsers
    can't drift apart on how they read the same column."""
    hours, minutes, seconds = (int(x) for x in time_str.strip().split(":"))
    return hours * 3600 + minutes * 60 + seconds


def anchor_datetime(seconds: int, service_date: date) -> datetime:
    """Turn a date-agnostic seconds-since-midnight offset into a concrete
    datetime anchored on service_date -- the inverse of seconds_since_midnight.

    Hours beyond 23 carry into the following day by construction, which is how
    a 25:15 GTFS departure lands at 01:15 on service_date + 1.
    """
    midnight = datetime.combine(service_date, datetime.min.time())
    return midnight + timedelta(seconds=seconds)


def parse_gtfs_time(time_str: str, service_date: date) -> datetime:
    """Convert a GTFS HH:MM:SS time (hours may exceed 23 for post-midnight
    trips) into a full datetime anchored on service_date."""
    return anchor_datetime(seconds_since_midnight(time_str), service_date)
