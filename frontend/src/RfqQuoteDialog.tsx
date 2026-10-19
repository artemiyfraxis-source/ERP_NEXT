import { useState, type CSSProperties } from "react";
import { failureText } from "./api";
import {
  rfqApi,
  type ImportedQuoteLine,
  type PastedRow,
  type QuoteLineInput,
  type RfqLine,
  type RfqMatrix,
  type RfqSupplier,
  type SupplierBrief,
} from "./purchasingRfqApi";
import { decimalInput, Dialog, localToday, quantity } from "./rfqUi";
import { useStageOption } from "./StageOptions";

/** «КП от поставщика» (Roller_Buyer QuoteEntryPanel): the header — supplier, received, «Номер КП», valid until,
 *  delivery, minimum order, note — and a row per request line asked of him: price, VAT, MOQ, pack, price breaks
 *  «500: 90; 1000: 80», lead time, in stock, can't supply, analog. A row left empty is not offered. */

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
export function askedLines(matrix: RfqMatrix, supplier: RfqSupplier | null): RfqLine[] {
  const live = matrix.lines.filter((line) => !line.cancelled);
  return supplier?.line_ids ? live.filter((line) => supplier.line_ids?.includes(line.id)) : live;
}

export function QuoteDialog({ matrix, supplier, directory = [], onClose, onSaved }: {
  matrix: RfqMatrix; supplier: RfqSupplier | null; directory?: SupplierBrief[]; onClose: () => void;
  onSaved: (name: string) => void;
}) {
  const blank = (): QuoteRowState => ({
    price: "", vat_included: true, vat_rate: matrix.settings.default_vat_rate, moq: "", pack: "", lead: "", tiers: "",
    in_stock: false, declined: false, analog: false, analog_note: "",
  });
  const asked = askedLines(matrix, supplier);
  const [rows, setRows] = useState<Record<string, QuoteRowState>>(() => Object.fromEntries(asked.map((line) => [line.id, blank()])));
  const [supplierName, setSupplierName] = useState("");
  const [receivedOn, setReceivedOn] = useState(localToday());
  const [quoteNumber, setQuoteNumber] = useState("");
  const [validUntil, setValidUntil] = useState("");
  const [delivery, setDelivery] = useState("");
  const [minOrder, setMinOrder] = useState("");
  const [note, setNote] = useState("");
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [unmatched, setUnmatched] = useState<PastedRow[]>([]);
  const importEnabled = useStageOption("procurement.quote_import");
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [importNotes, setImportNotes] = useState<string[]>([]);
  const [importUnmatched, setImportUnmatched] = useState<ImportedQuoteLine[]>([]);
  const [importUnread, setImportUnread] = useState<string[]>([]);
  // The supplier's wording per request line, remembered as an alias when the quote is saved.
  const [wording, setWording] = useState<Record<string, string>>({});
  const [source, setSource] = useState<"manual" | "paste" | "file">("manual");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const patch = (lineId: string, change: Partial<QuoteRowState>) =>
    setRows((current) => ({ ...current, [lineId]: { ...current[lineId], ...change } }));

  /** Who the quote is from: the column it was opened from, else the name typed (request → directory → new). */
  async function resolveSupplier(): Promise<{ id: string; name: string } | null> {
    if (supplier) return { id: supplier.supplier_id, name: supplier.name };
    const name = supplierName.trim().replace(/\s+/g, " ");
    if (!name) return null;
    const same = (value: string) => value.trim().toLowerCase() === name.toLowerCase();
    const inRequest = matrix.suppliers.find((item) => same(item.name));
    if (inRequest) return { id: inRequest.supplier_id, name: inRequest.name };
    const known = directory.find((item) => same(item.name));
    const added = await rfqApi.addSupplier(matrix.rfq.id, known ? { supplier_id: known.supplier_id } : { name });
    return { id: added.supplier_id, name: added.name };
  }
  const knownSupplierId = supplier?.supplier_id
    ?? matrix.suppliers.find((item) => item.name.trim().toLowerCase() === supplierName.trim().toLowerCase())?.supplier_id;

  async function parsePaste() {
    setError("");
    try {
      const preview = await rfqApi.paste(matrix.rfq.id, pasteText, knownSupplierId);
      const next = { ...rows };
      for (const row of preview.rows) {
        if (!row.rfq_line_id || !next[row.rfq_line_id]) continue;
        next[row.rfq_line_id] = {
          ...next[row.rfq_line_id], price: row.price ?? "", vat_included: preview.vat_included,
          vat_rate: row.vat_rate ?? next[row.rfq_line_id].vat_rate, lead: row.lead_days === null ? "" : String(row.lead_days),
          in_stock: row.in_stock, declined: row.declined, analog: row.analog,
        };
      }
      setRows(next);
      setUnmatched(preview.rows.filter((row) => !row.rfq_line_id));
      setSource("paste");
      setPasteOpen(false);
    } catch (reason) {
      setError(failureText(reason, "Вставка не разобрана"));
    }
  }

  /** One proposed line into the form row of its request line; the buyer still reviews and saves. */
  function fillFrom(next: Record<string, QuoteRowState>, row: ImportedQuoteLine, lineId: string) {
    const current = next[lineId];
    if (!current) return;
    next[lineId] = {
      ...current, price: row.declined ? "" : row.price ?? "", vat_rate: row.vat_rate ?? current.vat_rate,
      lead: row.in_stock || row.lead_days === null ? "" : String(row.lead_days), in_stock: row.in_stock,
      declined: row.declined, analog: row.analog, analog_note: row.analog_note, moq: row.moq ?? current.moq,
      pack: row.pack ?? current.pack,
    };
  }

  const wordingOf = (row: ImportedQuoteLine) => `${row.source_article} ${row.source_name}`.trim();

  async function runImport(input: { text: string } | { file_name: string; content_base64: string }) {
    setError("");
    setImportBusy(true);
    try {
      const result = await rfqApi.importQuote(matrix.rfq.id, knownSupplierId ?? null, input);
      const next = { ...rows };
      const words: Record<string, string> = {};
      for (const row of result.lines) {
        if (!row.rfq_line_id || !next[row.rfq_line_id]) continue;
        fillFrom(next, row, row.rfq_line_id);
        next[row.rfq_line_id].vat_included = result.vat_included;
        if (row.matched_by !== "alias") words[row.rfq_line_id] = wordingOf(row);
      }
      setRows(next);
      setWording((current) => ({ ...current, ...words }));
      setImportNotes(result.lines.filter((row) => row.pack_note).map((row) => `«${row.source_name}»: ${row.pack_note}`));
      setImportUnmatched(result.unmatched);
      setImportUnread(result.unread);
      setSource(result.source);
      setImportOpen(false);
    } catch (reason) {
      setError(failureText(reason, "КП не разобрано"));
    } finally {
      setImportBusy(false);
    }
  }

  function importFile(file: File | undefined) {
    if (!file) return;
    if (file.size > 20 * 1024 * 1024) {
      setError("PDF больше 20 МБ — сожмите файл или вставьте текст");
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => setError("Файл не прочитан");
    reader.onload = () => {
      const data = String(reader.result ?? "");
      void runImport({ file_name: file.name, content_base64: data.slice(data.indexOf(",") + 1) });
    };
    reader.readAsDataURL(file);
  }

  /** The buyer puts an unmatched quote line onto a request line by hand. */
  function assignUnmatched(index: number, lineId: string) {
    const row = importUnmatched[index];
    if (!row || !lineId) return;
    const next = { ...rows };
    fillFrom(next, row, lineId);
    setRows(next);
    setWording((current) => ({ ...current, [lineId]: wordingOf(row) }));
    if (row.pack_note) setImportNotes((current) => [...current, `«${row.source_name}»: ${row.pack_note}`]);
    setImportUnmatched((current) => current.filter((_, at) => at !== index));
  }

  const badTiers = asked.filter((line) => !rows[line.id].declined && rows[line.id].tiers.trim() && parseTiers(rows[line.id].tiers) === null);

  async function save() {
    if (badTiers.length > 0) {
      setError(`Ступени цены не прочитаны: ${badTiers.map((line) => line.name).join(", ")} — формат «количество: цена; …»`);
      return;
    }
    const lines: QuoteLineInput[] = asked.flatMap((line) => {
      const row = rows[line.id];
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
      const who = await resolveSupplier();
      if (!who) {
        setError("Укажите поставщика");
        setBusy(false);
        return;
      }
      await rfqApi.addQuote(matrix.rfq.id, {
        supplier_id: who.id, received_on: receivedOn || null, valid_until: validUntil || null,
        delivery_cost: decimalInput(delivery) || null, min_order_sum: decimalInput(minOrder) || null, source, lines,
        number: quoteNumber.trim() || null, note: note.trim() || null,
        ...(importEnabled ? { aliases: lines.filter((line) => wording[line.rfq_line_id]).map((line) => ({ rfq_line_id: line.rfq_line_id, text: wording[line.rfq_line_id] })) } : {}),
      });
      onSaved(who.name);
    } catch (reason) {
      setError(failureText(reason, "КП не сохранено"));
    } finally {
      setBusy(false);
    }
  }

  const small: CSSProperties = { width: 80 };
  const pool = [...matrix.suppliers.map((item) => item.name), ...directory.map((item) => item.name)];
  return (
    <Dialog title={supplier ? `КП от «${supplier.name}»` : "Внести КП"} wide onClose={onClose}>
      <div className="rfq-quote-head">
        {!supplier && (
          <label>Поставщик
            <input aria-label="Поставщик КП" list="rfq-quote-suppliers" value={supplierName} placeholder="Из запроса, справочника или новый"
              onChange={(event) => setSupplierName(event.target.value)} style={{ width: 260 }} />
            <datalist id="rfq-quote-suppliers">
              {[...new Set(pool)].map((name) => <option key={name} value={name} />)}
            </datalist>
          </label>
        )}
        <label>Получено<input type="date" value={receivedOn} onChange={(event) => setReceivedOn(event.target.value)} /></label>
        <label>Номер КП<input aria-label="Номер КП" value={quoteNumber} maxLength={64} onChange={(event) => setQuoteNumber(event.target.value)} style={{ width: 120 }} /></label>
        <label>Действует до<input type="date" value={validUntil} onChange={(event) => setValidUntil(event.target.value)} /></label>
        <label>Доставка, ₽ с НДС<input inputMode="decimal" value={delivery} placeholder="0 — бесплатно" onChange={(event) => setDelivery(event.target.value)} style={{ width: 110 }} /></label>
        <label>Мин. заказ, ₽<input inputMode="decimal" value={minOrder} onChange={(event) => setMinOrder(event.target.value)} style={{ width: 110 }} /></label>
        <label className="rfq-quote-note">Примечание<input aria-label="Примечание к КП" value={note} maxLength={2000} onChange={(event) => setNote(event.target.value)} /></label>
        <button type="button" className="secondary small" onClick={() => setPasteOpen(!pasteOpen)} aria-expanded={pasteOpen}>Вставить из Excel</button>
        {importEnabled && (
          <button type="button" className="secondary small" onClick={() => setImportOpen(!importOpen)} aria-expanded={importOpen}>Вставить из PDF / текста</button>
        )}
      </div>
      {importEnabled && importOpen && (
        <div className="quote-import-panel">
          <textarea
            aria-label="Текст КП" rows={6} value={importText} onChange={(event) => setImportText(event.target.value)}
            placeholder="Вставьте текст письма или КП: в каждой строке наименование, количество, цена и сумма (единица и срок — если есть)"
          />
          <div className="quote-import-actions">
            <button type="button" className="secondary small" disabled={!importText.trim() || importBusy} onClick={() => void runImport({ text: importText })}>Разобрать текст</button>
            <label className="quote-import-file">или PDF с текстовым слоем (до 20 МБ)
              <input type="file" accept="application/pdf,.pdf" aria-label="PDF с КП" disabled={importBusy} onChange={(event) => { importFile(event.target.files?.[0]); event.target.value = ""; }} />
            </label>
          </div>
        </div>
      )}
      {importNotes.length > 0 && (
        <ul className="quote-import-notes" aria-label="Что сделано при разборе">
          {importNotes.map((text) => <li key={text}>{text}</li>)}
        </ul>
      )}
      {importUnmatched.length > 0 && (
        <div className="warning-inline quote-import-unmatched">
          Не сопоставлено со строками запроса — отнесите к позиции или пропустите:
          {importUnmatched.map((row, index) => (
            <div key={`${row.source_name}-${index}`} className="quote-import-unmatched-row">
              <span>{row.source_name || row.source_article}{row.price ? `, ${row.price} ₽` : ""}</span>
              <select aria-label={`Отнести «${row.source_name}»`} value="" onChange={(event) => assignUnmatched(index, event.target.value)}>
                <option value="">— к позиции —</option>
                {asked.map((line) => <option key={line.id} value={line.id}>{line.name}</option>)}
              </select>
            </div>
          ))}
        </div>
      )}
      {importUnread.length > 0 && (
        <div className="warning-inline">Строки с ценой, которые не удалось прочитать: {importUnread.join("; ")}</div>
      )}
      {pasteOpen && (
        <div style={{ display: "grid", gap: 6 }}>
          <textarea
            aria-label="Таблица из Excel" rows={6} value={pasteText} onChange={(event) => setPasteText(event.target.value)}
            placeholder="Скопируйте строки КП из Excel вместе с заголовком: наименование, артикул, кол-во, цена, НДС, срок"
          />
          <div><button type="button" className="secondary small" disabled={!pasteText.trim()} onClick={() => void parsePaste()}>Разобрать</button></div>
        </div>
      )}
      {supplier && asked.length < matrix.lines.filter((line) => !line.cancelled).length && (
        <p className="muted" style={{ margin: 0, fontSize: 12 }}>
          У поставщика запрошено позиций: {asked.length} из {matrix.lines.filter((line) => !line.cancelled).length} — остальные в КП не вносятся.
        </p>
      )}
      {!supplier && (
        <p className="muted" style={{ margin: 0, fontSize: 12 }}>Поставщика нет в запросе — он добавится в запрос с этим КП (ответил — значит, запрошен).</p>
      )}
      {unmatched.length > 0 && (
        <div className="warning-inline">Не сопоставлено со строками запроса: {unmatched.map((row) => row.source_name || row.source_article).join("; ")}</div>
      )}
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="inwork-scroll" style={{ maxHeight: "50vh" }}>
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
              const row = rows[line.id];
              const tiersBad = !row.declined && row.tiers.trim() !== "" && parseTiers(row.tiers) === null;
              return (
                <tr key={line.id}>
                  <td style={{ whiteSpace: "normal", minWidth: 200 }}><strong>{line.name}</strong> <small className="muted">{line.article ?? ""}</small></td>
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
      <div className="form-actions">
        <span className="muted" style={{ marginRight: "auto", fontSize: 12 }}>Пустая строка без отказа — позиция не предложена. Повторное КП заменит прежнее по этим позициям.</span>
        <button type="button" className="secondary" onClick={onClose}>Отмена</button>
        <button type="button" className="primary" disabled={busy || (!supplier && !supplierName.trim())} onClick={() => void save()}>Сохранить КП</button>
      </div>
    </Dialog>
  );
}
