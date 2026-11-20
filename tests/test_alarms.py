from datetime import datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from gridwatch.alarms import (Observation, Thresholds, acknowledge_all_active, acknowledge_alarms,
                              evaluate_conditions, load_thresholds, reconcile_alarms, save_thresholds)
from gridwatch.database import Alarm, SimulationEvent

TH = Thresholds()
T0 = datetime(2026, 1, 15, 12, 0)


def obs(loading=50.0, pu=1.0, in_service=True, kv=230, lid=1):
    return Observation(lid, f"TL-{lid:02d}", kv, in_service, loading, pu)


# ---- pure detection -------------------------------------------------------
@pytest.mark.parametrize("loading,expected", [(79.9, None), (85, "WARNING"), (95, "HIGH"), (101, "CRITICAL")])
def test_overload_severity_bands(loading, expected):
    c = evaluate_conditions(obs(loading=loading), TH).get("OVERLOAD")
    assert (c.severity if c else None) == expected


@pytest.mark.parametrize("pu,typ,sev", [
    (1.00, None, None), (0.94, "UNDERVOLTAGE", "WARNING"), (0.91, "UNDERVOLTAGE", "HIGH"),
    (0.89, "UNDERVOLTAGE", "CRITICAL"), (1.06, "OVERVOLTAGE", "WARNING"),
    (1.09, "OVERVOLTAGE", "HIGH"), (1.11, "OVERVOLTAGE", "CRITICAL")])
def test_voltage_bands(pu, typ, sev):
    conds = evaluate_conditions(obs(pu=pu), TH)
    if typ is None:
        assert not conds
    else:
        assert conds[typ].severity == sev


def test_outage_severity_depends_on_voltage_class():
    assert evaluate_conditions(obs(in_service=False, kv=500), TH)["OUTAGE"].severity == "CRITICAL"
    assert evaluate_conditions(obs(in_service=False, kv=115), TH)["OUTAGE"].severity == "HIGH"


def test_offline_line_raises_no_voltage_or_loading_alarms():
    assert set(evaluate_conditions(obs(loading=0, pu=0, in_service=False), TH)) == {"OUTAGE"}


def test_hysteresis_holds_alarm_inside_deadband():
    assert "OVERLOAD" not in evaluate_conditions(obs(loading=79), TH)
    assert "OVERLOAD" in evaluate_conditions(obs(loading=79), TH, frozenset({"OVERLOAD"}))
    assert "OVERLOAD" not in evaluate_conditions(obs(loading=77.9), TH, frozenset({"OVERLOAD"}))


# ---- lifecycle in the database ----------------------------------------------
def count_open(s):
    return s.scalar(select(func.count(Alarm.id)).where(Alarm.state != "CLEARED"))


def test_alarm_raised_once_not_duplicated(sim):
    with sim.session() as s:
        for i in range(5):
            reconcile_alarms(s, [obs(loading=95)], T0, TH)
        assert count_open(s) == 1
        assert s.scalar(select(func.count(SimulationEvent.id)).where(SimulationEvent.event_type == "ALARM_RAISED")) == 1


def test_alarm_clears_and_can_reraise_as_new_record(sim):
    with sim.session() as s:
        reconcile_alarms(s, [obs(loading=95)], T0, TH)
        ch = reconcile_alarms(s, [obs(loading=40)], T0, TH)
        assert ch.cleared == 1 and count_open(s) == 0
        reconcile_alarms(s, [obs(loading=96)], T0, TH)
        assert s.scalar(select(func.count(Alarm.id))) == 2
        assert count_open(s) == 1
        cleared = s.scalars(select(Alarm).where(Alarm.state == "CLEARED")).one()
        assert cleared.cleared_at == T0


def test_escalation_updates_severity_and_resets_acknowledgement(sim):
    with sim.session() as s:
        reconcile_alarms(s, [obs(loading=85)], T0, TH)
        a = s.scalars(select(Alarm)).one()
        acknowledge_alarms(s, [a.id], "alice", T0)
        assert a.state == "ACKNOWLEDGED"
        ch = reconcile_alarms(s, [obs(loading=104)], T0, TH)
        assert ch.escalated == 1 and a.severity == "CRITICAL" and a.state == "ACTIVE"
        assert a.acknowledged_at is None
        assert a.peak_value == pytest.approx(104)


def test_deescalation_keeps_acknowledgement(sim):
    with sim.session() as s:
        reconcile_alarms(s, [obs(loading=104)], T0, TH)
        a = s.scalars(select(Alarm)).one()
        acknowledge_alarms(s, [a.id], "alice", T0)
        reconcile_alarms(s, [obs(loading=84)], T0, TH)
        assert a.severity == "WARNING" and a.state == "ACKNOWLEDGED"
        assert a.peak_severity == "CRITICAL"          # history keeps the worst level reached
        assert a.peak_value == pytest.approx(104)


def test_acknowledge_semantics(sim):
    with sim.session() as s:
        reconcile_alarms(s, [obs(loading=95, lid=1), obs(loading=95, lid=2)], T0, TH)
        ids = list(s.scalars(select(Alarm.id)))
        assert acknowledge_alarms(s, [ids[0]], "bob", T0) == 1
        assert acknowledge_alarms(s, [ids[0]], "bob", T0) == 0       # already acked
        assert acknowledge_alarms(s, [9999], "bob", T0) == 0         # unknown id
        assert acknowledge_alarms(s, [], "bob", T0) == 0
        assert acknowledge_all_active(s, "bob") == 1
        assert s.scalar(select(func.count(Alarm.id)).where(Alarm.state == "ACTIVE")) == 0


def test_cleared_alarm_cannot_be_acknowledged(sim):
    with sim.session() as s:
        reconcile_alarms(s, [obs(loading=95)], T0, TH)
        reconcile_alarms(s, [obs(loading=10)], T0, TH)
        a = s.scalars(select(Alarm)).one()
        assert acknowledge_alarms(s, [a.id], "bob", T0) == 0
        assert a.state == "CLEARED" and a.acknowledged_at is None


def test_database_rejects_duplicate_open_alarm(sim):
    with pytest.raises(IntegrityError):
        with sim.session() as s:
            reconcile_alarms(s, [obs(loading=95)], T0, TH)
            a = s.scalars(select(Alarm)).one()
            s.add(Alarm(line_id=a.line_id, alarm_type=a.alarm_type, severity="HIGH", peak_severity="HIGH",
                        state="ACTIVE", trigger_value=1, latest_value=1, peak_value=1, threshold_value=1,
                        unit="%", message="dup", raised_at=T0))
            s.flush()


# ---- thresholds ----------------------------------------------------------------
def test_threshold_persistence_and_validation(sim):
    with sim.session() as s:
        save_thresholds(s, Thresholds(loading_warning=70, loading_high=85, loading_critical=95))
    with sim.session() as s:
        th = load_thresholds(s)
        assert (th.loading_warning, th.loading_high, th.loading_critical) == (70, 85, 95)
        with pytest.raises(ValueError):
            save_thresholds(s, Thresholds(loading_warning=95, loading_high=90))
        with pytest.raises(ValueError):
            save_thresholds(s, Thresholds(v_low_warning=1.02))


def test_lowered_threshold_changes_detection(sim):
    with sim.session() as s:
        save_thresholds(s, Thresholds(loading_warning=40, loading_high=60, loading_critical=80))
    sim.step(1)
    with sim.session() as s:
        assert s.scalar(select(func.count(Alarm.id))) > 0
