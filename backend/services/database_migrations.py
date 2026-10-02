"""Programmatic Alembic upgrades guarded against concurrent app startup."""

from __future__ import annotations

import threading
import weakref
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

_lock = threading.Lock()
_migrated_engines: weakref.WeakSet[Engine] = weakref.WeakSet()


def apply_migrations(engine: Engine) -> None:
    if engine in _migrated_engines:
        return
    with _lock:
        if engine in _migrated_engines:
            return
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        with engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(71120261002)"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        _migrated_engines.add(engine)
