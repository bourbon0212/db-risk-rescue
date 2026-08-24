"""On-demand candidate Route generation (DATA_SPEC.md §5, §8 build sequence
step 6).

v1 scope, deliberately modest and symmetric with the rest of this app's
"don't over-engineer" ethos (DATA_SPEC.md §5), extended once from "1
transfer max" to "2 transfers max" once the network grew large enough for
one-transfer journeys to leave real station pairs (e.g. Berlin Hbf <->
Leipzig Hbf) unreachable despite the underlying Leg/Transfer graph
connecting them fine (see pipelines/id_crosswalk.py's module docstring):
  - Direct legs (no transfer) between origin and destination, departing at
    or after the requested time.
  - Single-transfer journeys: leg A out of the origin, a transfer, leg B
    into the destination.
  - Two-transfer journeys: leg A out of the origin, a transfer, leg B, a
    second transfer, leg C into the destination.

Still explicitly NOT in scope: 3+ transfer journeys, full graph
pathfinding/Dijkstra, or "best alternative on miss" re-routing -- deferred
in SPEC.md §7, unchanged by this extension. engine.py's simulate_route()
already chains delays/buffers/miss-probabilities generically over however
many legs and transfers a Route has (see test_multi_transfer_routing.py),
so this module is the only thing that needed to change to unlock 2-hop
journeys -- the simulation side needed no modification.
"""

from collections import defaultdict
from datetime import datetime

from models import Leg, MockDataset, Route, Transfer


def find_candidate_routes(
    dataset: MockDataset,
    origin_id: str,
    destination_id: str,
    departure_time: datetime,
) -> list[Route]:
    """Direct, single-transfer, and two-transfer Route candidates from
    origin to destination, departing at or after departure_time, built
    from dataset.legs/transfers.
    """
    if origin_id == destination_id:
        return []

    legs_by_id: dict[str, Leg] = {leg.leg_id: leg for leg in dataset.legs}
    transfers_by_from_leg: dict[str, list[Transfer]] = defaultdict(list)
    for transfer in dataset.transfers:
        transfers_by_from_leg[transfer.from_leg_id].append(transfer)

    routes: list[Route] = []

    # Only legs departing the origin at/after the cutoff are ever the first
    # leg of a candidate -- indexing on this (rather than scanning every
    # transfer in the dataset, which can be tens of thousands at this
    # network's scale) keeps the 1- and 2-transfer search proportional to
    # how much actually departs from origin_id, not the dataset's total size.
    origin_legs = [
        leg
        for leg in dataset.legs
        if leg.origin_station_id == origin_id and leg.scheduled_departure >= departure_time
    ]

    for leg_a in origin_legs:
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
            # could only leave and eventually come back to it (destination_id
            # is the one station every route must end at), which is a cycle
            # through the destination rather than a distinct route. Nothing
            # past this point can be legitimate, so stop extending leg_a.
            continue

        # Stations visited so far on this path -- any leg landing back on
        # one of these would be a cycle (SPEC.md §3, route search has no
        # concept of a "scenic route"; every candidate must be a simple path).
        visited = {origin_id, station_1}

        for transfer_1 in transfers_by_from_leg.get(leg_a.leg_id, []):
            leg_b = legs_by_id.get(transfer_1.to_leg_id)
            if leg_b is None:
                continue

            station_2 = leg_b.destination_station_id
            if station_2 in visited:
                # Revisits the origin or leg_a's arrival station: a cycle.
                continue

            if station_2 == destination_id:
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
                # Same reasoning as the direct-route case above: already
                # arrived, so stop extending leg_b instead of exploring a
                # second transfer that could only cycle back through it.
                continue

            for transfer_2 in transfers_by_from_leg.get(leg_b.leg_id, []):
                leg_c = legs_by_id.get(transfer_2.to_leg_id)
                if leg_c is None:
                    continue
                if leg_c.destination_station_id != destination_id:
                    continue
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
