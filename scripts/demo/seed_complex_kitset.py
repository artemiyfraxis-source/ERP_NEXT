"""Demo kitsets for the «Сложная сборка» pinout/splice/wrap screens: 663К.1 КБ2 «Жгут периферийного питания»
(БЦТС.002.01.03400.206.00, партия 300), КБ14 (ленты, ТСП-200) and 708К.5 КБ1 (лента тканевая), straight into a demo base.

    cd backend; ERP_DATABASE_URL=sqlite+pysqlite:///demo.db <venv python> ../scripts/demo/seed_complex_kitset.py

Only for a throw-away demo/preview base (never the server).
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


def K(art, typ, per, name=""):
    return KitComponent(article=art, type=typ, name=name or art, per_unit=Decimal(per))


actor = Actor(id=UUID("40000000-0000-0000-0000-000000000001"), name="Демо-технолог", department="Производство")


def load(project, *assemblies):
    flow.load_kitset(
        flow.LoadKitsetCommand(data=CalcKitset(project=project, customer="ООО «Демо»", calc_source="demo", assemblies=assemblies),
                               number="", deadline=None, ship={}, actor=actor, now=datetime.now()),
        lambda: SqlAlchemyTechcardsUnitOfWork(sessions), None, None, None)


kb2 = KitAssembly(index="КБ2", name="Жгут периферийного питания", run=Decimal("300"), components=(
    K("SMR-02V-BC", "Соед", "1"), K("SMP-02V-BC", "Соед", "1"), K("I-DS1071-SCB01x3", "Соед", "1"), K("GHR-06V-S", "Соед", "1"),
    K("SYM-001T-P0.6", "Терминал", "2"), K("SHF-001T-0.8BS", "Терминал", "2"), K("T-DS1071-SC600", "Терминал", "2"),
    K("SSHL-002T-P0.2", "Терминал", "6"),
    K("ПВАМ 0,35 красный", "Провод", "1.2"), K("ПВАМ 0,35 черный", "Провод", "1.2"),
    K("МС 16-13 0,12 красный", "Провод", "0.9"), K("МС 16-13 0,12 черный", "Провод", "0.9"), K("МГТФ 0,12", "Провод", "0.9"),
    K("ГС-2,5", "Др", "2", "Гильза ГС-2,5 неизолированная"),
    K("ТСП-200 2,0", "ТТК", "0.9", "Трубка Радиант ТСП-200 2,0 мм"), K("ТНТ-1/0,5", "ТТК", "0.05", "Трубка ТНТ-1/0,5"),
    K("ТНТ-4/2 белый", "ТТК", "0.35", "Трубка ТНТ-4/2 белая"),
))
kb14 = KitAssembly(index="КБ14", name="Жгут с лентами", run=Decimal("50"), components=(
    K("SMR-02V-BC", "Соед", "2"), K("SYM-001T-P0.6", "Терминал", "4"), K("ПВАМ 0,35 красный", "Провод", "1.5"),
    K("ПВАМ 0,35 черный", "Провод", "1.5"),
    K("Лента флисовая 19 мм", "Др", "0.8"), K("Изолента ПВХ 19 мм", "Др", "0.3"),
    K("ТСП-200 2,0", "ТТК", "0.5", "Трубка Радиант ТСП-200 2,0 мм"),
))
load("663К.1", kb2, kb14)
load("708К.5", KitAssembly(index="КБ1", name="Жгут 708К.5", run=Decimal("20"), components=(
    K("SMR-02V-BC", "Соед", "2"), K("SYM-001T-P0.6", "Терминал", "4"), K("ПВАМ 0,35 красный", "Провод", "1.2"),
    K("Лента тканевая 19 мм", "Др", "0.5"),
)))
print("ok")
