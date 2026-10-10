"""The stored harness drawing as a document (pure): its content types, size limit, JSON encoding, the read-back
normalisation and the revision record of the history.

A drawing is ONE row of the КД register per position (content type HARNESS_CONTENT_TYPE) holding the current bytes,
plus an append-only revision per save (`techcard_harness_revisions`, migration 0090). The integer revision is the
concurrency token: a save names the revision it was made on and gets a conflict when someone saved another one since.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
from typing import Any, Literal, Mapping

from .snapshot import SCHEMA_VERSION, dump_design, parse_design

# The harness drawing (TECHMAP-GRAPHER design document, schemaVersion 1) kept as a КД row of its position.
HARNESS_CONTENT_TYPE = "application/vnd.fraxis.harness+json"
# A drawing unlinked from its position («Отвязать»): the row, its bytes and its history stay; the position no longer
# reads it, the card lists it with «Вернуть». A harness drawing is never hard-deleted.
HARNESS_UNLINKED_CONTENT_TYPE = "application/vnd.fraxis.harness-unlinked+json"
# The same ceiling as an attached КД file.
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

# Library components as a drawing carried them before the component library (migration 0091) and as
# TECHMAP-GRAPHER's export carries them (project-level sections). A write moves them into the library
# (`components.split_embedded`); the stored drawing keeps only the pins on its connectors.
COMPONENT_SNAPSHOTS = "componentSnapshots"
COMPONENT_PLACEMENTS = "componentPlacements"
COMPONENT_GRAPH_KEYS = (COMPONENT_SNAPSHOTS, COMPONENT_PLACEMENTS)

# How a revision came to be: a save from the editor, an import of a TECHMAP-GRAPHER export, a restore of an earlier
# revision, or the drawing as it was when the history began (migration 0090 / a drawing older than its history).
RevisionKind = Literal["saved", "imported", "restored", "initial"]
REVISION_KINDS: tuple[str, ...] = ("saved", "imported", "restored", "initial")


class HarnessError(ValueError):
    """A drawing that cannot be read or stored; the message is for the technologist."""


class HarnessWriteConflict(RuntimeError):
    """The database refused a write because a concurrent one landed first (a revision number or the one linked
    drawing of a position is already taken)."""


# Autosaves of one person within this time from the start of their open «saved» history row fold into it.
FOLD_WINDOW = timedelta(minutes=10)


@dataclass(frozen=True)
class HarnessRevision:
    """One row of a drawing's history: the drawing as of `revision`, saved by one person from `started_at`
    (revision `first_revision`) to `saved_at`. Autosaves of that person within FOLD_WINDOW fold into the open row of
    kind «saved»; a closed row («Сохранить версию») and rows of other kinds never change again."""
    document_id: Any
    revision: int
    content_sha256: str
    size: int
    saved_by: str
    saved_at: datetime
    kind: str
    restored_from: int | None = None
    first_revision: int = 0
    started_at: datetime | None = None
    closed: bool = False

    def takes_autosave(self, saved_by: str, now: datetime) -> bool:
        """An autosave of `saved_by` at `now` folds into this row instead of starting a new one."""
        if self.kind != "saved" or self.closed or self.saved_by != saved_by or self.started_at is None:
            return False
        started, moment = self.started_at, now
        if (started.tzinfo is None) != (moment.tzinfo is None):  # SQLite hands times back without the zone
            started, moment = started.replace(tzinfo=None), moment.replace(tzinfo=None)
        return timedelta(0) <= moment - started <= FOLD_WINDOW


def plain(value: Any) -> Any:
    """Decimals as JSON numbers, tuples as lists."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def check_version(content: object) -> Mapping[str, Any]:
    if not isinstance(content, Mapping):
        raise HarnessError("Чертёж жгута должен быть объектом JSON")
    version = content.get("schemaVersion")
    if version is not None and version != SCHEMA_VERSION:
        raise HarnessError(f"Поддерживается только схема чертежа {SCHEMA_VERSION}, в документе — {version}")
    return content


def normalized(content: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The design document as stored: read leniently, written back with every field it had."""
    design, warnings = parse_design(check_version(content))
    return dump_design(design), warnings


def encode(document: Mapping[str, Any]) -> bytes:
    content = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HarnessError("Чертёж жгута больше 20 МБ")
    return content


def decode(content: bytes) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HarnessError("Сохранённый чертёж жгута повреждён — не JSON") from error
    if not isinstance(value, dict):
        raise HarnessError("Сохранённый чертёж жгута повреждён — не объект")
    return value


def content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def legacy_version(content: bytes) -> str:
    """The version token before revisions (a hash of the stored bytes). Accepted for one release, so a browser tab
    opened before the update still saves; remove with `Expected.legacy`."""
    return content_sha256(content)[:32]


@dataclass(frozen=True)
class Expected:
    """What a write was made on: the revision read with the drawing (or, for one release, the old hash token).
    Neither — the writer believes the position has no drawing yet."""
    revision: int | None = None
    legacy: str | None = None

    @classmethod
    def of(cls, revision: int | None, version: str | None) -> "Expected":
        """`version` is the deprecated field: a number in it is a revision (what this release hands out as
        `version`), anything else the old hash token."""
        text = (version or "").strip()
        if revision is None and text.isdigit():
            return cls(revision=int(text))
        return cls(revision=revision, legacy=(text or None) if revision is None else None)

    @property
    def given(self) -> bool:
        return self.revision is not None or self.legacy is not None

    def matches(self, revision: int, content: bytes) -> bool:
        if self.revision is not None:
            return self.revision == revision
        return self.legacy is not None and self.legacy == legacy_version(content)
