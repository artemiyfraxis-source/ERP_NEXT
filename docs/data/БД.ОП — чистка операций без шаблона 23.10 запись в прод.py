"""БД.ОП cleanup (owner 23.10.2026): drop operation rows that have no template and are not needed in production —
12 «Коаксиальная сборка» (a whole assembly as one operation; a tech card is per operation), DELETE_ABLOY, PREP1, PREP2.
Each row goes through references.application.delete_record: one audited event with the full old record (restorable).
A row is refused when any project card operation or template uses its code. argv[1] = dry-run | apply."""
import os, sys, uuid, json
from sqlalchemy import text
from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import delete_record
from app.modules.references.domain import TechOperationTab
from app.shared.clock import moscow_now
from app.shared.identity import Actor

CODES = {"MHF1-MHF1", "Str-Str long", "R/A-MHF1", "R/A-Str long", "R/A-Str short", "Str-R/A RG402(5)", "R/A-R/A long",
         "Str-Str short", "Str-Str RG402(5)", "R/A-R/A short", "R/A-R/A RG402(5)", "Str-MHF1", "DELETE_ABLOY", "PREP1", "PREP2"}
mode = sys.argv[1]
engine = build_engine(os.environ["ERP_DATABASE_URL"]); factory = build_references_uow_factory(engine)
with factory() as uow:
    rows = [r for r in uow.tech_operations.list(tab=TechOperationTab("op"), query="", limit=5000)
            if (r.fields.get("Номер") or r.fields.get("opNumber") or "").strip() in CODES]
with engine.connect() as c:
    total = c.execute(text("select count(*) from tech_operation_records where tab='op'")).scalar()
    used = {}
    for code in CODES:
        needle = json.dumps(code)[1:-1]
        n = c.execute(text("select count(*) from techcard_projects where cast(document as text) like :p"), {"p": f'%"{needle}"%'}).scalar()
        if n: used[code] = n
print(f"op rows: {total}; matched: {len(rows)} of {len(CODES)}; used by cards: {used or 'none'}")
for r in rows: print(" ", r.fields.get("Номер") or r.fields.get("opNumber"), "|", r.display_text[:50])
if mode == "apply":
    actor = Actor(id=uuid.uuid4(), name="чистка БД.ОП: операции без шаблона (владелец 23.10)", department="")
    for r in rows:
        code = (r.fields.get("Номер") or r.fields.get("opNumber")).strip()
        if code in used: print("  refused (used):", code); continue
        delete_record(r.id, actor, moscow_now(), factory(), reason="операция без шаблона не нужна в производстве; ТК — на операцию (владелец 23.10.2026)")
    with engine.connect() as c:
        print("op rows after:", c.execute(text("select count(*) from tech_operation_records where tab='op'")).scalar())
