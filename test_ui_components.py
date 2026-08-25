"""Tests for UI risk/health classification helpers and their wording.

Covers SPEC.md §5.2 (Global Health) and §5.3 (Local Risk + Impact Override),
and the wording UIUX_SPEC.md §1.3 requires: any base-Red transfer (Miss
likely *and* a downgraded Recoverable miss alike) shows the fallback's
absolute arrival clock time instead of the scheduled buffer (UIUX_SPEC.md §5,
history entries #16-#19).
"""

import inspect
from datetime import datetime

import pytest

from engine import TransferRisk
from models import Leg, Line, Route, Station, Transfer
from ui_components import (
    IMPACT_OVERRIDE_THRESHOLD_MINUTES,
    RISK_WORDING,
    RISK_WORDING_MCT_VIOLATION,
    RISK_WORDING_OVERRIDE,
    _fallback_arrival_label,
    _itinerary_html,
    classify_global_health,
    classify_local_risk,
    classify_risk,
)

# ---------------------------------------------------------------------------
# SPEC.md §3.1 / §5.3 — base probability band (unchanged by the Impact
# Override; classify_local_risk builds on top of this)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "miss_probability,expected",
    [(0.0, "low"), (0.09, "low"), (0.10, "medium"), (0.30, "medium"), (0.31, "high"), (1.0, "high")],
)
def test_classify_risk_bands(miss_probability, expected):
    assert classify_risk(miss_probability) == expected


# ---------------------------------------------------------------------------
# SPEC.md §5.3 — Local Risk Impact Override
# ---------------------------------------------------------------------------


def test_classify_local_risk_leaves_low_and_medium_untouched():
    assert classify_local_risk(0.05, impact_minutes=999) == ("low", False)
    assert classify_local_risk(0.20, impact_minutes=999) == ("medium", False)


def test_classify_local_risk_downgrades_red_when_impact_is_small():
    assert classify_local_risk(0.37, impact_minutes=12) == ("medium", True)


def test_classify_local_risk_keeps_red_when_impact_is_large():
    assert classify_local_risk(0.37, impact_minutes=16) == ("high", False)


def test_classify_local_risk_override_threshold_is_inclusive():
    band, is_override = classify_local_risk(0.37, impact_minutes=IMPACT_OVERRIDE_THRESHOLD_MINUTES)
    assert (band, is_override) == ("medium", True)


def test_classify_local_risk_downgrade_applies_even_when_fallback_arrives_early():
    """A fallback that beats the original schedule (negative impact) is the
    strongest case for a "harmless miss" -- must still downgrade."""
    assert classify_local_risk(0.99, impact_minutes=-15) == ("medium", True)


# ---------------------------------------------------------------------------
# SPEC.md §5.2 — Global Health (card left-edge strip)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "p85_penalty_minutes,expected",
    [(-10, "low"), (0, "low"), (30, "low"), (30.01, "medium"), (60, "medium"), (60.01, "high"), (639, "high")],
)
def test_classify_global_health_bands(p85_penalty_minutes, expected):
    assert classify_global_health(p85_penalty_minutes) == expected


def test_classify_global_health_takes_only_the_p85_penalty():
    """Structural guard for the decoupling UIUX_SPEC.md §2.3 documents: Global
    Health must not accept a transfer-count or per-transfer-probability
    argument, so a direct (0-transfer) route can't be special-cased back in
    by accident."""
    params = list(inspect.signature(classify_global_health).parameters)
    assert params == ["p85_penalty_minutes"]


# ---------------------------------------------------------------------------
# UIUX_SPEC.md §1.3 — distinct wording for the Impact Override band
# ---------------------------------------------------------------------------


def test_override_phrase_is_distinct_from_every_base_band_phrase():
    assert RISK_WORDING_OVERRIDE not in RISK_WORDING.values()


def test_mct_violation_phrase_is_distinct_from_every_other_phrase():
    assert RISK_WORDING_MCT_VIOLATION not in RISK_WORDING.values()
    assert RISK_WORDING_MCT_VIOLATION != RISK_WORDING_OVERRIDE


# ---------------------------------------------------------------------------
# UIUX_SPEC.md §1.3 / §5 (history #16-#18) — the fallback arrival figure
# shows an absolute clock time, deliberately distinct from Safe/Tight's bare
# "<Y> min" (scheduled buffer)
# ---------------------------------------------------------------------------

_SCHEDULED_ARRIVAL = datetime(2026, 8, 23, 10, 0)


@pytest.mark.parametrize(
    "impact_minutes,expected",
    [
        (-123, "arrives 07:57 if missed"),
        (-1, "arrives 09:59 if missed"),
        (0, "arrives 10:00 if missed"),
        (12, "arrives 10:12 if missed"),
        (38, "arrives 10:38 if missed"),
    ],
)
def test_fallback_arrival_label_shows_absolute_fallback_arrival(impact_minutes, expected):
    assert _fallback_arrival_label(_SCHEDULED_ARRIVAL, impact_minutes) == expected


def test_fallback_arrival_label_zero_impact_matches_scheduled_arrival_exactly():
    """The whole point of the absolute-time format: a harmless (0-minute
    impact) fallback shows the *same* clock time as the route's own
    Scheduled Arrival, so the "no cost" case is self-evident by matching the
    time already printed in the card header -- no special-cased wording
    (the old "on time") needed."""
    label = _fallback_arrival_label(_SCHEDULED_ARRIVAL, 0)
    assert f"{_SCHEDULED_ARRIVAL:%H:%M}" in label


def _single_transfer_route_fixtures(miss_probability: float, impact_minutes: float, below_mct: bool = False):
    """Minimal A -> B -> C route/leg/transfer set for an _itinerary_html
    smoke test -- only shape matters, not delay-distribution realism, since
    the risk figures are supplied directly via a hand-built TransferRisk."""
    stations_by_id = {s: Station(station_id=s, name=s) for s in ("A", "B", "C")}
    lines_by_id = {"LN": Line(line_id="LN", type="RE", operator="Test")}
    leg_ab = Leg(
        leg_id="AB", line_id="LN", origin_station_id="A", destination_station_id="B",
        scheduled_departure=datetime(2026, 8, 23, 9, 0), scheduled_arrival=datetime(2026, 8, 23, 9, 30),
        delay_distribution_minutes={"0": 1.0},
    )
    leg_bc = Leg(
        leg_id="BC", line_id="LN", origin_station_id="B", destination_station_id="C",
        scheduled_departure=datetime(2026, 8, 23, 9, 35), scheduled_arrival=datetime(2026, 8, 23, 10, 0),
        delay_distribution_minutes={"0": 1.0},
    )
    legs_by_id = {"AB": leg_ab, "BC": leg_bc}
    transfer = Transfer(
        transfer_id="T1", station_id="B", from_leg_id="AB", to_leg_id="BC", scheduled_buffer_minutes=5,
    )
    transfers_by_id = {"T1": transfer}
    route = Route(
        route_id="RT", legs=["AB", "BC"], transfers=["T1"],
        origin_station_id="A", destination_station_id="C",
        scheduled_departure=datetime(2026, 8, 23, 9, 0), scheduled_arrival=datetime(2026, 8, 23, 10, 0),
    )
    transfer_risks = [
        TransferRisk(
            transfer_id="T1", miss_probability=miss_probability,
            simulated_miss_rate=miss_probability, impact_minutes=impact_minutes,
            below_mct=below_mct,
        )
    ]
    return route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks


def test_itinerary_html_shows_scheduled_arrival_time_instead_of_zero_minutes_for_override():
    """Reproduces the exact real-corridor case that surfaced this: a base-Red
    transfer (38% miss probability) whose fallback plan lands at exactly the
    route's original scheduled arrival (impact_minutes == 0) -- the fallback
    clock time shown should exactly match the route's own 10:00 scheduled
    arrival, not a bare "0 min"."""
    route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks = (
        _single_transfer_route_fixtures(miss_probability=0.38, impact_minutes=0.0)
    )

    html = _itinerary_html(route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks)

    assert "Recoverable miss (38% risk)" in html
    assert f"arrives {route.scheduled_arrival:%H:%M} if missed" in html
    assert "0 min" not in html


def test_itinerary_html_shows_fallback_arrival_clock_time_for_override():
    route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks = (
        _single_transfer_route_fixtures(miss_probability=0.50, impact_minutes=12.0)
    )

    html = _itinerary_html(route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks)

    assert "Recoverable miss (50% risk)" in html
    assert "arrives 10:12 if missed" in html


def test_itinerary_html_shows_fallback_arrival_for_miss_likely_too():
    """The reasoning behind the override's arrival-clock-time figure (a
    base-Red transfer's own buffer is uninformative) applies just as much to
    a *non*-overridden base-Red transfer -- Miss likely should show the same
    kind of figure as Recoverable miss, not fall back to the buffer."""
    route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks = (
        _single_transfer_route_fixtures(miss_probability=0.50, impact_minutes=60.0)
    )

    html = _itinerary_html(route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks)

    assert "Miss likely (50% risk)" in html
    assert "arrives 11:00 if missed" in html
    assert "5 min" not in html


def test_itinerary_html_still_shows_buffer_for_safe_and_tight_connections():
    """Sanity check for the other side of the split: a base-Low/Medium
    transfer keeps the plain scheduled-buffer figure, since the connection
    is expected to hold and "how much slack" is still the relevant
    question there."""
    route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks = (
        _single_transfer_route_fixtures(miss_probability=0.20, impact_minutes=999.0)
    )

    html = _itinerary_html(route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks)

    assert "Tight connection (20% risk)" in html
    assert "5 min" in html
    assert "if missed" not in html


# ---------------------------------------------------------------------------
# SPEC.md §3.6.4 -- final 5-phrase MCT wording (no standalone caption, no
# "MCT" text anywhere in the rendered HTML). below_mct is engine-authoritative
# (engine.TransferRisk.below_mct, set by simulate_route's MCT gradient floor)
# -- the UI trusts it directly rather than recomputing anything itself.
# ---------------------------------------------------------------------------


def test_itinerary_html_upgrades_low_risk_below_mct_to_tight_connection():
    """A below-MCT connection can never read as "Safe" -- even when the
    numeric risk is low, it's folded into "Tight connection" (the same
    phrase a genuinely medium-risk connection gets), with the plain buffer
    minutes and no separate MCT text of any kind."""
    route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks = (
        _single_transfer_route_fixtures(miss_probability=0.05, impact_minutes=999.0, below_mct=True)
    )

    html = _itinerary_html(route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks)

    assert "Tight connection (5% risk)" in html
    assert "5 min" in html
    assert "if missed" not in html
    assert "Safe connection" not in html
    assert "MCT" not in html


def test_itinerary_html_keeps_safe_connection_when_low_risk_and_not_below_mct():
    route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks = (
        _single_transfer_route_fixtures(miss_probability=0.05, impact_minutes=999.0, below_mct=False)
    )

    html = _itinerary_html(route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks)

    assert "Safe connection (5% risk)" in html


def test_itinerary_html_shows_unrealistic_transfer_when_below_mct_high_with_no_fallback():
    """below_mct + base-High + no rescuing fallback (impact_minutes way
    above the Override threshold) -- the headline must say "Unrealistic
    transfer", not the generic statistical "Miss likely"."""
    route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks = (
        _single_transfer_route_fixtures(miss_probability=0.90, impact_minutes=999.0, below_mct=True)
    )

    html = _itinerary_html(route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks)

    assert "Unrealistic transfer (90% risk)" in html
    assert "Miss likely" not in html
    assert "MCT" not in html


def test_itinerary_html_keeps_recoverable_miss_wording_when_below_mct_but_fallback_is_cheap():
    """below_mct + base-High but the Impact Override fires (a cheap
    fallback exists) -- must keep the reassuring "Recoverable miss" phrase,
    not swap to "Unrealistic transfer". No MCT-specific text of any kind --
    the reassuring phrase alone is the whole story, per SPEC.md §3.6.4."""
    route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks = (
        _single_transfer_route_fixtures(miss_probability=0.90, impact_minutes=10.0, below_mct=True)
    )

    html = _itinerary_html(route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks)

    assert "Recoverable miss (90% risk)" in html
    assert "Unrealistic transfer" not in html
    assert "MCT" not in html


# ---------------------------------------------------------------------------
# SPEC.md §7's proposed platform-info extension -- transfer-strip platform pair
# ---------------------------------------------------------------------------


def test_itinerary_html_shows_platform_pair_when_both_endpoints_have_one():
    route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks = (
        _single_transfer_route_fixtures(miss_probability=0.05, impact_minutes=999.0)
    )
    legs_by_id = dict(legs_by_id)
    legs_by_id["AB"] = legs_by_id["AB"].model_copy(update={"destination_platform": "7"})
    legs_by_id["BC"] = legs_by_id["BC"].model_copy(update={"origin_platform": "3"})

    html = _itinerary_html(route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks)

    assert "Plat. 7" in html
    assert "Plat. 3" in html


def test_itinerary_html_hides_platform_when_either_endpoint_missing():
    """Real GTFS.DE platform_code coverage is sparse (confirmed against the
    real feed, close to 0% at this corridor's major hubs) -- the default
    fixtures (no platform set on either leg) must render nothing rather than
    a placeholder."""
    route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks = (
        _single_transfer_route_fixtures(miss_probability=0.05, impact_minutes=999.0)
    )

    html = _itinerary_html(route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, transfer_risks)

    assert "Plat." not in html
