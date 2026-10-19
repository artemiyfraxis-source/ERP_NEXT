"""The ETK refresh job (owner, 19.10.2026): cards must not need a manual «Пересобрать». A reference, a template or a
builder-rule change issues a NEW REVISION of every affected issued card by itself — each revision a frozen full
document, the technologist's hand edits kept (`etk_derive.derive` applies the overrides).

Triggers enqueue a row in `techcard_refresh_requests` inside the transaction of the change itself (`before_flush`
listener below: any write to a reference or template table, whichever code path made it). The job takes every pending
request as one batch — a card gets one revision per run, not one per edited cell — loads the references once, derives
every card that has `inputs`, and compares with the stored document:

    different  → issued card: revision N+1 (`source='auto'`, `diff`, journal reason); draft: refreshed in place
    same       → nothing (the hash is stamped when it was missing)
    conflicts  → `missing_operation` / `route_not_applied`: the revision is still issued, the overrides kept, the card
                 gets `review_note` «нужна проверка технолога: …»; `template_changed` is a journal note only

One runner at a time (a Postgres advisory lock; a process lock on SQLite). A card whose `version` moved under the job
(an edit form saved meanwhile) is skipped and its requests stay pending for the next run. Production tasks keep the
revision they were launched with (`etk_read.pinned_card`).

    python -m app.workflows.etk_refresh [--dry-run] [--all]

`--dry-run` writes nothing and prints per card: would-revise / unchanged / conflict, with the diff summary. `--all`
checks every card even without pending requests (a deploy with a changed builder). The outbox worker and the deploy
step run it live only when `ETK_AUTO_REVISION=1`; otherwise the deploy step is a dry-run, the worker stays out."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
import logging
import os
import sys
import threading
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.techcards import etk_derive
from app.modules.techcards.etk_derive import BUILD_FAILED, MISSING_OPERATION, NO_INPUTS, ROUTE_NOT_APPLIED, TEMPLATE_CHANGED
from app.modules.techcards.infrastructure import ProjectTechcardRow, RefreshRequestRow, SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.project_cards import ProjectTechcard, TechcardRevision, TechcardStatus, operation_key
from app.shared.clock import moscow_now
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.workflows.techcard_build import Refs, load_refs, with_technology

ENV_FLAG = "ETK_AUTO_REVISION"
ACTOR_NAME = "автоматическое обновление ЭТК"
NOTE_PREFIX = "нужна проверка технолога: "
REASON_REFERENCE, REASON_TEMPLATE, REASON_BUILDER = "reference", "template", "builder"
_LOCK_KEY = 7_540_190_001
_PROCESS_LOCK = threading.Lock()

# Tables whose write makes the stored cards due for a check (the reference bases the enrichers read; the operation
# template library the builder fills the cards from).
REFERENCE_TABLES = frozenset({
    "reference_entries", "tech_operation_records", "tech_operation_snapshots", "wire_gauges", "hookup_wires",
    "coax_cables", "coax_cable_layers", "coax_cable_connectors", "coax_cable_markers", "coax_cable_attenuation",
    "coax_cable_aliases",
})
TEMPLATE_TABLES = frozenset({"techcard_operation_templates", "techcard_operation_template_snapshots"})


def auto_revision_enabled() -> bool:
    return os.getenv(ENV_FLAG, "").strip() == "1"


# ── Triggers ──

def _touched(session: Session) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {REASON_REFERENCE: set(), REASON_TEMPLATE: set()}
    changed = [*session.new, *session.deleted, *(item for item in session.dirty if session.is_modified(item))]
    for item in changed:
        table = getattr(getattr(item, "__table__", None), "name", None)
        if table in REFERENCE_TABLES:
            found[REASON_REFERENCE].add(table)
        elif table in TEMPLATE_TABLES:
            found[REASON_TEMPLATE].add(table)
    return found


def _enqueue_on_flush(session: Session, _context: object, _instances: object) -> None:
    """A reference or template write queues one refresh request in the same transaction. One pending request per
    reason is enough: the job works in batches."""
    touched = _touched(session)
    for reason, tables in touched.items():
        if not tables:
            continue
        if any(isinstance(item, RefreshRequestRow) and item.reason == reason for item in session.new):
            continue
        with session.no_autoflush:
            pending = session.scalar(select(RefreshRequestRow.id).where(
                RefreshRequestRow.reason == reason, RefreshRequestRow.processed_at.is_(None)).limit(1))
        if pending is None:
            session.add(RefreshRequestRow(
                id=uuid4(), reason=reason, payload={"tables": sorted(tables)}, created_at=moscow_now()))


event.listen(Session, "before_flush", _enqueue_on_flush)


def enqueue_builder_check(session: Session, now: datetime) -> None:
    """Queue a check for a changed builder (the deploy step)."""
    session.add(RefreshRequestRow(
        id=uuid4(), reason=REASON_BUILDER, payload={"builder_hash": etk_derive.builder_hash()}, created_at=now))


# ── One card ──

@dataclass(frozen=True)
class CardResult:
    card_id: UUID
    label: str
    status: str  # revised | refreshed | unchanged | conflict | skipped | failed
    changes: tuple[dict[str, str], ...] = ()
    conflicts: tuple[dict[str, str], ...] = ()
    revision: int | None = None

    def line(self) -> str:
        names = ", ".join(dict.fromkeys(item["field"] for item in self.changes)) or "—"
        head = {
            "revised": "было бы выпущено" if self.revision is None else f"выпущена ревизия {self.revision}",
            "refreshed": "черновик обновлён", "unchanged": "без изменений", "conflict": "нужна проверка технолога",
            "skipped": "пропущена (правили во время запуска) — в следующий раз", "failed": "ошибка",
        }[self.status]
        text_ = f"{self.label}: {head}"
        if self.changes:
            text_ += f" — {names}"
        for item in self.conflicts:
            text_ += f"\n    ! {item['message']}" + (f" [{item['operation_key']}]" if item["operation_key"] else "")
        for item in self.changes[:10]:
            text_ += f"\n    {item['label']}: {item['field']}"
        return text_


@dataclass(frozen=True)
class Context:
    refs: Refs
    templates: tuple[Any, ...]
    commit: str
    snapshot: Mapping[str, Any] | None
    builder: str
    reason: str  # REASON_REFERENCE | REASON_BUILDER: what the journal names
    now: datetime


def _effective_overrides(card: ProjectTechcard) -> dict[str, Any]:
    """The stored overrides plus what an older card kept only in its document: operations hand-edited before the
    overrides existed (`manual_operation_keys`) and a hand-cut ПФ scheme — a rebuild must never lose them."""
    overrides = {name: dict(value) if isinstance(value, Mapping) else value for name, value in (card.overrides or {}).items()}
    document = card.document or {}
    edits = dict(overrides.get("operations") or {})
    versions = (document.get("source") or {}).get("templates") or {}
    by_key = {}
    for op in document.get("operations") or []:
        try:
            by_key[operation_key(op)] = op
        except Exception:  # noqa: BLE001 — an operation without a key cannot carry an edit
            continue
    for key in card.manual_operation_keys:
        if key in by_key and key not in edits:
            edits[key] = {"operation": by_key[key],
                          "base_template_version": versions.get(str(by_key[key].get("template_id") or "").strip(), "")}
    if edits:
        overrides["operations"] = edits
    if "route" not in overrides and (route := etk_derive.route_override(document)) is not None:
        overrides["route"] = route
    return overrides


def _label(card: ProjectTechcard) -> str:
    return f"{card.project} {card.item_index}".strip()


def _what(changes: list[dict[str, str]]) -> str:
    names = list(dict.fromkeys(item["field"] for item in changes))
    shown = ", ".join(names[:4]) + (" и др." if len(names) > 4 else "")
    return shown or "документ"


def _journal_reason(ctx: Context, changes: list[dict[str, str]], soft: list[dict[str, str]]) -> str:
    day = ctx.now.strftime("%d.%m.%Y")
    if ctx.reason == REASON_BUILDER:
        reason = f"обновлено по правилам сборки ({day})"
    else:
        reason = f"обновлено по справочнику: {_what(changes)} ({day})"
    if soft:
        reason += "; шаблон операции изменился после правки технолога — правка оставлена как была"
    return reason


def evaluate(card: ProjectTechcard, ctx: Context) -> tuple[ProjectTechcard | None, TechcardRevision | None, CardResult]:
    """(the card to save or None, the revision to add or None, the result). Pure: nothing is written here."""
    label = _label(card)
    overrides = _effective_overrides(card)
    document, conflicts = etk_derive.derive(
        card.inputs, overrides, lambda config: with_technology(config, refs=ctx.refs)[0], ctx.templates,
        operation_library_commit=ctx.commit, technology_source=ctx.snapshot)
    if document is None:
        kinds = {item["kind"] for item in conflicts}
        if NO_INPUTS in kinds:
            return None, None, CardResult(card.id, label, "unchanged")
        note = NOTE_PREFIX + "; ".join(item["message"] for item in conflicts)
        result = CardResult(card.id, label, "conflict", conflicts=tuple(conflicts))
        if card.review_note == note:
            return None, None, result
        return replace(card, review_note=note, version=card.version + 1, updated_at=ctx.now), None, result
    hard = [item for item in conflicts if item["kind"] in (MISSING_OPERATION, ROUTE_NOT_APPLIED)]
    soft = [item for item in conflicts if item["kind"] == TEMPLATE_CHANGED]
    versions = (document.get("source") or {}).get("templates") or {}
    new_hash = etk_derive.derived_hash(card.inputs, overrides, ctx.refs.stamp, versions, ctx.builder)
    if hard:
        note: str | None = NOTE_PREFIX + "; ".join(dict.fromkeys(item["message"] for item in hard))
    else:
        note = None if (card.review_note or "").startswith(NOTE_PREFIX) else card.review_note
    changes = etk_derive.document_diff(card.document, document)
    status = "conflict" if hard else "unchanged"
    if not changes:
        if new_hash == card.derived_hash and note == card.review_note:
            return None, None, CardResult(card.id, label, status, conflicts=tuple(hard))
        stamped = replace(card, derived_hash=new_hash, review_note=note, version=card.version + 1, updated_at=ctx.now)
        return stamped, None, CardResult(card.id, label, status, conflicts=tuple(hard))
    if card.status is not TechcardStatus.ISSUED:
        updated = replace(card, document=document, derived_hash=new_hash, review_note=note,
                          version=card.version + 1, updated_at=ctx.now)
        return updated, None, CardResult(card.id, label, "refreshed", tuple(changes), tuple(hard))
    updated = replace(card, document=document, revision=card.revision + 1, derived_hash=new_hash, review_note=note,
                      version=card.version + 1, updated_at=ctx.now)
    reason = _journal_reason(ctx, changes, soft)
    revision = TechcardRevision(
        id=uuid4(), project_card_id=card.id, revision=updated.revision, reason=reason,
        actor_name=ACTOR_NAME, occurred_at=ctx.now, document=document, source="auto",
        diff={"changes": changes, "reason": reason})
    return updated, revision, CardResult(
        card.id, label, "revised", tuple(changes), tuple(hard), updated.revision)


# ── The run ──

@dataclass
class Report:
    dry_run: bool
    requests: int = 0
    cards: list[CardResult] = field(default_factory=list)
    note: str = ""
    skipped: int = 0

    def count(self, *statuses: str) -> int:
        return sum(1 for item in self.cards if item.status in statuses)

    def text(self) -> str:
        head = "Пробный запуск (ничего не записано)" if self.dry_run else "Обновление ЭТК"
        lines = [f"{head}: запросов {self.requests}, карт {len(self.cards)}"]
        if self.note:
            lines.append(self.note)
        lines += [item.line() for item in self.cards if item.status != "unchanged" or item.conflicts]
        review = sum(1 for item in self.cards if item.conflicts or item.status == "conflict")
        lines.append(
            f"Итого: ревизий {self.count('revised')}, черновиков {self.count('refreshed')}, на проверку технологу "
            f"{review}, без изменений {self.count('unchanged')}, пропущено {self.skipped}")
        return "\n".join(lines)


@contextmanager
def single_runner(sessions: sessionmaker[Session]) -> Iterator[bool]:
    """One refresh at a time: a Postgres advisory lock (across the API, the worker and the CLI), a process lock else."""
    bind = sessions.kw.get("bind")
    if bind is not None and bind.dialect.name == "postgresql":
        connection = bind.connect()
        try:
            got = bool(connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _LOCK_KEY}).scalar())
            try:
                yield got
            finally:
                if got:
                    connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY})
        finally:
            connection.close()
        return
    if not _PROCESS_LOCK.acquire(blocking=False):
        yield False
        return
    try:
        yield True
    finally:
        _PROCESS_LOCK.release()


def run_refresh(
    sessions: sessionmaker[Session], *, now: datetime | None = None, dry_run: bool = False, everything: bool = False,
) -> Report:
    """One batch: every pending request, every card with `inputs`. `everything` — check the cards even without
    requests (a deploy with a changed builder)."""
    now = now or moscow_now()
    report = Report(dry_run=dry_run)
    with single_runner(sessions) as got:
        if not got:
            report.note = "Другое обновление уже идёт — этот запуск пропущен"
            return report
        with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
            pending = list(uow.session.scalars(select(RefreshRequestRow).where(
                RefreshRequestRow.processed_at.is_(None)).order_by(RefreshRequestRow.created_at)))
            request_ids = [item.id for item in pending]
            reasons = {item.reason for item in pending}
            report.requests = len(pending)
            if not pending and not everything:
                return report
            metadata = uow.operation_cards.metadata()
            templates = tuple(uow.operation_cards.list())
            card_ids = [card.id for card in uow.techcards.list() if card.inputs]
        if metadata is None:
            report.note = "Библиотека операционных карт не загружена — запросы остаются в очереди"
            return report
        refs = load_refs(lambda: SqlAlchemyReferencesUnitOfWork(sessions))
        ctx = Context(
            refs=refs, templates=templates, commit=str(metadata.get("source_commit") or ""),
            snapshot=dict(refs.snapshot) if refs.snapshot is not None else None, builder=etk_derive.builder_hash(),
            reason=REASON_REFERENCE if reasons & {REASON_REFERENCE, REASON_TEMPLATE} else REASON_BUILDER, now=now)
        for card_id in card_ids:
            report.cards.append(_refresh_one(sessions, card_id, ctx, dry_run))
        report.skipped = report.count("skipped")
        if not dry_run and request_ids and not report.skipped and not report.count("failed"):
            with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
                for row in uow.session.scalars(select(RefreshRequestRow).where(RefreshRequestRow.id.in_(request_ids))):
                    row.processed_at = now
                uow.commit()
    return report


def _refresh_one(sessions: sessionmaker[Session], card_id: UUID, ctx: Context, dry_run: bool) -> CardResult:
    try:
        with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
            card = uow.techcards.get(card_id)
            if card is None or not card.inputs or card.document is None:
                return CardResult(card_id, str(card_id), "unchanged")
            updated, revision, result = evaluate(card, ctx)
            if dry_run:
                return replace(result, revision=None)
            if updated is None:
                return result
            # The version guard: an edit form that saved meanwhile wins (the ORM check covers the rest on PostgreSQL).
            stored = uow.session.scalar(select(ProjectTechcardRow.version).where(ProjectTechcardRow.id == card_id))
            if stored != card.version:
                return CardResult(card_id, result.label, "skipped")
            uow.techcards.save(updated)
            if revision is not None:
                uow.techcards.add_revision(revision)
                uow.audit.add(AuditEvent(
                    id=uuid4(), aggregate_id=card.id, event_type="techcards.project_auto_revised",
                    actor=Actor(id=uuid4(), name=ACTOR_NAME, department=""), occurred_at=ctx.now,
                    details={"revision": revision.revision, "reason": revision.reason, "changes": len(result.changes)}))
            uow.commit()
            return result
    except StaleDataError:
        return CardResult(card_id, str(card_id), "skipped")
    except Exception:  # noqa: BLE001 — one broken card must not stop the batch
        logging.exception("ETK refresh: card %s failed", card_id)
        return CardResult(card_id, str(card_id), "failed")


def refresh_tick(sessions: sessionmaker[Session]) -> int:
    """The outbox worker's pass: pending requests become revisions, only with ETK_AUTO_REVISION=1. → revisions made."""
    if not auto_revision_enabled():
        return 0
    report = run_refresh(sessions)
    return report.count("revised", "refreshed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Автоматические ревизии ЭТК по правке справочника или правил сборки")
    parser.add_argument("--dry-run", action="store_true", help="ничего не писать: показать, что изменится")
    parser.add_argument("--all", action="store_true", help="проверить все карты, даже без запросов (после выкладки)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from app.database import build_engine

    engine = build_engine(url)
    try:
        sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
        print(run_refresh(sessions, dry_run=args.dry_run, everything=args.all).text())
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
