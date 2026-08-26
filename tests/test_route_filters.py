"""Tests for pipelines/route_filters.py (SPEC.md §3.7): the Sanity Filter
that prunes mathematically-valid-but-practically-absurd detour routes from a
top-level search result before display/simulation.
"""

from datetime import datetime, timedelta

from models import Route
from pipelines.route_filters import (
    DEFAULT_MAX_ADDITIONAL_MINUTES,
    DEFAULT_MAX_DURATION_RATIO,
    apply_sanity_filter,
)

DEP = datetime(2026, 8, 25, 3, 21)


def _route(route_id: str, duration_minutes: int, transfers: list[str] | None = None) -> Route:
    return Route(
        route_id=route_id,
        legs=["L1"],
        transfers=transfers or [],
        origin_station_id="A",
        destination_station_id="B",
        scheduled_departure=DEP,
        scheduled_arrival=DEP + timedelta(minutes=duration_minutes),
    )


def test_empty_list_returns_empty_list():
    assert apply_sanity_filter([]) == []


def test_single_route_always_survives():
    routes = [_route("R1", 411)]  # 6h51m, no competing faster route
    assert apply_sanity_filter(routes) == routes


def test_drops_a_multihour_detour_alongside_a_fast_direct_route():
    """Mirrors the reported Köln->Frankfurt case: a 1h27m direct route
    alongside 6h51m/6h03m 2-/1-transfer detours (~4.1x-4.7x the fastest) --
    both detours must be dropped, the direct route kept."""
    fast = _route("R_DIRECT", 87)  # 1h27m
    detour_2x = _route("R_DETOUR_2XFER", 411, transfers=["T1", "T2"])  # 6h51m
    detour_1x = _route("R_DETOUR_1XFER", 363, transfers=["T1"])  # 6h03m

    kept = apply_sanity_filter([fast, detour_2x, detour_1x])

    assert kept == [fast]


def test_keeps_a_moderately_slower_alternative_within_the_ratio():
    fast = _route("R_FAST", 90)
    moderate = _route("R_MODERATE", 90 * 2)  # exactly at the default 2.5x-under boundary

    kept = apply_sanity_filter([fast, moderate])

    assert {r.route_id for r in kept} == {"R_FAST", "R_MODERATE"}


def test_boundary_is_inclusive():
    fast = _route("R_FAST", 100)
    exactly_at_ratio = _route("R_AT_RATIO", int(100 * DEFAULT_MAX_DURATION_RATIO))

    kept = apply_sanity_filter([fast, exactly_at_ratio])

    assert {r.route_id for r in kept} == {"R_FAST", "R_AT_RATIO"}


def test_just_over_the_ratio_is_dropped():
    fast = _route("R_FAST", 100)
    just_over = _route("R_OVER", int(100 * DEFAULT_MAX_DURATION_RATIO) + 1)

    kept = apply_sanity_filter([fast, just_over])

    assert [r.route_id for r in kept] == ["R_FAST"]


def test_custom_ratio_is_respected():
    fast = _route("R_FAST", 60)
    moderate = _route("R_MODERATE", 100)  # 1.67x -- fine under 2.5x, not under 1.5x

    kept = apply_sanity_filter([fast, moderate], max_duration_ratio=1.5)

    assert [r.route_id for r in kept] == ["R_FAST"]


# --- the additive cap governs long trips, where a pure ratio under-prunes ---
# (SPEC.md §3.7 -- found by sweeping the real warehouse: Nürnberg Hbf ->
# Hannover Hbf has a fastest route of 4h00m and a genuine detour cluster at
# 8h03m-10h03m, ratio 2.01x-2.51x -- a pure 2.5x cap only caught the single
# worst of those seven detours.)


def test_additive_cap_catches_a_long_trip_detour_a_pure_ratio_would_miss():
    """A candidate at 2.2x the fastest duration survives the default 2.5x
    ratio on its own, but for a 4-hour fastest route that's 2h48m of extra
    travel time -- well past the default 150-minute additive ceiling -- so
    the hybrid cap must still drop it."""
    fast = _route("R_FAST", 240)  # 4h00m
    long_detour = _route("R_DETOUR", 528, transfers=["T1"])  # 8h48m, 2.2x

    kept = apply_sanity_filter([fast, long_detour])

    assert [r.route_id for r in kept] == ["R_FAST"]


def test_additive_cap_still_allows_a_genuine_slower_long_haul_alternative():
    fast = _route("R_FAST", 240)  # 4h00m
    # +140min, under the default 150-minute additive ceiling and under 2.5x.
    slower_alternative = _route("R_SLOWER", 380, transfers=["T1"])

    kept = apply_sanity_filter([fast, slower_alternative])

    assert {r.route_id for r in kept} == {"R_FAST", "R_SLOWER"}


def test_additive_cap_is_a_no_op_for_short_trips_where_the_ratio_is_tighter():
    """For a short fastest route, `fastest * 2.5` is far tighter than
    `fastest + 150min`, so the additive term must never be what's binding --
    matches the empirical sweep showing 0 extra drops in the <60min bucket."""
    fast = _route("R_FAST", 20)
    just_under_ratio = _route("R_UNDER_RATIO", 49)  # 2.45x, well under +150min

    kept = apply_sanity_filter([fast, just_under_ratio])

    assert {r.route_id for r in kept} == {"R_FAST", "R_UNDER_RATIO"}


def test_custom_additive_minutes_is_respected():
    fast = _route("R_FAST", 240)  # 4h00m
    detour = _route("R_DETOUR", 300)  # +60min, under the default +150min

    kept = apply_sanity_filter([fast, detour], max_additional_minutes=30)

    assert [r.route_id for r in kept] == ["R_FAST"]


def test_default_additive_minutes_constant_matches_module_default():
    assert DEFAULT_MAX_ADDITIONAL_MINUTES == 150


def test_preserves_input_order_and_does_not_mutate_input():
    fast = _route("R_FAST", 60)
    slow_ok = _route("R_SLOW_OK", 90)
    detour = _route("R_DETOUR", 600)
    original = [detour, fast, slow_ok]

    kept = apply_sanity_filter(original)

    assert [r.route_id for r in kept] == ["R_FAST", "R_SLOW_OK"]
    assert original == [detour, fast, slow_ok]
