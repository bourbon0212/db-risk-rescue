"""Monte Carlo delay propagation & risk scoring engine, per SPEC.md Section 3."""

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from models import Leg, Line, MockDataset, Route, Station, Transfer
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
    stations_by_id: dict[str, Station] | None = None,
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

    `stations_by_id`, if given, enables station-level Minimum Connection
    Time enforcement (SPEC.md §7's proposed MCT extension): a candidate
    whose first leg departs less than the stranded station's mct_minutes
    after the upstream leg's own scheduled arrival is rejected outright, so
    a physically-implausible dash across a large hub (e.g. a 2-minute
    "connection" at a station whose real MCT is 10) is never offered as a
    fallback. Left as None (the default), no MCT filtering is applied,
    reproducing the original behavior exactly.
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
        upstream_leg = ordered_legs[i]
        downstream_leg = ordered_legs[i + 1]
        remaining_budget = MAX_TOTAL_TRANSFERS - (i + 1)

        station = stations_by_id.get(transfer.station_id) if stations_by_id else None
        mct_minutes = station.mct_minutes if station is not None else 0

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
            # MCT enforcement: the candidate's own entry connection -- from
            # the upstream leg's scheduled arrival to this candidate's first
            # leg's departure -- must clear the station's Minimum Connection
            # Time, same as any other transfer's buffer, or it's rejected.
            and (
                legs_by_id[c.legs[0]].scheduled_departure - upstream_leg.scheduled_arrival
            ).total_seconds() / 60 >= mct_minutes
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

    Purely statistical -- this never looks at a station's MCT (see
    mct_violation_floor below for that). Kept unchanged/unfloored so its
    existing worked-example tests stay exact; simulate_route blends the MCT
    floor in separately, at the point where a TransferRisk is built.
    """
    buffer = transfer.scheduled_buffer_minutes
    return sum(
        prob
        for bucket_str, prob in upstream_leg.delay_distribution_minutes.items()
        if int(bucket_str) > buffer
    )


# SPEC.md §7's MCT extension: a below-MCT transfer's *effective* miss
# probability is never allowed to read as literal certainty (1.0) -- there's
# always some chance of a genuine cross-platform sprint -- so the gradient
# floor asymptotes at this ceiling instead of 1.0.
MCT_VIOLATION_MAX_FLOOR = 0.95


def mct_violation_floor(buffer_minutes: int, mct_minutes: int) -> float:
    """SPEC.md §7 — a gradient (not cliff-edge) floor on miss probability for
    a transfer whose scheduled buffer is below its station's Minimum
    Connection Time.

    Scales linearly from 0 (buffer at or above mct_minutes -- no floor
    applied at all) up to MCT_VIOLATION_MAX_FLOOR (buffer at or below zero
    minutes -- treated as near-certain regardless of how punctual the
    upstream line historically is). A buffer just 1 minute short of a
    10-minute hub MCT barely nudges the risk; a 0-minute "connection" at
    that same hub dominates it, deliberately avoiding the binary "impossible
    vs. fine" cliff a flat threshold/flat floor would both produce.
    """
    if mct_minutes <= 0:
        return 0.0
    deficit_fraction = min(max(mct_minutes - buffer_minutes, 0) / mct_minutes, 1.0)
    return deficit_fraction * MCT_VIOLATION_MAX_FLOOR


def _mct_extra_fail_probability(analytic_miss_probability: float, mct_floor: float) -> float:
    """The extra per-iteration Bernoulli probability of forcing a miss on an
    iteration that already held by schedule, chosen so the overall (natural
    + forced) miss rate converges to max(analytic_miss_probability,
    mct_floor) exactly:

        P(overall miss) = A + (1 - A) * P(extra | held) = max(A, F)
        => P(extra | held) = max(F - A, 0) / (1 - A)

    Returns 0 whenever the floor doesn't bind (F <= A) or A is already 1.0
    (nothing left to force), so passing mct_minutes=None/no station is a
    strict no-op that consumes zero extra random draws in the caller's loop
    -- this is what keeps every pre-existing (no stations_by_id) call to
    simulate_route bit-for-bit reproducible.
    """
    if mct_floor <= analytic_miss_probability or analytic_miss_probability >= 1.0:
        return 0.0
    return (mct_floor - analytic_miss_probability) / (1.0 - analytic_miss_probability)


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
    # SPEC.md §7 — True when this transfer's scheduled buffer is below its
    # station's MCT, i.e. miss_probability may include the gradient floor
    # rather than being purely statistical. ui_components.py uses this to
    # pick "Unrealistic transfer" wording instead of "Miss likely" when the
    # floor is the dominant reason and no fallback rescues it.
    below_mct: bool = False


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
    stations_by_id: dict[str, Station] | None = None,
) -> RouteSimulationResult:
    """SPEC.md §3.2 — Monte Carlo simulation of a route's realized arrival time.

    `fallback_plans` (SPEC.md §3.4), if given, is a {transfer_id: FallbackPlan
    or None} cache from precompute_fallback_plans() for this same route. When
    a transfer is missed and a fallback plan exists, that iteration switches
    onto the fallback route's legs/transfers for its remainder instead of
    waiting for the next same-line departure. Omitting it (the default)
    reproduces the original §3.2 Step 4 behavior exactly.

    `stations_by_id`, if given, enables SPEC.md §7's MCT gradient floor for
    the route's own transfers (not fallback-internal ones -- see
    precompute_fallback_plans for those): each transfer's *effective* miss
    probability becomes max(analytic, mct_violation_floor(...)), and the
    per-iteration loop is given a matching extra chance to force a miss on
    an iteration that held by schedule, so the simulated ETA and the
    displayed risk % can never tell contradictory stories. Left as None
    (the default), every transfer's extra-fail probability is exactly 0 and
    zero extra random draws are consumed, reproducing prior behavior
    bit-for-bit (see _mct_extra_fail_probability).
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

    # SPEC.md §7 — per-transfer MCT gradient, computed once up front (not
    # per iteration): analytic_miss_probability is the untouched statistical
    # figure (transfer_miss_probability); effective_miss_probability folds
    # in the floor for display/classification; extra_fail_probability is
    # what the loop below draws against so the simulated outcomes actually
    # match effective_miss_probability, not just the analytic figure.
    below_mct_flags: list[bool] = []
    effective_miss_probabilities: list[float] = []
    extra_fail_probabilities: list[float] = []
    for i, transfer in enumerate(ordered_transfers):
        analytic = transfer_miss_probability(ordered_legs[i], transfer)
        station = stations_by_id.get(transfer.station_id) if stations_by_id else None
        below_mct = station is not None and transfer.scheduled_buffer_minutes < station.mct_minutes
        floor = (
            mct_violation_floor(transfer.scheduled_buffer_minutes, station.mct_minutes)
            if below_mct
            else 0.0
        )
        below_mct_flags.append(below_mct)
        effective_miss_probabilities.append(max(analytic, floor))
        extra_fail_probabilities.append(_mct_extra_fail_probability(analytic, floor))

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

            connection_holds = realized_arrival <= downstream_leg.scheduled_departure
            # SPEC.md §7 — an MCT-forced miss only ever applies to the
            # original route's own transfers (current_transfers is
            # ordered_transfers), same scoping as miss_counts above:
            # fallback-internal transfers aren't MCT-checked here (that's
            # precompute_fallback_plans' entry-buffer check, a separate
            # concern). extra_fail_probabilities[leg_idx] is exactly 0 for
            # every pre-existing (no stations_by_id) call, so rng.random()
            # is never drawn in that case -- the random stream is untouched.
            if (
                connection_holds
                and current_transfers is ordered_transfers
                and extra_fail_probabilities[leg_idx] > 0
                and rng.random() < extra_fail_probabilities[leg_idx]
            ):
                connection_holds = False

            if connection_holds:
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
            miss_probability=effective_miss_probabilities[i],
            simulated_miss_rate=miss_counts[i] / n_iterations,
            impact_minutes=transfer_impact_minutes(
                transfer, ordered_legs[i + 1], route, lines_by_id, fallback_plans
            ),
            below_mct=below_mct_flags[i],
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
