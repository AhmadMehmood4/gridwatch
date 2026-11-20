import pytest
from sqlalchemy import func, select

from gridwatch.database import Alarm, Measurement, SimulationEvent, SimulationOverride, TransmissionLine
from gridwatch.simulator import SCENARIOS, GridSimulator


def rows(sim, stmt):
    with sim.session() as s:
        return list(s.execute(stmt))


def test_step_advances_clock_and_adds_rows(sim):
    st0 = sim.get_state()
    sim.step(3)
    st1 = sim.get_state()
    assert st1.tick == st0.tick + 3
    assert (st1.sim_time - st0.sim_time).total_seconds() == 3 * st0.step_minutes * 60
    with sim.session() as s:
        assert s.scalar(select(func.count(Measurement.id))) == st1.tick * 12


def test_same_seed_gives_identical_history(tmp_path):
    from gridwatch.database import create_db_engine
    data = []
    for name in ("a", "b"):
        eng = create_db_engine(f"sqlite:///{(tmp_path / (name + '.db')).as_posix()}")
        s = GridSimulator(eng)
        s.initialize(seed=99, backfill_ticks=30)
        with s.session() as ses:
            data.append(list(ses.execute(select(Measurement.line_id, Measurement.tick, Measurement.current_a,
                                                Measurement.voltage_kv).order_by(Measurement.id))))
        eng.dispose()
    assert data[0] == data[1]


def test_step_if_due_respects_state_and_interval(sim):
    assert sim.step_if_due(now=1000.0) is False                # paused
    sim.start()
    t0 = sim.get_state().tick
    assert sim.step_if_due(now=1000.0) is True
    assert sim.step_if_due(now=1000.1) is False                # same rerun burst -> no duplicate tick
    assert sim.step_if_due(now=1000.5) is False
    assert sim.step_if_due(now=1002.0) is True
    assert sim.get_state().tick == t0 + 2
    sim.pause()
    assert sim.step_if_due(now=2000.0) is False


def test_overload_injection_raises_alarm_then_clears(sim):
    sim.apply_overload("TL-06", 108)
    sim.step(2)
    a = rows(sim, select(Alarm.alarm_type, Alarm.severity, Alarm.state).where(Alarm.alarm_type == "OVERLOAD"))
    assert ("OVERLOAD", "CRITICAL", "ACTIVE") in a
    sim.clear_override("TL-06", "OVERLOAD")
    sim.step(3)
    with sim.session() as s:
        open_overload = s.scalar(select(func.count(Alarm.id)).where(Alarm.alarm_type == "OVERLOAD", Alarm.state != "CLEARED"))
    assert open_overload == 0


def test_voltage_injection(sim):
    sim.apply_voltage("TL-07", 0.89)
    sim.step(1)
    a = rows(sim, select(Alarm.alarm_type, Alarm.severity).where(Alarm.state != "CLEARED"))
    assert ("UNDERVOLTAGE", "CRITICAL") in a
    sim.apply_voltage("TL-03", 1.09)
    sim.step(1)
    a = rows(sim, select(Alarm.alarm_type).where(Alarm.state != "CLEARED"))
    assert ("OVERVOLTAGE",) in a


def test_trip_and_restore_line(sim):
    sim.trip_line("TL-02")
    sim.step(1)
    with sim.session() as s:
        ln = s.scalar(select(TransmissionLine).where(TransmissionLine.code == "TL-02"))
        last = s.scalars(select(Measurement).where(Measurement.line_id == ln.id).order_by(Measurement.tick.desc())).first()
        assert ln.status == "OUT_OF_SERVICE" and last.status == "OUT_OF_SERVICE" and last.current_a == 0
        outage = s.scalars(select(Alarm).where(Alarm.alarm_type == "OUTAGE", Alarm.state != "CLEARED")).all()
        assert len(outage) == 1 and outage[0].severity == "CRITICAL"    # 500 kV
    sim.restore_line("TL-02")
    sim.step(1)
    with sim.session() as s:
        assert s.scalar(select(func.count(Alarm.id)).where(Alarm.alarm_type == "OUTAGE", Alarm.state != "CLEARED")) == 0
        types = set(s.scalars(select(SimulationEvent.event_type)))
    assert {"LINE_TRIPPED", "LINE_RESTORED", "ALARM_RAISED", "ALARM_CLEARED"} <= types


def test_invalid_operations_raise(sim):
    with pytest.raises(ValueError):
        sim.apply_overload("NOPE", 100)
    with pytest.raises(ValueError):
        sim.apply_overload("TL-01", 5)
    with pytest.raises(ValueError):
        sim.apply_voltage("TL-01", 2.0)
    sim.trip_line("TL-01")
    with pytest.raises(ValueError):
        sim.trip_line("TL-01")
    with pytest.raises(ValueError):
        sim.apply_overload("TL-01", 100)       # cannot inject on an offline line
    sim.restore_line("TL-01")
    with pytest.raises(ValueError):
        sim.restore_line("TL-01")


def test_tripping_line_removes_its_overrides(sim):
    sim.apply_overload("TL-05", 105)
    sim.trip_line("TL-05")
    with sim.session() as s:
        assert s.scalar(select(func.count(SimulationOverride.id))) == 0


def test_reset_restores_clean_state(sim):
    sim.apply_overload("TL-06", 110)
    sim.trip_line("TL-02")
    sim.step(5)
    sim.reset(seed=7, backfill_ticks=10)
    st = sim.get_state()
    assert st.tick == 10 and st.seed == 7 and st.status == "PAUSED"
    with sim.session() as s:
        assert s.scalar(select(func.count(Measurement.id))) == 120
        assert s.scalar(select(func.count(SimulationOverride.id))) == 0
        assert not s.scalar(select(func.count(TransmissionLine.id)).where(TransmissionLine.status != "IN_SERVICE"))
        assert s.scalar(select(func.count(Alarm.id))) == 0


@pytest.mark.parametrize("key", list(SCENARIOS))
def test_scenarios_run_and_are_reproducible(sim, key):
    def snapshot():
        sim.run_scenario(key)
        with sim.session() as s:
            alarms = list(s.execute(select(Alarm.line_id, Alarm.alarm_type, Alarm.peak_severity, Alarm.raised_at).order_by(Alarm.id)))
            meas = s.scalar(select(func.sum(Measurement.current_a)))
        return alarms, meas
    a1, m1 = snapshot()
    a2, m2 = snapshot()
    assert a1 == a2 and m1 == pytest.approx(m2)
    if key == "line_outage_restoration":
        assert {a[1] for a in a1} >= {"OUTAGE"}
    if key == "evening_peak_overload":
        assert any(a[1] == "OVERLOAD" and a[2] == "CRITICAL" for a in a1)
    if key == "voltage_excursion":
        assert {"UNDERVOLTAGE", "OVERVOLTAGE"} <= {a[1] for a in a1}


def test_bad_sample_is_rejected_and_logged(sim, monkeypatch):
    import gridwatch.simulator as simmod
    monkeypatch.setattr(simmod, "validate_measurement", lambda rec, p: ["forced failure"] if rec.line_id == 1 else [])
    before = rows(sim, select(func.count(Measurement.id)))[0][0]
    sim.step(1)
    after = rows(sim, select(func.count(Measurement.id)))[0][0]
    assert after - before == 11
    ev = rows(sim, select(SimulationEvent.description).where(SimulationEvent.event_type == "DATA_QUALITY"))
    assert ev and "forced failure" in ev[0][0]
