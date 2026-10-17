"""Правки справочников, которые ERP вносит сама поверх зеркала SUPPLYMAP (app.modules.references.erp_corrections)."""

from app.modules.references.domain import TechOperationTab
from app.modules.references.erp_corrections import CORRECTIONS, apply_corrections
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import load_mirror

from .test_load_mirror import FILES, NOW, TER_HEADER, by_header, mirror, sessions, tsv  # noqa: F401


def _ter(applicator: str) -> str:
    row = by_header(TER_HEADER, **{
        "Тип\nразъема": "Обжимной контакт", "Производитель": "JST", "Product Name": "SH", "Series": "SHR",
        "Шаг\nразьема": "1", "Артикул\n контакта (REEL)": "SSH-003T-P0.2-H", "Аппликатор": applicator,
        "Высота обжима проводника , мм": "0,43 - 0,47", "От AWG": "32", "До AWG": "28",
    })
    return tsv(TER_HEADER, row)


def _terminal(factory):
    with factory() as uow:
        [record] = [item for item in uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=1000)
                    if item.fields.get("terArticle") == "SSH-003T-P0.2-H"]
    return record


def test_adds_the_machine_applicator_to_ssh_003t_h_once_and_keeps_it_over_a_mirror_reload(tmp_path, sessions):
    factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)  # noqa: E731
    load_mirror(mirror(tmp_path / "a", **{"БД.ТЕР": _ter("S9/M1")}), factory(), dry_run=False, now=NOW)

    report = apply_corrections(factory, NOW)

    assert report == ["SSH-003T-P0.2-H: Аппликатор «S9/M1» → «S9/M1/A19» "
                      "(аппликатор автомата A19 — как у SSH-003T-P0.2 (один аппликатор JST))"]
    record = _terminal(factory)
    assert (record.fields["Аппликатор"], record.fields["terApplicator"], record.edited_in_erp) == ("S9/M1/A19", "S9/M1/A19", True)
    assert apply_corrections(factory, NOW) == []

    load_mirror(mirror(tmp_path / "b", **{"БД.ТЕР": _ter("S9/M1")}), factory(), dry_run=False, now=NOW)
    assert _terminal(factory).fields["terApplicator"] == "S9/M1/A19"


def test_leaves_a_record_that_is_not_what_the_mirror_had(tmp_path, sessions):
    factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)  # noqa: E731
    load_mirror(mirror(tmp_path, **{"БД.ТЕР": _ter("S9")}), factory(), dry_run=False, now=NOW)

    [line] = apply_corrections(factory, NOW)

    assert "не трогаю" in line
    record = _terminal(factory)
    assert (record.fields["terApplicator"], record.edited_in_erp) == ("S9", False)


def test_missing_terminal_is_reported_not_created(tmp_path, sessions):
    factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)  # noqa: E731
    load_mirror(mirror(tmp_path), factory(), dry_run=False, now=NOW)

    assert apply_corrections(factory, NOW, CORRECTIONS) == ["SSH-003T-P0.2-H: записей 0, правка не нужна или неоднозначна"]
