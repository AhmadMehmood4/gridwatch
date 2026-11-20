"""Reproducible synthetic telemetry generation (pure functions, no database).

Each line's measurements are generated independently from a seeded RNG keyed on
(seed, line_id, tick), so output is identical regardless of call order. A daily
load profile, slow wander and noise are applied to the line's base loading; the
electrical quantities are then derived with balanced three-phase equations so
V, I, P, Q, S and loading are always mutually consistent.

THIS IS NOT A POWER-FLOW SOLUTION. Line values are not coupled to one another.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from gridwatch import calculations as calc


@dataclass(frozen=True)
class LineParams:
    line_id: int
    code: str
    nominal_kv: float
    rated_current_a: float
    base_loading_pct: float
    nominal_pf: float
    in_service: bool = True


@dataclass(frozen=True)
class MeasurementRecord:
    line_id: int
    tick: int
    timestamp: datetime
    status: str
    voltage_kv: float
    voltage_pu: float
    current_a: float
    power_factor: float
    p_mw: float
    q_mvar: float
    s_mva: float
    loading_pct: float


def daily_load_factor(t: datetime, phase_hours: float = 0.0) -> float:
    """Dimensionless demand profile: evening peak (~18:00), overnight trough (~04:00)."""
    h = t.hour + t.minute / 60.0 + t.second / 3600.0
    f = (1.0
         + 0.20 * math.cos(2 * math.pi * (h - 18.0 - phase_hours) / 24.0)
         + 0.05 * math.cos(2 * math.pi * (h - 8.0 - phase_hours) / 12.0))
    return f


def generate_measurement(params: LineParams, tick: int, sim_time: datetime, seed: int,
                         overload_pct: float | None = None,
                         voltage_pu_override: float | None = None) -> MeasurementRecord:
    """Generate one synthetic measurement for a line at a tick.

    overload_pct / voltage_pu_override are operator-injected abnormal conditions.
    An out-of-service line reads zero on every quantity.
    """
    if not params.in_service:
        return MeasurementRecord(params.line_id, tick, sim_time, "OUT_OF_SERVICE",
                                 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    rng = np.random.default_rng([int(seed) & 0x7FFFFFFF, int(params.line_id), int(tick)])
    lid = params.line_id

    if overload_pct is not None:
        load = overload_pct / 100.0 + rng.normal(0.0, 0.004)
    else:
        phase = (lid % 4) * 0.5                                   # staggered peaks across lines
        wander = 0.03 * math.sin(2 * math.pi * tick / (37 + 3 * lid) + lid)
        noise = rng.normal(0.0, 0.012)
        load = (params.base_loading_pct / 100.0) * daily_load_factor(sim_time, phase) * (1 + wander + noise)
    load = float(min(max(load, 0.02), 1.6))

    if voltage_pu_override is not None:
        v_pu = voltage_pu_override + rng.normal(0.0, 0.0015)
    else:
        droop = 0.030 * max(0.0, load - 0.45)                     # heavier loading -> slight sag
        slow = 0.006 * math.sin(2 * math.pi * tick / (53 + 2 * lid) + 0.7 * lid)
        v_pu = 1.005 + slow + rng.normal(0.0, 0.0025) - droop
    v_pu = float(max(v_pu, 0.0))

    pf = float(min(max(params.nominal_pf + rng.normal(0.0, 0.006) - 0.02 * max(0.0, load - 0.8),
                       0.90), 0.999))

    v_kv = v_pu * params.nominal_kv
    i_a = load * params.rated_current_a
    pw = calc.three_phase_power(v_kv, i_a, pf)
    return MeasurementRecord(
        line_id=lid, tick=tick, timestamp=sim_time, status="IN_SERVICE",
        voltage_kv=v_kv, voltage_pu=calc.voltage_pu(v_kv, params.nominal_kv),
        current_a=i_a, power_factor=pf, p_mw=pw.p_mw, q_mvar=pw.q_mvar, s_mva=pw.s_mva,
        loading_pct=calc.loading_percent(i_a, params.rated_current_a))


def validate_measurement(rec: MeasurementRecord, params: LineParams) -> list[str]:
    """Return a list of data-quality problems (empty list = record is acceptable)."""
    problems: list[str] = []
    numeric = {"voltage_kv": rec.voltage_kv, "voltage_pu": rec.voltage_pu,
               "current_a": rec.current_a, "power_factor": rec.power_factor,
               "p_mw": rec.p_mw, "q_mvar": rec.q_mvar, "s_mva": rec.s_mva,
               "loading_pct": rec.loading_pct}
    for name, value in numeric.items():
        if value is None or not math.isfinite(value):
            problems.append(f"{name} is missing or not finite")
        elif value < 0:
            problems.append(f"{name} is negative ({value:.3f})")
    if problems:
        return problems
    if not 0.0 <= rec.power_factor <= 1.0:
        problems.append(f"power factor out of range ({rec.power_factor:.3f})")
    if rec.status == "IN_SERVICE":
        s_expected = calc.apparent_power_mva(rec.voltage_kv, rec.current_a)
        if abs(s_expected - rec.s_mva) > 1e-6 * max(1.0, s_expected):
            problems.append("S inconsistent with sqrt(3)*V*I")
        if calc.power_triangle_residual(rec.p_mw, rec.q_mvar, rec.s_mva) > 1e-6 * max(1.0, rec.s_mva):
            problems.append("P, Q, S violate the power triangle")
        expected_loading = calc.loading_percent(rec.current_a, params.rated_current_a)
        if abs(expected_loading - rec.loading_pct) > 1e-6 * max(1.0, expected_loading):
            problems.append("loading inconsistent with I / I_rated")
    return problems
