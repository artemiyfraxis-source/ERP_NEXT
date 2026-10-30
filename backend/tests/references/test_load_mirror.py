"""REF-MIRROR-LOAD / REF-MIRROR-ALL: SUPPLYMAP DB mirror `_local/db/*.tsv` → references snapshot."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.references.application import ReplaceSnapshotCommand, TechRecordInput, replace_snapshot
from app.modules.references.domain import CABLE_TABS, TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_mirror import ACTOR, MirrorLoadError, default_source, format_report, load_mirror, main, read_mirror
from app.modules.references.technology import import_supplymap_snapshot, schema_keys
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema

NOW = datetime(2026, 9, 29, 12, 0)


def tsv(*rows: list[str]) -> str:
    """SUPPLYMAP db.mjs `toTsv`: escape backslash, TAB and line break in a cell, drop trailing empty cells."""
    def line(row):
        cells = [cell.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n") for cell in row]
        while cells and cells[-1] == "":
            cells.pop()
        return "\t".join(cells)
    return "\n".join(line(row) for row in rows) + "\n"


def by_header(header: list[str], **cells: str) -> list[str]:
    return [cells.get(name, "") for name in header]


# Real sheet headers (SUPPLYMAP `_local/db`, 2026-09-29), a few rows each.
TER_HEADER = [
    "Тип\nразъема", "Производитель", "Product Name", "Series", "Шаг\nразьема", "Тип\n контакта",
    "Артикул\n контакта (REEL)", "Артикул\n контакта (BAG)", "Аппликатор", "Пневма\nАвтомат", "ОТР", "С зачисткой",
    "Длина зачистки, мм", "L+", "L-", "Высота обжима проводника , мм", "Высота обжима изоляции, мм",
    "Усилие обрыва контакта, N", "От AWG", "До AWG", "От мм2", "До мм2", "Диаметр изоляции от, мм",
    "Диаметр изоляции до, мм", "Материал контакта", "Максисмальная сила тока, А",
    "Ссылка на DATASHEET на сайте производителя", "Источник высоты обжима",
]
MICRO_FIT = dict(zip(TER_HEADER, [
    "Обжимной контакт", "Molex", "Micro-Fit 3.0", "43020, 43640", "3", "Male", "430310001", "430310007", "S8/M8",
    "", "TRUE", "TRUE", "2,54 - 2,92", "6", "", "24 AWG: 0,83 - 0,93\n22 AWG: 0,83 - 0,93\n20 AWG: 0,89 - 0,99",
    "24 AWG: 1,58\n22 AWG: 1,58\n20 AWG: 1,7", "24 AWG: 22,2\n22 AWG: 35,6\n20 AWG: 57,8", "24", "20", "0,2", "0,5",
    "1,3", "1,85", "Phosphor Bronze / Tin", "8,5", "https://www.molex.com/en-us/products/part-detail/430310001",
    "Molex ATS-638190000 rev E",
]))
TER = tsv(
    TER_HEADER,
    [MICRO_FIT[name] for name in TER_HEADER],
    by_header(TER_HEADER, **{"Тип\nразъема": "Обжимная клемма", "Производитель": "КВТ", "Product Name": "НВИ",
                             "Series": "НВИ 1.5", "Артикул\n контакта (REEL)": "НВИ 1.5-3", "Аппликатор": "M31",
                             "От AWG": "20", "До AWG": "16", "От мм2": "0,5", "До мм2": "1,5"}),
    [""] * 4,
    # Applicator registry below the table: article + code, no manufacturer / series / product name → not a record.
    by_header(TER_HEADER, **{"Артикул\n контакта (REEL)": "430310001", "Аппликатор": "A12"}),
)
OP_HEADER = [
    "Номер", "Название", "Апликаторы / модули", "Программа", "Машина", "Полуфабрикат", "Провод", "Разъем",
    "Инструмент", "Время Операции", "Время подготовки, сек", "Расход на настройку м; шт;",
    "Время Чел, сек/оп; сек/м", "Время машины, сек/оп; сек/м", "Тип операции",
]
OP = tsv(
    [" ", "", "БД операции", "БД поля"],
    OP_HEADER,
    by_header(OP_HEADER, **{"Номер": "CUT_WIRE_auto", "Название": "Резка", "Машина": "EW-05F+M/Автомат",
                            "Время Операции": "3,50", "Время подготовки, сек": "180",
                            "Время Чел, сек/оп; сек/м": "0,00", "Время машины, сек/оп; сек/м": "3,50",
                            "Тип операции": "Погонный"}),
    by_header(OP_HEADER, **{"Время Чел, сек/оп; сек/м": "0,00"}),  # ARRAYFORMULA tail row, no number/name
)
OB_HEADER = ["№", "Тип", "Инв. номер", "Артикул", "Наменование", "Для базы", "Стоимость", "Габариты\nШхД"]
OB = tsv(
    [""],
    OB_HEADER,
    ["1", "Инструмент", "ОБ.048", "DT-8920", "Дифманометр", "DT-8920/Дифманометр", "35 000,00", "10\nx 20"],
    ["2", "Оборудование", "ОБ.094", "BZW-886", "Станок", "", "", ""],  # «Для базы» empty → no record
)
COAX_HEADER = ["Артикул", "Тип/Серия", "Провод", "Производитель", "Программа ", "D1", "D2", "L+", "Тип пина"]
COAX = tsv(
    [""],
    COAX_HEADER,
    ["BNC-C58P", "BNC-male", "RG58", "RUICHI", "", "1", "2,95", "", "пайка"],
)
PROV = "Марка\tAWG\nRG316\t\nUL1061\t28\n\t\n"
MAT = "\ufeffНаименование\tГруппа\r\nТУТ 4/2\tТУТ\r\n"
META = {"БД.ТЕР": {"pulledAt": "2026-09-29T08:25"}, "БД.КОАКС": {"pulledAt": "2026-09-29T09:23"},
        "БД.ОП": {"pulledAt": "2026-09-28T18:19"}, "БД.ОБ": {"pulledAt": "bad"}, "БД.ПРОВ": {"pulledAt": "2026-09-30T00:00"}}
FILES = {"БД.ТЕР": TER, "БД.ОП": OP, "БД.ОБ": OB, "БД.КОАКС": COAX, "БД.ПРОВ": PROV, "БД.МАТ": MAT}


@pytest.fixture()
def sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    engine.dispose()


@pytest.fixture()
def uow(sessions):
    return SqlAlchemyReferencesUnitOfWork(sessions)


def mirror(tmp_path: Path, meta: dict | None = META, **overrides: str | None) -> Path:
    db = tmp_path / "_local" / "db"
    db.mkdir(parents=True)
    for sheet, text in {**FILES, **{key.replace("_", "."): value for key, value in overrides.items()}}.items():
        if text is not None:
            (db / f"{sheet}.tsv").write_text(text, encoding="utf-8", newline="")
    if meta is not None:
        (db / "_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return tmp_path


def rows(uow, tab):
    with uow:
        return [item.display_text for item in uow.tech_operations.list(tab=tab, query="", limit=1000)]


def only(records, tab):
    [record] = [item for item in records if item.tab == tab]
    return record


def schema(record):
    """The schema values of a record; its sheet columns are checked separately."""
    keys = schema_keys(record.tab.value)
    return {key: value for key, value in record.fields.items() if key in keys}


def test_tech_bases_are_built_like_the_gas_exporter(tmp_path):
    tables = read_mirror(mirror(tmp_path)).tables

    op, = tables["op"]
    assert (op.display_text, op.search_text, op.sort_key, op.source_sheet) == (
        "Резка | CUT_WIRE_auto", "cut_wire_autoрезка", "Резка", "БД.ОП")
    assert schema(op) == {"opNumber": "CUT_WIRE_auto", "opName": "Резка", "tOp": "3,50", "tPrep": "180", "tMachine": "3,50"}
    ob, = tables["ob"]
    assert (ob.display_text, ob.search_text, ob.sort_key, schema(ob)) == (
        "DT-8920/Дифманометр", "dt-8920/дифманометр", "Инструмент DT-8920/Дифманометр", {"obType": "Инструмент"})
    coax, = tables["coax"]
    assert (coax.display_text, coax.search_text, coax.sort_key) == (
        "BNC-male | RG58", "bnc-c58pbnc-maleruichirg58", "RG58 BNC-male RUICHI BNC-C58P")
    assert schema(coax) == {"coaxArticle": "BNC-C58P", "coaxType": "BNC-male", "coaxMfr": "RUICHI", "coaxWire": "RG58"}
    micro_fit, kvt = tables["ter"]  # the registry row and the blank row are not records
    assert (micro_fit.display_text, micro_fit.sort_key) == ("Molex | 43020, 43640 | Micro-Fit 3.0", "")
    assert micro_fit.search_text == "molex43020,43640micro-fit3.0обжимнойконтактmale"
    # Every named sheet column is kept too, as the portal shows the base (owner 29.09.2026).
    assert (op.fields["Машина"], ob.fields["Инв. номер"], coax.fields["Тип пина"]) == ("EW-05F+M/Автомат", "ОБ.048", "пайка")
    assert micro_fit.fields["Высота обжима изоляции, мм"] == "24 AWG: 1,58\n22 AWG: 1,58\n20 AWG: 1,7"
    assert schema(micro_fit) == {
        "terManufacturer": "Molex", "terSeries": "43020, 43640", "terComponent": "Micro-Fit 3.0", "terType": "Male",
        "terArticle": "430310001", "terLPlus": "6", "terLMinus": "", "terStep": "3", "terApplicator": "S8/M8",
        "terStrip": "2,54 - 2,92", "terCrimpHeight": "24 AWG: 0,83 - 0,93\n22 AWG: 0,83 - 0,93\n20 AWG: 0,89 - 0,99",
        "terPullForceMin": "24 AWG: 22,2\n22 AWG: 35,6\n20 AWG: 57,8", "terPullForceMax": "",
        "awgFrom": "24", "awgTo": "20", "mm2From": "0,2", "mm2To": "0,5",
        # the contact card of protocol v5.2 (owner 12.10.2026), from the columns the GAS exporter never read
        "terGender": "Male", "terArticleBag": "430310007",
        "terInsCrimpHeight": "24 AWG: 1,58\n22 AWG: 1,58\n20 AWG: 1,7", "terInsOdFrom": "1,3", "terInsOdTo": "1,85",
        "terMaterial": "Phosphor Bronze / Tin", "terMaxCurrent": "8,5",
        "terDatasheet": "https://www.molex.com/en-us/products/part-detail/430310001",
    }
    assert (kvt.display_text, kvt.fields["terApplicator"], kvt.fields["awgFrom"]) == ("КВТ | НВИ 1.5 | НВИ", "M31", "20")


def test_mirror_records_equal_the_supplymap_snapshot_import(tmp_path):
    # The same row as the GAS exporter sends it to PUT /supplymap-snapshot: store fields + exportValues of every
    # named column; both paths must give identical records.
    gas_payload = {
        "meta": {"schemaVersion": 14, "updatedAt": "2026-09-29T08:25:00.000Z",
                 "columnHeadersByTab": {"ter": TER_HEADER}},
        "records": [{
            "tabKey": "ter", "displayText": "Molex | 43020, 43640 | Micro-Fit 3.0",
            "normalizedSearch": "molex43020,43640micro-fit3.0обжимнойконтактmale",
            "exportValues": [MICRO_FIT[name] for name in TER_HEADER], "sourceSheet": "БД.ТЕР",
            "terManufacturer": "Molex", "terSeries": "43020, 43640", "terComponent": "Micro-Fit 3.0", "terType": "Male",
            "terArticle": "430310001", "terLPlus": "6", "terLMinus": "", "terApplicator": "S8/M8",
            "terCrimpHeight": MICRO_FIT["Высота обжима проводника , мм"],
            "terPullForceMin": MICRO_FIT["Усилие обрыва контакта, N"], "terPullForceMax": "",
            "terStep": "3", "terStrip": "2,54 - 2,92",
        }],
    }
    expected = import_supplymap_snapshot(gas_payload).records[0]

    loaded = read_mirror(mirror(tmp_path)).tables["ter"][0]

    assert (loaded.display_text, loaded.search_text, loaded.source_sheet, loaded.sort_key, loaded.fields) == (
        expected.display_text, expected.search_text, expected.source_sheet, expected.sort_key, expected.fields)


def test_resolve_terminal_works_on_loaded_ter_rows(tmp_path, sessions):
    load_mirror(mirror(tmp_path), SqlAlchemyReferencesUnitOfWork(sessions), dry_run=False, now=NOW)
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ACTOR, email="admin@example.test", roles=frozenset({"admin"}), permissions={"erpref": "edit", "procurement": "edit"})
    try:
        response = TestClient(app).post("/api/references/technology/resolve-terminal", json={
            "article": "Molex 430310001", "wires": ["UL1007 22 AWG"]})
    finally:
        app.dependency_overrides.pop(authenticated_principal, None)
        del app.state.references_uow_factory

    assert response.status_code == 200
    assert response.json()["applicator"] == "S8/M8"
    assert response.json()["crimp_height"] == "0,83 - 0,93"
    assert response.json()["pull_force"] == "35,6"
    assert response.json()["by_section"][0]["found"] is True


def test_resolve_terminal_reads_every_ter_row_not_the_first_500(sessions):
    # БД.ТЕР has ~530 records; the endpoint used to read 500 in (sort_key, random id) order and lose the rest.
    def ter(article: str, sort_key: str) -> TechRecordInput:
        return TechRecordInput(TechOperationTab.TERMINAL, article, "", "БД.ТЕР", sort_key, {
            "terArticle": article, "terApplicator": f"A-{article}", "terCrimpHeight": "0,8",
            "awgFrom": "24", "awgTo": "20", "mm2From": "", "mm2To": ""})
    replace_snapshot(ReplaceSnapshotCommand(
        schema_version=14, source_updated_at=None, actor=ACTOR, now=NOW,
        records=tuple(ter(f"X{index}", "a") for index in range(520)) + (ter("TARGET", "z"),),
    ), SqlAlchemyReferencesUnitOfWork(sessions))
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ACTOR, email="admin@example.test", roles=frozenset({"admin"}), permissions={"erpref": "edit", "procurement": "edit"})
    try:
        response = TestClient(app).post("/api/references/technology/resolve-terminal", json={
            "article": "TARGET", "wires": ["22 AWG"]})
    finally:
        app.dependency_overrides.pop(authenticated_principal, None)
        del app.state.references_uow_factory

    assert response.json()["applicator"] == "A-TARGET"


def test_reads_header_and_skips_empty_rows_and_bom(tmp_path):
    tables = read_mirror(mirror(tmp_path)).tables

    assert [r.display_text for r in tables["prov"]] == ["RG316", "UL1061"]
    assert tables["mat"][0].fields == {"Наименование": "ТУТ 4/2", "Группа": "ТУТ"}


def test_escaped_cells_are_unescaped_in_sheet_tables(tmp_path):
    prov = tsv(["Марка", "Примечание"], ["МГТФ", "строка 1\nстрока 2\tи\\слэш"])

    record, = read_mirror(mirror(tmp_path, БД_ПРОВ=prov)).tables["prov"]

    assert record.fields["Примечание"] == "строка 1\nстрока 2\tи\\слэш"


@pytest.mark.parametrize("sheet", ["БД.ОП", "БД.ОБ", "БД.ТЕР", "БД.КОАКС", "БД.ПРОВ", "БД.МАТ"])
def test_missing_file_is_a_clear_error(tmp_path, sheet):
    with pytest.raises(MirrorLoadError, match=f"{sheet}.tsv"):
        read_mirror(mirror(tmp_path, **{sheet.replace(".", "_"): None}))


def test_tech_sheet_without_records_is_an_error_not_an_empty_base(tmp_path):
    with pytest.raises(MirrorLoadError, match="БД.ОП.tsv: ни одной записи"):
        read_mirror(mirror(tmp_path, БД_ОП=tsv([" ", "", "БД операции"])))


def test_file_without_header_is_an_error(tmp_path):
    with pytest.raises(MirrorLoadError, match="БД.ПРОВ.tsv"):
        read_mirror(mirror(tmp_path, БД_ПРОВ=""))


def test_source_date_is_the_latest_pull_of_the_tech_sheets(tmp_path):
    assert read_mirror(mirror(tmp_path)).source_updated_at == datetime(2026, 9, 29, 9, 23, tzinfo=timezone.utc)
    assert read_mirror(mirror(tmp_path / "no-meta", meta=None)).source_updated_at is None


def test_dry_run_counts_rows_and_writes_nothing(tmp_path, uow):
    result = load_mirror(mirror(tmp_path), uow, dry_run=True, now=NOW)

    assert result.counts == {"ob": 1, "op": 1, "ter": 2, "coax": 1, "prov": 2, "mat": 1} and not result.written
    assert rows(uow, TechOperationTab.WIRE) == [] and rows(uow, TechOperationTab.TERMINAL) == []
    with uow:
        assert uow.tech_operations.metadata() is None


def test_load_replaces_every_mirrored_base_in_one_snapshot_and_is_repeatable(tmp_path, uow):
    replace_snapshot(ReplaceSnapshotCommand(
        schema_version=14, source_updated_at=None, actor=ACTOR, now=NOW,
        records=(TechRecordInput(TechOperationTab.TERMINAL, "T1", "t1", "БД.ТЕР", "T1", {"a": "1"}),
                 TechRecordInput(TechOperationTab.WIRE, "OLD", "", "БД.ПРОВ", "OLD", {"Марка": "OLD"}),
                 TechRecordInput(TechOperationTab.KAB_AWG, "24", "", "СПР.КАБ", "00001", {"AWG": "24"})),
    ), uow)
    source = mirror(tmp_path)

    load_mirror(source, uow, dry_run=False, now=NOW)
    load_mirror(source, uow, dry_run=False, now=NOW)

    # a БД.ТЕР sheet row goes to «Терминалы» or «Наконечники», a БД.ПРОВ row to its cable base (migration 0165)
    assert rows(uow, TechOperationTab.TERMINAL) == ["Molex | 43020, 43640 | Micro-Fit 3.0"]
    assert rows(uow, TechOperationTab.LUG) == ["КВТ | НВИ 1.5 | НВИ"]
    assert rows(uow, TechOperationTab.OPERATION) == ["Резка | CUT_WIRE_auto"]
    assert rows(uow, TechOperationTab.EQUIPMENT) == ["DT-8920/Дифманометр"]
    assert rows(uow, TechOperationTab.COAX) == ["BNC-male | RG58"]
    assert rows(uow, TechOperationTab.WIRE) == ["UL1061"] and rows(uow, TechOperationTab.CAB_COAX) == ["RG316"]
    assert rows(uow, TechOperationTab.MATERIAL) == ["ТУТ 4/2"]
    assert rows(uow, TechOperationTab.KAB_AWG) == ["24"]  # no СПР.КАБ.tsv in this mirror → kept as it was
    with uow:
        metadata = uow.tech_operations.metadata()
    assert metadata["record_count"] == 9 and metadata["schema_version"] == 14
    assert metadata["source_updated_at"].replace(tzinfo=timezone.utc) == datetime(2026, 9, 29, 9, 23, tzinfo=timezone.utc)


def test_default_source_is_the_snapshot_bundled_with_the_repo(monkeypatch, tmp_path):
    """Owner 05.10: the demo and local bases load references without the sibling SUPPLYMAP checkout."""
    monkeypatch.setenv("SUPPLYMAP_SOURCE_REPOSITORY", str(tmp_path / "absent"))
    source = default_source()
    assert source == Path(__file__).resolve().parents[2] / "data" / "reference-mirror"
    snapshot = read_mirror(source)
    # the mirror's sheets: БД.ТЕР and БД.ПРОВ come whole, each row goes to its base on the load
    assert set(snapshot.tables) == {tab.value for tab in TechOperationTab} - {"lug", "cab_coax", "cab_multi", "cab_lan"}
    assert all(snapshot.tables.values()), "every tab of the bundled snapshot has records"
    assert snapshot.source_updated_at == datetime(2026, 9, 29, 9, 23, tzinfo=timezone.utc)


def test_source_may_be_a_flat_folder_of_tsv_files(tmp_path, uow):
    nested = mirror(tmp_path / "repo")
    flat = nested / "_local" / "db"
    assert read_mirror(flat).tables == read_mirror(nested).tables


def test_main_needs_database_url(monkeypatch, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    assert main(["--dry-run"]) == 2
    assert "ERP_DATABASE_URL" in capsys.readouterr().err


def test_main_missing_mirror_exits_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ERP_DATABASE_URL", f"sqlite:///{tmp_path / 'x.db'}")
    assert main(["--dry-run", "--source", str(tmp_path)]) == 2
    assert "Зеркало не читается" in capsys.readouterr().err


# --- правки в ERP переживают перезагрузку зеркала (как в SUPPLYMAP, где лист БД — единственный источник) ---

def _erp_record(uow, tab, display):
    with uow:
        [record] = [item for item in uow.tech_operations.list(tab=tab, query="", limit=1000) if item.display_text == display]
    return record


def test_reload_keeps_erp_edit_and_updates_untouched(tmp_path, uow):
    from app.modules.references.application import EditRecordCommand, FieldEdit, edit_record
    load_mirror(mirror(tmp_path / "a"), uow, dry_run=False, now=NOW)
    record = _erp_record(uow, TechOperationTab.CAB_COAX, "RG316")
    edit_record(EditRecordCommand(record.id, (FieldEdit(next(iter(record.fields)), "RG316", "RG316-ERP"),), ACTOR, NOW), uow)

    result = load_mirror(mirror(tmp_path / "b"), uow, dry_run=False, now=NOW)

    edited = _erp_record(uow, TechOperationTab.CAB_COAX, "RG316-ERP")
    assert edited.id == record.id and edited.edited_in_erp
    assert sorted(rows(uow, CABLE_TABS)) == ["RG316-ERP", "UL1061"]  # no mirror twin next to the edit
    assert result.skipped_edited["prov"] == 1 and sum(result.skipped_edited.values()) == 1
    assert "пропущено (правлено в ERP): 1" in format_report(result)
    with uow:
        assert uow.tech_operations.metadata()["record_count"] == 8


def test_reload_updates_untouched_record_from_new_mirror(tmp_path, uow):
    load_mirror(mirror(tmp_path / "a"), uow, dry_run=False, now=NOW)
    old_id = _erp_record(uow, TechOperationTab.WIRE, "UL1061").id
    changed = FILES["БД.ПРОВ"].replace("UL1061", "UL1061-NEW")
    assert changed != FILES["БД.ПРОВ"]

    result = load_mirror(mirror(tmp_path / "b", **{"БД.ПРОВ": changed}), uow, dry_run=False, now=NOW)

    assert sorted(rows(uow, CABLE_TABS)) == ["RG316", "UL1061-NEW"]
    assert sum(result.skipped_edited.values()) == 0
    assert _erp_record(uow, TechOperationTab.WIRE, "UL1061-NEW").id != old_id


def test_reload_keeps_record_created_in_erp(tmp_path, uow):
    from app.modules.references.application import CreateRecordCommand, create_record
    load_mirror(mirror(tmp_path / "a"), uow, dry_run=False, now=NOW)
    created = create_record(CreateRecordCommand(TechOperationTab.WIRE, {"Марка": "ERP-NEW"}, ACTOR, NOW), uow)

    result = load_mirror(mirror(tmp_path / "b"), uow, dry_run=False, now=NOW)
    load_mirror(mirror(tmp_path / "c"), uow, dry_run=False, now=NOW)

    assert _erp_record(uow, TechOperationTab.WIRE, "ERP-NEW").id == created.id
    assert sorted(rows(uow, CABLE_TABS)) == ["ERP-NEW", "RG316", "UL1061"]
    assert sum(result.skipped_edited.values()) == 0  # the new record has no mirror twin


def test_a_row_moved_to_another_base_and_edited_is_not_inserted_again_in_the_base_the_rule_gives(tmp_path, uow):
    # owner 29.10.2026: «Наконечники» and each cable kind are bases of their own (migration 0165); a reload knows a row
    # by its sheet family and mirror key, so a move by hand plus an edit holds
    from app.modules.references.application import (
        EditRecordCommand, FieldEdit, MoveRecordCommand, edit_record, move_record,
    )
    load_mirror(mirror(tmp_path / "a"), uow, dry_run=False, now=NOW)
    lug = _erp_record(uow, TechOperationTab.LUG, "КВТ | НВИ 1.5 | НВИ")
    wire = _erp_record(uow, TechOperationTab.WIRE, "UL1061")
    move_record(MoveRecordCommand(lug.id, TechOperationTab.TERMINAL, ACTOR, NOW), uow)
    move_record(MoveRecordCommand(wire.id, TechOperationTab.CAB_MULTI, ACTOR, NOW), uow)
    moved_wire = _erp_record(uow, TechOperationTab.CAB_MULTI, "UL1061")
    edit_record(EditRecordCommand(wire.id, (FieldEdit("Марка", "UL1061", "UL1061 (правка)"),), ACTOR, NOW), uow)

    result = load_mirror(mirror(tmp_path / "b"), uow, dry_run=False, now=NOW)

    assert rows(uow, TechOperationTab.LUG) == []  # the rule would send it back to «Наконечники»: kept where it was moved
    assert sorted(rows(uow, TechOperationTab.TERMINAL)) == ["Molex | 43020, 43640 | Micro-Fit 3.0", "КВТ | НВИ 1.5 | НВИ"]
    assert _erp_record(uow, TechOperationTab.TERMINAL, "КВТ | НВИ 1.5 | НВИ").id == lug.id
    assert rows(uow, TechOperationTab.WIRE) == []  # neither the rule's монтажный twin nor the cable's mark map
    assert rows(uow, TechOperationTab.CAB_MULTI) == ["UL1061 (правка)"] and moved_wire.id == wire.id
    assert (result.skipped_edited["ter"], result.skipped_edited["prov"]) == (1, 1)  # counted by the mirror sheet


def test_dry_run_report_has_no_skipped_column(tmp_path, uow):
    load_mirror(mirror(tmp_path / "a"), uow, dry_run=False, now=NOW)
    assert "пропущено" not in format_report(load_mirror(mirror(tmp_path / "b"), uow, dry_run=True, now=NOW))
