"""Moving the existing ETKs onto ПФ (owner 13.10: «старые сам переделай»): `python -m app.workflows.pf_split_existing`
— a dry run prints the split, --apply writes it as a revision «Правка ПФ (перенос на ПФ)» through the «Сохранить ПФ»
path, skips «Сложная сборка» and a running ПФ task it would change, and writes nothing the second time."""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.operation_cards import parse_operation_library
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.identity import Actor
from app.shared.persistence import create_schema
from app.workflows import pf_split_existing as cli


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
FIXTURES = json.loads((ROOT / "tests" / "techcards" / "fixtures" / "current-etk.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"])
NOW = datetime(2026, 10, 13, 18, 0)
ACTOR = Actor(id=uuid4(), name="перенос ЭТК на ПФ", department="")


def document(name: str) -> dict:
    config = deepcopy(FIXTURES["files"][name]["payload"]["config"])
    return build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])


@pytest.fixture()
def shop():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)  # noqa: E731
    app.state.techcards_uow_factory = factory
    app.dependency_overrides[moscow_now] = lambda: NOW
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=UUID("30000000-0000-0000-0000-000000000001"), name="Технолог", department="Производство"),
        email="tech@example.test", roles=frozenset({"engineer"}), permissions={"techcards": "edit"},
    )
    yield SimpleNamespace(api=TestClient(app), factory=factory, sessions=sessions)
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.techcards_uow_factory
    engine.dispose()


def issued(api: TestClient, item: str, etk: dict) -> dict:
    card = api.post("/api/techcards/projects", json={
        "project": "720К.", "item_index": item, "product_name": "Жгут", "source_calculation": "",
    }).json()
    built = api.put(f"/api/techcards/projects/{card['id']}/etk", json={"version": card["version"], "document": etk})
    assert built.status_code == 200, built.text
    result = api.post(f"/api/techcards/projects/{card['id']}/issue", json={"version": built.json()["version"]})
    assert result.status_code == 200, result.text
    return result.json()


def run(shop, apply: bool, tasks=(), **options) -> tuple[str, int]:
    return cli.run(shop.factory, lambda: list(tasks), ACTOR, NOW, apply=apply, **options)


def card(shop, card_id: str) -> dict:
    return shop.api.get(f"/api/techcards/projects/{card_id}").json()


# Cut, crimp A, side A, crimp B, side B (owner 14.10) — a ПФ per operation, as the derived scheme, but named ПФ.
SPLIT = ("ПФ-1 · Нарезанные провода (01) · ПФ-2 · Обжатые провода, сторона А (02) · ПФ-3 · Сторона А собрана (03) · "
         "ПФ-4 · Обжатые провода, сторона В (04) · ПФ-5 · Кабельная сборка 39012140 to 39012141 16AWG 250mm (05)")


def test_dry_run_prints_the_split_and_writes_nothing(shop):
    typical = issued(shop.api, "КБ1", document("kit-720К.-КБ1.json"))
    text, code = run(shop, apply=False)

    assert code == 0
    assert f"720К. · КБ1 · Выдана ред. 1 · 5 оп. → {SPLIT}" in text
    assert "Перенести: 1 · уже по ПФ: 0 · пропущено: 0" in text
    assert "Проверка без записи" in text
    after = card(shop, typical["id"])
    assert (after["version"], after["revision"], after["document"]["route"]["source"]) == (typical["version"], 1, "derived")


def test_apply_writes_a_revision_once_and_a_second_apply_writes_nothing(shop):
    typical = issued(shop.api, "КБ1", document("kit-720К.-КБ1.json"))
    text, code = run(shop, apply=True)

    assert code == 0 and f"перенесено {SPLIT}" in text and "Перенесено: 1" in text
    after = card(shop, typical["id"])
    assert after["revision"] == 2 and after["document"]["route"]["source"] == "generated"
    assert [block["ops"] for block in after["document"]["route"]["blocks"]] == [[1], [2], [3], [4], [5]]
    revisions = shop.api.get(f"/api/techcards/projects/{typical['id']}/revisions").json()["items"]
    moved = next(item for item in revisions if item["revision"] == 2)
    assert moved["reason"] == "Правка ПФ (перенос на ПФ)" and moved["actor_name"] == "перенос ЭТК на ПФ"

    again, code = run(shop, apply=True)
    assert code == 0 and "Перенесено: 0 · уже по ПФ: 1 · пропущено: 0" in again
    assert "720К. · КБ1 · Выдана ред. 2 · 5 оп. — уже по ПФ" in again
    assert card(shop, typical["id"])["version"] == after["version"]


def test_complex_assembly_is_skipped_unless_asked(shop):
    complex_card = issued(shop.api, "КБ2", document("kit-708К.2-КБ1.json"))
    text, _ = run(shop, apply=True)

    assert "720К. · КБ2 · Выдана ред. 1 · 15 оп. — Сложная сборка — ПФ настраивает владелец" in text
    assert card(shop, complex_card["id"])["revision"] == 1

    text, _ = run(shop, apply=True, include_complex=True, card_id=UUID(complex_card["id"]))
    assert "Перенесено: 1" in text
    assert card(shop, complex_card["id"])["document"]["route"]["source"] == "generated"


def test_a_running_pf_task_that_would_change_skips_the_card_and_a_task_by_operations_does_not(shop):
    typical = issued(shop.api, "КБ1", document("kit-720К.-КБ1.json"))

    def task(number: str, *compositions):
        operations = [SimpleNamespace(etk_ops=tuple(SimpleNamespace(position=p) for p in ops)) for ops in compositions]
        return SimpleNamespace(number=number, techcard_id=UUID(typical["id"]), techcard_revision=1,
                               project="720К.", item="КБ1", operations=operations)

    by_pf = task("ПЗ-7", (1,), (2, 3), (4, 5))
    text, _ = run(shop, apply=True, tasks=[by_pf])
    assert "идёт задание ПЗ-7 по ПФ этой ревизии — состав ПФ стал бы другим" in text
    assert card(shop, typical["id"])["revision"] == 1

    by_operations = task("ПЗ-8", (), (), (), (), ())
    text, _ = run(shop, apply=True, tasks=[by_operations])
    assert "идут задания ПЗ-8 по операциям ЭТК — остаются по своему снимку" in text
    assert card(shop, typical["id"])["revision"] == 2


def test_card_filter_and_unknown_card(shop):
    first = issued(shop.api, "КБ1", document("kit-720К.-КБ1.json"))
    issued(shop.api, "КБ3", document("kit-729К.2-КБ1.json"))

    text, code = run(shop, apply=False, card_id=UUID(first["id"]))
    assert code == 0 and "КБ1" in text and "КБ3" not in text
    text, code = run(shop, apply=False, card_id=UUID("00000000-0000-0000-0000-000000000009"))
    assert code == 2 and "нет" in text


def test_main_needs_the_database_url_and_a_mode(monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    assert cli.main(["--dry-run"]) == 2
    with pytest.raises(SystemExit):
        cli.main([])
    # --regenerate is a dry run by default; --project only goes with it.
    assert cli.main(["--regenerate"]) == 2
    with pytest.raises(SystemExit):
        cli.main(["--dry-run", "--project", "720К."])


# ── --regenerate (owner 14.10): issued generator ETKs onto the current ПФ rule and texts ──

OLD_SIDE_A = "Разъём Molex 39012140 смонтирован (ст. А, 2 провода)"
# The rule before PR #377: both sides' hand crimps in one ПФ «Опрессовка терминалов» (ops 02 and 04).
OLD_ROUTE = {
    "blocks": [
        {"key": "wires", "name": "Нарезанные провода", "ops": [1]},
        {"key": "crimped", "name": "Опрессовка терминалов", "ops": [2, 4]},
        {"key": "A", "name": "Сторона А собрана", "ops": [3]},
        {"key": "B", "name": "Кабельная сборка 39012140 to 39012141 16AWG 250mm", "ops": [5], "final": True},
    ],
    "edges": [{"from": "wires", "to": "crimped", "per_unit": 1}, {"from": "crimped", "to": "A", "per_unit": 1},
              {"from": "crimped", "to": "B", "per_unit": 1}, {"from": "A", "to": "B", "per_unit": 1}],
}
OLD_SPLIT = ("ПФ-1 · Нарезанные провода (01) · ПФ-2 · Опрессовка терминалов (02,04) · ПФ-3 · Сторона А собрана (03) · "
             "ПФ-4 · Кабельная сборка 39012140 to 39012141 16AWG 250mm (05)")


@pytest.fixture()
def library(shop):
    from app.modules.techcards.application import ReplaceOperationLibraryCommand, replace_operation_library

    replace_operation_library(ReplaceOperationLibraryCommand(
        source_commit=LIBRARY["sourceCommit"], templates=tuple(LIBRARY["templates"]), actor=ACTOR, now=NOW,
    ), shop.factory())
    return shop


def old_generated(name: str = "kit-720К.-КБ1.json", **config) -> dict:
    """An ETK the typical tab issued before 14.10 13:00: both crimps in one ПФ, the old wording of side A."""
    from app.modules.techcards.application import _hand_route

    payload = {**deepcopy(FIXTURES["files"][name]["payload"]["config"]), "tab": "inter", **config}
    etk = build_etk(payload, CARDS, operation_library_commit=LIBRARY["sourceCommit"])
    etk["route"] = _hand_route(etk, OLD_ROUTE, generated=True)
    etk["operations"][2]["result"][0]["name"] = OLD_SIDE_A
    etk["operations"][3]["semis"][0]["name"] = OLD_SIDE_A
    return etk


def regenerate(shop, apply: bool, tasks=(), **options) -> tuple[str, int]:
    return cli.run_regenerate(shop.factory, lambda: list(tasks), ACTOR, NOW, apply=apply, **options)


def test_regenerate_moves_an_old_two_crimps_pf_onto_a_crimp_per_side_with_new_names(library):
    old = issued(library.api, "КБ3", old_generated())
    text, code = regenerate(library, apply=False)

    assert code == 0
    assert "720К. · КБ3 · Выдана ред. 1 · 5 оп. → перестроить" in text
    assert f"было:   {OLD_SPLIT}" in text and f"станет: {SPLIT}" in text
    assert "тексты меняются у 2 из 5 оп." in text
    assert "Перестроить: 1 · без изменений: 0 · пропущено: 0" in text and "Проверка без записи" in text
    unchanged = card(library, old["id"])
    assert (unchanged["version"], unchanged["revision"]) == (old["version"], 1)
    assert [block["ops"] for block in unchanged["document"]["route"]["blocks"]] == [[1], [2, 4], [3], [5]]

    text, code = regenerate(library, apply=True)
    assert code == 0 and "перестроено" in text and "новая ревизия 2" in text and "Перестроено: 1" in text
    after = card(library, old["id"])
    route = after["document"]["route"]
    assert after["revision"] == 2 and route["source"] == "generated"
    assert [block["ops"] for block in route["blocks"]] == [[1], [2], [3], [4], [5]]
    assert [block["name"] for block in route["blocks"]][1:4] == [
        "Обжатые провода, сторона А", "Сторона А собрана", "Обжатые провода, сторона В"]
    assert after["document"]["operations"][2]["result"][0]["name"] == "Сторона А собрана: провода вставлены в разъём Molex 39012140"
    assert "route" not in after["document"]["build_config"] and after["document"]["build_config"]["tab"] == "inter"
    revisions = library.api.get(f"/api/techcards/projects/{old['id']}/revisions").json()["items"]
    rebuilt = next(item for item in revisions if item["revision"] == 2)
    assert (rebuilt["reason"], rebuilt["actor_name"]) == (cli.REGEN_REASON, ACTOR.name)
    from sqlalchemy import select

    from app.shared.persistence import AuditEventRow

    with library.sessions() as session:
        events = session.scalars(select(AuditEventRow).where(
            AuditEventRow.event_type == "techcards.project_auto_built",
            AuditEventRow.aggregate_id == UUID(old["id"]))).all()
    assert [(event.actor_name, event.details["reason"]) for event in events] == [(ACTOR.name, cli.REGEN_REASON)]

    again, code = regenerate(library, apply=True)
    assert code == 0 and "720К. · КБ3 · Выдана ред. 2 · 5 оп. — без изменений" in again
    assert "Перестроено: 0 · без изменений: 1 · пропущено: 0" in again
    assert card(library, old["id"])["version"] == after["version"]


def test_regenerate_leaves_hand_edited_schemes_and_other_tabs_alone(library):
    by_hand = old_generated()
    by_hand["route"]["source"] = "manual"
    hand = issued(library.api, "КБ1", by_hand)
    ordered = issued(library.api, "КБ2", old_generated(opOrder=["a", "b"]))
    complex_card = issued(library.api, "КБ4", document("kit-708К.2-КБ1.json"))
    text, _ = regenerate(library, apply=True)

    assert "720К. · КБ1 · Выдана ред. 1 · 5 оп. — схема ПФ правлена вручную — не трогаю" in text
    assert "720К. · КБ2 · Выдана ред. 1 · 5 оп. — свой порядок операций — не трогаю" in text
    assert "720К. · КБ4 · Выдана ред. 1 · 15 оп. — Сложная сборка — ПФ настраивает владелец" in text
    assert "Перестроено: 0 · без изменений: 0 · пропущено: 3" in text
    assert [card(library, item["id"])["revision"] for item in (hand, ordered, complex_card)] == [1, 1, 1]


def test_regenerate_skips_an_etk_with_a_running_pf_task_and_keeps_a_task_by_operations(library):
    old = issued(library.api, "КБ3", old_generated())

    def task(number: str, *compositions, revision: int = 1):
        operations = [SimpleNamespace(etk_ops=tuple(SimpleNamespace(position=p) for p in ops)) for ops in compositions]
        return SimpleNamespace(number=number, techcard_id=UUID(old["id"]), techcard_revision=revision,
                               project="720К.", item="КБ3", operations=operations)

    text, _ = regenerate(library, apply=True, tasks=[task("ПЗ-7", (1,), (2, 4), (3,), (5,))])
    assert "720К. · КБ3 · Выдана ред. 1 · 5 оп. — идёт задание ПЗ-7 по ПФ — перестроить после его закрытия" in text
    assert card(library, old["id"])["revision"] == 1

    text, _ = regenerate(library, apply=True, tasks=[task("ПЗ-8", (), (), (), (), ())])
    assert "идут задания ПЗ-8 по операциям ЭТК — остаются по своему снимку" in text
    assert card(library, old["id"])["revision"] == 2


def test_regenerate_project_filter(library):
    issued(library.api, "КБ3", old_generated())
    text, code = regenerate(library, apply=False, project="999К.")
    assert code == 0 and "КБ3" not in text and "Перестроить: 0" in text
    text, _ = regenerate(library, apply=False, project="720к")
    assert "720К. · КБ3" in text
