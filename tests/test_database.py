import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from gridwatch.database import Measurement, Substation, TransmissionLine
from gridwatch.network import LINES, SUBSTATIONS, build_graph, n_minus_1_topology, topology_report, validate_network_spec


def test_network_size_and_voltage_classes(sim):
    with sim.session() as s:
        assert s.scalar(select(func.count(Substation.id))) == 8 == len(SUBSTATIONS)
        assert s.scalar(select(func.count(TransmissionLine.id))) == 12 == len(LINES)
        kvs = set(s.scalars(select(TransmissionLine.nominal_kv)))
    assert kvs == {115, 230, 500}


def test_network_spec_voltage_consistency():
    validate_network_spec()


def test_initialize_is_idempotent(sim):
    before = sim.get_state().tick
    assert sim.initialize() is False
    assert sim.get_state().tick == before
    with sim.session() as s:
        assert s.scalar(select(func.count(Substation.id))) == 8


def test_backfill_writes_one_row_per_line_per_tick(sim):
    with sim.session() as s:
        assert s.scalar(select(func.count(Measurement.id))) == 24 * 12


def test_foreign_keys_enforced(sim):
    with pytest.raises(IntegrityError):
        with sim.session() as s:
            s.add(Measurement(line_id=9999, tick=1, timestamp=sim.get_state().sim_time,
                              status="IN_SERVICE", voltage_kv=1, voltage_pu=1, current_a=1,
                              power_factor=0.9, p_mw=1, q_mvar=1, s_mva=1, loading_pct=1))


def test_check_constraint_rejects_bad_line(sim):
    with pytest.raises(IntegrityError):
        with sim.session() as s:
            s.add(TransmissionLine(code="BAD", name="bad", from_substation_id=1, to_substation_id=1,
                                   nominal_kv=230, rated_current_a=100, length_km=1,
                                   base_loading_pct=50, nominal_pf=0.97))


def test_unique_line_tick_constraint(sim):
    with pytest.raises(IntegrityError):
        with sim.session() as s:
            m = s.scalars(select(Measurement).limit(1)).one()
            s.add(Measurement(line_id=m.line_id, tick=m.tick, timestamp=m.timestamp, status="IN_SERVICE",
                              voltage_kv=1, voltage_pu=1, current_a=1, power_factor=0.9,
                              p_mw=1, q_mvar=1, s_mva=1, loading_pct=1))


def test_failed_transaction_rolls_back(sim):
    n = sim.get_state().tick
    with pytest.raises(ValueError):
        with sim.session() as s:
            s.add(Substation(code="ZZZ", name="tmp", region="x", voltage_levels_kv="230",
                             max_voltage_kv=230, map_x=0, map_y=0))
            raise ValueError("boom")
    with sim.session() as s:
        assert s.scalar(select(func.count(Substation.id))) == 8
    assert sim.get_state().tick == n


def test_indexes_exist(engine, sim):
    with engine.connect() as c:
        names = {r[1] for r in c.execute(text("PRAGMA index_list('alarms')"))}
    assert "uq_alarm_one_open_per_condition" in names


def test_graph_connected_and_n_minus_1(sim):
    with sim.session() as s:
        g = build_graph(s)
    assert topology_report(g)["connected"]
    assert g.number_of_edges() == 12
    assert n_minus_1_topology(g, "TL-01")["connected"]
    with pytest.raises(KeyError):
        n_minus_1_topology(g, "NOPE")


def test_tripped_lines_can_island_a_substation(sim):
    sim.trip_line("TL-01")
    sim.trip_line("TL-04")
    with sim.session() as s:
        rep = topology_report(build_graph(s, only_in_service=True))
    assert rep["connected"] is False and rep["islands"] == 2
