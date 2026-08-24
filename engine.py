"""Monte Carlo delay propagation & risk scoring engine, per SPEC.md Section 3."""

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from models import Leg, Line, MockDataset, Route, Transfer
from pipelines.route_search import find_candidate_routes

# SPEC.md §2.6 — static lookup, not part of the mock JSON.
SERVICE_FREQUENCY_MINUTES: dict[str, int] = {
    "ICE": 60,
    "IC": 60,
    "RE": 60,
    "RB": 60,
    "S-Bahn": 20,
}

# SPEC.md §3.4 — must stay in sync with pipelines/route_search.py's 2-transfer cap.
MAX_TOTAL_TRANSFERS = 2


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


@dataclass
class FallbackPlan:
    """SPEC.md §3.4 — the best alternative Route from a transfer's station to
    the journey's final destination, pre-resolved to Leg/Transfer objects so
    the Monte Carlo loop never has to look them up by id mid-iteration."""

    route: Route
    legs: list[Leg]
    transfers: list[Transfer]


def precompute_fallback_plans(
    route: Route,
    dataset: MockDataset | None,
    legs_by_id: dict[str, Leg],
    transfers_by_id: dict[str, Transfer],
    route_search_fn: Callable[[str, str, datetime], list[Route]] | None = None,
    search_indexes: tuple[dict[str, Leg], dict[str, list[Transfer]]] | None = None,
) -> dict[str, FallbackPlan | None]:
    """SPEC.md §3.4 — one fallback lookup per transfer node in `route`,
    computed once before the Monte Carlo loop so a missed connection is an
    O(1) cache hit during simulation rather than a fresh pathfinding query
    on every iteration.

    For the transfer at index i, the fallback search is subject to a
    remaining transfer budget of MAX_TOTAL_TRANSFERS - (i + 1) — the
    network-wide cap minus the transfers already used reaching that
    station. A transfer with no surviving candidate route maps to None,
    meaning simulate_route falls back to the static same-line-headway wait
    (§3.2 Step 4) for that node, unchanged.

    `route_search_fn` (SPEC.md §3.5), if given, replaces the default
    in-memory `find_candidate_routes(dataset, ...)` call with
    `route_search_fn(origin_id, destination_id, departure_time)` — this is
    how the Phase 3 DuckDB-backed path (pipelines/route_search_duckdb.py)
    plugs in without this function's control flow changing at all. `dataset`
    may be None whenever route_search_fn is given, since it's then never
    touched. Either way, this whole function still runs once per transfer
    node before the Monte Carlo loop, never inside it — the O(1)-per-
    iteration guarantee only depends on that call count, not on which
    backend answers each call.

    `search_indexes`, if given, is a prior
    pipelines.route_search.build_route_search_indexes(dataset) result,
    passed straight through to the default find_candidate_routes call so a
    caller running several routes' worth of fallback searches against the
    same dataset in one batch can share one pair of indexes instead of each
    sub-search rebuilding them. Ignored when route_search_fn is given.
    """
    ordered_legs = [legs_by_id[leg_id] for leg_id in route.legs]
    ordered_transfers = [transfers_by_id[t_id] for t_id in route.transfers]

    search = route_search_fn or (
        lambda origin_id, destination_id, departure_time: find_candidate_routes(
            dataset, origin_id, destination_id, departure_time, indexes=search_indexes
        )
    )

    plans: dict[str, FallbackPlan | None] = {}
    for i, transfer in enumerate(ordered_transfers):
        downstream_leg = ordered_legs[i + 1]
        remaining_budget = MAX_TOTAL_TRANSFERS - (i + 1)

        candidates = search(
            transfer.station_id,
            route.destination_station_id,
            downstream_leg.scheduled_departure,
        )
        candidates = [
            c
            for c in candidates
            if len(c.transfers) <= remaining_budget
            # A candidate that starts by boarding the exact leg that was
            # just missed isn't a real alternative -- that leg has already
            # departed without the passenger. Fall through to the static
            # same-line-headway wait for that case instead.
            and c.legs[0] != downstream_leg.leg_id
        ]

        if not candidates:
            plans[transfer.transfer_id] = None
            continue

        best = min(candidates, key=lambda c: (c.scheduled_arrival, c.route_id))
        plans[transfer.transfer_id] = FallbackPlan(
            route=best,
            legs=[legs_by_id[leg_id] for leg_id in best.legs],
            transfers=[transfers_by_id[t_id] for t_id in best.transfers],
        )

    return plans


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


def transfer_impact_minutes(
    transfer: Transfer,
    downstream_leg: Leg,
    route: Route,
    lines_by_id: dict[str, Line],
    fallback_plans: dict[str, FallbackPlan | None] | None,
) -> float:
    """SPEC.md §3.4 / §5.3 — schedule-level cost of missing this transfer, for
    the UI's Local Risk Impact Override. Reads the same precomputed
    FallbackPlan the simulation loop uses on a miss (O(1), no extra search):
    when one exists, the impact is how much later (or earlier, if the
    fallback beats the original schedule) the fallback route's own scheduled
    arrival lands versus the route's; when none survived the
    remaining-transfer-budget filter, the impact is the downstream line's
    headway (SPEC.md §2.6) -- the schedule-level equivalent of the same-line
    wait computed per-iteration in §3.2 Step 4.
    """
    fallback = fallback_plans.get(transfer.transfer_id) if fallback_plans else None
    if fallback is not None:
        delta = fallback.route.scheduled_arrival - route.scheduled_arrival
        return delta.total_seconds() / 60.0
    line_type = lines_by_id[downstream_leg.line_id].type
    return float(get_headway_minutes(line_type))


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
    impact_minutes: float


@dataclass
class RouteSimulationResult:
    route_id: str
    n_iterations: int
    mean_eta: datetime
    p85_eta: datetime
    p90_eta: datetime
    transfer_risks: list[TransferRisk]
    p85_penalty_minutes: float
    simulated_arrivals: list[datetime] = field(repr=False)


def simulate_route(
    route: Route,
    legs_by_id: dict[str, Leg],
    transfers_by_id: dict[str, Transfer],
    lines_by_id: dict[str, Line],
    n_iterations: int = 1000,
    rng: random.Random | None = None,
    fallback_plans: dict[str, FallbackPlan | None] | None = None,
) -> RouteSimulationResult:
    """SPEC.md §3.2 — Monte Carlo simulation of a route's realized arrival time.

    `fallback_plans` (SPEC.md §3.4), if given, is a {transfer_id: FallbackPlan
    or None} cache from precompute_fallback_plans() for this same route. When
    a transfer is missed and a fallback plan exists, that iteration switches
    onto the fallback route's legs/transfers for its remainder instead of
    waiting for the next same-line departure. Omitting it (the default)
    reproduces the original §3.2 Step 4 behavior exactly.
    """
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
        # Swapped onto a FallbackPlan's legs/transfers on a §3.4 re-route;
        # miss_counts (sized to the original route) is only ever indexed
        # while current_legs/current_transfers are still the original ones.
        current_legs = ordered_legs
        current_transfers = ordered_transfers
        leg_idx = 0

        first_leg = current_legs[0]
        delay = sample_delay_minutes(first_leg.delay_distribution_minutes, rng)
        realized_arrival = first_leg.scheduled_arrival + timedelta(minutes=delay)

        while leg_idx < len(current_transfers):
            transfer = current_transfers[leg_idx]
            downstream_leg = current_legs[leg_idx + 1]

            if realized_arrival <= downstream_leg.scheduled_departure:
                # Step 3: connection holds — downstream leg departs on schedule.
                delay = sample_delay_minutes(downstream_leg.delay_distribution_minutes, rng)
                realized_arrival = downstream_leg.scheduled_arrival + timedelta(minutes=delay)
                leg_idx += 1
                continue

            # Step 4: connection missed.
            if current_legs is ordered_legs:
                miss_counts[leg_idx] += 1

            fallback = fallback_plans.get(transfer.transfer_id) if fallback_plans else None
            if fallback is not None:
                # §3.4: switch onto the pre-computed best alternative route
                # from here to the destination for the rest of this iteration.
                current_legs = fallback.legs
                current_transfers = fallback.transfers
                leg_idx = 0
                first_leg = current_legs[0]
                delay = sample_delay_minutes(first_leg.delay_distribution_minutes, rng)
                realized_arrival = first_leg.scheduled_arrival + timedelta(minutes=delay)
                continue

            # No fallback available — resolve via next periodic departure
            # of the same downstream line (unchanged §3.2 Step 4).
            line_type = lines_by_id[downstream_leg.line_id].type
            headway = get_headway_minutes(line_type)
            next_departure = _next_periodic_departure(
                downstream_leg.scheduled_departure, realized_arrival, headway
            )
            leg_duration = downstream_leg.scheduled_arrival - downstream_leg.scheduled_departure
            fresh_delay = sample_delay_minutes(downstream_leg.delay_distribution_minutes, rng)
            realized_arrival = next_departure + leg_duration + timedelta(minutes=fresh_delay)
            leg_idx += 1

        simulated_arrivals.append(realized_arrival)

    simulated_arrivals.sort()
    p85_eta = _percentile(simulated_arrivals, 85)

    transfer_risks = [
        TransferRisk(
            transfer_id=transfer.transfer_id,
            miss_probability=transfer_miss_probability(ordered_legs[i], transfer),
            simulated_miss_rate=miss_counts[i] / n_iterations,
            impact_minutes=transfer_impact_minutes(
                transfer, ordered_legs[i + 1], route, lines_by_id, fallback_plans
            ),
        )
        for i, transfer in enumerate(ordered_transfers)
    ]

    return RouteSimulationResult(
        route_id=route.route_id,
        n_iterations=n_iterations,
        mean_eta=_mean_datetime(simulated_arrivals),
        p85_eta=p85_eta,
        p90_eta=_percentile(simulated_arrivals, 90),
        transfer_risks=transfer_risks,
        p85_penalty_minutes=(p85_eta - route.scheduled_arrival).total_seconds() / 60.0,
        simulated_arrivals=simulated_arrivals,
    )
