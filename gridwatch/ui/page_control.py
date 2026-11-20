"""Simulation Control: run state, scenarios, fault injection, live observation."""
from __future__ import annotations

import streamlit as st

from gridwatch import config
from gridwatch.analytics import run_query
from gridwatch.simulator import SCENARIOS
from gridwatch.ui import charts, tables
from gridwatch.ui.context import AppContext, live_section, toast
from gridwatch.ui.theme import empty_state, kpi_row, page_title, section, status_strip


def _do(label: str, fn, *args, step_after: bool = False, ctx: AppContext | None = None) -> None:
    """Run an engine action; show a toast. Optionally advance one tick so the effect is visible."""
    try:
        fn(*args)
        if step_after and ctx is not None:
            ctx.sim.step(1)
        toast(label)
    except ValueError as e:
        toast(str(e), ok=False)


def _reset(ctx: AppContext) -> None:
    ctx.sim.reset(seed=int(st.session_state["rs_seed"]), backfill_ticks=int(st.session_state["rs_backfill"]))
    st.session_state["rs_confirm"] = False
    toast("Simulation reset")


def render(ctx: AppContext) -> None:
    page_title("Simulation control",
               "Start, pause and reset the simulation, replay scenarios, and inject faults. "
               "Changes flow through the database to every other page.")
    snap = ctx.sim.get_state()
    codes = list(run_query(ctx.engine, "latest_measurements")["line_code"])

    a, b, c = st.columns([1.2, 1.3, 1.1], gap="large")
    with a:
        section("Run control")
        r1, r2 = st.columns(2)
        r1.button("Start", on_click=_do, args=("Simulation started", ctx.sim.start), type="primary",
                  disabled=snap.running, width="stretch")
        r2.button("Pause", on_click=_do, args=("Simulation paused", ctx.sim.pause), disabled=not snap.running, width="stretch")
        s1, s2 = st.columns(2)
        s1.button("Step once", on_click=_do, args=("Advanced one update", ctx.sim.step), width="stretch")
        n = s2.number_input("Advance by (updates)", 1, 288, 12, key="adv_n", label_visibility="collapsed")
        st.button(f"Advance {n} updates", on_click=_do, args=(f"Advanced {n} updates", ctx.sim.step, int(n)), width="stretch")
        st.caption("Refresh interval and step size are set on the Live monitoring page.")
    with b:
        section("Reproducible scenarios")
        key = st.selectbox("Scenario", list(SCENARIOS), format_func=lambda k: SCENARIOS[k].title, key="sc_key")
        st.caption(SCENARIOS[key].description)
        st.button("Run scenario", on_click=_do, args=(f"Scenario complete: {SCENARIOS[key].title}", ctx.sim.run_scenario, key),
                  width="stretch")
        st.caption("Replaces current history with a seeded run; identical every time.")
    with c:
        section("Reset")
        st.number_input("Random seed", 0, 2**31 - 1, snap.seed, key="rs_seed")
        st.selectbox("History to pre-generate", [0, 48, 120, config.DEFAULT_BACKFILL_TICKS], index=3, key="rs_backfill",
                     format_func=lambda t: f"{t} updates ({t * config.DEFAULT_STEP_MINUTES / 60:g} h)")
        ok = st.checkbox("I understand this deletes all telemetry, alarms and events", key="rs_confirm")
        st.button("Reset simulation", on_click=_reset, args=(ctx,), disabled=not ok, width="stretch")

    section("Fault injection")
    sel = st.selectbox("Target line", codes, key="fi_line")
    f1, f2, f3 = st.columns(3, gap="large")
    with f1:
        st.markdown("**Line overload**")
        pct = st.slider("Target loading (%)", 60, 150, 105, key="fi_pct")
        st.button("Apply overload", on_click=_do, args=(f"Overload applied to {sel}", ctx.sim.apply_overload, sel, float(pct)),
                  kwargs={"step_after": True, "ctx": ctx}, width="stretch")
    with f2:
        st.markdown("**Voltage abnormality**")
        pu = st.slider("Target voltage (pu)", 0.80, 1.15, 0.90, 0.005, key="fi_pu", format="%.3f")
        st.button("Apply voltage abnormality", on_click=_do, args=(f"Voltage abnormality applied to {sel}", ctx.sim.apply_voltage, sel, float(pu)),
                  kwargs={"step_after": True, "ctx": ctx}, width="stretch")
    with f3:
        st.markdown("**Equipment outage**")
        st.caption("A tripped line reads zero and raises an outage alarm.")
        o1, o2 = st.columns(2)
        o1.button("Trip line", on_click=_do, args=(f"{sel} tripped", ctx.sim.trip_line, sel),
                  kwargs={"step_after": True, "ctx": ctx}, width="stretch")
        o2.button("Restore line", on_click=_do, args=(f"{sel} restored", ctx.sim.restore_line, sel),
                  kwargs={"step_after": True, "ctx": ctx}, width="stretch")
    k1, k2, _ = st.columns([1, 1, 2])
    k1.button("Clear injected conditions (this line)", on_click=_do,
              args=(f"Injected conditions cleared on {sel}", ctx.sim.clear_override, sel), kwargs={"step_after": True, "ctx": ctx}, width="stretch")
    k2.button("Clear all injected conditions", on_click=_do, args=("All injected conditions cleared", ctx.sim.clear_all_overrides),
              kwargs={"step_after": True, "ctx": ctx}, width="stretch")
    st.caption("Each injection advances the simulation by one update so the effect appears immediately, even while paused.")
    live_section(ctx, _observe)


def _observe(ctx: AppContext) -> None:
    section("Observed effect")
    status_strip(ctx.sim.get_state())
    th = ctx.thresholds()
    latest = run_query(ctx.engine, "latest_measurements")
    if latest.empty:
        empty_state("No telemetry yet.")
        return
    sev = run_query(ctx.engine, "active_alarms_by_severity").set_index("severity")
    over = run_query(ctx.engine, "active_overrides")
    off = run_query(ctx.engine, "offline_equipment")
    cnt = lambda s: int(sev["alarm_count"].get(s, 0)) if s in sev.index else 0  # noqa: E731
    kpi_row([
        dict(label="Critical alarms", value=cnt("CRITICAL"), tone="crit" if cnt("CRITICAL") else "ok"),
        dict(label="High alarms", value=cnt("HIGH"), tone="high" if cnt("HIGH") else "ok"),
        dict(label="Warnings", value=cnt("WARNING"), tone="warn" if cnt("WARNING") else "ok"),
        dict(label="Lines out of service", value=len(off), tone="crit" if len(off) else "ok"),
        dict(label="Injected conditions", value=len(over), tone="accent"),
    ])
    left, right = st.columns([3, 2])
    left.plotly_chart(charts.loading_bars(latest, th, 380), key="ctl_bars", width="stretch", config={"displayModeBar": False})
    with right:
        st.markdown("**Injected conditions**")
        if over.empty:
            st.caption("None applied.")
        else:
            tables.show(over, cfg={"applied_at": tables.CC.DatetimeColumn(format="MMM DD HH:mm")})
        st.markdown("**Offline equipment (SQL)**")
        if off.empty:
            st.caption("All lines in service.")
        else:
            tables.show(off[["line_code", "nominal_kv", "outage_since"]])
        st.markdown("**Recent events**")
        ev = run_query(ctx.engine, "recent_events", limit=10)
        tables.show(ev[["timestamp", "event_type", "line_code"]], height=300,
                    cfg={"timestamp": tables.CC.DatetimeColumn(format="HH:mm")})
