"""Sanity Filter for top-level candidate-route lists (SPEC.md §3.7).

Route search returns every mathematically valid path, with no notion of
whether one is a *sane* passenger choice. This module prunes the multi-hour
detours before they reach pagination or the Monte Carlo loop, which has no
reason to spend iterations on a route nobody would pick.

Deliberately NOT applied inside `find_candidate_routes`, and NOT wired into
`engine.precompute_fallback_plans`'s per-transfer fallback search -- doing so
there could discard the soonest-arriving fallback in favour of a merely
shorter one departing hours later. Full reasoning: SPEC.md §3.7.
"""

from datetime import timedelta

from models import Route

# Two bounds, not one: the ratio governs short trips, the flat ceiling long
# ones. Derived from a sweep over every connected pair -- SPEC.md §3.7.
DEFAULT_MAX_DURATION_RATIO = 2.5
DEFAULT_MAX_ADDITIONAL_MINUTES = 150


def apply_sanity_filter(
    routes: list[Route],
    max_duration_ratio: float = DEFAULT_MAX_DURATION_RATIO,
    max_additional_minutes: float = DEFAULT_MAX_ADDITIONAL_MINUTES,
) -> list[Route]:
    """Drops any route whose scheduled duration exceeds the *tighter* of the
    ratio bound and the flat-minutes bound, both measured against the fastest
    duration among `routes` in this same search result (SPEC.md §3.7).

    Because both bounds are relative to what was actually found rather than
    fixed cutoffs, the fastest route always survives -- this can never empty
    a non-empty list.
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
