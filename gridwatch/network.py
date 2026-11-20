"""Fictional Ontario-inspired transmission network: seed data and topology tools.

All substation and line names are fictional. Layout coordinates are schematic
(north-up) and carry no geographic meaning.
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gridwatch.database import Substation, TransmissionLine


@dataclass(frozen=True)
class SubstationSpec:
    code: str
    name: str
    region: str
    levels: str          # voltage levels hosted, e.g. "500/230"
    x: float
    y: float

    @property
    def max_kv(self) -> int:
        return max(int(v) for v in self.levels.split("/"))


@dataclass(frozen=True)
class LineSpec:
    code: str
    name: str
    from_code: str
    to_code: str
    kv: int
    rated_a: float
    length_km: float
    base_loading_pct: float   # mean loading used by the telemetry profile
    pf: float


SUBSTATIONS: tuple[SubstationSpec, ...] = (
    SubstationSpec("GBG", "Georgian Bay GS", "Northwest", "500", 2.0, 6.8),
    SubstationSpec("SUD", "Sudbury Junction TS", "Northeast", "500/230", 5.2, 9.0),
    SubstationSpec("LHT", "Lake Huron TS", "West", "500/230", 0.6, 4.0),
    SubstationSpec("HHT", "Halton Hills TS", "Central", "500/230", 3.0, 2.4),
    SubstationSpec("NFT", "Niagara Falls TS", "South", "500/230", 5.4, 0.7),
    SubstationSpec("GTA", "Greater Toronto TS", "Central", "230/115", 6.2, 3.8),
    SubstationSpec("OTT", "Ottawa Valley TS", "East", "230/115", 10.2, 6.6),
    SubstationSpec("KNG", "Kingston Ridge TS", "East", "230/115", 10.0, 3.0),
)

LINES: tuple[LineSpec, ...] = (
    LineSpec("TL-01", "Georgian Bay - Lake Huron 500 kV", "GBG", "LHT", 500, 2000, 185, 52, 0.98),
    LineSpec("TL-02", "Lake Huron - Halton Hills 500 kV", "LHT", "HHT", 500, 2000, 140, 58, 0.98),
    LineSpec("TL-03", "Halton Hills - Niagara Falls 500 kV", "HHT", "NFT", 500, 1800, 95, 46, 0.98),
    LineSpec("TL-04", "Georgian Bay - Sudbury 500 kV", "GBG", "SUD", 500, 1800, 210, 41, 0.98),
    LineSpec("TL-05", "Lake Huron - Greater Toronto 230 kV", "LHT", "GTA", 230, 1000, 120, 55, 0.97),
    LineSpec("TL-06", "Halton Hills - Greater Toronto 230 kV", "HHT", "GTA", 230, 1100, 45, 66, 0.97),
    LineSpec("TL-07", "Sudbury - Ottawa Valley 230 kV", "SUD", "OTT", 230, 900, 380, 38, 0.97),
    LineSpec("TL-08", "Greater Toronto - Kingston Ridge 230 kV", "GTA", "KNG", 230, 1000, 260, 49, 0.97),
    LineSpec("TL-09", "Niagara Falls - Greater Toronto 230 kV", "NFT", "GTA", 230, 1000, 130, 60, 0.97),
    LineSpec("TL-10", "Ottawa Valley - Kingston Ridge 230 kV", "OTT", "KNG", 230, 800, 150, 44, 0.97),
    LineSpec("TL-11", "Greater Toronto - Kingston Ridge 115 kV", "GTA", "KNG", 115, 600, 240, 65, 0.96),
    LineSpec("TL-12", "Ottawa Valley - Kingston Ridge 115 kV", "OTT", "KNG", 115, 500, 150, 57, 0.96),
)


def validate_network_spec() -> None:
    """Sanity-check seed data: every line voltage must exist at both ends."""
    subs = {s.code: s for s in SUBSTATIONS}
    for ln in LINES:
        for code in (ln.from_code, ln.to_code):
            if str(ln.kv) not in subs[code].levels.split("/"):
                raise ValueError(f"{ln.code}: {code} has no {ln.kv} kV bus")


def seed_network(session: Session) -> bool:
    """Insert the fictional network if the database is empty. Returns True if seeded."""
    if session.scalar(select(func.count(Substation.id))):
        return False
    validate_network_spec()
    by_code: dict[str, Substation] = {}
    for s in SUBSTATIONS:
        row = Substation(code=s.code, name=s.name, region=s.region, voltage_levels_kv=s.levels,
                         max_voltage_kv=s.max_kv, map_x=s.x, map_y=s.y)
        session.add(row)
        by_code[s.code] = row
    session.flush()
    for ln in LINES:
        session.add(TransmissionLine(
            code=ln.code, name=ln.name,
            from_substation_id=by_code[ln.from_code].id, to_substation_id=by_code[ln.to_code].id,
            nominal_kv=ln.kv, rated_current_a=ln.rated_a, length_km=ln.length_km,
            status="IN_SERVICE", base_loading_pct=ln.base_loading_pct, nominal_pf=ln.pf))
    session.flush()
    return True


def build_graph(session: Session, only_in_service: bool = False) -> nx.MultiGraph:
    """Topology graph (MultiGraph: parallel lines between the same pair are kept).

    Connectivity here is purely topological; it ignores voltage-level
    transformation and says nothing about power-flow feasibility.
    """
    g: nx.MultiGraph = nx.MultiGraph()
    for s in session.scalars(select(Substation)):
        g.add_node(s.code, name=s.name, x=s.map_x, y=s.map_y, kv=s.max_voltage_kv,
                   levels=s.voltage_levels_kv)
    for ln in session.scalars(select(TransmissionLine)):
        if only_in_service and ln.status != "IN_SERVICE":
            continue
        g.add_edge(ln.from_substation.code, ln.to_substation.code, key=ln.code,
                   code=ln.code, kv=ln.nominal_kv, status=ln.status, name=ln.name)
    return g


def topology_report(g: nx.MultiGraph) -> dict:
    """Connectivity summary of an (in-service) graph."""
    comps = [sorted(c) for c in nx.connected_components(g)]
    return {
        "substations": g.number_of_nodes(),
        "lines": g.number_of_edges(),
        "islands": len(comps),
        "connected": len(comps) == 1,
        "components": sorted(comps, key=len, reverse=True),
    }


def n_minus_1_topology(g: nx.MultiGraph, line_code: str) -> dict:
    """Would removing this line split the network? (topological screen only)."""
    h = g.copy()
    for u, v, key in list(h.edges(keys=True)):
        if key == line_code:
            h.remove_edge(u, v, key=key)
            break
    else:
        raise KeyError(line_code)
    rep = topology_report(h)
    # every substation outside the largest remaining component is cut off
    rep["isolated_substations"] = sorted(n for comp in rep["components"][1:] for n in comp)
    rep["line"] = line_code
    return rep
