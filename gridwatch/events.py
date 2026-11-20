"""Operational event log helper (table: simulation_events)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from gridwatch.database import SimulationEvent


def log_event(session: Session, timestamp: datetime, event_type: str,
              description: str, line_id: int | None = None) -> SimulationEvent:
    """Append an operational event. Caller controls the transaction."""
    ev = SimulationEvent(timestamp=timestamp, event_type=event_type,
                         description=description[:300], line_id=line_id)
    session.add(ev)
    return ev
