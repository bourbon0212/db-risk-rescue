"""Sanity Filter for top-level candidate-route lists (SPEC.md §3.7).

Route search (`pipelines/route_search.py`, `pipelines/route_search_duckdb.py`)
finds every mathematically valid direct/1-transfer/2-transfer path departing
at or after the requested time -- with no notion of whether a path is a
*sane* passenger choice. On a well-connected network that includes multi-hour
detours through a distant hub alongside a 90-minute direct train, purely
because both happen to depart in the same search window. This module prunes
those before they reach `app.py`'s display-limit slicing or the Monte Carlo
simulation (SPEC.md §3.3), which has no reason to spend N=1,000 iterations on
a route no passenger would ever choose.

Deliberately NOT applied inside `find_candidate_routes` itself, and NOT
wired into `engine.precompute_fallback_plans`'s per-transfer fallback
search (SPEC.md §3.4): that search already picks the single
earliest-*arriving* candidate (`min(candidates, key=scheduled_arrival)`),
which already prefers a fast, near-term option over a distant one. Its
candidate pool can also legitimately span a much wider departure-time
window than one top-level search page (there's no "next 5" pagination
mid-fallback), so comparing every candidate's raw duration against
whichever happens to be shortest risks discarding the soonest-arriving
option in favor of a merely shorter-duration one departing hours later.
"""

from datetime import timedelta

from models import Route

# A pure ratio (below) turns out to under-prune long trips: sweeping every
# connected station pair in the Phase 3 warehouse (SPEC.md §4.3) and bucketing
# the worst observed duration ratio by fastest-route length showed detour
# explosion is a short/medium-trip phenomenon on this network (<60min-fastest
# pairs: median worst ratio 15x, some as bad as 458x) that tapers off for
# long-haul pairs (>240min-fastest: median worst ratio 1.4x, 0% of pairs even
# reach 2.5x) -- because the 2-transfer cap and real corridor connectivity
# self-limit how convoluted a long route can get. But one real counter-example
# survived a pure 2.5x cap: Nürnberg Hbf -> Hannover Hbf (fastest 4h00m) has a
# genuine alternative cluster up to 6h36m (<=1.65x) and a separate detour
# cluster from 8h03m-10h03m (2.01x-2.51x) -- the ratio alone only caught the
# single worst of those seven detours. A flat ceiling on *additional* minutes
# closes that gap without changing short-trip behavior at all (verified: 0
# extra drops in the <60min bucket across the same sweep, since the ratio is
# already the tighter constraint there).
DEFAULT_MAX_DURATION_RATIO = 2.5
DEFAULT_MAX_ADDITIONAL_MINUTES = 150


def apply_sanity_filter(
    routes: list[Route],
    max_duration_ratio: float = DEFAULT_MAX_DURATION_RATIO,
    max_additional_minutes: float = DEFAULT_MAX_ADDITIONAL_MINUTES,
) -> list[Route]:
    """Drops any route whose scheduled duration exceeds the *tighter* of two
    bounds, both measured against the fastest scheduled duration among
    `routes` in this same search result:

    - `max_duration_ratio` times the fastest duration -- e.g. a 6h51m
      2-transfer detour when a 1h27m direct route was also found.
    - the fastest duration plus a flat `max_additional_minutes` -- e.g. a
      10h03m detour when the fastest option is 4h00m (2.51x -- inside a pure
      ratio cap, but 6h03m of extra travel time no passenger would accept).

    Both bounds are relative to the fastest candidate *actually found*, not a
    fixed cutoff, so together they scale with trip distance the way a single
    ratio can't: short trips are governed by the ratio (a fixed minutes
    allowance would be too permissive there), long trips by the flat ceiling
    (a fixed ratio would be too permissive there).

    The fastest route in `routes` always survives (duration 0 over its own
    baseline, ratio exactly 1.0), so this can never empty a non-empty list
    down to nothing.
    """
    if not routes:
        return routes
    fastest_duration = min(r.scheduled_arrival - r.scheduled_departure for r in routes)
    max_allowed = min(
        fastest_duration * max_duration_ratio,
        fastest_duration + timedelta(minutes=max_additional_minutes),
    )
    return [
        r for r in routes if (r.scheduled_arrival - r.scheduled_departure) <= max_allowed
    ]
