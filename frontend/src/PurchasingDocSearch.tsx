import { useEffect, useState } from "react";
import { failureText } from "./api";
import { urlParam } from "./navigation";
import { InvoiceCardDialog, PositionsTable } from "./PurchasingInvoices";
import { ORIGIN_LABELS, dayText, documentsApi, invoiceFileUrl, rub, type SearchHit, type SearchKind, type SearchResult } from "./purchasingDocumentsApi";
import { StatusChip } from "./StatusChip";

/** «Поиск по документам» (option documents.chain): an article, a piece of a name, a number, a seller or an amount →
 *  every UPD and Kaiten invoice that has it. Separators, case, «ё» and Latin look-alikes do not matter. */

const KINDS: { key: SearchKind; label: string }[] = [
  { key: "all", label: "Все документы" }, { key: "upd", label: "УПД и акты" }, { key: "invoice", label: "Счета из Kaiten" },
];
const MATCHED: Record<SearchHit["matched"], string> = {
  lines: "", header: "совпали номер, продавец или ИНН", amount: "совпала сумма", text: "нашлось в тексте счёта", title: "нашлось в названии карточки Kaiten",
};

export function PurchasingDocSearch({ readOnly = false, onOpenUpd }: { readOnly?: boolean; onOpenUpd?: (id: string) => void } = {}) {
  const [q, setQ] = useState(() => urlParam("q"));
  const [kind, setKind] = useState<SearchKind>("all");
  const [result, setResult] = useState<SearchResult | null>(null);
  const [error, setError] = useState("");
  const [invoiceId, setInvoiceId] = useState<string | null>(null);
  const ready = q.trim().length >= 2;

  useEffect(() => {
    if (!ready) { setResult(null); setError(""); return; }
    let live = true;
    const timer = setTimeout(() => {
      documentsApi.search(q, kind)
        .then((next) => { if (live) { setResult(next); setError(""); } })
        .catch((reason) => { if (live) setError(failureText(reason, "Поиск не удался")); });
    }, 250);
    return () => { live = false; clearTimeout(timer); };
  }, [q, kind, ready]);

  return (
    <section className="panel table-panel">
      <div className="panel-heading">
        <h2>Поиск по документам</h2>
        <span>{result ? `${result.found} док.${result.found > result.hits.length ? ` · показаны ${result.hits.length} самых новых` : ""}` : "УПД и счета: позиция, номер, продавец, сумма"}</span>
      </div>
      <div className="inline-action purchasing-toolbar">
        <input type="search" aria-label="Что найти в документах" autoFocus placeholder="Артикул или часть наименования: RG-316, ТУТнг 8/4, SMA-J; номер; ИНН; сумма" value={q} onChange={(event) => setQ(event.target.value)} />
        <div className="calendar-chips doc-filter" role="group" aria-label="Какие документы">
          {KINDS.map((k) => (
            <button key={k.key} type="button" className={`secondary small${kind === k.key ? " active" : ""}`} aria-pressed={kind === k.key} onClick={() => setKind(k.key)}>{k.label}</button>
          ))}
        </div>
      </div>
      <p className="page-sub">
        Ищет по позициям УПД (действующая версия, без дублей) и счетов из Kaiten, по номеру, продавцу, ИНН и сумме. Регистр, «ё», пробелы,
        дефисы, точки и латинские буквы-двойники не важны; нужно, чтобы нашлись все слова запроса.
      </p>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!ready && <div className="empty">Введите хотя бы два знака: артикул, наименование, номер или сумму.</div>}
      {ready && result && result.hits.length === 0 && <div className="empty">Ничего не найдено. Попробуйте часть артикула или одно слово наименования.</div>}
      <div className="doc-hits">
        {ready && result?.hits.map((hit) => (
          <HitCard key={`${hit.kind}-${hit.id}`} hit={hit} onOpenUpd={onOpenUpd} onOpenInvoice={setInvoiceId} />
        ))}
      </div>
      {invoiceId && <InvoiceCardDialog id={invoiceId} readOnly={readOnly} onOpenUpd={onOpenUpd} onClose={() => setInvoiceId(null)} onChanged={() => undefined} />}
    </section>
  );
}

function HitCard({ hit, onOpenUpd, onOpenInvoice }: { hit: SearchHit; onOpenUpd?: (id: string) => void; onOpenInvoice: (id: string) => void }) {
  const title = `${hit.doc_kind}${hit.number ? ` № ${hit.number}` : ""} от ${dayText(hit.date)}`;
  const open = hit.kind === "invoice" ? () => onOpenInvoice(hit.id) : onOpenUpd ? () => onOpenUpd(hit.id) : undefined;
  return (
    <article className="doc-hit" aria-label={title}>
      <div className="doc-hit-head">
        {open ? <button type="button" className="link-button doc-link" onClick={open}>{title}</button> : <strong>{title}</strong>}
        {hit.origin && <StatusChip tone={hit.origin === "edo" ? "info" : "neutral"}>{ORIGIN_LABELS[hit.origin]}</StatusChip>}
        <span>{hit.seller_name ?? <span className="muted">поставщик не прочитан</span>}</span>
        {hit.seller_inn && <span className="muted">{hit.seller_inn}</span>}
        {hit.kind === "invoice" && hit.file_id && <a href={invoiceFileUrl(hit.id, hit.file_id)} target="_blank" rel="noreferrer">PDF счёта</a>}
        <span className="doc-hit-sum">{rub(hit.total)}</span>
      </div>
      {hit.kind === "invoice" && hit.title && <div className="muted">Карточка Kaiten: {hit.title}</div>}
      {MATCHED[hit.matched] && <div className="muted">{MATCHED[hit.matched]}</div>}
      {hit.lines.length > 0 && <PositionsTable lines={hit.lines} priceTitle={hit.kind === "upd" ? "Цена с НДС" : "Цена"} />}
      {hit.lines.length === 0 && hit.snippet && hit.matched === "text" && <p className="doc-hit-snippet">{hit.snippet}</p>}
    </article>
  );
}
