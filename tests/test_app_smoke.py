"""Run every dashboard page headlessly with Streamlit's AppTest and assert no exceptions."""
from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")
ROOT = Path(__file__).resolve().parent.parent
PAGES = ["overview", "network", "live", "alarms", "analytics", "control"]


def _page_script(page: str) -> str:
    return f"""
from gridwatch.ui.context import get_context
from gridwatch.ui import theme, page_{page} as m
theme.inject_css()
m.render(get_context())
"""


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIDWATCH_DB_URL", f"sqlite:///{(tmp_path / 'ui.db').as_posix()}")
    import streamlit as st
    st.cache_resource.clear()
    at = st_testing.AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    return at


def test_default_page_renders(app):
    assert not app.exception, [e.value for e in app.exception]


@pytest.mark.parametrize("page", PAGES)
def test_each_page_renders(app, page):
    # Each page's render() runs in its own AppTest script; the `app` fixture only
    # provides the isolated temporary database via GRIDWATCH_DB_URL.
    at = st_testing.AppTest.from_string(_page_script(page), default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]


@pytest.fixture()
def busy_db(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'busy.db').as_posix()}"
    monkeypatch.setenv("GRIDWATCH_DB_URL", url)
    import streamlit as st
    from gridwatch.database import create_db_engine
    from gridwatch.simulator import GridSimulator
    st.cache_resource.clear()
    eng = create_db_engine(url)
    sim = GridSimulator(eng)
    sim.initialize(backfill_ticks=40)
    sim.apply_overload("TL-06", 108)
    sim.apply_voltage("TL-07", 0.89)
    sim.trip_line("TL-02")
    sim.step(5)
    eng.dispose()
    return url


@pytest.mark.parametrize("page", PAGES)
def test_pages_render_with_alarms_outages_and_faults(busy_db, page):
    at = st_testing.AppTest.from_string(_page_script(page), default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def test_acknowledge_button_persists_to_database(busy_db):
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session
    from gridwatch.database import Alarm, create_db_engine
    at = st_testing.AppTest.from_string(_page_script("alarms"), default_timeout=60).run()
    ack_all = [b for b in at.button if b.label == "Acknowledge all active"]
    assert ack_all, "acknowledge button missing"
    ack_all[0].click().run()
    assert not at.exception
    eng = create_db_engine(busy_db)
    with Session(eng) as s:
        assert s.scalar(select(func.count(Alarm.id)).where(Alarm.state == "ACTIVE")) == 0
        assert s.scalar(select(func.count(Alarm.id)).where(Alarm.state == "ACKNOWLEDGED")) >= 3
    eng.dispose()
    at.run()            # a plain rerun must not create or change anything
    assert not at.exception
