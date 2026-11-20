from datetime import datetime

import pytest

from gridwatch import calculations as c
from gridwatch.telemetry import (LineParams, MeasurementRecord, daily_load_factor,
                                 generate_measurement, validate_measurement)

P = LineParams(1, "TL-X", 230, 1000, 60, 0.97)
T = datetime(2026, 1, 15, 18, 0)


def test_reproducible_for_same_seed_and_tick():
    assert generate_measurement(P, 10, T, 42) == generate_measurement(P, 10, T, 42)


def test_different_seed_or_tick_changes_output():
    a = generate_measurement(P, 10, T, 42)
    assert a != generate_measurement(P, 10, T, 43)
    assert a != generate_measurement(P, 11, T, 42)


def test_measurements_are_internally_consistent():
    for tick in range(200):
        r = generate_measurement(P, tick, T, 5)
        assert r.s_mva == pytest.approx(c.apparent_power_mva(r.voltage_kv, r.current_a))
        assert r.p_mw == pytest.approx(r.s_mva * r.power_factor)
        assert c.power_triangle_residual(r.p_mw, r.q_mvar, r.s_mva) < 1e-6
        assert r.loading_pct == pytest.approx(100 * r.current_a / P.rated_current_a)
        assert r.voltage_pu == pytest.approx(r.voltage_kv / P.nominal_kv)
        assert validate_measurement(r, P) == []


def test_evening_peak_exceeds_overnight_trough():
    assert daily_load_factor(datetime(2026, 1, 15, 18)) > daily_load_factor(datetime(2026, 1, 15, 4))


def test_normal_operation_stays_in_voltage_band():
    for tick in range(500):
        r = generate_measurement(P, tick, T, 1)
        assert 0.95 < r.voltage_pu < 1.05


def test_overload_override_hits_target():
    r = generate_measurement(P, 3, T, 42, overload_pct=108)
    assert r.loading_pct == pytest.approx(108, abs=2.5)


def test_voltage_override_hits_target():
    r = generate_measurement(P, 3, T, 42, voltage_pu_override=0.88)
    assert r.voltage_pu == pytest.approx(0.88, abs=0.01)


def test_out_of_service_line_reads_zero():
    off = LineParams(1, "TL-X", 230, 1000, 60, 0.97, in_service=False)
    r = generate_measurement(off, 1, T, 42)
    assert r.status == "OUT_OF_SERVICE"
    assert (r.current_a, r.p_mw, r.s_mva, r.loading_pct, r.voltage_kv) == (0, 0, 0, 0, 0)


def test_validate_flags_bad_data():
    good = generate_measurement(P, 1, T, 42)
    nan = MeasurementRecord(**{**good.__dict__, "current_a": float("nan")})
    neg = MeasurementRecord(**{**good.__dict__, "p_mw": -5.0})
    inconsistent = MeasurementRecord(**{**good.__dict__, "s_mva": good.s_mva * 1.5})
    assert validate_measurement(nan, P)
    assert validate_measurement(neg, P)
    assert validate_measurement(inconsistent, P)
