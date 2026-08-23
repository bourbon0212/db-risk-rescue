"""On-demand candidate Route generation (DATA_SPEC.md §5, §8 build sequence
step 6).

v1 scope only, deliberately modest and symmetric with the rest of this
app's "don't over-engineer" ethos (DATA_SPEC.md §5):
  - Direct legs (no transfer) between origin and destination, departing at
    or after the requested time.
  - Single-transfer journeys: leg A out of the origin, a transfer from
    dataset.transfers, leg B into the destination.

Explicitly NOT in scope: multi-transfer (2+) journeys, full graph
pathfinding/Dijkstra, or "best alternative on miss" re-routing -- all
already deferred in SPEC.md §5, and moving to real data doesn't change that.
"""

from datetime import datetime

from models import Leg, MockDataset, Route


def find_candidate_routes(
    dataset: MockDataset,
    origin_id: str,
    destination_id: str,
    departure_time: datetime,
) -> list[Route]:
    """Direct and single-transfer Route candidates from origin to destination,
    departing at or after departure_time, built from dataset.legs/transfers.
    """
    if origin_id == destination_id:
        return []

    legs_by_id: dict[str, Leg] = {leg.leg_id: leg for leg in dataset.legs}
    routes: list[Route] = []

    for leg in dataset.legs:
        if (
            leg.origin_station_id == origin_id
            and leg.destination_station_id == destination_id
            and leg.scheduled_departure >= departure_time
        ):
            routes.append(
                Route(
                    route_id=f"RS_DIRECT_{leg.leg_id}",
                    legs=[leg.leg_id],
                    transfers=[],
                    origin_station_id=origin_id,
                    destination_station_id=destination_id,
                    scheduled_departure=leg.scheduled_departure,
                    scheduled_arrival=leg.scheduled_arrival,
                )
            )

    for transfer in dataset.transfers:
        from_leg = legs_by_id.get(transfer.from_leg_id)
        to_leg = legs_by_id.get(transfer.to_leg_id)
        if from_leg is None or to_leg is None:
            continue
        if (
            from_leg.origin_station_id == origin_id
            and to_leg.destination_station_id == destination_id
            and from_leg.scheduled_departure >= departure_time
        ):
            routes.append(
                Route(
                    route_id=f"RS_XFER_{transfer.transfer_id}",
                    legs=[from_leg.leg_id, to_leg.leg_id],
                    transfers=[transfer.transfer_id],
                    origin_station_id=origin_id,
                    destination_station_id=destination_id,
                    scheduled_departure=from_leg.scheduled_departure,
                    scheduled_arrival=to_leg.scheduled_arrival,
                )
            )

    routes.sort(key=lambda r: r.scheduled_departure)
    return routes
