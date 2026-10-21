import { useEffect, useMemo, useState } from "react";
import { ApiError, failureText } from "./api";
import { formatDate, formatDay, unitLabel } from "./format";
import { INVOICE_STATUS_LABELS } from "./purchasingDocumentsApi";
import { rfqApi, type KaitenInvoice, type KaitenOrderReport, type RfqMatrix } from "./purchasingRfqApi";
import { GATE_LABELS, PRICIER_LABELS, type GateCode, type PricierCode } from "./rfqReasons";
import { decimalInput, money, quantity } from "./rfqUi";

/** «Счета из Kaiten» in a request (Roller_Buyer KaitenInvoices): invoices dated on or after the request whose
 *  positions match its lines. A card in Kaiten means the purchase went ahead, so «Оформить» records it — the
 *  supplier's quote (or one made from the invoice), the choice, and the procurement lines through ERP's own steps:
 *  «Сформировать запрос» → the invoice for the order → «В оплату». A dry run checks first and says what is missing. */

type Alloc = { rfqLineId: string; qty: string };

export function RfqKaitenInvoices({ m, editable, onDone }: { m: RfqMatrix; editable: boolean; onDone: (message: string) => void }) {
  const [list, setList] = useState<Awaited<ReturnType<typeof rfqApi.kaiten>> | null>(null);
  const [error, setError] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    rfqApi.kaiten(m.rfq.id).then((result) => { if (live) setList(result); }).catch((reason) => {
      // No invoices to show is not an error of the request: only a real failure is said.
      if (live && !(reason instanceof ApiError && reason.status === 404)) setError(failureText(reason, "Счета Kaiten не загрузились"));
    });
    return () => { live = false; };
  }, [m.rfq.id, m.lines]);

  if (error) return <div className="error-banner" role="alert">{error}</div>;
  if (!list || (list.invoices.length === 0 && list.unread === 0)) return null;
  const todo = list.invoices.filter((invoice) => invoice.matched > 0).length;
  const open = list.invoices.find((invoice) => invoice.invoice_id === openId);
  return (
    <section className="panel rfq-letters rfq-kaiten" aria-label="Счета из Kaiten">
      <div className="rfq-letters-head">
        <h3>Счета из Kaiten</h3>
        <span className="muted">
          датой не раньше запроса ({formatDay(list.rfq_date)}){todo ? ` · оформить: ${todo}` : ""}
          {list.synced_at ? ` · Kaiten обновлён ${formatDate(list.synced_at)}` : ""}
        </span>
      </div>
      {list.unread > 0 && (
        <p className="muted">Ещё {list.unread} счетов после даты запроса без прочитанных позиций (скан, xls или не перечитаны) — их строки здесь не сопоставить.</p>
      )}
      {list.invoices.length > 0 && (
        <table className="grid rfq-basket-table">
          <thead><tr><th>Счёт</th><th className="grow">Поставщик</th><th>Статус</th><th className="num">Сумма</th><th className="num">Строк запроса</th><th aria-label="Действия" /></tr></thead>
          <tbody>
            {list.invoices.map((invoice) => (
              <tr key={invoice.invoice_id}>
                <td className="text">
                  № {invoice.number ?? "?"} от {formatDay(invoice.date)}
                  {invoice.has_file && <> <a href={rfqApi.kaitenFileUrl(m.rfq.id, invoice.invoice_id)} target="_blank" rel="noreferrer">PDF</a></>}
                </td>
                <td className="text">{invoice.supplier_name ?? invoice.seller_name}</td>
                <td>{INVOICE_STATUS_LABELS[invoice.status as keyof typeof INVOICE_STATUS_LABELS] ?? invoice.status}</td>
                <td className="num">{money(invoice.total)}</td>
                <td className="num">{invoice.lines.length ? `${invoice.matched} из ${invoice.lines.filter((line) => !line.delivery).length}` : "не прочитаны"}</td>
                <td>
                  {invoice.ordered_lines.length > 0 && <span className="muted" title={invoice.ordered_lines.join(", ")}>оформлен ({invoice.ordered_lines.length}) </span>}
                  {editable && invoice.matched > 0 && (
                    <button type="button" className={openId === invoice.invoice_id ? "secondary small" : "primary small"} onClick={() => setOpenId(openId === invoice.invoice_id ? null : invoice.invoice_id)}>
                      {openId === invoice.invoice_id ? "Свернуть" : "Оформить"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {open && open.matched > 0 && editable && (
        <KaitenOrderForm key={open.invoice_id} m={m} invoice={open} onClose={() => setOpenId(null)} onDone={(message) => { setOpenId(null); onDone(message); }} />
      )}
    </section>
  );
}

function KaitenOrderForm({ m, invoice, onClose, onDone }: { m: RfqMatrix; invoice: KaitenInvoice; onClose: () => void; onDone: (message: string) => void }) {
  const goods = invoice.lines.filter((line) => !line.delivery);
  const [rows, setRows] = useState<Record<number, Alloc[]>>(() => Object.fromEntries(goods.map((line) => [
    line.seq, line.targets.length ? line.targets.map((target) => ({ rfqLineId: target.rfq_line_id, qty: target.quantity ?? "" })) : [{ rfqLineId: "", qty: "" }],
  ])));
  const [expected, setExpected] = useState("");
  const [gateCode, setGateCode] = useState<GateCode | "">("");
  const [gateNote, setGateNote] = useState("");
  const [pricierCode, setPricierCode] = useState<PricierCode | "">("");
  const [pricierNote, setPricierNote] = useState("");
  const [analogBy, setAnalogBy] = useState("");
  const [check, setCheck] = useState<KaitenOrderReport | null>(null);
  const [checkError, setCheckError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const lineOf = new Map(m.lines.map((line) => [line.id, line]));
  const open = m.lines.filter((line) => !line.cancelled && line.editable);
  const taken = new Set(Object.values(rows).flatMap((list) => list.map((alloc) => alloc.rfqLineId).filter(Boolean)));
  const picks = useMemo(() => goods.flatMap((line) => (rows[line.seq] ?? []).filter((alloc) => alloc.rfqLineId)
    .map((alloc) => ({ seq: line.seq, rfq_line_id: alloc.rfqLineId, quantity: decimalInput(alloc.qty) }))), [goods, rows]);
  const badQty = picks.some((pick) => !(Number(pick.quantity) > 0));
  const key = JSON.stringify(picks);

  useEffect(() => {
    if (picks.length === 0 || badQty) { setCheck(null); return; }
    let live = true;
    setCheckError("");
    rfqApi.kaitenOrder(m.rfq.id, invoice.invoice_id, { picks, dry_run: true })
      .then((report) => { if (live) { setCheck(report); setExpected((current) => current || report.expected_suggestion || ""); } })
      .catch((reason) => { if (live) { setCheck(null); setCheckError(failureText(reason, "Проверка не прошла")); } });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, badQty, m.rfq.id, invoice.invoice_id]);

  const needs = check?.needs ?? { gate: 0, not_cheapest: 0, analog: 0 };
  const problems = check?.problems ?? [];
  const missing = picks.length === 0 ? "Сопоставьте хотя бы одну позицию со строкой запроса"
    : badQty ? "Укажите количество в единицах запроса"
      : !expected ? "Укажите ожидаемую дату поставки"
        : needs.gate && (!gateCode || (gateCode === "other" && !gateNote.trim())) ? "Укажите причину обхода правила КП"
          : needs.not_cheapest && (!pricierCode || (pricierCode === "other" && !pricierNote.trim())) ? "Укажите, почему не самое дешёвое"
            : needs.analog && !analogBy.trim() ? "Аналог: укажите, с кем согласована замена"
              : check && !check.has_file ? "Файла счёта нет в Kaiten — внесите счёт в листе закупки" : null;

  async function save() {
    setBusy(true);
    setError("");
    try {
      const result = await rfqApi.kaitenOrder(m.rfq.id, invoice.invoice_id, {
        picks, expected_arrival: expected,
        ...(needs.gate && gateCode ? { gate_override_code: gateCode, gate_override_note: gateCode === "other" ? gateNote.trim() : null } : {}),
        ...(needs.not_cheapest && pricierCode ? { not_cheapest_code: pricierCode, not_cheapest_note: pricierCode === "other" ? pricierNote.trim() : null } : {}),
        ...(needs.analog ? { analog_approved_by: analogBy.trim() } : {}),
      });
      onDone(`Счёт № ${invoice.number} оформлен: ${(result.done ?? []).join("; ")}`);
    } catch (reason) {
      const detail = reason instanceof ApiError ? (reason.detail as { code?: string; message?: string } | undefined) : undefined;
      if (detail?.code === "kaiten_partial") onDone(detail.message ?? "Оформлено не до конца");
      else setError(failureText(reason, "Счёт не оформлен"));
    } finally {
      setBusy(false);
    }
  }

  const result = new Map((check?.lines ?? []).map((line) => [line.rfq_line_id, line]));
  const setAlloc = (seq: number, index: number, patch: Partial<Alloc>) =>
    setRows((current) => ({ ...current, [seq]: current[seq].map((alloc, at) => (at === index ? { ...alloc, ...patch } : alloc)) }));
  return (
    <div className="kaiten-order" role="region" aria-label={`Оформить счёт № ${invoice.number ?? ""}`}>
      <p>
        <strong>{invoice.supplier_name ?? "—"}</strong>
        {invoice.seller_name && invoice.supplier_name !== invoice.seller_name ? <span className="muted"> · продавец в Kaiten: {invoice.seller_name}</span> : null}
        {" · "}{invoice.quote_number ? `КП № ${invoice.quote_number}` : "КП нет — будет создано из цен счёта"}
        {" · "}цены {invoice.vat_included ? "с НДС" : "без НДС"} {invoice.vat_rate} %
        {Number(invoice.delivery_gross) > 0 ? ` · доставка по счёту ${money(invoice.delivery_gross)} (в строки не пишется)` : ""}
      </p>
      {invoice.supplier_id === null && <p className="tone-warn">Продавца нет в справочнике — добавьте поставщика (с ИНН {invoice.seller_inn || "?"}) и обновите счета из Kaiten.</p>}
      <div className="inwork-scroll" style={{ maxHeight: "40vh" }}>
        <table className="inwork-table">
          <thead><tr><th>Позиция счёта</th><th>Кол-во</th><th>Сумма</th><th>Строка запроса</th><th>Кол-во в ед. запроса</th><th>Цена за ед.</th></tr></thead>
          <tbody>
            {invoice.lines.flatMap((line) => {
              const head = <>
                <td style={{ whiteSpace: "normal", minWidth: 220 }}>{line.name}{line.article ? <small className="muted"> · {line.article}</small> : null}</td>
                <td>{line.qty ?? "—"} {line.unit}</td>
                <td>{money(line.sum ?? null)}</td>
              </>;
              if (line.delivery) return [<tr key={line.seq}>{head}<td colSpan={3} className="muted">не строка запроса (доставка или иное)</td></tr>];
              const allocs = rows[line.seq] ?? [];
              const total = allocs.reduce((sum, alloc) => sum + (alloc.rfqLineId ? Number(decimalInput(alloc.qty)) || 0 : 0), 0);
              return allocs.map((alloc, index) => {
                const target = alloc.rfqLineId ? lineOf.get(alloc.rfqLineId) : undefined;
                const done = target ? result.get(target.id) : undefined;
                const priceOff = done && done.quote_gross !== null && Math.abs(Number(done.quote_gross) - Number(done.invoice_gross)) > Math.max(0.01, Number(done.invoice_gross) * 0.01);
                return (
                  <tr key={`${line.seq}-${index}`}>
                    {index === 0 ? head : <td colSpan={3} />}
                    <td style={{ whiteSpace: "normal" }}>
                      <select aria-label={`Строка запроса для «${line.name}»`} value={alloc.rfqLineId} onChange={(event) => setAlloc(line.seq, index, { rfqLineId: event.target.value })}>
                        <option value="">— не из этого запроса —</option>
                        {open.map((option) => (
                          <option key={option.id} value={option.id} disabled={taken.has(option.id) && alloc.rfqLineId !== option.id}>
                            {option.name} · нужно {quantity(option.quantity)} {unitLabel(option.unit)}
                          </option>
                        ))}
                      </select>{" "}
                      {index === allocs.length - 1 && alloc.rfqLineId && (
                        <button type="button" className="link-button" title="Позиция покрывает ещё одну строку запроса (тот же товар по другому проекту)"
                          onClick={() => setRows((current) => ({ ...current, [line.seq]: [...current[line.seq], { rfqLineId: "", qty: "" }] }))}>+ строка</button>
                      )}
                      {index > 0 && (
                        <button type="button" className="link-button" onClick={() => setRows((current) => ({ ...current, [line.seq]: current[line.seq].filter((_, at) => at !== index) }))}>убрать</button>
                      )}
                      {done?.decision === "changed" && <small className="rfq-buy" style={{ display: "block" }}>по строке выбран другой поставщик — сначала отмените выбор</small>}
                      {done?.offer === "invoice" && <small className="muted" style={{ display: "block" }}>цена в КП — из счёта</small>}
                      {priceOff && target && <small className="rfq-buy" style={{ display: "block" }}>в КП {money(done.quote_gross)} за {unitLabel(target.unit)}, по счёту {money(done.invoice_gross)} — проверьте КП (цена упаковки за штуку?)</small>}
                    </td>
                    <td>{target && <><input aria-label={`Количество для «${line.name}»`} value={alloc.qty} onChange={(event) => setAlloc(line.seq, index, { qty: event.target.value })} style={{ width: 80 }} /> {unitLabel(target.unit)}</>}</td>
                    <td>{target && line.sum && total > 0 ? money(Number(line.sum) / total) : "—"}</td>
                  </tr>
                );
              });
            })}
          </tbody>
        </table>
      </div>
      <div className="rfq-quote-head">
        <label>Ожидаемая дата поставки<input type="date" aria-label="Ожидаемая дата поставки" value={expected} min={invoice.date ?? undefined} onChange={(event) => setExpected(event.target.value)} /></label>
      </div>
      <div className="rfq-basket-reasons">
        {needs.gate > 0 && (
          <label>Меньше КП, чем нужно, по {needs.gate} строк — причина обхода
            <select aria-label="Причина обхода" value={gateCode} onChange={(event) => setGateCode(event.target.value as GateCode)}>
              <option value="" disabled>выберите…</option>
              {Object.entries(GATE_LABELS).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
            </select>
            {gateCode === "other" && <input aria-label="Своими словами: причина обхода" value={gateNote} onChange={(event) => setGateNote(event.target.value)} />}
          </label>
        )}
        {needs.not_cheapest > 0 && (
          <label>Не самое дешёвое по {needs.not_cheapest} строк — причина
            <select aria-label="Причина: не самое дешёвое" value={pricierCode} onChange={(event) => setPricierCode(event.target.value as PricierCode)}>
              <option value="" disabled>выберите…</option>
              {Object.entries(PRICIER_LABELS).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
            </select>
            {pricierCode === "other" && <input aria-label="Своими словами: не самое дешёвое" value={pricierNote} onChange={(event) => setPricierNote(event.target.value)} />}
          </label>
        )}
        {needs.analog > 0 && (
          <label>Аналоги ({needs.analog}) — с кем согласована замена
            <input aria-label="С кем согласован аналог" value={analogBy} onChange={(event) => setAnalogBy(event.target.value)} />
          </label>
        )}
      </div>
      {checkError && <div className="warning-inline">{checkError}</div>}
      {problems.map((problem) => <div key={problem} className="warning-inline">{problem}</div>)}
      {error && <div className="error-banner" role="alert">{error}</div>}
      <p className="muted" style={{ margin: 0, fontSize: 12 }}>
        Дальше — шаги листа закупки: поставщик и количество в строки, «Сформировать запрос» (№ заказа), счёт с файлом на весь заказ → «В оплату».
      </p>
      <div className="form-actions">
        {missing && <span className="muted" style={{ marginRight: "auto" }}>{missing}</span>}
        <button type="button" className="secondary" onClick={onClose}>Отмена</button>
        <button type="button" className="primary" disabled={missing !== null || problems.length > 0 || Boolean(checkError) || busy} onClick={() => void save()}>
          {busy ? "Оформляю…" : `Оформить счёт № ${invoice.number ?? "?"} (${picks.length} строк)`}
        </button>
      </div>
    </div>
  );
}
