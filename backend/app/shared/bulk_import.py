"""Bulk import into a reference table (owner 12.10.2026: «парсить контакты, разъёмы вручную гемор»).

One framework every per-table importer (`POST /api/references/import/<table>?dry_run=1|0`, body `ImportRequest`,
answer `ImportReport`) runs through, so a program with an API token (`refs:write`) or a person loads rows the same way:

* rows are matched to the table by a natural key (`ImportTable.key_fields`, values joined by `KEY_SEPARATOR`); a key
  field in `ImportTable.optional_keys` may be empty (БД.ПРОВ «Цвет»: only a монтажный wire has one) and is then left
  out of the key string;
* a key not in the table is created, a known one is updated field by field, the rest is counted as unchanged;
* nothing is ever deleted, and a field that is null or absent in the row stays as it is (`""` clears it);
* `old` — the values the caller read before: a field whose current value differs is the whole row refused with
  `STALE_REASON` (like FieldEdit in references/api.py), so a load prepared from a stale export never overwrites a
  newer edit;
* a bad row is refused with its reason, every other row still applies;
* dry-run runs the very same path — hooks, audit event included — and rolls back, so its report is what the apply does.

The table's own module supplies an `ImportTarget` over its unit of work; this file knows no table."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Protocol, Sequence
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .events import AuditEvent
from .identity import Actor

MAX_IMPORT_ROWS = 2000
# The request body ceiling for `/api/references/import` is set in app/main.py (RequestBodyLimitMiddleware path limits).
MAX_IMPORT_BODY_BYTES = 8 * 1_048_576
MAX_VALUE_LENGTH = 4000
KEY_SEPARATOR = " / "
AUDIT_KEYS_MAX = 50
STALE_REASON = "значение изменилось с момента выгрузки"
DUPLICATE_REASON = "ключ повторяется в загрузке"


# ── Request / report (the HTTP contract) ──────────────────────────────────────────────────────────────────────────


class ImportRowIn(BaseModel):
    """One incoming row: `fields` — field → value (null = leave as is); `old` — optional field → value read before."""

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Any] = Field(min_length=1)
    old: dict[str, Any] | None = None


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[ImportRowIn] = Field(min_length=1, max_length=MAX_IMPORT_ROWS)


class ImportUpdate(BaseModel):
    key: str
    changes: dict[str, tuple[Any, Any]]  # field → [old, new] in JSON


class ImportRefusal(BaseModel):
    key: str  # the row's key, or `#<row number from 1>` when the row has no usable key
    reason: str


class ImportReport(BaseModel):
    dry_run: bool
    created: list[str] = Field(default_factory=list)
    updated: list[ImportUpdate] = Field(default_factory=list)
    unchanged: int = 0
    refused: list[ImportRefusal] = Field(default_factory=list)


# ── Errors ────────────────────────────────────────────────────────────────────────────────────────────────────────


class RowRefused(ValueError):
    """Raised by a normalizer or an apply hook: this row is refused with the message, the others go on."""


class ImportRefused(Exception):
    """The whole call is refused; `status_code` is what the HTTP route answers."""

    status_code = 422


class ImportTooLarge(ImportRefused):
    status_code = 413


class ImportRateLimited(ImportRefused):
    status_code = 429


# ── Table and target ──────────────────────────────────────────────────────────────────────────────────────────────


def clean_value(field: str, value: Any) -> Any:
    """Default normalizer: text is stripped, numbers become text, anything else refuses the row. None stays None."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise RowRefused(f"{field}: ожидается текст или число")
    if isinstance(value, float):
        text = str(int(value)) if value.is_integer() else repr(value)
    else:
        text = str(value).strip()
    if len(text) > MAX_VALUE_LENGTH:
        raise RowRefused(f"{field}: длиннее {MAX_VALUE_LENGTH} символов")
    return text


@dataclass(frozen=True)
class ImportTable:
    name: str  # the `<table>` path segment and the audit detail
    key_fields: tuple[str, ...]
    fields: frozenset[str]  # every field a row may carry, key fields included
    event_type: str  # the audit event of one apply, e.g. "references.connectors_imported"
    aggregate_id: UUID
    normalize: Callable[[str, Any], Any] = clean_value
    optional_keys: frozenset[str] = frozenset()  # key fields a row may leave empty (not part of the key then)

    def __post_init__(self) -> None:
        if not self.key_fields or not set(self.key_fields) <= self.fields:
            raise ValueError("Key fields must be a non-empty subset of the table fields.")
        if not self.optional_keys < set(self.key_fields):
            raise ValueError("Optional keys must be key fields, and not all of them.")

    @property
    def required_keys(self) -> tuple[str, ...]:
        return tuple(name for name in self.key_fields if name not in self.optional_keys)


def row_key(table: ImportTable, fields: Mapping[str, Any]) -> str:
    """The natural key as one string: the target uses it for its own rows too, so both sides match. An empty optional
    key field is left out."""
    return KEY_SEPARATOR.join(str(fields.get(name) or "") for name in table.key_fields
                              if name not in table.optional_keys or fields.get(name))


class ImportTarget(Protocol):
    """The table's side, over one unit of work opened for this call."""

    def existing(self, keys: Sequence[str]) -> Mapping[str, Mapping[str, Any]]:
        """Current fields of the rows with these keys (`row_key`), normalized the way `ImportTable.normalize` does
        (they are compared as is); a key not in the table is simply absent."""

    def create(self, key: str, fields: dict[str, Any]) -> None:
        """Add a row (non-null incoming fields, key fields included). Raise `RowRefused` before writing to refuse it."""

    def update(self, key: str, changes: dict[str, Any]) -> None:
        """Set these fields of an existing row. Raise `RowRefused` before writing to refuse it."""

    def audit(self, event: AuditEvent) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


# ── Rate limit ────────────────────────────────────────────────────────────────────────────────────────────────────


class ImportRateLimiter:
    """At most `max_calls` import calls (dry-runs included) per principal within `window_seconds`.

    Per process: the counters live in this API process's memory, so with several workers each counts on its own and a
    restart clears them. It is a brake on a runaway script, not an accounting of calls."""

    def __init__(self, max_calls: int = 30, window_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self.clock = clock
        self._calls: dict[UUID, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()  # sync routes run in the threadpool

    def check(self, principal_id: UUID) -> None:
        now = self.clock()
        with self._lock:
            calls = self._calls[principal_id]
            while calls and calls[0] <= now - self.window_seconds:
                calls.popleft()
            if len(calls) >= self.max_calls:
                raise ImportRateLimited(
                    f"Слишком часто: не больше {self.max_calls} загрузок за {int(self.window_seconds)} с — подождите."
                )
            calls.append(now)


RATE_LIMITER = ImportRateLimiter()


# ── The import ────────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass
class _Row:
    key: str
    fields: dict[str, Any]
    old: dict[str, Any] | None


def _empty(value: Any) -> Any:
    return "" if value is None else value


def _parse(table: ImportTable, row: ImportRowIn) -> _Row:
    unknown = sorted(set(row.fields) - table.fields) + sorted(set(row.old or {}) - table.fields)
    if unknown:
        raise RowRefused("неизвестное поле: " + ", ".join(dict.fromkeys(unknown)))
    fields = {name: table.normalize(name, value) for name, value in row.fields.items()}
    missing = [name for name in table.required_keys if not fields.get(name)]
    if missing:
        raise RowRefused("не заполнен ключ: " + ", ".join(missing))
    old = None if row.old is None else {name: table.normalize(name, value) for name, value in row.old.items()}
    return _Row(row_key(table, fields), fields, old)


def run_import(
    table: ImportTable,
    rows: Sequence[ImportRowIn],
    target: ImportTarget,
    *,
    actor: Actor,
    now: datetime,
    dry_run: bool,
    limiter: ImportRateLimiter | None = RATE_LIMITER,
) -> ImportReport:
    """Diff the rows against the table, apply them through the target and audit the apply in one event (actor —
    the principal: a token appears as «API: <name>»). Commits only when `dry_run` is false and something changed."""
    if len(rows) > MAX_IMPORT_ROWS:
        raise ImportTooLarge(f"Строк за раз: не больше {MAX_IMPORT_ROWS}")
    if limiter is not None:
        limiter.check(actor.id)
    report = ImportReport(dry_run=dry_run)
    parsed: list[tuple[int, _Row]] = []
    for number, row in enumerate(rows, start=1):
        try:
            parsed.append((number, _parse(table, row)))
        except (RowRefused, ValueError) as error:
            key = row_key(table, row.fields) if all(row.fields.get(name) for name in table.required_keys) else ""
            report.refused.append(ImportRefusal(key=key or f"#{number}", reason=str(error)))
    counts: dict[str, int] = defaultdict(int)
    for _number, row in parsed:
        counts[row.key] += 1
    for _number, row in parsed:
        if counts[row.key] > 1:
            report.refused.append(ImportRefusal(key=row.key, reason=DUPLICATE_REASON))
    parsed = [(number, row) for number, row in parsed if counts[row.key] == 1]
    try:
        current_rows = target.existing([row.key for _number, row in parsed])
        changed_keys: list[str] = []
        for _number, row in parsed:
            current = current_rows.get(row.key)
            values = dict(current or {})
            if row.old is not None and any(_empty(value) != _empty(values.get(name)) for name, value in row.old.items()):
                report.refused.append(ImportRefusal(key=row.key, reason=STALE_REASON))
                continue
            try:
                if current is None:
                    fields = {name: value for name, value in row.fields.items() if value is not None}
                    target.create(row.key, fields)
                    report.created.append(row.key)
                    changed_keys.append(row.key)
                    continue
                changes = {
                    name: (values.get(name), value) for name, value in row.fields.items()
                    if value is not None and name not in table.key_fields and value != _empty(values.get(name))
                }
                if not changes:
                    report.unchanged += 1
                    continue
                target.update(row.key, {name: new for name, (_old, new) in changes.items()})
                report.updated.append(ImportUpdate(key=row.key, changes=changes))
                changed_keys.append(row.key)
            except RowRefused as error:
                report.refused.append(ImportRefusal(key=row.key, reason=str(error)))
        if changed_keys:
            target.audit(AuditEvent(
                id=uuid4(), aggregate_id=table.aggregate_id, event_type=table.event_type, actor=actor, occurred_at=now,
                details={
                    "table": table.name, "created": str(len(report.created)), "updated": str(len(report.updated)),
                    "unchanged": str(report.unchanged), "refused": str(len(report.refused)),
                    "keys": changed_keys[:AUDIT_KEYS_MAX],  # type: ignore[dict-item]  (a JSON column)
                },
            ))
        if dry_run or not changed_keys:
            target.rollback()
        else:
            target.commit()
    except BaseException:
        target.rollback()
        raise
    return report
