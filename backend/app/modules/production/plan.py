"""Pure production-plan rules ported from current SUPPLYMAP ProdPlan.gs."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
import re
from typing import Collection, Iterable, Mapping
from uuid import UUID, uuid4

from app.shared.project_number import normalize_project_number, project_from_purpose  # noqa: F401 — зовут отсюда

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
# Владелец 29.10.2026: «чем стоп от холда отличается и стоп от паузы — не нужно раздувать статусы». Один блокер
# «СТОП»; старые записи журнала (неизменяемы) с ХОЛД / ПАУЗА читаются как «СТОП», новые — отказ (422, CHECK 0161).
PLAN_BLOCKERS = ("СТОП", "ОЖИД ИНФ", "ОЖИД КОМПЛ")
LEGACY_BLOCKER_ALIASES = {"ХОЛД": "СТОП", "ПАУЗА": "СТОП"}
# Одна короткая лестница этапов на всё ERP (владелец 29.10.2026: «можно убрать распланировано пока что и сделать
# возможно двух статусов одновременно КБ набрана и ТК готова»): «В работе» — кто-то начал работу (смена по заданию КБ
# или «взял» по доле операции); «Готово» — производство закончено (весь тираж КБ сделан: задания «Выполнено» / сдано
# площадкой), ОТК ещё не трогал; «ОТК» — сделанное на проверке (коробка последней операции в очереди ОТК или уже
# проверена ОТК); «ОТК пройден» — ОТК принял весь тираж (пока заглушка: ставится руками, сам — только когда проверки
# ОТК в ERP покрыли весь тираж); ГП ещё не принята на склад (владелец 29.10: «Готово — это то, что производство
# закончено; ОТК — то, что на проверке»; «сделаем заглушку после Готово — на ОТК и ОТК пройден»);
# «Принято складом» — ГП принята (принято ≥ нужно);
# «Упаковано» — ГП отобрана в отправки и упакована (отобрано ≥ нужно), ещё не уехала; «Отгружено» — уехало (отправлено ≥ нужно).
# «ТК готова» и «КБ набрана» — не этапы, а две независимые отметки да/нет рядом с этапом (`KitStage.tc` / `.kit`,
# проект — `project_markers`).
KIT_STAGES = ("Новый", "В работе", "Готово", "ОТК", "ОТК пройден", "Принято складом", "Упаковано", "Отгружено")
# Старые записи журнала «Этап КБ» неизменяемы (0101): их слово читается как этап новой лестницы; новые записи
# со старым словом не принимаются (422 в приложении, CHECK 0108 / 0161 в базе).
LEGACY_STAGE_ALIASES = {
    "КБ на складе": "Принято складом", "Готово к отгрузке": "Принято складом", "На отгрузке": "Упаковано",
}
# Бывшие этапы, ставшие отметками (29.10): старая запись «Этап КБ» с этим словом — не ручной этап (решают факты),
# а включённые отметки КБ: «ТК готова» — ТК; «КБ набрана» / «Распланировано» — ТК и КБ.
LEGACY_MARKER_STAGES = {"ТК готова": ("tc",), "КБ набрана": ("tc", "kit"), "Распланировано": ("tc", "kit")}
_LEGACY_STAGE_REFUSALS = {
    "ТК готова": "Этапа «ТК готова» больше нет — это отметка «ТК готова» рядом с этапом.",
    "КБ набрана": "Этапа «КБ набрана» больше нет — это отметка «КБ набрана» рядом с этапом.",
    "Распланировано": "Этапа «Распланировано» больше нет — до начала работы этап «Новый», затем «В работе».",
}
# Отметки проекта: ручной переключатель — запись «Отметка плана» с этим полем (да / нет / авто).
MARKER_FIELDS = {"tc_ready": "ТК готова", "kit_picked": "КБ набрана"}
TC_REVIEW_VALUES = ("не проверена", "проверена", "с погрешностями")
PLAN_STAGE_AUTO = "авто"
# ERP_PROJ_TERMINALS_ (Schema.gs): решение человека перекрывает вычисленный этап. В SUPPLYMAP — СТАТУС листа
# «Проекты»; в ERP ставится записью журнала «Статус проекта» (value «авто» — вернуть в работу), она сильнее снимка.
# Владелец 12.10: закрыть проект можно только «Отменён»; «Отказ» и «Слился» в старых записях читаются как «Отменён».
PROJECT_TERMINALS = ("Отменён",)
LEGACY_TERMINAL_ALIASES = {"Отказ": "Отменён", "Слился": "Отменён"}
# Лист цеха «ЗАГРУЗ» (владелец 04.10: «тут вот такое представление я хочу»): статусы ячейки «Статус», в одной
# ячейке их может быть несколько через запятую. Порядок — как в выпадающем списке листа.
# 29.10: ХОЛД / ПАУЗА — теперь «СТОП»; «К ЗАПУСК» и «РАСПЛАН» были этапами «КБ набрана» / «Распланировано» — их нет.
SHOP_STATUSES = (
    "СТОП", "ПРВ КОМПЛ", "В РАБОТЕ", "ОЖИД КОМПЛ", "ОЖИД ИНФ", "ОТК", "НА ОТГР", "ГОТОВО", "УПАК", "ОТГРУЖЕН",
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
# Owner 12.10: статус листа сквозной (этап проекта + блокер); старые отметки `status` читаются как история и не действуют.
STATUS_MARK_REFUSAL = "Статус в загрузке цеха берётся из этапа проекта — меняйте этап"
PLAN_DAY_FIELD_PREFIX = "day:"
PLAN_DAY_NOTE_MAX = 16
# Факт, дающий этап лестницы (тот же порядок, что KIT_STAGES): work — работа начата, made — производство закончено,
# qc — сделанное у ОТК (в очереди / проверено), passed — ОТК проверил весь тираж, stock — ГП принята, packed —
# упакована в отправки, shipped — уехало. tc / kit — отметки, не этапы.
_KIT_STAGE_FACTS = ("", "work", "made", "qc", "passed", "stock", "packed", "shipped")
# С начала работы КБ считается набранной (правило листа «ЗАГРУЗ»): этап «В работе» и дальше — «КБ набрана» да.
_WORK_INDEX = KIT_STAGES.index("В работе")
# № проекта сравнивается по `app.shared.project_number` (normalize_project_number / project_from_purpose выше).
MANUAL_TECHCARD_PROJECTS = frozenset(("714К.1", "35К.М.34"))


def stage_alias(value: object) -> str:
    """A stored stage word as the current ladder reads it: an old word (from an immutable journal row) → its new name."""
    text = str(value or "")
    return LEGACY_STAGE_ALIASES.get(text, text)


def terminal_alias(value: object) -> str:
    """A stored closed-project word as the current list reads it («Отказ» / «Слился» → «Отменён»)."""
    text = str(value or "")
    return LEGACY_TERMINAL_ALIASES.get(text, text)


def blocker_alias(value: object) -> str:
    """A stored blocker word as the current list reads it («ХОЛД» / «ПАУЗА» → «СТОП»)."""
    text = str(value or "")
    return LEGACY_BLOCKER_ALIASES.get(text, text)


def legacy_stage_refusal(value: object) -> str:
    """The refusal for a new write of an old stage word ("" — not an old word)."""
    text = str(value or "").strip()
    if text in _LEGACY_STAGE_REFUSALS:
        return _LEGACY_STAGE_REFUSALS[text]
    return f"Этапа «{text}» больше нет — теперь «{LEGACY_STAGE_ALIASES[text]}»." if text in LEGACY_STAGE_ALIASES else ""


def legacy_blocker_refusal(value: object) -> str:
    """The refusal for a new blocker with an old word ("" — not an old word)."""
    text = str(value or "").strip()
    if text not in LEGACY_BLOCKER_ALIASES:
        return ""
    return f"Блокера «{text}» больше нет — теперь «{LEGACY_BLOCKER_ALIASES[text]}»."


def require_stage_value(value: object) -> str:
    """A stage pick (a ladder stage or «авто») or a Russian refusal; the old «КБ на складе» is refused by name."""
    text = str(value or "")
    if legacy_stage_refusal(text):
        raise ProductionValidationError(legacy_stage_refusal(text))
    if text not in (*KIT_STAGES, PLAN_STAGE_AUTO):
        raise ProductionValidationError("Неизвестный этап.")
    return text


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
    # Markers next to the stage (owner 29.10), independent of it: «ТК готова» — techcard issued, a manual-techcard
    # project or an old «ТК готова» / «КБ набрана» / «Распланировано» stage row; «КБ набрана» — kit request picked,
    # work begun (stage «В работе» and on) or an old «КБ набрана» / «Распланировано» stage row.
    tc: bool = False
    kit: bool = False
    # Stages a manual pick jumped over and no fact covers since (ladder order): the card shows them as skipped.
    skipped: tuple[str, ...] = ()


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
    # Stages jumped by the manual picks since the last «авто» (ladder order), below the current manual stage.
    skipped: tuple[str, ...] = ()
    # Markers an old «ТК готова» / «КБ набрана» / «Распланировано» stage row turned on (cleared by «авто» / «Новый»).
    tc: bool = False
    kit: bool = False


@dataclass(frozen=True)
class ProjectStage:
    status: str
    kits: tuple[KitStage, ...]
    # True when a journal stage mark with an empty KB set the stage of a project without KBs.
    manual: bool = False
    # Project markers from the facts: every КБ has it (a project without KBs — its own facts and marks). The manual
    # toggle on top — `project_markers`.
    tc: bool = False
    kit: bool = False


@dataclass(frozen=True)
class ProjectMarkers:
    """«ТК готова» / «КБ набрана» of a project: the value shown, the computed one and the manual toggle ("да" / "нет";
    None — «авто», the facts decide)."""

    tc_ready: bool
    kit_picked: bool
    tc_auto: bool
    kit_auto: bool
    tc_manual: str | None = None
    kit_manual: str | None = None

    def payload(self) -> dict[str, object]:
        """The API shape (plan rows, project list and card): shown value and the manual toggle (None — «авто»)."""
        return {
            "tc_ready": self.tc_ready, "kit_picked": self.kit_picked,
            "tc_manual": self.tc_manual, "kit_manual": self.kit_manual,
        }


def project_markers(stage: ProjectStage, plan_marks: Mapping[str, str] | None = None) -> ProjectMarkers:
    """The one marker rule (sheet columns «ТК готова» / «Комплект набран», project list, card, plan rows): the
    project's computed markers, overridden by its plan marks `tc_ready` / `kit_picked` (`summarize_plan_marks`)."""
    plan_marks = plan_marks or {}
    tc_manual = plan_marks.get("tc_ready") if plan_marks.get("tc_ready") in PLAN_MARK_FLAGS else None
    kit_manual = plan_marks.get("kit_picked") if plan_marks.get("kit_picked") in PLAN_MARK_FLAGS else None
    return ProjectMarkers(
        tc_ready=stage.tc if tc_manual is None else tc_manual == "да",
        kit_picked=stage.kit if kit_manual is None else kit_manual == "да",
        tc_auto=stage.tc, kit_auto=stage.kit, tc_manual=tc_manual, kit_manual=kit_manual,
    )


def _fact_index(key: str, facts: Mapping[str, Collection[str]]) -> int:
    """Highest ladder stage the facts alone give the КБ (0 — «Новый»)."""
    stage_index = len(KIT_STAGES) - 1
    while stage_index > 0:
        if key in facts.get(_KIT_STAGE_FACTS[stage_index], ()):
            break
        stage_index -= 1
    return stage_index


# Факты склада (владелец 15.10.2026: «не произошла смена статуса после принятия на склад»): приёмка ГП, упаковка и
# отгрузка — физические события, ручной этап ниже них их не перекрывает и ниже них не опускается.
_WAREHOUSE_FACTS = ("shipped", "packed", "stock")


def _warehouse_floor(key: str, facts: Mapping[str, Collection[str]]) -> int:
    """Ladder index of the highest warehouse fact of the КБ (-1 — none)."""
    for fact in _WAREHOUSE_FACTS:
        if key in facts.get(fact, ()):
            return _KIT_STAGE_FACTS.index(fact)
    return -1


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
        stage_index = _fact_index(key, facts)
        mark = marks.get(key, KitMark())
        manual_index = KIT_STAGES.index(mark.stage) if mark.stage in KIT_STAGES else -1
        # Контрактное производство: отгрузка по проекту — последний статус, ручной этап её не перекрывает
        # (владелец 29.09; в SUPPLYMAP ручной этап перекрывал и отгрузку); так же приёмка ГП и упаковка (15.10).
        if manual_index < _warehouse_floor(key, facts):
            manual_index = -1
        status_index = manual_index if manual_index >= 0 else stage_index
        stages.append(KitStage(
            str(item_index).strip().upper(),
            KIT_STAGES[status_index],
            manual_index >= 0,
            mark.review,
            tc=key in facts.get("tc", ()) or manual_tc or mark.tc,
            kit=key in facts.get("kit", ()) or mark.kit or status_index >= _WORK_INDEX,
            skipped=tuple(stage for stage in mark.skipped if stage_index < KIT_STAGES.index(stage) < status_index),
        ))
    project_index = min((KIT_STAGES.index(stage.status) for stage in stages), default=0)
    # A project without KBs (a shop row with no calculation, or already shipped) takes its stage
    # from a journal stage mark with an empty KB; a project with KBs ignores such a mark.
    project_mark = marks.get(kit_stage_key(project, ""), KitMark())
    manual = not stages and project_mark.stage in KIT_STAGES
    status = project_mark.stage if manual else KIT_STAGES[project_index]
    if stages:
        tc, kit = all(stage.tc for stage in stages), all(stage.kit for stage in stages)
    else:
        tc = manual_tc or project_mark.tc
        kit = project_mark.kit or KIT_STAGES.index(status) >= _WORK_INDEX
    if terminal_status:
        return ProjectStage(terminal_status, tuple(stages), tc=tc, kit=kit)
    return ProjectStage(status, tuple(stages), manual=manual, tc=tc, kit=kit)


# ── Ручная смена этапа: карточка проекта, этап КБ, «Производственный план» ─────────────────────────────────
# Владелец 10.10: «быстро руками сменить… пропуская этап, который был пропущен вживую». Вперёд — без причины, назад —
# с причиной; проект целиком только поднимает отставшие КБ и не понижает ушедшие вперёд; ниже факта отгрузки не
# опускается. Смена этапа — только записи журнала: склад, отгрузки, задания цеха и заявки не трогаются.
STAGE_FORWARD = "forward"
STAGE_BACK = "back"
STAGE_AUTO = "auto"
STAGE_NONE = "none"


@dataclass(frozen=True)
class KitStageNow:
    """A КБ's stage right now. ``item_index`` "" — a project without KBs (its stage is a project mark)."""

    item_index: str
    status: str
    # A manual stage mark is set, even one a shipment hides (only «авто» clears it).
    manual: bool
    # The stage the facts alone give — what «авто» returns to.
    auto_status: str
    shipped: bool = False
    # The warehouse fact the КБ is held at («Принято складом» / «Упаковано» / «Отгружено»; "" — none): no lower stage.
    floor: str = ""

    @property
    def held_at(self) -> str:
        return self.floor or ("Отгружено" if self.shipped else "")


@dataclass(frozen=True)
class KitStageChange:
    item_index: str
    from_stage: str
    to_stage: str
    # What the journal row stores: the stage, or «авто».
    value: str
    skipped: tuple[str, ...] = ()


@dataclass(frozen=True)
class KitStageKept:
    item_index: str
    stage: str
    note: str


@dataclass(frozen=True)
class StagePlan:
    value: str
    kb: str
    direction: str
    changes: tuple[KitStageChange, ...] = ()
    kept: tuple[KitStageKept, ...] = ()
    error: str = ""

    @property
    def reason_required(self) -> bool:
        return self.direction == STAGE_BACK


def stage_skipped(from_stage: object, to_stage: object) -> tuple[str, ...]:
    """Stages strictly between two ladder stages when moving forward (ladder order); () otherwise."""
    if from_stage not in KIT_STAGES or to_stage not in KIT_STAGES:
        return ()
    return KIT_STAGES[KIT_STAGES.index(from_stage) + 1:KIT_STAGES.index(to_stage)]


def project_stage_now(
    project: str,
    item_indexes: Iterable[str],
    facts: Mapping[str, Collection[str]],
    marks: Mapping[str, KitMark] | None = None,
) -> tuple[KitStageNow, ...]:
    """Per КБ: the stage now, the stage the facts give, a manual mark, a shipment — the ladder of `project_stage`.
    A project without KBs is one entry with an empty КБ (its project mark)."""
    marks = marks or {}
    kits: list[KitStageNow] = []
    for item_index in dict.fromkeys(str(item or "").strip().upper() for item in item_indexes):
        if not item_index:
            continue
        key = kit_stage_key(project, item_index)
        fact_index = _fact_index(key, facts)
        mark = marks.get(key, KitMark())
        shipped = key in facts.get("shipped", ())
        floor = _warehouse_floor(key, facts)
        manual = mark.stage in KIT_STAGES
        status = mark.stage if manual and KIT_STAGES.index(mark.stage) >= floor else KIT_STAGES[fact_index]
        kits.append(KitStageNow(item_index, status, manual, KIT_STAGES[fact_index], shipped, KIT_STAGES[floor] if floor >= 0 else ""))
    if not kits:
        mark = marks.get(kit_stage_key(project, ""), KitMark())
        auto = KIT_STAGES[0]
        manual = mark.stage in KIT_STAGES
        kits.append(KitStageNow("", mark.stage if manual else auto, manual, auto))
    return tuple(kits)


def kit_label(item_index: str) -> str:
    return item_index or "Проект"


def plan_stage_change(kits: Iterable[KitStageNow], value: object, kb: object = "") -> StagePlan:
    """What a manual pick of ``value`` (a ladder stage or «авто») does to one КБ (``kb``) or to the whole project.

    A refused pick comes back with ``error`` (the preview shows it); an unknown stage or КБ raises."""
    value = require_stage_value(str(value or "").strip())
    kb = str(kb or "").strip().upper()
    kits = tuple(kits)
    if kb:
        if not re.match(r"^КБ\d+", kb, flags=re.I):
            raise ProductionValidationError("Укажи КБ.")
        targets = tuple(kit for kit in kits if kit.item_index == kb)
        if not targets:
            raise ProductionValidationError(f"В проекте нет {kb}.")
    else:
        targets = kits
    if value == PLAN_STAGE_AUTO:
        changes = tuple(
            KitStageChange(kit.item_index, kit.status, kit.auto_status, PLAN_STAGE_AUTO)
            for kit in targets if kit.manual
        )
        kept = tuple(KitStageKept(kit.item_index, kit.status, "Этап и так по факту.") for kit in targets if not kit.manual)
        return StagePlan(value, kb, STAGE_AUTO if changes else STAGE_NONE, changes, kept)

    target = KIT_STAGES.index(value)

    def change(kit: KitStageNow) -> KitStageChange:
        forward = target > KIT_STAGES.index(kit.status)
        return KitStageChange(kit.item_index, kit.status, value, value, stage_skipped(kit.status, value) if forward else ())

    def kept(kit: KitStageNow, back: bool = False) -> KitStageKept:
        current = KIT_STAGES.index(kit.status)
        if current == target:
            note = "Уже на этом этапе."
        elif back:
            note = f"{kit.held_at} по факту — ниже не опускаем."
        else:
            note = "Выше выбранного этапа — не понижаем."
        return KitStageKept(kit.item_index, kit.status, note)

    floor = min(KIT_STAGES.index(kit.status) for kit in targets)
    if target == floor:
        return StagePlan(value, kb, STAGE_NONE, (), tuple(kept(kit) for kit in targets))
    if target > floor:
        # Вперёд: поднимаются только отставшие КБ, ушедшие дальше не понижаются.
        return StagePlan(
            value, kb, STAGE_FORWARD,
            tuple(change(kit) for kit in targets if KIT_STAGES.index(kit.status) < target),
            tuple(kept(kit) for kit in targets if KIT_STAGES.index(kit.status) >= target),
        )
    # Назад: причина обязательна, ниже факта склада (принято / упаковано / отгружено) не опускаем.
    def held_back(kit: KitStageNow) -> bool:
        return bool(kit.held_at) and target < KIT_STAGES.index(kit.held_at)

    lowered = tuple(change(kit) for kit in targets if not held_back(kit))
    held = tuple(kept(kit, back=True) for kit in targets if held_back(kit))
    error = ""
    if not lowered:
        floors = {kit.held_at for kit in targets}
        if floors == {"Отгружено"}:
            error = (
                f"{kb} отгружена по факту — этап ниже «Отгружено» не ставится." if kb
                else "Все КБ проекта отгружены по факту — этап ниже «Отгружено» не ставится."
            )
        else:
            lowest = min(floors, key=KIT_STAGES.index)
            error = (
                f"{kb} по факту склада — «{lowest}»: этап ниже не ставится." if kb
                else f"Все КБ проекта по факту склада не ниже «{lowest}»: этап ниже не ставится."
            )
    return StagePlan(value, kb, STAGE_BACK, lowered, held, error)


def stage_change_text(change: KitStageChange, reason: str = "") -> str:
    """Journal text of one «Этап КБ» row: from → to, the skipped stages, the reason."""
    text = f"{change.from_stage} → {change.to_stage}"
    if change.value == PLAN_STAGE_AUTO:
        text += " (авто)"
    if change.skipped:
        text += f" · пропущены: {', '.join(change.skipped)}"
    if reason:
        text += f" · причина: {reason}"
    return text


def stage_plan_message(plan: StagePlan) -> str:
    if plan.error:
        return plan.error
    if not plan.changes:
        return "Без изменений: " + "; ".join(f"{kit_label(item.item_index)} — {item.note}" for item in plan.kept)
    parts = []
    for change in plan.changes:
        part = f"{kit_label(change.item_index)}: {change.from_stage} → {change.to_stage}"
        if change.skipped:
            part += f" (пропущены: {', '.join(change.skipped)})"
        parts.append(part)
    return "; ".join(parts) + "."


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


def work_days_left(
    deadline: object, today: date | datetime, holidays: Collection[date] = (), weekdays: Collection[int] = (0, 1, 2, 3, 4),
) -> int | None:
    """Рабочих дней до дедлайна (владелец 14.10): рабочие дни после сегодня по день дедлайна включительно — по графику
    смены (`weekdays`) без праздников календаря цеха; просрочка — минус рабочие дни после дедлайна по сегодня."""
    parsed = _date(deadline)
    if parsed is None:
        return None
    current = today.date() if isinstance(today, datetime) else today
    start, end, sign = (current, parsed, 1) if parsed >= current else (parsed, current, -1)
    count, day = 0, start + timedelta(days=1)
    while day <= end:
        if day.weekday() in weekdays and day not in holidays:
            count += 1
        day += timedelta(days=1)
    return sign * count


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
    # Bought positions (paid: «В пути», «Карантин», or already «Готово»), customer-supplied ones aside — the ЗКП column.
    purchased: int = 0
    eta: str = ""


def summarize_supply(positions: Iterable[SupplyPosition]) -> dict[str, SupplySummary]:
    counters: dict[str, dict[str, int | str]] = {}
    for position in positions:
        if not position.project or not position.bucket:
            continue
        project = normalize_project_number(position.project)
        row = counters.setdefault(project, {
            "total": 0, "stock": 0, "transit": 0, "buying": 0, "request": 0,
            "customer_supplied": 0, "customer_supplied_stock": 0, "purchased": 0, "eta": "",
        })
        row["total"] += 1
        row[position.bucket] += 1
        if position.customer_supplied:
            row["customer_supplied"] += 1
            if position.bucket == "stock":
                row["customer_supplied_stock"] += 1
        elif position.bucket in {"transit", "stock"}:
            row["purchased"] += 1
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
    # «Этап КБ»: этап до смены ("" — запись до 0100); «Статус проекта»: закрытый статус, из которого вернули в работу.
    from_value: str = ""
    # Причина ручной смены этапа (назад — обязательна).
    reason: str = ""


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
            # An old ХОЛД / ПАУЗА row (immutable journal) is the one «СТОП» now, whatever store the event came from.
            row["blocker"] = ActiveBlocker(blocker_alias(event.blocker), event.text, event.occurred_at, event.who)
        elif event.kind == PLAN_KINDS["unblock"]:
            row["blocker"] = None
        elif event.kind == PLAN_KINDS["deadline"]:
            row["deadline"] = event.deadline
        elif event.kind == PLAN_KINDS["status"]:
            row["status"] = terminal_alias(event.value) if terminal_alias(event.value) in PROJECT_TERMINALS else ""
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
    return terminal_alias(snapshot.status) if snapshot is not None and snapshot.terminal and snapshot.status else ""


def summarize_kit_marks(events: Iterable[PlanEvent]) -> dict[str, KitMark]:
    marks: dict[str, KitMark] = {}
    for event in events:
        if event.kind not in {PLAN_KINDS["stage"], PLAN_KINDS["tcReview"]}:
            continue
        key = kit_stage_key(event.project, event.item_index)
        current = marks.get(key, KitMark())
        if event.kind == PLAN_KINDS["stage"]:
            if event.value in LEGACY_MARKER_STAGES:
                # A former stage that is a marker now (29.10): no manual stage — the facts decide — and its markers on.
                markers = LEGACY_MARKER_STAGES[event.value]
                marks[key] = KitMark(review=current.review, tc="tc" in markers, kit="kit" in markers)
                continue
            # An old word (immutable journal row) reads as the current ladder stage, whatever store the event came from.
            stage = None if event.value == PLAN_STAGE_AUTO else stage_alias(event.value)
            skipped: tuple[str, ...] = ()
            if stage in KIT_STAGES:
                # Пропущенное вживую копится по ручным шагам вперёд; шаг назад срезает то, что выше нового этапа.
                jumped = (*current.skipped, *stage_skipped(stage_alias(event.from_value), stage))
                skipped = tuple(item for item in KIT_STAGES[:KIT_STAGES.index(stage)] if item in jumped)
            # Markers of an old marker-stage row hold through later stages; «авто» and «Новый» clear them.
            keep = stage in KIT_STAGES and stage != KIT_STAGES[0]
            marks[key] = KitMark(
                stage=stage, review=current.review, skipped=skipped, tc=current.tc and keep, kit=current.kit and keep,
            )
        else:
            marks[key] = replace(current, review=KitReview(event.value, event.text, event.who, event.occurred_at))
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
    if field_name == "status":
        raise ProductionValidationError(STATUS_MARK_REFUSAL)
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
        if event.kind != PLAN_KINDS["mark"] or not event.field or event.field == "status":
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
    if kind == "block" and legacy_blocker_refusal(payload.get("blocker")):
        raise ProductionValidationError(legacy_blocker_refusal(payload.get("blocker")))
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
    if kind == "stage":
        require_stage_value(payload.get("value"))
    if kind == "status" and payload.get("value") not in (*PROJECT_TERMINALS, PLAN_STAGE_AUTO):
        raise ProductionValidationError("Неизвестный статус проекта.")
    if kind == "tcReview" and payload.get("value") not in TC_REVIEW_VALUES:
        raise ProductionValidationError("Неизвестная отметка проверки.")
    if kind == "tcReview" and payload.get("value") == "с погрешностями" and not str(payload.get("text") or "").strip():
        raise ProductionValidationError("Напиши, какие погрешности.")
    if kind == "mark":
        plan_mark_value(str(payload.get("field") or ""), payload.get("value"), payload.get("text"))
