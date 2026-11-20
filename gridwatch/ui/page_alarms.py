"""Alarm Management: active alarms, acknowledgement, history, thresholds, exports."""
from __future__ import annotations

from dataclasses import fields

import pandas as pd
import streamlit as st

from gridwatch import analytics
from gridwatch.alarms import (THRESHOLD_META, Thresholds, acknowledge_alarms, acknowledge_all_active,
                              save_thresholds)
from gridwatch.analytics import run_query
from gridwatch.ui import tables
from gridwatch.ui.context import AppContext, export_button, live_section, toast
from gridwatch.ui.theme import empty_state, kpi_row, page_title, section, status_strip


def render(ctx: AppContext) -> None:
    page_title("Alarm management",
               "Automatic overload, voltage and outage detection with persistent acknowledgement and history.")
    live_section(ctx, _body)
    _thresholds_panel(ctx)
    _exports_panel(ctx)


def _body(ctx: AppContext) -> None:
    status_strip(ctx.sim.get_state())
    alarms = run_query(ctx.engine, "alarm_list", limit=5000)
    open_ = alarms[alarms["state"] != "CLEARED"] if not alarms.empty else alarms

    def cnt(sev=None, state=None) -> int:
        d = open_
        if d.empty:
            return 0
        if sev:
            d = d[d["severity"] == sev]
        if state:
            d = d[d["state"] == state]
        return len(d)

    kpi_row([
        dict(label="Unacknowledged", value=cnt(state="ACTIVE"), sub="need operator attention",
             tone="crit" if cnt("CRITICAL", "ACTIVE") else "warn" if cnt(state="ACTIVE") else "ok",
             pulse=cnt("CRITICAL", "ACTIVE") > 0),
        dict(label="Acknowledged", value=cnt(state="ACKNOWLEDGED"), sub="still open", tone="accent"),
        dict(label="Critical open", value=cnt("CRITICAL"), tone="crit" if cnt("CRITICAL") else "ok"),
        dict(label="High open", value=cnt("HIGH"), tone="high" if cnt("HIGH") else "ok"),
        dict(label="Warning open", value=cnt("WARNING"), tone="warn" if cnt("WARNING") else "ok"),
        dict(label="Cleared (history)", value=0 if alarms.empty else int((alarms["state"] == "CLEARED").sum()),
             sub=f"{len(alarms)} alarms recorded", tone="off"),
    ])
    tab_active, tab_hist = st.tabs(["Active alarms", "Alarm history"])
    with tab_active:
        _active_tab(ctx, open_)
    with tab_hist:
        _history_tab(alarms)


def _ack(ctx: AppContext, mode: str) -> None:
    user = (st.session_state.get("ack_user") or "operator").strip()[:40] or "operator"
    with ctx.sim.session() as s:
        n = (acknowledge_all_active(s, user) if mode == "all"
             else acknowledge_alarms(s, [int(i) for i in st.session_state.get("ack_sel", [])], user))
    st.session_state["ack_sel"] = []
    toast(f"{n} alarm(s) acknowledged by {user}" if n else "Nothing to acknowledge", ok=bool(n))


def _active_tab(ctx: AppContext, open_: pd.DataFrame) -> None:
    if open_.empty:
        empty_state("No active alarms. All monitored conditions are within limits.")
        return
    open_ = tables.sort_by_severity(open_)
    tables.show(tables.alarm_display(open_), height=min(480, 40 + 35 * len(open_)), cfg=tables.ALARM_CFG)
    unacked = open_[open_["state"] == "ACTIVE"]
    section("Acknowledge")
    if unacked.empty:
        st.caption("Every open alarm has been acknowledged.")
        return
    labels = {int(r.alarm_id): f"#{r.alarm_id}  {r.severity.title()}  {r.alarm_type.title()}  {r.line_code}"
              for r in unacked.itertuples()}
    st.session_state["ack_sel"] = [i for i in st.session_state.get("ack_sel", []) if i in labels]
    c1, c2 = st.columns([3, 1])
    c1.multiselect("Alarms to acknowledge", list(labels), key="ack_sel", format_func=labels.get)
    c2.text_input("Acknowledged by", value="operator", key="ack_user", max_chars=40)
    b1, b2, _ = st.columns([1, 1, 2])
    b1.button("Acknowledge selected", on_click=_ack, args=(ctx, "sel"), type="primary",
              disabled=not st.session_state["ack_sel"], width="stretch")
    b2.button("Acknowledge all active", on_click=_ack, args=(ctx, "all"), width="stretch")
    st.caption("Acknowledgement is stored in the database. If an alarm later escalates to a higher severity, "
               "it returns to Active and must be acknowledged again.")


def _history_tab(alarms: pd.DataFrame) -> None:
    if alarms.empty:
        empty_state("No alarms have been recorded yet.")
        return
    c1, c2, c3, c4 = st.columns(4)
    states = c1.multiselect("State", list(tables.STATE_LABEL.values()), key="h_state")
    sevs = c2.multiselect("Worst severity", ["Critical", "High", "Warning"], key="h_sev")
    types = c3.multiselect("Type", sorted(alarms["alarm_type"].str.title().unique()), key="h_type")
    lines = c4.multiselect("Line", sorted(alarms["line_code"].unique()), key="h_line")
    df = alarms
    if states:
        df = df[df["state"].map(tables.STATE_LABEL).isin(states)]
    if sevs:
        df = df[df["peak_severity"].str.title().isin(sevs)]
    if types:
        df = df[df["alarm_type"].str.title().isin(types)]
    if lines:
        df = df[df["line_code"].isin(lines)]
    st.caption(f"{len(df)} of {len(alarms)} alarms (simulation time stamps)")
    tables.show(tables.alarm_display(df, history=True), height=480, cfg=tables.ALARM_CFG)


def _thresholds_panel(ctx: AppContext) -> None:
    th = ctx.thresholds()
    with st.expander("Alarm thresholds (stored in the database)"):
        with st.form("th_form"):
            vals: dict[str, float] = {}
            cols = st.columns(4)
            for i, f in enumerate(fields(Thresholds)):
                unit, desc = THRESHOLD_META[f.name]
                is_pu = unit == "pu"
                vals[f.name] = cols[i % 4].number_input(
                    f"{desc} ({unit})", value=float(getattr(th, f.name)), step=0.005 if is_pu else 1.0,
                    format="%.3f" if is_pu else "%.1f", key=f"th_{f.name}")
            saved = st.form_submit_button("Save thresholds", type="primary")
        if saved:
            try:
                with ctx.sim.session() as s:
                    save_thresholds(s, Thresholds(**vals))
                toast("Thresholds saved. They apply from the next simulation update.")
                st.rerun()
            except ValueError as e:
                st.error(str(e))
        if st.button("Restore default thresholds"):
            with ctx.sim.session() as s:
                save_thresholds(s, Thresholds())
            for f in fields(Thresholds):
                st.session_state.pop(f"th_{f.name}", None)
            st.rerun()


def _exports_panel(ctx: AppContext) -> None:
    with st.expander("Export CSV"):
        c1, c2, c3 = st.columns(3)
        with c1:
            export_button("alarm register", lambda: analytics.alarms_csv(ctx.engine), "gridwatch_alarms.csv", "alarms")
        with c2:
            export_button("telemetry (all lines)", lambda: analytics.measurements_csv(ctx.engine),
                          "gridwatch_measurements.csv", "meas")
        with c3:
            export_button("event log", lambda: analytics.events_csv(ctx.engine), "gridwatch_events.csv", "events")
