import { useCallback, useEffect, useState } from "react";
import { failureText } from "./api";
import { formatQty } from "./format";
import { urlParam } from "./navigation";
import { ChainDetail } from "./PurchasingDocChain";
import {
  CHAIN_STATE_LABELS, CHAIN_STATE_TONE, INVOICE_STATUS_LABELS, INVOICE_STATUS_TONE, dayText, documentsApi, invoiceFileUrl, rub,
  type InvoiceCard, type InvoiceFile, type InvoiceLine, type InvoiceList, type InvoiceQuery, type InvoiceRow, type InvoiceStatus,
} from "./purchasingDocumentsApi";
import { StatusChip } from "./StatusChip";
import "./purchasingDocs.css";

/** «Счета»: the invoice registry mirrored from Kaiten — every card with its PDF and our
 *  payment orders, seller and total read from the PDF, the payment from the order's file name, and the closing
 *  documents from the same computation as «Цепочка документов». Read-only towards Kaiten: the importer fills it. */

const STATUSES: { key: InvoiceStatus | ""; label: string }[] = [
  { key: "", label: "Все" }, { key: "to_pay", label: INVOICE_STATUS_LABELS.to_pay }, { key: "paid", label: INVOICE_STATUS_LABELS.paid },
  { key: "return", label: INVOICE_STATUS_LABELS.return }, { key: "other", label: INVOICE_STATUS_LABELS.other },
];
const FILE_KIND: Record<InvoiceFile["kind"], string> = { invoice: "счёт", payment: "платёжка", other: "файл" };

/** `&tab=invoices&status=to_pay` (a link from «Сегодня»): the registry opens on that status. */
function statusFromUrl(): InvoiceStatus | "" {
  if (urlParam("tab") !== "invoices") return "";
  const wanted = urlParam("status");
  return STATUSES.find((status) => status.key && status.key === wanted)?.key ?? "";
}

export function PurchasingInvoices({ readOnly = false, onOpenUpd }: { readOnly?: boolean; onOpenUpd?: (id: string) => void } = {}) {
  const [query, setQuery] = useState<InvoiceQuery>(() => ({ q: "", status: statusFromUrl(), closing: "", from: "", to: "", page: 1 }));
  const [list, setList] = useState<InvoiceList | null>(null);
  const [error, setError] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const set = (patch: Partial<InvoiceQuery>) => setQuery((current) => ({ ...current, page: 1, ...patch }));

  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      documentsApi.invoices(query)
        .then((next) => { if (live) { setList(next); setError(""); } })
        .catch((reason) => { if (live) setError(failureText(reason, "Счета не загрузились")); });
    }, query.q ? 250 : 0);
    return () => { live = false; clearTimeout(timer); };
  }, [query, reload]);

  const total = list ? Object.values(list.counts).reduce((sum, n) => sum + n, 0) : 0;
  const pages = list ? Math.max(1, Math.ceil(list.total / list.page_size)) : 1;
  const withIssues = list ? list.items.filter((row) => row.parse_issues).length : 0;
  return (
    <section className="panel table-panel">
      <div className="panel-heading">
        <h2>Счета</h2>
        <span>{list ? `${list.total} · на ${rub(list.sum)}` : "счета из Kaiten с платёжками и закрывающими документами"}</span>
      </div>
      <div className="inline-action purchasing-toolbar">
        <input type="search" aria-label="Поиск по счетам" placeholder="Номер, поставщик, ИНН, № платёжки" value={query.q} onChange={(event) => set({ q: event.target.value })} />
        <label className="doc-inline-label">с <input type="date" aria-label="Дата с" value={query.from} onChange={(event) => set({ from: event.target.value })} /></label>
        <label className="doc-inline-label">по <input type="date" aria-label="Дата по" value={query.to} onChange={(event) => set({ to: event.target.value })} /></label>
        <label><input type="checkbox" checked={query.closing === "none"} onChange={(event) => set({ closing: event.target.checked ? "none" : "" })} /> Нет закрывающих</label>
      </div>
      <div className="calendar-chips" role="group" aria-label="Статус счёта">
        {STATUSES.map((s) => (
          <button key={s.key || "all"} type="button" className={`secondary small${query.status === s.key ? " active" : ""}`} aria-pressed={query.status === s.key} onClick={() => set({ status: s.key })}>
            {s.label} {list ? (s.key ? list.counts[s.key] ?? 0 : total) : ""}
          </button>
        ))}
        {withIssues > 0 && <span className="muted doc-issues-count">с замечаниями разбора: {withIssues}</span>}
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!error && !list && <div className="empty">Загружаю…</div>}
      {list && list.items.length === 0 && (
        <div className="empty">{query.q || query.status || query.closing || query.from || query.to ? "Ничего не найдено." : "Счетов ещё нет: их загружает перенос из Kaiten."}</div>
      )}
      {list && list.items.length > 0 && (
        <ul className="pdoc-grid" aria-label="Счета">
          {list.items.map((row) => <li key={row.id}><InvoiceTile row={row} onOpen={() => setOpenId(row.id)} /></li>)}
        </ul>
      )}
      {list && list.total > list.page_size && (
        <div className="inline-action">
          <button type="button" className="secondary small" disabled={query.page <= 1} onClick={() => setQuery({ ...query, page: query.page - 1 })}>Назад</button>
          <span>Страница {query.page} из {pages}</span>
          <button type="button" className="secondary small" disabled={query.page >= pages} onClick={() => setQuery({ ...query, page: query.page + 1 })}>Вперёд</button>
        </div>
      )}
      {openId && (
        <InvoiceCardDialog id={openId} readOnly={readOnly} onOpenUpd={onOpenUpd} onClose={() => setOpenId(null)} onChanged={() => setReload((n) => n + 1)} />
      )}
    </section>
  );
}

function PaymentCell({ row }: { row: InvoiceRow }) {
  if (!row.payment_date) return <span className="muted">—</span>;
  return (
    <>
      {dayText(row.payment_date)} · {rub(row.payment_sum)}{" "}
      {row.payment_matches === true && <span title="Сумма платёжки равна сумме счёта" aria-label="сумма платёжки равна сумме счёта">✓</span>}
      {row.payment_matches === false && <span className="danger-text" title="Сумма платёжки не равна сумме счёта">≠ сумме счёта</span>}
    </>
  );
}

function InvoiceTile({ row, onOpen }: { row: InvoiceRow; onOpen: () => void }) {
  return (
    <article className="pdoc-card clickable" onClick={onOpen}>
      <div className="pdoc-top">
        <span className="pdoc-title">
          <button type="button" className="link-button table-link" onClick={(event) => { event.stopPropagation(); onOpen(); }}>
            {row.number ? <span className="doc-number">{row.number}</span> : row.title}
          </button>
        </span>
        <span className="pdoc-chips"><StatusChip tone={INVOICE_STATUS_TONE[row.status]}>{INVOICE_STATUS_LABELS[row.status]}</StatusChip></span>
      </div>
      <div className="pdoc-meta">
        <span>{dayText(row.date ?? row.card_created?.slice(0, 10) ?? null)}</span>
        <span>{row.supplier_name ?? row.seller_name ?? <span className="muted">—</span>}{row.seller_inn && <span className="muted"> · {row.seller_inn}</span>}</span>
        <span className="pdoc-sum">{rub(row.total)}</span>
      </div>
      <div className="pdoc-sub">
        <span>Оплата: <PaymentCell row={row} /></span>
        {row.line_count > 0 && <span>позиций: {row.line_count}</span>}
      </div>
      {row.chain && (
        <div className="pdoc-sub" title={row.chain.checks.map((c) => c.text).join("\n")}>
          <span>Закрывающие:</span>
          <StatusChip tone={CHAIN_STATE_TONE[row.chain.state]}>{CHAIN_STATE_LABELS[row.chain.state]}</StatusChip>
          {row.chain.upds > 0 && <span>{row.chain.upds} док. · {rub(row.chain.closed_gross)}</span>}
        </div>
      )}
      {row.parse_issues && <div className="pdoc-issues">{row.parse_issues}</div>}
      {row.files.length > 0 && (
        <div className="pdoc-files doc-files" onClick={(event) => event.stopPropagation()}>
          {row.files.map((file) => <FileLink key={file.id} invoiceId={row.id} file={file} short />)}
        </div>
      )}
    </article>
  );
}

function FileLink({ invoiceId, file, short = false }: { invoiceId: string; file: InvoiceFile; short?: boolean }) {
  const label = short ? FILE_KIND[file.kind] : file.name;
  if (!file.downloaded) return <span className="muted" title={`${file.name} — не скачан из Kaiten`}>{label}</span>;
  return <a href={invoiceFileUrl(invoiceId, file.id)} target="_blank" rel="noreferrer" title={file.name}>{label}</a>;
}

export function InvoiceCardDialog({ id, readOnly, onClose, onChanged, onOpenUpd }: {
  id: string; readOnly: boolean; onClose: () => void; onChanged: () => void; onOpenUpd?: (id: string) => void;
}) {
  const [card, setCard] = useState<InvoiceCard | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      setCard(await documentsApi.invoice(id));
      setError("");
    } catch (reason) {
      setError(failureText(reason, "Карточка счёта не загрузилась"));
    }
  }, [id]);
  useEffect(() => { void load(); }, [load]);

  return (
    <div className="dialog-overlay" role="presentation">
      <div className="panel move-dialog wide" role="dialog" aria-label="Карточка счёта">
        <h2>{card ? `Счёт ${card.number ? `№ ${card.number}` : card.title}${card.date ? ` от ${dayText(card.date)}` : ""}` : "Карточка счёта"}</h2>
        {error && <div className="error-banner" role="alert">{error}</div>}
        {!card && !error && <div className="empty">Загружаю…</div>}
        {card && (
          <>
            <dl className="doc-header">
              <dt>Поставщик</dt><dd>{card.supplier_name ?? card.seller_name ?? "продавец не прочитан"}{card.seller_inn ? ` · ИНН ${card.seller_inn}` : ""}</dd>
              <dt>Сумма</dt><dd>{rub(card.total)}{card.vat_free ? " · без НДС" : card.vat !== null ? ` · НДС ${rub(card.vat)}` : ""}</dd>
              <dt>Статус</dt><dd><StatusChip tone={INVOICE_STATUS_TONE[card.status]}>{INVOICE_STATUS_LABELS[card.status]}</StatusChip>{card.column_title ? <span className="muted"> · колонка Kaiten «{card.column_title}»</span> : null}</dd>
              <dt>{card.source === "procurement" ? "Загружен в закупке" : "Карточка Kaiten"}</dt><dd>{card.title}</dd>
            </dl>
            {card.parse_issues && <p className="doc-check warning">Замечания разбора: {card.parse_issues}</p>}

            <h3>Позиции</h3>
            {card.lines.length === 0 ? <p className="muted">Позиции из PDF не прочитаны — откройте счёт.</p> : <PositionsTable lines={card.lines} />}
            {card.lines.length > 0 && <p className="muted">Прочитано из таблицы PDF. Цены — как напечатал поставщик: с НДС или без.</p>}

            <h3>Платёжные поручения</h3>
            {card.payments.length === 0 ? <p className="muted">Платёжек в карточке нет.</p> : (
              <ul className="doc-list" aria-label="Платёжные поручения">
                {card.payments.map((p) => <li key={`${p.number}|${p.date}|${p.sum}`}>№ {p.number} от {dayText(p.date)} · {rub(p.sum)}</li>)}
              </ul>
            )}
            {card.payment_matches === false && <p className="doc-check warning">Оплачено {rub(card.payment_sum)}, счёт на {rub(card.total)}</p>}

            <h3>Файлы</h3>
            {card.files.length === 0 ? <p className="muted">Файлов в карточке нет.</p> : (
              <ul className="doc-list" aria-label="Файлы счёта">
                {card.files.map((file) => (
                  <li key={file.id}>
                    {file.name} <span className="muted">· {FILE_KIND[file.kind]}</span>{" "}
                    {file.downloaded
                      ? <a className="secondary small doc-open" href={invoiceFileUrl(card.id, file.id)} target="_blank" rel="noreferrer">Открыть</a>
                      : <span className="muted">не скачан из Kaiten</span>}
                  </li>
                ))}
              </ul>
            )}

            <h3>Цепочка документов</h3>
            {card.chain
              ? <ChainDetail chain={card.chain} readOnly={readOnly} receivingAvailable={card.receiving_available} showReceivingLines onOpenUpd={onOpenUpd} onChanged={() => { onChanged(); void load(); }} />
              : <p className="muted">Цепочка не построена.</p>}
            <div className="move-actions"><button type="button" className="secondary" onClick={onClose}>Закрыть</button></div>
          </>
        )}
        {!card && <div className="move-actions"><button type="button" className="secondary" onClick={onClose}>Закрыть</button></div>}
      </div>
    </div>
  );
}

/** Positions of a document as read from an invoice PDF or a UPD. */
export function PositionsTable({ lines, priceTitle = "Цена" }: { lines: InvoiceLine[]; priceTitle?: string }) {
  const withArticle = lines.some((line) => line.article);
  return (
    <div className="table-wrap doc-positions">
      <table className="grid" aria-label="Позиции">
        <thead>
          <tr>
            <th className="num">№</th><th>Наименование</th>{withArticle && <th>Код / артикул</th>}
            <th className="num">Кол-во</th><th>Ед.</th><th className="num">{priceTitle}</th><th className="num">Сумма</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line, index) => (
            <tr key={`${line.seq}-${index}`}>
              <td className="num muted">{line.seq}</td>
              <td className="text">{line.name}</td>
              {withArticle && <td>{line.article}</td>}
              <td className="num">{formatQty(line.qty) || "—"}</td>
              <td>{line.unit}</td>
              <td className="num">{rub(line.price)}</td>
              <td className="num">{rub(line.sum)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
