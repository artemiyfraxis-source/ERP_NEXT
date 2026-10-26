import { Fragment, useEffect, useState } from "react";
import { failureText } from "./api";
import { countLabel, formatQty } from "./format";
import {
  CHAIN_STATES, CHAIN_STATE_LABELS, CHAIN_STATE_TONE, INVOICE_STATUS_LABELS, LINK_METHOD_LABELS, ORIGIN_LABELS,
  dayText, docLabel, documentsApi, invoiceFileUrl, rub,
  type ChainList, type ChainQuery, type ChainUpd, type DocChain, type DocCheck, type ReceivingSummary,
} from "./purchasingDocumentsApi";
import { StatusChip } from "./StatusChip";
import "./purchasingDocs.css";

/** «Цепочка документов»: every Kaiten invoice with our payment orders, the closing documents
 *  found for it (from the UPD's basis and payment references, or the buyer's word), and the ERP receiving behind each
 *  UPD — what disagrees comes first. `onOpenUpd` opens the UPD card of the registry when the stand has it. */

const CHECK_CLASS: Record<DocCheck["level"], string> = { error: "doc-check error", warning: "doc-check warning", info: "doc-check muted" };
const BUYERS: { key: ChainQuery["buyer"]; label: string }[] = [
  { key: "", label: "все" }, { key: "ours", label: "мы" }, { key: "other", label: "другие организации" }, { key: "unknown", label: "не указан" },
];
const CLOSINGS: { key: ChainQuery["closing"]; label: string }[] = [
  { key: "", label: "Любыми" }, { key: "edo", label: "ЭДО" }, { key: "paper", label: "Бумажными" }, { key: "none", label: "Без документов" },
];

export function PurchasingDocChain({ readOnly = false, onOpenUpd }: { readOnly?: boolean; onOpenUpd?: (id: string) => void } = {}) {
  const [query, setQuery] = useState<ChainQuery>({ view: "problems", q: "", closing: "", buyer: "", from: "", to: "", page: 1 });
  const [list, setList] = useState<ChainList | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const set = (patch: Partial<ChainQuery>) => setQuery((current) => ({ ...current, page: 1, ...patch }));

  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      documentsApi.chains(query)
        .then((next) => {
          if (!live) return;
          // «Показать ещё» appends the next page to what is shown.
          setList((current) => (query.page > 1 && current ? { ...next, chains: [...current.chains, ...next.chains] } : next));
          setError("");
        })
        .catch((reason) => { if (live) setError(failureText(reason, "Цепочка документов не загрузилась")); });
    }, query.q ? 250 : 0);
    return () => { live = false; clearTimeout(timer); };
  }, [query, reload]);

  const summary = list?.summary;
  const changed = () => { setQuery((current) => ({ ...current, page: 1 })); setReload((n) => n + 1); };
  return (
    <section className="panel table-panel doc-chain">
      <div className="panel-heading"><h2>Цепочка документов</h2><span>счёт → оплата → закрывающие → приёмка</span></div>
      {summary && (
        <div className="doc-summary" aria-label="Состояния цепочек">
          {CHAIN_STATES.filter((state) => summary.by_state[state] > 0).map((state) => (
            <StatusChip key={state} tone={CHAIN_STATE_TONE[state]}>{CHAIN_STATE_LABELS[state]}: {summary.by_state[state]}</StatusChip>
          ))}
        </div>
      )}
      <div className="inline-action purchasing-toolbar">
        <div className="calendar-chips doc-filter" role="group" aria-label="Что показать">
          <button type="button" className={`secondary small${query.view === "problems" ? " active" : ""}`} aria-pressed={query.view === "problems"} onClick={() => set({ view: "problems" })}>
            Требуют внимания {summary ? summary.problems : ""}
          </button>
          <button type="button" className={`secondary small${query.view === "all" ? " active" : ""}`} aria-pressed={query.view === "all"} onClick={() => set({ view: "all" })}>Все</button>
        </div>
        <input type="search" aria-label="Поиск по цепочкам" placeholder="№ счёта, УПД, платёжки, поставщик, ИНН" value={query.q} onChange={(event) => set({ q: event.target.value })} />
        <label className="doc-inline-label">с <input type="date" aria-label="Дата с" value={query.from} onChange={(event) => set({ from: event.target.value })} /></label>
        <label className="doc-inline-label">по <input type="date" aria-label="Дата по" value={query.to} onChange={(event) => set({ to: event.target.value })} /></label>
      </div>
      <div className="inline-action purchasing-toolbar">
        <div className="calendar-chips doc-filter" role="group" aria-label="Закрыт документами">
          <span className="muted">Закрыт документами:</span>
          {CLOSINGS.map((c) => (
            <button key={c.key || "all"} type="button" className={`secondary small${query.closing === c.key ? " active" : ""}`} aria-pressed={query.closing === c.key} onClick={() => set({ closing: c.key })}>
              {c.label}{c.key && summary ? ` ${summary.by_closing[c.key]}` : ""}
            </button>
          ))}
        </div>
        <label className="doc-inline-label">Покупатель
          <select aria-label="Покупатель" value={query.buyer} onChange={(event) => set({ buyer: event.target.value as ChainQuery["buyer"] })}>
            {BUYERS.map((b) => <option key={b.key || "all"} value={b.key}>{b.label}</option>)}
          </select>
        </label>
      </div>
      {query.closing === "paper" && <p className="page-sub">По таким счетам нужны бумажные оригиналы.</p>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!error && !list && <div className="empty">Сверяю документы…</div>}
      {list && list.chains.length === 0 && (
        <div className="empty">{query.view === "problems" ? "Расхождений нет: все счета закрыты документами или ещё в сроке." : "Ничего не найдено."}</div>
      )}
      {list && list.chains.length > 0 && (
        <ul className="pdoc-grid" aria-label="Цепочки документов">
          {list.chains.map((chain) => (
            <Fragment key={chain.key}>
              <li>
                <ChainTile chain={chain} receivingAvailable={list.receiving_available} open={open === chain.key}
                  onToggle={() => setOpen(open === chain.key ? null : chain.key)} />
              </li>
              {open === chain.key && (
                <li className="pdoc-detail">
                  <ChainDetail chain={chain} readOnly={readOnly} receivingAvailable={list.receiving_available} onChanged={changed} onOpenUpd={onOpenUpd} />
                </li>
              )}
            </Fragment>
          ))}
        </ul>
      )}
      {list && list.chains.length < list.total && (
        <div className="inline-action">
          <button type="button" className="secondary small" onClick={() => setQuery((current) => ({ ...current, page: current.page + 1 }))}>
            Показать ещё ({list.total - list.chains.length})
          </button>
        </div>
      )}
    </section>
  );
}

function ChainTile({ chain, receivingAvailable, open, onToggle }: { chain: DocChain; receivingAvailable: boolean; open: boolean; onToggle: () => void }) {
  return (
    <article className={`pdoc-card clickable${open ? " is-open" : ""}`} onClick={onToggle}>
      <div className="pdoc-top">
        <span className="pdoc-title"><InvoiceCell chain={chain} /></span>
        <span className="pdoc-chips"><StatusChip tone={CHAIN_STATE_TONE[chain.state]}>{CHAIN_STATE_LABELS[chain.state]}</StatusChip></span>
      </div>
      <div className="pdoc-block">
        <span className="pdoc-block-label">Оплата</span>
        {chain.invoice?.payments.length
          ? chain.invoice.payments.map((p) => <div key={`${p.number}|${p.date}`}>№ {p.number} от {dayText(p.date)} · {rub(p.sum)}</div>)
          : <span className="muted">{chain.invoice ? INVOICE_STATUS_LABELS[chain.invoice.status] : "—"}</span>}
      </div>
      <div className="pdoc-block">
        <span className="pdoc-block-label">Закрывающие</span>
        {chain.upds.length ? (
          <>
            <div>{chain.upds.length === 1 ? docLabel(chain.upds[0]) : `${chain.upds.length} док.`} · {rub(chain.closed_gross)}</div>
            <div className="doc-origins">
              {(["edo", "google", "scan"] as const).filter((o) => chain.upds.some((u) => u.origin === o)).map((o) => (
                <StatusChip key={o} tone={o === "edo" ? "info" : "neutral"}>{ORIGIN_LABELS[o]}</StatusChip>
              ))}
            </div>
          </>
        ) : <span className="muted">нет</span>}
        {chain.suggestions.length > 0 && <div className="doc-check warning">возможно: {chain.suggestions.map(docLabel).join(", ")}</div>}
      </div>
      <div className="pdoc-block">
        <span className="pdoc-block-label">Приёмка</span>
        {receivingAvailable ? <ReceivingCount upds={chain.upds} /> : <span className="muted">—</span>}
      </div>
      {chain.checks.length > 0 && (
        <div className="pdoc-block">
          <span className="pdoc-block-label">Что не так</span>
          {chain.checks.slice(0, 2).map((check, index) => <div key={index} className={CHECK_CLASS[check.level]}>{check.text}</div>)}
          {chain.checks.length > 2 && <div className="muted">ещё {chain.checks.length - 2}</div>}
        </div>
      )}
    </article>
  );
}

function InvoiceCell({ chain }: { chain: DocChain }) {
  const inv = chain.invoice;
  if (!inv) {
    return <><span className="muted">счёт не найден</span><div>{chain.upds[0]?.seller_name || "—"}</div></>;
  }
  return (
    <>
      <span className="doc-number">{inv.number ?? inv.title}</span> <span className="muted">от {dayText(inv.date)}</span> · {rub(inv.total)}
      <div>{inv.supplier_name ?? inv.seller_name ?? <span className="muted">продавец не прочитан</span>}</div>
    </>
  );
}

function ReceivingCount({ upds }: { upds: ChainUpd[] }) {
  const lines = upds.reduce((sum, u) => sum + (u.receiving?.lines ?? 0), 0);
  if (!upds.length) return <span className="muted">—</span>;
  if (!lines) return <span className="muted">не найдена</span>;
  return <>{countLabel(lines, ["строка", "строки", "строк"])}</>;
}

/** «Приёмка: 3 строки · ГРН FRX.26.0000101, … · Принято 2, Карантин 1». */
export function ReceivingBrief({ receiving, available = true }: { receiving: ReceivingSummary | null | undefined; available?: boolean }) {
  if (!available || receiving === undefined) return null;
  if (receiving === null) return <div className="muted">Приёмка на этом стенде недоступна</div>;
  if (!receiving.lines) return <div className="muted">Приёмка: строк с этим документом нет</div>;
  const shown = receiving.grns.slice(0, 4);
  return (
    <div className="doc-receiving">
      Приёмка: {countLabel(receiving.lines, ["строка", "строки", "строк"])}
      {shown.length > 0 && <> · ГРН {shown.join(", ")}{receiving.grns.length > shown.length ? ` и ещё ${receiving.grns.length - shown.length}` : ""}</>}
      {" · "}
      {receiving.statuses.map((s) => <StatusChip key={s.status} status={s.status} count={s.count} />)}
    </div>
  );
}

/** One chain opened: the invoice and its PDF, every closing document with its references and receiving, the amount
 *  suggestions (with «Связать» for the buyer) and the checks. */
export function ChainDetail({ chain, readOnly, receivingAvailable, onChanged, onOpenUpd, showReceivingLines = false }: {
  chain: DocChain; readOnly: boolean; receivingAvailable: boolean; onChanged: () => void; onOpenUpd?: (id: string) => void;
  showReceivingLines?: boolean;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const inv = chain.invoice;
  const link = async (u: ChainUpd) => {
    if (!inv) return;
    setBusy(u.id);
    setError("");
    try {
      await documentsApi.decide(u.id, inv.id, "confirmed");
      onChanged();
    } catch (reason) {
      setError(failureText(reason, "Не удалось связать"));
    } finally {
      setBusy("");
    }
  };
  return (
    <div className="doc-detail">
      {inv && (
        <div>
          <strong>Счёт</strong> {inv.title}
          {inv.invoice_file_id && <> · <a href={invoiceFileUrl(inv.id, inv.invoice_file_id)} target="_blank" rel="noreferrer">Открыть PDF счёта</a></>}
          {inv.seller_inn && <span className="muted"> · ИНН {inv.seller_inn}</span>}
        </div>
      )}
      <div>
        <strong>Закрывающие</strong>{chain.upds.length === 0 && <span className="muted"> — нет</span>}
        <ul className="doc-list">
          {chain.upds.map((u) => (
            <li key={u.id}>
              <UpdTitle upd={u} onOpenUpd={onOpenUpd} /> · {u.seller_name || "—"} · {rub(u.total_gross)}
              {u.buyer_kind === "other" && <span className="doc-check warning"> · покупатель — ИНН {u.buyer_org_inn}</span>}
              <span className="muted">
                {" · "}{ORIGIN_LABELS[u.origin]}{inv ? ` · ${LINK_METHOD_LABELS[u.method]}` : ""}{u.services_only ? " · услуги" : ""}
              </span>
              {u.basis && <div className="muted">основание: {u.basis}</div>}
              {u.payment_doc && <div className="muted">платёжка в документе: {u.payment_doc}</div>}
              <ReceivingBrief receiving={u.receiving} available={receivingAvailable} />
              {showReceivingLines && u.receiving?.items && u.receiving.items.length > 0 && <ReceivingLines lines={u.receiving.items} />}
            </li>
          ))}
        </ul>
      </div>
      {chain.suggestions.length > 0 && (
        <div>
          <strong>Возможно, закрывает этот счёт</strong> <span className="muted">(совпала сумма)</span>
          <ul className="doc-list">
            {chain.suggestions.map((u) => (
              <li key={u.id}>
                <UpdTitle upd={u} onOpenUpd={onOpenUpd} /> · {rub(u.total_gross)}{" "}
                {!readOnly && inv && (
                  <button type="button" className="secondary small" disabled={busy === u.id} onClick={() => void link(u)}>Связать</button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {chain.checks.length > 0 && (
        <ul className="doc-checks">
          {chain.checks.map((check, index) => <li key={index} className={CHECK_CLASS[check.level]}>{check.text}</li>)}
        </ul>
      )}
    </div>
  );
}

function UpdTitle({ upd, onOpenUpd }: { upd: ChainUpd; onOpenUpd?: (id: string) => void }) {
  if (!onOpenUpd) return <span>{docLabel(upd)}</span>;
  return <button type="button" className="link-button doc-link" onClick={(event) => { event.stopPropagation(); onOpenUpd(upd.id); }}>{docLabel(upd)}</button>;
}

function ReceivingLines({ lines }: { lines: NonNullable<ReceivingSummary["items"]> }) {
  return (
    <div className="table-wrap doc-receiving-lines">
      <table className="grid" aria-label="Строки приёмки">
        <thead><tr><th>ГРН</th><th>Наименование</th><th>Артикул</th><th className="num">Принято</th><th>Статус</th></tr></thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line.id}>
              <td>{line.grn ? `${line.grn}${line.package ? ` ${line.package}` : ""}` : "—"}</td>
              <td className="text">{line.name}</td>
              <td>{line.article || "—"}</td>
              <td className="num">{formatQty(line.accepted_quantity ?? line.fact_quantity, line.unit) || "—"}</td>
              <td><StatusChip status={line.status} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
