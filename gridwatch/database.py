"""SQLAlchemy models, engine factory and session helpers.

Schema overview (all FKs enforced via PRAGMA foreign_keys=ON):

    substations 1 --< transmission_lines (from/to) 1 --< measurements
                                          1 --< alarms
                                          1 --< simulation_events (optional)
                                          1 --< simulation_overrides
    alarm_thresholds   : persisted, configurable alarm limits
    simulation_state   : single-row simulation clock / run status
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Sequence

from sqlalchemy import (
    CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String,
    UniqueConstraint, create_engine, event, text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker,
)
from sqlalchemy.pool import StaticPool

from gridwatch.config import get_database_url

LINE_STATUSES = ("IN_SERVICE", "OUT_OF_SERVICE")
SEVERITIES = ("WARNING", "HIGH", "CRITICAL")
ALARM_STATES = ("ACTIVE", "ACKNOWLEDGED", "CLEARED")
ALARM_TYPES = ("OVERLOAD", "OVERVOLTAGE", "UNDERVOLTAGE", "OUTAGE")
OVERRIDE_KINDS = ("OVERLOAD", "VOLTAGE")
SIM_STATUSES = ("RUNNING", "PAUSED")


def _in(column: str, values: Sequence[object]) -> str:
    rendered = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in values)
    return f"{column} IN ({rendered})"


class Base(DeclarativeBase):
    pass


class Substation(Base):
    __tablename__ = "substations"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    region: Mapped[str] = mapped_column(String(40), nullable=False)
    voltage_levels_kv: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g. "500/230"
    max_voltage_kv: Mapped[int] = mapped_column(Integer, nullable=False)
    map_x: Mapped[float] = mapped_column(Float, nullable=False)  # schematic layout only
    map_y: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        CheckConstraint(_in("max_voltage_kv", (115, 230, 500)), name="ck_substation_voltage_class"),
    )


class TransmissionLine(Base):
    __tablename__ = "transmission_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    from_substation_id: Mapped[int] = mapped_column(ForeignKey("substations.id"), nullable=False)
    to_substation_id: Mapped[int] = mapped_column(ForeignKey("substations.id"), nullable=False)
    nominal_kv: Mapped[int] = mapped_column(Integer, nullable=False)          # line-to-line
    rated_current_a: Mapped[float] = mapped_column(Float, nullable=False)     # configurable limit
    length_km: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(14), nullable=False, default="IN_SERVICE")
    base_loading_pct: Mapped[float] = mapped_column(Float, nullable=False)    # simulation profile
    nominal_pf: Mapped[float] = mapped_column(Float, nullable=False, default=0.97)

    from_substation: Mapped[Substation] = relationship(foreign_keys=[from_substation_id])
    to_substation: Mapped[Substation] = relationship(foreign_keys=[to_substation_id])

    __table_args__ = (
        CheckConstraint(_in("nominal_kv", (115, 230, 500)), name="ck_line_voltage_class"),
        CheckConstraint("rated_current_a > 0", name="ck_line_rating_positive"),
        CheckConstraint("length_km > 0", name="ck_line_length_positive"),
        CheckConstraint("from_substation_id <> to_substation_id", name="ck_line_distinct_ends"),
        CheckConstraint(_in("status", LINE_STATUSES), name="ck_line_status"),
        CheckConstraint("base_loading_pct BETWEEN 0 AND 150", name="ck_line_base_loading"),
        CheckConstraint("nominal_pf BETWEEN 0.8 AND 1.0", name="ck_line_pf"),
        Index("ix_lines_status", "status"),
    )

    @property
    def rated_mva(self) -> float:
        return 3 ** 0.5 * self.nominal_kv * self.rated_current_a / 1000.0


class Measurement(Base):
    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("transmission_lines.id"), nullable=False)
    tick: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # simulation clock
    status: Mapped[str] = mapped_column(String(14), nullable=False)
    voltage_kv: Mapped[float] = mapped_column(Float, nullable=False)
    voltage_pu: Mapped[float] = mapped_column(Float, nullable=False)
    current_a: Mapped[float] = mapped_column(Float, nullable=False)
    power_factor: Mapped[float] = mapped_column(Float, nullable=False)
    p_mw: Mapped[float] = mapped_column(Float, nullable=False)
    q_mvar: Mapped[float] = mapped_column(Float, nullable=False)
    s_mva: Mapped[float] = mapped_column(Float, nullable=False)
    loading_pct: Mapped[float] = mapped_column(Float, nullable=False)
    data_source: Mapped[str] = mapped_column(String(12), nullable=False, default="SYNTHETIC")

    line: Mapped[TransmissionLine] = relationship()

    __table_args__ = (
        UniqueConstraint("line_id", "tick", name="uq_measurement_line_tick"),
        CheckConstraint("voltage_kv >= 0 AND current_a >= 0 AND s_mva >= 0 AND p_mw >= 0 AND q_mvar >= 0",
                        name="ck_measurement_non_negative"),
        CheckConstraint("power_factor BETWEEN 0 AND 1", name="ck_measurement_pf"),
        CheckConstraint("loading_pct >= 0", name="ck_measurement_loading"),
        CheckConstraint(_in("status", LINE_STATUSES), name="ck_measurement_status"),
        Index("ix_measurements_line_ts", "line_id", "timestamp"),
        Index("ix_measurements_ts", "timestamp"),
        Index("ix_measurements_tick", "tick"),
    )


class Alarm(Base):
    __tablename__ = "alarms"

    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("transmission_lines.id"), nullable=False)
    alarm_type: Mapped[str] = mapped_column(String(14), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)       # current severity
    peak_severity: Mapped[str] = mapped_column(String(10), nullable=False)  # worst severity reached
    state: Mapped[str] = mapped_column(String(14), nullable=False, default="ACTIVE")
    trigger_value: Mapped[float] = mapped_column(Float, nullable=False)
    latest_value: Mapped[float] = mapped_column(Float, nullable=False)
    peak_value: Mapped[float] = mapped_column(Float, nullable=False)   # worst value seen
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(8), nullable=False)
    message: Mapped[str] = mapped_column(String(240), nullable=False)
    raised_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)
    acknowledged_by: Mapped[str | None] = mapped_column(String(40))
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime)

    line: Mapped[TransmissionLine] = relationship()

    __table_args__ = (
        CheckConstraint(_in("severity", SEVERITIES), name="ck_alarm_severity"),
        CheckConstraint(_in("peak_severity", SEVERITIES), name="ck_alarm_peak_severity"),
        CheckConstraint(_in("state", ALARM_STATES), name="ck_alarm_state"),
        CheckConstraint(_in("alarm_type", ALARM_TYPES), name="ck_alarm_type"),
        # Duplicate prevention enforced by the database itself: only one open
        # (not cleared) alarm may exist per line + alarm type.
        Index("uq_alarm_one_open_per_condition", "line_id", "alarm_type", unique=True,
              sqlite_where=text("state <> 'CLEARED'")),
        Index("ix_alarms_state_severity", "state", "severity"),
        Index("ix_alarms_raised_at", "raised_at"),
    )


class SimulationEvent(Base):
    __tablename__ = "simulation_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    line_id: Mapped[int | None] = mapped_column(ForeignKey("transmission_lines.id"))
    description: Mapped[str] = mapped_column(String(300), nullable=False)

    __table_args__ = (
        Index("ix_events_ts", "timestamp"),
        Index("ix_events_type", "event_type"),
    )


class AlarmThreshold(Base):
    __tablename__ = "alarm_thresholds"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(8), nullable=False)
    description: Mapped[str] = mapped_column(String(160), nullable=False, default="")


class SimulationState(Base):
    __tablename__ = "simulation_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    tick: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sim_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="PAUSED")
    step_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    refresh_interval_s: Mapped[float] = mapped_column(Float, nullable=False, default=2.0)
    last_step_wall: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    scenario: Mapped[str | None] = mapped_column(String(60))

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_state_singleton"),
        CheckConstraint(_in("status", SIM_STATUSES), name="ck_state_status"),
        CheckConstraint("step_minutes > 0", name="ck_state_step"),
    )


class SimulationOverride(Base):
    """Operator-injected abnormal condition applied to a line's telemetry."""
    __tablename__ = "simulation_overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("transmission_lines.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    magnitude: Mapped[float] = mapped_column(Float, nullable=False)  # % loading or per-unit voltage
    applied_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    line: Mapped[TransmissionLine] = relationship()

    __table_args__ = (
        UniqueConstraint("line_id", "kind", name="uq_override_line_kind"),
        CheckConstraint(_in("kind", OVERRIDE_KINDS), name="ck_override_kind"),
    )


def create_db_engine(url: str | None = None, echo: bool = False) -> Engine:
    """Create an engine with SQLite foreign keys enforced."""
    url = url or get_database_url()
    kwargs: dict = {"echo": echo}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)

    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any error."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
