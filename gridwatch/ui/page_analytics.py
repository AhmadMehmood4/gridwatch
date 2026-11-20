"""Historical Analytics: every number on this page is the result of a SQL query."""
from __future__ import annotations

from datetime import timedelta

import streamlit as st

from gridwatch.analytics import QUERIES, run_query
from gridwatch.ui import charts, tables
from gridwatch.ui.context import AppContext
from gridwatch.ui.theme import empty_state, kpi_row, note, page_title, section, status_strip

WINDOWS = {"All history": None, "Last 2 hours": 2, "Last 6 hours": 6, "Last 12 hours": 12, "Last 24 hours": 24}
CFG = tables.CC


def render(ctx: AppContext) -> None:
    page_title("Historical analytics",
               "Loading, utilisation, voltage and alarm statistics computed by SQL over the stored telemetry.")
    snap = ctx.sim.get_state()
    status_strip(snap)
    c1, c2, c3 = st.columns([1.4, 1.4, 3])
    win = c1.selectbox("Time window (simulation time)", list(WINDOWS), key="an_win")
    thr = c2.slider("Loading threshold (%)", 50, 120, 90, key="an_thr")
    c3.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    c3.button("Refresh data", help="Analytics are computed when the page renders; refresh after the simulation advances.")
    hours = WINDOWS[win]
    since = snap.sim_time - timedelta(hours=hours) if hours else None
    p = {"since": since} if since else {}
    th = ctx.thresholds()
    stats = run_query(ctx.engine, "loading_by_line", **p)
    if stats.empty or stats["samples"].sum() == 0:
        empty_state("No telemetry in this window.")
        return

    over = run_query(ctx.engine, "lines_over_loading_history", threshold_pct=thr, **p)
    alarms_f = run_query(ctx.engine, "alarm_frequency", **p)
    peak = stats.iloc[0]
    kpi_row([
        dict(label="Peak loading", value=f"{peak['max_loading_pct']:.1f}", unit="%", sub=str(peak["line_code"]),
             tone="crit" if peak["max_loading_pct"] > th.loading_critical else "warn" if peak["max_loading_pct"] > th.loading_warning else "ok"),
        dict(label="Average loading (fleet)", value=f"{stats['avg_loading_pct'].mean():.1f}", unit="%", sub="mean of line averages"),
        dict(label=f"Lines above {thr}%", value=len(over), sub="at least once in window", tone="warn" if len(over) else "ok"),
        dict(label="Alarms raised", value=int(alarms_f["alarm_count"].sum()) if not alarms_f.empty else 0, sub="in window"),
        dict(label="Samples analysed", value=f"{int(stats['samples'].sum()):,}", sub="rows read by SQL", tone="off"),
    ])

    t_load, t_util, t_volt, t_alarm, t_sql = st.tabs(
        ["Loading trends", "Utilisation", "Voltage deviations", "Alarm frequency", "SQL reports"])
    cfg = {"displayModeBar": False}

    with t_load:
        hourly = run_query(ctx.engine, "loading_trend_hourly", **p)
        if not hourly.empty:
            st.plotly_chart(charts.loading_heatmap(hourly, th), key="an_heat", width="stretch", config=cfg)
        a, b = st.columns([3, 2])
        a.plotly_chart(charts.loading_stats_chart(stats, th), key="an_stats", width="stretch", config=cfg)
        with b:
            section("Peak and average loading")
            tables.show(stats[["line_code", "avg_loading_pct", "max_loading_pct", "availability_pct"]].rename(columns={
                "line_code": "Line", "avg_loading_pct": "Avg %", "max_loading_pct": "Peak %", "availability_pct": "Available %"}),
                height=420)
        section(f"Lines that exceeded {thr}% loading")
        if over.empty:
            st.caption("No line exceeded the threshold in this window.")
        else:
            tables.show(over, cfg={"first_seen": CFG.DatetimeColumn(format="MMM DD HH:mm"),
                                   "last_seen": CFG.DatetimeColumn(format="MMM DD HH:mm")})

    with t_util:
        util = run_query(ctx.engine, "utilization", **p)
        if not util.empty:
            st.plotly_chart(charts.utilization_chart(util), key="an_util", width="stretch", config=cfg)
            tables.show(util)
        subsum = run_query(ctx.engine, "substation_summary")
        section("Substation connectivity")
        tables.show(subsum)

    with t_volt:
        dev = run_query(ctx.engine, "voltage_deviation", **p)
        if not dev.empty:
            st.plotly_chart(charts.voltage_range_chart(dev, th), key="an_vr", width="stretch", config=cfg)
        section("Samples outside the voltage warning band")
        ab = run_query(ctx.engine, "voltage_abnormalities", v_low=th.v_low_warning, v_high=th.v_high_warning, limit=500, **p)
        if ab.empty:
            st.caption("No voltage abnormalities in this window.")
        else:
            st.caption(f"{len(ab)} most recent abnormal samples (limit 500).")
            tables.show(ab, height=300, cfg={"timestamp": CFG.DatetimeColumn(format="MMM DD HH:mm")})
        tables.show(dev, height=300)

    with t_alarm:
        by_hour = run_query(ctx.engine, "alarms_by_hour", **p)
        counts = run_query(ctx.engine, "equipment_alarm_counts", **p)
        if alarms_f.empty:
            empty_state("No alarms in this window.")
        else:
            a, b = st.columns(2)
            a.plotly_chart(charts.alarm_frequency_chart(alarms_f), key="an_af", width="stretch", config=cfg)
            b.plotly_chart(charts.equipment_alarm_chart(counts), key="an_ec", width="stretch", config=cfg)
            st.plotly_chart(charts.alarms_by_hour_chart(by_hour), key="an_ah", width="stretch", config=cfg)
            tables.show(alarms_f)

    with t_sql:
        note("Each report below is a parameterised SQL statement run against the SQLite database through SQLAlchemy. "
             "The query text is shown with its result.")
        key = st.selectbox("Report", list(QUERIES), format_func=lambda k: QUERIES[k].title, key="an_report")
        q = QUERIES[key]
        st.caption(q.description)
        params = {}
        if "threshold_pct" in q.defaults:
            params["threshold_pct"] = thr
        if "since" in q.defaults and since:
            params["since"] = since
        if key == "voltage_abnormalities":
            params.update(v_low=th.v_low_warning, v_high=th.v_high_warning)
        if key == "measurement_series":
            params["codes"] = [stats["line_code"].iloc[0]]
            params["min_tick"] = snap.tick - 50
        df = run_query(ctx.engine, key, **params)
        st.code(q.sql.strip(), language="sql")
        st.caption(f"{len(df)} rows")
        tables.show(df, height=360)
        st.download_button("Download result as CSV", df.to_csv(index=False), f"gridwatch_{key}.csv", "text/csv", key=f"dl_{key}")
