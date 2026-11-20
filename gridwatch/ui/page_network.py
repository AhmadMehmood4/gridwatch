"""Transmission Network Map: single-line-style diagram with selectable equipment."""
from __future__ import annotations

import streamlit as st

from gridwatch.analytics import run_query
from gridwatch.network import build_graph, n_minus_1_topology, topology_report
from gridwatch.ui import charts, tables
from gridwatch.ui.context import AppContext, live_section
from gridwatch.ui.theme import (C, KV_COLOR, empty_state, kpi_html, note, page_title, section,
                                status_strip)


def render(ctx: AppContext) -> None:
    page_title("Transmission network map",
               "Schematic single-line view. Click a loading badge on the map, or pick equipment from the list.")
    live_section(ctx, _body)


def _handle_click(event, codes: list[str]) -> None:
    """Map click -> selection. Only reacts to a *new* click so the selectbox stays in control."""
    try:
        points = event.selection.points
    except AttributeError:
        points = event.get("selection", {}).get("points", []) if isinstance(event, dict) else []
    clicked = None
    for p in points or []:
        cd = p.get("customdata")
        cd = cd[0] if isinstance(cd, (list, tuple)) and cd else cd
        if cd in codes:
            clicked = cd
            break
    if clicked is None:
        st.session_state["map_last_click"] = None
        return
    if clicked != st.session_state.get("map_last_click"):
        st.session_state["map_last_click"] = clicked
        if clicked != st.session_state.get("map_sel"):
            st.session_state["map_sel"] = clicked
            st.rerun(scope="fragment")


def _body(ctx: AppContext) -> None:
    snap = ctx.sim.get_state()
    status_strip(snap)
    th = ctx.thresholds()
    latest = run_query(ctx.engine, "latest_measurements")
    if latest.empty:
        empty_state("No telemetry yet. Start or step the simulation.")
        return
    subs = ctx.substations()
    codes = list(latest["line_code"])
    if st.session_state.get("map_sel") not in codes:
        st.session_state["map_sel"] = codes[0]
    names = dict(zip(latest["line_code"], latest["line_name"]))

    left, right = st.columns([3, 2], gap="large")
    with left:
        color_by = st.radio("Colour lines by", ["Loading status", "Voltage class"], horizontal=True, key="map_color")
        fig = charts.network_figure(latest, subs, th, st.session_state["map_sel"], color_by)
        event = st.plotly_chart(fig, key="map_chart", on_select="rerun", selection_mode="points",
                                width="stretch", config={"displayModeBar": False})
        _handle_click(event, codes)
        if color_by == "Loading status":
            legend = (f'<span><i style="background:{C["ok"]}"></i>Normal</span>'
                      f'<span><i style="background:{C["warn"]}"></i>&gt; {th.loading_warning:.0f}% warning</span>'
                      f'<span><i style="background:{C["high"]}"></i>&gt; {th.loading_high:.0f}% high</span>'
                      f'<span><i style="background:{C["crit"]}"></i>&gt; {th.loading_critical:.0f}% critical</span>'
                      f'<span><i style="background:{C["offline"]}"></i>Out of service (dashed)</span>')
        else:
            legend = "".join(f'<span><i style="background:{KV_COLOR[k]}"></i>{k} kV</span>' for k in (500, 230, 115))
        st.markdown(f'<div class="gw-legend">{legend}<span>Line thickness = voltage class &middot; badge = % of rated current</span></div>',
                    unsafe_allow_html=True)

    with right:
        st.selectbox("Selected equipment", codes, key="map_sel", format_func=lambda c: f"{c}  ·  {names[c]}")
        _detail(ctx, latest, th, st.session_state["map_sel"])


def _detail(ctx: AppContext, latest, th, code: str) -> None:
    row = latest[latest["line_code"] == code].iloc[0]
    online = row["equipment_status"] == "IN_SERVICE"
    section(f"{code}  ·  {int(row['nominal_kv'])} kV line")
    st.caption(f"{row['line_name']}  ·  {row['from_sub']} → {row['to_sub']}  ·  {tables.STATUS_LAMP[row['equipment_status']]}")

    def tone(p):
        return "off" if not online else ("crit" if p > th.loading_critical else "high" if p > th.loading_high
                                          else "warn" if p > th.loading_warning else "ok")
    cells = [
        ("Loading", f"{row['loading_pct']:.1f}", "%", f"{row['current_a']:.0f} A of {row['rated_current_a']:.0f} A", tone(row["loading_pct"])),
        ("Voltage", f"{row['voltage_kv']:.1f}", "kV", f"{row['voltage_pu']:.3f} pu", "off" if not online else "accent"),
        ("Active power", f"{row['p_mw']:.1f}", "MW", f"PF {row['power_factor']:.3f}", "accent"),
        ("Reactive power", f"{row['q_mvar']:.1f}", "MVAr", "magnitude", "accent"),
        ("Apparent power", f"{row['s_mva']:.1f}", "MVA", f"rating {row['rated_mva']:.0f} MVA", "accent"),
        ("Current", f"{row['current_a']:.0f}", "A", f"limit {row['rated_current_a']:.0f} A", "accent"),
    ]
    for i in range(0, len(cells), 2):
        cols = st.columns(2)
        for col, (label, value, unit, sub, t) in zip(cols, cells[i:i + 2]):
            col.markdown(kpi_html(label, value, sub, t, unit, compact=True), unsafe_allow_html=True)

    series = run_query(ctx.engine, "measurement_series", codes=[code], min_tick=int(row["tick"]) - 60)
    if not series.empty:
        st.plotly_chart(charts.mini_loading_trend(series, th), key="map_mini", width="stretch",
                        config={"displayModeBar": False})

    alarms = run_query(ctx.engine, "alarm_list", limit=500)
    mine = alarms[(alarms["line_code"] == code) & (alarms["state"] != "CLEARED")]
    if mine.empty:
        st.markdown('<div class="gw-note">No open alarms on this line.</div>', unsafe_allow_html=True)
    else:
        tables.show(tables.alarm_display(tables.sort_by_severity(mine))[["Severity", "Type", "State", "Value"]],
                    height=38 + 35 * len(mine))

    with ctx.sim.session() as s:
        g_live = build_graph(s, only_in_service=True)
    live = topology_report(g_live)
    msg = (f"Topology (NetworkX): all {live['substations']} substations are connected through in-service lines."
           if live["connected"] else
           f"Topology (NetworkX): network is split into {live['islands']} islands: " +
           " | ".join("-".join(c) for c in live["components"]) + ".")
    if online:
        n1 = n_minus_1_topology(g_live, code)
        msg += (" Losing this line would not split the network." if n1["connected"] else
                f" Losing this line would isolate: {', '.join(n1['isolated_substations'])}.")
    note(msg + " Topology screen only; not a power-flow or contingency study.")
