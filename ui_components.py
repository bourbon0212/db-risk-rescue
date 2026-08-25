"""Streamlit rendering helpers for the search form and route cards (SPEC.md §5).

Visual system ported from the finalized UI/UX spec + design_mock.html: a white
search card with a session-state-backed station swap, and route cards built as
single HTML blobs (native <details>/<summary> for the itinerary, so expanding
it never triggers a Streamlit rerun).
"""

from datetime import date, datetime, time, timedelta

import streamlit as st

from engine import RouteSimulationResult, TransferRisk
from models import Leg, Line, Route, Station, Transfer

DB_RED = "#EB0016"

# design_mock.html §1 tokens, ported 1:1.
INK = "#161a20"
MUTED = "#68707c"
FAINT = "#9aa1ab"
LINE = "#e3e5e9"
CARD_BG = "#ffffff"

RISK_TOKENS = {
    "low": {"bg": "#eaf7ee", "border": "#2e7d32", "text": "#1c6b2c"},
    "medium": {"bg": "#fff5e3", "border": "#d98c1f", "text": "#8a5300"},
    "high": {"bg": "#fdeceb", "border": "#d63a30", "text": "#a3231b"},
}
# Action-first wording per design_mock.html §2 notes — one phrase, one number, one buffer.
RISK_WORDING = {"low": "Safe connection", "medium": "Tight connection", "high": "Miss likely"}
# UIUX_SPEC.md §1.3 — distinct phrase for a base-Red transfer downgraded by the
# Impact Override (SPEC.md §5.3), so a high probability is never relabeled
# with the genuine-Medium phrase.
RISK_WORDING_OVERRIDE = "Recoverable miss"
# SPEC.md §3.6.4 — distinct phrase for a base-Red transfer driven by the MCT
# gradient floor (engine.TransferRisk.below_mct) with no rescuing fallback:
# the cause is a physical connection time, not a statistical delay history,
# so it must never share wording with "Miss likely".
RISK_WORDING_MCT_VIOLATION = "Unrealistic transfer"
# SPEC.md §5.3 — a base-Red transfer downgrades to Yellow when its precomputed
# fallback impact is at or under this many minutes.
IMPACT_OVERRIDE_THRESHOLD_MINUTES = 15
# SPEC.md §5.2 — card left-edge strip (Global Health), thresholded on the P85
# penalty alone, independent of transfer count or per-transfer probabilities.
GLOBAL_HEALTH_YELLOW_MAX_MINUTES = 30
GLOBAL_HEALTH_RED_MIN_MINUTES = 60

# Line-type → DB category chip, per spec item 3 (ICE/IC = dark grey, RE/RB = light
# grey with border, S-Bahn = DB green).
LINE_TYPE_CHIP_CLASS = {
    "ICE": "chip-ice",
    "IC": "chip-ice",
    "EC": "chip-ice",
    "RE": "chip-re",
    "RB": "chip-re",
    "S-Bahn": "chip-sbahn",
    "S": "chip-sbahn",
}
DEFAULT_CHIP_CLASS = "chip-ice"


def classify_risk(miss_probability: float) -> str:
    """SPEC.md §5.3 — base probability band, before the Impact Override."""
    if miss_probability < 0.10:
        return "low"
    if miss_probability <= 0.30:
        return "medium"
    return "high"


def classify_local_risk(miss_probability: float, impact_minutes: float) -> tuple[str, bool]:
    """SPEC.md §5.3 — Local Risk: the base probability band (classify_risk)
    with the Impact Override applied. Returns (displayed_band, is_override):
    displayed_band is one of "low"/"medium"/"high" for coloring, and
    is_override is True only when a base-High transfer was downgraded to
    Medium because its precomputed fallback impact is small — callers use
    that flag to pick the "Recoverable miss" wording instead of "Tight
    connection" (UIUX_SPEC.md §1.3), since the two Yellow cases mean
    different things even though they share a color.
    """
    base = classify_risk(miss_probability)
    if base == "high" and impact_minutes <= IMPACT_OVERRIDE_THRESHOLD_MINUTES:
        return "medium", True
    return base, False


def classify_global_health(p85_penalty_minutes: float) -> str:
    """SPEC.md §5.2 — Global Health: the card left-edge strip, driven solely
    by the P85 penalty (P85 True ETA - Scheduled Arrival), independent of
    transfer count or any individual transfer's miss probability. Applies
    uniformly, including to direct (0-transfer) routes (UIUX_SPEC.md §2.3).
    """
    if p85_penalty_minutes <= GLOBAL_HEALTH_YELLOW_MAX_MINUTES:
        return "low"
    if p85_penalty_minutes <= GLOBAL_HEALTH_RED_MIN_MINUTES:
        return "medium"
    return "high"


def format_duration(start: datetime, end: datetime) -> str:
    total_minutes = int((end - start).total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def _delay_label(scheduled: datetime, projected: datetime) -> str:
    minutes = round((projected - scheduled).total_seconds() / 60)
    return "on time" if minutes <= 0 else f"+{minutes}m"


def _fallback_arrival_label(route_scheduled_arrival: datetime, impact_minutes: float) -> str:
    """UIUX_SPEC.md §1.3 (history #18-#19) — shows the fallback's own
    absolute arrival clock time rather than a relative delta, for any
    base-Red transfer (Miss likely and a downgraded Recoverable miss alike —
    SPEC.md §5.3's Impact Override changes the color band, not which figure
    is relevant). A number like "+12 min" or "on time" still begs the
    question "relative to what" (the route's own Scheduled Arrival, per
    SPEC.md §3.4/§5.3 -- not the Monte Carlo Expected or Safest figures
    shown elsewhere on the card); a concrete clock time sidesteps that
    ambiguity entirely, since it's self-evidently comparable to the
    Scheduled Arrival time already printed in the card header without the
    reader needing to know what the number is measured against.
    """
    fallback_arrival = route_scheduled_arrival + timedelta(minutes=impact_minutes)
    return f"arrives {fallback_arrival:%H:%M} if missed"


def _chip_class(leg: Leg, lines_by_id: dict[str, Line]) -> str:
    line = lines_by_id.get(leg.line_id)
    return LINE_TYPE_CHIP_CLASS.get(line.type if line else "", DEFAULT_CHIP_CLASS)


def inject_global_styles() -> None:
    """Emits the app-wide <style> block once (design_mock.html §1–2, ported to CSS vars)."""
    st.markdown(
        f"""
        <style>
        :root {{
            --ink:{INK}; --muted:{MUTED}; --faint:{FAINT}; --line:{LINE}; --card-bg:{CARD_BG};
            --low-bg:{RISK_TOKENS["low"]["bg"]}; --low-border:{RISK_TOKENS["low"]["border"]}; --low-text:{RISK_TOKENS["low"]["text"]};
            --med-bg:{RISK_TOKENS["medium"]["bg"]}; --med-border:{RISK_TOKENS["medium"]["border"]}; --med-text:{RISK_TOKENS["medium"]["text"]};
            --high-bg:{RISK_TOKENS["high"]["bg"]}; --high-border:{RISK_TOKENS["high"]["border"]}; --high-text:{RISK_TOKENS["high"]["text"]};
            --chip-ice:#33383f; --chip-re-bg:#d3d6db; --chip-re-text:#20232a; --chip-sbahn:#0f8a3f;
            --db-red:{DB_RED};
        }}

        /* ---- page shell: cap width so the app doesn't stretch on ultra-wide monitors ---- */
        [data-testid="stMainBlockContainer"]{{max-width:1050px !important; margin-left:auto !important; margin-right:auto !important;}}

        /* ---- app shell (typography/geometry only — no emoji, per spec item 1) ---- */
        .app-banner{{background:var(--db-red); border-radius:10px; padding:1.1rem 1.5rem; margin-bottom:1.5rem; display:flex; align-items:center; gap:0.9rem;}}
        .db-mark{{background:#fff; color:var(--db-red); font-weight:800; font-size:1rem; letter-spacing:.02em; border-radius:6px; padding:0.4rem 0.55rem; line-height:1;}}
        .app-banner-title{{color:#fff; font-size:1.5rem; font-weight:700; line-height:1.25;}}
        .app-banner-sub{{color:#FBD7DB; font-size:0.85rem; margin-top:2px;}}

        /* ---- search card (design_mock.html §1) ---- */
        .st-key-search_card{{background:var(--card-bg); border:1px solid var(--line); border-radius:12px; box-shadow:0 2px 6px rgba(20,20,30,.06); padding:18px 20px 20px;}}
        .search-title{{font-size:15px; font-weight:700; margin-bottom:14px;}}
        .search-divider{{border:none; border-top:1px solid var(--line); margin:6px 0 14px;}}
        .st-key-search_card [data-testid="stWidgetLabel"] p{{font-size:11.5px; font-weight:600; color:var(--muted);}}
        .st-key-search_card [data-testid="stSelectbox"] [role="group"],
        .st-key-search_card [data-testid="stTimeInputTimeDisplay"],
        .st-key-search_card [data-testid="stDateInputField"]{{background:#f4f5f7 !important; border-color:var(--line) !important; border-radius:8px !important; font-weight:600;}}

        /* Swap button: force a true 36px circle (Streamlit's own button min-height
           otherwise wins over a plain height:36px and stretches it into an oval).
           Streamlit's own vertical_alignment="bottom" on the station-row columns
           already lands the button flush against the *bottom* of the 40px input
           boxes (not the labels) — a 2px margin-bottom then nudges it up from
           flush-bottom to truly centered within that 40px box height, since a
           36px button flush to the bottom of a 40px box sits 2px low otherwise.
           Horizontally, the button's own wrapper chain (stButton, stElementContainer)
           shrink-wraps tight to the button's 36px width, so margin:auto on the
           button itself has no free space to distribute. The actual free space —
           the middle column being wider than the button — lives one level up, on
           stElementContainer as a flex-item of stVerticalBlock (the column's own
           flex container); centering has to happen there via the .st-key-swap_stations
           class (which targets that exact stElementContainer), not on the button.
           The icon itself is a Material Symbol (see render_search_card), not the
           "⇄" text glyph — text-glyph baselines vary by font/OS and can look
           visually off-center even when the button's own box measures centered. */
        .st-key-swap_stations{{margin-left:auto !important; margin-right:auto !important;}}
        .st-key-swap_stations button{{width:36px !important; height:36px !important; min-height:36px !important; margin-bottom:2px !important; border-radius:50% !important; padding:0 !important; background:#fff; border:1px solid var(--line); box-shadow:0 1px 2px rgba(20,20,30,.08); color:var(--muted);}}

        /* Secondary row: the segmented control's pill renders shorter (32px) than
           the date/time input boxes (40px) — match heights so Date, Departure time,
           and Sort share one consistent baseline instead of the pill trailing low.
           Streamlit's default flex-basis is "fit-content", so the two options grow
           proportionally from their own (unequal) text widths rather than splitting
           the row evenly — flex-basis:0 forces a true 50/50 split regardless of
           how much longer one option's label is than the other's. */
        .st-key-search_card [data-testid="stButtonGroup"] [role="radiogroup"]{{min-height:40px !important;}}
        .st-key-search_card [data-testid="stButtonGroup"] button[role="radio"]{{min-height:40px !important; flex:1 1 0 !important;}}
        .st-key-search_card [data-testid="stButtonGroup"] button[aria-checked="true"]{{background:var(--ink) !important; border-color:var(--ink) !important;}}
        .st-key-search_card [data-testid="stButtonGroup"] button[aria-checked="true"] p{{color:#fff !important;}}

        /* ---- route cards (design_mock.html §2) ---- */
        .card{{background:var(--card-bg); border:1px solid var(--line); border-radius:10px; overflow:hidden; box-shadow:0 1px 2px rgba(20,20,30,.04); margin-bottom:14px; border-left:4px solid var(--line);}}
        .card.strip-low{{border-left-color:var(--low-border);}}
        .card.strip-medium{{border-left-color:var(--med-border);}}
        .card.strip-high{{border-left-color:var(--high-border);}}

        .head-row{{display:grid; grid-template-columns:1fr auto; align-items:start; padding:16px 16px 0; gap:12px;}}
        .sched-time{{font-size:20px; font-weight:700; font-variant-numeric:tabular-nums;}}
        .sched-meta{{font-size:12px; color:var(--muted); margin-top:2px;}}

        .predictions{{border:1px solid var(--line); border-radius:8px; padding:7px 13px 8px;}}
        .pred-cap{{font-size:9px; font-weight:700; text-transform:uppercase; letter-spacing:.04em; color:var(--faint); text-align:right; margin-bottom:5px;}}

        /* Wide cards: Expected/Safest sit side-by-side as two single-line rows
           (name, value, delta all in one nowrap flow), divided by a dashed rule
           — this is what keeps the value+delta from breaking onto a second line
           when the box would otherwise be squeezed. Narrow viewports (§ below)
           fall back to a vertical stack instead. */
        .pred-rows{{display:flex; flex-direction:row; align-items:center; gap:14px;}}
        .pred-row{{display:flex; flex-direction:row; align-items:baseline; gap:6px; white-space:nowrap;}}
        .pred-row + .pred-row{{border-left:1px dashed var(--line); padding-left:14px;}}
        .pred-name{{font-size:11px; font-weight:600; color:var(--muted); white-space:nowrap;}}
        .pred-value{{font-variant-numeric:tabular-nums; white-space:nowrap;}}

        /* Expected is context, Safest is the headline recommendation — the two
           sides should not read as equal-weight siblings. Expected is pushed
           down in weight and color; Safest is pushed up in both. */
        .pred-row.expected .pred-value{{font-size:13px; font-weight:500; color:var(--muted);}}
        .pred-row.safest .pred-name{{color:var(--ink); font-weight:700;}}
        .pred-row.safest .pred-value{{font-size:17px; font-weight:800; color:var(--ink);}}
        .pred-delta{{font-size:10px; color:var(--faint); font-weight:600; margin-left:3px; white-space:nowrap;}}

        @media (max-width: 480px) {{
            .pred-rows{{flex-direction:column; align-items:stretch; gap:0;}}
            .pred-row{{justify-content:space-between;}}
            .pred-row + .pred-row{{border-left:none; border-top:1px dashed var(--line); margin-top:5px; padding-top:6px; padding-left:0;}}
        }}

        .train-bar{{display:flex; gap:3px; padding:12px 16px 0;}}
        .chip{{display:flex; align-items:center; justify-content:center; min-height:34px; border-radius:5px; font-size:12.5px; font-weight:700; letter-spacing:.02em; padding:0 10px; min-width:78px;}}
        .chip-ice{{background:var(--chip-ice); color:#fff;}}
        .chip-re{{background:var(--chip-re-bg); color:var(--chip-re-text); border:1px solid #b9bdc4;}}
        .chip-sbahn{{background:var(--chip-sbahn); color:#fff;}}
        .transfer-gap{{width:3px; border-left:2px dashed var(--faint); margin:2px 3px; flex:0 0 auto;}}

        .station-row2{{display:flex; justify-content:space-between; padding:9px 16px 2px; font-size:13.5px; font-weight:600;}}
        .station-row2 span:last-child{{text-align:right;}}

        .itin-details{{border-top:1px solid var(--line); margin-top:10px;}}
        .itin-details > summary{{list-style:none; cursor:pointer; display:flex; align-items:center; justify-content:center; gap:6px; padding:11px; font-size:12.5px; font-weight:600; color:var(--muted); user-select:none;}}
        .itin-details > summary:hover{{color:var(--ink);}}
        .itin-details > summary::-webkit-details-marker{{display:none;}}
        .itin-details > summary::marker{{content:"";}}
        .itin-details > summary .caret{{width:0; height:0; border-left:4px solid transparent; border-right:4px solid transparent; border-top:5px solid var(--faint); transition:transform .15s ease;}}
        .itin-details[open] > summary .caret{{transform:rotate(180deg);}}
        .itin-details > summary .lbl-closed{{display:inline;}}
        .itin-details > summary .lbl-open{{display:none;}}
        .itin-details[open] > summary .lbl-closed{{display:none;}}
        .itin-details[open] > summary .lbl-open{{display:inline;}}

        .itin{{padding:6px 16px 4px;}}
        .timetable{{display:grid; grid-template-columns:42px 20px 1fr; row-gap:0; column-gap:9px;}}
        .tt-time{{font-variant-numeric:tabular-nums; font-size:12.5px; font-weight:600; padding:9px 0 9px;}}
        .tt-dot-col{{display:flex; flex-direction:column; align-items:center;}}
        .tt-dot{{width:9px; height:9px; border-radius:50%; background:var(--ink); margin-top:14px; flex:0 0 auto;}}
        .tt-dot.hollow{{background:#fff; border:2px solid var(--ink); width:7px; height:7px;}}
        .tt-connector{{flex:1 1 auto; width:2px; background:var(--ink); margin-top:2px;}}
        .tt-connector.dashed{{background:none; border-left:2px dashed var(--faint); width:0;}}
        .tt-station-wrap{{padding:8px 0; display:flex; align-items:center; justify-content:space-between; gap:8px;}}
        .tt-station{{font-size:13px; font-weight:600;}}
        .chip-sm{{min-height:auto; padding:3px 8px; font-size:10.5px; min-width:0; border-radius:4px;}}

        .transfer-bar{{border-radius:7px; padding:9px 12px; margin:3px 0; align-self:center; display:flex; align-items:center; gap:8px; flex-wrap:wrap;}}
        .transfer-bar .t-headline{{font-size:12.5px; font-weight:700;}}
        .transfer-bar .t-buffer{{font-size:12px; font-weight:400; opacity:.75;}}
        .transfer-bar .t-platform{{font-size:12px; font-weight:400; opacity:.75;}}
        .risk-low{{background:var(--low-bg); color:var(--low-text);}}
        .risk-medium{{background:var(--med-bg); color:var(--med-text);}}
        .risk-high{{background:var(--high-bg); color:var(--high-text);}}

        /* ---- "Load more" pagination row -- same muted-text hover as
           .itin-details' "Details" toggle above (design consistency: one
           expand-cue vocabulary for the whole app). ---- */
        .st-key-load_more_row{{display:flex; flex-direction:column; align-items:center; gap:8px; margin:4px 0 20px;}}
        .st-key-load_more_row [data-testid="stCaptionContainer"]{{color:var(--faint); font-size:12px;}}
        .st-key-load_more_routes{{width:100%; max-width:320px;}}
        .st-key-load_more_routes button{{background:#fff; border:1px solid var(--line); border-radius:8px; color:var(--muted); font-weight:600; font-size:12.5px; padding:11px 16px; box-shadow:0 1px 2px rgba(20,20,30,.04);}}
        .st-key-load_more_routes button:hover{{color:var(--ink);}}
        .st-key-load_more_routes button p{{font-weight:600; margin:0;}}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header_banner(n_iterations: int) -> None:
    """DB-style red banner. Uses a typographic "DB" mark instead of a train emoji."""
    st.markdown(
        f"""
        <div class="app-banner">
            <div class="db-mark">DB</div>
            <div>
                <div class="app-banner-title">DB Risk &amp; Rescue</div>
                <div class="app-banner-sub">
                    Probability-aware trip planning — True Expected Time of Arrival via
                    {n_iterations:,}-iteration Monte Carlo simulation.
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_search_card(
    station_ids: list[str],
    stations_by_id: dict[str, Station],
    default_origin_id: str,
    default_destination_id: str,
    default_departure_time: time,
    sort_options: tuple[str, str],
    *,
    calendar_range: tuple[date, date] | None = None,
    default_date: date | None = None,
) -> tuple[str, str, date | None, time, str]:
    """Renders the "Plan your trip" search card (design_mock.html §1).

    Returns (origin_id, destination_id, service_date, departure_time, sort_choice).
    service_date is None when calendar_range isn't provided (Phase 1/2 datasets
    are baked to a single fixed calendar date).
    """
    if "search_origin_id" not in st.session_state or st.session_state.search_origin_id not in station_ids:
        st.session_state.search_origin_id = default_origin_id
    if (
        "search_destination_id" not in st.session_state
        or st.session_state.search_destination_id not in station_ids
    ):
        st.session_state.search_destination_id = default_destination_id
    if "search_sort" not in st.session_state:
        st.session_state.search_sort = sort_options[0]
    if calendar_range is not None:
        calendar_min, calendar_max = calendar_range
        if "search_date" not in st.session_state or not (calendar_min <= st.session_state.search_date <= calendar_max):
            st.session_state.search_date = default_date

    def _swap_stations() -> None:
        st.session_state.search_origin_id, st.session_state.search_destination_id = (
            st.session_state.search_destination_id,
            st.session_state.search_origin_id,
        )

    with st.container(border=False, key="search_card"):
        st.markdown('<div class="search-title">Plan your trip</div>', unsafe_allow_html=True)

        station_cols = st.columns([5, 1, 5], vertical_alignment="bottom", gap="small")
        origin_id = station_cols[0].selectbox(
            "Origin",
            options=station_ids,
            format_func=lambda sid: stations_by_id[sid].name,
            key="search_origin_id",
        )
        with station_cols[1]:
            st.button("", icon=":material/swap_horiz:", key="swap_stations", on_click=_swap_stations)
        destination_id = station_cols[2].selectbox(
            "Destination",
            options=station_ids,
            format_func=lambda sid: stations_by_id[sid].name,
            key="search_destination_id",
        )

        st.markdown('<hr class="search-divider">', unsafe_allow_html=True)

        if calendar_range is not None:
            calendar_min, calendar_max = calendar_range
            datetime_cols = st.columns([1, 1], vertical_alignment="bottom", gap="small")
            service_date = datetime_cols[0].date_input(
                "Date", min_value=calendar_min, max_value=calendar_max, key="search_date"
            )
            departure_time = datetime_cols[1].time_input(
                "Departure at or after", value=default_departure_time, key="search_time"
            )
        else:
            service_date = None
            departure_time = st.time_input(
                "Departure at or after", value=default_departure_time, key="search_time"
            )

        st.markdown('<hr class="search-divider">', unsafe_allow_html=True)

        sort_choice = st.segmented_control(
            "Sort by", sort_options, key="search_sort", required=True, width="stretch"
        )

    return origin_id, destination_id, service_date, departure_time, sort_choice


def _train_bar_html(ordered_legs: list[Leg], lines_by_id: dict[str, Line]) -> str:
    """Full-width chip segments sized proportional to each leg's travel time."""
    parts = []
    for i, leg in enumerate(ordered_legs):
        if i > 0:
            parts.append('<div class="transfer-gap"></div>')
        duration_minutes = max((leg.scheduled_arrival - leg.scheduled_departure).total_seconds() / 60, 1)
        parts.append(
            f'<div class="chip {_chip_class(leg, lines_by_id)}" style="flex:{duration_minutes:.1f};">'
            f"{leg.line_id}</div>"
        )
    return f'<div class="train-bar">{"".join(parts)}</div>'


def _itinerary_html(
    route: Route,
    legs_by_id: dict[str, Leg],
    transfers_by_id: dict[str, Transfer],
    stations_by_id: dict[str, Station],
    lines_by_id: dict[str, Line],
    transfer_risks: list[TransferRisk],
) -> str:
    """Vertical CSS-grid itinerary: solid dots/lines for legs, hollow rings and
    dashed lines for interchanges, with an inline risk-colored transfer bar."""
    ordered_legs = [legs_by_id[leg_id] for leg_id in route.legs]
    risk_by_transfer_id = {r.transfer_id: r for r in transfer_risks}
    n_legs = len(ordered_legs)

    rows = []
    for i, leg in enumerate(ordered_legs):
        origin = stations_by_id[leg.origin_station_id].name
        destination = stations_by_id[leg.destination_station_id].name
        is_last_leg = i == n_legs - 1
        chip_class = _chip_class(leg, lines_by_id)

        rows.append(
            '<div style="display:contents;">'
            f'<div class="tt-time">{leg.scheduled_departure:%H:%M}</div>'
            '<div class="tt-dot-col"><div class="tt-dot"></div><div class="tt-connector"></div></div>'
            f'<div class="tt-station-wrap"><span class="tt-station">{origin}</span>'
            f'<span class="chip {chip_class} chip-sm">{leg.line_id}</span></div>'
            "</div>"
        )

        connector = '<div class="tt-connector dashed"></div>' if not is_last_leg else ""
        rows.append(
            '<div style="display:contents;">'
            f'<div class="tt-time">{leg.scheduled_arrival:%H:%M}</div>'
            f'<div class="tt-dot-col"><div class="tt-dot hollow"></div>{connector}</div>'
            f'<div class="tt-station-wrap"><span class="tt-station">{destination}</span></div>'
            "</div>"
        )

        if not is_last_leg:
            downstream_leg = ordered_legs[i + 1]
            transfer_id = route.transfers[i]
            transfer = transfers_by_id[transfer_id]
            risk = risk_by_transfer_id[transfer_id]
            risk_level, is_override = classify_local_risk(risk.miss_probability, risk.impact_minutes)
            # SPEC.md §3.6.4 — a below-MCT connection can never read as fully
            # "Safe": even when engine.py's gradient floor is too small to
            # push the numeric probability past the low/medium threshold on
            # its own, a scheduled buffer under the station's MCT still
            # deserves at least a "pay attention" band. This band floor is
            # deliberately silent about *why* -- the passenger's action
            # (don't dawdle) is identical whether the cause is delay history
            # or station size, so it folds into the existing "Tight
            # connection" phrase rather than earning a separate word.
            if risk.below_mct and risk_level == "low":
                risk_level = "medium"
            # A below-MCT transfer that lands base-High with no rescuing
            # fallback is a *physical* impossibility, not a statistical one:
            # the headline says so instead of "Miss likely". When the Impact
            # Override applies instead (a cheap fallback exists), it stays
            # "Recoverable miss" -- the reassuring, actionable truth wins
            # over the diagnostic one when both are true (SPEC.md §3.6.4,
            # UIUX_SPEC.md §1.4).
            is_mct_violation = risk.below_mct and risk_level == "high" and not is_override
            phrase = (
                RISK_WORDING_MCT_VIOLATION
                if is_mct_violation
                else RISK_WORDING_OVERRIDE if is_override else RISK_WORDING[risk_level]
            )
            # UIUX_SPEC.md §1.3 — any base-Red transfer (Miss likely *or* a
            # downgraded Recoverable miss) shows the fallback arrival instead
            # of the scheduled buffer: a base-Red transfer's own buffer is by
            # definition tight, so it'd just restate "this is risky" rather
            # than answer the question that actually matters once a miss is
            # the likely outcome -- what happens if it's missed. Buffer stays
            # for Safe/Tight (including a below-MCT connection folded into
            # Tight above), where the connection is expected to hold and
            # "how much slack" is the relevant framing -- deliberately the
            # same plain minutes figure regardless of cause, per SPEC.md
            # §3.6.4: a comparison figure here was tried and rejected as
            # requiring context ("10 of what?") no passenger has in hand.
            is_base_high = classify_risk(risk.miss_probability) == "high"
            minutes_label = (
                _fallback_arrival_label(route.scheduled_arrival, risk.impact_minutes)
                if is_base_high
                else f"{transfer.scheduled_buffer_minutes} min"
            )
            # Platform info (SPEC.md §7's proposed extension): real GTFS.DE
            # platform_code coverage is sparse and, at this corridor's major
            # hubs specifically, close to 0% (confirmed against the real
            # feed) -- shown only when both the arriving and departing leg
            # actually have one, hidden gracefully otherwise rather than
            # printing a placeholder for missing data.
            platform_html = ""
            if leg.destination_platform and downstream_leg.origin_platform:
                platform_html = (
                    '<span class="t-platform">Plat. '
                    f"{leg.destination_platform} → Plat. {downstream_leg.origin_platform}"
                    "</span>"
                )
            rows.append(
                '<div style="display:contents;">'
                '<div class="tt-time"></div>'
                '<div class="tt-dot-col"><div class="tt-connector dashed"></div></div>'
                f'<div class="transfer-bar risk-{risk_level}">'
                f'<span class="t-headline">{phrase} ({risk.miss_probability:.0%} risk)</span>'
                f'<span class="t-buffer">·  {minutes_label}</span>'
                f"{platform_html}"
                "</div></div>"
            )

    return (
        '<details class="itin-details"><summary>'
        '<span class="lbl-closed">Details</span><span class="lbl-open">Hide details</span>'
        '<div class="caret"></div></summary>'
        f'<div class="itin"><div class="timetable">{"".join(rows)}</div></div>'
        "</details>"
    )


def render_route_card(
    route: Route,
    result: RouteSimulationResult,
    stations_by_id: dict[str, Station],
    legs_by_id: dict[str, Leg],
    transfers_by_id: dict[str, Transfer],
    lines_by_id: dict[str, Line],
) -> None:
    """Renders one DB Navigator-style route card as a single HTML block, per
    design_mock.html §2 — the itinerary's <details> toggle needs no Streamlit
    rerun since the whole card, including its expander, is static markup."""
    duration = format_duration(route.scheduled_departure, route.scheduled_arrival)
    n_transfers = len(route.transfers)
    ordered_legs = [legs_by_id[leg_id] for leg_id in route.legs]
    origin = stations_by_id[route.origin_station_id].name
    destination = stations_by_id[route.destination_station_id].name

    if n_transfers == 0:
        transfer_meta = "Direct"
    else:
        transfer_meta = f"{n_transfers} transfer" + ("" if n_transfers == 1 else "s")

    # SPEC.md §5.2 — Global Health: driven solely by the P85 penalty, applies
    # uniformly including to direct (0-transfer) routes.
    health_level = classify_global_health(result.p85_penalty_minutes)
    strip_class = f" strip-{health_level}"

    expected_delay = _delay_label(route.scheduled_arrival, result.mean_eta)
    safest_delay = _delay_label(route.scheduled_arrival, result.p85_eta)

    html = f"""
    <div class="card{strip_class}">
      <div class="head-row">
        <div>
          <div class="sched-time">{route.scheduled_departure:%H:%M} – {route.scheduled_arrival:%H:%M}</div>
          <div class="sched-meta">{duration} · {transfer_meta}</div>
        </div>
        <div class="predictions">
          <div class="pred-cap">Predicted arrival</div>
          <div class="pred-rows">
            <div class="pred-row expected"><span class="pred-name">Expected</span>
              <span><span class="pred-value">{result.mean_eta:%H:%M}</span><span class="pred-delta">{expected_delay}</span></span></div>
            <div class="pred-row safest"><span class="pred-name">Safest</span>
              <span><span class="pred-value">{result.p85_eta:%H:%M}</span><span class="pred-delta">{safest_delay}</span></span></div>
          </div>
        </div>
      </div>
      {_train_bar_html(ordered_legs, lines_by_id)}
      <div class="station-row2"><span>{origin}</span><span>{destination}</span></div>
      {_itinerary_html(route, legs_by_id, transfers_by_id, stations_by_id, lines_by_id, result.transfer_risks)}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)
