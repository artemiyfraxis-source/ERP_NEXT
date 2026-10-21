import { useState } from "react";
import { failureText } from "./api";
import {
  rfqApi,
  type ImportHeader,
  type ImportedQuoteLine,
  type PasteColumn,
  type PastePreview,
  type RfqLine,
} from "./purchasingRfqApi";

/** Quote import (Roller_Buyer QuoteImport, without an LLM): rows pasted from Excel — the columns guessed from the header
 *  and re-mapped by the buyer, «цены с НДС» ticked or not — or the text / PDF text layer of a quote. Every imported row
 *  ends matched to a request line or explicitly skipped — nothing is dropped silently — and only then fills the form.
 *  The header found in the text («№», «действует до», the seller's ИНН) comes along; a PDF is kept with the quote. */

export const COLUMN_KEYS: PasteColumn[] = ["name", "article", "qty", "unit", "price", "vat", "lead"];
const COLUMN_LABELS: Record<PasteColumn, string> = {
  name: "Наименование", article: "Артикул", qty: "Кол-во", unit: "Ед.", price: "Цена", vat: "НДС", lead: "Срок",
};
const MATCH_LABELS: Record<string, string> = { alias: "как в прошлый раз", article: "по артикулу", name: "по названию" };

export type Choice = string | "skip" | null;

/** What the import hands to the quote form. */
export type ImportResult = {
  byLine: Map<string, ImportedQuoteLine>;
  vatIncluded: boolean;
  source: "paste" | "file";
  header: ImportHeader | null;
  /** the PDF, sent again with the quote to be kept with it */
  file: { name: string; base64: string } | null;
  /** the supplier's wording of each line, remembered when the quote is saved */
  aliases: { rfq_line_id: string; text: string }[];
  notes: string[];
  unread: string[];
};

type Review = {
  rows: ImportedQuoteLine[]; choices: Choice[]; vatIncluded: boolean; source: "paste" | "file"; header: ImportHeader | null;
  file: { name: string; base64: string } | null; unread: string[];
};

const wordingOf = (row: ImportedQuoteLine) => `${row.source_article} ${row.source_name}`.trim();

/** The rows the buyer kept, by request line, with the wordings to remember (not those that came from memory). */
export function applyChoices(rows: ImportedQuoteLine[], choices: Choice[]): Pick<ImportResult, "byLine" | "aliases" | "notes"> {
  const byLine = new Map<string, ImportedQuoteLine>();
  const aliases: { rfq_line_id: string; text: string }[] = [];
  const notes: string[] = [];
  rows.forEach((row, index) => {
    const choice = choices[index];
    if (!choice || choice === "skip") return;
    byLine.set(choice, row);
    if (row.matched_by !== "alias" || row.rfq_line_id !== choice) aliases.push({ rfq_line_id: choice, text: wordingOf(row) });
    if (row.pack_note) notes.push(`«${row.source_name || row.source_article}»: ${row.pack_note}`);
  });
  return { byLine, aliases, notes };
}

export function RfqQuoteImport({ rfqId, supplierId, lines, importEnabled, onApply }: {
  rfqId: string; supplierId: string | undefined; lines: RfqLine[]; importEnabled: boolean; onApply: (result: ImportResult) => void;
}) {
  const [mode, setMode] = useState<"none" | "paste" | "text">("none");
  const [pasteText, setPasteText] = useState("");
  // «цены с НДС»: read from the price column's title until the buyer says otherwise.
  const [vatIncluded, setVatIncluded] = useState(true);
  const [vatTouched, setVatTouched] = useState(false);
  const [paste, setPaste] = useState<PastePreview | null>(null);
  const [importText, setImportText] = useState("");
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function parsePaste(columns?: Record<string, number>) {
    setBusy(true);
    setError("");
    try {
      const result = await rfqApi.paste(rfqId, pasteText, supplierId, { ...(vatTouched ? { vat_included: vatIncluded } : {}), ...(columns ? { columns } : {}) });
      setPaste(result);
      setVatIncluded(result.vat_included);
      setReview({ rows: result.rows, choices: result.rows.map((row) => row.rfq_line_id), vatIncluded: result.vat_included,
        source: "paste", header: null, file: null, unread: [] });
    } catch (reason) {
      setError(failureText(reason, "Вставка не разобрана"));
    } finally {
      setBusy(false);
    }
  }

  async function parseText(input: { text: string } | { file_name: string; content_base64: string }) {
    setBusy(true);
    setError("");
    try {
      const result = await rfqApi.importQuote(rfqId, supplierId ?? null, input);
      const rows = [...result.lines, ...result.unmatched];
      setReview({
        rows, choices: rows.map((row) => row.rfq_line_id), vatIncluded: result.vat_included, source: result.source,
        header: result.header ?? null, unread: result.unread,
        file: "content_base64" in input ? { name: input.file_name, base64: input.content_base64 } : null,
      });
    } catch (reason) {
      setError(failureText(reason, "КП не разобрано"));
    } finally {
      setBusy(false);
    }
  }

  function readFile(file: File | undefined) {
    if (!file) return;
    if (file.size > 20 * 1024 * 1024) {
      setError("PDF больше 20 МБ — сожмите файл или вставьте текст");
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => setError("Файл не прочитан");
    reader.onload = () => {
      const data = String(reader.result ?? "");
      void parseText({ file_name: file.name, content_base64: data.slice(data.indexOf(",") + 1) });
    };
    reader.readAsDataURL(file);
  }

  function apply() {
    if (!review) return;
    onApply({ ...applyChoices(review.rows, review.choices), vatIncluded: review.vatIncluded, source: review.source,
      header: review.header, file: review.file, unread: review.unread });
    setReview(null);
    setPaste(null);
    setMode("none");
    setPasteText("");
    setImportText("");
  }

  const width = paste ? Math.max(paste.width ?? 0, paste.header?.length ?? 0) : 0;
  const columnName = (index: number) => (paste?.header?.[index] ? `${index + 1}. ${paste.header[index]}` : `Столбец ${index + 1}`);
  const taken = new Set((review?.choices ?? []).filter((choice): choice is string => Boolean(choice) && choice !== "skip"));
  const undecided = (review?.choices ?? []).filter((choice) => choice === null).length;
  const setChoice = (index: number, value: string) => setReview((current) => current && {
    ...current, choices: current.choices.map((choice, at) => (at === index ? (value === "skip" ? "skip" : value === "" ? null : value) : choice)),
  });

  return (
    <div className="quote-import">
      <div className="rfq-quote-import-bar">
        <button type="button" className="secondary small" aria-expanded={mode === "paste"} onClick={() => setMode(mode === "paste" ? "none" : "paste")}>Вставить из Excel</button>
        {importEnabled && (
          <button type="button" className="secondary small" aria-expanded={mode === "text"} onClick={() => setMode(mode === "text" ? "none" : "text")}>Вставить из PDF / текста</button>
        )}
        <span className="muted">Или заполните таблицу ниже вручную.</span>
      </div>
      {mode === "paste" && (
        <div className="quote-import-panel">
          <textarea
            aria-label="Таблица из Excel" rows={5} value={pasteText} onChange={(event) => setPasteText(event.target.value)}
            placeholder="Скопируйте строки таблицы в Excel (можно с заголовком) и вставьте сюда"
          />
          <div className="quote-import-actions">
            <label className="rfq-basket-check"><input type="checkbox" checked={vatIncluded} onChange={(event) => { setVatIncluded(event.target.checked); setVatTouched(true); }} /> цены с НДС</label>
            <button type="button" className="secondary small" disabled={!pasteText.trim() || busy} onClick={() => void parsePaste()}>Разобрать</button>
          </div>
          {paste && (
            <div className="quote-import-columns" role="group" aria-label="Столбцы вставки">
              {COLUMN_KEYS.map((key) => (
                <label key={key}>
                  <span className="muted">{paste.column_labels?.[key] ?? COLUMN_LABELS[key]}</span>
                  <select aria-label={`Столбец «${COLUMN_LABELS[key]}»`} value={paste.columns[key] ?? -1}
                    onChange={(event) => void parsePaste({ ...paste.columns, [key]: Number(event.target.value) })}>
                    <option value={-1}>—</option>
                    {Array.from({ length: width }, (_, index) => <option key={index} value={index}>{columnName(index)}</option>)}
                  </select>
                </label>
              ))}
            </div>
          )}
        </div>
      )}
      {mode === "text" && importEnabled && (
        <div className="quote-import-panel">
          <textarea
            aria-label="Текст КП" rows={6} value={importText} onChange={(event) => setImportText(event.target.value)}
            placeholder="Вставьте текст письма или КП: в каждой строке наименование, количество, цена и сумма (единица и срок — если есть)"
          />
          <div className="quote-import-actions">
            <button type="button" className="secondary small" disabled={!importText.trim() || busy} onClick={() => void parseText({ text: importText })}>Разобрать текст</button>
            <label className="quote-import-file">или PDF с текстовым слоем (до 20 МБ) — файл сохранится вместе с КП
              <input type="file" accept="application/pdf,.pdf" aria-label="PDF с КП" disabled={busy} onChange={(event) => { readFile(event.target.files?.[0]); event.target.value = ""; }} />
            </label>
          </div>
        </div>
      )}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {review && (
        <div className="quote-import-review" role="region" aria-label="Проверка импорта">
          <div className="muted">
            Разобрано строк: {review.rows.length}{review.vatIncluded ? " · цены с НДС" : " · цены без НДС"}
            {review.header?.inn ? ` · ИНН в документе: ${review.header.inn}${review.header.supplier_name ? ` (${review.header.supplier_name})` : " — в справочнике нет"}` : ""}
            {review.header?.number ? ` · № ${review.header.number}` : ""}
            {review.header?.valid_until ? ` · действует до ${review.header.valid_until.split("-").reverse().join(".")}` : ""}
            {review.file ? ` · файл ${review.file.name}` : ""}
          </div>
          <div className="inwork-scroll" style={{ maxHeight: "34vh" }}>
            <table className="inwork-table">
              <thead><tr><th>Строка предложения</th><th>Кол-во</th><th>Цена</th><th>Срок</th><th>Позиция запроса</th></tr></thead>
              <tbody>
                {review.rows.map((row, index) => {
                  const choice = review.choices[index];
                  return (
                    <tr key={`${row.source_name}-${index}`} className={choice === null ? "quote-import-undecided" : undefined}>
                      <td style={{ whiteSpace: "normal", minWidth: 220 }}>
                        {row.source_name}{row.source_article ? <small className="muted"> {row.source_article}</small> : null}
                        {row.analog ? <small className="muted"> · аналог{row.analog_note ? `: ${row.analog_note}` : ""}</small> : null}
                        {row.pack_note ? <small className="muted" style={{ display: "block" }}>{row.pack_note}</small> : null}
                      </td>
                      <td>{row.qty ?? "—"} {row.unit}</td>
                      <td>{row.declined ? "отказ" : row.price ?? "—"}</td>
                      <td>{row.in_stock ? "в наличии" : row.lead_days !== null ? `${row.lead_days} дн` : "—"}</td>
                      <td>
                        <select aria-label={`Позиция для «${row.source_name || row.source_article}»`} value={choice ?? ""} onChange={(event) => setChoice(index, event.target.value)}>
                          <option value="">выберите…</option>
                          <option value="skip">— пропустить строку —</option>
                          {lines.map((line) => (
                            <option key={line.id} value={line.id} disabled={taken.has(line.id) && choice !== line.id}>{line.name}</option>
                          ))}
                        </select>
                        {row.matched_by && choice === row.rfq_line_id ? <small className="muted"> {MATCH_LABELS[row.matched_by]}</small> : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {review.unread.length > 0 && <div className="warning-inline">Строки с ценой, которые не удалось прочитать: {review.unread.join("; ")}</div>}
          <div className="quote-import-actions">
            {undecided > 0 && <span className="tone-warn">Не решено строк: {undecided} — выберите позицию или «пропустить».</span>}
            <span style={{ flex: 1 }} />
            <button type="button" className="secondary small" onClick={() => setReview(null)}>Отменить импорт</button>
            <button type="button" className="primary small" disabled={undecided > 0} onClick={apply}>Перенести в КП</button>
          </div>
        </div>
      )}
    </div>
  );
}
