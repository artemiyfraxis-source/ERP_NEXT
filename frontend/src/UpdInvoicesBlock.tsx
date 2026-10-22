import { useCallback, useEffect, useState } from "react";
import { failureText } from "./api";
import { ReceivingBrief } from "./PurchasingDocChain";
import { InvoiceCardDialog } from "./PurchasingInvoices";
import {
  INVOICE_STATUS_LABELS, LINK_METHOD_LABELS, dayText, documentsApi, invoiceFileUrl, rub,
  type Decision, type DocCheck, type UpdInvoice, type UpdInvoices,
} from "./purchasingDocumentsApi";
import { StatusChip } from "./StatusChip";

/** «Счета по этому УПД» — mounted into the UPD card of the registry:
 *  the Kaiten invoices the document closes and how they were found, the seller's invoices near its date to link by
 *  hand, the pairs the buyer rejected (to undo), and the receiving lines that came with it. */

const CHECK_CLASS: Record<DocCheck["level"], string> = { error: "doc-check error", warning: "doc-check warning", info: "doc-check muted" };
const SHOWN_CANDIDATES = 3;

export function UpdInvoicesBlock({ documentId, readOnly = false }: { documentId: string; readOnly?: boolean }) {
  const [data, setData] = useState<UpdInvoices | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [openInvoice, setOpenInvoice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await documentsApi.updInvoices(documentId));
      setError("");
    } catch (reason) {
      setError(failureText(reason, "Счета по документу не загрузились"));
    }
  }, [documentId]);
  useEffect(() => { void load(); }, [load]);

  const decide = async (invoiceId: string, decision: Decision) => {
    setBusy(true);
    setError("");
    try {
      setData(await documentsApi.decide(documentId, invoiceId, decision));
    } catch (reason) {
      setError(failureText(reason, "Не удалось сохранить решение"));
    } finally {
      setBusy(false);
    }
  };

  const label = (invoice: UpdInvoice) => (
    <>
      <button type="button" className="link-button doc-link" onClick={() => setOpenInvoice(invoice.id)}>
        счёт {invoice.number ? `№ ${invoice.number}` : invoice.title}
      </button>{" "}
      от {dayText(invoice.date)} · {rub(invoice.total)}
      <span className="muted">
        {invoice.payments.length
          ? ` · оплачен ${invoice.payments.map((p) => `№ ${p.number} от ${dayText(p.date)}`).join(", ")}`
          : ` · ${INVOICE_STATUS_LABELS[invoice.status]}`}
      </span>
      {invoice.invoice_file_id && <> · <a href={invoiceFileUrl(invoice.id, invoice.invoice_file_id)} target="_blank" rel="noreferrer">PDF</a></>}
    </>
  );

  if (!data) {
    return (
      <section className="doc-upd-invoices" aria-label="Счета по этому УПД">
        <h3>Счета по этому УПД</h3>
        {error ? <div className="error-banner" role="alert">{error}</div> : <p className="muted">Загружаю…</p>}
      </section>
    );
  }
  const candidates = showAll ? data.candidates : data.candidates.slice(0, SHOWN_CANDIDATES);
  return (
    <section className="doc-upd-invoices" aria-label="Счета по этому УПД">
      <h3>Счета по этому УПД</h3>
      {data.linked.length === 0 && <p className="muted">Счёт в Kaiten не найден.</p>}
      {data.linked.length > 0 && (
        <ul className="doc-list" aria-label="Связанные счета">
          {data.linked.map((invoice) => (
            <li key={invoice.id}>
              {label(invoice)} <StatusChip tone={invoice.decision === "confirmed" ? "success" : "info"}>{LINK_METHOD_LABELS[invoice.method]}</StatusChip>
              {!readOnly && (invoice.decision === "confirmed" ? (
                <button type="button" className="secondary small" disabled={busy} onClick={() => void decide(invoice.id, null)}>Отменить связь</button>
              ) : (
                <>
                  <button type="button" className="secondary small" disabled={busy} onClick={() => void decide(invoice.id, "confirmed")}>Подтвердить</button>
                  <button type="button" className="secondary small" disabled={busy} onClick={() => void decide(invoice.id, "rejected")}>Не тот счёт</button>
                </>
              ))}
            </li>
          ))}
        </ul>
      )}
      {(data.basis_refs.length > 0 || data.payment_refs.length > 0) && (
        <p className="muted">
          {data.basis_refs.length > 0 && <>В основании: {data.basis_refs.map((r) => r.raw).join("; ")}. </>}
          {data.payment_refs.length > 0 && <>Платёжки в документе: {data.payment_refs.map((p) => `№ ${p.number}${p.date ? ` от ${dayText(p.date)}` : ""}`).join(", ")}.</>}
        </p>
      )}
      {data.checks.length > 0 && (
        <ul className="doc-checks">
          {data.checks.map((check, index) => <li key={index} className={CHECK_CLASS[check.level]}>{check.text}</li>)}
        </ul>
      )}
      {!readOnly && data.candidates.length > 0 && (
        <>
          <p className="muted">Счета этого продавца рядом по дате:</p>
          <ul className="doc-list" aria-label="Возможные счета">
            {candidates.map((invoice) => (
              <li key={invoice.id}>
                {label(invoice)} {invoice.suggested && <StatusChip tone="warning">совпала сумма</StatusChip>}
                <button type="button" className="secondary small" disabled={busy} onClick={() => void decide(invoice.id, "confirmed")}>Связать</button>
              </li>
            ))}
          </ul>
          {data.candidates.length > SHOWN_CANDIDATES && !showAll && (
            <button type="button" className="link-button" onClick={() => setShowAll(true)}>Показать ещё {data.candidates.length - SHOWN_CANDIDATES}</button>
          )}
        </>
      )}
      {!readOnly && data.rejected.length > 0 && (
        <div className="muted" aria-label="Отклонённые счета">
          Отклонены вами:{" "}
          {data.rejected.map((invoice) => (
            <span key={invoice.id} className="doc-rejected">
              {invoice.number ?? invoice.title}{" "}
              <button type="button" className="secondary small" disabled={busy} onClick={() => void decide(invoice.id, null)}>Вернуть</button>
            </span>
          ))}
        </div>
      )}
      <ReceivingBrief receiving={data.receiving} available={data.receiving !== null} />
      {error && <div className="error-banner" role="alert">{error}</div>}
      {openInvoice && <InvoiceCardDialog id={openInvoice} readOnly={readOnly} onClose={() => setOpenInvoice(null)} onChanged={() => void load()} />}
    </section>
  );
}
