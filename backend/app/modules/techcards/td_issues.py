"""Выпуски комплекта ТД по ЕСТД (ГОСТ РВ): статус, обозначения по ГОСТ 3.1201, нормоконтроль (migration 0129).

Выпуск замораживает карту ЭТК проекта — JSON ровно как его отдаёт GET /api/techcards/projects/{id} в момент выпуска
(`snapshot`) и её ревизию (`etk_revision`). Более новая ревизия карты — `has_changes`: нужен новый выпуск по извещению.

Обозначение документа: `{org}.{вид документа}{вид организации}{метод}.{порядковый:05d}`, вид организации «1»
(единичный ТП): МК «10», КЭ «20», ОК «60», ВМ «43», ВО «42», ВТД «40» — с методом выпуска (`method_code`); ОК на ТК
контроля — «60» + «1» + «02». Пример: ФРАК.60185.00001. Порядковый номер один на карту: первый выпуск карты берёт
следующий свободный (max + 1 по таблице), следующие выпуски той же карты его повторяют.

Статусы: draft → signing (без блокирующих замечаний нормоконтроля, иначе 422 {findings}), signing → draft,
signing → active (approved_at; прежний действующий выпуск карты аннулируется в той же транзакции), active → annulled,
draft → annulled; остальное — 409. Правка и пересъёмка снимка — только в draft. `version` сверяется при каждой записи.

Выпуск хранит цех и участок (0130: МК «Цех», «Уч.», ТЛ, сопроводительный лист); без них подпись блокируется
(`place_missing`). Снимок несёт `td_ops` — профессию и разряд операций (td_grades.py): разряд по умолчанию —
предупреждение `grade_default` на выпуск, без разряда — `grade_missing` на операцию. Комплектующее с аналогом вместо
артикула по КД (`kd` задан — etk_parts.use_article) — предупреждение `analog_used`: для РВ нужно разрешение на замену.
Автоматический выпуск при выдаче ЭТК и её новой ревизии — app/workflows/td_auto.py.

Ключ операции в замечаниях и в `sto_sections` печати — «код::connector::side», как `project_cards.operation_key` и
`operationKeyOf` печати ЭТК (frontend/src/etkPrint.ts); у своей операции без кода ключ «::<connector>::<side>».
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from .td_grades import BY_DEFAULT, operation_grade
from .td_sto import outside_sto, sto_section

STATUS_LABELS = {"draft": "проект", "signing": "на подписи", "active": "действует", "annulled": "аннулирован"}
LITERAS = ("", "П", "О", "О1", "О2", "О3", "О4", "О5", "А", "Б", "И")
METHOD_CODES = ("85", "88", "80", "81", "02", "03")
TRANSITIONS = frozenset({
    ("draft", "signing"), ("signing", "draft"), ("signing", "active"), ("active", "annulled"), ("draft", "annulled"),
})
ROLES = ("developer", "checker", "normcontrol", "approver", "agreed_vp")
ROLE_LABELS = {
    "developer": "Разраб.", "checker": "Пров.", "normcontrol": "Н.контр.", "approver": "Утв.",
    "agreed_vp": "Согласовано ВП",
}
ORG_CODE = "ФРАК"
ORG_KIND = "1"  # единичный технологический процесс (ГОСТ 3.1201)
DOCUMENT_CODES = {"mk": "10", "ke": "20", "ok": "60", "vm": "43", "vo": "42", "vtd": "40"}
CONTROL_METHOD = "02"
MAX_NOTICE = 64
MAX_TEXT = 2000
MAX_NAME = 255
SITES = ("ЦАР", "ЖУК")
DEFAULT_WORKSHOP = "Цех электромонтажа"
SYSTEM_ACTOR = "система"

# Внутренний код латиницей с подчёркиванием (cutWireStrip не ловится — у него нет «_»; INS_гофра — кириллица).
_LATIN_CODE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b")
_CODE_PREFIX = re.compile(r"^\s*[A-Za-z][A-Za-z0-9_+./-]*\s*\|\s*")


class TdIssueError(Exception):
    status_code = 422

    def __init__(self, detail: Any):
        super().__init__(detail if isinstance(detail, str) else "td issue error")
        self.detail = detail


class TdIssueInvalid(TdIssueError):
    status_code = 422


class TdIssueConflict(TdIssueError):
    status_code = 409


class TdIssueNotFound(TdIssueError):
    status_code = 404


class TdIssueBlocked(TdIssueError):
    """Нормоконтроль нашёл блокирующие замечания: на подпись нельзя."""

    status_code = 422

    def __init__(self, findings: list[dict[str, Any]]):
        super().__init__({"message": "Нормоконтроль не пройден", "findings": findings})


def _text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _same_person(left: str, right: str) -> bool:
    return bool(left) and left.casefold().replace("ё", "е") == right.casefold().replace("ё", "е")


@dataclass(frozen=True)
class TdIssueInput:
    litera: str
    method_code: str
    signatories: dict[str, dict[str, str]]
    notice_no: str
    reason: str
    workshop: str = ""
    site: str = ""


def parse_input(raw: dict[str, Any]) -> TdIssueInput:
    """Вход выпуска: литера и метод из списков ГОСТ, подписанты всех пяти граф (пустое имя — ещё не вписан),
    Н.контр. не совпадает с Разраб. и Пров."""
    litera = _text(raw.get("litera"))
    if litera not in LITERAS:
        raise TdIssueInvalid(f"Литера «{litera}» не из ГОСТ Р 3.102: допустимы {', '.join(item or '(без литеры)' for item in LITERAS)}")
    method = _text(raw.get("method_code"))
    if method not in METHOD_CODES:
        raise TdIssueInvalid(f"Вид ТП «{method}» не из ГОСТ 3.1201: допустимы {', '.join(METHOD_CODES)}")
    source = raw.get("signatories") if isinstance(raw.get("signatories"), dict) else {}
    signatories: dict[str, dict[str, str]] = {}
    for role in ROLES:
        item = source.get(role) if isinstance(source.get(role), dict) else {}
        name, position = _text(item.get("name")), _text(item.get("position"))
        if len(name) > MAX_NAME or len(position) > MAX_NAME:
            raise TdIssueInvalid(f"{ROLE_LABELS[role]}: не длиннее {MAX_NAME} знаков")
        signatories[role] = {"name": name, "position": position}
    for role in ("developer", "checker"):
        if _same_person(signatories["normcontrol"]["name"], signatories[role]["name"]):
            raise TdIssueInvalid(f"Н.контр. не может быть тем же человеком, что {ROLE_LABELS[role]}")
    notice, reason = _text(raw.get("notice_no")), str(raw.get("reason") or "").strip()
    if len(notice) > MAX_NOTICE:
        raise TdIssueInvalid(f"Номер извещения — не длиннее {MAX_NOTICE} знаков")
    if len(reason) > MAX_TEXT:
        raise TdIssueInvalid(f"Причина изменения — не длиннее {MAX_TEXT} знаков")
    workshop, site = _text(raw.get("workshop")), _text(raw.get("site"))
    if len(workshop) > MAX_NAME:
        raise TdIssueInvalid(f"Цех — не длиннее {MAX_NAME} знаков")
    if site and site not in SITES:
        raise TdIssueInvalid(f"Участок «{site}»: допустимы {', '.join(SITES)}")
    return TdIssueInput(
        litera=litera, method_code=method, signatories=signatories, notice_no=notice, reason=reason,
        workshop=workshop, site=site,
    )


def _require_notice(change_no: int, value: TdIssueInput) -> None:
    if change_no > 0 and not (value.notice_no and value.reason):
        raise TdIssueInvalid(f"Изменение {change_no}: укажите номер извещения и причину изменения")


def designations(org_code: str, method_code: str, sequence_no: int) -> dict[str, str]:
    number = f"{sequence_no:05d}"
    found = {key: f"{org_code}.{code}{ORG_KIND}{method_code}.{number}" for key, code in DOCUMENT_CODES.items()}
    found["ok_tk"] = f"{org_code}.{DOCUMENT_CODES['ok']}{ORG_KIND}{CONTROL_METHOD}.{number}"
    return {key: found[key] for key in ("vtd", "mk", "ke", "ok", "ok_tk", "vm", "vo")}


@dataclass(frozen=True)
class TdIssue:
    id: UUID
    version: int
    project_card_id: UUID
    change_no: int
    sequence_no: int
    org_code: str
    method_code: str
    litera: str
    status: str
    etk_revision: int
    snapshot: dict[str, Any]
    signatories: dict[str, dict[str, str]]
    notice_no: str
    reason: str
    created_at: datetime
    created_by: str
    approved_at: datetime | None = None
    annulled_at: datetime | None = None
    workshop: str = ""
    site: str = ""

    @classmethod
    def open(
        cls, *, card_id: UUID, value: TdIssueInput, snapshot: dict[str, Any], existing: list["TdIssue"],
        next_sequence: int, actor: str, now: datetime,
    ) -> "TdIssue":
        """Новый выпуск карты (draft): номер изменения — следующий после последнего выпуска карты, порядковый номер —
        карты, а у первого выпуска — следующий свободный. Пока у карты есть выпуск в работе, второй не открывается."""
        if not isinstance(snapshot.get("document"), dict):
            raise TdIssueConflict("У карты нет электронной техкарты: выпускать нечего")
        open_issue = next((issue for issue in existing if issue.status in {"draft", "signing"}), None)
        if open_issue is not None:
            raise TdIssueConflict(
                f"У карты уже есть выпуск в работе (изменение {open_issue.change_no}, «{STATUS_LABELS[open_issue.status]}»)",
            )
        change_no = max((issue.change_no for issue in existing), default=-1) + 1
        _require_notice(change_no, value)
        sequence = existing[0].sequence_no if existing else next_sequence
        return cls(
            id=uuid4(), version=1, project_card_id=card_id, change_no=change_no, sequence_no=sequence,
            org_code=ORG_CODE, method_code=value.method_code, litera=value.litera, status="draft",
            etk_revision=int(snapshot.get("revision") or 0), snapshot=snapshot, signatories=value.signatories,
            notice_no=value.notice_no, reason=value.reason, created_at=now, created_by=actor,
            workshop=value.workshop, site=value.site,
        )

    @property
    def designations(self) -> dict[str, str]:
        return designations(self.org_code, self.method_code, self.sequence_no)

    def check_version(self, expected: int) -> None:
        if expected != self.version:
            raise TdIssueConflict("Выпуск изменён другим пользователем: обновите и повторите")

    def _draft_only(self, action: str) -> None:
        if self.status != "draft":
            raise TdIssueConflict(f"{action}: выпуск «{STATUS_LABELS[self.status]}», менять можно только проект")

    def edited(self, value: TdIssueInput) -> "TdIssue":
        self._draft_only("Правка выпуска")
        _require_notice(self.change_no, value)
        return replace(
            self, version=self.version + 1, method_code=value.method_code, litera=value.litera,
            signatories=value.signatories, notice_no=value.notice_no, reason=value.reason,
            workshop=value.workshop, site=value.site,
        )

    def resnapshot(self, snapshot: dict[str, Any]) -> "TdIssue":
        self._draft_only("Обновить снимок ЭТК")
        if not isinstance(snapshot.get("document"), dict):
            raise TdIssueConflict("У карты нет электронной техкарты: выпускать нечего")
        return replace(
            self, version=self.version + 1, snapshot=snapshot, etk_revision=int(snapshot.get("revision") or 0),
        )

    def moved(self, to: str, now: datetime) -> "TdIssue":
        if to not in STATUS_LABELS:
            raise TdIssueInvalid(f"Неизвестный статус выпуска «{to}»")
        if (self.status, to) not in TRANSITIONS:
            raise TdIssueConflict(f"Выпуск «{STATUS_LABELS[self.status]}» нельзя перевести в «{STATUS_LABELS[to]}»")
        if to == "signing":
            blocking = [item for item in normcontrol(self) if item["severity"] == "blocking"]
            if blocking:
                raise TdIssueBlocked(blocking)
        return replace(
            self, version=self.version + 1, status=to,
            approved_at=now if to == "active" else self.approved_at,
            annulled_at=now if to == "annulled" else self.annulled_at,
        )

    def view(self, current_etk_revision: int) -> dict[str, Any]:
        """TdIssue frontend/src/tdTypes.ts без снимка."""
        return {
            "id": str(self.id), "version": self.version, "project_card_id": str(self.project_card_id),
            "change_no": self.change_no, "status": self.status, "status_label": STATUS_LABELS[self.status],
            "etk_revision": self.etk_revision, "current_etk_revision": current_etk_revision,
            "has_changes": current_etk_revision > self.etk_revision, "sequence_no": self.sequence_no,
            "org_code": self.org_code, "method_code": self.method_code, "litera": self.litera,
            "designations": self.designations, "signatories": self.signatories,
            "notice_no": self.notice_no, "reason": self.reason, "workshop": self.workshop, "site": self.site,
            "created_at": self.created_at.isoformat(), "created_by": self.created_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "annulled_at": self.annulled_at.isoformat() if self.annulled_at else None,
        }


# ── нормоконтроль ────────────────────────────────────────────────────────────


def operation_key_of(operation: dict[str, Any]) -> str:
    return "::".join(_text(operation.get(field)) for field in ("code", "connector", "side"))


def snapshot_operations(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    document = snapshot.get("document") if isinstance(snapshot.get("document"), dict) else {}
    return [item for item in document.get("operations") or [] if isinstance(item, dict)]


def _finding(code: str, severity: str, key: str | None, text: str) -> dict[str, Any]:
    return {"code": code, "severity": severity, "operation_key": key, "text": text}


def _without_code_prefix(value: object) -> str:
    """Название шаблона «CUT_WIRE_auto | Резка» печатается без кода (etkOperationName на фронте) — префикс не замечание."""
    return _CODE_PREFIX.sub("", _text(value))


def _printed_texts(operation: dict[str, Any]) -> list[str]:
    card = operation.get("card") if isinstance(operation.get("card"), dict) else {}
    texts = [_without_code_prefix(operation.get("title")), _without_code_prefix(operation.get("name")),
             _without_code_prefix(card.get("name"))]
    for step in card.get("steps") or []:
        if isinstance(step, dict):
            texts += [step.get("text"), step.get("tolerance")]
    for field in ("result", "components"):
        texts += [row.get("name") for row in operation.get(field) or [] if isinstance(row, dict)]
    return [str(text) for text in texts if text]


def _seconds(value: object) -> float:
    try:
        return float(str(value or "0").strip().replace(",", "."))
    except ValueError:
        return 0.0


def normcontrol(issue: TdIssue) -> list[dict[str, Any]]:
    """Замечания нормоконтроля по снимку и подписантам: blocking — на подпись нельзя, warning — проверить."""
    found: list[dict[str, Any]] = []
    names = {role: _text((issue.signatories.get(role) or {}).get("name")) for role in ROLES}
    for role in ("developer", "checker", "normcontrol", "approver"):
        if not names[role]:
            found.append(_finding("signatory_missing", "blocking", None, f"Не указан подписант «{ROLE_LABELS[role]}»"))
    for role in ("developer", "checker"):
        if _same_person(names["normcontrol"], names[role]):
            found.append(_finding(
                "normcontrol_same_person", "blocking", None,
                f"Н.контр. и {ROLE_LABELS[role]} — один человек ({names[role]}): нормоконтроль ведёт другой",
            ))
    if not names["agreed_vp"]:
        found.append(_finding("agreed_vp_missing", "warning", None, "Не указан представитель ВП в «Согласовано»"))
    missing_place = [title for title, value in (("цех", issue.workshop), ("участок", issue.site)) if not _text(value)]
    if missing_place:
        found.append(_finding(
            "place_missing", "blocking", None, f"Не указан {' и '.join(missing_place)} выпуска",
        ))
    grades = td_ops_of(issue.snapshot)
    defaulted = 0
    for position, operation in enumerate(snapshot_operations(issue.snapshot), start=1):
        key = operation_key_of(operation)
        label = f"Операция {operation.get('position') or position}"
        title = _without_code_prefix(operation.get("title") or operation.get("name") or (operation.get("card") or {}).get("name"))
        if title:
            label += f" «{title}»"
        card = operation.get("card") if isinstance(operation.get("card"), dict) else {}
        steps = [step for step in card.get("steps") or [] if isinstance(step, dict)]
        for step in steps:
            if _text(step.get("kind")) == "Контроль" and "не задан" in str(step.get("tolerance") or "").casefold():
                found.append(_finding(
                    "tolerance_missing", "blocking", key,
                    f"{label}: в шаге контроля {step.get('number') or ''} допуск не задан".replace("  ", " "),
                ))
        codes = sorted({match for text in _printed_texts(operation) for match in _LATIN_CODE.findall(text)})
        if codes:
            found.append(_finding(
                "internal_code", "blocking", key,
                f"{label}: в тексте документа внутренний код {', '.join(codes)} — заменить русским наименованием",
            ))
        if not any(_text(step.get("kind")) == "Контроль" for step in steps):
            found.append(_finding("no_control_step", "warning", key, f"{label}: нет шага «Контроль»"))
        times = operation.get("times") if isinstance(operation.get("times"), dict) else {}
        if _seconds(times.get("operation_seconds")) <= 0:
            found.append(_finding("no_time_norm", "warning", key, f"{label}: не задана норма времени"))
        seen: set[tuple[str, str]] = set()
        for row in operation.get("components") or []:
            if not isinstance(row, dict):
                continue
            kd, article = _text(row.get("kd")), _text(row.get("art"))
            if kd and article and kd != article and (kd, article) not in seen:
                seen.add((kd, article))
                found.append(_finding(
                    "analog_used", "warning", key,
                    f"{label}: вместо {kd} по КД применён аналог {article} — для РВ нужно разрешение на замену, "
                    "согласованное с разработчиком КД и ВП",
                ))
        grade = grades.get(key) or {}
        if not _text(grade.get("grade")):
            found.append(_finding("grade_missing", "warning", key, f"{label}: не задан разряд исполнителя"))
        elif grade.get("source") == BY_DEFAULT:
            defaulted += 1
        code = _text(operation.get("code"))
        if sto_section(code) is None and not outside_sto(code):
            found.append(_finding(
                "no_sto_section", "warning", key,
                f"{label}: для операции {code or 'без кода'} нет раздела СТО типовых дефектов",
            ))
    if defaulted:
        found.append(_finding(
            "grade_default", "warning", None,
            f"Разряд и профессия по умолчанию (ЕТКС) у {defaulted} операций — задайте в БД.ОП, если иначе",
        ))
    return found


def with_td_ops(snapshot: dict[str, Any], reference: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Снимок с профессией и разрядом каждой операции (`td_ops`): БД.ОП по коду, иначе по умолчанию (td_grades)."""
    td_ops = {operation_key_of(item): operation_grade(_text(item.get("code")), reference)
              for item in snapshot_operations(snapshot)}
    return {**snapshot, "td_ops": td_ops}


def td_ops_of(snapshot: dict[str, Any]) -> dict[str, dict[str, str]]:
    """`td_ops` снимка; снимок до 0130 (без них) — по умолчанию."""
    stored = snapshot.get("td_ops")
    if isinstance(stored, dict):
        return stored
    return with_td_ops(snapshot, {})["td_ops"]


def print_card(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Снимок для печати: как заморожен; у снимка до 0130 `td_ops` достраиваются по умолчанию."""
    return snapshot if isinstance(snapshot.get("td_ops"), dict) else with_td_ops(snapshot, {})


def sto_sections(snapshot: dict[str, Any]) -> dict[str, dict[str, str] | None]:
    return {operation_key_of(item): sto_section(_text(item.get("code"))) for item in snapshot_operations(snapshot)}


def change_rows(issues: list[TdIssue]) -> list[dict[str, Any]]:
    """Лист регистрации изменений: выпуски карты, ушедшие из проекта, по номеру изменения. Проект, аннулированный
    без утверждения, в силу не вступал и в лист не попадает."""
    rows = []
    for issue in sorted(issues, key=lambda item: item.change_no):
        if issue.status == "draft" or (issue.status == "annulled" and issue.approved_at is None):
            continue
        rows.append({
            "change_no": issue.change_no, "notice_no": issue.notice_no, "reason": issue.reason,
            "designation": issue.designations["vtd"],
            "approved_at": issue.approved_at.isoformat() if issue.approved_at else None,
            "developer": (issue.signatories.get("developer") or {}).get("name", ""),
        })
    return rows


# ── сценарии ─────────────────────────────────────────────────────────────────


class TdIssueRepository(Protocol):
    def get(self, issue_id: UUID) -> TdIssue | None: ...
    def for_card(self, card_id: UUID) -> list[TdIssue]: ...
    def active_for_card(self, card_id: UUID) -> TdIssue | None: ...
    def next_sequence(self) -> int: ...
    def add(self, issue: TdIssue) -> None: ...
    def save(self, issue: TdIssue, expected_version: int) -> None: ...
    def flush(self) -> None: ...


def create_issue(uow, card_id: UUID, value: TdIssueInput, snapshot: dict[str, Any], actor: str, now: datetime) -> TdIssue:
    with uow:
        if uow.techcards.get(card_id) is None:
            raise TdIssueNotFound("Карточка проекта не найдена")
        issue = TdIssue.open(
            card_id=card_id, value=value, snapshot=snapshot, existing=uow.td_issues.for_card(card_id),
            next_sequence=uow.td_issues.next_sequence(), actor=actor, now=now,
        )
        uow.td_issues.add(issue)
        uow.commit()
    return issue


def _issue_or_404(uow, issue_id: UUID) -> TdIssue:
    issue = uow.td_issues.get(issue_id)
    if issue is None:
        raise TdIssueNotFound("Выпуск ТД не найден")
    return issue


def edit_issue(uow, issue_id: UUID, expected_version: int, value: TdIssueInput) -> TdIssue:
    with uow:
        issue = _issue_or_404(uow, issue_id)
        issue.check_version(expected_version)
        edited = issue.edited(value)
        uow.td_issues.save(edited, issue.version)
        uow.commit()
    return edited


def resnapshot_issue(uow, issue_id: UUID, expected_version: int, snapshot_of) -> TdIssue:
    """`snapshot_of(card_id)` — карта ЭТК как её отдаёт GET карты сейчас."""
    with uow:
        issue = _issue_or_404(uow, issue_id)
    issue.check_version(expected_version)
    issue._draft_only("Обновить снимок ЭТК")
    snapshot = snapshot_of(issue.project_card_id)
    with uow:
        current = _issue_or_404(uow, issue_id)
        current.check_version(expected_version)
        updated = current.resnapshot(snapshot)
        uow.td_issues.save(updated, current.version)
        uow.commit()
    return updated


def move_issue(uow, issue_id: UUID, expected_version: int, to: str, now: datetime) -> TdIssue:
    """Перевод статуса; утверждение аннулирует прежний действующий выпуск карты в той же транзакции."""
    with uow:
        issue = _issue_or_404(uow, issue_id)
        issue.check_version(expected_version)
        moved = issue.moved(to, now)
        if to == "active":
            previous = uow.td_issues.active_for_card(issue.project_card_id)
            if previous is not None and previous.id != issue.id:
                uow.td_issues.save(previous.moved("annulled", now), previous.version)
                uow.td_issues.flush()  # прежний снят до утверждения нового: частичный UNIQUE «один действующий»
        uow.td_issues.save(moved, issue.version)
        uow.commit()
    return moved
