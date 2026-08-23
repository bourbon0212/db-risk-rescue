"""Monte Carlo delay propagation & risk scoring engine, per SPEC.md Section 3."""

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from models import Leg, Line, MockDataset, Route, Transfer

# SPEC.md §2.6 — static lookup, not part of the mock JSON.
SERVICE_FREQUENCY_MINUTES: dict[str, int] = {
    "ICE": 60,
    "IC": 60,
    "RE": 60,
    "RB": 60,
    "S-Bahn": 20,
}


def get_headway_minutes(line_type: str) -> int:
    try:
        return SERVICE_FREQUENCY_MINUTES[line_type]
    except KeyError:
        raise ValueError(f"No service frequency defined for line type '{line_type}'")


def index_dataset(
    dataset: MockDataset,
) -> tuple[dict[str, Leg], dict[str, Transfer], dict[str, Line]]:
    """Convenience lookup tables keyed by id, built from a loaded MockDataset."""
    legs_by_id = {leg.leg_id: leg for leg in dataset.legs}
    transfers_by_id = {t.transfer_id: t for t in dataset.transfers}
    lines_by_id = {line.line_id: line for line in dataset.lines}
    return legs_by_id, transfers_by_id, lines_by_id


def sample_delay_minutes(distribution: dict[str, float], rng: random.Random) -> int:
    """Draw one realized delay (minutes) from an empirical bucket distribution."""
    buckets = [int(bucket) for bucket in distribution.keys()]
    weights = list(distribution.values())
    return rng.choices(buckets, weights=weights, k=1)[0]


def transfer_miss_probability(upstream_leg: Leg, transfer: Transfer) -> float:
    """SPEC.md §3.1 — analytic CDF lookup against the upstream leg's distribution.

    P(miss) = P(delay_from_leg > scheduled_buffer_minutes)
    """
    buffer = transfer.scheduled_buffer_minutes
    return sum(
        prob
        for bucket_str, prob in upstream_leg.delay_distribution_minutes.items()
        if int(bucket_str) > buffer
    )


def _next_periodic_departure(
    leg_scheduled_departure: datetime,
    realized_arrival_time: datetime,
    headway_minutes: int,
) -> datetime:
    """SPEC.md §3.2 Step 4 — next periodic departure of the downstream line."""
    elapsed_minutes = (realized_arrival_time - leg_scheduled_departure).total_seconds() / 60.0
    n_headways = math.ceil(elapsed_minutes / headway_minutes)
    return leg_scheduled_departure + timedelta(minutes=headway_minutes * n_headways)


def _mean_datetime(values: list[datetime]) -> datetime:
    epoch = values[0]
    avg_offset_seconds = sum((v - epoch).total_seconds() for v in values) / len(values)
    return epoch + timedelta(seconds=avg_offset_seconds)


def _percentile(sorted_values: list[datetime], pct: float) -> datetime:
    """Nearest-rank percentile over a pre-sorted (ascending) list of datetimes."""
    if not sorted_values:
        raise ValueError("Cannot compute percentile of an empty list")
    idx = math.ceil(pct / 100 * len(sorted_values)) - 1
    idx = min(max(idx, 0), len(sorted_values) - 1)
    return sorted_values[idx]


@dataclass
class TransferRisk:
    transfer_id: str
    miss_probability: float
    simulated_miss_rate: float


@dataclass
class RouteSimulationResult:
    route_id: str
    n_iterations: int
    mean_eta: datetime
    p85_eta: datetime
    p90_eta: datetime
    transfer_risks: list[TransferRisk]
    simulated_arrivals: list[datetime] = field(repr=False)


def simulate_route(
    route: Route,
    legs_by_id: dict[str, Leg],
    transfers_by_id: dict[str, Transfer],
    lines_by_id: dict[str, Line],
    n_iterations: int = 1000,
    rng: random.Random | None = None,
) -> RouteSimulationResult:
    """SPEC.md §3.2 — Monte Carlo simulation of a route's realized arrival time."""
    if rng is None:
        rng = random.Random()

    ordered_legs = [legs_by_id[leg_id] for leg_id in route.legs]
    ordered_transfers = [transfers_by_id[t_id] for t_id in route.transfers]

    if len(ordered_transfers) != max(len(ordered_legs) - 1, 0):
        raise ValueError(
            f"Route {route.route_id} has {len(ordered_legs)} legs but "
            f"{len(ordered_transfers)} transfers (expected {len(ordered_legs) - 1})"
        )
    for i, transfer in enumerate(ordered_transfers):
        if transfer.from_leg_id != ordered_legs[i].leg_id or (
            transfer.to_leg_id != ordered_legs[i + 1].leg_id
        ):
            raise ValueError(
                f"Transfer {transfer.transfer_id} does not connect legs "
                f"{ordered_legs[i].leg_id} -> {ordered_legs[i + 1].leg_id}"
            )

    simulated_arrivals: list[datetime] = []
    miss_counts = [0] * len(ordered_transfers)

    for _ in range(n_iterations):
        first_leg = ordered_legs[0]
        delay = sample_delay_minutes(first_leg.delay_distribution_minutes, rng)
        realized_arrival = first_leg.scheduled_arrival + timedelta(minutes=delay)

        for i, transfer in enumerate(ordered_transfers):
            downstream_leg = ordered_legs[i + 1]

            if realized_arrival <= downstream_leg.scheduled_departure:
                # Step 3: connection holds — downstream leg departs on schedule.
                delay = sample_delay_minutes(downstream_leg.delay_distribution_minutes, rng)
                realized_arrival = downstream_leg.scheduled_arrival + timedelta(minutes=delay)
            else:
                # Step 4: connection missed — resolve via next periodic departure.
                miss_counts[i] += 1
                line_type = lines_by_id[downstream_leg.line_id].type
                headway = get_headway_minutes(line_type)
                next_departure = _next_periodic_departure(
                    downstream_leg.scheduled_departure, realized_arrival, headway
                )
                leg_duration = downstream_leg.scheduled_arrival - downstream_leg.scheduled_departure
                fresh_delay = sample_delay_minutes(downstream_leg.delay_distribution_minutes, rng)
                realized_arrival = next_departure + leg_duration + timedelta(minutes=fresh_delay)

        simulated_arrivals.append(realized_arrival)

    simulated_arrivals.sort()

    transfer_risks = [
        TransferRisk(
            transfer_id=transfer.transfer_id,
            miss_probability=transfer_miss_probability(ordered_legs[i], transfer),
            simulated_miss_rate=miss_counts[i] / n_iterations,
        )
        for i, transfer in enumerate(ordered_transfers)
    ]

    return RouteSimulationResult(
        route_id=route.route_id,
        n_iterations=n_iterations,
        mean_eta=_mean_datetime(simulated_arrivals),
        p85_eta=_percentile(simulated_arrivals, 85),
        p90_eta=_percentile(simulated_arrivals, 90),
        transfer_risks=transfer_risks,
        simulated_arrivals=simulated_arrivals,
    )
