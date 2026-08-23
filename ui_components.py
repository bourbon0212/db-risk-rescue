"""Streamlit rendering helpers for the route comparison and detail views (SPEC.md §4)."""

from datetime import datetime

import streamlit as st

from engine import RouteSimulationResult, TransferRisk
from models import Leg, Route, Station, Transfer

# SPEC.md §4.3 — thresholds to be tuned during prototyping.
RISK_COLORS = {"low": "#2ecc71", "medium": "#f1c40f", "high": "#e74c3c"}


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
            badge_col.markdown("📍 *Viewing*")

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
            f'fill="#eef2f7" stroke="#4a6fa5" stroke-width="1.5"/>'
        )
        parts.append(
            f'<text x="{cursor + leg_width / 2}" y="{y_mid - 5}" text-anchor="middle" '
            f'font-size="12" font-weight="700" fill="#1a1a1a">{leg.line_id}</text>'
        )
        parts.append(
            f'<text x="{cursor + leg_width / 2}" y="{y_mid + 12}" text-anchor="middle" '
            f'font-size="10" fill="#333">{origin} → {destination}</text>'
        )
        parts.append(
            f'<text x="{cursor + leg_width / 2}" y="{y_mid + 52}" text-anchor="middle" '
            f'font-size="10" fill="#666">{leg.scheduled_departure:%H:%M} – {leg.scheduled_arrival:%H:%M}</text>'
        )

        leg_right_edge = cursor + leg_width
        cursor = leg_right_edge

        if i < len(route.transfers):
            transfer_id = route.transfers[i]
            transfer = transfers_by_id[transfer_id]
            risk = risk_by_transfer_id[transfer_id]
            color = RISK_COLORS[classify_risk(risk.miss_probability)]
            node_cx = cursor + gap / 2

            parts.append(
                f'<line x1="{leg_right_edge}" y1="{y_mid}" x2="{cursor + gap}" y2="{y_mid}" '
                f'stroke="#9aa5b1" stroke-width="2" stroke-dasharray="4,3"/>'
            )
            parts.append(
                f'<circle cx="{node_cx}" cy="{y_mid}" r="{node_radius}" fill="{color}" '
                f'stroke="#1a1a1a" stroke-width="1">'
                f'<title>{stations_by_id[transfer.station_id].name}: '
                f'{risk.miss_probability:.0%} chance of missing this connection '
                f'(scheduled buffer {transfer.scheduled_buffer_minutes} min)</title></circle>'
            )
            parts.append(
                f'<text x="{node_cx}" y="{y_mid + 4}" text-anchor="middle" font-size="10" '
                f'font-weight="700" fill="#1a1a1a">{risk.miss_probability:.0%}</text>'
            )
            parts.append(
                f'<text x="{node_cx}" y="{y_mid - node_radius - 10}" text-anchor="middle" '
                f'font-size="9" fill="#555">{stations_by_id[transfer.station_id].name}</text>'
            )

            cursor += gap

    parts.append("</svg>")
    st.markdown("".join(parts), unsafe_allow_html=True)

    legend_cols = st.columns(3)
    legend_cols[0].markdown("🟢 **Low risk** — P(miss) < 10%")
    legend_cols[1].markdown("🟡 **Medium risk** — 10–30%")
    legend_cols[2].markdown("🔴 **High risk** — > 30%")
