"""shared/bulk_import.py over a fake in-memory table: dry-run writes nothing, a repeated apply changes nothing, a bad
or stale row is refused while the rest applies, nothing is deleted, null leaves a field as is."""

import json
from datetime import datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.shared.bulk_import import (
    DUPLICATE_REASON,
    MAX_IMPORT_ROWS,
    STALE_REASON,
    ImportRateLimited,
    ImportRateLimiter,
    ImportRequest,
    ImportRowIn,
    ImportTable,
    ImportTooLarge,
    RowRefused,
    row_key,
    run_import,
)
from app.shared.clock import MOSCOW
from app.shared.identity import Actor

NOW = datetime(2026, 10, 12, 10, 0, tzinfo=MOSCOW)
TOKEN = Actor(id=UUID(int=7), name="API: Загрузчик разъёмов", department="Токен API")
CONNECTORS = ImportTable(
    name="connectors", key_fields=("Артикул",), fields=frozenset({"Артикул", "Контактов", "Шаг", "Серия"}),
    event_type="references.connectors_imported", aggregate_id=UUID(int=10),
)


class FakeTable:
    """A table with a transaction: writes go to a working copy that commit publishes and rollback drops."""

    def __init__(self, table: ImportTable, rows: list[dict]) -> None:
        self.table = table
        self.stored = {row_key(table, row): dict(row) for row in rows}
        self.events: list = []
        self._work = {key: dict(row) for key, row in self.stored.items()}
        self._pending: list = []
        self.commits = 0
        self.refuse: set[str] = set()

    def existing(self, keys):
        return {key: dict(self._work[key]) for key in keys if key in self._work}

    def create(self, key, fields):
        if key in self.refuse:
            raise RowRefused("отклонено правилом таблицы")
        self._work[key] = dict(fields)

    def update(self, key, changes):
        if key in self.refuse:
            raise RowRefused("отклонено правилом таблицы")
        self._work[key].update(changes)

    def audit(self, event):
        self._pending.append(event)

    def commit(self):
        self.stored = {key: dict(row) for key, row in self._work.items()}
        self.events += self._pending
        self._pending = []
        self.commits += 1

    def rollback(self):
        self._work = {key: dict(row) for key, row in self.stored.items()}
        self._pending = []


def _rows(*rows) -> list[ImportRowIn]:
    return [ImportRowIn(**row) for row in rows]


def _run(target, rows, *, dry_run=False, table=CONNECTORS, **kwargs):
    return run_import(table, rows, target, actor=TOKEN, now=NOW, dry_run=dry_run, limiter=None, **kwargs)


SEED = [{"Артикул": "ZHR-5", "Контактов": "5", "Шаг": "1.5", "Серия": "ZH"}]
LOAD = _rows(
    {"fields": {"Артикул": "ZHR-5", "Контактов": "5", "Шаг": "1,5"}},
    {"fields": {"Артикул": "PHR-2", "Контактов": 2, "Шаг": 2.0, "Серия": "  PH "}},
)


def test_dry_run_reports_what_the_apply_does_and_writes_nothing():
    target = FakeTable(CONNECTORS, SEED)

    preview = _run(target, LOAD, dry_run=True)
    assert target.stored == {"ZHR-5": SEED[0]} and target.events == [] and target.commits == 0

    applied = _run(target, LOAD)
    assert preview.model_dump(exclude={"dry_run"}) == applied.model_dump(exclude={"dry_run"})
    assert preview.dry_run is True and applied.dry_run is False
    assert applied.created == ["PHR-2"]
    assert [(item.key, item.changes) for item in applied.updated] == [("ZHR-5", {"Шаг": ("1.5", "1,5")})]
    assert applied.unchanged == 0 and applied.refused == []
    assert target.stored["PHR-2"] == {"Артикул": "PHR-2", "Контактов": "2", "Шаг": "2", "Серия": "PH"}
    assert target.stored["ZHR-5"] == {**SEED[0], "Шаг": "1,5"}


def test_the_report_is_json_with_old_new_pairs():
    report = _run(FakeTable(CONNECTORS, SEED), LOAD, dry_run=True)
    assert json.loads(report.model_dump_json()) == {
        "dry_run": True,
        "created": ["PHR-2"],
        "updated": [{"key": "ZHR-5", "changes": {"Шаг": ["1.5", "1,5"]}}],
        "unchanged": 0,
        "refused": [],
    }


def test_a_second_apply_changes_nothing_and_writes_no_audit():
    target = FakeTable(CONNECTORS, SEED)
    _run(target, LOAD)
    assert len(target.events) == 1 and target.commits == 1

    again = _run(target, LOAD)
    assert (again.created, again.updated, again.unchanged, again.refused) == ([], [], 2, [])
    assert len(target.events) == 1 and target.commits == 1


def test_one_audit_event_per_apply_carries_the_principal():
    target = FakeTable(CONNECTORS, SEED)
    _run(target, LOAD)
    [event] = target.events
    assert event.actor == TOKEN and event.actor.name == "API: Загрузчик разъёмов"
    assert event.event_type == "references.connectors_imported" and event.aggregate_id == UUID(int=10)
    assert event.occurred_at == NOW
    assert event.details == {
        "table": "connectors", "created": "1", "updated": "1", "unchanged": "0", "refused": "0",
        "keys": ["ZHR-5", "PHR-2"],
    }


def test_bad_rows_are_refused_and_the_rest_still_applies():
    target = FakeTable(CONNECTORS, SEED)
    target.refuse = {"BAD-1"}
    report = _run(target, _rows(
        {"fields": {"Артикул": "XH-3", "Цвет": "белый"}},
        {"fields": {"Контактов": "3"}},
        {"fields": {"Артикул": "XH-4", "Контактов": True}},
        {"fields": {"Артикул": "XH-5", "Шаг": {"мм": 2.5}}},
        {"fields": {"Артикул": "BAD-1", "Контактов": "1"}},
        {"fields": {"Артикул": "GOOD-1", "Контактов": "1"}},
    ))
    assert report.created == ["GOOD-1"]
    assert {(item.key, item.reason) for item in report.refused} == {
        ("XH-3", "неизвестное поле: Цвет"),
        ("#2", "не заполнен ключ: Артикул"),
        ("XH-4", "Контактов: ожидается текст или число"),
        ("XH-5", "Шаг: ожидается текст или число"),
        ("BAD-1", "отклонено правилом таблицы"),
    }
    assert set(target.stored) == {"ZHR-5", "GOOD-1"}


def test_a_row_whose_old_value_changed_since_the_export_is_refused():
    target = FakeTable(CONNECTORS, SEED)
    report = _run(target, _rows(
        {"fields": {"Артикул": "ZHR-5", "Контактов": "6"}, "old": {"Контактов": "4"}},
        {"fields": {"Артикул": "PHR-2", "Контактов": "2"}, "old": {"Контактов": "1"}},
    ))
    assert [(item.key, item.reason) for item in report.refused] == [("ZHR-5", STALE_REASON), ("PHR-2", STALE_REASON)]
    assert report.created == [] and report.updated == [] and target.commits == 0
    assert target.stored == {"ZHR-5": SEED[0]}

    matching = _run(target, _rows(
        {"fields": {"Артикул": "ZHR-5", "Контактов": "6"}, "old": {"Контактов": " 5 ", "Серия": "ZH"}},
        {"fields": {"Артикул": "PHR-2", "Контактов": "2"}, "old": {"Контактов": None}},
    ))
    assert matching.refused == [] and matching.created == ["PHR-2"]
    assert target.stored["ZHR-5"]["Контактов"] == "6"


def test_nothing_is_deleted_null_keeps_a_field_and_empty_text_clears_it():
    target = FakeTable(CONNECTORS, [*SEED, {"Артикул": "VH-2", "Контактов": "2"}])
    report = _run(target, _rows({"fields": {"Артикул": "ZHR-5", "Контактов": None, "Серия": ""}}))
    assert [(item.key, item.changes) for item in report.updated] == [("ZHR-5", {"Серия": ("ZH", "")})]
    assert target.stored["ZHR-5"] == {**SEED[0], "Серия": ""}
    assert target.stored["VH-2"] == {"Артикул": "VH-2", "Контактов": "2"}


def test_a_key_repeated_in_one_load_refuses_every_copy():
    target = FakeTable(CONNECTORS, [])
    report = _run(target, _rows(
        {"fields": {"Артикул": "A-1", "Контактов": "1"}},
        {"fields": {"Артикул": "A-1", "Контактов": "2"}},
        {"fields": {"Артикул": "B-1"}},
    ))
    assert report.created == ["B-1"]
    assert [(item.key, item.reason) for item in report.refused] == [("A-1", DUPLICATE_REASON)] * 2


def test_a_composite_key_matches_on_every_key_field():
    table = ImportTable(
        name="contacts", key_fields=("Серия", "Тип"), fields=frozenset({"Серия", "Тип", "Сечение"}),
        event_type="references.contacts_imported", aggregate_id=UUID(int=10),
    )
    target = FakeTable(table, [{"Серия": "ZH", "Тип": "гнездо", "Сечение": "0,08-0,2"}])
    report = _run(target, _rows(
        {"fields": {"Серия": "ZH", "Тип": "гнездо", "Сечение": "0,05-0,2"}},
        {"fields": {"Серия": "ZH", "Тип": "штырь", "Сечение": "0,05-0,2"}},
    ), table=table)
    assert report.created == ["ZH / штырь"] and [item.key for item in report.updated] == ["ZH / гнездо"]


def test_a_failing_target_rolls_back_and_the_error_surfaces():
    class Broken(FakeTable):
        def update(self, key, changes):
            super().update(key, changes)
            raise RuntimeError("БД недоступна")

    target = Broken(CONNECTORS, SEED)
    with pytest.raises(RuntimeError):
        _run(target, LOAD)
    assert target.stored == {"ZHR-5": SEED[0]} and target.existing(["ZHR-5", "PHR-2"]) == {"ZHR-5": SEED[0]}


def test_row_count_is_capped_in_the_request_and_in_the_import():
    row = {"fields": {"Артикул": "A"}}
    assert len(ImportRequest(rows=[row] * MAX_IMPORT_ROWS).rows) == MAX_IMPORT_ROWS
    with pytest.raises(ValidationError):
        ImportRequest(rows=[row] * (MAX_IMPORT_ROWS + 1))
    with pytest.raises(ValidationError):
        ImportRequest(rows=[])
    with pytest.raises(ImportTooLarge) as refused:
        _run(FakeTable(CONNECTORS, []), _rows(row) * (MAX_IMPORT_ROWS + 1))
    assert refused.value.status_code == 413


def test_the_rate_limit_counts_calls_per_principal_within_the_window():
    clock = [100.0]
    limiter = ImportRateLimiter(max_calls=2, window_seconds=60, clock=lambda: clock[0])
    target = FakeTable(CONNECTORS, [])
    rows = _rows({"fields": {"Артикул": "A"}})

    def call(actor=TOKEN):
        return run_import(CONNECTORS, rows, target, actor=actor, now=NOW, dry_run=True, limiter=limiter)

    call(), call()
    with pytest.raises(ImportRateLimited) as limited:
        call()
    assert limited.value.status_code == 429
    call(Actor(id=UUID(int=8), name="Технолог", department="ИТ"))  # another principal has its own counter
    clock[0] += 61
    call()


def test_a_table_key_must_be_among_its_fields():
    with pytest.raises(ValueError):
        ImportTable(name="x", key_fields=("Код",), fields=frozenset({"Имя"}), event_type="x", aggregate_id=UUID(int=1))


def test_the_import_path_has_its_own_body_ceiling():
    from fastapi.testclient import TestClient

    from app.main import app
    from app.shared.bulk_import import MAX_IMPORT_BODY_BYTES

    client = TestClient(app)
    two_mib = b"x" * (2 * 1_048_576)
    assert client.post("/api/references/import/connectors?dry_run=1", content=two_mib).status_code != 413
    assert client.post("/api/references/tech-operations", content=two_mib).status_code == 413
    too_big = b"x" * (MAX_IMPORT_BODY_BYTES + 1)
    assert client.post("/api/references/import/connectors?dry_run=1", content=too_big).status_code == 413
