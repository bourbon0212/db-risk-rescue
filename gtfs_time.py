"""GTFS temporal vocabulary shared by `pipelines/` and `routing/`.

Leg times are stored as seconds since midnight of the nominal service day, so
encoding them is an ingestion job and decoding them a query job: neither
package owns the representation and both must agree on it. Why that lands in a
module of its own: `DATA_SPEC.md` §2. The storage contract itself: §3 step 5.

Deliberately a leaf -- stdlib only, no first-party imports.
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
