import io
from datetime import timedelta

import pandas as pd
import pytest

from gridwatch import analytics as an
from gridwatch.alarms import Thresholds, save_thresholds


@pytest.fixture()
def busy(sim):
    sim.apply_overload("TL-06", 108)
    sim.apply_overload("TL-09", 92)
    sim.apply_voltage("TL-07", 0.90)
    sim.trip_line("TL-02")
    sim.step(6)
    return sim


def test_every_query_executes_on_empty_and_busy_db(engine, sim, busy):
    for key in an.QUERIES:
        kw = {"codes": ["TL-01"]} if key == "measurement_series" else {}
        assert isinstance(an.run_query(engine, key, **kw), pd.DataFrame)


def test_lines_over_90_percent_matches_manual_calc(engine, busy):
    df = an.run_query(engine, "lines_over_loading_now", threshold_pct=90)
    assert set(df["line_code"]) == {"TL-06", "TL-09"}
    assert (df["loading_pct"] > 90).all()


def test_loading_stats_aggregates(engine, busy):
    df = an.run_query(engine, "loading_by_line").set_index("line_code")
    assert df.loc["TL-06", "max_loading_pct"] > 100
    assert df.loc["TL-02", "availability_pct"] < 100          # tripped
    assert (df["max_loading_pct"] >= df["avg_loading_pct"].fillna(0)).all()


def test_active_alarms_by_severity_counts_open_only(engine, busy):
    df = an.run_query(engine, "active_alarms_by_severity")
    alarms = an.run_query(engine, "alarm_list")
    open_ = alarms[alarms["state"] != "CLEARED"]
    assert int(df["alarm_count"].sum()) == len(open_)
    assert list(df["severity"]) == sorted(df["severity"], key=["CRITICAL", "HIGH", "WARNING"].index)


def test_equipment_alarm_ranking_sorted_desc(engine, busy):
    df = an.run_query(engine, "equipment_alarm_counts")
    assert list(df["total_alarms"]) == sorted(df["total_alarms"], reverse=True)
    assert len(df) == 12                                       # LEFT JOIN keeps zero-alarm lines


def test_voltage_abnormalities_respect_thresholds(engine, busy):
    df = an.run_query(engine, "voltage_abnormalities", v_low=0.95, v_high=1.05)
    assert "TL-07" in set(df["line_code"])
    assert ((df["voltage_pu"] < 0.95) | (df["voltage_pu"] > 1.05)).all()
    none = an.run_query(engine, "voltage_abnormalities", v_low=0.5, v_high=1.5)
    assert none.empty


def test_offline_equipment_report(engine, busy):
    df = an.run_query(engine, "offline_equipment")
    assert list(df["line_code"]) == ["TL-02"] and pd.notna(df["outage_since"].iloc[0])
    busy.restore_line("TL-02")
    assert an.run_query(engine, "offline_equipment").empty


def test_substation_summary_having_and_join(engine, busy):
    df = an.run_query(engine, "substation_summary")
    assert len(df) == 8 and df["connected_lines"].sum() == 24  # each line touches two substations


def test_window_filter_reduces_rows(engine, busy):
    full = an.run_query(engine, "loading_by_line")["samples"].sum()
    since = busy.get_state().sim_time - timedelta(hours=1)
    recent = an.run_query(engine, "loading_by_line", since=since)["samples"].sum()
    assert 0 < recent < full


def test_csv_exports_round_trip(engine, busy):
    m = pd.read_csv(io.StringIO(an.measurements_csv(engine, ["TL-06"])))
    assert set(m["line_code"]) == {"TL-06"} and (m["data_source"] == "SYNTHETIC").all()
    a = pd.read_csv(io.StringIO(an.alarms_csv(engine)))
    assert len(a) == len(an.run_query(engine, "alarm_list"))
    assert "alarm_id" in a.columns
    assert len(pd.read_csv(io.StringIO(an.events_csv(engine)))) > 0
    all_lines = pd.read_csv(io.StringIO(an.measurements_csv(engine)))
    assert all_lines["line_code"].nunique() == 12


def test_analytics_follow_threshold_changes(engine, sim):
    with sim.session() as s:
        save_thresholds(s, Thresholds(loading_warning=30, loading_high=40, loading_critical=50))
    sim.step(2)
    assert an.run_query(engine, "active_alarms_by_severity")["alarm_count"].sum() > 0
