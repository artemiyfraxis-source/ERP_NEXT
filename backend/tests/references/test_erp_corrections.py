"""Правки справочников, которые ERP вносит сама поверх зеркала SUPPLYMAP (app.modules.references.erp_corrections)."""

from app.modules.references.domain import TechOperationTab
from app.modules.references.application import EditRecordCommand, FieldEdit, edit_record
from app.modules.references.erp_corrections import ACTOR, CORRECTIONS, apply_corrections
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import load_mirror

from .test_load_mirror import FILES, NOW, OP_HEADER, TER_HEADER, by_header, mirror, sessions, tsv  # noqa: F401

TER_FIX = tuple(item for item in CORRECTIONS if item.key == "SSH-003T-P0.2-H")
PRINT_MARK_FIX = tuple(item for item in CORRECTIONS if item.key == "PRINT_MARK")
SPP = "SPP-L410/Принтер маркировочный портативный"
TP80E = "TP80E/Принтер кабельных маркеров и термоусаживаемых трубок"


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

    report = apply_corrections(factory, NOW, TER_FIX)

    assert report == ["SSH-003T-P0.2-H: Аппликатор «S9/M1» → «S9/M1/A19» "
                      "(аппликатор автомата A19 — как у SSH-003T-P0.2 (один аппликатор JST))"]
    record = _terminal(factory)
    assert (record.fields["Аппликатор"], record.fields["terApplicator"], record.edited_in_erp) == ("S9/M1/A19", "S9/M1/A19", True)
    assert apply_corrections(factory, NOW, TER_FIX) == []

    load_mirror(mirror(tmp_path / "b", **{"БД.ТЕР": _ter("S9/M1")}), factory(), dry_run=False, now=NOW)
    assert _terminal(factory).fields["terApplicator"] == "S9/M1/A19"


def test_leaves_a_record_that_is_not_what_the_mirror_had(tmp_path, sessions):
    factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)  # noqa: E731
    load_mirror(mirror(tmp_path, **{"БД.ТЕР": _ter("S9")}), factory(), dry_run=False, now=NOW)

    [line] = apply_corrections(factory, NOW, TER_FIX)

    assert "не трогаю" in line
    record = _terminal(factory)
    assert (record.fields["terApplicator"], record.edited_in_erp) == ("S9", False)


def test_missing_terminal_is_reported_not_created(tmp_path, sessions):
    factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)  # noqa: E731
    load_mirror(mirror(tmp_path), factory(), dry_run=False, now=NOW)

    assert apply_corrections(factory, NOW, CORRECTIONS) == ["SSH-003T-P0.2-H: записей 0, правка не нужна или неоднозначна",
                                                            "PRINT_MARK: записей 0, правка не нужна или неоднозначна"]


def _op_mirror(machine: str) -> str:
    return tsv(
        [" ", "", "БД операции", "БД поля"], OP_HEADER,
        by_header(OP_HEADER, **{"Номер": "PRINT_MARK", "Название": "Печать маркировок", "Машина": machine,
                                "Полуфабрикат": "Термоусадка на трубке", "Время Операции": "2,00",
                                "Время подготовки, сек": "600", "Тип операции": "Статичный"}),
        by_header(OP_HEADER, **{"Номер": "PRINT_MARK_TUBE", "Название": "Монтаж маркировки",
                                "Машина": "ZCUT-RT660/Маркиратор проводов трубчатый", "Тип операции": "Статичный"}),
    )


def _print_mark(factory):
    with factory() as uow:
        [record] = [item for item in uow.tech_operations.list(tab=TechOperationTab.OPERATION, query="", limit=1000)
                    if item.fields.get("opNumber") == "PRINT_MARK"]
    return record


def test_print_mark_is_relinked_from_spp_l410_to_tp80e_once_and_keeps_it_over_a_mirror_reload(tmp_path, sessions):
    """Владелец 20.11: маркировку проводов печатает TP80E ОБ.057; строка БД.ОП PRINT_MARK пришла с SPP-L410."""
    factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)  # noqa: E731
    load_mirror(mirror(tmp_path / "a", **{"БД.ОП": _op_mirror(SPP)}), factory(), dry_run=False, now=NOW)

    [line] = apply_corrections(factory, NOW, PRINT_MARK_FIX)

    assert line.startswith(f"PRINT_MARK: Машина «{SPP}» → «{TP80E}»")
    record = _print_mark(factory)
    assert (record.fields["Машина"], record.edited_in_erp) == (TP80E, True)
    assert apply_corrections(factory, NOW, PRINT_MARK_FIX) == []

    load_mirror(mirror(tmp_path / "b", **{"БД.ОП": _op_mirror(SPP)}), factory(), dry_run=False, now=NOW)
    assert _print_mark(factory).fields["Машина"] == TP80E
    assert apply_corrections(factory, NOW, PRINT_MARK_FIX) == []


def test_print_mark_edited_in_erp_is_left_alone(tmp_path, sessions):
    factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)  # noqa: E731
    load_mirror(mirror(tmp_path, **{"БД.ОП": _op_mirror(SPP)}), factory(), dry_run=False, now=NOW)
    record = _print_mark(factory)
    other = "ZCUT-RT430/Маркиратор проводов флашковый"
    edit_record(EditRecordCommand(record.id, (FieldEdit("Машина", SPP, other),), ACTOR, NOW), factory())

    [line] = apply_corrections(factory, NOW, PRINT_MARK_FIX)

    assert "не трогаю" in line
    assert _print_mark(factory).fields["Машина"] == other


def test_print_mark_with_another_machine_from_the_mirror_is_left_alone(tmp_path, sessions):
    factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)  # noqa: E731
    load_mirror(mirror(tmp_path, **{"БД.ОП": _op_mirror("BMP21/Принтер")}), factory(), dry_run=False, now=NOW)

    [line] = apply_corrections(factory, NOW, PRINT_MARK_FIX)

    assert "не трогаю" in line
    assert (_print_mark(factory).fields["Машина"], _print_mark(factory).edited_in_erp) == ("BMP21/Принтер", False)
