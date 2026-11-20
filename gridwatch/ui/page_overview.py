"""Operations Overview."""
from __future__ import annotations

import streamlit as st

from gridwatch.analytics import run_query
from gridwatch.ui import charts, tables
from gridwatch.ui.context import AppContext, live_section
from gridwatch.ui.theme import empty_state, kpi_row, page_title, section, status_strip


def _tone(pct: float, th) -> str:
    return ("crit" if pct > th.loading_critical else "high" if pct > th.loading_high
            else "warn" if pct > th.loading_warning else "ok")


def render(ctx: AppContext) -> None:
    page_title("Operations overview", "System status, loading and alarm summary for the simulated transmission network.")
    live_section(ctx, _body)


def _body(ctx: AppContext) -> None:
    snap = ctx.sim.get_state()
    status_strip(snap)
    th = ctx.thresholds()
    latest = run_query(ctx.engine, "latest_measurements")
    if latest.empty:
        empty_state("No telemetry yet. Press Start in the sidebar or step the simulation from Simulation control.")
        return
    subs = ctx.substations()
    sev = run_query(ctx.engine, "active_alarms_by_severity").set_index("severity")

    def n(level: str, col: str = "alarm_count") -> int:
        return int(sev[col].get(level, 0)) if level in sev.index else 0

    warnings, critical = n("WARNING") + n("HIGH"), n("CRITICAL")
    unack_w, unack_c = n("WARNING", "unacknowledged") + n("HIGH", "unacknowledged"), n("CRITICAL", "unacknowledged")
    online = int((latest["equipment_status"] == "IN_SERVICE").sum())
    total = len(latest)
    peak = latest.loc[latest["loading_pct"].idxmax()]

    kpi_row([
        dict(label="Substations", value=len(subs), sub=f"{int((subs['max_voltage_kv'] == 500).sum())} with 500 kV bus", tone="accent"),
        dict(label="Transmission lines", value=total, sub="115 / 230 / 500 kV", tone="accent"),
        dict(label="Active warnings", value=warnings, sub=f"{unack_w} unacknowledged (warning + high)",
             tone="warn" if warnings else "ok"),
        dict(label="Critical alarms", value=critical, sub=f"{unack_c} unacknowledged",
             tone="crit" if critical else "ok", pulse=unack_c > 0),
        dict(label="Equipment availability", value=f"{100 * online / total:.0f}", unit="%",
             sub=f"{online} of {total} lines in service", tone="ok" if online == total else ("warn" if online >= total - 1 else "crit")),
        dict(label="Highest line loading", value=f"{peak['loading_pct']:.1f}", unit="%",
             sub=f"{peak['line_code']} · {int(peak['nominal_kv'])} kV", tone=_tone(peak["loading_pct"], th)),
    ])

    left, right = st.columns([3, 2])
    with left:
        section("Current line loading")
        st.plotly_chart(charts.loading_bars(latest, th), key="ov_loading", width="stretch",
                        config={"displayModeBar": False})
    with right:
        section("Active alarms")
        alarms = run_query(ctx.engine, "alarm_list", limit=500)
        open_ = tables.sort_by_severity(alarms[alarms["state"] != "CLEARED"])
        if open_.empty:
            empty_state("No active alarms. All monitored conditions are within limits.")
        else:
            disp = tables.alarm_display(open_.head(10))[["Severity", "Line", "Type", "State", "Value", "Raised (sim)"]]
            tables.show(disp, height=min(430, 38 + 35 * len(disp)), cfg=tables.ALARM_CFG)
            st.caption(f"{len(open_)} open alarms. Acknowledge them on the Alarm management page.")
        section("Operational events")
        ev = run_query(ctx.engine, "recent_events", limit=8)
        ev = ev.rename(columns={"timestamp": "Time (sim)", "event_type": "Event", "line_code": "Line", "description": "Description"})
        tables.show(ev, height=330, cfg={"Time (sim)": tables.CC.DatetimeColumn(format="HH:mm", width="small"),
                                        "Description": tables.CC.TextColumn(width="large")})

    section("Latest measurements")
    tables.show(tables.measurements_display(latest), height=462, cfg=tables.MEAS_CFG)
