import math

import pytest

from gridwatch import calculations as c


def test_apparent_power_500kv_1000a():
    assert c.apparent_power_mva(500, 1000) == pytest.approx(866.0254, rel=1e-6)


def test_power_triangle_is_consistent():
    tp = c.three_phase_power(230, 800, 0.97)
    assert tp.p_mw == pytest.approx(tp.s_mva * 0.97)
    assert math.hypot(tp.p_mw, tp.q_mvar) == pytest.approx(tp.s_mva)
    assert c.power_triangle_residual(tp.p_mw, tp.q_mvar, tp.s_mva) < 1e-9


def test_q_formula_matches_definition():
    s, pf = 400.0, 0.9
    p = c.active_power_mw(s, pf)
    assert c.reactive_power_mvar(s, p) == pytest.approx(math.sqrt(s**2 - p**2))


def test_unity_pf_gives_zero_q():
    tp = c.three_phase_power(115, 500, 1.0)
    assert tp.q_mvar == pytest.approx(0.0, abs=1e-9)


def test_current_round_trip():
    s = c.apparent_power_mva(230, 612.5)
    assert c.current_from_apparent_power_a(s, 230) == pytest.approx(612.5)


def test_loading_and_per_unit():
    assert c.loading_percent(900, 1000) == pytest.approx(90.0)
    assert c.voltage_pu(218.5, 230) == pytest.approx(0.95)
    assert c.rated_capacity_mva(500, 2000) == pytest.approx(1732.05, rel=1e-4)


@pytest.mark.parametrize("fn,args", [
    (c.apparent_power_mva, (-1, 10)),
    (c.apparent_power_mva, (230, float("nan"))),
    (c.active_power_mw, (100, 1.2)),
    (c.active_power_mw, (100, -0.1)),
    (c.loading_percent, (100, 0)),
    (c.voltage_pu, (230, 0)),
    (c.reactive_power_mvar, (100, 150)),
])
def test_invalid_inputs_raise(fn, args):
    with pytest.raises(c.CalculationError):
        fn(*args)
