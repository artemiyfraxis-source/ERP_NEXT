"""Pure production-plan rules ported from current SUPPLYMAP ProdPlan.gs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import re
from typing import Collection, Iterable, Mapping
from uuid import UUID, uuid4

from .domain import ProductionValidationError


PLAN_KINDS = {
    "note": "Комментарий",
    "block": "Блокер",
    "unblock": "Блокер снят",
    "deadline": "Дедлайн",
    "stage": "Этап КБ",
    "tcReview": "Проверка ТК",
    "status": "Статус проекта",
    "mark": "Отметка плана",
}
PLAN_BLOCKERS = ("ХОЛД", "СТОП", "ОЖИД ИНФ", "ОЖИД КОМПЛ", "ПАУЗА")
KIT_STAGES = ("Новый", "ТК готова", "КБ набрана", "В работе", "КБ на складе", "Отгружено")
TC_REVIEW_VALUES = ("не проверена", "проверена", "с погрешностями")
PLAN_STAGE_AUTO = "авто"
# ERP_PROJ_TERMINALS_ (Schema.gs): решение человека перекрывает вычисленный этап. В SUPPLYMAP — СТАТУС листа
# «Проекты»; в ERP ставится записью журнала «Статус проекта» (value «авто» — вернуть в работу), она сильнее снимка.
PROJECT_TERMINALS = ("Отказ", "Слился", "Отменён")
# Лист цеха «ЗАГРУЗ» (владелец 04.10: «тут вот такое представление я хочу»): статусы ячейки «Статус», в одной
# ячейке их может быть несколько через запятую. Порядок — как в выпадающем списке листа.
SHOP_STATUSES = (
    "ХОЛД", "СТОП", "ПАУЗА", "ПРВ КОМПЛ", "К ЗАПУСК", "В РАБОТЕ", "ОЖИД КОМПЛ", "ОЖИД ИНФ",
    "ОТК", "НА ОТГР", "ГОТОВО", "ОТГРУЖЕН",
)
# ProdPlan.gs planShopStatus_: статусы в листе набраны руками — опечатки к словарю.
_SHOP_STATUS_TYPOS = {"К ЗАПУС": "К ЗАПУСК", "ОЖД ИНФ": "ОЖИД ИНФ", "ОЖИД КОМП": "ОЖИД КОМПЛ"}
# Отметки плана (запись журнала «Отметка плана», поле `field`): ручное значение колонки листа поверх
# вычисленного; value «авто» (или пусто) возвращает вычисленное. Комментарий хранится в text.
PLAN_MARK_FLAGS = ("да", "нет")
PLAN_MARK_SUPPLIED = ("НЕТ", "ДА", "Ч/Т")
PLAN_MARK_ARRIVAL = ("Склад", "Ждем", "???")
PLAN_MARK_FIELDS = (
    "comment", "status", "ready", "in_plan", "customer_supplied", "kit_checked", "kit_present",
    "zkp", "kit_arrival", "tc_ready", "test_sb", "kit_picked",
)
PLAN_MARK_FLAG_FIELDS = ("in_plan", "kit_checked", "kit_present", "zkp", "tc_ready", "test_sb", "kit_picked")
PLAN_DAY_FIELD_PREFIX = "day:"
PLAN_DAY_NOTE_MAX = 16
_KIT_STAGE_FACTS = ("", "tc", "kit", "work", "stock", "shipped")
_HOMOGLYPHS = str.maketrans("KCEOPAHTMBXY", "КСЕОРАНТМВХУ")


def normalize_project_number(value: object) -> str:
    normalized = re.sub(r"\s+", "", str(value or "").strip().upper()).translate(_HOMOGLYPHS)
    if re.fullmatch(r"[1-9]\d*(?:К[А-Я]?|МФ)(?:\.[А-Я]{1,3})?\.?", normalized):
        return normalized.rstrip(".") + ".1"
    return normalized


MANUAL_TECHCARD_PROJECTS = frozenset(("714К.1", "35К.М.34"))


def project_from_purpose(value: object) -> str:
    purpose = re.sub(r"[\s·,;:\-—]*КБ\s*\d+[\s\S]*$", "", str(value or "").strip(), flags=re.I)
    token = re.split(r"[\s·,;]+", purpose)[0] if purpose else ""
    token = re.sub(r"\.\d{3,}$", "", token)
    return normalize_project_number(token) if token else ""


def kit_stage_key(project: object, item_index: object) -> str:
    return f"{normalize_project_number(project)}|{str(item_index or '').strip().upper()}"


def kit_key_from_purpose(value: object) -> str:
    match = re.match(r"^(.*?)[\s·,;:\-—]*(КБ\s*\d+)", str(value or "").strip(), flags=re.I)
    if not match:
        return ""
    project = re.sub(r"[\s·,;:\-—]+$", "", match[1]).strip()
    item_index = re.sub(r"\s+", "", match[2]).upper()
    return kit_stage_key(project, item_index) if project else ""


@dataclass(frozen=True)
class KitStage:
    item_index: str
    status: str
    manual: bool = False
    review: KitReview | None = None
    # Facts next to the stage: a manual stage jumps the ladder and would hide them otherwise.
    tc: bool = False
    kit: bool = False


@dataclass(frozen=True)
class KitReview:
    value: str
    text: str = ""
    who: str = ""
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class KitMark:
    stage: str | None = None
    review: KitReview | None = None


@dataclass(frozen=True)
class ProjectStage:
    status: str
    kits: tuple[KitStage, ...]
    # True when a journal stage mark with an empty KB set the stage of a project without KBs.
    manual: bool = False


def project_stage(
    project: str,
    item_indexes: Iterable[str],
    facts: Mapping[str, Collection[str]],
    terminal_status: str = "",
    marks: Mapping[str, KitMark] | None = None,
) -> ProjectStage:
    stages: list[KitStage] = []
    marks = marks or {}
    manual_tc = normalize_project_number(project) in facts.get("tc_project", ())
    for item_index in item_indexes:
        key = kit_stage_key(project, item_index)
        stage_index = len(KIT_STAGES) - 1
        while stage_index > 0:
            fact = _KIT_STAGE_FACTS[stage_index]
            if key in facts.get(fact, ()) or (fact == "tc" and manual_tc):
                break
            stage_index -= 1
        mark = marks.get(key, KitMark())
        manual_index = KIT_STAGES.index(mark.stage) if mark.stage in KIT_STAGES else -1
        # Контрактное производство: отгрузка по проекту — последний статус, ручной этап её не перекрывает
        # (владелец 29.09; в SUPPLYMAP ручной этап перекрывал и отгрузку).
        if key in facts.get("shipped", ()):
            manual_index = -1
        stages.append(KitStage(
            str(item_index).strip().upper(),
            KIT_STAGES[manual_index if manual_index >= 0 else stage_index],
            manual_index >= 0,
            mark.review,
            tc=key in facts.get("tc", ()) or manual_tc,
            kit=key in facts.get("kit", ()),
        ))
    project_index = min(
        (KIT_STAGES.index(stage.status) for stage in stages),
        default=1 if manual_tc else 0,
    )
    if terminal_status:
        return ProjectStage(terminal_status, tuple(stages))
    # A project without KBs (a shop row with no calculation, or already shipped) takes its stage
    # from a journal stage mark with an empty KB; a project with KBs ignores such a mark.
    project_mark = marks.get(kit_stage_key(project, ""), KitMark())
    if not stages and project_mark.stage in KIT_STAGES:
        return ProjectStage(project_mark.stage, (), manual=True)
    return ProjectStage(KIT_STAGES[project_index], tuple(stages))


def supply_bucket(status: object, in_purchase: bool) -> str:
    normalized = str(status or "").strip().casefold()
    if normalized == "отменено":
        return ""
    if normalized in {"готово", "закрыто"}:
        return "stock"
    if normalized in {"в пути", "карантин"}:
        return "transit"
    return "buying" if in_purchase else "request"


def _date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    match = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})", str(value or ""))
    if not match:
        return None
    try:
        return date(int(match[3]), int(match[2]), int(match[1]))
    except ValueError:
        return None


def days_left(deadline: object, today: date | datetime) -> int | None:
    parsed = _date(deadline)
    if parsed is None:
        return None
    current = today.date() if isinstance(today, datetime) else today
    return (parsed - current).days


@dataclass(frozen=True)
class SupplyPosition:
    project: str
    bucket: str
    eta: str = ""
    customer_supplied: bool = False


@dataclass(frozen=True)
class SupplySummary:
    total: int = 0
    stock: int = 0
    transit: int = 0
    buying: int = 0
    request: int = 0
    customer_supplied: int = 0
    customer_supplied_stock: int = 0
    eta: str = ""


def summarize_supply(positions: Iterable[SupplyPosition]) -> dict[str, SupplySummary]:
    counters: dict[str, dict[str, int | str]] = {}
    for position in positions:
        if not position.project or not position.bucket:
            continue
        project = normalize_project_number(position.project)
        row = counters.setdefault(project, {
            "total": 0, "stock": 0, "transit": 0, "buying": 0, "request": 0,
            "customer_supplied": 0, "customer_supplied_stock": 0, "eta": "",
        })
        row["total"] += 1
        row[position.bucket] += 1
        if position.customer_supplied:
            row["customer_supplied"] += 1
            if position.bucket == "stock":
                row["customer_supplied_stock"] += 1
        current_eta = str(row["eta"])
        if position.bucket != "stock" and (_date(position.eta) or date.min) > (_date(current_eta) or date.min):
            row["eta"] = position.eta
    return {project: SupplySummary(**row) for project, row in counters.items()}


@dataclass(frozen=True)
class PlanEvent:
    project: str
    occurred_at: datetime
    who: str
    kind: str
    blocker: str = ""
    text: str = ""
    deadline: date | None = None
    item_index: str = ""
    value: str = ""
    sequence: int = 0
    id: UUID = field(default_factory=uuid4)
    # Колонка листа «ЗАГРУЗ» у записи «Отметка плана» (comment, status, … или day:ГГГГ-ММ-ДД).
    field: str = ""


@dataclass(frozen=True)
class PlanProjectSnapshot:
    project: str
    customer: str = ""
    opened_on: date | None = None
    deadline: date | None = None
    status: str = ""
    terminal: bool = False
    imported_at: datetime | None = None


@dataclass(frozen=True)
class ActiveBlocker:
    type: str
    text: str
    since: datetime
    who: str


@dataclass(frozen=True)
class LastComment:
    occurred_at: datetime
    who: str
    text: str


@dataclass(frozen=True)
class ProjectJournal:
    blocker: ActiveBlocker | None
    last: LastComment | None
    events: tuple[PlanEvent, ...]
    deadline: date | None = None
    # Последняя запись «Статус проекта»: None — записей нет (решает снимок реестра), "" — возвращён в работу.
    status: str | None = None


def summarize_journal(events: Iterable[PlanEvent]) -> dict[str, ProjectJournal]:
    state: dict[str, dict[str, object]] = {}
    for event in events:
        project = normalize_project_number(event.project)
        if not project:
            continue
        row = state.setdefault(project, {"blocker": None, "last": None, "events": [], "deadline": None, "status": None})
        row["events"].insert(0, event)
        if event.kind == PLAN_KINDS["block"]:
            row["blocker"] = ActiveBlocker(event.blocker, event.text, event.occurred_at, event.who)
        elif event.kind == PLAN_KINDS["unblock"]:
            row["blocker"] = None
        elif event.kind == PLAN_KINDS["deadline"]:
            row["deadline"] = event.deadline
        elif event.kind == PLAN_KINDS["status"]:
            row["status"] = event.value if event.value in PROJECT_TERMINALS else ""
        if event.text and event.kind in {
            PLAN_KINDS["note"], PLAN_KINDS["block"], PLAN_KINDS["unblock"],
        }:
            row["last"] = LastComment(event.occurred_at, event.who, event.text)
    return {
        project: ProjectJournal(row["blocker"], row["last"], tuple(row["events"]), row["deadline"], row["status"])
        for project, row in state.items()
    }


def project_terminal(snapshot: PlanProjectSnapshot | None, journal: ProjectJournal | None) -> str:
    """Терминальный статус проекта (Отказ/Слился/Отменён) или "": запись журнала сильнее снимка реестра."""
    if journal is not None and journal.status is not None:
        return journal.status
    return snapshot.status if snapshot is not None and snapshot.terminal and snapshot.status else ""


def summarize_kit_marks(events: Iterable[PlanEvent]) -> dict[str, KitMark]:
    marks: dict[str, KitMark] = {}
    for event in events:
        if event.kind not in {PLAN_KINDS["stage"], PLAN_KINDS["tcReview"]}:
            continue
        key = kit_stage_key(event.project, event.item_index)
        current = marks.get(key, KitMark())
        if event.kind == PLAN_KINDS["stage"]:
            stage = None if event.value == PLAN_STAGE_AUTO else event.value
            marks[key] = KitMark(stage=stage, review=current.review)
        else:
            marks[key] = KitMark(
                stage=current.stage,
                review=KitReview(event.value, event.text, event.who, event.occurred_at),
            )
    return marks


def normalize_shop_statuses(value: object) -> str:
    """ProdPlan.gs planShopStatus_ for each comma-separated status; unknown ones are kept for validation."""
    result: list[str] = []
    for part in str(value or "").split(","):
        status = re.sub(r"[.\s]+$", "", re.sub(r"\s+", " ", part.upper())).strip()
        status = _SHOP_STATUS_TYPOS.get(status, status)
        if status and status not in result:
            result.append(status)
    return ", ".join(result)


def plan_mark_value(field_name: str, value: object, text: object = "") -> str:
    """Validated, normalized value of a plan mark; "" clears the override. Raises on a bad value."""
    raw = str(value or "").strip()
    if field_name == "comment":
        return str(text or "").strip()
    if raw in ("", PLAN_STAGE_AUTO):
        return ""
    if field_name.startswith(PLAN_DAY_FIELD_PREFIX):
        if _iso_date(field_name[len(PLAN_DAY_FIELD_PREFIX):]) is None:
            raise ProductionValidationError("Неизвестный день плана.")
        if len(raw) > PLAN_DAY_NOTE_MAX:
            raise ProductionValidationError(f"Отметка дня — до {PLAN_DAY_NOTE_MAX} знаков.")
        return raw
    if field_name not in PLAN_MARK_FIELDS:
        raise ProductionValidationError("Неизвестная колонка плана.")
    if field_name == "status":
        statuses = normalize_shop_statuses(raw)
        unknown = [status for status in statuses.split(", ") if status not in SHOP_STATUSES]
        if unknown:
            raise ProductionValidationError(f"Неизвестный статус: {', '.join(unknown)}.")
        return statuses
    if field_name == "ready":
        if not re.fullmatch(r"\d{1,3}", raw) or int(raw) > 100:
            raise ProductionValidationError("Готовность — число от 0 до 100.")
        return str(int(raw))
    if field_name in PLAN_MARK_FLAG_FIELDS:
        if raw not in PLAN_MARK_FLAGS:
            raise ProductionValidationError("Отметка — да или нет.")
        return raw
    if field_name == "customer_supplied":
        if raw.upper() not in PLAN_MARK_SUPPLIED:
            raise ProductionValidationError("Давальческое — НЕТ, ДА или Ч/Т.")
        return raw.upper()
    # kit_arrival: a date or one of the sheet words.
    if raw in PLAN_MARK_ARRIVAL:
        return raw
    parsed = _iso_date(raw) or _date(raw)
    if parsed is None:
        raise ProductionValidationError("Дата прихода — дата, «Склад», «Ждем» или «???».")
    return parsed.isoformat()


def _iso_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        return None


def summarize_plan_marks(events: Iterable[PlanEvent]) -> dict[str, dict[str, str]]:
    """Current manual value per project and sheet column; the latest mark wins, an empty one clears."""
    marks: dict[str, dict[str, str]] = {}
    for event in events:
        if event.kind != PLAN_KINDS["mark"] or not event.field:
            continue
        project = normalize_project_number(event.project)
        current = marks.setdefault(project, {})
        value = event.text if event.field == "comment" else event.value
        if value and value != PLAN_STAGE_AUTO:
            current[event.field] = value
        else:
            current.pop(event.field, None)
    return marks


def validate_plan_note(payload: Mapping[str, object], known_projects: Iterable[str]) -> None:
    known = {normalize_project_number(project) for project in known_projects}
    project = normalize_project_number(payload.get("project"))
    kind = str(payload.get("kind") or "")
    if not project or project not in known:
        raise ProductionValidationError("Проект не найден в реестре.")
    if kind not in PLAN_KINDS:
        raise ProductionValidationError("Неизвестный вид записи.")
    if kind == "block" and payload.get("blocker") not in PLAN_BLOCKERS:
        raise ProductionValidationError("Выбери вид блокера.")
    if kind in {"note", "block"} and not str(payload.get("text") or "").strip():
        raise ProductionValidationError("Напиши, что происходит.")
    if kind == "deadline" and _date(payload.get("deadline")) is None:
        raise ProductionValidationError("Укажи дату дедлайна.")
    # A stage mark with an empty KB is the stage of the whole project; a techcard review always needs a KB.
    item_index = str(payload.get("item_index") or "").strip()
    if (kind == "tcReview" or (kind == "stage" and item_index)) and not re.match(r"^КБ\d+", item_index, flags=re.I):
        raise ProductionValidationError("Укажи КБ.")
    if kind == "stage" and payload.get("value") not in (*KIT_STAGES, PLAN_STAGE_AUTO):
        raise ProductionValidationError("Неизвестный этап.")
    if kind == "status" and payload.get("value") not in (*PROJECT_TERMINALS, PLAN_STAGE_AUTO):
        raise ProductionValidationError("Неизвестный статус проекта.")
    if kind == "tcReview" and payload.get("value") not in TC_REVIEW_VALUES:
        raise ProductionValidationError("Неизвестная отметка проверки.")
    if kind == "tcReview" and payload.get("value") == "с погрешностями" and not str(payload.get("text") or "").strip():
        raise ProductionValidationError("Напиши, какие погрешности.")
    if kind == "mark":
        plan_mark_value(str(payload.get("field") or ""), payload.get("value"), payload.get("text"))
