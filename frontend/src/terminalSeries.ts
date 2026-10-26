import type { ConnectorSeries } from "./api";
import type { TerminalRow } from "./TerminalCard";
import { awgToMm2, compareNumbers, firstNumber } from "./refSort";
import { searchKey } from "./searchText";

/* «База данных → Терминалы» по сериям (владелец 16.10.2026: «в терминалах надо сделать группировку по серии, а не по
   производителю»). Серия контакта — семейство разъёмов, к которому он идёт (JST SH, Molex Micro-Fit 3.0, TE Superseal
   1.5…), как её видит «База данных → Разъёмы»: сервер (techcards/connector_series.py `terminals_for`) уже раскладывает
   артикулы БД.ТЕР по сериям каталога. Чего каталог не узнал:
   1) строка с той же «Серией» листа (тот же класс наконечника КВТ — НШВИ, ПМ, РПИ-П…) идёт в серию соседей;
   2) иначе — своя серия по колонке «Серия»: производитель + первое название («JST JWPF», «KST FDFN1-250»);
   3) в «Серии» нет названия (пусто, только номер корпуса или размер) — группа «Без серии», в самом конце. */

export const NO_SERIES = "Без серии";

export interface TerminalGroup {
  /** Имя группы: «JST SH», «Molex Micro-Fit 3.0», «Наконечники и гильзы». */
  name: string;
  /** Производитель серии (пусто у сборных групп вроде «Наконечники и гильзы» — там свои у каждой строки). */
  maker: string;
}

const clean = (value?: string) => {
  const text = String(value ?? "").trim();
  return /^[-–—]$/.test(text) || /^н\/д/i.test(text) ? "" : text;
};
// Поле записи: карточка контакта с сервера, ключ схемы (terSeries…) или заголовок листа («Series», «Артикул контакта (REEL)»).
function field(row: TerminalRow, schemaKey: string, header: (key: string) => boolean): string {
  if (row.fields[schemaKey]) return row.fields[schemaKey];
  const found = Object.keys(row.fields).find((key) => header(key.replace(/\s+/g, " ").trim().toLowerCase()));
  return found ? row.fields[found] ?? "" : "";
}
export const terminalMaker = (row: TerminalRow) => clean(row.card?.maker
  || field(row, "terManufacturer", (key) => key === "производитель"));
const seriesText = (row: TerminalRow) => clean(row.card?.series
  || field(row, "terSeries", (key) => key === "series" || key.startsWith("серия")));
export const terminalArticle = (row: TerminalRow) => clean(row.card?.article
  || field(row, "terArticle", (key) => key.startsWith("артикул контакта") && !key.includes("bag")));

// Класс наконечника по-русски в начале «Серии»: «НШВИ 1.5» → НШВИ, «РПИ-М(н) (6.3)» → РПИ-М, «РППи-м 1.5-(6.3)» → РППИ-М.
const LUG_CLASS = /^([А-ЯЁа-яё]+(?:-[А-ЯЁа-яё]+)?)/u;

/** Ключ «та же серия листа» для строк, которых каталог не узнал. */
function siblingKey(series: string): string {
  const lug = LUG_CLASS.exec(series);
  return lug ? `ru:${lug[1].toLocaleUpperCase("ru-RU")}` : series ? `sheet:${searchKey(series)}` : "";
}

/** Своя серия по колонке «Серия»: первое название, если оно начинается с буквы (не номер корпуса Molex и не «2,8мм»). */
function ownSeries(row: TerminalRow): TerminalGroup | null {
  const series = seriesText(row);
  const maker = terminalMaker(row);
  const lug = LUG_CLASS.exec(series);
  const first = lug ? lug[1].toLocaleUpperCase("ru-RU") : series.split(/[,;]/)[0].trim();
  if (!first || !/^\p{L}/u.test(first)) return null;
  const name = maker && !first.toLowerCase().startsWith(maker.toLowerCase()) ? `${maker} ${first}` : first;
  return { name, maker };
}

/** Короткое имя серии каталога как ключ: «JST PHD» → «phd», «Molex Micro-Fit 3.0» → «micro». */
function shortName(item: ConnectorSeries): string {
  const title = item.maker && item.title.startsWith(`${item.maker} `) ? item.title.slice(item.maker.length + 1) : item.title;
  return searchKey(title.split(/[\s(]/u)[0]);
}

const firstName = (row: TerminalRow) => searchKey(seriesText(row).split(/[,;]/)[0]);

/** Серии, чьё короткое имя начинает «Серию» строки; самое длинное совпадение первым («PHDR» — JST PHD, а не JST PH). */
function byPrefix(candidates: ConnectorSeries[], row: TerminalRow): ConnectorSeries[] {
  const first = firstName(row);
  return candidates.filter((item) => first && shortName(item).length >= 2 && first.startsWith(shortName(item)))
    .sort((a, b) => shortName(b).length - shortName(a).length);
}

/** Из нескольких серий каталога — та, чьё короткое имя начинает «Серию» строки («SHDP, SHR…» → JST SH, не JST LBT). */
function pick(candidates: ConnectorSeries[], row: TerminalRow): ConnectorSeries {
  return candidates.length === 1 ? candidates[0] : byPrefix(candidates, row)[0] ?? candidates[0];
}

/** Серия каждой строки «Терминалов» (по id записи). */
export function terminalGroups(rows: TerminalRow[], series: ConnectorSeries[]): Map<string, TerminalGroup> {
  const byArticle = new Map<string, ConnectorSeries[]>();
  for (const item of series) {
    for (const terminal of item.terminals) {
      const key = searchKey(terminal.article);
      if (!key) continue;
      const list = byArticle.get(key) ?? [];
      if (!list.includes(item)) list.push(item);
      byArticle.set(key, list);
    }
  }
  const out = new Map<string, TerminalGroup>();
  // Каталог: артикул контакта → серия.
  const votes = new Map<string, Map<string, { group: TerminalGroup; count: number }>>();
  for (const row of rows) {
    const found = byArticle.get(searchKey(terminalArticle(row)));
    if (!found) continue;
    const item = pick(found, row);
    const group = { name: item.title, maker: item.maker };
    out.set(row.id, group);
    const key = siblingKey(seriesText(row));
    if (!key) continue;
    const tally = votes.get(key) ?? new Map();
    const entry = tally.get(group.name) ?? { group, count: 0 };
    entry.count += 1;
    tally.set(group.name, entry);
    votes.set(key, tally);
  }
  for (const row of rows) {
    if (out.has(row.id)) continue;
    const tally = votes.get(siblingKey(seriesText(row)));
    const sibling = tally ? [...tally.values()].sort((a, b) => b.count - a.count)[0]?.group : undefined;
    // Серия каталога того же производителя по имени («PAP» — корпус серии JST PA).
    const maker = terminalMaker(row).toLowerCase();
    const named = maker ? byPrefix(series.filter((item) => item.maker.toLowerCase() === maker), row)[0] : undefined;
    out.set(row.id, sibling ?? (named && { name: named.title, maker: named.maker }) ?? ownSeries(row) ?? { name: NO_SERIES, maker: "" });
  }
  return out;
}

// Латиница раньше кириллицы, числа — как числа: «JST GH» < «JST PH» < «Molex Micro-Fit 3.0» < «Наконечники и гильзы».
const natural = (a: string, b: string) => a.localeCompare(b, "en", { numeric: true, sensitivity: "base" });

/** Производители в порядке каталога «Разъёмы» (сервер сортирует серии по MAKERS: JST, Molex, Hirose, JAE, TE, Connfly…). */
export const makerOrder = (series: ConnectorSeries[]): string[] => [...new Set(series.map((item) => item.maker).filter(Boolean))];

/** Порядок строк (владелец 16.10: «реши» — JST, Molex, TE первыми): группы по производителю в порядке каталога, затем
 *  остальные производители и сборные группы, внутри производителя — по имени серии; «Без серии» в конце; внутри — по
 *  наименьшему сечению провода контакта, затем по артикулу (владелец 25.10.2026: «размер, затем наименование»). */
/** The smallest wire cross-section of the contact's range, mm² (its `мм²` pair; else the thinnest AWG of its range). */
export function terminalMinSection(row: TerminalRow): number | null {
  const sections = [row.card?.mm2_from, row.card?.mm2_to].map(firstNumber).filter((value): value is number => value !== null);
  if (sections.length) return Math.min(...sections);
  const gauges = [row.card?.awg_from, row.card?.awg_to].map(firstNumber).filter((value): value is number => value !== null);
  return gauges.length ? awgToMm2(Math.max(...gauges)) : null;
}

export function sortBySeries(rows: TerminalRow[], groups: Map<string, TerminalGroup>, makers: string[] = []): TerminalRow[] {
  const name = (row: TerminalRow) => groups.get(row.id)?.name ?? NO_SERIES;
  const rank = (row: TerminalRow) => {
    const index = makers.indexOf(groups.get(row.id)?.maker ?? "");
    return index >= 0 ? index : makers.length;
  };
  return [...rows].sort((a, b) => Number(name(a) === NO_SERIES) - Number(name(b) === NO_SERIES)
    || rank(a) - rank(b)
    || natural(name(a), name(b))
    || compareNumbers(terminalMinSection(a), terminalMinSection(b))
    || Number(!terminalArticle(a)) - Number(!terminalArticle(b))
    || natural(terminalArticle(a), terminalArticle(b)));
}

/** Подпись группы: имя серии и производители строк, которых в имени нет («Наконечники и гильзы — КВТ, Копир»). */
export function seriesCaption(name: string, rows: TerminalRow[]): string {
  const title = name.toLocaleLowerCase("ru-RU");
  const makers = [...new Set(rows.map(terminalMaker).filter(Boolean))]
    .filter((maker) => !title.includes(maker.split(/\s+/)[0].toLocaleLowerCase("ru-RU")));
  return makers.length ? `${name} — ${makers.join(", ")}` : name;
}
