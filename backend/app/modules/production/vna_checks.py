"""VNA calibration and accuracy-control journal (СТО ФРАК-002-2026, 11.4, 14; form В.1).

One append-only record per check written by the VNA program on the VNA's own card:
- session — after every calibration: the ACM confidence test («Данные/Память», 4.1 limits) and LOAD on both ports;
- flex — the cable bend check with a thru: S21/S12 stays within ±0.05 dB;
- control_standard — weekly: the control adapter (СИ.009) against its stored reference curve, deviation ≤ U;
- kit_standards — monthly: the 6650F27 standards against their description, √(U² + δ²) (МП п. 10.6.61).
The verdict is recomputed here and by CHECK constraints in the database: a record whose numbers disagree with its
verdict cannot be stored.
"""

from calendar import monthrange
from datetime import datetime, timedelta

from app.shared.clock import as_utc
from .domain import ProductionValidationError

KINDS = ("session", "flex", "control_standard", "kit_standards")
KIND_LABELS = {
    "session": "Проверка калибровки: доверительный тест АКМ и LOAD",
    "flex": "Проверка тракта на изгиб",
    "control_standard": "Контрольная мера (еженедельно)",
    "kit_standards": "Меры 6650F27 (ежемесячно)",
}
LOAD_LIMIT = 1.06            # 11.4: max VSWR of the LOAD on each port over the whole band
CONFIDENCE_TX_LIMIT_DB = 0.1  # 4.1: transmission near 0 dB
FLEX_S21_LIMIT_DB = 0.05      # 14: S21 of the thru while bending
PERIODIC = ("control_standard", "kit_standards")
DUE_STATUS_LABELS = {
    "ok": "В срок",
    "overdue": "Контроль просрочен",
    "never": "Контроль просрочен — ещё не выполнялся",
    "failed": "Последняя проверка не пройдена — замеры остановить",
}


def add_month(moment: datetime) -> datetime:
    """The same day next month; the 31st becomes the last day of a shorter month."""
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return moment.replace(year=year, month=month, day=min(moment.day, monthrange(year, month)[1]))


def due_after(kind: str, moment: datetime) -> datetime:
    return moment + timedelta(days=7) if kind == "control_standard" else add_month(moment)


def expected_verdict(kind: str, values: dict) -> bool:
    """The verdict the numbers give; the program's own verdict must agree with it."""
    def need(*names):
        missing = [name for name in names if values.get(name) is None]
        if missing:
            raise ProductionValidationError(f"Для проверки «{KIND_LABELS[kind]}» не хватает значений: {', '.join(missing)}")
    if kind == "session":
        need("confidence_passed", "confidence_tx_dev_db")
        if values["confidence_passed"] and values["confidence_tx_dev_db"] > CONFIDENCE_TX_LIMIT_DB:
            raise ProductionValidationError("Доверительный тест отмечен годным, но отклонение передачи больше 0,1 дБ")
        if values["confidence_passed"]:
            need("load_p1_vswr", "load_p2_vswr")
        loads = (values.get("load_p1_vswr"), values.get("load_p2_vswr"))
        return bool(values["confidence_passed"]) and all(load is not None and load <= LOAD_LIMIT for load in loads)
    if kind == "flex":
        need("s21_dev_db", "vswr_dev")
        return values["s21_dev_db"] <= FLEX_S21_LIMIT_DB
    if kind == "control_standard":
        need("standard_inventory", "vswr_dev", "vswr_limit", "s21_dev_db", "s21_limit_db")
        return values["vswr_dev"] <= values["vswr_limit"] and values["s21_dev_db"] <= values["s21_limit_db"]
    if kind == "kit_standards":
        need("standard_inventory", "kit_ratio")
        return values["kit_ratio"] <= 1
    raise ProductionValidationError("Неизвестный вид проверки ВАЦ")


def due_items(latest: dict[str, tuple[datetime, bool, datetime | None]], now: datetime) -> list[dict]:
    """Weekly and monthly control state from the latest record of each kind and the latest passed one."""
    items = []
    for kind in PERIODIC:
        last_at, last_passed, last_pass_at = latest.get(kind, (None, None, None))
        if last_at is None:
            status, due_at = "never", None
        elif not last_passed:
            status, due_at = "failed", None
        else:
            due_at = due_after(kind, as_utc(last_pass_at))
            status = "overdue" if as_utc(now) > due_at else "ok"
        items.append({"kind": kind, "label": KIND_LABELS[kind], "last_at": as_utc(last_at) if last_at else None, "last_passed": last_passed,
                      "due_at": due_at, "status": status, "status_label": DUE_STATUS_LABELS[status]})
    return items
