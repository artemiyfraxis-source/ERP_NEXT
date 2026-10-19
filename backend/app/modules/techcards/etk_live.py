"""Live operation cards of a stored ETK (owner, 17.10.2026: «если карта берётся без изменений — она должна идти из базы
данных, а не копией»).

The ETK document keeps a filled copy of every operation card (`etk_builder`). On read, an operation that nobody edited
by hand shows the card of its template as the template is now: when the template changed after the build, the card is
filled again by the same builder from the document's own `build_config` — the revision, route, kit rows, norms and
quantities of the document stay as stored, only `card` is replaced. Each operation of the view says where its card came
from (`card_source`): «template» — the template (unchanged since the build, or filled anew), «edited» — corrected in
this ETK by hand («Править операцию»), «snapshot» — the copy made at the build (no template in the library any more, an
imported ETK without a build config, or filling failed).

Which template version a build saw is stamped in `document.source.templates` ({template id: version}); a document
built before the stamp counts a template as changed when its ERP edit is newer than the card's last update."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import hashlib
import json
from typing import Any

from .etk_builder import COMBINED_DEFECTS, build_etk, with_coax_connectors
from .operation_cards import OperationCardTemplate
from .project_cards import TechcardValidationError, operation_key

TEMPLATE = "template"
EDITED = "edited"
SNAPSHOT = "snapshot"
# View-only keys of an operation: never stored back into the document.
VIEW_KEYS = ("card_source", "card_changed_at")
_COMBINED_CODES = tuple(component for _, component, _ in COMBINED_DEFECTS)


def template_version(card: OperationCardTemplate) -> str:
    """A short hash of what the template puts into a card — any edit (ERP record edit, saved template sheet, library
    reload) gives a new version."""
    content = {key: value for key, value in card.document().items() if key != "images"}
    return hashlib.sha1(json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def _operations(document: Mapping[str, Any] | None) -> list[Any]:
    value = document.get("operations") if isinstance(document, Mapping) else None
    return value if isinstance(value, list) else []


def referenced(documents: Iterable[Mapping[str, Any] | None]) -> tuple[set[str], set[str]]:
    """Template ids and codes the documents' operations name (plus the cards whose defects combined cards add) — what
    a read of their live cards needs from the library."""
    ids: set[str] = set()
    codes: set[str] = set()
    for document in documents:
        for op in _operations(document):
            if not isinstance(op, Mapping):
                continue
            if str(op.get("template_id") or "").strip():
                ids.add(str(op["template_id"]).strip())
            if str(op.get("code") or "").strip():
                codes.add(str(op["code"]).strip())
    if ids or codes:
        codes.update(_COMBINED_CODES)
    return ids, codes


def load_templates(repository: Any, documents: Sequence[Mapping[str, Any] | None]) -> list[OperationCardTemplate]:
    """The library templates the documents need, read once (in the library's order, as a build reads them)."""
    ids, codes = referenced(documents)
    if not ids and not codes or repository is None:
        return []
    matching = getattr(repository, "matching", None)
    if callable(matching):
        return list(matching(ids, codes))
    return [card for card in repository.list() if card.source_id in ids or card.code in codes]


def template_for(op: Mapping[str, Any], by_id: Mapping[str, OperationCardTemplate],
                  templates: Sequence[OperationCardTemplate]) -> OperationCardTemplate | None:
    """The operation's template by its id; gone (a library reload renamed it) — by its code, the same side first. An
    operation built without a card (its template was missing then) gets none: the ETK stays as it was built."""
    template_id = str(op.get("template_id") or "").strip()
    if template_id in by_id:
        return by_id[template_id]
    code = str(op.get("code") or "").strip()
    if not template_id or not code or not isinstance(op.get("card"), Mapping):
        return None
    found = [card for card in templates if card.code == code]
    side = str(op.get("side") or "").strip()
    return next((card for card in found if card.side == side), found[0] if found else None)


def _after(left: datetime, right: datetime) -> bool:
    if (left.tzinfo is None) != (right.tzinfo is None):
        left, right = left.replace(tzinfo=None), right.replace(tzinfo=None)
    return left > right


def seen_versions(
    document: Mapping[str, Any] | None, templates: Sequence[OperationCardTemplate], built_at: datetime | None,
) -> dict[str, str]:
    """{template id: version} the document's cards were filled from: the build's stamp, else (a document built before
    the stamp) the current version of each template not edited in ERP after `built_at`, «» — edited after it."""
    source = document.get("source") if isinstance(document, Mapping) else None
    stamped = source.get("templates") if isinstance(source, Mapping) else None
    if isinstance(stamped, Mapping):
        return {str(key): str(value) for key, value in stamped.items()}
    by_id = {card.source_id: card for card in templates}
    seen: dict[str, str] = {}
    for op in _operations(document):
        if not isinstance(op, Mapping):
            continue
        template = template_for(op, by_id, templates)
        template_id = str(op.get("template_id") or "").strip()
        if template is None or not template_id:
            continue
        newer = template.changed_at is not None and built_at is not None and _after(template.changed_at, built_at)
        seen[template_id] = "" if newer else template_version(template)
    return seen


def stamp_templates(document: dict[str, Any], templates: Sequence[OperationCardTemplate]) -> dict[str, Any]:
    """The built document with the versions of the templates its operations were filled from."""
    by_id = {card.source_id: card for card in templates}
    versions = {
        str(op["template_id"]).strip(): template_version(by_id[str(op["template_id"]).strip()])
        for op in _operations(document)
        if isinstance(op, Mapping) and str(op.get("template_id") or "").strip() in by_id
    }
    source = document.get("source") if isinstance(document.get("source"), Mapping) else {}
    return {**document, "source": {**source, "templates": versions}}


def _edited_at(op: Mapping[str, Any]) -> str:
    edited = op.get("edited")
    raw = str(edited.get("at") or "") if isinstance(edited, Mapping) else ""
    try:
        return datetime.strptime(raw, "%d.%m.%Y %H:%M").isoformat()
    except ValueError:
        return ""


def _key(op: Mapping[str, Any]) -> str | None:
    try:
        return operation_key(dict(op))
    except TechcardValidationError:
        return None


def live_document(
    document: Mapping[str, Any] | None, *, manual_keys: Iterable[str], templates: Sequence[OperationCardTemplate],
    built_at: datetime | None, refill_all: bool = False, stored: bool = False,
) -> Mapping[str, Any] | None:
    """A copy of the document whose operations carry the live card and `card_source` / `card_changed_at`.
    `stored` — an issued card: the cards stay as stored (a template change is a revision, not a silent refill), the
    labels come from the stored stamps (`edited`, `template_id`, `build_config`), the template's date is not read.
    `refill_all` — fill every template card anew even when the template did not change (tests: the builder's
    filling reproduces the stored copy)."""
    operations = _operations(document)
    if not operations:
        return document
    manual = set(manual_keys)
    by_id = {card.source_id: card for card in templates}
    seen = seen_versions(document, templates, built_at)
    config = document.get("build_config")
    stamped = (document.get("source") or {}).get("templates") if isinstance(document.get("source"), Mapping) else None
    result: list[Any] = []
    refill: dict[int, OperationCardTemplate] = {}
    for index, op in enumerate(operations):
        if not isinstance(op, Mapping):
            result.append(op)
            continue
        view = dict(op)
        if op.get("edited") or _key(op) in manual:
            view.update(card_source=EDITED, card_changed_at=_edited_at(op))
            result.append(view)
            continue
        template_id = str(op.get("template_id") or "").strip()
        if not template_id:
            result.append(view)  # an own operation of «Ручная сборка»: its card is its own, no template to follow
            continue
        if stored:
            # the stamp of the build (`source.templates`): the card was filled from its template; else snapshot
            from_template = isinstance(config, Mapping) and (
                template_id in stamped if isinstance(stamped, Mapping) else template_for(op, by_id, templates) is not None)
            view.update(card_source=TEMPLATE if from_template else SNAPSHOT, card_changed_at="")
            result.append(view)
            continue
        template = template_for(op, by_id, templates)
        if template is None or not isinstance(config, Mapping):
            view.update(card_source=SNAPSHOT, card_changed_at="")
        else:
            view.update(card_source=TEMPLATE,
                        card_changed_at=template.changed_at.isoformat() if template.changed_at else "")
            if refill_all or seen.get(template_id) != template_version(template):
                refill[index] = template
        result.append(view)
    if refill:
        rebuilt = _rebuilt_cards(document, templates, refill, result)
        for index in refill:
            card = rebuilt.get(index)
            if card is None:
                result[index].update(card_source=SNAPSHOT, card_changed_at="")
            else:
                result[index]["card"] = card
    if isinstance(config, Mapping):
        result = [with_coax_connectors(op, config) if isinstance(op, Mapping) else op for op in result]
    return {**document, "operations": result}


def _rebuilt_cards(
    document: Mapping[str, Any], templates: Sequence[OperationCardTemplate],
    refill: Mapping[int, OperationCardTemplate], operations: Sequence[Mapping[str, Any]],
) -> dict[int, dict[str, Any]]:
    """The cards of the `refill` operations filled by the builder from the document's build config and the current
    templates; an operation the build no longer gives the same way (key, template) — none."""
    cards = list(templates)
    known = {card.source_id for card in cards}
    for index, template in refill.items():
        template_id = str(operations[index].get("template_id") or "").strip()
        if template_id and template_id not in known:
            cards.append(replace(template, source_id=template_id))
            known.add(template_id)
    source = document.get("source") if isinstance(document.get("source"), Mapping) else {}
    try:
        built = build_etk(document["build_config"], cards,
                          operation_library_commit=str(source.get("operation_library_commit") or ""))
    except Exception:  # noqa: BLE001 — a config the builder no longer takes keeps the stored copies
        return {}
    by_key = {_key(op): op for op in _operations(built) if isinstance(op, Mapping)}
    found: dict[int, dict[str, Any]] = {}
    for index in refill:
        op = operations[index]
        twin = by_key.get(_key(op))
        if (twin is not None and isinstance(twin.get("card"), Mapping)
                and str(twin.get("template_id") or "") == str(op.get("template_id") or "")):
            found[index] = deepcopy(dict(twin["card"]))
    return found


def fresh_card(
    document: Mapping[str, Any] | None, key: str, templates: Sequence[OperationCardTemplate],
) -> Mapping[str, Any] | None:
    """The card of one operation filled from its current template now — a hand edit of the operation set aside. None
    when the builder cannot fill it (no template, no build config)."""
    operations = [
        {name: value for name, value in op.items() if name != "edited"} if isinstance(op, Mapping) and _key(op) == key
        else op for op in _operations(document)
    ]
    if not operations:
        return None
    live = live_document({**document, "operations": operations}, manual_keys=(), templates=templates, built_at=None,
                         refill_all=True)
    for op in _operations(live):
        if isinstance(op, Mapping) and _key(op) == key and op.get("card_source") == TEMPLATE:
            card = op.get("card")
            return card if isinstance(card, Mapping) else None
    return None


# ── «Сохранить также в шаблон»: the edited ETK card back into its template ──

def _same(left: object, right: object) -> bool:
    return str(left or "").strip() == str(right or "").strip()


def _tool_key(tool: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(tool.get(field) or "").strip() for field in (
        "section", "name", "inventory_number", "program", "parameters", "norm", "unit"))


def template_record(
    template: OperationCardTemplate, filled: Mapping[str, Any] | None, edited: Mapping[str, Any],
) -> dict[str, Any]:
    """The template record to save from the card edited in an ETK. What the technologist left as it was shown goes
    back as the template has it (with its placeholders «{{…}}», no crimp height or applicator of this product);
    what he changed is written as he typed it. The template's name stays."""
    filled = filled if isinstance(filled, Mapping) else {}
    safety = template.safety if _same(edited.get("safety"), filled.get("safety")) else edited.get("safety")
    defects = str(edited.get("defects") or "")
    filled_defects = str(filled.get("defects") or "")
    own = template.defects.rstrip()
    if _same(defects, filled_defects):
        defects = template.defects
    elif filled_defects.startswith(own) and len(filled_defects) > len(own):
        # The defects other cards add to a combined one are added again on every build — not stored in the template.
        added = filled_defects[len(own):]
        if defects.endswith(added):
            defects = defects[: len(defects) - len(added)]
    filled_steps = [step for step in filled.get("steps") or [] if isinstance(step, Mapping)]
    mapped = len(filled_steps) == len(template.steps)
    steps = []
    used: set[int] = set()
    for step in edited.get("steps") or []:
        if not isinstance(step, Mapping):
            continue
        twin = next((index for index, item in enumerate(filled_steps)
                     if mapped and index not in used and _same(item.get("text"), step.get("text"))), None)
        if twin is None:
            steps.append({"kind": step.get("kind"), "text": step.get("text"), "tolerance": step.get("tolerance")})
            continue
        used.add(twin)
        source = template.steps[twin]
        tolerance = source.tolerance if _same(step.get("tolerance"), filled_steps[twin].get("tolerance")) else step.get("tolerance")
        steps.append({"kind": step.get("kind"), "text": source.text, "tolerance": tolerance})
    filled_tooling = [tool for tool in filled.get("tooling") or [] if isinstance(tool, Mapping)]
    edited_tooling = [tool for tool in edited.get("tooling") or [] if isinstance(tool, Mapping)]
    if [_tool_key(tool) for tool in edited_tooling] == [_tool_key(tool) for tool in filled_tooling]:
        tooling = [tool.__dict__ for tool in template.tooling]
    else:
        tooling = [dict(tool) for tool in edited_tooling]
    return {
        "name": template.name, "safety": safety, "defects": defects, "revision": template.revision,
        "steps": steps, "tooling": tooling,
    }
