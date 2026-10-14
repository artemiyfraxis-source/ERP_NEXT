"""«Оболочки»: тело загрузки `backend/data/cables/mat-coverings-gaps.json` (`POST /api/references/import/mat`) закрывает
пробелы гофры DKC/ДКС по страницам из их же «Ссылка на datasheet» и не перетирает ячейку, которую уже заполнили."""

import json
from datetime import datetime
from pathlib import Path

from app.modules.references.coverings import build
from app.modules.references.domain import TechOperationTab
from app.modules.references.importers import import_rows
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import ACTOR, load_mirror
from app.shared.bulk_import import ImportRowIn
from tests.references.test_load_mirror import sessions  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = ROOT / "data" / "cables" / "mat-coverings-gaps.json"
NOW = datetime(2026, 10, 13, 12, 0)


def _rows(payload: dict) -> list[ImportRowIn]:
    return [ImportRowIn.model_validate(row) for row in payload["rows"]]


def _coverings(factory) -> dict[str, dict]:
    with factory() as uow:
        records = uow.tech_operations.list(tab=TechOperationTab.MATERIAL, query="", limit=10_000)
    return {row["name"]: row for row in build([(str(item.id), dict(item.fields)) for item in records])["rows"]}


def test_the_payload_fills_the_gaps_and_a_second_run_changes_nothing(sessions):  # noqa: F811
    factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)  # noqa: E731
    load_mirror(ROOT / "data" / "reference-mirror", factory(), dry_run=False, now=NOW)
    payload = json.loads(PAYLOAD.read_text(encoding="utf-8"))

    check = import_rows("mat", factory(), _rows(payload), ACTOR, NOW, dry_run=True)
    assert not check.refused and len(check.updated) == 3 and not check.created
    assert _coverings(factory)["Трубка гофрированная 4,6мм"]["outer_mm"] is None  # the check wrote nothing

    import_rows("mat", factory(), _rows(payload), ACTOR, NOW, dry_run=False)
    rows = _coverings(factory)
    assert (rows["Трубка гофрированная 4,6мм"]["inner_mm"], rows["Трубка гофрированная 4,6мм"]["outer_mm"]) == (4.6, 7.1)
    assert (rows["Трубка гофрированная 9,8мм"]["outer_mm"], rows["Трубка гофрированная 9,8мм"]["temp_min_c"]) == (12.7, -40)
    assert rows["Гофра 6,8мм"]["split"] is True

    again = import_rows("mat", factory(), _rows(payload), ACTOR, NOW, dry_run=True)
    assert again.refused and not again.updated  # the cells are no longer empty: «old» refuses a stale load
