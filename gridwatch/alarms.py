"""Alarm detection, lifecycle management and configurable thresholds.

Lifecycle:  (condition appears) -> ACTIVE -> [operator ack] -> ACKNOWLEDGED
            -> (condition returns to normal) -> CLEARED

* One open alarm per (line, alarm type) - enforced in code and by a partial
  unique index in the database.
* Severity escalation re-opens an acknowledged alarm (operator must re-ack).
* Hysteresis (dead-band) prevents chattering around a threshold.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from gridwatch.database import Alarm, AlarmThreshold, SimulationState
from gridwatch.events import log_event

SEVERITY_RANK = {"WARNING": 1, "HIGH": 2, "CRITICAL": 3}


@dataclass(frozen=True)
class Thresholds:
    loading_warning: float = 80.0      # % of rated current
    loading_high: float = 90.0
    loading_critical: float = 100.0
    v_low_warning: float = 0.95        # per unit
    v_low_high: float = 0.92
    v_low_critical: float = 0.90
    v_high_warning: float = 1.05
    v_high_high: float = 1.08
    v_high_critical: float = 1.10
    loading_deadband: float = 2.0      # % hysteresis before an overload alarm clears
    voltage_deadband: float = 0.005    # pu hysteresis before a voltage alarm clears

    def validate(self) -> None:
        """Raise ValueError if limits are not strictly ordered."""
        if not (0 < self.loading_warning < self.loading_high < self.loading_critical <= 300):
            raise ValueError("Loading limits must satisfy 0 < warning < high < critical <= 300 %")
        if not (0.5 <= self.v_low_critical < self.v_low_high < self.v_low_warning < 1.0):
            raise ValueError("Low-voltage limits must satisfy 0.5 <= critical < high < warning < 1.0 pu")
        if not (1.0 < self.v_high_warning < self.v_high_high < self.v_high_critical <= 1.5):
            raise ValueError("High-voltage limits must satisfy 1.0 < warning < high < critical <= 1.5 pu")
        if self.loading_deadband < 0 or self.voltage_deadband < 0:
            raise ValueError("Dead-bands must be >= 0")

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


THRESHOLD_META: dict[str, tuple[str, str]] = {
    "loading_warning": ("%", "Overload warning (loading above)"),
    "loading_high": ("%", "Overload high severity"),
    "loading_critical": ("%", "Overload critical (at/over thermal rating)"),
    "v_low_warning": ("pu", "Undervoltage warning (below)"),
    "v_low_high": ("pu", "Undervoltage high severity"),
    "v_low_critical": ("pu", "Undervoltage critical"),
    "v_high_warning": ("pu", "Overvoltage warning (above)"),
    "v_high_high": ("pu", "Overvoltage high severity"),
    "v_high_critical": ("pu", "Overvoltage critical"),
    "loading_deadband": ("%", "Hysteresis before overload alarm clears"),
    "voltage_deadband": ("pu", "Hysteresis before voltage alarm clears"),
}


def ensure_default_thresholds(session: Session) -> None:
    existing = {t.name for t in session.scalars(select(AlarmThreshold))}
    defaults = Thresholds().as_dict()
    for name, value in defaults.items():
        if name not in existing:
            unit, desc = THRESHOLD_META[name]
            session.add(AlarmThreshold(name=name, value=value, unit=unit, description=desc))


def load_thresholds(session: Session) -> Thresholds:
    """Load thresholds from the DB, falling back to defaults for missing rows."""
    values = Thresholds().as_dict()
    for row in session.scalars(select(AlarmThreshold)):
        if row.name in values:
            values[row.name] = float(row.value)
    return Thresholds(**values)


def save_thresholds(session: Session, th: Thresholds, timestamp: datetime | None = None) -> None:
    """Validate and persist thresholds (raises ValueError if invalid)."""
    th.validate()
    rows = {r.name: r for r in session.scalars(select(AlarmThreshold))}
    changed = []
    for f in fields(Thresholds):
        new = float(getattr(th, f.name))
        unit, desc = THRESHOLD_META[f.name]
        if f.name in rows:
            if abs(rows[f.name].value - new) > 1e-12:
                changed.append(f.name)
            rows[f.name].value = new
        else:
            session.add(AlarmThreshold(name=f.name, value=new, unit=unit, description=desc))
            changed.append(f.name)
    if changed:
        ts = timestamp or session.scalar(select(SimulationState.sim_time)) or datetime.now()
        log_event(session, ts, "THRESHOLDS_UPDATED", f"Alarm thresholds changed: {', '.join(changed)}")


# --------------------------------------------------------------------------- detection
@dataclass(frozen=True)
class Condition:
    alarm_type: str
    severity: str
    value: float
    threshold: float
    unit: str
    message: str


@dataclass(frozen=True)
class Observation:
    """Minimal per-line snapshot needed to evaluate alarm conditions."""
    line_id: int
    code: str
    nominal_kv: int
    in_service: bool
    loading_pct: float
    voltage_pu: float


@dataclass
class AlarmChanges:
    raised: int = 0
    escalated: int = 0
    deescalated: int = 0
    cleared: int = 0


def _high_side(value: float, warn: float, high: float, crit: float) -> tuple[str, float] | None:
    if value > crit:
        return "CRITICAL", crit
    if value > high:
        return "HIGH", high
    if value > warn:
        return "WARNING", warn
    return None


def _low_side(value: float, warn: float, high: float, crit: float) -> tuple[str, float] | None:
    if value < crit:
        return "CRITICAL", crit
    if value < high:
        return "HIGH", high
    if value < warn:
        return "WARNING", warn
    return None


def evaluate_conditions(obs: Observation, th: Thresholds,
                        open_types: frozenset[str] = frozenset()) -> dict[str, Condition]:
    """Return the alarm conditions currently true for a line, keyed by alarm type.

    open_types: types that already have an open alarm; these use hysteresis so
    that a value hovering at a limit does not make the alarm chatter.
    """
    out: dict[str, Condition] = {}
    if not obs.in_service:
        sev = "CRITICAL" if obs.nominal_kv >= 500 else "HIGH"
        out["OUTAGE"] = Condition("OUTAGE", sev, 0.0, 0.0, "status",
                                  f"{obs.code} is out of service ({obs.nominal_kv} kV)")
        return out

    relax_l = th.loading_deadband if "OVERLOAD" in open_types else 0.0
    hit = _high_side(obs.loading_pct + relax_l, th.loading_warning, th.loading_high, th.loading_critical)
    if hit:
        sev, thr = hit
        out["OVERLOAD"] = Condition("OVERLOAD", sev, obs.loading_pct, thr, "%",
                                    f"{obs.code} loading {obs.loading_pct:.1f}% exceeds {thr:.0f}% limit")

    relax_o = th.voltage_deadband if "OVERVOLTAGE" in open_types else 0.0
    hit = _high_side(obs.voltage_pu + relax_o, th.v_high_warning, th.v_high_high, th.v_high_critical)
    if hit:
        sev, thr = hit
        out["OVERVOLTAGE"] = Condition("OVERVOLTAGE", sev, obs.voltage_pu, thr, "pu",
                                       f"{obs.code} voltage {obs.voltage_pu:.3f} pu above {thr:.3f} pu")

    relax_u = th.voltage_deadband if "UNDERVOLTAGE" in open_types else 0.0
    hit = _low_side(obs.voltage_pu - relax_u, th.v_low_warning, th.v_low_high, th.v_low_critical)
    if hit:
        sev, thr = hit
        out["UNDERVOLTAGE"] = Condition("UNDERVOLTAGE", sev, obs.voltage_pu, thr, "pu",
                                        f"{obs.code} voltage {obs.voltage_pu:.3f} pu below {thr:.3f} pu")
    return out


def _worse(alarm_type: str, new: float, old: float) -> float:
    """Keep the worst value seen (lowest for undervoltage, highest otherwise)."""
    return min(new, old) if alarm_type == "UNDERVOLTAGE" else max(new, old)


def reconcile_alarms(session: Session, observations: list[Observation], timestamp: datetime,
                     th: Thresholds) -> AlarmChanges:
    """Bring the alarms table in line with the latest observations.

    Raises new alarms, escalates/de-escalates severity, updates peaks and clears
    alarms whose condition has gone. Never creates a duplicate open alarm.
    """
    changes = AlarmChanges()
    open_rows = session.scalars(select(Alarm).where(Alarm.state != "CLEARED")).all()
    index: dict[tuple[int, str], Alarm] = {(a.line_id, a.alarm_type): a for a in open_rows}
    types_by_line: dict[int, set[str]] = {}
    for (lid, typ) in index:
        types_by_line.setdefault(lid, set()).add(typ)

    for obs in observations:
        conds = evaluate_conditions(obs, th, frozenset(types_by_line.get(obs.line_id, set())))
        for typ in ("OVERLOAD", "OVERVOLTAGE", "UNDERVOLTAGE", "OUTAGE"):
            cond = conds.get(typ)
            existing = index.get((obs.line_id, typ))
            if cond and existing is None:
                alarm = Alarm(line_id=obs.line_id, alarm_type=typ, severity=cond.severity,
                              peak_severity=cond.severity, state="ACTIVE",
                              trigger_value=cond.value, latest_value=cond.value,
                              peak_value=cond.value, threshold_value=cond.threshold, unit=cond.unit,
                              message=cond.message, raised_at=timestamp)
                session.add(alarm)
                index[(obs.line_id, typ)] = alarm
                log_event(session, timestamp, "ALARM_RAISED",
                          f"{cond.severity} {typ}: {cond.message}", obs.line_id)
                changes.raised += 1
            elif cond and existing is not None:
                existing.latest_value = cond.value
                existing.peak_value = _worse(typ, cond.value, existing.peak_value)
                existing.threshold_value = cond.threshold
                rank_new, rank_old = SEVERITY_RANK[cond.severity], SEVERITY_RANK[existing.severity]
                if rank_new > rank_old:
                    existing.severity = cond.severity
                    existing.peak_severity = cond.severity
                    existing.message = cond.message
                    existing.state = "ACTIVE"            # escalation must be re-acknowledged
                    existing.acknowledged_at = None
                    existing.acknowledged_by = None
                    log_event(session, timestamp, "ALARM_ESCALATED",
                              f"{typ} on {obs.code} escalated to {cond.severity}", obs.line_id)
                    changes.escalated += 1
                elif rank_new < rank_old:
                    existing.severity = cond.severity
                    log_event(session, timestamp, "ALARM_DEESCALATED",
                              f"{typ} on {obs.code} reduced to {cond.severity}", obs.line_id)
                    changes.deescalated += 1
            elif existing is not None and not cond:
                existing.state = "CLEARED"
                existing.cleared_at = timestamp
                del index[(obs.line_id, typ)]
                log_event(session, timestamp, "ALARM_CLEARED",
                          f"{typ} alarm on {obs.code} returned to normal", obs.line_id)
                changes.cleared += 1
    session.flush()
    return changes


# --------------------------------------------------------------------------- operator actions
def acknowledge_alarms(session: Session, alarm_ids: list[int], user: str = "operator",
                       timestamp: datetime | None = None) -> int:
    """Acknowledge ACTIVE alarms by id. Cleared/unknown/already-acked ids are ignored.

    Returns the number of alarms acknowledged.
    """
    if not alarm_ids:
        return 0
    ts = timestamp or session.scalar(select(SimulationState.sim_time)) or datetime.now()
    rows = session.scalars(select(Alarm).where(Alarm.id.in_(alarm_ids), Alarm.state == "ACTIVE")).all()
    for a in rows:
        a.state = "ACKNOWLEDGED"
        a.acknowledged_at = ts
        a.acknowledged_by = user
        log_event(session, ts, "ALARM_ACKNOWLEDGED",
                  f"{a.severity} {a.alarm_type} on line {a.line_id} acknowledged by {user}", a.line_id)
    session.flush()
    return len(rows)


def acknowledge_all_active(session: Session, user: str = "operator") -> int:
    ids = list(session.scalars(select(Alarm.id).where(Alarm.state == "ACTIVE")))
    return acknowledge_alarms(session, ids, user)
