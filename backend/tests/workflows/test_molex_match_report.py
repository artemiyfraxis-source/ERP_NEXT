"""M0 runs on real SQLite tables and must issue only SELECTs, even with a dirty session."""

import json
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Boolean, Date, DateTime, Integer, JSON, Numeric, Uuid, create_engine, event, inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from app.shared.persistence import Base, create_schema
from app.workflows import molex_match_report as workflow

NOW = datetime(2026, 11, 11)
PROJECT = "591К.3"


def insert(session, model, **values):
    """Fill irrelevant required snapshot columns; tests override domain-specific constraints."""
    table = model.__table__
    for column in table.columns:
        if column.name in values or column.nullable or column.default is not None or column.server_default is not None:
            continue
        kind = column.type
        if isinstance(kind, Uuid):
            value = uuid4()
        elif isinstance(kind, DateTime):
            value = NOW
        elif isinstance(kind, Date):
            value = date(2026, 11, 11)
        elif isinstance(kind, JSON):
            value = []
        elif isinstance(kind, Boolean):
            value = False
        elif isinstance(kind, (Integer, Numeric)):
            value = 0
        else:
            value = ""
        values[column.name] = value
    session.execute(table.insert().values(**values))
    return values["id"] if "id" in values else None


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "report.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    create_schema(engine)
    with Session(engine) as session:
        purchase = insert(session, workflow.ProcurementLineRow,
                          design_article="51021-0400", calculated_name="Корпус", project_reference=PROJECT,
                          calculated_quantity=Decimal(8), calculated_unit="шт", source_idempotency_key="purchase")
        receiving = insert(session, workflow.ReceivingLineRow, procurement_line_id=purchase,
                           article="51021-0400", goal=PROJECT, name="Корпус", unit="шт", source_idempotency_key="receipt")
        lot = insert(session, workflow.StockLotRow, article="51021-0400", manufacturer="Molex", name="Корпус",
                     warehouse="ЦАР", grn="ГРН1", unit="шт", inbound=Decimal(10), reserved=Decimal(2),
                     source_receiving_ids=[str(receiving)])
        insert(session, workflow.KitsetRow, project=PROJECT, project_key=workflow.project_key(PROJECT), assemblies=[{
            "index": "КБ1", "run": "4", "components": [{"art": "0510210400", "name": "Корпус", "per": "2"}],
        }])
        request = insert(session, workflow.FulfillmentRequestRow, number="КМ1", source_idempotency_key="kit",
                         status="Новая", purpose=f"{PROJECT} · КБ1", project_key=workflow.project_key(PROJECT), item_index="КБ1")
        line = insert(session, workflow.FulfillmentLineRow, request_id=request, article="0510210400", name="Корпус",
                      unit="шт", requirement=Decimal(8), off_book=False)
        insert(session, workflow.RequestRow, legacy_id="ЗКП1", category="other", design_article="0510210400",
               name="Корпус", project_reference=PROJECT, unit="шт", quantity=Decimal(8))
        for index, article in enumerate(("0510210400", "51021-0400")):
            insert(session, workflow.ItemRow, part_number=article, part_key=workflow.code_key(article),
                   manufacturer="Molex", name="Корпус", unit="шт", category="connector")
        insert(session, workflow.UpdLineRow, document_id="document", source="manual", seq=1,
               article="0510210400", article_key="0510210400", quantity=Decimal(8), unit="шт")
        insert(session, workflow.UpdLineRow, document_id="document", source="manual", seq=2,
               article="51021-0400", article_key="510210400", quantity=Decimal(8), unit="шт")
        session.commit()
    yield engine, path, lot, line
    engine.dispose()


def snapshot(session):
    return {name: session.execute(select(Base.metadata.tables[name])).all()
            for name in inspect(session.connection()).get_table_names()}


def test_all_seams_collisions_and_quantities(db):
    engine, _, lot, line = db
    with Session(engine) as session:
        result = workflow.report(session)
    changes = result["changes"]
    assert set(changes) == set(workflow.SEAMS)
    arrival = changes["arrival_matches"]
    assert len(arrival) == 1
    assert arrival[0]["left"]["id"] == str(lot)
    assert arrival[0]["right"]["id"] == str(line)
    assert arrival[0]["right"]["project"] == workflow.project_key(PROJECT)
    assert arrival[0]["right"]["kb"] == "КБ1"
    assert Decimal(arrival[0]["left"]["available"]) == 8
    assert Decimal(arrival[0]["right"]["quantity"]) == 8
    for seam in ("_supply_match", "_supply_key", "procurement code_key"):
        assert changes[seam]
    assert any(row["before"] is None and row["after"]["family"] == "molex-picoblade" for row in changes["housing_of"])
    collision = result["canonical_collisions"][0]
    assert collision["key"] == "510210400"
    assert collision["articles"] == ["0510210400", "51021-0400"]
    assert {row["column"] for row in result["stored_key_collisions"]} == {
        "purchasing_items.part_key", "purchasing_upd_lines.article_key",
    }
    assert result["read_only"] is True
    assert "ГРН1" in workflow.format_report(result)


def test_report_never_writes_or_flushes_pending_changes(db):
    engine, _, lot_id, _ = db
    statements = []
    def record(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    with Session(engine) as session:
        before = snapshot(session)
        lot = session.get(workflow.StockLotRow, lot_id)
        lot.article = "pending change"
        event.listen(engine, "before_cursor_execute", record)
        try:
            result = workflow.report(session)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert statements and all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
        assert result["changes"]["arrival_matches"]
        assert lot.article == "pending change" and lot in session.dirty
        session.rollback()
        assert snapshot(session) == before


def test_etk_formatting_alias_is_not_a_replacement_and_supply_baseline_is_respected(db):
    engine, _, _, _ = db
    with Session(engine) as session:
        row = session.get(workflow.KitsetRow, PROJECT)
        row.assemblies = [{"index": "КБ1", "run": "4", "components": [
            {"art": "0510210400", "name": "51021-0400", "per": "2"},
        ]}]
        session.commit()
        changes = workflow.report(session)["changes"]
    assert any(row["entry"]["kind"] == "kit" and row["after"]["kd"] == "" for row in changes["etk_parts.use_article"])
    assert not any(row["left"]["kind"] == "lot" for row in changes["_supply_match"])
    assert any(row["left"]["kind"] == "lot" for row in changes["_supply_key"])


def test_supply_includes_used_article_when_kd_is_a_different_component(db):
    engine, _, _, _ = db
    with Session(engine) as session:
        row = session.get(workflow.KitsetRow, PROJECT)
        row.assemblies = [{"index": "КБ1", "run": "4", "components": [
            {"art": "CLONE-1", "name": "0510210400", "per": "2"},
        ]}]
        session.commit()
        changes = workflow.report(session)["changes"]
    pair = next(row for row in changes["_supply_match"] if row["left"]["kind"] == "lot")
    assert pair["right"]["article"] == "0510210400"
    assert pair["right"]["kd_article"] == "CLONE-1"
    assert not changes["_supply_key"]


def test_supply_baseline_unions_aliases_across_kbs(db):
    engine, _, _, _ = db
    with Session(engine) as session:
        row = session.get(workflow.KitsetRow, PROJECT)
        row.assemblies = [
            {"index": "КБ1", "run": "1", "components": [{"art": "0510210400", "name": "51021-0400"}]},
            {"index": "КБ2", "run": "1", "components": [{"art": "0510210400", "name": "Корпус"}]},
        ]
        session.commit()
        changes = workflow.report(session)["changes"]
    assert not any(row["left"]["kind"] == "lot" for row in changes["_supply_match"])


def test_purchase_inherits_request_identity_and_project(db):
    engine, _, _, _ = db
    with Session(engine) as session:
        purchase = session.scalar(select(workflow.ProcurementLineRow))
        request = session.scalar(select(workflow.RequestRow))
        purchase.source_request_id = request.id
        purchase.design_article = None
        purchase.project_reference = None
        purchase.actual_quantity = Decimal(0)
        row = session.get(workflow.KitsetRow, PROJECT)
        row.assemblies = [{"index": "КБ1", "run": "1", "components": [{"art": "51021-0400", "name": "Корпус"}]}]
        session.commit()
        changes = workflow.report(session)["changes"]
    pair = next(row for row in changes["_supply_match"] if row["left"]["kind"] == "purchase")
    assert pair["left"]["project"] == PROJECT
    assert Decimal(pair["left"]["quantity"]) == 0


def test_replacement_receipt_links_to_original_receiving_line(db):
    engine, _, lot_id, _ = db
    with Session(engine) as session:
        lot = session.get(workflow.StockLotRow, lot_id)
        replacement = lot.source_receiving_ids[0] + ":r2"
        lot.source_receiving_ids = [replacement]
        session.commit()
        rows = workflow.report(session)["changes"]["arrival_matches"]
    assert len(rows) == 1
    assert rows[0]["arrival_source"]["receipt_id"] == replacement


def test_arrival_uses_source_articles_even_when_lot_has_a_supplier_code(db):
    engine, _, lot_id, _ = db
    with Session(engine) as session:
        lot = session.get(workflow.StockLotRow, lot_id)
        lot.article = "SUPPLIER-SKU-4"
        receipt = session.scalar(select(workflow.ReceivingLineRow))
        receipt.fact_quantity = Decimal(6)
        session.commit()
        rows = workflow.report(session)["changes"]["arrival_matches"]
    assert len(rows) == 1  # Both source articles yield the same pair only once.
    assert rows[0]["left"]["article"] == "SUPPLIER-SKU-4"
    assert rows[0]["key"] == "510210400"
    assert Decimal(rows[0]["arrival_source"]["quantity"]) == 6


def test_arrival_source_manufacturer_vetoes_family_inference(db):
    engine, _, _, _ = db
    with Session(engine) as session:
        session.scalar(select(workflow.ReceivingLineRow)).manufacturer = "JST"
        session.commit()
        assert not workflow.report(session)["changes"]["arrival_matches"]


@pytest.mark.parametrize("legacy_match", [False, True])
def test_arrival_without_an_article_uses_only_source_names(db, legacy_match):
    engine, _, _, line_id = db
    with Session(engine) as session:
        line = session.get(workflow.FulfillmentLineRow, line_id)
        line.article, line.name = "", "0510210400"
        receipt = session.scalar(select(workflow.ReceivingLineRow))
        receipt.name = "0510210400" if legacy_match else "51021-0400"
        session.commit()
        rows = workflow.report(session)["changes"]["arrival_matches"]
    if legacy_match:
        assert not rows
    else:
        assert len(rows) == 1
        assert rows[0]["arrival_source"]["match_field"] == "names"
        assert rows[0]["right_match_field"] == "name"


def test_stored_key_collision_includes_an_unrelated_article_occupying_the_key(db):
    engine, _, _, _ = db
    with Session(engine) as session:
        occupant = session.scalar(select(workflow.ItemRow).where(workflow.ItemRow.part_key == "510210400"))
        occupant.part_number = "OTHER-ARTICLE"
        occupant.manufacturer = "JST"
        session.commit()
        collisions = workflow.report(session)["stored_key_collisions"]
    collision = next(row for row in collisions if row["column"] == "purchasing_items.part_key")
    assert any(row["article"] == "OTHER-ARTICLE" for row in collision["entries"])


def test_null_item_keys_would_collide_but_existing_duplicate_upd_keys_are_not_new(db):
    engine, _, _, _ = db
    with Session(engine) as session:
        for item in session.scalars(select(workflow.ItemRow)):
            item.part_key = None
        for line in session.scalars(select(workflow.UpdLineRow)):
            line.article_key = "510210400"
        session.commit()
        collisions = workflow.report(session)["stored_key_collisions"]
    assert [row["column"] for row in collisions] == ["purchasing_items.part_key"]


def test_conflicting_catalogue_manufacturer_requires_review(db):
    engine, _, _, _ = db
    with Session(engine) as session:
        insert(session, workflow.ItemRow, part_number="0510210400", part_key="other-maker", manufacturer="JST",
               name="Изделие", unit="шт", category="connector")
        result = workflow.report(session)
    assert result["manufacturer_conflicts"][0]["manufacturers"] == ["JST"]
    assert any(row["manufacturer"] == "JST" for row in result["manufacturer_conflicts"][0]["entries"])
    assert any("JST" in reason for row in result["changes"]["_supply_match"] for reason in row["review_reasons"])


@pytest.mark.parametrize("change", ["other_project", "manual_purpose", "closed", "bound", "legacy_match", "missing_receiving"])
def test_arrival_excludes_unaffected_or_ineligible_lines(db, change):
    engine, _, lot_id, line_id = db
    with Session(engine) as session:
        line = session.get(workflow.FulfillmentLineRow, line_id)
        request = session.get(workflow.FulfillmentRequestRow, line.request_id)
        if change == "other_project":
            request.purpose = "999К.1 · КБ1"
        elif change == "manual_purpose":
            request.purpose = f"{PROJECT} КБ1"
        elif change == "closed":
            request.status = "Готово"
        elif change == "bound":
            line.lot_id = lot_id
        elif change == "legacy_match":
            line.article = "51021-0400"
        else:
            session.get(workflow.StockLotRow, lot_id).source_receiving_ids = []
        session.commit()
        assert not workflow.report(session)["changes"]["arrival_matches"]


def test_arrival_uses_purpose_even_when_cached_project_is_stale(db):
    engine, _, _, line_id = db
    with Session(engine) as session:
        line = session.get(workflow.FulfillmentLineRow, line_id)
        session.get(workflow.FulfillmentRequestRow, line.request_id).project_key = "999К.1"
        session.commit()
        assert workflow.report(session)["changes"]["arrival_matches"]


@pytest.mark.parametrize("legacy_name", [False, True])
def test_supply_name_fallback_for_sources_without_an_article(db, legacy_name):
    engine, _, lot_id, _ = db
    with Session(engine) as session:
        session.get(workflow.StockLotRow, lot_id).article = ""
        session.get(workflow.StockLotRow, lot_id).name = "51021-0400"
        request = session.scalar(select(workflow.RequestRow))
        request.design_article = None
        request.name = "51021-0400"
        if legacy_name:
            kit = session.get(workflow.KitsetRow, PROJECT)
            kit.assemblies = [{"index": "КБ1", "run": "1", "components": [
                {"art": "0510210400", "name": "51021-0400"},
            ]}]
        session.commit()
        changes = workflow.report(session)["changes"]
    pairs = [row for row in changes["_supply_match"] if row["left"]["kind"] in {"lot", "request"}]
    if legacy_name:
        assert not pairs
    else:
        assert {row["left"]["kind"] for row in pairs} == {"lot", "request"}
        assert all(row["left_match_field"] == "name" and not row["left"]["article"] for row in pairs)
    assert not any(not row["left"]["article"] for row in changes["_supply_key"])


def test_document_code_baseline_includes_name_only_request_codes(db):
    engine, _, _, _ = db
    with Session(engine) as session:
        request = session.scalar(select(workflow.RequestRow))
        request.design_article, request.name = None, "0510210400"
        session.commit()
        rows = workflow.report(session)["changes"]["procurement code_key"]
    pairs = [row for row in rows if row["left"]["kind"] == "upd" and row["right"]["kind"] == "request"]
    assert len(pairs) == 1
    assert pairs[0]["left"]["article"] == "51021-0400"
    assert pairs[0]["right_match_field"] == "name"


def test_non_molex_suffixes_catalogue_evidence_and_units(db):
    engine, _, _, _ = db
    with Session(engine) as session:
        for article, maker in (("0510210400-LF", "Molex"), ("0510210400(N)", "Molex"), ("0510210400", "JST")):
            insert(session, workflow.StockLotRow, article=article, manufacturer=maker, grn=str(uuid4()), unit="м")
        insert(session, workflow.ItemRow, part_number="0012345678", part_key="0012345678", manufacturer="Molex",
               name="Изделие", unit="шт", category="other")
        insert(session, workflow.StockLotRow, article="0012345678", grn="ГРН2", unit="м")
        insert(session, workflow.KitsetRow, project="592К.1", project_key="592К.1", assemblies=[{
            "index": "КБ2", "run": "1", "components": [{"art": "12345678", "name": "Изделие"}],
        }])
        # Exact catalogue identity is required for each otherwise unknown article spelling.
        result = workflow.report(session)
        collision = next(row for row in result["canonical_collisions"] if row["key"] == "510210400")
        assert not any(row["manufacturer"] == "JST" or "LF" in row["article"] or "(N)" in row["article"] for row in collision["entries"])
        assert not any(row["key"] == "12345678" for row in result["changes"]["_supply_match"])
        insert(session, workflow.ItemRow, part_number="12345678", part_key="12345678", manufacturer="Molex",
               name="Изделие", unit="шт", category="other")
        rows = workflow.report(session)["changes"]["_supply_match"]
        candidate = next(row for row in rows if row["key"] == "12345678")
        assert candidate["review_reasons"] == ["разные единицы измерения"]


@pytest.mark.parametrize("args", [[], ["--json"]])
def test_cli_is_read_only_on_configured_sqlite_database(db, monkeypatch, capsys, args):
    engine, path, _, _ = db
    monkeypatch.setenv("ERP_DATABASE_URL", f"sqlite:///{path.as_posix()}")
    with Session(engine) as session:
        before = snapshot(session)
    assert workflow.main(args) == 0
    output = capsys.readouterr().out
    if args:
        assert json.loads(output)["read_only"] is True
    else:
        assert "только чтение" in output
    with Session(engine) as session:
        assert snapshot(session) == before


def test_cli_requires_explicit_database(monkeypatch, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    assert workflow.main([]) == 2
    assert "ERP_DATABASE_URL" in capsys.readouterr().err


def test_cli_database_connection_rejects_writes(db, monkeypatch, capsys):
    engine, path, _, _ = db
    monkeypatch.setenv("ERP_DATABASE_URL", f"sqlite:///{path.as_posix()}")
    original = workflow.report
    def attempted_write(session):
        with pytest.raises(OperationalError, match="readonly|read-only"):
            session.execute(workflow.StockLotRow.__table__.update().values(article="changed"))
        return original(session)
    monkeypatch.setattr(workflow, "report", attempted_write)
    with Session(engine) as session:
        before = snapshot(session)
    assert workflow.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["read_only"] is True
    with Session(engine) as session:
        assert snapshot(session) == before


def test_cli_cannot_create_a_database_at_a_mistyped_path(tmp_path, monkeypatch):
    path = tmp_path / "does-not-exist.db"
    monkeypatch.setenv("ERP_DATABASE_URL", f"sqlite:///{path.as_posix()}")
    with pytest.raises(OperationalError, match="unable to open database file"):
        workflow.main(["--json"])
    assert not path.exists()
