"""Stock-cover transactions on PostgreSQL; opt in with ERP_TEST_POSTGRES_URL."""

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Lock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.main import app
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.requests.api import moscow_now
from app.shared.http_auth import authenticated_principal
from app.shared.persistence import create_schema
from app.shared import warehouse_location
from app.workflows.procurement_stock import SqlAlchemyProcurementStockPort, SqlAlchemyProcurementStockUnitOfWork
from tests.procurement.test_stock_cover import (
    NOW, add_lot, bind, buyer_principal, cover, cover_without_lot, get_lot, line_in_work, stock,
)
# Collect the existing version/rollback contracts against the PostgreSQL fixtures below.
from tests.procurement.test_stock_cover_concurrency import (  # noqa: F401
    test_command_retries_and_revalidates_changed_unit,
    test_stock_port_flush_rejects_change_after_domain_mutation,
    test_stock_port_rejects_a_view_changed_before_mutation,
)
from tests.procurement.test_stock_cover_transactions import (  # noqa: F401
    snapshot, test_late_failure_rolls_back_stock_cover_transaction,
)


@pytest.fixture(autouse=True)
def isolated_rack_layout(monkeypatch):
    # Domain validation must not call the loader installed by app.main's configured database.
    monkeypatch.setattr(warehouse_location, "current_rack_layout", lambda: warehouse_location.DEFAULT_RACK_LAYOUT)


def test_rack_layout_does_not_load_configured_database(monkeypatch):
    calls = []

    def external_loader():
        calls.append("external database loader")
        return None

    monkeypatch.setattr(warehouse_location, "_layout_loader", external_loader)
    monkeypatch.setattr(warehouse_location, "_layout_cache", None)
    lot = stock()
    lot.edit_row({"storage_location": "2G5"})
    assert calls == []


@pytest.fixture()
def env(monkeypatch):
    raw_url = os.environ.get("ERP_TEST_POSTGRES_URL")
    if raw_url is None:
        pytest.skip("ERP_TEST_POSTGRES_URL is not set (PostgreSQL stock tests)")
    try:
        url = make_url(raw_url)
    except (ArgumentError, ValueError):
        pytest.fail("ERP_TEST_POSTGRES_URL must be a valid PostgreSQL URL", pytrace=False)
    if (
        url.drivername not in {"postgresql", "postgresql+psycopg"}
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or url.database != "erp_stock_test"
        or url.query
    ):
        pytest.fail(
            "ERP_TEST_POSTGRES_URL requires PostgreSQL on loopback, database erp_stock_test, and no query options",
            pytrace=False,
        )
    url = url.set(drivername="postgresql+psycopg")
    schema = f"erp_stock_{uuid4().hex}"
    connect_args = {
        "connect_timeout": 5,
        # Pin localhost to loopback even when libpq environment defaults are present.
        "hostaddr": "::1" if url.host == "::1" else "127.0.0.1",
        "options": "-cstatement_timeout=15000 -clock_timeout=5000 -cidle_in_transaction_session_timeout=30000",
    }
    admin = create_engine(url, connect_args=connect_args, poolclass=NullPool)
    engine = create_engine(
        url, poolclass=NullPool, isolation_level="READ COMMITTED",
        connect_args={**connect_args, "options": f"{connect_args['options']} -csearch_path={schema}"},
    )
    created = False
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        created = True
        create_schema(engine)
        sessions = sessionmaker(bind=engine, expire_on_commit=False)
        monkeypatch.setattr(
            app.state, "procurement_uow_factory", lambda: SqlAlchemyProcurementUnitOfWork(sessions), raising=False,
        )
        monkeypatch.setattr(
            app.state, "procurement_stock_uow_factory", lambda: SqlAlchemyProcurementStockUnitOfWork(sessions),
            raising=False,
        )
        monkeypatch.setitem(app.dependency_overrides, authenticated_principal, buyer_principal)
        monkeypatch.setitem(app.dependency_overrides, moscow_now, lambda: NOW)
        # No lifespan: commands use test UoWs; the rack-layout fixture also isolates domain validation.
        client = TestClient(app)
        try:
            yield client, sessions
        finally:
            client.close()
    finally:
        engine.dispose()
        try:
            if created:
                with admin.begin() as connection:
                    connection.execute(DropSchema(schema, cascade=True))
        finally:
            admin.dispose()


@pytest.fixture()
def concurrent_env(env):
    return env


def synchronize_mutations(monkeypatch, method):
    barrier = Barrier(2, timeout=10)
    lock = Lock()
    attempts = []
    original = getattr(SqlAlchemyProcurementStockPort, method)

    def mutate(port, lot_id, quantity, *, expected_version):
        session = port.repository.session
        # Reading the driver's PID does not issue SQL or trigger autoflush.
        pid = session.connection().connection.driver_connection.info.backend_pid
        with lock:
            attempts.append((session, expected_version, pid))
            first_attempt = len(attempts) <= 2
        result = original(port, lot_id, quantity, expected_version=expected_version)
        if first_attempt:
            # Both rows are still dirty, before the next query/autoflush can hold a row lock.
            # Retried commands must never wait for a request that has already finished.
            barrier.wait()
        return result

    monkeypatch.setattr(SqlAlchemyProcurementStockPort, method, mutate)
    return attempts


def concurrent_requests(operation):
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(operation, index) for index in range(2)]
        return [future.result(timeout=30) for future in futures]


def assert_only_new_audits(before, after, expected):
    previous = {row["id"]: row for row in before["audit"]}
    assert {row["id"]: row for row in after["audit"] if row["id"] in previous} == previous
    added = [row for row in after["audit"] if row["id"] not in previous]
    assert len(added) == len(expected)
    assert {(row["event_type"], row["aggregate_id"]) for row in added} == expected
    assert after["outbox"] == before["outbox"]


@pytest.mark.parametrize("action", ["cover", "bind"])
def test_concurrent_http_reservations_retry_without_overbooking(env, monkeypatch, action):
    client, sessions = env
    lines = [line_in_work(client, sessions, suffix=index + 1) for index in range(2)]
    lot = add_lot(sessions, stock("100"))
    cover_ids = []
    if action == "bind":
        for index, line in enumerate(lines):
            response = cover_without_lot(client, line, "80")
            assert response.status_code == 200, response.text
            lines[index] = response.json()["line"]
            cover_ids.append(response.json()["cover"]["id"])
    with sessions() as session:
        before = snapshot(session.connection())
    attempts = synchronize_mutations(monkeypatch, "reserve")

    def request(index):
        if action == "cover":
            return cover(client, lines[index], lot, "80")
        return bind(client, lines[index], cover_ids[index], lot)

    responses = concurrent_requests(request)

    assert sorted(response.status_code for response in responses) == [200, 422], [r.text for r in responses]
    winner = next(index for index, response in enumerate(responses) if response.status_code == 200)
    loser = 1 - winner
    assert "доступно 20 шт" in responses[loser].json()["detail"]
    assert [version for _, version, _ in attempts] == [lot.version, lot.version, lot.version + 1]
    assert len({session for session, _, _ in attempts}) == 3  # the retry also starts a fresh UoW
    assert attempts[0][2] != attempts[1][2]  # simultaneous PostgreSQL connections
    stored = get_lot(sessions, lot.id)
    assert (stored.version, stored.reserved, stored.available) == (lot.version + 1, Decimal("80"), Decimal("20"))
    with sessions() as session:
        after = snapshot(session.connection())
    winner_id, loser_id = UUID(lines[winner]["id"]), UUID(lines[loser]["id"])
    assert next(row for row in after["lines"] if row["id"] == loser_id) == next(
        row for row in before["lines"] if row["id"] == loser_id
    )
    winning_line = next(row for row in after["lines"] if row["id"] == winner_id)
    assert (winning_line["version"], winning_line["stock_covered"]) == (lines[winner]["version"] + 1, Decimal("80"))
    bound = [row for row in after["covers"] if row["lot_id"] == lot.id]
    assert len(bound) == 1
    assert (bound[0]["line_id"], bound[0]["quantity"]) == (winner_id, Decimal("80"))
    if action == "cover":
        assert after["covers"] == bound
    else:
        assert len(after["covers"]) == 2
        assert next(row for row in after["covers"] if row["line_id"] == loser_id) == next(
            row for row in before["covers"] if row["line_id"] == loser_id
        )
    event_type = "procurement.stock_covered" if action == "cover" else "procurement.stock_cover_bound"
    assert_only_new_audits(before, after, {
        (event_type, winner_id), ("warehouse.reserved_for_procurement", lot.id),
    })


def test_concurrent_http_cancellation_releases_reserve_once(env, monkeypatch):
    client, sessions = env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("100"))
    response = cover(client, line, lot, "80")
    assert response.status_code == 200, response.text
    line = response.json()["line"]  # Partial cover: cancellation is still allowed.
    reserved_lot = get_lot(sessions, lot.id)
    with sessions() as session:
        before = snapshot(session.connection())
    attempts = synchronize_mutations(monkeypatch, "release")

    responses = concurrent_requests(lambda _: client.post(
        f"/api/procurement/{line['id']}/cancel", json={"version": line["version"]},
    ))

    assert sorted(response.status_code for response in responses) == [200, 409], [r.text for r in responses]
    assert [version for _, version, _ in attempts] == [reserved_lot.version] * 2
    assert attempts[0][0] is not attempts[1][0]
    assert attempts[0][2] != attempts[1][2]
    stored = get_lot(sessions, lot.id)
    assert (stored.version, stored.reserved, stored.available) == (reserved_lot.version + 1, Decimal("0"), Decimal("100"))
    with sessions() as session:
        after = snapshot(session.connection())
    assert len(after["lines"]) == len(after["covers"]) == 1
    cancelled, released = after["lines"][0], after["covers"][0]
    assert (cancelled["version"], cancelled["status"], cancelled["stock_covered"]) == (
        line["version"] + 1, "Отменено", Decimal("0"),
    )
    assert (released["version"], released["quantity"], released["consumed"]) == (
        before["covers"][0]["version"] + 1, Decimal("0"), Decimal("0"),
    )
    assert released["released_at"] is not None
    assert_only_new_audits(before, after, {
        ("procurement.cancelled", UUID(line["id"])), ("warehouse.procurement_reserve_released", lot.id),
    })
