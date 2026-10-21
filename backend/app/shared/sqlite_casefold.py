"""SQLite folds case for ASCII only, so «ЧипДип» never matched «чипдип» in a LIKE prefilter on the stage/test
database. Every SQLite connection gets `erp_casefold(text)` (Python str.casefold); PostgreSQL uses ILIKE instead,
which folds Unicode by itself. Registered for any Engine, once, on import."""

from __future__ import annotations

import sqlite3

from sqlalchemy import event
from sqlalchemy.engine import Engine


def _casefold(value):
    return value.casefold() if isinstance(value, str) else value


@event.listens_for(Engine, "connect")
def _register(dbapi_connection, _record) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        dbapi_connection.create_function("erp_casefold", 1, _casefold, deterministic=True)
