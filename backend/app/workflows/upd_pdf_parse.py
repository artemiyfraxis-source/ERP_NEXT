"""The registry's PDF parser at work: a queued PDF read by the ERP's document reader, the answer written into the УПД
registry.

The reader is the one the warehouse reads its scans with (`app.shared.doc_reader`, Витя 26.10.2026: one parser for the
whole ERP): an electronic PDF (a real text layer) in ONE text-only call, a scan page by page in parallel (upright
grayscale pictures, the numbering-gap reread), the pages merged and only then checked (`closing.closing_doc`). The
model settings are the warehouse's «Распознавание УПД» (env `ERP_UPD_LLM_*` over the saved settings: the same key,
model and fallback model) — this port is the one place purchasing reaches them. Purchasing owns the writes
(`purchasing.upd_pdf`) and the registry's columns (`purchasing.upd_parse`).

    ERP_DATABASE_URL=… python -m app.workflows.upd_pdf_parse --benchmark [--limit N]   # writes nothing
    ERP_DATABASE_URL=… python -m app.workflows.upd_pdf_parse --benchmark-scans N [--budget S]   # writes nothing
    ERP_DATABASE_URL=… python -m app.workflows.upd_pdf_parse --queued [--apply]
    ERP_DATABASE_URL=… python -m app.workflows.upd_pdf_parse --ids <id,id> | --all [--limit N] [--apply]

`--benchmark` reads every PDF whose document also came as an EDO XML and counts, field by field, where the stored
reading (Roller_Buyer's Google parser) and the new one differ from the XML. `--benchmark-scans N` re-reads the last N
warehouse scans (`warehouse_receipt_files` with a stored reading) as «Отсканировать УПД» reads them and prints, per
file, what differs from the stored reading (`warehouse.scan_benchmark`). Without `--apply` the others read and print,
writing nothing; with it the readings replace the stored ones. The read-only modes run in a read-only session (on
PostgreSQL a READ ONLY transaction; any flush or commit refused; rolled back). Every reading is a paid model call."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.modules.purchasing import upd_parse, upd_pdf
from app.modules.purchasing.infrastructure import DocumentFileRow, UpdDocumentRow, UpdLineRow
from app.modules.warehouse import scan_benchmark
from app.modules.warehouse.reference_fields import item_types
from app.modules.warehouse.scan_recognition import llm_config as warehouse_llm_config
from app.shared.clock import moscow_now
from app.shared.doc_reader import pages as doc_pages
from app.shared.doc_reader import reader as doc_reader
from app.shared.doc_reader.transport import LlmConfig, LlmError, llm_config as config_of
from app.shared.identity import Actor

log = logging.getLogger(__name__)

CALL_TIMEOUT = 300.0  # one text call reads the whole document; a background task, no proxy minute to keep
BUDGET = 900.0  # seconds for one document, all its pages
NO_KEY = "не задан ключ модели: «Склад» → «Распознавание УПД» (или ERP_UPD_LLM_API_KEY)"
NO_PAGES = "нет ни изображений страниц (pdftoppm), ни текстового слоя — разобрать нечем"
PARSER_ACTOR = Actor(id=UUID(int=0), name="Разборщик УПД", department="Система")


class ParseFailed(RuntimeError):
    pass


def llm_config(warehouse: Callable | None) -> LlmConfig:
    """The warehouse's model settings; no warehouse module on this server — the env alone."""
    return warehouse_llm_config(warehouse) if warehouse is not None else config_of({})


def text_layer(content: bytes) -> str:
    """The PDF's own text when it is a real one (an electronic document, not a scan); '' otherwise."""
    return doc_reader.text_layer(content)


def page_pictures(content: bytes) -> list[bytes]:
    """The pages as the model reads them best (upright grayscale JPEGs); [] — no pdftoppm on this server."""
    return doc_pages.page_pictures(content, doc_pages.PDF)


def read_pdf(content: bytes, file_name: str, tag: str, config: LlmConfig) -> tuple[upd_parse.ParsedDoc, str]:
    """One PDF → (the normalized document, the model(s) that read it). A page no model read is named in the remarks;
    nothing read at all — ParseFailed with the models' answers."""
    if not config.ready:
        raise ParseFailed(NO_KEY)
    reading = doc_reader.read_closing(config, text=text_layer(content), pictures=lambda: page_pictures(content),
                                      tag=tag, budget=BUDGET, call_timeout=CALL_TIMEOUT)
    if not reading.pages:
        raise ParseFailed(NO_PAGES)
    if not reading.read:
        raise ParseFailed("; ".join(reading.errors) or "модель не ответила")
    return reading.doc(tag, file_name), reading.model


Reader = Callable[[bytes, str, str, LlmConfig], tuple[upd_parse.ParsedDoc, str]]


def parse_document(purchasing: Callable, document_id: str, config: LlmConfig, actor: Actor = PARSER_ACTOR,
                   now: Callable[[], datetime] = moscow_now, reader: Reader | None = None) -> str:
    """Claim → read → write. Returns the verdict, «skipped» (not queued / taken by another process) or «error»."""
    uow = purchasing()
    with uow:
        claimed = upd_pdf.claim(uow, document_id, now())
        uow.commit()
    if claimed is None:
        return "skipped"
    try:
        doc, model = (reader or read_pdf)(claimed.content, claimed.file_name, claimed.tag, config)
    except Exception as error:  # noqa: BLE001 — the document must leave «Разбирается» whatever happened
        log.exception("Разбор PDF %s не удался", document_id)
        message = str(error) if isinstance(error, (ParseFailed, LlmError)) else "внутренняя ошибка разбора"
        uow = purchasing()
        with uow:
            upd_pdf.store_failure(uow, document_id, message, now())
            uow.commit()
        return "error"
    uow = purchasing()
    with uow:
        verdict = upd_pdf.store_parse(uow, document_id, doc, model, actor, now())
        uow.commit()
    return verdict or "skipped"


def parse_documents(purchasing: Callable, warehouse: Callable | None, document_ids: list[str],
                    actor: Actor = PARSER_ACTOR) -> Counter:
    """The background task after an upload or «Разобрать заново»: the documents one after another."""
    results: Counter = Counter()
    if not document_ids:
        return results
    try:
        config = llm_config(warehouse)
    except Exception:  # noqa: BLE001 — settings unreadable: every document says so instead of hanging queued
        log.exception("Настройки распознавания УПД не прочитаны")
        config = LlmConfig()
    for document_id in document_ids:
        results[parse_document(purchasing, document_id, config, actor)] += 1
    return results


# ---- command line -------------------------------------------------------------------------------------------------

class ReadOnly(RuntimeError):
    pass


def _refuse(*_args) -> None:
    raise ReadOnly("пробный прогон: запись в базу запрещена")


@contextmanager
def read_only(sessions) -> Iterator[Session]:
    """A session that cannot write: a READ ONLY transaction on PostgreSQL, every flush and commit refused, rolled back
    and closed at the end."""
    session = sessions()
    event.listen(session, "before_flush", _refuse)
    event.listen(session, "before_commit", _refuse)
    try:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(text("SET TRANSACTION READ ONLY"))
        yield session
    finally:
        session.rollback()
        session.close()


_FOLD = str.maketrans({"ё": "е", "Ё": "Е"})


def _fold(text) -> str:
    return " ".join(str(text or "").translate(_FOLD).split()).casefold()


def _numbers(text) -> frozenset[str]:
    """«№ 586 от 24.06.2026, № 590 от …» → {'586', '590'}: the payment numbers without their wording and dates."""
    return frozenset(re.findall(r"№\s*([\w/-]+)", str(text or ""))) or frozenset(
        part.split(" от ")[0].strip() for part in str(text or "").split(",") if part.strip())


HEADER_FIELDS = ("number", "doc_date", "seller_inn", "seller_kpp", "total_net", "total_vat", "total_gross",
                 "payment_doc", "line_count")


def compare(reading: dict, exact: dict) -> Counter:
    """Field-by-field differences of a reading from the exact (EDO XML) document; lines are compared in order."""
    wrong: Counter = Counter()
    for name in HEADER_FIELDS:
        a, b = reading["header"].get(name), exact["header"].get(name)
        same = _numbers(a) == _numbers(b) if name == "payment_doc" else (_fold(a) == _fold(b) if name == "number"
                                                                          else a == b)
        wrong[name] += 0 if same else 1
    for mine, theirs in zip(reading["lines"], exact["lines"]):
        wrong["строк сверено"] += 1
        wrong["name"] += _fold(mine["name"]) != _fold(theirs["name"])
        for name in ("quantity", "price", "gross"):
            a, b = mine.get(name), theirs.get(name)
            wrong[name] += not (a == b or (a is not None and b is not None and abs(float(a) - float(b)) < 0.005))
    return wrong


def _document_facts(session, document_id: str) -> dict:
    row = session.get(UpdDocumentRow, document_id)
    lines = session.scalars(select(UpdLineRow).where(UpdLineRow.document_id == document_id, UpdLineRow.source == "google")
                            .order_by(UpdLineRow.seq)).all()
    return {"header": {name: getattr(row, name) for name in HEADER_FIELDS},
            "lines": [{"name": l.name, "quantity": l.quantity, "price": l.price, "gross": l.gross} for l in lines]}


def _parsed_facts(doc: upd_parse.ParsedDoc) -> dict:
    header = upd_parse.header_values(doc)
    return {"header": {name: header.get(name) for name in HEADER_FIELDS},
            "lines": [{"name": v["name"], "quantity": v["quantity"], "price": v["price"], "gross": v["gross"]}
                      for v in map(upd_parse.line_values, doc.lines)]}


def _file(session, document_id: str):
    row = session.get(UpdDocumentRow, document_id)
    content = session.get(DocumentFileRow, row.file_id).content if row is not None and row.file_id else None
    return row, content


def benchmark(sessions, config: LlmConfig, limit: int | None, out=None) -> dict:
    """Every PDF with an EDO twin, read anew; errors of the stored and of the new reading against the XML."""
    out = out or sys.stdout
    with read_only(sessions) as session:
        docs = {row.id: row for row in session.scalars(select(UpdDocumentRow))}
        pairs = []
        for row in docs.values():
            other = docs.get(row.twin_of) if row.twin_of else None
            if other is None or {row.origin, other.origin} != {"google", "edo"}:
                continue
            pdf, xml = (row, other) if row.origin == "google" else (other, row)
            if pdf.file_id is not None and (pdf.id, xml.id) not in pairs:
                pairs.append((pdf.id, xml.id))
        pairs = pairs[:limit] if limit else pairs
        stored, fresh, failed = Counter(), Counter(), 0
        for number, (pdf_id, xml_id) in enumerate(pairs, start=1):
            exact = _document_facts(session, xml_id)
            stored += compare(_document_facts(session, pdf_id), exact)
            row, content = _file(session, pdf_id)
            try:
                doc, model = read_pdf(content, row.file_name, upd_pdf.tag_code(row), config)
            except (ParseFailed, LlmError) as error:
                failed += 1
                print(f"{number}/{len(pairs)} {row.file_name}: не прочитан — {error}", file=out)
                continue
            mine = compare(_parsed_facts(doc), exact)
            fresh += mine
            print(f"{number}/{len(pairs)} {row.file_name} ({model}): расхождений {sum(mine.values()) - mine['строк сверено']}",
                  file=out)
    print(f"\nПар PDF ↔ ЭДО: {len(pairs)}, не прочитано заново: {failed}", file=out)
    print(f"{'поле':<14}{'было (сохранённый разбор)':>28}{'стало (' + config.model + ')':>28}", file=out)
    for name in (*HEADER_FIELDS, "name", "quantity", "price", "gross", "строк сверено"):
        print(f"{name:<14}{stored[name]:>28}{fresh[name]:>28}", file=out)
    return {"pairs": len(pairs), "failed": failed, "stored": dict(stored), "fresh": dict(fresh)}


def benchmark_scans(sessions, warehouse: Callable, config: LlmConfig, limit: int, budget: float | None = None,
                    out=None, reader: scan_benchmark.Reader | None = None) -> dict:
    """The last `limit` warehouse scans read anew and compared with their stored readings (read-only)."""
    types = item_types(warehouse())
    with read_only(sessions) as session:  # the files are loaded, the session closed: no transaction held while reading
        scans = scan_benchmark.recent_scans(session, limit)
    return scan_benchmark.run(scans, reader or scan_benchmark.reader_of(config, types), model=config.model,
                              budget=budget, out=out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Разборщик PDF реестра УПД: очередь, повторный разбор, сравнение с ЭДО")
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument("--benchmark", action="store_true", help="сравнить с XML из ЭДО (ничего не пишет)")
    what.add_argument("--benchmark-scans", type=int, metavar="N",
                      help="перечитать N последних сканов склада и сравнить с сохранённым чтением (ничего не пишет)")
    what.add_argument("--queued", action="store_true", help="документы «В очереди»")
    what.add_argument("--all", action="store_true", help="все PDF реестра заново")
    what.add_argument("--ids", help="ключи документов через запятую")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--apply", action="store_true", help="записать (без флага — прочитать и показать)")
    parser.add_argument("--budget", type=float, default=None,
                        help="--benchmark-scans: не начинать новый файл после стольких секунд прогона")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from app.database import build_engine, build_purchasing_uow_factory, build_warehouse_uow_factory

    database_url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not database_url:
        print("Задайте ERP_DATABASE_URL", file=sys.stderr)
        return 2
    engine = build_engine(database_url)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    config = llm_config(build_warehouse_uow_factory(engine))
    if not config.ready:
        print(f"Нельзя: {NO_KEY}", file=sys.stderr)
        return 2
    if args.benchmark:
        benchmark(sessions, config, args.limit)
        return 0
    if args.benchmark_scans:
        benchmark_scans(sessions, build_warehouse_uow_factory(engine), config, args.benchmark_scans, args.budget)
        return 0

    with read_only(sessions) as session:
        if args.ids:
            ids = [part.strip() for part in args.ids.split(",") if part.strip()]
        elif args.queued:
            ids = upd_pdf.queued_ids(session)
        else:
            ids = list(session.scalars(select(UpdDocumentRow.id).where(
                UpdDocumentRow.origin == "google", UpdDocumentRow.file_id.is_not(None)).order_by(UpdDocumentRow.id)))
    ids = ids[:args.limit] if args.limit else ids
    print(f"Документов: {len(ids)}, модель {config.model} (запасная {config.fallback_model or '—'})"
          + ("" if args.apply else " — пробный прогон, ничего не пишется"))
    if not args.apply:
        with read_only(sessions) as session:
            for number, document_id in enumerate(ids, start=1):
                row, content = _file(session, document_id)
                if row is None or content is None:
                    print(f"{number}. {document_id}: нет документа или файла")
                    continue
                try:
                    doc, model = read_pdf(content, row.file_name, upd_pdf.tag_code(row), config)
                except (ParseFailed, LlmError) as error:
                    print(f"{number}. {row.file_name}: не прочитан — {error}")
                    continue
                print(f"{number}. {row.file_name} ({model}): {doc.status}; № {doc.number or '—'} от {doc.doc_date or '—'}, "
                      f"строк {len(doc.lines)} (было {row.line_count}), итог {doc.total_gross} "
                      f"(было {row.total_gross / 100 if row.total_gross is not None else '—'})"
                      + (f"; замечания: {'; '.join(doc.issues)}" if doc.issues else ""))
        return 0
    purchasing = build_purchasing_uow_factory(engine)
    results: Counter = Counter()
    for number, document_id in enumerate(ids, start=1):
        if not args.queued:
            uow = purchasing()
            with uow:
                try:
                    upd_pdf.queue_parse(uow, document_id, PARSER_ACTOR, moscow_now())
                    uow.commit()
                except upd_pdf.UpdError as error:
                    print(f"{number}. {document_id}: {error}")
                    continue
        verdict = parse_document(purchasing, document_id, config)
        results[verdict] += 1
        print(f"{number}/{len(ids)} {document_id}: {verdict}")
    print("Итог: " + ", ".join(f"{name} — {count}" for name, count in results.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
