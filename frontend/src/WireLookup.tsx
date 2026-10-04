import { useMemo, useState } from "react";
import type { TechOperationRecord } from "./api";

/** «Подбор провода» над СПР.КАБ · AWG (owner 29.09.2026: «сечение и диаметр в зависимости от типа кабеля UL1061,
    1571…, для сопоставления»). Same match as the ETK generator (SUPPLYMAP TcGenerator.gs wireDiaFromTable_ /
    TcGenInline lookupWireDia_): the mark by occurrence in the article, the row by «NN AWG», else by the ГОСТ section. */

export type WireRow = { awg: string; gost: string; core: string; byMark: Record<string, string> };
export type WireMark = { name: string; norm: string };
type Row = WireRow;
type Mark = WireMark;

const markNorm = (value: string) => value.toLowerCase().replace(/[\s-]/g, "");
const num = (value: string) => { const n = parseFloat(String(value || "").replace(",", ".")); return Number.isFinite(n) ? n : NaN; };
export const shownWireValue = (value: string | undefined) => (value && value.trim() && value.trim() !== "—" ? value.trim() : "");
const shown = shownWireValue;

/** The AWG table (СПР.КАБ · AWG records, keys = sheet header in order) → the marks and rows the generator matches. */
export function parseWireTable(records: TechOperationRecord[]): { marks: Mark[]; rows: Row[] } {
  if (!records.length) return { marks: [], rows: [] };
  const headers = Object.keys(records[0].fields);
  const awgCol = headers.findIndex((h) => h.trim().toLowerCase() === "awg");
  if (awgCol < 0) return { marks: [], rows: [] };
  const gostKey = headers.find((h) => h.trim().toLowerCase() === "гост") ?? "";
  const coreKey = headers[awgCol + 2] ?? "";
  const marks = headers.filter((_, c) => c > awgCol + 2).filter(Boolean).map((name) => ({ name, norm: markNorm(name) }));
  const rows = records.map((record) => ({
    awg: String(record.fields[headers[awgCol]] ?? "").trim(),
    gost: String(record.fields[gostKey] ?? "").trim(),
    core: String(record.fields[coreKey] ?? "").trim(),
    byMark: Object.fromEntries(marks.map((mark) => [mark.norm, String(record.fields[mark.name] ?? "")])),
  })).filter((row) => row.awg);
  return { marks, rows };
}

/** The generator's lookup, returning the matched mark and row (null — the article names no known mark or size). */
export function matchWire(article: string, marks: Mark[], rows: Row[]): { mark: Mark; row: Row | null } | null {
  const s = String(article || "").toLowerCase().replace(/,/g, ".");
  const mark = marks.find((item) => item.norm && markNorm(s).includes(item.norm));
  if (!mark) return null;
  const aw = s.match(/(\d+)\s*awg/);
  let row = aw ? rows.find((item) => item.awg === aw[1]) ?? null : null;
  if (!row) {
    // wireGostKey_: cut the mark out (spaces/hyphens inside it allowed), take the numbers left; first with a Ø for the mark.
    const re = new RegExp(mark.norm.split("").map((ch) => ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("[\\s-]*"));
    const nums = s.replace(re, " ").match(/\d+(?:\.\d+)?/g) ?? [];
    const candidates = nums.map((n) => rows.find((item) => num(item.gost) === Number(n))).filter((item): item is Row => Boolean(item));
    row = candidates.find((item) => num(item.byMark[mark.norm]) > 0) ?? candidates[0] ?? null;
  }
  return { mark, row };
}

export function WireLookup({ records }: { records: TechOperationRecord[] }) {
  const { marks, rows } = useMemo(() => parseWireTable(records), [records]);
  const [mark, setMark] = useState("");
  const [size, setSize] = useState("");
  const [article, setArticle] = useState("");
  if (!marks.length) return null;
  const markItem = marks.find((item) => item.norm === mark);
  const sizeRow = rows.find((row) => row.awg === size);
  const matched = article.trim() ? matchWire(article, marks, rows) : null;
  const result = (row: Row | null | undefined, item: Mark | undefined) => row && item
    ? <div className="wire-result">
      <span><small>AWG</small><b>{row.awg}</b></span>
      <span><small>Сечение ГОСТ, мм²</small><b>{shown(row.gost) || "—"}</b></span>
      <span><small>Ø жилы, мм</small><b>{shown(row.core) || "—"}</b></span>
      <span><small>Ø по изоляции {item.name}, мм</small><b>{shown(row.byMark[item.norm]) || <span className="muted">нет у марки</span>}</b></span>
    </div> : null;
  return <article className="panel wire-lookup">
    <div className="panel-heading"><strong>Подбор провода: тип кабеля + сечение → диаметр</strong></div>
    <div className="form-grid">
      <label>Тип кабеля (марка)<select aria-label="Тип кабеля" value={mark} onChange={(event) => setMark(event.target.value)}>
        <option value="">— выбрать —</option>{marks.map((item) => <option key={item.norm} value={item.norm}>{item.name}</option>)}
      </select></label>
      <label>AWG / сечение<select aria-label="Сечение" value={size} onChange={(event) => setSize(event.target.value)}>
        <option value="">— выбрать —</option>{rows.filter((row) => !markItem || shown(row.byMark[markItem.norm])).map((row) => <option key={row.awg} value={row.awg}>{row.awg} AWG{shown(row.gost) ? ` · ${row.gost} мм²` : ""}</option>)}
      </select></label>
      <label>Как в спецификации техкарты<input aria-label="Артикул провода" placeholder="UL1061 24AWG, МГТФ 0,35, НВ-4 0,5…" value={article} onChange={(event) => setArticle(event.target.value)} /></label>
    </div>
    {result(sizeRow, markItem)}
    {article.trim() && (matched ? (matched.row ? result(matched.row, matched.mark)
      : <div className="warning-inline">Марка {matched.mark.name} найдена, сечение не распознано: укажите «NN AWG» или сечение ГОСТ.</div>)
      : <div className="warning-inline">Марка не распознана: в артикуле нет ни одной из {marks.length} марок таблицы AWG, генератор возьмёт Ø вручную.</div>)}
    <p className="page-sub">Так же сопоставляет генератор ЭТК: марку ищет в артикуле провода, строку — по «NN AWG», иначе по сечению ГОСТ. Сечение ГОСТ → AWG для обжима — из этой же таблицы.</p>
  </article>;
}
