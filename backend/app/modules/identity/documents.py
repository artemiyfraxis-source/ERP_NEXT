"""Документы с ознакомлением — «Личная карточка», фаза 3 (native, no SUPPLYMAP source).

«Кадры» (section `hr`, edit) upload a PDF — «Должностная инструкция», «Положение» or «Прочее» — aimed at everyone, at a
department or at a job title. Every employee in scope signs «Ознакомлен» with their PIN or password typed again (the
same signer check and brute-force counters as a leave request, identity/leave.py). Rules:
- who must read is computed live from the scope among active employees (all · department, exact after
  normalize_department · job title, case-insensitive), so a new hire sees every document in force;
- only the latest version of a document that is not archived is asked; a new version needs a fresh signature — the old
  one stays in the sheet of its own version and no longer counts;
- one signature per person and version (UNIQUE); it keeps who, when, how and the SHA-256 of the PDF it covers;
- a version somebody signed is never deleted (FK RESTRICT; the app refuses first) — the document is archived instead;
- every new version notifies everyone in scope (kind=document); «Кадры» see who signed and who has not per version.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Protocol
from uuid import UUID, uuid4

from app.shared.clock import as_utc
from app.shared.events import AuditEvent
from app.shared.file_types import sniffed_type
from app.shared.identity import Actor
from app.shared.notifications import notification_requested, publish_notification

from .application import UnitOfWork
from .domain import IdentityUser, IdentityValidationError, normalize_department
from .leave import METHOD_LABELS, Staff, _signer, _verify_signer, staff_of

MAX_PDF_BYTES = 20 * 1024 * 1024
TITLE_LIMIT = 255
FILE_NAME_LIMIT = 255
SCOPE_VALUE_LIMIT = 255


class DocumentKind(str, Enum):
    JOB_DESCRIPTION = "job_description"
    REGULATION = "regulation"
    OTHER = "other"


KIND_LABELS: dict[DocumentKind, str] = {
    DocumentKind.JOB_DESCRIPTION: "Должностная инструкция",
    DocumentKind.REGULATION: "Положение",
    DocumentKind.OTHER: "Прочее",
}


class DocumentScope(str, Enum):
    ALL = "all"
    DEPARTMENT = "department"
    JOB_TITLE = "job_title"


SCOPE_LABELS: dict[DocumentScope, str] = {
    DocumentScope.ALL: "Все сотрудники",
    DocumentScope.DEPARTMENT: "Отдел",
    DocumentScope.JOB_TITLE: "Должность",
}


class DocumentError(ValueError):
    """A rule of the document is broken (422)."""


class DocumentForbidden(PermissionError):
    """The person may not do this (403)."""


class DocumentConflict(RuntimeError):
    """Already done, or the document changed meanwhile (409)."""


class DocumentNotFound(LookupError):
    pass


@dataclass(frozen=True)
class DocumentVersion:
    id: UUID
    document_id: UUID
    version_no: int
    file_name: str
    size: int
    sha256: str
    uploaded_by: UUID
    uploaded_at: datetime


@dataclass(frozen=True)
class Document:
    id: UUID
    title: str
    kind: DocumentKind
    scope: DocumentScope
    scope_value: str
    archived: bool
    created_by: UUID
    created_at: datetime
    versions: tuple[DocumentVersion, ...] = ()  # by version_no

    @property
    def current(self) -> DocumentVersion | None:
        return self.versions[-1] if self.versions else None


@dataclass(frozen=True)
class Acknowledgement:
    id: UUID
    version_id: UUID
    user_id: UUID
    signed_at: datetime
    method: str
    sha256: str


class DocumentRepository(Protocol):
    def all(self) -> list[Document]: ...
    def get(self, document_id: UUID, *, for_update: bool = False) -> Document | None: ...
    def add(self, document: Document) -> None: ...
    def set_archived(self, document_id: UUID, archived: bool) -> None: ...
    def add_version(self, version: DocumentVersion, content: bytes) -> None: ...
    def version(self, version_id: UUID) -> DocumentVersion | None: ...
    def content(self, version_id: UUID) -> bytes | None: ...
    def delete_version(self, version_id: UUID) -> None: ...
    def acks_of_versions(self, version_ids: Iterable[UUID]) -> list[Acknowledgement]: ...
    def acks_of_user(self, user_id: UUID) -> list[Acknowledgement]: ...
    def add_ack(self, ack: Acknowledgement) -> None: ...


# ── Pure rules ───────────────────────────────────────────────────────────────────────────────────────


def normalize_title(value: str | None) -> str:
    title = str(value or "").strip()
    if not title:
        raise DocumentError("Укажите название документа.")
    if len(title) > TITLE_LIMIT:
        raise DocumentError(f"Название длиннее {TITLE_LIMIT} символов.")
    return title


def normalize_kind(value: str | None) -> DocumentKind:
    try:
        return DocumentKind(str(value or "").strip())
    except ValueError as error:
        raise DocumentError("Вид документа — должностная инструкция, положение или прочее.") from error


def normalize_scope(scope: str | None, value: str | None) -> tuple[DocumentScope, str]:
    """The audience and its value: everyone takes no value; a department or a job title must be named."""
    try:
        kind = DocumentScope(str(scope or "").strip())
    except ValueError as error:
        raise DocumentError("Кому адресован документ — всем, отделу или должности.") from error
    if kind is DocumentScope.ALL:
        return kind, ""
    try:
        text = normalize_department(value or "") if kind is DocumentScope.DEPARTMENT else str(value or "").strip()
    except IdentityValidationError as error:
        raise DocumentError(str(error)) from error
    if not text:
        raise DocumentError("Укажите отдел." if kind is DocumentScope.DEPARTMENT else "Укажите должность.")
    if len(text) > SCOPE_VALUE_LIMIT:
        raise DocumentError(f"Отдел или должность длиннее {SCOPE_VALUE_LIMIT} символов.")
    return kind, text


def check_pdf(content: bytes) -> str:
    """The PDF itself: not empty, at most 20 МБ, a real PDF by its bytes (never by the declared type). Its SHA-256."""
    if not content:
        raise DocumentError("Файл пустой — загрузите PDF.")
    if len(content) > MAX_PDF_BYTES:
        raise DocumentError(
            f"Файл {len(content) / 1024 / 1024:.1f} МБ — больше {MAX_PDF_BYTES // 1024 // 1024} МБ. "
            "Сожмите PDF или разделите его."
        )
    if sniffed_type(content) != "application/pdf":
        raise DocumentError("Это не PDF — загрузите документ в формате PDF.")
    return hashlib.sha256(content).hexdigest()


def normalize_file_name(value: str | None, title: str) -> str:
    """The name the PDF opens under: the uploaded name without folders, or the title; always `.pdf`."""
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip() or title
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return name[-FILE_NAME_LIMIT:]


def in_scope(document: Document, user: IdentityUser) -> bool:
    """Whether the employee must read the document — live, from the scope (a new hire or a transfer counts at once)."""
    if not user.active:
        return False
    if document.scope is DocumentScope.ALL:
        return True
    if document.scope is DocumentScope.DEPARTMENT:
        return normalize_department(user.department or "") == document.scope_value
    return str(user.job_title or "").strip().casefold() == document.scope_value.casefold()


def readers(document: Document, users: Iterable[IdentityUser]) -> list[IdentityUser]:
    return sorted((user for user in users if in_scope(document, user)), key=lambda user: (user.name.casefold(), str(user.id)))


def asked(document: Document, user: IdentityUser) -> bool:
    """The document waits for this person's signature on its current version (the signature itself not checked)."""
    return not document.archived and document.current is not None and in_scope(document, user)


# ── Use cases ────────────────────────────────────────────────────────────────────────────────────────


def _audit(uow: UnitOfWork, document: Document, event: str, actor: Actor, now: datetime, **details: str) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=document.id, event_type=f"identity.document_{event}", actor=actor, occurred_at=now,
        details={"title": document.title, "kind": document.kind.value, "scope": document.scope.value,
                 "scope_value": document.scope_value, **details},
    ))


def _require_hr(staff: Staff, actor: Actor) -> None:
    if actor.id not in staff.hr_ids:
        raise DocumentForbidden("Документы загружает сотрудник с доступом «Кадры».")


def _notify_version(uow: UnitOfWork, document: Document, version: DocumentVersion, staff: Staff, now: datetime) -> None:
    """Everyone in scope but the uploader hears of the new version: one message per version."""
    recipients = [user.id for user in readers(document, staff.users.values()) if user.id != version.uploaded_by]
    first = version.version_no == 1
    publish_notification(uow.outbox, notification_requested(
        key=f"document:{version.id}", now=now,
        title="Новый документ для ознакомления" if first else "Новая редакция документа",
        body=(
            f"{KIND_LABELS[document.kind]} «{document.title}»"
            + ("" if first else f", редакция {version.version_no}")
            + ". Прочитайте и подпишите «Ознакомлен» в «Моей карточке»."
        ),
        kind="document", reference=document.title, user_ids=recipients,
    ))


def _version(document: Document, version_no: int, file_name: str, content: bytes, sha256: str, actor: Actor,
             now: datetime) -> DocumentVersion:
    return DocumentVersion(
        id=uuid4(), document_id=document.id, version_no=version_no, file_name=file_name, size=len(content),
        sha256=sha256, uploaded_by=actor.id, uploaded_at=now,
    )


def create_document(
    actor: Actor, title: str, kind: str, scope: str, scope_value: str | None, file_name: str | None, content: bytes,
    now: datetime, uow: UnitOfWork,
) -> Document:
    """«Кадры» upload a new document with its first version; everyone in scope is told."""
    stamp = as_utc(now)
    text, document_kind = normalize_title(title), normalize_kind(kind)
    document_scope, value = normalize_scope(scope, scope_value)
    sha256 = check_pdf(content)
    with uow:
        staff = staff_of(uow)
        _require_hr(staff, actor)
        document = Document(
            id=uuid4(), title=text, kind=document_kind, scope=document_scope, scope_value=value, archived=False,
            created_by=actor.id, created_at=stamp,
        )
        if not readers(document, staff.users.values()):
            raise DocumentError(
                f"Нет действующих сотрудников: {SCOPE_LABELS[document_scope].lower()} «{value}». Проверьте написание."
            )
        version = _version(document, 1, normalize_file_name(file_name, text), content, sha256, actor, stamp)
        uow.documents.add(document)
        uow.documents.add_version(version, content)
        document = replace(document, versions=(version,))
        _audit(uow, document, "created", actor, stamp, version_no="1", sha256=sha256)
        _notify_version(uow, document, version, staff, stamp)
        uow.commit()
        return document


def upload_version(
    document_id: UUID, actor: Actor, file_name: str | None, content: bytes, now: datetime, uow: UnitOfWork,
) -> Document:
    """A new version of a document: the signatures of the old one no longer count, everyone in scope signs again."""
    stamp = as_utc(now)
    sha256 = check_pdf(content)
    with uow:
        staff = staff_of(uow)
        _require_hr(staff, actor)
        document = uow.documents.get(document_id, for_update=True)
        if document is None:
            raise DocumentNotFound("Документ не найден.")
        if document.archived:
            raise DocumentConflict("Документ в архиве — верните его из архива, затем загружайте новую редакцию.")
        current = document.current
        if current is not None and current.sha256 == sha256:
            raise DocumentConflict("Этот же файл уже загружен последней редакцией.")
        version_no = (current.version_no if current is not None else 0) + 1
        version = _version(document, version_no, normalize_file_name(file_name, document.title), content, sha256,
                           actor, stamp)
        uow.documents.add_version(version, content)
        document = replace(document, versions=(*document.versions, version))
        _audit(uow, document, "version_uploaded", actor, stamp, version_no=str(version_no), sha256=sha256)
        _notify_version(uow, document, version, staff, stamp)
        uow.commit()
        return document


def set_archived(document_id: UUID, actor: Actor, archived: bool, now: datetime, uow: UnitOfWork) -> Document:
    """An archived document is asked of nobody; its versions and signatures stay. Restoring asks the current version
    again of whoever has not signed it."""
    stamp = as_utc(now)
    with uow:
        _require_hr(staff_of(uow), actor)
        document = uow.documents.get(document_id, for_update=True)
        if document is None:
            raise DocumentNotFound("Документ не найден.")
        if document.archived == archived:
            raise DocumentConflict("Документ уже в архиве." if archived else "Документ уже действует.")
        uow.documents.set_archived(document.id, archived)
        document = replace(document, archived=archived)
        _audit(uow, document, "archived" if archived else "restored", actor, stamp)
        uow.commit()
        return document


def delete_version(version_id: UUID, actor: Actor, now: datetime, uow: UnitOfWork) -> Document | None:
    """A version uploaded by mistake: only the latest one and only while nobody has signed it. The only version goes
    with its document. Returns the document left, or None."""
    stamp = as_utc(now)
    with uow:
        _require_hr(staff_of(uow), actor)
        version = uow.documents.version(version_id)
        if version is None:
            raise DocumentNotFound("Редакция не найдена.")
        document = uow.documents.get(version.document_id, for_update=True)
        if document is None:
            raise DocumentNotFound("Документ не найден.")
        if document.current is None or document.current.id != version.id:
            raise DocumentConflict("Удалить можно только последнюю редакцию.")
        if uow.documents.acks_of_versions((version.id,)):
            raise DocumentConflict("Редакцию уже подписали — удалить её нельзя, документ можно отправить в архив.")
        uow.documents.delete_version(version.id)
        left = replace(document, versions=document.versions[:-1])
        _audit(uow, left, "version_deleted", actor, stamp, version_no=str(version.version_no), sha256=version.sha256)
        uow.commit()
        return left if left.versions else None


def acknowledge(
    version_id: UUID, actor: Actor, secret: str, now: datetime, uow: UnitOfWork, *, client_key: str,
) -> Acknowledgement:
    """«Ознакомлен»: the employee in scope signs the current version of a document in force, with the PIN or password
    typed again. A second signature of the same version is refused."""
    stamp = as_utc(now)
    with uow:
        version = uow.documents.version(version_id)
        if version is None:
            raise DocumentNotFound("Редакция не найдена.")
        document = uow.documents.get(version.document_id)
        if document is None:
            raise DocumentNotFound("Документ не найден.")
        if document.archived:
            raise DocumentConflict("Документ в архиве — подпись не нужна.")
        if document.current is None or document.current.id != version.id:
            raise DocumentConflict("Вышла новая редакция — обновите страницу и ознакомьтесь с ней.")
        signer = _signer(uow, actor)
        if not in_scope(document, signer):
            raise DocumentForbidden("Этот документ адресован не вам.")
        if any(ack.user_id == signer.id for ack in uow.documents.acks_of_versions((version.id,))):
            raise DocumentConflict("Вы уже ознакомились с этой редакцией.")
        method = _verify_signer(uow, signer, secret, stamp, client_key)
        ack = Acknowledgement(
            id=uuid4(), version_id=version.id, user_id=signer.id, signed_at=stamp, method=method, sha256=version.sha256,
        )
        uow.documents.add_ack(ack)
        _audit(uow, document, "acknowledged", actor, stamp, version_no=str(version.version_no), method=method,
               sha256=version.sha256)
        uow.commit()
        return ack


# ── Reading ──────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DocumentsOverview:
    own: list[dict]
    journal: list[dict] | None  # «Кадры»: every document with its signature count; None — not theirs to see
    hr: bool


def _version_view(version: DocumentVersion, staff: Staff) -> dict:
    return {
        "id": str(version.id),
        "version_no": version.version_no,
        "file_name": version.file_name,
        "size": version.size,
        "sha256": version.sha256,
        "uploaded_by_name": staff.name(version.uploaded_by),
        "uploaded_at": version.uploaded_at.isoformat(),
    }


def _document_view(document: Document, staff: Staff) -> dict:
    current = document.current
    return {
        "id": str(document.id),
        "title": document.title,
        "kind": document.kind.value,
        "kind_label": KIND_LABELS[document.kind],
        "scope": document.scope.value,
        "scope_label": SCOPE_LABELS[document.scope],
        "scope_value": document.scope_value,
        "audience": SCOPE_LABELS[document.scope] if document.scope is DocumentScope.ALL
        else f"{SCOPE_LABELS[document.scope]}: {document.scope_value}",
        "archived": document.archived,
        "created_by_name": staff.name(document.created_by),
        "created_at": document.created_at.isoformat(),
        "current": _version_view(current, staff) if current is not None else None,
    }


def own_view(document: Document, acks: Sequence[Acknowledgement], staff: Staff) -> dict:
    """One document for the employee: the current version and whether their signature covers it."""
    current = document.current
    by_version = {ack.version_id: ack for ack in acks}
    signed = by_version.get(current.id) if current is not None else None
    earlier = [version for version in document.versions[:-1] if version.id in by_version]
    return {
        **_document_view(document, staff),
        "signed": None if signed is None else {
            "signed_at": signed.signed_at.isoformat(), "method_label": METHOD_LABELS.get(signed.method, signed.method),
        },
        "signed_earlier_version": earlier[-1].version_no if earlier and signed is None else None,
    }


def documents_overview(user_id: UUID, uow: UnitOfWork) -> DocumentsOverview:
    """The person's documents in force (unsigned first) and, with «Кадры» edit, every document with its count."""
    with uow:
        staff = staff_of(uow)
        documents = uow.documents.all()
        user = staff.users.get(user_id)
        mine = [document for document in documents if user is not None and asked(document, user)]
        acks = uow.documents.acks_of_user(user_id)
        own = [own_view(document, acks, staff) for document in mine]
        own.sort(key=lambda item: item["current"]["uploaded_at"], reverse=True)
        own.sort(key=lambda item: item["signed"] is not None)  # unsigned first, newest first within each
        journal = None
        hr = user_id in staff.hr_ids
        if hr:
            current_ids = [document.current.id for document in documents if document.current is not None]
            signed: dict[UUID, set[UUID]] = {}
            for ack in uow.documents.acks_of_versions(current_ids):
                signed.setdefault(ack.version_id, set()).add(ack.user_id)
            journal = []
            for document in documents:
                must = readers(document, staff.users.values())
                done = signed.get(document.current.id, set()) if document.current is not None else set()
                journal.append({
                    **_document_view(document, staff),
                    "versions": [_version_view(version, staff) for version in reversed(document.versions)],
                    "readers": len(must),
                    "signed": sum(1 for reader in must if reader.id in done),
                })
            journal.sort(key=lambda item: (item["archived"], item["title"].casefold()))
        return DocumentsOverview(own=own, journal=journal, hr=hr)


def pending_count(user_id: UUID, uow: UnitOfWork) -> int:
    """The badge: documents in force whose current version waits for this person's signature."""
    with uow:
        user = staff_of(uow).users.get(user_id)
        if user is None:
            return 0
        signed = {ack.version_id for ack in uow.documents.acks_of_user(user_id)}
        return sum(
            1 for document in uow.documents.all() if asked(document, user) and document.current.id not in signed
        )


def signature_sheet(document_id: UUID, viewer_id: UUID, uow: UnitOfWork, version_no: int | None = None) -> dict:
    """«Лист ознакомления» for «Кадры»: everyone the document is aimed at today with their signature of the version
    (the current one by default), plus whoever signed it and is no longer in scope."""
    with uow:
        staff = staff_of(uow)
        if viewer_id not in staff.hr_ids:
            raise DocumentForbidden("Лист ознакомления видит сотрудник с доступом «Кадры».")
        document = uow.documents.get(document_id)
        if document is None:
            raise DocumentNotFound("Документ не найден.")
        version = document.current if version_no is None else next(
            (item for item in document.versions if item.version_no == version_no), None,
        )
        if version is None:
            raise DocumentNotFound("Редакция не найдена.")
        acks = {ack.user_id: ack for ack in uow.documents.acks_of_versions((version.id,))}
        must = readers(document, staff.users.values())
        must_ids = {user.id for user in must}
        left_scope = [staff.users[user_id] for user_id in acks if user_id in staff.users and user_id not in must_ids]
        rows = []
        for user in (*must, *left_scope):
            ack = acks.get(user.id)
            rows.append({
                "user_id": str(user.id),
                "name": user.name,
                "department": user.department,
                "job_title": user.job_title,
                "in_scope": in_scope(document, user),
                "signed_at": ack.signed_at.isoformat() if ack is not None else None,
                "method_label": METHOD_LABELS.get(ack.method, ack.method) if ack is not None else None,
            })
        rows.sort(key=lambda row: (row["signed_at"] is not None, row["name"].casefold()))
        return {
            **_document_view(document, staff),
            "versions": [_version_view(item, staff) for item in reversed(document.versions)],
            "version": _version_view(version, staff),
            "is_current": document.current is not None and version.id == document.current.id,
            "readers": len(must),
            "signed": sum(1 for user in must if user.id in acks),
            "rows": rows,
        }


def version_file(version_id: UUID, viewer_id: UUID, uow: UnitOfWork) -> tuple[str, bytes]:
    """The PDF of a version: «Кадры» open any, an employee the versions of a document in force aimed at them."""
    with uow:
        version = uow.documents.version(version_id)
        document = uow.documents.get(version.document_id) if version is not None else None
        if version is None or document is None:
            raise DocumentNotFound("Документ не найден.")
        staff = staff_of(uow)
        user = staff.users.get(viewer_id)
        if viewer_id not in staff.hr_ids and not (user is not None and asked(document, user)):
            raise DocumentForbidden("Этот документ адресован не вам.")
        content = uow.documents.content(version.id)
        if content is None:
            raise DocumentNotFound("Документ не найден.")
        return version.file_name, content
