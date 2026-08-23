"""Streamlit rendering helpers for the route comparison and detail views (SPEC.md §4)."""

from datetime import datetime

import streamlit as st

from engine import RouteSimulationResult, TransferRisk
from models import Leg, Route, Station, Transfer

# SPEC.md §4.3 — thresholds to be tuned during prototyping.
# "high" uses official DB Red for brand-consistent risk signaling.
RISK_COLORS = {"low": "#2E7D32", "medium": "#F5A623", "high": "#EB0016"}

DB_RED = "#EB0016"
DB_CHARCOAL = "#212529"
DB_BORDER = "#DEE2E6"
DB_LIGHT_GREY = "#F1F3F5"


def classify_risk(miss_probability: float) -> str:
    if miss_probability < 0.10:
        return "low"
    if miss_probability <= 0.30:
        return "medium"
    return "high"


def format_duration(start: datetime, end: datetime) -> str:
    total_minutes = int((end - start).total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def _delay_label(scheduled: datetime, projected: datetime) -> str:
    minutes = round((projected - scheduled).total_seconds() / 60)
    return "on time" if minutes <= 0 else f"+{minutes} min"


def render_header_banner(n_iterations: int) -> None:
    """DB-style red banner replacing a plain st.title — the app's branding element."""
    st.markdown(
        f"""
        <div style="
            background: {DB_RED};
            border-radius: 10px;
            padding: 1.1rem 1.5rem;
            margin-bottom: 1.5rem;
            display: flex;
            align-items: center;
            gap: 0.9rem;
        ">
            <span style="font-size: 2rem; line-height: 1;">🚆</span>
            <div>
                <div style="color: #FFFFFF; font-size: 1.5rem; font-weight: 700; line-height: 1.25;">
                    DB Risk &amp; Rescue
                </div>
                <div style="color: #FBD7DB; font-size: 0.85rem;">
                    Probability-aware trip planning — True Expected Time of Arrival via
                    {n_iterations:,}-iteration Monte Carlo simulation.
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_route_card(
    route: Route,
    result: RouteSimulationResult,
    stations_by_id: dict[str, Station],
    is_selected: bool = False,
) -> bool:
    """Renders one route card per SPEC.md §4.2. Returns True if its Details button was clicked."""
    origin = stations_by_id[route.origin_station_id].name
    destination = stations_by_id[route.destination_station_id].name
    duration = format_duration(route.scheduled_departure, route.scheduled_arrival)
    n_transfers = len(route.transfers)

    with st.container(border=True):
        header_col, badge_col = st.columns([5, 1])
        header_col.markdown(f"**{origin} → {destination}**")
        if is_selected:
            badge_col.markdown(
                f'<span style="background:{DB_RED}; color:#FFFFFF; padding:0.2rem 0.6rem; '
                f'border-radius:999px; font-size:0.75rem; font-weight:600;">📍 Viewing</span>',
                unsafe_allow_html=True,
            )

        st.caption(
            f"{route.scheduled_departure:%H:%M} → {route.scheduled_arrival:%H:%M}  ·  "
            f"{duration}  ·  {n_transfers} transfer{'s' if n_transfers != 1 else ''}"
        )

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Scheduled arrival", f"{route.scheduled_arrival:%H:%M}")
        m2.metric(
            "Mean True ETA",
            f"{result.mean_eta:%H:%M}",
            delta=_delay_label(route.scheduled_arrival, result.mean_eta),
            delta_color="inverse",
        )
        m3.metric(
            "P85 True ETA",
            f"{result.p85_eta:%H:%M}",
            delta=_delay_label(route.scheduled_arrival, result.p85_eta),
            delta_color="inverse",
        )
        clicked = m4.button(
            "View timeline", key=f"select_{route.route_id}", use_container_width=True
        )

    return clicked


def render_route_timeline(
    route: Route,
    legs_by_id: dict[str, Leg],
    transfers_by_id: dict[str, Transfer],
    stations_by_id: dict[str, Station],
    transfer_risks: list[TransferRisk],
) -> None:
    """Renders the leg → transfer → leg horizontal timeline per SPEC.md §4.3."""
    ordered_legs = [legs_by_id[leg_id] for leg_id in route.legs]
    risk_by_transfer_id = {r.transfer_id: r for r in transfer_risks}

    leg_width, gap, node_radius, height = 220, 90, 16, 140
    total_width = 40 + len(ordered_legs) * leg_width + max(len(ordered_legs) - 1, 0) * gap
    y_mid = height / 2 - 5

    parts = [
        f'<svg width="{total_width}" height="{height}" '
        f'viewBox="0 0 {total_width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="sans-serif" style="max-width:100%;">'
    ]

    cursor = 20
    for i, leg in enumerate(ordered_legs):
        origin = stations_by_id[leg.origin_station_id].name
        destination = stations_by_id[leg.destination_station_id].name

        parts.append(
            f'<rect x="{cursor}" y="{y_mid - 22}" width="{leg_width}" height="48" rx="8" '
            f'fill="{DB_LIGHT_GREY}" stroke="{DB_BORDER}" stroke-width="1.5"/>'
        )
        parts.append(
            f'<rect x="{cursor}" y="{y_mid - 22}" width="{leg_width}" height="4" rx="2" fill="{DB_RED}"/>'
        )
        parts.append(
            f'<text x="{cursor + leg_width / 2}" y="{y_mid - 3}" text-anchor="middle" '
            f'font-size="12" font-weight="700" fill="{DB_CHARCOAL}">{leg.line_id}</text>'
        )
        parts.append(
            f'<text x="{cursor + leg_width / 2}" y="{y_mid + 13}" text-anchor="middle" '
            f'font-size="10" fill="#495057">{origin} → {destination}</text>'
        )
        parts.append(
            f'<text x="{cursor + leg_width / 2}" y="{y_mid + 52}" text-anchor="middle" '
            f'font-size="10" fill="#6c757d">{leg.scheduled_departure:%H:%M} – {leg.scheduled_arrival:%H:%M}</text>'
        )

        leg_right_edge = cursor + leg_width
        cursor = leg_right_edge

        if i < len(route.transfers):
            transfer_id = route.transfers[i]
            transfer = transfers_by_id[transfer_id]
            risk = risk_by_transfer_id[transfer_id]
            risk_level = classify_risk(risk.miss_probability)
            color = RISK_COLORS[risk_level]
            # Amber is too light for white text to stay readable (WCAG contrast).
            node_text_color = DB_CHARCOAL if risk_level == "medium" else "#FFFFFF"
            node_cx = cursor + gap / 2

            parts.append(
                f'<line x1="{leg_right_edge}" y1="{y_mid}" x2="{cursor + gap}" y2="{y_mid}" '
                f'stroke="#ADB5BD" stroke-width="2" stroke-dasharray="4,3"/>'
            )
            parts.append(
                f'<circle cx="{node_cx}" cy="{y_mid}" r="{node_radius}" fill="{color}" '
                f'stroke="#FFFFFF" stroke-width="2">'
                f'<title>{stations_by_id[transfer.station_id].name}: '
                f'{risk.miss_probability:.0%} chance of missing this connection '
                f'(scheduled buffer {transfer.scheduled_buffer_minutes} min)</title></circle>'
            )
            parts.append(
                f'<text x="{node_cx}" y="{y_mid + 4}" text-anchor="middle" font-size="10" '
                f'font-weight="700" fill="{node_text_color}">{risk.miss_probability:.0%}</text>'
            )
            parts.append(
                f'<text x="{node_cx}" y="{y_mid - node_radius - 10}" text-anchor="middle" '
                f'font-size="9" fill="{DB_CHARCOAL}">{stations_by_id[transfer.station_id].name}</text>'
            )

            cursor += gap

    parts.append("</svg>")
    st.markdown("".join(parts), unsafe_allow_html=True)

    legend_labels = [
        ("low", "Low risk — P(miss) < 10%"),
        ("medium", "Medium risk — 10–30%"),
        ("high", "High risk — > 30%"),
    ]
    legend_cols = st.columns(3)
    for col, (level, label) in zip(legend_cols, legend_labels):
        col.markdown(
            f'<span style="display:inline-block; width:0.7rem; height:0.7rem; '
            f'border-radius:50%; background:{RISK_COLORS[level]}; margin-right:0.4rem;"></span>'
            f'{label}',
            unsafe_allow_html=True,
        )
