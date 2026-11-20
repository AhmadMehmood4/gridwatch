"""Central configuration constants for GridWatch."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "gridwatch.db"

# Simulation defaults ---------------------------------------------------------
DEFAULT_SEED = 42
SIM_EPOCH = datetime(2026, 11, 19, 0, 0, 0)   # simulation clock starts here (synthetic date)
DEFAULT_STEP_MINUTES = 5                      # simulated minutes advanced per tick
DEFAULT_BACKFILL_TICKS = 204                  # 17 h of history generated on first start (clock lands at 17:00)
DEFAULT_REFRESH_SECONDS = 2.0                 # wall-clock seconds between live ticks
RETENTION_TICKS = 20_000                      # older measurements are pruned automatically

VOLTAGE_CLASSES_KV = (115, 230, 500)
STEP_MINUTE_CHOICES = (1, 5, 15, 30)
REFRESH_CHOICES_S = (1.0, 2.0, 3.0, 5.0, 10.0)

DATA_SOURCE_LABEL = "SYNTHETIC"
DISCLAIMER = (
    "Synthetic telemetry from an educational simulator. Not utility data and not a "
    "validated AC power-flow solution; each line's measurements are generated "
    "independently and are only internally consistent (S = √3·V·I)."
)


def get_database_url() -> str:
    """SQLAlchemy URL for the SQLite database (override with GRIDWATCH_DB_URL)."""
    env = os.environ.get("GRIDWATCH_DB_URL")
    if env:
        return env
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"
