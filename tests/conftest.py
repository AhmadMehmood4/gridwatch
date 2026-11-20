import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gridwatch.database import create_db_engine  # noqa: E402
from gridwatch.simulator import GridSimulator  # noqa: E402


@pytest.fixture()
def engine(tmp_path):
    eng = create_db_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    yield eng
    eng.dispose()


@pytest.fixture()
def sim(engine):
    s = GridSimulator(engine)
    s.initialize(seed=42, backfill_ticks=24)
    return s
