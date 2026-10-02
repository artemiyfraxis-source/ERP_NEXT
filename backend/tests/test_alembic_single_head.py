"""Parallel branches each adding a migration must be renumbered on merge: the history keeps one head."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_single_head() -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, f"Alembic has {len(heads)} heads {heads}: point the newer migration's down_revision at the other head"
