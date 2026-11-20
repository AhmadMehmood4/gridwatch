"""Simulation engine: advances the synthetic clock, stores telemetry, drives alarms.

All state lives in the database (simulation_state, simulation_overrides,
transmission_lines.status) so it survives Streamlit reruns and restarts.
Every public mutating method is a single transaction.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from gridwatch import config
from gridwatch.alarms import (
    Observation, ensure_default_thresholds, load_thresholds, reconcile_alarms,
)
from gridwatch.database import (
    Alarm, Base, Measurement, SimulationEvent, SimulationOverride, SimulationState,
    TransmissionLine, make_session_factory, session_scope,
)
from gridwatch.events import log_event
from gridwatch.network import seed_network
from gridwatch.telemetry import LineParams, generate_measurement, validate_measurement


@dataclass(frozen=True)
class SimSnapshot:
    """Detached copy of the simulation state, safe to use after the session closes."""
    tick: int
    sim_time: datetime
    seed: int
    status: str
    step_minutes: int
    refresh_interval_s: float
    scenario: str | None

    @property
    def running(self) -> bool:
        return self.status == "RUNNING"


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    description: str
    seed: int
    duration_ticks: int
    actions: tuple[tuple[int, str, dict], ...]   # (tick offset, method name, kwargs)
    backfill_ticks: int = 180                    # history before the scenario begins (15:00)


SCENARIOS: dict[str, Scenario] = {s.key: s for s in (
    Scenario("normal_operation", "Normal operation (baseline)",
             "No injected events. Daily load profile only - useful as a control run.",
             seed=42, duration_ticks=72, actions=()),
    Scenario("evening_peak_overload", "Evening peak overload",
             "TL-06 climbs past its high and critical limits, TL-09 briefly enters the high band, then both recover.",
             seed=7, duration_ticks=60, actions=(
                 (6, "apply_overload", {"line_code": "TL-06", "target_pct": 93.0}),
                 (14, "apply_overload", {"line_code": "TL-06", "target_pct": 108.0}),
                 (20, "apply_overload", {"line_code": "TL-09", "target_pct": 91.0}),
                 (34, "clear_override", {"line_code": "TL-09", "kind": "OVERLOAD"}),
                 (42, "clear_override", {"line_code": "TL-06", "kind": "OVERLOAD"}))),
    Scenario("line_outage_restoration", "Line outages and restoration",
             "A 500 kV and a 115 kV line trip and are later restored; observe outage alarms and availability.",
             seed=11, duration_ticks=60, actions=(
                 (8, "trip_line", {"line_code": "TL-02"}),
                 (14, "trip_line", {"line_code": "TL-11"}),
                 (36, "restore_line", {"line_code": "TL-02"}),
                 (44, "restore_line", {"line_code": "TL-11"}))),
    Scenario("voltage_excursion", "Voltage excursions",
             "TL-07 sags to undervoltage (deepening), TL-03 swells to overvoltage; both return to normal.",
             seed=21, duration_ticks=60, actions=(
                 (6, "apply_voltage", {"line_code": "TL-07", "target_pu": 0.935}),
                 (14, "apply_voltage", {"line_code": "TL-07", "target_pu": 0.885}),
                 (20, "apply_voltage", {"line_code": "TL-03", "target_pu": 1.07}),
                 (32, "clear_override", {"line_code": "TL-03", "kind": "VOLTAGE"}),
                 (40, "clear_override", {"line_code": "TL-07", "kind": "VOLTAGE"}))),
)}


class GridSimulator:
    def __init__(self, engine: Engine):
        self.engine = engine
        self._factory = make_session_factory(engine)

    # ------------------------------------------------------------------ lifecycle
    def session(self):
        return session_scope(self._factory)

    def initialize(self, seed: int = config.DEFAULT_SEED,
                   backfill_ticks: int = config.DEFAULT_BACKFILL_TICKS) -> bool:
        """Create schema + seed data (idempotent). Returns True if a fresh DB was built."""
        Base.metadata.create_all(self.engine)
        created = False
        with self.session() as s:
            seed_network(s)
            ensure_default_thresholds(s)
            if s.get(SimulationState, 1) is None:
                s.add(SimulationState(
                    id=1, tick=0, sim_time=config.SIM_EPOCH, seed=seed, status="PAUSED",
                    step_minutes=config.DEFAULT_STEP_MINUTES,
                    refresh_interval_s=config.DEFAULT_REFRESH_SECONDS, last_step_wall=0.0))
                s.flush()
                log_event(s, config.SIM_EPOCH, "SIM_INIT",
                          f"Simulation initialised (seed {seed}, synthetic telemetry)")
                created = True
        if created and backfill_ticks > 0:
            self.step(backfill_ticks)
        return created

    def get_state(self) -> SimSnapshot:
        with self.session() as s:
            st = s.get(SimulationState, 1)
            if st is None:
                raise RuntimeError("Simulation not initialised; call initialize() first")
            return SimSnapshot(st.tick, st.sim_time, st.seed, st.status, st.step_minutes,
                               st.refresh_interval_s, st.scenario)

    # ------------------------------------------------------------------ run control
    def start(self) -> None:
        with self.session() as s:
            st = s.get(SimulationState, 1)
            if st.status != "RUNNING":
                st.status = "RUNNING"
                st.last_step_wall = 0.0
                log_event(s, st.sim_time, "SIM_START", "Simulation started")

    def pause(self) -> None:
        with self.session() as s:
            st = s.get(SimulationState, 1)
            if st.status != "PAUSED":
                st.status = "PAUSED"
                log_event(s, st.sim_time, "SIM_PAUSE", "Simulation paused")

    def set_refresh_interval(self, seconds: float) -> None:
        if seconds <= 0:
            raise ValueError("Refresh interval must be positive")
        with self.session() as s:
            s.get(SimulationState, 1).refresh_interval_s = float(seconds)

    def set_step_minutes(self, minutes: int) -> None:
        if minutes <= 0:
            raise ValueError("Step must be positive")
        with self.session() as s:
            s.get(SimulationState, 1).step_minutes = int(minutes)

    def reset(self, seed: int | None = None, backfill_ticks: int = config.DEFAULT_BACKFILL_TICKS,
              scenario: str | None = None) -> None:
        """Wipe telemetry, alarms, events and injected faults; restart the clock.

        Alarm thresholds (configuration) are preserved.
        """
        with self.session() as s:
            st = s.get(SimulationState, 1)
            seed = st.seed if seed is None else int(seed)
            for model in (SimulationEvent, Alarm, Measurement, SimulationOverride):
                s.execute(delete(model))
            s.execute(update(TransmissionLine).values(status="IN_SERVICE"))
            st.tick, st.sim_time, st.seed = 0, config.SIM_EPOCH, seed
            st.status, st.last_step_wall, st.scenario = "PAUSED", 0.0, scenario
            s.flush()
            log_event(s, st.sim_time, "SIM_RESET", f"Simulation reset (seed {seed})")
        if backfill_ticks > 0:
            self.step(backfill_ticks)

    # ------------------------------------------------------------------ stepping
    def step(self, n: int = 1) -> int:
        """Advance n ticks in one transaction. Returns the new tick number."""
        if n < 1:
            raise ValueError("n must be >= 1")
        with self.session() as s:
            st = s.get(SimulationState, 1)
            for _ in range(n):
                self._tick(s, st)
            return st.tick

    def step_if_due(self, now: float | None = None) -> bool:
        """Advance one tick if RUNNING and the refresh interval has elapsed.

        The 'claim' is a single atomic UPDATE ... WHERE, so Streamlit reruns,
        multiple fragments or several browser tabs can never produce duplicate ticks.
        """
        now = time.time() if now is None else now
        with self.session() as s:
            st = s.get(SimulationState, 1)
            due_before = now - st.refresh_interval_s * 0.8
            claimed = s.execute(
                update(SimulationState)
                .where(SimulationState.id == 1, SimulationState.status == "RUNNING",
                       SimulationState.last_step_wall <= due_before)
                .values(last_step_wall=now)).rowcount
            if not claimed:
                return False
            s.refresh(st)
            self._tick(s, st)
            return True

    def _tick(self, s: Session, st: SimulationState) -> None:
        st.tick += 1
        st.sim_time = st.sim_time + timedelta(minutes=st.step_minutes)
        lines = s.scalars(select(TransmissionLine).order_by(TransmissionLine.id)).all()
        overrides: dict[int, dict[str, float]] = {}
        for o in s.scalars(select(SimulationOverride)):
            overrides.setdefault(o.line_id, {})[o.kind] = o.magnitude
        th = load_thresholds(s)

        observations: list[Observation] = []
        for ln in lines:
            params = LineParams(ln.id, ln.code, ln.nominal_kv, ln.rated_current_a,
                                ln.base_loading_pct, ln.nominal_pf, ln.status == "IN_SERVICE")
            ov = overrides.get(ln.id, {})
            rec = generate_measurement(params, st.tick, st.sim_time, st.seed,
                                       overload_pct=ov.get("OVERLOAD"),
                                       voltage_pu_override=ov.get("VOLTAGE"))
            problems = validate_measurement(rec, params)
            if problems:   # graceful handling: drop the bad sample, record why
                log_event(s, st.sim_time, "DATA_QUALITY",
                          f"Rejected sample for {ln.code}: {'; '.join(problems)}", ln.id)
                continue
            s.add(Measurement(
                line_id=rec.line_id, tick=rec.tick, timestamp=rec.timestamp, status=rec.status,
                voltage_kv=rec.voltage_kv, voltage_pu=rec.voltage_pu, current_a=rec.current_a,
                power_factor=rec.power_factor, p_mw=rec.p_mw, q_mvar=rec.q_mvar, s_mva=rec.s_mva,
                loading_pct=rec.loading_pct, data_source=config.DATA_SOURCE_LABEL))
            observations.append(Observation(ln.id, ln.code, ln.nominal_kv, rec.status == "IN_SERVICE",
                                            rec.loading_pct, rec.voltage_pu))
        s.flush()
        reconcile_alarms(s, observations, st.sim_time, th)
        if st.tick % 250 == 0:
            s.execute(delete(Measurement).where(Measurement.tick <= st.tick - config.RETENTION_TICKS))

    # ------------------------------------------------------------------ fault injection
    def _line(self, s: Session, code: str) -> TransmissionLine:
        ln = s.scalar(select(TransmissionLine).where(TransmissionLine.code == code))
        if ln is None:
            raise ValueError(f"Unknown line {code!r}")
        return ln

    def _set_override(self, line_code: str, kind: str, magnitude: float, event_type: str, text: str) -> None:
        with self.session() as s:
            st = s.get(SimulationState, 1)
            ln = self._line(s, line_code)
            if ln.status != "IN_SERVICE":
                raise ValueError(f"{line_code} is out of service; restore it before injecting a condition")
            row = s.scalar(select(SimulationOverride).where(
                SimulationOverride.line_id == ln.id, SimulationOverride.kind == kind))
            if row:
                row.magnitude, row.applied_at = magnitude, st.sim_time
            else:
                s.add(SimulationOverride(line_id=ln.id, kind=kind, magnitude=magnitude,
                                         applied_at=st.sim_time))
            log_event(s, st.sim_time, event_type, text, ln.id)

    def apply_overload(self, line_code: str, target_pct: float) -> None:
        """Force a line to read ~target_pct % of its rated current until cleared."""
        if not 20.0 <= target_pct <= 200.0:
            raise ValueError("Target loading must be between 20 % and 200 %")
        self._set_override(line_code, "OVERLOAD", float(target_pct), "OVERLOAD_APPLIED",
                           f"Operator-injected overload on {line_code}: target {target_pct:.0f}% loading")

    def apply_voltage(self, line_code: str, target_pu: float) -> None:
        """Force a line's voltage to ~target_pu until cleared (sag < 1.0 < swell)."""
        if not 0.5 <= target_pu <= 1.3:
            raise ValueError("Target voltage must be between 0.50 and 1.30 pu")
        self._set_override(line_code, "VOLTAGE", float(target_pu), "VOLTAGE_ABNORMALITY_APPLIED",
                           f"Operator-injected voltage abnormality on {line_code}: target {target_pu:.3f} pu")

    def clear_override(self, line_code: str, kind: str | None = None) -> int:
        """Remove injected conditions from a line (all kinds if kind is None). Returns rows removed."""
        with self.session() as s:
            st = s.get(SimulationState, 1)
            ln = self._line(s, line_code)
            stmt = delete(SimulationOverride).where(SimulationOverride.line_id == ln.id)
            if kind:
                stmt = stmt.where(SimulationOverride.kind == kind)
            n = s.execute(stmt).rowcount
            if n:
                log_event(s, st.sim_time, "OVERRIDE_CLEARED",
                          f"Injected {kind or 'all'} condition(s) cleared on {line_code}", ln.id)
            return n

    def clear_all_overrides(self) -> int:
        with self.session() as s:
            st = s.get(SimulationState, 1)
            n = s.execute(delete(SimulationOverride)).rowcount
            if n:
                log_event(s, st.sim_time, "OVERRIDE_CLEARED", f"All injected conditions cleared ({n})")
            return n

    def trip_line(self, line_code: str) -> None:
        with self.session() as s:
            st = s.get(SimulationState, 1)
            ln = self._line(s, line_code)
            if ln.status == "OUT_OF_SERVICE":
                raise ValueError(f"{line_code} is already out of service")
            ln.status = "OUT_OF_SERVICE"
            s.execute(delete(SimulationOverride).where(SimulationOverride.line_id == ln.id))
            log_event(s, st.sim_time, "LINE_TRIPPED", f"{line_code} tripped out of service", ln.id)

    def restore_line(self, line_code: str) -> None:
        with self.session() as s:
            st = s.get(SimulationState, 1)
            ln = self._line(s, line_code)
            if ln.status == "IN_SERVICE":
                raise ValueError(f"{line_code} is already in service")
            ln.status = "IN_SERVICE"
            log_event(s, st.sim_time, "LINE_RESTORED", f"{line_code} restored to service", ln.id)

    # ------------------------------------------------------------------ scenarios
    def run_scenario(self, key: str) -> Scenario:
        """Reset with the scenario's seed, then replay its scripted event timeline.

        Deterministic: the same scenario always yields identical telemetry and alarms.
        Leaves the simulation paused at the end so results can be inspected.
        """
        sc = SCENARIOS.get(key)
        if sc is None:
            raise ValueError(f"Unknown scenario {key!r}")
        self.reset(seed=sc.seed, backfill_ticks=sc.backfill_ticks, scenario=sc.key)
        with self.session() as s:
            st = s.get(SimulationState, 1)
            log_event(s, st.sim_time, "SCENARIO_START", f"Scenario started: {sc.title}")
        schedule: dict[int, list[tuple[str, dict]]] = {}
        for offset, method, kwargs in sc.actions:
            schedule.setdefault(offset, []).append((method, kwargs))
        for i in range(sc.duration_ticks):
            for method, kwargs in schedule.get(i, []):
                getattr(self, method)(**kwargs)
            self.step(1)
        with self.session() as s:
            st = s.get(SimulationState, 1)
            log_event(s, st.sim_time, "SCENARIO_END", f"Scenario finished: {sc.title}")
        return sc
