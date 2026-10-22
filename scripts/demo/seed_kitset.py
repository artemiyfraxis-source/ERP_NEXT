"""Demo kitset for the techcards «Генератор»: project 713К.4, one КБ (XHP-4 x2, НВ4 0,5, SXH-001T-P0.6), straight into
a demo base (kitsets are created from a Google calculation in the app, which a demo has no access to).

    cd backend; ERP_DATABASE_URL=sqlite+pysqlite:///demo.db <venv python> ../scripts/demo/seed_kitset.py

Only for a throw-away demo/preview base (never the server). Re-running refreshes the same card.
"""
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
DB_URL = os.environ.get("ERP_DATABASE_URL") or f"sqlite+pysqlite:///{BACKEND}/demo.db"
if "erp-server" in DB_URL or DB_URL.startswith("postgres"):
    sys.exit("Refusing to seed a demo kitset into a non-demo database.")

from app.database import build_engine  # noqa: E402
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork  # noqa: E402
from app.modules.techcards.kitsets import CalcKitset, KitAssembly, KitComponent  # noqa: E402
from app.shared.identity import Actor  # noqa: E402
from app.workflows import project_kitset as flow  # noqa: E402

sessions = sessionmaker(bind=build_engine(DB_URL), expire_on_commit=False, class_=Session)
assembly = KitAssembly(index="КБ1", name="Жгут питания платы XHP-4", run=Decimal("20"), components=(
    KitComponent(article="XHP-4", type="Корпус", name="Корпус JST XHP-4", per_unit=Decimal("2")),
    KitComponent(article="SXH-001T-P0.6", type="Клемма", name="Клемма JST SXH-001T-P0.6", per_unit=Decimal("8")),
    KitComponent(article="НВ4 0,5", type="Провод", name="Провод НВ4 0,5 мм²", per_unit=Decimal("1.2")),
))
data = CalcKitset(project="713К.4", customer="ООО «Демо»", calc_source="demo", assemblies=(assembly,))
actor = Actor(id=UUID("40000000-0000-0000-0000-000000000001"), name="Демо-технолог", department="Производство")
result = flow.load_kitset(
    flow.LoadKitsetCommand(data=data, number="", deadline=None, ship={}, actor=actor, now=datetime.now()),
    lambda: SqlAlchemyTechcardsUnitOfWork(sessions), None, None, None,
)
print(f"Kitset {result.project}: КБ {result.assemblies}, строк {result.lines}")
