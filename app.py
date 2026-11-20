"""GridWatch - Transmission Grid Monitoring & Alert System.

Run with:  streamlit run app.py
"""
import streamlit as st

st.set_page_config(page_title="GridWatch", page_icon=":material/bolt:", layout="wide",
                   initial_sidebar_state="expanded")

from gridwatch import config  # noqa: E402
from gridwatch.ui import (page_alarms, page_analytics, page_control, page_live,  # noqa: E402
                          page_network, page_overview, theme)
from gridwatch.ui.context import get_context  # noqa: E402

theme.inject_css()
ctx = get_context()


def _toggle_run() -> None:
    ctx.sim.pause() if ctx.sim.get_state().running else ctx.sim.start()


def _step_once() -> None:
    ctx.sim.step(1)


def sidebar() -> None:
    snap = ctx.sim.get_state()
    with st.sidebar:
        st.markdown('<div class="gw-brand">Grid<span>Watch</span></div>'
                    '<div class="gw-brand-sub">Transmission monitoring and alerts</div>', unsafe_allow_html=True)
        st.markdown("&nbsp;", unsafe_allow_html=True)
        st.button("Pause simulation" if snap.running else "Start simulation", on_click=_toggle_run,
                  type="secondary" if snap.running else "primary", width="stretch",
                  icon=":material/pause:" if snap.running else ":material/play_arrow:")
        st.button("Step one update", on_click=_step_once, width="stretch", icon=":material/skip_next:")
        theme.status_strip(snap)
        st.markdown(f'<div class="gw-note">{theme.esc(config.DISCLAIMER)}<br><br>'
                    'Fictional Ontario-inspired network. Names and layout are illustrative.</div>', unsafe_allow_html=True)


def _pg(module, title, path, icon, default=False):
    return st.Page(lambda: module.render(ctx), title=title, url_path=path, icon=icon, default=default)


sidebar()
nav = st.navigation([
    _pg(page_overview, "Operations overview", "overview", ":material/dashboard:", True),
    _pg(page_network, "Network map", "network", ":material/hub:"),
    _pg(page_live, "Live monitoring", "live", ":material/monitoring:"),
    _pg(page_alarms, "Alarm management", "alarms", ":material/notifications_active:"),
    _pg(page_analytics, "Historical analytics", "analytics", ":material/analytics:"),
    _pg(page_control, "Simulation control", "control", ":material/tune:"),
], position="top")
nav.run()
