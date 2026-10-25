"""Production sizing in deploy/docker-compose.yml (owner 26.10.2026, «задействовать ядра»): PostgreSQL's
max_connections covers every pool of every API process plus the outbox worker and the one-off CLIs; the rehearsal stack
(and so CI) stays on one process and PostgreSQL's defaults."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
ENTRYPOINT = Path(__file__).resolve().parents[1] / "docker-entrypoint.sh"
SQLALCHEMY_POOL = 5 + 10  # a process without ERP_DB_POOL_SIZE / ERP_DB_MAX_OVERFLOW (the outbox worker, a CLI)
CLIS_AT_ONCE = 3  # update.sh's migration + integrity snapshot + an operator's `erp.sh cli`
RESERVED = 3 + 2  # superuser_reserved_connections + pg_dump of the backup container and a psql


def _default(value: str) -> str:
    found = re.fullmatch(r"\$\{[A-Z_]+:-([^}]*)\}", str(value))
    assert found, f"{value!r}: expected ${{NAME:-default}} so config/.env can override it"
    return found.group(1)


def _postgres_settings(service: dict) -> dict[str, str]:
    command = service["command"]
    assert command[0] == "postgres"
    pairs = command[1:]
    assert pairs[::2] == ["-c"] * (len(pairs) // 2)
    settings = {}
    for item in pairs[1::2]:
        name, _, value = item.partition("=")
        settings[name] = value
    return settings


def test_max_connections_cover_every_pool():
    services = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    api = services["api"]["environment"]
    workers = int(_default(api["ERP_API_WORKERS"]))
    per_process = int(_default(api["ERP_DB_POOL_SIZE"])) + int(_default(api["ERP_DB_MAX_OVERFLOW"]))
    max_connections = int(_default(_postgres_settings(services["db"])["max_connections"]))
    needed = workers * per_process + SQLALCHEMY_POOL + CLIS_AT_ONCE * SQLALCHEMY_POOL + RESERVED
    assert workers > 1
    assert max_connections >= needed, f"max_connections {max_connections} < {needed} the pools may open"


def test_parallel_queries_get_shared_memory_and_workers_fit():
    db = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["db"]
    settings = {name: _default(value) if value.startswith("${") else value
                for name, value in _postgres_settings(db).items()}
    assert _default(db["shm_size"]) not in {"", "64m", "64mb"}
    assert int(settings["max_parallel_workers"]) <= int(settings["max_worker_processes"])
    assert int(settings["max_parallel_workers_per_gather"]) <= int(settings["max_parallel_workers"])


def test_the_rehearsal_and_local_runs_stay_on_one_process_and_postgres_defaults():
    rehearsal = yaml.safe_load((DEPLOY / "docker-compose.rehearsal.yml").read_text(encoding="utf-8"))["services"]
    assert "command" not in rehearsal["db"] and "shm_size" not in rehearsal["db"]
    assert str(rehearsal["api"]["environment"]["ERP_API_WORKERS"]) == "1"
    assert 'workers=$(whole ERP_API_WORKERS "${ERP_API_WORKERS:-1}")' in ENTRYPOINT.read_text(encoding="utf-8")
