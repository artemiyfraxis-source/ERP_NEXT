import { useEffect, useState } from "react";
import { failureText } from "./api";
import { openPlace } from "./navigation";
import { rfqApi, type InvoiceRequest, type RfqLine } from "./purchasingRfqApi";
import { mailtoHref, useCopied } from "./RfqLetters";
import { money } from "./rfqUi";

/** «Запросить счета» (Roller_Buyer InvoiceRequests): for the lines chosen in this request that have no invoice yet, one
 *  letter per supplier asking for an invoice — what to buy (whole packs), at his quote's prices, the delivery address
 *  of the lines and our requisites («Настройки» → «Реквизиты для писем»). The ERP sends no mail: copy, or open in the
 *  mail program. ERP's own «Сформировать запрос» (the PO letter without prices) stays on «Очереди». */

/** Statuses where the invoice is still to be asked for (backend rfq_invoice_requests.ASK_STATUSES). */
export const ASK_STATUSES = ["В работе", "Готов к запросу", "Жду счет"];
export const invoiceAskable = (line: RfqLine) => Boolean(line.decision && line.status && ASK_STATUSES.includes(line.status) && !line.cancelled);

export function RfqInvoiceRequests({ rfqId, onClose }: { rfqId: string; onClose: () => void }) {
  const [requests, setRequests] = useState<InvoiceRequest[] | null>(null);
  const [ready, setReady] = useState(0);
  const [error, setError] = useState("");
  const { copy, label } = useCopied();

  useEffect(() => {
    let live = true;
    rfqApi.invoiceRequests(rfqId)
      .then((result) => { if (live) { setRequests(result.requests); setReady(result.ready_for_request); } })
      .catch((reason) => { if (live) setError(failureText(reason, "Запросы счетов не загрузились")); });
    return () => { live = false; };
  }, [rfqId]);

  return (
    <section className="panel rfq-letters" aria-label="Запросы счетов">
      <div className="rfq-letters-head">
        <h3 title="По строкам, где выбран поставщик и счёта ещё нет. Количество — сколько придётся купить (мин. партия, целые бухты), цены — из его КП.">Запросы счетов</h3>
        <span className="muted">Количество — сколько придётся купить, цены — из КП поставщика. Почту ERP не отправляет.</span>
        <button type="button" className="secondary small rfq-letters-close" onClick={onClose}>Свернуть</button>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!requests && !error && <p className="muted">Загрузка…</p>}
      {requests?.length === 0 && <p className="muted">Нет строк с выбранным поставщиком без счёта.</p>}
      {ready > 0 && (
        <p className="muted rfq-letter-po">
          Строк «Готов к запросу»: {ready} — их можно отправить и запросом поставщику ERP (без цен, с номером заказа):{" "}
          <button type="button" className="link-button" onClick={() => openPlace("procurement", { tab: "queues" })}>Очереди → «Сформировать запрос»</button>.
        </p>
      )}
      {requests?.map((request) => {
        const key = (what: string) => `${request.supplier_id}-${what}`;
        return (
          <section key={request.supplier_id} className="rfq-letter" aria-label={`Запрос счёта: ${request.supplier_name}`}>
            <div className="rfq-letter-to-line">
              <strong>{request.supplier_name}</strong> · {request.lines.length} поз. · {money(request.total)}
              {request.email ? <span className="mono muted"> · {request.email}</span> : <span className="muted"> · почты нет в карточке поставщика</span>}
            </div>
            <div className="rfq-letter-subject"><span className="muted">Тема:</span> {request.subject}</div>
            <pre className="rfq-letter-body">{request.body}</pre>
            <div className="rfq-letter-actions">
              <button type="button" className="primary small" onClick={() => void copy(key("body"), request.body)}>{label(key("body"), "Скопировать текст")}</button>
              <button type="button" className="secondary small" onClick={() => void copy(key("subject"), request.subject)}>{label(key("subject"), "Скопировать тему")}</button>
              {request.email && <>
                <button type="button" className="secondary small" onClick={() => void copy(key("to"), request.email!)}>{label(key("to"), "Скопировать адрес")}</button>
                <a className="secondary small" href={mailtoHref(request.email, request.subject, request.body)}>Открыть в почте</a>
              </>}
              <button type="button" className="secondary small" title="Для заказа на сайте: позиции и количество бухт" onClick={() => void copy(key("cart"), request.cart)}>
                {label(key("cart"), "Список для корзины на сайте")}
              </button>
            </div>
          </section>
        );
      })}
    </section>
  );
}
