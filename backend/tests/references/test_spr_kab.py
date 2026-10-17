"""СПР.КАБ (REF-SPRKAB): coax D1–D3 table above the AWG table on one sheet — view-only storage."""

import pytest

from app.modules.references.application import ReplaceSnapshotCommand, TechRecordInput, replace_snapshot
from app.modules.references.domain import TechOperationTab
from app.modules.references.load_mirror import ACTOR, MirrorLoadError, format_report, load_mirror, read_mirror
from app.modules.references.technology import import_supplymap_snapshot, parse_spr_kab_tsv
from tests.references.test_api import references_api  # noqa: F401  (fixture)
from tests.references.test_load_mirror import NOW, mirror, rows, sessions, uow  # noqa: F401  (fixtures/helpers)

COAX = "Кабель\tD1\tD2\tD3\nRG-316\t0,5\t1,48\t1,95\nRG-6\t1,02\t4,57\nRG-142\t0,94\t3,00\t3,95\nRG-142\t0,94\t3,00\t3,95\n"
AWG = "AWG\tГОСТ\tØ жилы\tМГТФ\n22\t0,35\t0,64\t1,2\n1/0\t50\t8,25\t—\n"
SHEET = COAX + "\n\n" + AWG
META = {"schemaVersion": 14, "updatedAt": "2026-09-28T18:20:00.000Z"}


def _put(client, **tables):
    return client.put("/api/references/tech-operations/supplymap-snapshot", json={"meta": META, "records": [], "tables": tables})


def _rows(client, tab):
    return client.get("/api/references/tech-operations", params={"tab": tab}).json()


def test_splits_both_tables_at_the_awg_header_across_blank_rows():
    tables = parse_spr_kab_tsv(SHEET)

    assert tables["kab_coax"]["header"] == ["Кабель", "D1", "D2", "D3"]
    assert [row[0] for row in tables["kab_coax"]["rows"]] == ["RG-316", "RG-6", "RG-142", "RG-142"]
    assert tables["kab_awg"]["header"] == ["AWG", "ГОСТ", "Ø жилы", "МГТФ"]
    assert [row[0] for row in tables["kab_awg"]["rows"]] == ["22", "1/0"]


def test_more_blank_rows_bom_crlf_and_trailing_newlines_are_fine():
    text = "﻿" + (COAX + "\n\n\n\n" + AWG).replace("\n", "\r\n") + "\r\n\r\n"

    tables = parse_spr_kab_tsv(text)

    assert len(tables["kab_coax"]["rows"]) == 4 and len(tables["kab_awg"]["rows"]) == 2


def test_records_keep_decimal_commas_short_rows_and_sheet_order():
    records = import_supplymap_snapshot({"meta": META, "records": [], "tables": parse_spr_kab_tsv(SHEET)}).records
    coax = [item for item in records if item.tab == "kab_coax"]

    assert [item.fields["Кабель"] for item in coax] == ["RG-316", "RG-6", "RG-142", "RG-142"]
    assert coax[0].fields == {"Кабель": "RG-316", "D1": "0,5", "D2": "1,48", "D3": "1,95"}
    assert coax[1].fields["D3"] == ""  # RG-6 has no D3: padded, not shifted
    assert sorted(item.sort_key for item in coax) == [item.sort_key for item in coax]  # sheet order, not alphabetical
    assert coax[0].source_sheet.startswith("СПР.КАБ")
    assert [item.fields["МГТФ"] for item in records if item.tab == "kab_awg"] == ["1,2", "—"]


@pytest.mark.parametrize("text", ["", COAX, "\n\n" + AWG])
def test_sheet_without_both_tables_is_an_error(text):
    with pytest.raises(ValueError):
        parse_spr_kab_tsv(text)


def test_api_reads_both_tables_and_survives_other_snapshots(references_api):
    assert _put(references_api, **parse_spr_kab_tsv(SHEET)).status_code == 200
    coax = _rows(references_api, "kab_coax")

    assert coax["columns"] == ["Кабель", "D1", "D2", "D3"]
    assert [item["display_text"] for item in coax["records"]] == ["RG-316", "RG-6", "RG-142", "RG-142"]
    assert len(_rows(references_api, "kab_awg")["records"]) == 2
    # a v14 snapshot without `tables` and a whole-catalogue replace must not wipe them
    op = {"tabKey": "op", "displayText": "Резка", "normalizedSearch": "резка", "sourceSheet": "БД.ОП"}
    references_api.put("/api/references/tech-operations/supplymap-snapshot", json={"meta": META, "records": [op]})
    references_api.put("/api/references/tech-operations/snapshot", json={"schema_version": 14, "records": []})
    assert len(_rows(references_api, "kab_coax")["records"]) == 4
    assert len(_rows(references_api, "kab_awg")["records"]) == 2


def test_api_refuses_to_edit_or_create_in_spr_kab(references_api):
    _put(references_api, **parse_spr_kab_tsv(SHEET))
    record = _rows(references_api, "kab_coax")["records"][0]

    patched = references_api.patch(f"/api/references/tech-operations/{record['id']}", json={"edits": [{"field": "D1", "old": "0,5", "value": "9"}]})
    created = references_api.post("/api/references/tech-operations", json={"tab": "kab_awg", "fields": {"AWG": "99"}})

    assert patched.status_code == 422 and created.status_code == 422
    assert _rows(references_api, "kab_coax")["records"][0]["fields"]["D1"] == "0,5"


def test_mirror_reads_spr_kab_when_present_and_skips_it_when_absent(tmp_path):
    source = mirror(tmp_path)
    assert set(read_mirror(source).tables) == {"ob", "op", "ter", "coax", "prov", "mat"}

    (source / "_local" / "db" / "СПР.КАБ.tsv").write_text(SHEET, encoding="utf-8", newline="")
    tables = read_mirror(source).tables

    assert {tab: len(items) for tab, items in tables.items() if tab.startswith(("prov", "mat", "kab"))} == {
        "prov": 2, "mat": 1, "kab_coax": 4, "kab_awg": 2}


def test_broken_spr_kab_file_is_an_error(tmp_path):
    source = mirror(tmp_path)
    (source / "_local" / "db" / "СПР.КАБ.tsv").write_text(COAX, encoding="utf-8", newline="")

    with pytest.raises(MirrorLoadError, match="СПР.КАБ.tsv"):
        read_mirror(source)


def test_load_replaces_spr_kab_and_dry_run_reports_counts(tmp_path, uow):  # noqa: F811
    replace_snapshot(ReplaceSnapshotCommand(
        schema_version=14, source_updated_at=None, actor=ACTOR, now=NOW,
        records=(TechRecordInput(TechOperationTab.TERMINAL, "T1", "t1", "БД.ТЕР", "T1", {"a": "1"}),
                 TechRecordInput(TechOperationTab.KAB_COAX, "OLD", "", "СПР.КАБ", "00001", {"Кабель": "OLD"})),
    ), uow)
    source = mirror(tmp_path)
    (source / "_local" / "db" / "СПР.КАБ.tsv").write_text(SHEET, encoding="utf-8", newline="")

    dry = load_mirror(source, uow, dry_run=True, now=NOW)
    assert dry.counts["kab_coax"] == 4 and dry.counts["kab_awg"] == 2 and dry.current["kab_coax"] == 1
    assert "СПР.КАБ · коаксиал: строк в зеркале 4" in format_report(dry)
    assert rows(uow, TechOperationTab.KAB_COAX) == ["OLD"]

    load_mirror(source, uow, dry_run=False, now=NOW)
    load_mirror(source, uow, dry_run=False, now=NOW)

    assert rows(uow, TechOperationTab.KAB_COAX) == ["RG-316", "RG-6", "RG-142", "RG-142"]
    assert rows(uow, TechOperationTab.KAB_AWG) == ["22", "1/0"]
    assert "T1" not in rows(uow, TechOperationTab.TERMINAL)  # БД.ТЕР now comes from the mirror too (REF-MIRROR-ALL)
