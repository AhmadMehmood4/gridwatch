"""Shared application context, live-refresh helper and export helper."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd
import streamlit as st
from sqlalchemy.engine import Engine

from gridwatch import analytics
from gridwatch.alarms import Thresholds, load_thresholds
from gridwatch.database import create_db_engine
from gridwatch.simulator import GridSimulator, SimSnapshot


@dataclass
class AppContext:
    engine: Engine
    sim: GridSimulator

    def thresholds(self) -> Thresholds:
        with self.sim.session() as s:
            return load_thresholds(s)

    def substations(self) -> pd.DataFrame:
        return analytics.run_query(self.engine, "substations")

    def sim_state(self) -> SimSnapshot:
        return self.sim.get_state()


@st.cache_resource(show_spinner="Initialising simulation database...")
def get_context() -> AppContext:
    """One engine + simulator per server process; initialisation is idempotent."""
    engine = create_db_engine()
    sim = GridSimulator(engine)
    sim.initialize()
    return AppContext(engine=engine, sim=sim)


def live_section(ctx: AppContext, body: Callable[[AppContext], None]) -> None:
    """Render `body` in a fragment that auto-refreshes while the simulation is running.

    Each refresh calls step_if_due(): an atomic, time-gated claim in the database, so
    normal page reruns (clicks, filters) never add extra ticks or duplicate records.
    """
    snap = ctx.sim.get_state()
    run_every = snap.refresh_interval_s if snap.running else None

    @st.fragment(run_every=run_every)
    def _live() -> None:
        ctx.sim.step_if_due()
        body(ctx)

    _live()


def export_button(label: str, builder: Callable[[], str], filename: str, key: str) -> None:
    """Two-step export: build the CSV only when asked, then offer the download."""
    if st.button(f"Prepare {label}", key=f"prep_{key}", width="stretch"):
        st.session_state[f"csv_{key}"] = builder()
    data = st.session_state.get(f"csv_{key}")
    if data is not None:
        st.download_button(f"Download {filename}", data=data, file_name=filename, mime="text/csv",
                           key=f"dl_{key}", width="stretch")


def toast(message: str, ok: bool = True) -> None:
    st.toast(message, icon=":material/check_circle:" if ok else ":material/warning:")
