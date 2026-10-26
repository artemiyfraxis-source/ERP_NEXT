import { useEffect, useRef, useState, type CSSProperties } from "react";
import { failureText } from "./api";
import {
  rfqApi,
  type QuoteLineInput,
  type RfqLine,
  type RfqMatrix,
  type RfqSupplier,
} from "./purchasingRfqApi";
import { decimalInput, localToday, quantity } from "./rfqUi";
import { RfqQuoteImport, type ImportResult } from "./RfqQuoteImport";

/** «Внести КП» (Roller_Buyer QuoteEntryPanel): a list under the request's matrix, not a window (Витя via the owner,
 *  23.10). The header — the supplier picked from those in the request, received, «Номер КП», valid until, delivery,
 *  minimum order, note — the drop zone for the supplier's PDF, and a row per request line asked of him: price, VAT, MOQ,
 *  pack, price breaks «500: 90; 1000: 80», lead time, in stock, can't supply, analog. A row left empty is not offered. */

type QuoteRowState = {
  price: string; vat_included: boolean; vat_rate: string; moq: string; pack: string; lead: string;
  /** «500: 90; 1000: 80» — from this quantity, this unit price (the row's VAT mode) */
  tiers: string;
  in_stock: boolean; declined: boolean; analog: boolean; analog_note: string;
};

/** «500: 90; 1000: 80,50» → price breaks; null when a part cannot be read (Roller parseTiers). */
export function parseTiers(text: string): { from: string; price: string }[] | null {
  const parts = text.split(/[;\n]+/).map((part) => part.trim()).filter(Boolean);
  const tiers: { from: string; price: string }[] = [];
  for (const part of parts) {
    // «1 000: 80» — a thousands space inside a number is glued back first.
    const numbers = part.replace(/(\d)\s+(?=\d{3}\b)/g, "$1").match(/\d+(?:[.,]\d+)?/g);
    if (!numbers || numbers.length < 2) return null;
    const from = Number(numbers[0].replace(",", "."));
    const price = Number(numbers[1].replace(",", "."));
    if (!(from > 0) || !(price > 0)) return null;
    tiers.push({ from: String(from), price: String(price) });
  }
  return tiers;
}

/** The lines asked of a supplier (all of them for a supplier not in the request yet), cancelled ones left out. */
export function askedLines(matrix: RfqMatrix, supplier: RfqSupplier | null | undefined): RfqLine[] {
  const live = matrix.lines.filter((line) => !line.cancelled);
  return supplier?.line_ids ? live.filter((line) => supplier.line_ids?.includes(line.id)) : live;
}

/** Whom the form opens on: the column it was opened from, else the first who has not answered yet, else the first. */
export function firstSupplier(matrix: RfqMatrix, wanted: string | null): string {
  const known = matrix.suppliers.find((item) => item.supplier_id === wanted)
    ?? matrix.suppliers.find((item) => !item.answered && !item.declined_at)
    ?? matrix.suppliers[0];
  return known?.supplier_id ?? "";
}

export function QuoteEntry({ matrix, supplierId: wanted, onClose, onSaved }: {
  matrix: RfqMatrix; supplierId: string | null; onClose: () => void; onSaved: (name: string) => void;
}) {
  const blank = (): QuoteRowState => ({
    price: "", vat_included: true, vat_rate: matrix.settings.default_vat_rate, moq: "", pack: "", lead: "", tiers: "",
    in_stock: false, declined: false, analog: false, analog_note: "",
  });
  const [supplierId, setSupplierId] = useState(() => firstSupplier(matrix, wanted));
  const supplier = matrix.suppliers.find((item) => item.supplier_id === supplierId);
  const asked = askedLines(matrix, supplier);
  const live = matrix.lines.filter((line) => !line.cancelled);
  // Every live line has its row: switching the supplier keeps what was typed for the lines both were asked.
  const [rows, setRows] = useState<Record<string, QuoteRowState>>(() => Object.fromEntries(live.map((line) => [line.id, blank()])));
  const [receivedOn, setReceivedOn] = useState(localToday());
  const [quoteNumber, setQuoteNumber] = useState("");
  const [validUntil, setValidUntil] = useState("");
  const [delivery, setDelivery] = useState("");
  const [minOrder, setMinOrder] = useState("");
  const [note, setNote] = useState("");
  const [importNotes, setImportNotes] = useState<string[]>([]);
  const [importFile, setImportFile] = useState<{ name: string; base64: string } | null>(null);
  // The supplier's wording per request line, remembered as an alias when the quote is saved.
  const [wording, setWording] = useState<Record<string, string>>({});
  const [source, setSource] = useState<"manual" | "paste" | "file">("manual");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLElement>(null);
  const picker = useRef<HTMLSelectElement>(null);

  // Opened (or re-aimed by another column's «+ КП»): the list is under the matrix — bring it into view.
  useEffect(() => {
    if (wanted) setSupplierId(firstSupplier(matrix, wanted));
    const still = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    box.current?.scrollIntoView?.({ block: "start", behavior: still ? "auto" : "smooth" });
    picker.current?.focus({ preventScroll: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only the column asked for moves the form
  }, [wanted]);

  const patch = (lineId: string, change: Partial<QuoteRowState>) =>
    setRows((current) => ({ ...current, [lineId]: { ...(current[lineId] ?? blank()), ...change } }));
  const rowOf = (lineId: string) => rows[lineId] ?? blank();

  /** The reviewed import into the form: rows, the header found in the text, the file to keep, the wordings. */
  function applyImport(result: ImportResult) {
    setRows((current) => {
      const next = { ...current };
      for (const [lineId, row] of result.byLine) {
        const draft = next[lineId];
        if (!draft) continue;
        next[lineId] = {
          ...draft, price: row.declined ? "" : row.price ?? "", vat_included: result.vatIncluded,
          vat_rate: row.vat_rate ?? draft.vat_rate, lead: row.in_stock || row.lead_days === null ? "" : String(row.lead_days),
          in_stock: row.in_stock, declined: row.declined, analog: row.analog, analog_note: row.analog_note,
          moq: row.moq ?? draft.moq, pack: row.pack ?? draft.pack,
        };
      }
      return next;
    });
    setWording((current) => ({ ...current, ...Object.fromEntries(result.aliases.map((alias) => [alias.rfq_line_id, alias.text])) }));
    const header = result.header;
    const notes = [...result.notes];
    if (header?.number) setQuoteNumber(header.number);
    if (header?.valid_until) setValidUntil(header.valid_until);
    const inRequest = header?.supplier_id ? matrix.suppliers.find((item) => item.supplier_id === header.supplier_id) : undefined;
    if (header && inRequest && header.supplier_id !== supplierId) {
      setSupplierId(inRequest.supplier_id);
      notes.unshift(`Поставщик выбран по ${header.inn ? "ИНН" : "названию"} в документе: «${inRequest.name}».`);
    } else if (header?.inn && header.supplier_id !== supplierId) {
      const label = `${header.supplier_name ?? (header.document_name || "продавец")}, ИНН ${header.inn}`;
      notes.unshift(`В документе поставщик: ${label}. ${header.supplier_id === null ? "В справочнике его нет — добавьте в «Поставщики»" : "В этом запросе его нет — добавьте его в запрос («Добавить поставщика»)"} или проверьте, тот ли это документ.`);
    } else if (header?.supplier_matches === false && header.document_name) {
      notes.unshift(`В документе поставщик «${header.document_name}», а КП вносится от «${supplier?.name ?? "выбранного"}» — проверьте, тот ли это документ.`);
    }
    setImportNotes(notes);
    setImportFile(result.file);
    setSource(result.source);
  }

  const badTiers = asked.filter((line) => !rowOf(line.id).declined && rowOf(line.id).tiers.trim() && parseTiers(rowOf(line.id).tiers) === null);

  async function save() {
    if (!supplier) {
      setError("Выберите поставщика");
      return;
    }
    if (badTiers.length > 0) {
      setError(`Ступени цены не прочитаны: ${badTiers.map((line) => line.name).join(", ")} — формат «количество: цена; …»`);
      return;
    }
    const lines: QuoteLineInput[] = asked.flatMap((line) => {
      const row = rowOf(line.id);
      if (!row.declined && !decimalInput(row.price)) return [];
      return [{
        rfq_line_id: line.id, unit_price: row.declined ? null : decimalInput(row.price), vat_included: row.vat_included,
        vat_rate: decimalInput(row.vat_rate) || null, moq: decimalInput(row.moq) || null, pack: decimalInput(row.pack) || null,
        tiers: row.declined ? [] : parseTiers(row.tiers) ?? [],
        lead_days: row.in_stock ? 0 : row.lead.trim() ? Number(row.lead) : null, in_stock: row.in_stock, declined: row.declined,
        analog: row.analog, analog_note: row.analog ? row.analog_note.trim() || null : null,
      }];
    });
    if (lines.length === 0) {
      setError("Заполните цену или отказ хотя бы по одной позиции");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await rfqApi.addQuote(matrix.rfq.id, {
        supplier_id: supplier.supplier_id, received_on: receivedOn || null, valid_until: validUntil || null,
        delivery_cost: decimalInput(delivery) || null, min_order_sum: decimalInput(minOrder) || null, source, lines,
        number: quoteNumber.trim() || null, note: note.trim() || null,
        ...(importFile && source === "file" ? { file_name: importFile.name, file_base64: importFile.base64 } : {}),
        aliases: lines.filter((line) => wording[line.rfq_line_id]).map((line) => ({ rfq_line_id: line.rfq_line_id, text: wording[line.rfq_line_id] })),
      });
      onSaved(supplier.name);
    } catch (reason) {
      setError(failureText(reason, "КП не сохранено"));
    } finally {
      setBusy(false);
    }
  }

  const small: CSSProperties = { width: 80 };
  return (
    <section ref={box} className="rfq-quote-entry" aria-label="Внести КП">
      <div className="rfq-quote-entry-head">
        <h3>Внести КП</h3>
        {supplier && (
          <span className="muted" style={{ fontSize: 12 }}>
            {asked.length < live.length ? `у поставщика запрошено позиций: ${asked.length} из ${live.length} — остальные в КП не вносятся` : `позиций: ${asked.length}`}
          </span>
        )}
      </div>
      <div className="rfq-quote-head">
        <label>Поставщик
          <select ref={picker} aria-label="Поставщик КП" value={supplierId} onChange={(event) => setSupplierId(event.target.value)}>
            {matrix.suppliers.map((item) => (
              <option key={item.supplier_id} value={item.supplier_id}>
                {item.name}{item.is_key ? " ★" : ""}{item.answered ? " — КП есть" : item.declined_at ? " — отказался" : ""}
              </option>
            ))}
          </select>
        </label>
        <label>Получено<input type="date" value={receivedOn} onChange={(event) => setReceivedOn(event.target.value)} /></label>
        <label>Номер КП<input aria-label="Номер КП" value={quoteNumber} maxLength={64} onChange={(event) => setQuoteNumber(event.target.value)} style={{ width: 120 }} /></label>
        <label>Действует до<input type="date" value={validUntil} onChange={(event) => setValidUntil(event.target.value)} /></label>
        <label>Доставка, ₽ с НДС<input inputMode="decimal" value={delivery} placeholder="0 — бесплатно" onChange={(event) => setDelivery(event.target.value)} style={{ width: 110 }} /></label>
        <label>Мин. заказ, ₽<input inputMode="decimal" value={minOrder} onChange={(event) => setMinOrder(event.target.value)} style={{ width: 110 }} /></label>
        <label className="rfq-quote-note">Примечание<input aria-label="Примечание к КП" value={note} maxLength={2000} onChange={(event) => setNote(event.target.value)} /></label>
      </div>
      <RfqQuoteImport rfqId={matrix.rfq.id} supplierId={supplier?.supplier_id} lines={asked} importEnabled onApply={applyImport} />
      {importNotes.length > 0 && (
        <ul className="quote-import-notes" aria-label="Что сделано при разборе">
          {importNotes.map((text) => <li key={text}>{text}</li>)}
        </ul>
      )}
      {importFile && <p className="muted" style={{ margin: 0, fontSize: 12 }}>Файл «{importFile.name}» сохранится вместе с КП.</p>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="inwork-scroll">
        <table className="inwork-table">
          <thead>
            <tr>
              <th>Позиция</th><th>Кол-во</th><th>Цена</th><th>НДС в цене</th><th>Ставка, %</th><th>Мин. партия</th><th>Упаковка</th>
              <th title="Если цена зависит от количества: «500: 90; 1000: 80» — от 500 по 90 ₽, от 1000 по 80 ₽, в том же режиме НДС, что и основная цена. Нужную ступень для количества к покупке ERP возьмёт сама.">Ступени цены</th>
              <th>Срок, дн</th><th>В наличии</th><th>Отказ</th><th>Аналог</th>
            </tr>
          </thead>
          <tbody>
            {asked.map((line) => {
              const row = rowOf(line.id);
              const tiersBad = !row.declined && row.tiers.trim() !== "" && parseTiers(row.tiers) === null;
              return (
                <tr key={line.id}>
                  <td style={{ whiteSpace: "normal", minWidth: 200 }}><strong>{line.name}</strong> <small className="muted">{line.article && line.article.trim().toLowerCase() !== line.name.trim().toLowerCase() ? line.article : ""}</small></td>
                  <td>{quantity(line.quantity)} {line.unit}</td>
                  <td><input aria-label={`Цена ${line.name}`} inputMode="decimal" disabled={row.declined} value={row.price} onChange={(event) => patch(line.id, { price: event.target.value })} style={{ width: 100 }} /></td>
                  <td><input type="checkbox" aria-label={`НДС в цене ${line.name}`} checked={row.vat_included} onChange={(event) => patch(line.id, { vat_included: event.target.checked })} /></td>
                  <td><input aria-label={`Ставка НДС ${line.name}`} inputMode="decimal" value={row.vat_rate} onChange={(event) => patch(line.id, { vat_rate: event.target.value })} style={{ width: 56 }} /></td>
                  <td><input aria-label={`Мин. партия ${line.name}`} inputMode="decimal" value={row.moq} onChange={(event) => patch(line.id, { moq: event.target.value })} style={small} /></td>
                  <td><input aria-label={`Упаковка ${line.name}`} inputMode="decimal" value={row.pack} onChange={(event) => patch(line.id, { pack: event.target.value })} style={small} /></td>
                  <td>
                    <input
                      aria-label={`Ступени цены ${line.name}`} aria-invalid={tiersBad} className={tiersBad ? "rfq-input-bad" : undefined}
                      placeholder="500: 90; 1000: 80" disabled={row.declined} value={row.tiers}
                      onChange={(event) => patch(line.id, { tiers: event.target.value })} style={{ width: 140 }}
                    />
                    {tiersBad && <small className="tone-danger" style={{ display: "block" }}>количество: цена; …</small>}
                  </td>
                  <td><input aria-label={`Срок ${line.name}`} inputMode="numeric" disabled={row.in_stock} value={row.lead} onChange={(event) => patch(line.id, { lead: event.target.value.replace(/\D/g, "") })} style={{ width: 56 }} /></td>
                  <td><input type="checkbox" aria-label={`В наличии ${line.name}`} checked={row.in_stock} onChange={(event) => patch(line.id, { in_stock: event.target.checked })} /></td>
                  <td><input type="checkbox" aria-label={`Отказ ${line.name}`} checked={row.declined} onChange={(event) => patch(line.id, { declined: event.target.checked, price: event.target.checked ? "" : row.price })} /></td>
                  <td>
                    <input type="checkbox" aria-label={`Аналог ${line.name}`} checked={row.analog} onChange={(event) => patch(line.id, { analog: event.target.checked })} />
                    {row.analog && <input aria-label={`Какой аналог ${line.name}`} value={row.analog_note} placeholder="какой" onChange={(event) => patch(line.id, { analog_note: event.target.value })} style={{ width: 120, marginLeft: 4 }} />}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="rfq-quote-actions">
        <span className="rfq-quote-hint">Пустая строка без отказа — позиция не предложена. Повторное КП заменит прежнее по этим позициям.</span>
        <button type="button" className="secondary" onClick={onClose}>Отмена</button>
        <button type="button" className="primary" disabled={busy || !supplier} onClick={() => void save()}>Сохранить КП</button>
      </div>
    </section>
  );
}
