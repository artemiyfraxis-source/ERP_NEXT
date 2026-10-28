"""Several API processes (ERP_API_WORKERS > 1, owner 26.10.2026): what one process starts must hold across all of them.

Two engines over one database stand for two API processes: each has its own connection pool, exactly as two uvicorn
workers do. SQLite runs everywhere (the process-lock fallback); PostgreSQL — the production path, a real advisory
lock — opts in with ERP_TEST_POSTGRES_URL (CI job migrations-pg)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.database import build_engine, pool_options
from app.modules.warehouse import scan_recognition
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork, WarehouseReceiptFileRow
from app.modules.warehouse.scan_agents import ScanAgentRow
from app.modules.warehouse.scan_jobs import DONE, ScanJob, ScanJobRow, stored_result
from app.shared import device_logs
from app.shared.advisory_lock import advisory_lock, lock_key
from app.shared.persistence import Base

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
TABLES = [WarehouseReceiptFileRow.__table__, ScanAgentRow.__table__, ScanJobRow.__table__]  # jobs point at their agent


def _postgres_url():
    raw = os.environ.get("ERP_TEST_POSTGRES_URL")
    if raw is None:
        pytest.skip("ERP_TEST_POSTGRES_URL is not set (PostgreSQL advisory-lock tests)")
    url = make_url(raw)
    if url.host not in {"localhost", "127.0.0.1", "::1"} or url.database != "erp_stock_test":
        pytest.fail("ERP_TEST_POSTGRES_URL requires PostgreSQL on loopback, database erp_stock_test", pytrace=False)
    return url.set(drivername="postgresql+psycopg")


@pytest.fixture(params=["sqlite", "postgresql"])
def two_processes(request, tmp_path):
    """Two engines on one database with the scan tables — two API processes."""
    if request.param == "sqlite":
        url = f"sqlite+pysqlite:///{tmp_path / 'erp.db'}"
        engines = [create_engine(url, connect_args={"check_same_thread": False}) for _ in range(2)]
        Base.metadata.create_all(engines[0], tables=TABLES)
        try:
            yield engines
        finally:
            for engine in engines:
                engine.dispose()
        return
    url = _postgres_url()
    schema = f"erp_workers_{uuid4().hex}"
    admin = create_engine(url, poolclass=NullPool)
    options = {"options": f"-csearch_path={schema} -clock_timeout=10000"}
    engines = [create_engine(url, connect_args=options) for _ in range(2)]
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        Base.metadata.create_all(engines[0], tables=TABLES)
        yield engines
    finally:
        for engine in engines:
            engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def test_an_advisory_lock_held_by_one_process_is_held_for_the_other(two_processes):
    first, second = two_processes
    key = lock_key("test", uuid4())
    with advisory_lock(first, key) as got:
        assert got
        with advisory_lock(second, key, wait=False) as other:
            assert other is False
        with advisory_lock(second, lock_key("test", uuid4()), wait=False) as another_key:
            assert another_key  # another key is free
    with advisory_lock(second, key, wait=False) as other:
        assert other  # released on leaving the block


def test_a_waiting_process_gets_the_lock_once_the_holder_leaves(two_processes):
    first, second = two_processes
    key = lock_key("test", uuid4())
    order: list[str] = []
    entered = threading.Event()

    def holder():
        with advisory_lock(first, key):
            entered.set()
            time.sleep(0.3)
            order.append("first out")

    thread = threading.Thread(target=holder)
    thread.start()
    assert entered.wait(5)
    with advisory_lock(second, key):
        order.append("second in")
    thread.join(5)
    assert order == ["first out", "second in"]


def test_a_scan_is_recognised_once_when_the_upload_and_the_poll_land_in_two_processes(two_processes, monkeypatch):
    """The upload's background task runs in one API process, the screen's poll restarts the reading in another: the
    model (a paid call) reads once — the second process finds the lock taken and leaves; the answer is stored once."""
    first, second = two_processes
    file_id, job_id = uuid4(), uuid4()
    with first.begin() as connection:
        connection.execute(WarehouseReceiptFileRow.__table__.insert().values(
            id=file_id, file_name="upd.pdf", content_type="application/pdf", size=3, content=b"%PD",
            uploaded_by="Агент", uploaded_at=NOW))
        connection.execute(ScanJobRow.__table__.insert().values(
            id=job_id, status=DONE, requested_by="Кладовщик", created_at=NOW, claimed_at=NOW, finished_at=NOW,
            file_id=file_id, error="", result=""))
    calls: list[int] = []

    def slow_reading(_uow_factory, _file_id, _config):
        calls.append(1)
        time.sleep(0.4)  # a model reads for seconds: the other process arrives meanwhile
        return None

    monkeypatch.setattr(scan_recognition, "file_reading", slow_reading)
    monkeypatch.setattr(scan_recognition, "scan_answer",
                        lambda _sources, link, _reading: {"scan_file": link, "recognised": {}, "lines": [], "note": "ответ"})
    factories = [
        (lambda sessions=sessionmaker(bind=engine, expire_on_commit=False): SqlAlchemyWarehouseUnitOfWork(sessions))
        for engine in (first, second)
    ]
    job = ScanJob(id=job_id, status=DONE, error="", file_id=file_id, result="", requested_by="Кладовщик")
    start = threading.Barrier(2, timeout=10)

    def process(index: int) -> None:
        start.wait()
        scan_recognition.recognise_job(factories[index], scan_recognition.ScanSources(), job)

    threads = [threading.Thread(target=process, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len(calls) == 1
    assert json.loads(stored_result(factories[1](), job_id))["note"] == "ответ"
def test_each_api_process_takes_its_pool_size_from_the_environment(monkeypatch):
    monkeypatch.setenv("ERP_DB_POOL_SIZE", "7")
    monkeypatch.setenv("ERP_DB_MAX_OVERFLOW", "3")
    assert pool_options() == {"pool_size": 7, "max_overflow": 3}
    engine = build_engine("postgresql+psycopg://erp:x@127.0.0.1:1/erp")  # never connects
    try:
        assert engine.pool.size() == 7
        assert engine.pool._max_overflow == 3
    finally:
        engine.dispose()
    monkeypatch.delenv("ERP_DB_POOL_SIZE")
    monkeypatch.delenv("ERP_DB_MAX_OVERFLOW")
    assert pool_options() == {}  # SQLAlchemy's 5 + 10, as before


@pytest.mark.parametrize("value", ["", "abc", "-1", "0"])
def test_a_broken_pool_size_stops_the_start(monkeypatch, value):
    monkeypatch.setenv("ERP_DB_POOL_SIZE", value)
    if value == "":
        assert pool_options() == {}
        return
    with pytest.raises(RuntimeError, match="ERP_DB_POOL_SIZE"):
        pool_options()


@pytest.mark.skipif(device_logs.fcntl is None, reason="flock: Linux only (the local Windows ERP runs one process)")
def test_a_device_journal_is_locked_for_the_other_api_processes(tmp_path):
    """Appending and trimming the journal holds a file lock another process cannot take meanwhile."""
    path = tmp_path / "scanner-log.txt"
    probe = (
        "import fcntl, sys\n"
        "handle = open(sys.argv[1], 'a')\n"
        "try:\n"
        "    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "except BlockingIOError:\n"
        "    sys.exit(3)\n"
    )
    lock_file = str(path) + ".lock"
    with device_logs._locked(path):
        assert subprocess.run([sys.executable, "-c", probe, lock_file]).returncode == 3
    assert subprocess.run([sys.executable, "-c", probe, lock_file]).returncode == 0
    device_logs._append(path, "строка\n", 1024)
    assert path.read_text(encoding="utf-8") == "строка\n"
