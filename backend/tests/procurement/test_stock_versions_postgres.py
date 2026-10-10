"""Audit F01 on PostgreSQL: two connections race for one line, one cover or one lot; opt in with ERP_TEST_POSTGRES_URL.

Both commands read their rows and stop at a barrier before writing. Then either both write at once, or one of them
waits until the other has committed — the F01 window, where a read is still used after another transaction's
commit. One wins; the loser gets 409 (or 422 when its single retry finds the stock gone) and leaves nothing: no
extra reserve, no audit, no outbox. The SQLite interleavings of test_stock_versions run here too.
"""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Event, Lock
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from app.modules.fulfillment import application as fulfillment_application
from app.modules.fulfillment.infrastructure import FulfillmentLineRow
from app.modules.procurement.domain import ProcurementLine
from app.modules.procurement.stock_port import StockVersionConflict
from tests.procurement.test_stock_cover import add_lot, bind, cover, get_lot, line_in_work, stock
from tests.procurement.test_stock_cover_postgres import (  # noqa: F401 — fixtures
    assert_only_new_audits, env, isolated_rack_layout,
)
from tests.procurement.test_stock_cover_transactions import snapshot
# The reconciliation report reads PostgreSQL types (uuid, json, numeric) too.
from tests.procurement.test_stock_reconcile import (  # noqa: F401
    test_correct_reserves_report_nothing,
    test_one_cover_bound_to_two_lots_leaves_a_reserve_without_holder,
    test_two_covers_written_over_one_line_are_reported,
)
# Collect the deterministic interleavings against the PostgreSQL fixture below.
from tests.procurement.test_stock_versions import (  # noqa: F401
    bind_kit,
    kit,
    lotless_line,
    test_a_snapshot_older_than_the_row_is_refused,
    test_bind_loses_to_a_release_or_cancel_committed_meanwhile,
    test_http_loser_gets_409_after_its_retry_and_writes_nothing,
    test_kit_reserve_loses_to_a_procurement_cover_on_its_lot,
    test_kit_takeover_loses_to_a_release_of_the_cover,
    test_line_and_cover_refuse_a_write_over_a_commit_made_after_the_read,
    test_lot_written_twice_in_one_transaction_and_read_again_after_commit,
    test_one_lotless_cover_bound_to_two_lots_stays_on_one,
    test_procurement_cover_loses_to_a_kit_reserve_on_its_lot,
    test_release_or_cancel_loses_to_a_bind_committed_meanwhile,
    test_two_covers_of_one_line_from_two_lots_leave_one,
)


@pytest.fixture()
def concurrent_env(env):
    return env


def race(monkeypatch, points, commands, hold=None):
    """Run two commands on two connections; the first call of each (owner, method) point is where a command has read
    and not yet written, and both wait there for each other. Then `hold`:
    None — both go on at once;
    "arrival" — the one the barrier picks waits until the other command has finished (committed);
    a method name — the command that paused in it waits until the other has finished.
    Later calls (a retry) pass straight through. → each command's result or the exception it raised."""
    barrier = Barrier(2, timeout=10)
    finished = Event()
    lock = Lock()
    calls = []
    for owner, method in points:
        original = getattr(owner, method)

        def paused(*args, __original=original, __method=method, **kwargs):
            result = __original(*args, **kwargs)
            with lock:
                calls.append(__method)
                first = len(calls) <= 2
            if first:
                arrival = barrier.wait()
                if (hold == "arrival" and arrival == 0) or hold == __method:
                    assert finished.wait(10), "the other command did not finish"
            return result

        monkeypatch.setattr(owner, method, paused)

    def run(command):
        try:
            return command()
        except Exception as error:  # the loser's refusal is part of the result
            return error
        finally:
            finished.set()  # a held command cannot finish first: this is the other one

    with ThreadPoolExecutor(max_workers=len(commands)) as executor:
        futures = [executor.submit(run, command) for command in commands]
        return [future.result(timeout=30) for future in futures]


def state(sessions):
    with sessions() as session:
        return snapshot(session.connection())


def fulfillment_lines(sessions):
    with sessions() as session:
        return [dict(row) for row in session.execute(select(FulfillmentLineRow.__table__)).mappings()]


@pytest.mark.parametrize("hold", [None, "arrival"])
def test_two_connections_cover_one_line_from_two_lots(env, monkeypatch, hold):
    client, sessions = env
    line = line_in_work(client, sessions)  # КОЛ-ВО 124: 80 + 80 does not fit
    lots = [add_lot(sessions, stock("100", grn=f"FRX.26.000030{index}")) for index in range(2)]
    before = state(sessions)

    responses = race(
        monkeypatch, [(ProcurementLine, "cover_from_stock")],
        [lambda lot=lot: cover(client, line, lot, "80") for lot in lots], hold,
    )

    assert sorted(response.status_code for response in responses) == [200, 409], [r.text for r in responses]
    winner = next(index for index, response in enumerate(responses) if response.status_code == 200)
    assert responses[1 - winner].json()["detail"] == "Строка изменилась после загрузки формы"
    after = state(sessions)
    stored = after["lines"][0]
    assert (stored["version"], stored["stock_covered"]) == (line["version"] + 1, Decimal("80"))
    (only,) = after["covers"]
    assert (only["lot_id"], only["quantity"]) == (lots[winner].id, Decimal("80"))
    won = get_lot(sessions, lots[winner].id)
    assert (won.reserved, won.version) == (Decimal("80"), lots[winner].version + 1)
    assert get_lot(sessions, lots[1 - winner].id) == lots[1 - winner]
    assert_only_new_audits(before, after, {
        ("procurement.stock_covered", UUID(line["id"])), ("warehouse.reserved_for_procurement", lots[winner].id),
    })


@pytest.mark.parametrize("hold", [None, "arrival"])
def test_two_connections_bind_one_cover_to_two_lots(env, monkeypatch, hold):
    client, sessions = env
    line, cover_id = lotless_line(client, sessions)
    lots = [add_lot(sessions, stock("100", grn=f"FRX.26.000040{index}")) for index in range(2)]
    before = state(sessions)

    responses = race(
        monkeypatch, [(ProcurementLine, "bind_stock_cover")],
        [lambda lot=lot: bind(client, line, cover_id, lot) for lot in lots], hold,
    )

    assert sorted(response.status_code for response in responses) == [200, 409], [r.text for r in responses]
    winner = next(index for index, response in enumerate(responses) if response.status_code == 200)
    after = state(sessions)
    (bound,) = after["covers"]
    assert (bound["lot_id"], bound["quantity"], bound["version"]) == (lots[winner].id, Decimal("80"), 2)
    assert after["lines"][0]["version"] == line["version"] + 1
    assert get_lot(sessions, lots[winner].id).reserved == Decimal("80")
    assert get_lot(sessions, lots[1 - winner].id) == lots[1 - winner]
    assert_only_new_audits(before, after, {
        ("procurement.stock_cover_bound", UUID(line["id"])), ("warehouse.reserved_for_procurement", lots[winner].id),
    })


@pytest.mark.parametrize("other", ["release", "cancel"])
@pytest.mark.parametrize("hold", [None, "bind first", "bind waits"])
def test_two_connections_bind_against_release_or_cancel(env, monkeypatch, other, hold):
    client, sessions = env
    line, cover_id = lotless_line(client, sessions)
    lot = add_lot(sessions, stock("100"))
    before = state(sessions)
    taking_back = "release_stock_cover" if other == "release" else "cancel"
    path = "stock-cover/release" if other == "release" else "cancel"

    responses = race(
        monkeypatch, [(ProcurementLine, "bind_stock_cover"), (ProcurementLine, taking_back)],
        [
            lambda: bind(client, line, cover_id, lot),
            lambda: client.post(f"/api/procurement/{line['id']}/{path}", json={"version": line["version"]}),
        ],
        {None: None, "bind first": taking_back, "bind waits": "bind_stock_cover"}[hold],
    )

    assert sorted(response.status_code for response in responses) == [200, 409], [r.text for r in responses]
    if hold is not None:
        assert responses[0].status_code == (200 if hold == "bind first" else 409)
    after = state(sessions)
    (held,) = after["covers"]
    stored = get_lot(sessions, lot.id)
    assert after["lines"][0]["version"] == line["version"] + 1
    line_id = UUID(line["id"])
    if responses[0].status_code == 200:  # the bind won: the lot holds the cover's reserve
        assert (held["lot_id"], held["quantity"]) == (lot.id, Decimal("80"))
        assert (stored.reserved, stored.version) == (Decimal("80"), lot.version + 1)
        assert_only_new_audits(before, after, {
            ("procurement.stock_cover_bound", line_id), ("warehouse.reserved_for_procurement", lot.id),
        })
    else:  # the release / cancel won: no lot was touched, no reserve without a holder
        assert (held["lot_id"], held["quantity"]) == (None, 0)
        assert stored == lot
        event = "procurement.stock_cover_released" if other == "release" else "procurement.cancelled"
        assert_only_new_audits(before, after, {(event, line_id)})


@pytest.mark.parametrize("hold", [None, "procurement first", "kit first"])
def test_two_connections_kit_reserve_against_procurement_cover_on_one_lot(env, monkeypatch, hold):
    client, sessions = env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("100"))
    request = kit(sessions, "700К.1 · КБ1 · Жгут", "50")  # another project: the kit cannot take the cover over
    before = state(sessions)

    procurement, kitting = race(
        monkeypatch, [(ProcurementLine, "cover_from_stock"), (fulfillment_application, "_kit_reserves")],
        [lambda: cover(client, line, lot, "80"), lambda: bind_kit(sessions, request, lot)],
        {None: None, "procurement first": "_kit_reserves", "kit first": "cover_from_stock"}[hold],
    )

    after = state(sessions)
    stored = get_lot(sessions, lot.id)
    assert stored.version == lot.version + 1  # exactly one writer moved the lot
    if hold is not None:
        assert procurement.status_code == (200 if hold == "procurement first" else 422), procurement.text
    if procurement.status_code == 200:
        # The kit wrote over a lot it read before the cover: refused (409 in the API). In a free race the stale write is
        # caught either by add() (StockVersionConflict) or by the ORM's version-guarded UPDATE at flush (StaleDataError).
        assert isinstance(kitting, StaleDataError), kitting
        assert stored.reserved == Decimal("80")
        (only,) = after["covers"]
        assert (only["lot_id"], only["quantity"]) == (lot.id, Decimal("80"))
        assert [row["reserved"] for row in fulfillment_lines(sessions)] == [0]
        assert_only_new_audits(before, after, {
            ("procurement.stock_covered", UUID(line["id"])), ("warehouse.reserved_for_procurement", lot.id),
        })
    else:
        # The kit won; the cover's single retry read the new lot and found only 50 available.
        assert procurement.status_code == 422, procurement.text
        assert "доступно 50 шт" in procurement.json()["detail"]
        assert not isinstance(kitting, Exception), kitting
        assert stored.reserved == Decimal("50")
        assert after["covers"] == [] and after["lines"] == before["lines"]
        assert [row["reserved"] for row in fulfillment_lines(sessions)] == [Decimal("50")]
        assert after["outbox"] == before["outbox"]
        added = {row["event_type"] for row in after["audit"]} - {row["event_type"] for row in before["audit"]}
        assert added == {"fulfillment.lot_bound", "fulfillment.completed"}  # fully reserved → auto-complete
