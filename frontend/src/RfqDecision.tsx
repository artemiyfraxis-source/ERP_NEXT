import { useMemo, useState } from "react";
import { failureText } from "./api";
import { formatDay } from "./format";
import { decisionViolations, quoteCell, rfqApi, type DecisionField, type RfqLine, type RfqMatrix } from "./purchasingRfqApi";
import { chip, gateHint, gateText, locked } from "./RfqMatrix";
import { GATE_LABELS, PRICIER_LABELS, type GateCode, type PricierCode } from "./rfqReasons";
import { Dialog, money, quantity } from "./rfqUi";

/** «Выбор поставщика» (Roller_Buyer DecisionCard in ERP's dialog): the line's offers side by side; the card says
 *  before the click exactly what the server will demand — a gate override, «почему не самое дешёвое», an analog
 *  sign-off — from Roller's fixed lists, with the buyer's words on «Другое». The choice writes supplier and quantity
 *  into the procurement line with its version; the quote's price is not written there: the line's money is filled
 *  from the invoice at «Жду счет» / «В оплату», the only stages ERP lets money be edited. */

export function reasonOk(code: string, note: string) {
  return Boolean(code) && (code !== "other" || note.trim() !== "");
}

function ReasonSelect<T extends string>({ label, hint, labels, code, note, onCode, onNote }: {
  label: string; hint?: string; labels: Record<T, string>; code: T | ""; note: string; onCode: (code: T) => void; onNote: (note: string) => void;
}) {
  return (
    <div className="rfq-reason">
      <span className="rfq-reason-label">{label}</span>
      {hint && <small className="muted">{hint}</small>}
      <select aria-label={label} value={code} onChange={(event) => onCode(event.target.value as T)}>
        <option value="" disabled>Выберите причину</option>
        {(Object.entries(labels) as [T, string][]).map(([value, text]) => <option key={value} value={value}>{text}</option>)}
      </select>
      {code === "other" && (
        <input aria-label={`${label}: своими словами`} value={note} maxLength={1000} onChange={(event) => onNote(event.target.value)} placeholder="Опишите причину" />
      )}
    </div>
  );
}

export function DecisionPanel({ matrix, line, supplierId, editable, onClose, onDone }: {
  matrix: RfqMatrix; line: RfqLine; supplierId: string; editable: boolean; onClose: () => void; onDone: (message: string) => void;
}) {
  const [chosenId, setChosenId] = useState(supplierId);
  const [gateCode, setGateCode] = useState<GateCode | "">("");
  const [gateNote, setGateNote] = useState("");
  const [pricierCode, setPricierCode] = useState<PricierCode | "">("");
  const [pricierNote, setPricierNote] = useState("");
  const [analogBy, setAnalogBy] = useState("");
  const [serverFields, setServerFields] = useState<DecisionField[]>([]);
  const [messages, setMessages] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const chosen = quoteCell(line, chosenId);
  const chosenSupplier = matrix.suppliers.find((supplier) => supplier.supplier_id === chosenId);
  const decidedSupplier = line.decision ? matrix.suppliers.find((supplier) => supplier.supplier_id === line.decision?.supplier_id) : undefined;
  const choosable = Boolean(chosen && !chosen.declined && !chosen.expired && chosen.gross_unit !== null);
  const required = useMemo(() => new Set<DecisionField>([...(chosen?.requires ?? []), ...serverFields]), [chosen, serverFields]);
  // What the server would refuse, said before the click: the button waits for it.
  const missing = required.has("gate_override_reason") && !reasonOk(gateCode, gateNote) ? "Укажите причину обхода правила КП"
    : required.has("not_cheapest_reason") && !reasonOk(pricierCode, pricierNote) ? "Укажите, почему не самое дешёвое КП"
      : required.has("analog_approved_by") && !analogBy.trim() ? "Аналог: укажите, с кем согласована замена" : null;
  const offers = matrix.suppliers.flatMap((supplier) => {
    const cell = quoteCell(line, supplier.supplier_id);
    return cell ? [{ supplier, cell }] : [];
  });

  async function decide() {
    if (!chosen) return;
    setBusy(true);
    setError("");
    setMessages([]);
    try {
      await rfqApi.decide(matrix.rfq.id, {
        rfq_line_id: line.id, quote_line_id: chosen.quote_line_id, expected_version: line.version,
        ...(required.has("gate_override_reason") && gateCode ? { gate_override_code: gateCode, gate_override_note: gateCode === "other" ? gateNote.trim() : null } : {}),
        ...(required.has("not_cheapest_reason") && pricierCode ? { not_cheapest_code: pricierCode, not_cheapest_note: pricierCode === "other" ? pricierNote.trim() : null } : {}),
        ...(required.has("analog_approved_by") ? { analog_approved_by: analogBy.trim() } : {}),
      });
      onDone(`${line.legacy_id ?? line.name}: выбран «${chosenSupplier?.name ?? ""}» — поставщик и количество записаны в строку закупки`);
    } catch (reason) {
      const violations = decisionViolations(reason);
      if (violations.length > 0) {
        setServerFields(violations.map((violation) => violation.field));
        setMessages(violations.map((violation) => violation.message));
      } else {
        setError(failureText(reason, "Решение не сохранено"));
      }
    } finally {
      setBusy(false);
    }
  }

  async function undo() {
    if (!line.decision) return;
    setBusy(true);
    setError("");
    try {
      await rfqApi.undoDecision(matrix.rfq.id, line.decision.id);
      onDone(`${line.legacy_id ?? line.name}: решение отменено, данные строки закупки остались как были`);
    } catch (reason) {
      setError(failureText(reason, "Решение не отменено"));
    } finally {
      setBusy(false);
    }
  }

  const decision = line.decision;
  return (
    <Dialog title={`${line.name}: выбор поставщика`} wide onClose={onClose}>
      <p className="page-sub">
        Нужно {quantity(line.quantity)} {line.unit} · план {money(line.budget_unit_gross)} за {line.unit} ·{" "}
        <span style={chip(line.gate.passes ? "ok" : "warn")}>{gateText(line)}</span> {line.gate.passes ? "" : gateHint(line)}
      </p>
      <div className="inwork-scroll">
        <table className="inwork-table">
          <thead>
            <tr><th>Поставщик</th><th>Цена с НДС</th><th>Берём</th><th>Сумма</th><th>Срок</th><th>Отметки</th></tr>
          </thead>
          <tbody>
            {offers.map(({ supplier, cell }) => {
              const marks = [
                cell.cheapest && "самое дешёвое", cell.baseline && "база экономии", cell.decided && "выбрано",
                cell.analog && `аналог${cell.analog_note ? `: ${cell.analog_note}` : ""}`, cell.expired && "срок КП истёк",
                cell.declined && "отказ",
              ].filter(Boolean).join(", ");
              const active = supplier.supplier_id === chosenId;
              return (
                <tr
                  key={supplier.supplier_id} aria-selected={active}
                  style={{ cursor: "pointer", background: active ? "var(--sheet-selected)" : undefined }}
                  onClick={() => { setChosenId(supplier.supplier_id); setServerFields([]); setMessages([]); }}
                >
                  <td><strong>{supplier.name}{supplier.is_key ? " ★" : ""}</strong></td>
                  <td>{cell.declined ? "—" : money(cell.gross_unit)}</td>
                  <td>{cell.cover_qty ? `${quantity(cell.cover_qty)} ${line.unit}` : "—"}</td>
                  <td>{cell.declined ? "—" : money(cell.total)}</td>
                  <td>{cell.in_stock ? "в наличии" : cell.lead_days !== null ? `${cell.lead_days} дн` : ""}</td>
                  <td>{marks}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {decision ? (
        <div style={{ display: "grid", gap: 6 }}>
          <p className="page-sub">
            Выбран «{decidedSupplier?.name ?? ""}»: {quantity(decision.cover_quantity)} {line.unit} по {money(decision.unit_gross)} — {money(decision.total_gross)}
            {decision.applied_at ? `, поставщик и количество записаны в строку закупки ${formatDay(decision.applied_at)}` : ""}.
            {decision.gate_override_reason ? ` Обход: ${decision.gate_override_reason}.` : ""}
            {decision.not_cheapest_reason ? ` Не самое дешёвое: ${decision.not_cheapest_reason}.` : ""}
            {decision.analog_approved_by ? ` Аналог согласован: ${decision.analog_approved_by}.` : ""}
          </p>
          <p className="muted rfq-price-note">
            Цена из КП в строку закупки не пишется: деньги строки вносятся по счёту на этапах «Жду счет» / «В оплату».
          </p>
          {editable && line.editable && (
            <div className="form-actions">
              <span className="muted" style={{ marginRight: "auto", fontSize: 12 }}>При отмене поставщик и количество в строке закупки останутся как есть.</span>
              <button type="button" className="secondary" disabled={busy} onClick={() => void undo()}>Отменить выбор</button>
            </div>
          )}
          {locked(line) && <p className="muted">Заказано — строка уже «{line.status}», решение зафиксировано.</p>}
        </div>
      ) : !editable ? (
        <p className="muted">Решение принимает закупщик строки в открытом запросе.</p>
      ) : !line.editable ? (
        <p className="muted">Строка уже «{line.status}» — поставщик в неё не пишется.</p>
      ) : !choosable ? (
        <p className="muted">Это предложение выбрать нельзя: {chosen?.declined ? "поставщик отказался" : chosen?.expired ? "срок КП истёк" : "нет цены"}.</p>
      ) : (
        <form className="dialog-form" onSubmit={(event) => { event.preventDefault(); void decide(); }}>
          {required.size === 0 && <p className="page-sub">Причины не нужны: КП достаточно и выбрано самое дешёвое.</p>}
          {required.has("gate_override_reason") && (
            <ReasonSelect
              label="Гейт конкуренции не пройден — причина обхода" hint={gateHint(line)} labels={GATE_LABELS}
              code={gateCode} note={gateNote} onCode={setGateCode} onNote={setGateNote}
            />
          )}
          {required.has("not_cheapest_reason") && (
            <ReasonSelect
              label="Не самое дешёвое КП — причина" labels={PRICIER_LABELS}
              code={pricierCode} note={pricierNote} onCode={setPricierCode} onNote={setPricierNote}
            />
          )}
          {required.has("analog_approved_by") && (
            <label>Аналог — с кем согласовано{chosen?.analog_note ? ` (${chosen.analog_note})` : ""}
              <input aria-label="Аналог — с кем согласовано" value={analogBy} maxLength={255} onChange={(event) => setAnalogBy(event.target.value)} placeholder="ФИО конструктора" />
            </label>
          )}
          {messages.map((message) => <div key={message} className="warning-inline">{message}</div>)}
          <p className="muted rfq-price-note">В строку закупки запишутся поставщик и количество; цена из КП — нет (деньги строки вносятся по счёту).</p>
          <div className="form-actions">
            {missing && <span className="muted" style={{ marginRight: "auto" }}>{missing}</span>}
            <button type="button" className="secondary" onClick={onClose}>Отмена</button>
            <button type="submit" className="primary" disabled={busy || missing !== null} title={missing ?? ""}>
              Выбрать «{chosenSupplier?.name ?? ""}» — {quantity(chosen?.cover_qty)} {line.unit} на {money(chosen?.total)}
            </button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
