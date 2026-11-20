"""Live Monitoring: filters, configurable interval, interactive trends."""
from __future__ import annotations

import streamlit as st

from gridwatch import config
from gridwatch.analytics import run_query
from gridwatch.ui import charts, tables
from gridwatch.ui.context import AppContext, live_section
from gridwatch.ui.theme import C, empty_state, page_title, section, status_strip

DEFAULT_LINES = ["TL-02", "TL-06", "TL-09"]


def _set_refresh(ctx: AppContext) -> None:
    ctx.sim.set_refresh_interval(float(st.session_state["live_refresh"]))


def _set_step(ctx: AppContext) -> None:
    ctx.sim.set_step_minutes(int(st.session_state["live_step"]))


def render(ctx: AppContext) -> None:
    page_title("Live monitoring",
               "Synthetic real-time telemetry. Filter equipment, choose how often the dashboard refreshes, "
               "and how much simulated time each update advances.")
    snap = ctx.sim.get_state()
    all_lines = run_query(ctx.engine, "latest_measurements")
    if all_lines.empty:
        empty_state("No telemetry yet. Start or step the simulation.")
        return

    # Controls live outside the auto-refreshing fragment: changing them re-creates the timer.
    st.session_state["live_refresh"] = snap.refresh_interval_s
    st.session_state["live_step"] = snap.step_minutes
    st.session_state.setdefault("live_kv", [500, 230, 115])
    c1, c2, c3, c4, c5 = st.columns([1.5, 2.8, 1.3, 1.1, 1.2])
    kv = c1.multiselect("Voltage class (kV)", [500, 230, 115], key="live_kv")
    options = list(all_lines[all_lines["nominal_kv"].isin(kv)]["line_code"])
    cur = [c for c in st.session_state.get("live_lines", DEFAULT_LINES) if c in options]
    st.session_state["live_lines"] = cur
    c2.multiselect("Equipment", options, key="live_lines")
    c3.selectbox("History window", [30, 60, 120, 288], key="live_window", index=1,
                 format_func=lambda n: f"Last {n} samples")
    refresh_opts = sorted(set(config.REFRESH_CHOICES_S) | {snap.refresh_interval_s})
    c4.selectbox("Refresh every", refresh_opts, key="live_refresh", format_func=lambda s: f"{s:g} s",
                 on_change=_set_refresh, args=(ctx,))
    step_opts = sorted(set(config.STEP_MINUTE_CHOICES) | {snap.step_minutes})
    c5.selectbox("Simulated step", step_opts, key="live_step", format_func=lambda m: f"{m} min / update",
                 on_change=_set_step, args=(ctx,))
    live_section(ctx, _body)


def _body(ctx: AppContext) -> None:
    snap = ctx.sim.get_state()
    status_strip(snap)
    th = ctx.thresholds()
    codes = st.session_state.get("live_lines") or []
    if not codes:
        empty_state("Select at least one line to display telemetry.")
        return
    window = int(st.session_state.get("live_window", 60))
    df = run_query(ctx.engine, "measurement_series", codes=codes, min_tick=snap.tick - window)
    if df.empty:
        empty_state("No measurements stored for the selected equipment yet.")
        return

    latest = run_query(ctx.engine, "latest_measurements")
    latest = latest[latest["line_code"].isin(codes)]
    section("Selected equipment, latest values")
    tables.show(tables.measurements_display(latest), height=38 + 35 * len(latest) + 4, cfg=tables.MEAS_CFG)

    g1, g2 = st.columns(2)
    cfg = {"displayModeBar": False}
    with g1:
        st.plotly_chart(charts.trend_chart(
            df, "voltage_pu", "Voltage (per unit)", "pu", band=(th.v_low_warning, th.v_high_warning),
            hlines=[(th.v_low_warning, C["warn"], "under"), (th.v_high_warning, C["warn"], "over")]),
            key="lv_v", width="stretch", config=cfg)
        st.plotly_chart(charts.trend_chart(df, "p_mw", "Active power (solid, MW) and reactive power (dotted, MVAr)",
                                           "MW / MVAr", extra="q_mvar"), key="lv_p", width="stretch", config=cfg)
    with g2:
        st.plotly_chart(charts.trend_chart(df, "current_a", "Line current", "A"), key="lv_i", width="stretch", config=cfg)
        st.plotly_chart(charts.trend_chart(
            df, "loading_pct", "Loading (% of rated current)", "%",
            hlines=[(th.loading_warning, C["warn"], "warning"), (th.loading_high, C["high"], "high"),
                    (th.loading_critical, C["crit"], "critical")]), key="lv_l", width="stretch", config=cfg)
    st.caption("Hover any chart for values at a timestamp; drag to zoom, double-click to reset. "
               "Timestamps are simulation time. Offline lines read zero and are omitted from the voltage chart.")
