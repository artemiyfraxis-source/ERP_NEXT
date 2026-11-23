// Данные операции ЭТК, которых нет отдельными полями в карточке операции (SUPPLYMAP БД.ОП-ТК / БД.ШАГИ / БД.ОП-ОБ):
// режим пайки (жало и температура — в шаблонах они только в тексте шагов) и типовые дефекты, разобранные из
// текста в список (рисунки «Норма | Брак» — etkDefectArt.ts). Считается при показе и печати, поэтому выданные ТК перевыпускать не нужно.

import { opTypeName } from "./tcGenLogic";
import { countLabel, formatNumber } from "./format";
import { markLabelList, type MarkSerialRange } from "./markSerial";

type Row = Record<string, unknown>;

function obj(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

function list(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") as Row[] : [];
}

function str(value: unknown): string {
  return value == null ? "" : String(value);
}

/** Preserve the stored norm: blank is unknown, while an explicit zero is a known value. */
export function etkTimeRows(operation: Row): Array<{ name: string; norm: string }> {
  return list(operation.time).map((row) => ({
    name: str(row.name),
    norm: str(row.norm),
  }));
}

/** Correct the label-print output when reading an issued ETK, without rewriting its snapshot. */
export function etkLabelResult(operation: Row, serial?: MarkSerialRange | null): { name: string; norm: string; unit: string } | null {
  const marks = list(obj(operation.fields).marks);
  if (!marks.length || !(str(operation.code) === "PRINT_MARK_LABEL" || list(operation.result).some((row) => /^Напечатанные этикетки:/.test(str(row.name))))) return null;
  const labels = serial ? markLabelList(marks, serial) : [];
  const count = labels.length || marks.length;
  const total = labels.length ? labels.reduce((sum, label) => sum + label.quantity, 0)
    : marks.reduce((sum, mark) => sum + (Number(str(mark.batch).replace(",", ".")) || 0), 0);
  return { name: `Этикетки: ${countLabel(count, ["вид", "вида", "видов"])}, ${formatNumber(total)} шт.`, norm: String(total), unit: "Шт." };
}

export type ShrinkRequirementLine = { material: string; text: string; missing: boolean; sourceUrl: string; quote: string };

/** Frozen in the issued operation, never looked up from today's material catalog at render time. */
export function shrinkRequirementLines(operation: Row): ShrinkRequirementLine[] {
  const fields = obj(operation.fields);
  const requirements = list(fields.shrinkRequirements);
  if (!("shrinkRequirements" in fields) && (["heatTut", "coaxHeat", "endCap"].includes(str(operation.type)) || /^HEAT_(?:TYT|TTK)_/.test(str(operation.code)))) {
    requirements.push({ status: "missing" });
  }
  const number = (value: number) => String(value).replace(".", ",");
  const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
  const lines = requirements.map((requirement): ShrinkRequirementLine => {
    const material = obj(requirement.material), temperature = obj(requirement.temperature);
    const low = temperature.lower_c, high = temperature.upper_c;
    const missing = requirement.status !== "resolved" || (!finite(low) && !finite(high));
    const value = missing ? "не задано — проверить" : finite(low) && finite(high)
      ? (low === high ? number(low) : `${number(low)}…${number(high)}`) + " °C"
      : finite(low) ? `не ниже ${number(low)} °C` : `не выше ${number(high as number)} °C`;
    const source = [str(temperature.kind), str(temperature.document), temperature.page ? `стр. ${str(temperature.page)}` : ""].filter(Boolean).join(", ");
    const characteristic = finite(temperature.characteristic_c) ? `; по характеристикам: ${number(temperature.characteristic_c)} °C` : "";
    return {
      material: [...new Set([str(material.article), str(material.name)].filter(Boolean))].join(" · ") || "Материал не определён",
      text: `Температура усадки материала: ${str(requirement.display_text) || `${value}${!missing && source ? ` (${source})` : ""}${!missing ? characteristic : ""}`}`,
      missing, sourceUrl: /^https?:\/\//i.test(str(temperature.source_url)) ? str(temperature.source_url) : "",
      quote: str(temperature.quote),
    };
  });
  if (fields.shrinkReview) lines.push({ material: "Проверка режима", text: str(fields.shrinkReview), missing: true, sourceUrl: "", quote: "" });
  return lines;
}

/** Порядок разделов оснастки: инструмент → оборудование → расходники, затем прочее (SUPPLYMAP PortalApp:4533). */
export const TOOLING_ORDER = ["Инструмент", "Оборудование", "Расходники", "Матрицы", "Оснастка"];

/** Разделы блока «Оснастка» операции ЭТК, которые показываются всегда, даже пустыми (erp-screens §6, аудит 10.10 ЭТК-1). */
export const ETK_TOOLING_SECTIONS = ["Инструмент", "Оборудование", "Расходники"] as const;

export function toolingSections(sections: string[]): string[] {
  const rank = (section: string) => {
    const index = TOOLING_ORDER.indexOf(section);
    return index < 0 ? TOOLING_ORDER.length : index;
  };
  return [...new Set(sections)].sort((a, b) => rank(a) - rank(b));
}

export type SolderMode = {
  method: "iron" | "bath" | "induction";
  tip: string;
  temperature: string;
  tolerance: string;
  /** true — значение не из карты операции, а по умолчанию ERP (надо уточнить технологу). */
  tipAssumed: boolean;
  temperatureAssumed: boolean;
};

const TIP_CLASSES: Array<[RegExp, string]> = [
  [/(особо|очень)\s+тонк|прецизионн/i, "Очень тонкое конусное, Ø 0,4–0,8 мм"],
  [/тонк/i, "Тонкое конусное / микроклин, 1,0–1,6 мм"],
  [/массивн|широк/i, "Массивное клиновидное (долото), 3,2–5,0 мм"],
  [/клин|конус/i, "Клиновидное, 2,4–3,2 мм"],
];

function isSoldering(operation: Row): boolean {
  const card = obj(operation.card);
  const names = `${str(operation.title)} ${str(card.name)} ${str(operation.type)}`;
  return /^SDR/i.test(str(operation.code)) || /пайк|лужен/i.test(names);
}

/** Температура пайки и лужения по умолчанию (владелец 17.10: 240 °C, допуск ±10 °C). */
export const SOLDER_DEFAULT_TEMPERATURE = "240 °C";

/** Режим пайки операции: жало паяльника и температура. Сначала — параметры оборудования и текст шагов карты
 *  (параметры/программа ванны «200 °C»; в шагах «Паяльная станция, тонкое жало; t° 320–350 °C», допуск «(+/-) 10 °C»); чего в карте нет — по умолчанию
 *  для Sn63Pb37: паяльник 240 °C, ванна лужения — те же 240 °C (владелец 17.10: «на пайке 240»). */
export function solderMode(operation: Row): SolderMode | null {
  if (!isSoldering(operation)) return null;
  const card = obj(operation.card);
  const steps = list(card.steps);
  const tooling = list(card.tooling);
  const equipment = tooling.filter((tool) => str(tool.section) === "Оборудование");
  const equipmentNames = equipment.map((tool) => str(tool.name)).join(" ");
  // Станок резки с модулем лужения (BZW-5.0+Z «обжим+лужение») лудит погружением, как ванна.
  const method: SolderMode["method"] = /ванн|лужени/i.test(equipmentNames) ? "bath" : /индукц/i.test(equipmentNames) ? "induction" : "iron";
  const stepText = steps.map((step) => str(step.text)).join("\n");
  const sources = [...equipment.map((tool) => `${str(tool.parameters)} ${str(tool.program)}`), stepText];
  let temperature = "";
  for (const source of sources) {
    const match = source.match(/(\d{3})\s*[–—-]\s*(\d{3})\s*°\s*[CС]/i) || source.match(/(\d{3})\s*°\s*[CС]/i);
    if (match) { temperature = match[2] ? `${match[1]}–${match[2]} °C` : `${match[1]} °C`; break; }
  }
  const toleranceMatch = steps.map((step) => str(step.tolerance)).join(" ").match(/(?:\(\+\/-\)|±|\+\/-)\s*(\d+)\s*°/);
  const tolerance = toleranceMatch ? `±${toleranceMatch[1]} °C` : "±10 °C";
  const tipLine = stepText.split("\n").find((line) => /жал/i.test(line) && TIP_CLASSES.some(([pattern]) => pattern.test(line.split(/жал/i)[0])));
  if (method === "bath") {
    return { method, tip: "Не требуется — лужение погружением в ванну", temperature: temperature || SOLDER_DEFAULT_TEMPERATURE, tolerance,
      tipAssumed: false, temperatureAssumed: !temperature };
  }
  if (method === "induction") {
    return { method, tip: "Не требуется — индукционный нагрев", temperature: temperature || "По программе установки, до растекания припоя",
      tolerance: temperature ? tolerance : "", tipAssumed: false, temperatureAssumed: !temperature };
  }
  const upper = Number((temperature.match(/(\d{3})\s*°/) || [])[1] || 0);
  const tipFromCard = tipLine ? TIP_CLASSES.find(([pattern]) => pattern.test(tipLine.split(/жал/i)[0]))?.[1] || "" : "";
  const tip = tipFromCard || (upper >= 370 ? TIP_CLASSES[2][1] : upper >= 350 ? TIP_CLASSES[3][1] : "Клиновидное, 1,6–2,4 мм");
  return { method, tip, temperature: temperature || SOLDER_DEFAULT_TEMPERATURE, tolerance,
    tipAssumed: !tipFromCard, temperatureAssumed: !temperature };
}

export type DefectItem = { name: string; text: string };
export type DefectGroup = { title: string; items: DefectItem[] };
export type Defects = { intro: string[]; groups: DefectGroup[] };

/** Пункт «4.3.1 Название» из таблицы дефектов СТО ФРАК-001-2026 (код строки — три и более числа через точку). */
export const STO_ROW = /^\d+\.\d+(?:\.\d+)+\s/;
const STO_SPLIT = /;\s*(?=\d+\.\d+(?:\.\d+)+\s)/;

const EXPLAINS = /^(Приводит|Это|Признак|Допустим|Снижает|Возника|Происходит|Характерн|Причин|Вед[её]т|Влеч[её]т|Нарушает|При |Так как|Что )/;

function listItem(fragment: string): DefectItem | null {
  const clean = fragment.trim().replace(/[.;]\s*$/, "");
  if (clean.length < 2) return null;
  const upper = clean[0].toUpperCase() + clean.slice(1);
  const paren = upper.match(/^(.+?)\s*\(([^()]+)\)$/);
  const name = paren ? paren[1] : upper;
  const text = paren ? paren[2][0].toUpperCase() + paren[2].slice(1) : "";
  return { name, text };
}

/** Текст «Типовые дефекты» из карты операции → группы с пунктами. Форматы шаблонов SUPPLYMAP (БД.ОП-ТК):
 *  «1. Группа:» + строки «Название: признак» (бывают склеены без перевода строки — «…ТУТ.Недолив припоя: …»),
 *  группы без номера «Термические дефекты (Раздел 4):», строка-перечень «Типовые дефекты (NG): а; б (признак); в.»
 *  и строки-предложения без двоеточия «Надрезы, царапины или обрыв проволочек жилы.». */
export function parseDefects(raw: unknown): Defects {
  const text = str(raw).replace(/\r/g, "").split("\n")
    .map((row) => (STO_ROW.test(row.trim()) ? row : row.replace(/([.)»:])(?=[А-ЯЁA-Z«][^.:;\n]{1,70}:)/g, "$1\n"))).join("\n");
  const intro: string[] = [];
  const groups: DefectGroup[] = [];
  const current = () => {
    if (!groups.length) groups.push({ title: "", items: [] });
    return groups[groups.length - 1];
  };
  const pushList = (rest: string) => rest.split(";").map(listItem).forEach((item) => item && current().items.push(item));
  for (const source of text.split("\n")) {
    const line = source.trim().replace(/^[-•*–]\s*/, "");
    if (!line) continue;
    // Хвост карточек по СТО ФРАК-001-2026 («Критерии, метод контроля и действие — по СТО…; при расхождении…») — не дефект.
    if (/^критерии, метод контроля/i.test(line)) {
      const tail = line.replace(/[.:]\s*$/, "");
      if (!intro.includes(tail)) intro.push(tail);  // совмещённая карта: хвост СТО у каждой части — показать один раз
      continue;
    }
    // Строки СТО «4.3.1 Название; 4.3.2 Название»: каждый пункт со своим кодом строки; двоеточие внутри названия не режет.
    if (STO_ROW.test(line)) {
      for (const part of line.split(STO_SPLIT)) {
        const name = part.trim().replace(/[.;]\s*$/, "");
        if (name) current().items.push({ name, text: "" });
      }
      continue;
    }
    if (/^(типовые дефекты|в соответствии|для данной операции)/i.test(line) && (/:\s*$/.test(line) || !line.includes(":"))) {
      intro.push(line.replace(/:\s*$/, ""));
      continue;
    }
    const lead = line.match(/^((?:типовые дефекты|в соответствии)[^:]*):\s*(.*)$/i);
    if (lead) {
      intro.push(lead[1].trim());
      if (lead[2]) pushList(lead[2]);
      continue;
    }
    const group = line.match(/^(?:\d+[.)]\s*)?([^:;]{2,120}?)\s*:\s*$/) || line.match(/^\d+[.)]\s*([^:;]{2,120}?)\s*$/);
    if (group) { groups.push({ title: group[1].trim(), items: [] }); continue; }
    const item = line.replace(/^\d+[.)]\s*/, "").match(/^([^:;]{2,80}?)\s*:\s*(.+)$/);
    if (item) {
      const name = item[1].trim();
      current().items.push({ name, text: item[2].trim() });
      continue;
    }
    const last = groups.length ? groups[groups.length - 1].items.at(-1) : undefined;
    if (last && /^[a-zа-яё]/.test(line)) { last.text = `${last.text} ${line}`.trim(); continue; }
    if (line.includes(";")) { pushList(line); continue; }
    // «Название. Пояснение.» — пояснение, если начинается как следствие; иначе каждое предложение — свой дефект.
    const sentences = line.split(/(?<=[^.\s]{2}\.)\s+(?=[А-ЯЁA-Z«])/);
    const explains = sentences.length > 1 && EXPLAINS.test(sentences[1]);
    const names = explains ? [sentences[0]] : sentences;
    const note = explains ? sentences.slice(1).join(" ") : "";
    for (const sentence of names) {
      const name = sentence.replace(/\.\s*$/, "");
      current().items.push({ name, text: note });
    }
  }
  return { intro, groups: groups.filter((item) => item.items.length) };
}

/** Название операции ЭТК: имя карты, а когда оно только начало названия вида операции генератора («Резка» у резки
 *  кабеля и резки ТУТ, «Монтаж» у ТУТ и втулки экрана, «Пайка» центрального контакта) — полное название вида, иначе
 *  в маршруте коакса две «Резки» и две «Монтаж» подряд (владелец 15.10, ТК КБ1 796К.2). Считается при показе — выданные
 *  ЭТК перевыпускать не нужно. */
export function etkOperationName(cardName: string, type: unknown): string {
  const name = cardName.trim();
  const full = opTypeName(str(type));
  if (!name || full === "Операция") return name;
  return full.length > name.length && full.toLowerCase().startsWith(`${name.toLowerCase()} `) ? full : name;
}

/** Категория карточки по префиксу кода (SUPPLYMAP PortalApp.html TC_REC_CATS_ / tcRecCat_). */
export const RECORD_CATEGORIES: Array<[string, string]> = [
  ["CUT", "Резка"], ["STRIP", "Разделка"], ["TWIST", "Свивка"], ["PRS", "Опрессовка"], ["SDR", "Пайка и лужение"],
  ["INS", "Монтаж"], ["HEAT", "Усадка"], ["US", "УЗ-сварка"], ["MOLD", "Литьё"], ["PLASM", "Литьё"],
];

export function recordCategory(code: string): string {
  const prefix = String(code).split("_")[0];
  return RECORD_CATEGORIES.find(([key]) => key === prefix)?.[1] ?? "Прочее";
}

/** Строка допуска шага: подпись («Сторона В», «Время», «Зазор») и значение («2–3 мм», «≤ 2,0 сек»). */
export type ToleranceLine = { label: string; value: string };

/** Допуск шага строками — каждое значение своей строкой (владелец 17.10: «почему допуски так мелко»): «Допуск:» в начале
 *  лишнее, «(+/-) 10 °C» → «± 10 °C», «0,5 - 1,5 мм» → «0,5–1,5 мм»; части по переносу строки и «; ». Пусто и «-» —
 *  допуска нет. Тот же разбор — на экране и в печати. */
export function etkToleranceLines(raw: unknown): ToleranceLine[] {
  const text = str(raw).trim();
  if (!text || text === "-") return [];
  return text.replace(/^допуск\s*:\s*/i, "").split(/\n|;\s+/).map((part) => part.trim()).filter((part) => part && part !== "-")
    .map((part) => (etkToleranceIsReference(part) ? part : part
      .replace(/\(\s*\+\s*\/\s*-\s*\)|\+\s*\/\s*-/g, "±")
      .replace(/(\d)\s*-\s*(?=\d)/g, "$1–")
      .replace(/\s{2,}/g, " ")))
    .map((part) => {
      const found = part.match(/^([^:\d]{1,40}):\s*(.+)$/);
      return found ? { label: found[1].trim(), value: found[2].trim() } : { label: "", value: part };
    });
}

/** Ссылка на стандарт или КД («IPC/WHMA-A-620F (Раздел 13)», «ГОСТ 23592-96», «Согласно КД») — не размер: цифры в
 *  ней не выделяются. */
export function etkToleranceIsReference(value: string): boolean {
  return /IPC|WHMA|ГОСТ|ОСТ(?![а-яёa-z])|(?<![а-яёa-z])КД(?![а-яёa-z])|Раздел|ТУ(?![а-яёa-z])/i.test(value);
}

/** Значение допуска кусками: числа с единицей и знаком (± 10 °C, 2–3 мм, ≤ 2,0 сек) — `strong`, остальное — текст. */
export function etkToleranceParts(value: string): Array<{ text: string; strong: boolean }> {
  if (etkToleranceIsReference(value)) return [{ text: value, strong: false }];
  const pattern = /(?:[±≤≥<>]\s?)?\d+(?:[.,]\d+)?(?:\s?[–-]\s?\d+(?:[.,]\d+)?)?(?:\s?(?:мм|°\s?[CС]|сек|мин|с|%|Н|кгс|м))?(?![\p{L}\d])/gu;
  const parts: Array<{ text: string; strong: boolean }> = [];
  let at = 0;
  for (const match of value.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > at) parts.push({ text: value.slice(at, start), strong: false });
    parts.push({ text: match[0], strong: true });
    at = start + match[0].length;
  }
  if (at < value.length) parts.push({ text: value.slice(at), strong: false });
  return parts.length ? parts : [{ text: value, strong: false }];
}
