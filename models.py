"""Pydantic data models for DB Risk & Rescue, per SPEC.md §2."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class Station(BaseModel):
    """SPEC.md §2.1"""

    station_id: str
    name: str
    # SPEC.md §3.6.1's station-tier MCT classification (no per-station
    # min_transfer_time exists in either real GTFS.DE feed -- see
    # pipelines/gtfs_ingest.py's classify_station_mct). Defaults to the
    # standard-station value so pre-existing fixtures (mock_data.json) that
    # don't set it validate unchanged.
    mct_minutes: int = 5


class Line(BaseModel):
    """SPEC.md §2.2"""

    line_id: str
    type: str
    operator: str


class Leg(BaseModel):
    """SPEC.md §2.3"""

    leg_id: str
    line_id: str
    origin_station_id: str
    destination_station_id: str
    scheduled_departure: datetime
    scheduled_arrival: datetime
    delay_distribution_minutes: dict[str, float]
    # Real GTFS.DE platform_code coverage is sparse and, at this corridor's
    # major hubs specifically, close to 0% (confirmed against the real
    # feed) -- these stay None for almost every leg, by design, rather than
    # a placeholder string. ui_components.py only renders a platform pair
    # when both a leg's arrival and the next leg's departure have one.
    origin_platform: str | None = None
    destination_platform: str | None = None

    @field_validator("delay_distribution_minutes")
    @classmethod
    def probabilities_sum_to_one(cls, v: dict[str, float]) -> dict[str, float]:
        total = sum(v.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"delay_distribution_minutes probabilities must sum to 1.0, got {total}"
            )
        return v


class Transfer(BaseModel):
    """SPEC.md §2.4"""

    transfer_id: str
    station_id: str
    from_leg_id: str
    to_leg_id: str
    scheduled_buffer_minutes: int


class Route(BaseModel):
    """SPEC.md §2.5"""

    route_id: str
    legs: list[str]
    transfers: list[str]
    origin_station_id: str
    destination_station_id: str
    scheduled_departure: datetime
    scheduled_arrival: datetime


class MockDataset(BaseModel):
    """Top-level container matching the structure of mock_data.json."""

    stations: list[Station]
    lines: list[Line]
    legs: list[Leg]
    transfers: list[Transfer]
    routes: list[Route] = Field(default_factory=list)
