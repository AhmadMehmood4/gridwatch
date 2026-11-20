"""Balanced three-phase engineering calculations.

Units: voltages in kV (line-to-line), currents in A, powers in MW / MVAr / MVA.

    S = sqrt(3) * V_LL * I          (kV * A / 1000 -> MVA)
    P = S * PF
    Q = sqrt(S^2 - P^2)             (magnitude; lead/lag sign is not modelled)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

SQRT3 = math.sqrt(3.0)


class CalculationError(ValueError):
    """Raised for physically invalid inputs (negative, NaN, PF outside 0..1)."""


def _finite_non_negative(name: str, value: float) -> float:
    if value is None or not math.isfinite(value):
        raise CalculationError(f"{name} must be a finite number, got {value!r}")
    if value < 0:
        raise CalculationError(f"{name} must be >= 0, got {value}")
    return float(value)


def _positive(name: str, value: float) -> float:
    value = _finite_non_negative(name, value)
    if value == 0:
        raise CalculationError(f"{name} must be > 0")
    return value


def apparent_power_mva(v_ll_kv: float, current_a: float) -> float:
    """S = sqrt(3) * V_LL * I."""
    v = _finite_non_negative("voltage_kv", v_ll_kv)
    i = _finite_non_negative("current_a", current_a)
    return SQRT3 * v * i / 1000.0


def active_power_mw(s_mva: float, power_factor: float) -> float:
    """P = S * PF."""
    s = _finite_non_negative("s_mva", s_mva)
    if power_factor is None or not math.isfinite(power_factor) or not 0.0 <= power_factor <= 1.0:
        raise CalculationError(f"power_factor must be within 0..1, got {power_factor!r}")
    return s * power_factor


def reactive_power_mvar(s_mva: float, p_mw: float) -> float:
    """Q = sqrt(S^2 - P^2) (magnitude)."""
    s = _finite_non_negative("s_mva", s_mva)
    p = _finite_non_negative("p_mw", p_mw)
    if p > s * (1 + 1e-9) + 1e-12:
        raise CalculationError(f"P ({p}) cannot exceed S ({s})")
    return math.sqrt(max(s * s - p * p, 0.0))


def current_from_apparent_power_a(s_mva: float, v_ll_kv: float) -> float:
    """I = S / (sqrt(3) * V_LL) in amperes."""
    s = _finite_non_negative("s_mva", s_mva)
    v = _positive("voltage_kv", v_ll_kv)
    return s * 1000.0 / (SQRT3 * v)


def rated_capacity_mva(nominal_kv: float, rated_current_a: float) -> float:
    """Thermal MVA capacity at nominal voltage."""
    return apparent_power_mva(_positive("nominal_kv", nominal_kv),
                              _positive("rated_current_a", rated_current_a))


def loading_percent(current_a: float, rated_current_a: float) -> float:
    """Loading % = measured current / rated current * 100."""
    i = _finite_non_negative("current_a", current_a)
    return 100.0 * i / _positive("rated_current_a", rated_current_a)


def voltage_pu(voltage_kv: float, nominal_kv: float) -> float:
    """Per-unit voltage on the line's nominal kV base."""
    return _finite_non_negative("voltage_kv", voltage_kv) / _positive("nominal_kv", nominal_kv)


@dataclass(frozen=True)
class ThreePhasePower:
    s_mva: float
    p_mw: float
    q_mvar: float
    power_factor: float


def three_phase_power(v_ll_kv: float, current_a: float, power_factor: float) -> ThreePhasePower:
    """Compute a consistent (S, P, Q) triple from V, I and PF."""
    s = apparent_power_mva(v_ll_kv, current_a)
    p = active_power_mw(s, power_factor)
    q = reactive_power_mvar(s, p)
    return ThreePhasePower(s_mva=s, p_mw=p, q_mvar=q, power_factor=power_factor)


def power_triangle_residual(p_mw: float, q_mvar: float, s_mva: float) -> float:
    """|sqrt(P^2+Q^2) - S| in MVA; ~0 for a consistent measurement set."""
    return abs(math.hypot(p_mw, q_mvar) - s_mva)
