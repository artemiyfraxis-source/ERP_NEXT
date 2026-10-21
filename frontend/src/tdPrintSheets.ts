// Комплект ТД по ЕСТД: листы с основной надписью (ГОСТ 3.1103) из замороженного снимка выпуска. ТЛ (3.1105), ВТД (3.1122),
// МК (3.1118), ОК (3.1407), КЭ, ОК технического контроля (3.1502), ВМ (3.1123), ВО (3.1122), ЛР (Р 2.503). Одна HTML-страница,
// A4 альбомная, каждый лист в рамке с полем подшивки 20 мм. Типовые дефекты в комплект не входят — только ссылка на СТО.
import { etkDrawHarness, etkFigure, etkParams, type EtkTopologyData } from "./etkDraw";
import { etkLinkScheme, etkLinkSchemeMarkup } from "./etkLinkScheme";
import { etkToleranceLines } from "./etkOperation";
import { etkOperationTitle, operationKeyOf } from "./etkPrint";
import { unitLabel } from "./format";
import { tdOpGrade, type TdOpGrade, type TdPrintModel, type TdSignatory, type TdStoRef } from "./tdTypes";

type Row = Record<string, unknown>;

const ORGANISATION = "ООО «ЗЭП «ФРАКСИС»";
const REFERENCE_DOCS = ["ГОСТ 23586-96", "ГОСТ 23587-96", "ГОСТ 23585-79", "ГОСТ Р 72182.2-2025"];

export function esc(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

/** Внутренние коды видов операций («CUT_WIRE_auto», «CUT_WIRE+1crimp») в печать не попадают. */
export function cleanCodes(value: string): string {
  return value.replace(/\b[A-Za-z]+_[A-Za-z0-9_]*(\+[A-Za-z0-9_]+)*/g, "").replace(/^[\s|·—-]+/, "").replace(/[ \t]{2,}/g, " ").trim();
}

function text(value: unknown): string { return value == null ? "" : String(value); }
function rows(value: unknown): Row[] { return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") as Row[] : []; }
function object(value: unknown): Row { return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {}; }
function num(value: unknown): number { return Number(text(value).replace(",", ".").replace(/[^\d.\-]/g, "")) || 0; }

export function formatDate(iso: string | null | undefined): string {
  const match = text(iso).match(/^(\d{4})-(\d\d)-(\d\d)/);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : "";
}

const minutes = (seconds: unknown): string => (num(seconds) / 60).toFixed(2).replace(".", ",");
const operationNumber = (index: number): string => String((index + 1) * 5).padStart(3, "0");

// ---- таблицы с переносом по листам ----

interface Col { h: string; mm: number }
interface TRow { cells: string[]; cls?: string }

const BUDGET_MM = 146;
const LINE_MM = 3.7;

function lines(cell: string, mm: number): number {
  const chars = Math.max(3, Math.floor((mm - 2) / 1.75));
  return cell.split("\n").reduce((sum, part) => sum + Math.max(1, Math.ceil(part.length / chars)), 0);
}
function rowMm(row: TRow, cols: Col[]): number {
  return Math.max(...row.cells.map((cell, index) => lines(cell, cols[index]?.mm ?? 20))) * LINE_MM + 1.4;
}

/** Строки по листам: на лист — не больше BUDGET_MM высоты (минус шапка таблицы и `reserved` под заголовок). */
function chunk(cols: Col[], body: TRow[], reserved = 0): TRow[][] {
  const head = Math.max(...cols.map((col) => lines(col.h, col.mm))) * LINE_MM + 1.4;
  const out: TRow[][] = [[]];
  let used = head + reserved;
  body.forEach((row) => {
    const height = rowMm(row, cols);
    if (used + height > BUDGET_MM && out[out.length - 1].length) { out.push([]); used = head; }
    out[out.length - 1].push(row);
    used += height;
  });
  return out;
}

function tableHtml(cols: Col[], body: TRow[]): string {
  const colgroup = `<colgroup>${cols.map((col) => `<col width="${col.mm}mm">`).join("")}</colgroup>`;
  const head = `<thead><tr>${cols.map((col) => `<th>${esc(cleanCodes(col.h))}</th>`).join("")}</tr></thead>`;
  const rowsHtml = body.map((row) => `<tr${row.cls ? ` class="${row.cls}"` : ""}>${row.cells.map((cell) => `<td>${esc(cleanCodes(cell))}</td>`).join("")}</tr>`).join("");
  return `<table class="grid">${colgroup}${head}<tbody>${rowsHtml}</tbody></table>`;
}

const heading = (title: string): string => `<h2>${esc(cleanCodes(title))}</h2>`;

/** Содержимое листов документа: заголовок + таблица, разбитая на листы. */
function tableSheets(title: string, cols: Col[], body: TRow[], emptyNote = ""): string[] {
  if (!body.length) return [heading(title) + (emptyNote ? `<p class="note">${esc(emptyNote)}</p>` : "")];
  return chunk(cols, body, 8).map((part, index) => (index === 0 ? heading(title) : heading(`${title} (продолжение)`)) + tableHtml(cols, part));
}

// ---- данные операций ----

interface Op {
  raw: Row; key: string; number: string; name: string; steps: Row[]; tooling: Row[]; components: Row[];
  equipment: string[]; sto: TdStoRef | null;
  /** Профессия и разряд из снимка выпуска (td_ops). */
  grade: TdOpGrade;
  /** РМ — инв. № оборудования операции (оснастка, раздел «Оборудование»). */
  workplace: string;
}

const isControl = (step: Row): boolean => /^контрол/i.test(text(step.kind).trim());

function operationsOf(model: TdPrintModel): Op[] {
  const document = object(model.card.document);
  return rows(document.operations).map((raw, index): Op => {
    const card = object(raw.card);
    const tooling = rows(card.tooling);
    const key = operationKeyOf(raw);
    return {
      raw, key, number: operationNumber(index), name: cleanCodes(etkOperationTitle(raw)) || `Операция ${index + 1}`,
      steps: rows(card.steps), tooling, components: rows(raw.components),
      equipment: tooling.filter((tool) => text(tool.section) === "Оборудование").map((tool) => cleanCodes(text(tool.name))).filter(Boolean),
      sto: model.sto_sections?.[key] ?? null,
      grade: tdOpGrade(model.card, key),
      workplace: [...new Set(tooling.filter((tool) => text(tool.section) === "Оборудование")
        .map((tool) => text(tool.inventory_number).trim()).filter(Boolean))].join(", "),
    };
  });
}

const stoName = (model: TdPrintModel): string => text(model.sto?.designation) || "СТО ФРАК-001-2026";

function stoSectionText(model: TdPrintModel, op: Op): string {
  return `${stoName(model)}, разд. ${op.sto?.section ? text(op.sto.section) : "4"}`;
}

const batchOf = (model: TdPrintModel): number => num(object(object(model.card.document).kit).part_quantity) || 1;

function perItem(norm: unknown, batch: number): string {
  const value = num(norm);
  if (!value) return "";
  return String(Math.round((value / batch) * 10000) / 10000).replace(".", ",");
}

// Провод и прочее, что нормируется в метрах, — материал (строка «М» по ГОСТ 3.1118), а не комплектующее.
const isMaterial = (item: Row): boolean =>
  /материал/i.test(`${text(item.kind)} ${text(item.category)} ${text(item.section)}`) || /^м\.?$/i.test(text(item.unit).trim());

// Комплектующее ЭТК: «art» — артикул по КД; «kd» заполнен, только когда вместо КД применён аналог (etk_parts.use_article):
// тогда «kd» — артикул по КД, «art» — применённый аналог.
const kdOf = (item: Row): string => text(item.kd).trim();
const artOf = (item: Row): string => text(item.art).trim();
const analogOf = (item: Row): string => (kdOf(item) && artOf(item) && artOf(item) !== kdOf(item) ? artOf(item) : "");

/** Обозначение по КД: артикул по КД («kd» при аналоге, иначе «art»). */
function designation(item: Row): string {
  return kdOf(item) || artOf(item);
}

/** У провода из БД.ПРОВ наименования нет — марка («ПВАМ 1,5 белый») лежит в «art»: она и есть наименование. */
function itemName(item: Row): string {
  return text(item.name).trim() || designation(item) || "—";
}

/** Наименование ВМ с аналогом: «Контакт», ниже «аналог: SHF-001T-0.8BS». */
function nameWithAnalog(item: Row): string {
  const analog = analogOf(item);
  return analog ? `${itemName(item)}\nаналог: ${analog}` : itemName(item);
}

/** Строка К/М маршрутной карты: наименование, обозначение по КД (если не совпадает с наименованием), аналог. */
function componentName(item: Row): string {
  const name = itemName(item), code = designation(item), analog = analogOf(item);
  return `${name}${code && code !== name ? `, ${code}` : ""}${analog ? `\nаналог: ${analog}` : ""}`;
}

/** Профессия в узкой графе МК: «радиоэлектронной аппаратуры» — «РЭА». */
const shortProfession = (value: string): string => value.replace(/радиоэлектронной аппаратуры/gi, "РЭА");

// ---- основная надпись ----

interface Frame { designation: string; docName: string; sheet: number; sheets: number }

function signRow(role: string, person: TdSignatory | undefined): string {
  return `<tr><td colspan="2" class="l">${role}</td><td class="l">${esc(cleanCodes(text(person?.name)))}</td><td></td><td></td></tr>`;
}

function stamp(model: TdPrintModel, frame: Frame): string {
  const issue = model.issue;
  const changed = issue.change_no > 0;
  const sign = issue.signatories;
  const left = `<table class="stamp-left"><colgroup><col width="7mm"><col width="8mm"><col width="30mm"><col width="16mm"><col width="12mm"></colgroup>` +
    `<tr class="h"><td>Изм.</td><td>Лист</td><td>№ докум.</td><td>Подп.</td><td>Дата</td></tr>` +
    `<tr><td>${changed ? issue.change_no : ""}</td><td>${changed ? "все" : ""}</td><td>${changed ? esc(issue.notice_no) : ""}</td><td></td><td>${changed ? formatDate(issue.approved_at || issue.created_at) : ""}</td></tr>` +
    signRow("Разраб.", sign?.developer) + signRow("Пров.", sign?.checker) + signRow("Н. контр.", sign?.normcontrol) + signRow("Утв.", sign?.approver) +
    `</table>`;
  const right = `<table class="stamp-right"><colgroup><col><col width="18mm"><col width="14mm"><col width="16mm"></colgroup>` +
    `<tr><td colspan="4" class="des">${esc(frame.designation)}</td></tr>` +
    `<tr><td rowspan="3" class="name">${esc(cleanCodes(text(model.card.product_name)))} · ${esc(text(model.card.project))} ${esc(text(model.card.item_index))}<br>${esc(frame.docName)}</td>` +
    `<td class="h">Лит.</td><td class="h">Лист</td><td class="h">Листов</td></tr>` +
    `<tr><td>${esc(issue.litera || "—")}</td><td>${frame.sheet}</td><td>${frame.sheets}</td></tr>` +
    `<tr><td colspan="3" class="org">${esc(ORGANISATION)}</td></tr></table>`;
  return `<div class="stamp"><div class="stamp-l">${left}</div><div class="stamp-r">${right}</div></div>`;
}

const sheetHtml = (model: TdPrintModel, frame: Frame, content: string): string =>
  `<section class="sheet"><div class="frame"><div class="content">${content}</div>${stamp(model, frame)}</div></section>`;

const KIT_CSS = `
  @page { size: A4 landscape; margin: 5mm; }
  * { box-sizing: border-box; }
  body { font-family: Arial, Helvetica, sans-serif; margin: 0; color: #000; font-size: 8.5pt; }
  .sheet { width: 287mm; height: 199mm; padding-left: 20mm; page-break-after: always; overflow: hidden; }
  .sheet:last-child { page-break-after: auto; }
  .frame { height: 100%; border: 0.6mm solid #000; display: flex; flex-direction: column; }
  .content { flex: 1; padding: 2mm 2mm 0; overflow: hidden; }
  h2 { font-size: 11pt; margin: 0 0 2mm; }
  .note { margin: 1mm 0; }
  table.grid { width: 100%; border-collapse: collapse; table-layout: fixed; }
  table.grid th, table.grid td { border: 0.25mm solid #000; padding: 0.4mm 1mm; text-align: left; vertical-align: top; white-space: pre-line; overflow-wrap: break-word; hyphens: auto; }
  table.grid th { background: #eee; font-weight: bold; }
  tr.sym-a td { font-weight: bold; background: #f4f4f4; }
  tr.empty td { height: 6mm; }
  .stamp { display: flex; height: 33mm; border-top: 0.6mm solid #000; font-size: 7pt; }
  .stamp-l { width: 73mm; border-right: 0.6mm solid #000; }
  .stamp-r { flex: 1; }
  .stamp table { width: 100%; height: 100%; border-collapse: collapse; table-layout: fixed; }
  .stamp td { border: 0.25mm solid #000; padding: 0 0.8mm; text-align: center; vertical-align: middle; overflow: hidden; }
  .stamp td.l { text-align: left; }
  .stamp-left col:nth-child(1) { width: 7mm; } .stamp-left col:nth-child(2) { width: 8mm; }
  .stamp-left col:nth-child(3) { width: 30mm; } .stamp-left col:nth-child(4) { width: 16mm; }
  .stamp-left col:nth-child(5) { width: 12mm; }
  .stamp-right col:nth-child(2) { width: 15mm; } .stamp-right col:nth-child(3) { width: 15mm; }
  .stamp-right col:nth-child(4) { width: 18mm; }
  .stamp td.h { background: #f4f4f4; }
  .stamp .des { font-size: 14pt; font-weight: bold; height: 11mm; }
  .stamp .name { font-size: 9pt; text-align: left; }
  .stamp .org { font-size: 8pt; }
  .title { text-align: center; }
  .title h1 { font-size: 15pt; margin: 14mm 0 6mm; }
  .title .product { font-size: 13pt; font-weight: bold; margin: 4mm 0; }
  .title .org { font-size: 11pt; margin-top: 12mm; }
  .title .place { font-size: 11pt; margin-top: 2mm; }
  .approve { display: flex; justify-content: space-between; margin-bottom: 6mm; }
  .approve div { width: 95mm; line-height: 1.6; }
  .fig svg { max-width: 100%; max-height: 135mm; height: auto; }
`;

// ---- документы комплекта ----

interface Doc { code: string; name: string; designation: string; sheets: string[] }

function titleSheet(model: TdPrintModel): string {
  const issue = model.issue;
  const method = ({ "88": "сборки", "80": "пайки" } as Record<string, string>)[issue.method_code] ?? "электромонтажа";
  const approver = issue.signatories?.approver, vp = issue.signatories?.agreed_vp;
  const vpPosition = text(vp?.position).trim() || "Представитель ВП МО";
  return `<div class="approve"><div><b>СОГЛАСОВАНО</b><br>${esc(vpPosition)}<br>${esc(text(vp?.name))}<br>«____» ____________ 20____ г.</div>` +
    `<div><b>УТВЕРЖДАЮ</b><br>${esc(text(approver?.position))}<br>${esc(text(approver?.name))}<br>«____» ____________ 20____ г.</div></div>` +
    `<div class="title"><h1>КОМПЛЕКТ ДОКУМЕНТОВ<br>на технологический процесс ${method}</h1>` +
    `<div class="product">${esc(cleanCodes(text(model.card.product_name)))}</div>` +
    `<div>${esc(text(model.card.project))} ${esc(text(model.card.item_index))}</div>` +
    `<div class="product">${esc(issue.designations.vtd)}</div>` +
    `<div>Литера: ${esc(issue.litera || "—")}</div><div class="org">${esc(ORGANISATION)}</div>` +
    `<div class="place">${esc(placeText(model))}</div></div>`;
}

/** «Цех электромонтажа, участок ЦАР» — ТЛ. */
function placeText(model: TdPrintModel): string {
  const workshop = text(model.issue.workshop).trim(), site = text(model.issue.site).trim();
  return [workshop, site ? `участок ${site}` : ""].filter(Boolean).join(", ");
}

function vtdSheets(docs: Doc[], model: TdPrintModel, ownCount: number): string[] {
  const cols: Col[] = [{ h: "№", mm: 12 }, { h: "Обозначение", mm: 55 }, { h: "Наименование документа", mm: 140 }, { h: "Листов", mm: 25 }, { h: "Примечание", mm: 28 }];
  const body: TRow[] = docs.map((doc, index) => ({ cells: [String(index + 1), doc.designation, doc.name, String(doc.code === "ВТД" ? ownCount : doc.sheets.length), ""] }));
  const refs: TRow[] = [`${stoName(model)}, ред. ${text(model.sto?.revision)}`.replace(/, ред\. $/, ""), ...REFERENCE_DOCS]
    .map((name, index) => ({ cells: [String(index + 1), name, "Документ, на который даны ссылки в ТД", "", ""] }));
  const refCols: Col[] = [{ h: "№", mm: 12 }, { h: "Обозначение", mm: 55 }, { h: "Наименование документа", mm: 140 }, { h: "", mm: 25 }, { h: "", mm: 28 }];
  const parts = chunk(cols, body, 8);
  const sheets = parts.map((part, index) => heading(index ? "Ведомость технологических документов (продолжение)" : "Ведомость технологических документов") + tableHtml(cols, part));
  const last = sheets.length - 1;
  sheets[last] += `<h2>Документы, на которые даны ссылки</h2>` + tableHtml(refCols, refs);
  return sheets;
}

function routeSheets(model: TdPrintModel, ops: Op[], docs: { ok: string }): string[] {
  const cols: Col[] = [{ h: "Сл.", mm: 8 }, { h: "Цех", mm: 25 }, { h: "Уч.", mm: 10 }, { h: "РМ", mm: 14 }, { h: "Опер.", mm: 12 },
    { h: "Наименование операции, оборудование, материалы и комплектующие", mm: 57 }, { h: "Документы", mm: 42 }, { h: "Проф.", mm: 24 }, { h: "Р", mm: 8 },
    { h: "КР", mm: 8 }, { h: "ЕН", mm: 10 }, { h: "Н. расх. на изд.", mm: 14 }, { h: "Тпз, мин", mm: 13 }, { h: "Тшт, мин", mm: 13 }];
  const batch = batchOf(model);
  const workshop = text(model.issue.workshop).trim(), site = text(model.issue.site).trim();
  const body: TRow[] = ops.flatMap((op) => {
    const times = object(op.raw.times);
    const items = op.components.map((item): TRow => ({
      cells: [isMaterial(item) ? "М" : "К", "", "", "", "", componentName(item), "", "", "", "", unitLabel(text(item.unit)) || "—", perItem(item.norm, batch), "", ""],
    }));
    return [
      { cells: ["А", workshop, site, op.workplace, op.number, op.name, `${docs.ok}\n${stoSectionText(model, op)}`, "", "", "", "", "", "", ""], cls: "sym-a" },
      { cells: ["Б", "", "", "", "", op.equipment.join("; "), "", shortProfession(op.grade.profession), op.grade.grade, "1", "шт.", "",
        minutes(times.setup_seconds), minutes(times.operation_seconds)] },
      ...items,
    ];
  });
  return tableSheets("Маршрутная карта", cols, body, "Операций в карте ЭТК нет.");
}

const PARAM_LABEL = /обжим|отрыв|аппликатор|жало|ванн|температур/i;

function operationParams(op: Op): string {
  const params = etkParams(op.raw);
  const out: string[] = [];
  params.sides.forEach((side) => side.items.filter((item) => PARAM_LABEL.test(item[0])).forEach((item) => out.push(`${side.title}: ${item[0]} — ${item[1]}`)));
  params.common.filter((item) => PARAM_LABEL.test(item[0])).forEach((item) => out.push(`${item[0]} — ${item[1]}`));
  return out.join("\n");
}

function operationCardSheets(model: TdPrintModel, ops: Op[], okTk: string): string[] {
  const cols: Col[] = [{ h: "Сл.", mm: 10 }, { h: "№ пер.", mm: 14 }, { h: "Содержание перехода, оснастка, режимы", mm: 150 }, { h: "Допуск", mm: 56 }, { h: "Инв. №", mm: 30 }];
  return ops.flatMap((op) => {
    const steps = op.steps.filter((step) => !isControl(step));
    const controls = op.steps.some(isControl);
    const body: TRow[] = [
      { cells: ["А", "", `Операция ${op.number}. ${op.name}`, "", ""], cls: "sym-a" },
      { cells: ["Б", "", `Оборудование: ${op.equipment.length ? op.equipment.join("; ") : "—"}`, "", ""] },
      ...steps.map((step, index): TRow => ({
        cells: ["О", String(index + 1), text(step.text), etkToleranceLines(step.tolerance).map((line) => (line.label ? `${line.label}: ${line.value}` : line.value)).join("\n"), ""],
      })),
      ...op.tooling.map((tool): TRow => ({ cells: ["Т", "", `${text(tool.section) ? `${text(tool.section)}: ` : ""}${text(tool.name)}`, "", text(tool.inventory_number)] })),
    ];
    const params = operationParams(op);
    if (params) body.push({ cells: ["Р", "", params, "", ""] });
    if (controls) body.push({ cells: ["О", String(steps.length + 1), `Контролировать по ОК технического контроля ${okTk}, операция ${op.number}`, "", ""] });
    body.push({ cells: ["", "", `Дефекты и критерии приёмки — по ${stoSectionText(model, op)}${op.sto?.title ? ` «${text(op.sto.title)}»` : ""}`, "", ""] });
    const performer = `<p class="note">Исполнитель: ${esc(op.grade.profession || "—")}, разряд ${esc(op.grade.grade || "—")}</p>`;
    return chunk(cols, body, 13).map((part, index) => heading(`Операционная карта. Операция ${op.number}. ${op.name}${index ? " (продолжение)" : ""}`) + performer + tableHtml(cols, part));
  });
}

function sketchSheets(model: TdPrintModel, ops: Op[]): string[] {
  const document = object(model.card.document);
  const out: string[] = [];
  const scheme = etkLinkScheme(model.card.document);
  const linkSvg = scheme ? etkLinkSchemeMarkup(scheme) : "";
  const harness = linkSvg ? "" : etkDrawHarness(document.topology as EtkTopologyData | undefined, {});
  if (linkSvg || harness) out.push(`${heading("Карта эскизов к маршрутной карте. Схема изделия")}<div class="fig">${linkSvg || harness}</div>`);
  ops.forEach((op) => {
    const figure = etkFigure(op.raw);
    if (figure) out.push(`${heading(`Карта эскизов. Операция ${op.number}. ${op.name}`)}<div class="fig">${figure}</div>`);
  });
  return out;
}

const MEASURING = /штангенциркул\S*|микрометр\S*|линейк\S*|рулетк\S*|весы|динамометр\S*|мультиметр\S*|тестер\S*|лупа|микроскоп\S*|калибр\S*|шаблон\S*|измерител\S*/i;

function measuringTool(op: Op, stepText: string): string {
  const lower = stepText.toLowerCase();
  const named = op.tooling.map((tool) => cleanCodes(text(tool.name))).filter((name) => MEASURING.test(name))
    .find((name) => lower.includes(name.toLowerCase().split(/\s+/)[0].slice(0, 6)));
  if (named) return named;
  const mentioned = stepText.match(MEASURING);
  return mentioned ? mentioned[0].toLowerCase() : "по ОК, графа Т";
}

function controlSheets(model: TdPrintModel, ops: Op[]): string[] {
  const cols: Col[] = [{ h: "№ операции", mm: 20 }, { h: "Контролируемый параметр", mm: 100 }, { h: "Норма, допуск", mm: 55 }, { h: "Средство измерения", mm: 45 }, { h: "Объём", mm: 16 }, { h: "Исполнитель", mm: 24 }];
  const withControl = ops.filter((op) => op.steps.some(isControl));
  const lastKey = withControl[withControl.length - 1]?.key;
  const body: TRow[] = withControl.flatMap((op) => op.steps.filter(isControl).map((step): TRow => ({
    cells: [op.number, text(step.text), etkToleranceLines(step.tolerance).map((line) => (line.label ? `${line.label}: ${line.value}` : line.value)).join("\n") || "по КД",
      measuringTool(op, text(step.text)), "100 %", op.key === lastKey ? "ОТК, ВП" : "ОТК"],
  })));
  void model;
  return tableSheets("Операционная карта технического контроля", cols, body, "Контрольных операций в ЭТК нет.");
}

function aggregate<T extends { count: number }>(map: Map<string, T>, key: string, make: () => T): T {
  let entry = map.get(key);
  if (!entry) { entry = make(); map.set(key, entry); }
  return entry;
}

function materialSheets(model: TdPrintModel, ops: Op[]): string[] {
  const cols: Col[] = [{ h: "№", mm: 10 }, { h: "Наименование", mm: 110 }, { h: "Обозначение по КД", mm: 55 }, { h: "Ед.", mm: 16 }, { h: "Норма на изделие", mm: 30 }, { h: "Примечание", mm: 39 }];
  const batch = batchOf(model);
  const sums = new Map<string, { name: string; kd: string; analog: string; unit: string; total: number; ops: string[]; material: boolean; count: number }>();
  ops.forEach((op) => op.components.forEach((item) => {
    const kd = designation(item), analog = analogOf(item), name = nameWithAnalog(item), unit = unitLabel(text(item.unit)) || "—";
    const entry = aggregate(sums, `${name}\u0000${kd}\u0000${analog}\u0000${unit}`, () => ({ name, kd, analog, unit, total: 0, ops: [], material: isMaterial(item), count: 0 }));
    entry.total += num(item.norm);
    entry.count += 1;
    if (!entry.ops.includes(op.number)) entry.ops.push(op.number);
  }));
  const body: TRow[] = [...sums.values()].map((entry, index) => ({
    cells: [String(index + 1), entry.name, entry.kd || "—", entry.unit, entry.total ? perItem(entry.total, batch) : "",
      `${entry.analog ? `аналог вместо ${entry.kd} — по разрешению; ` : ""}${entry.material ? "Материал; " : ""}оп. ${entry.ops.join(", ")}`],
  }));
  return tableSheets("Ведомость материалов", cols, body, "Комплектующих и материалов в ЭТК нет.");
}

function toolingSheets(ops: Op[]): string[] {
  const cols: Col[] = [{ h: "№", mm: 10 }, { h: "Раздел", mm: 32 }, { h: "Наименование", mm: 118 }, { h: "Инв. №", mm: 40 }, { h: "Операции", mm: 60 }];
  const sums = new Map<string, { section: string; name: string; inv: string; ops: string[]; count: number }>();
  ops.forEach((op) => op.tooling.forEach((tool) => {
    const section = text(tool.section) || "Оснастка", name = cleanCodes(text(tool.name)), inv = text(tool.inventory_number).trim();
    if (!name) return;
    const entry = aggregate(sums, `${section}\u0000${name}\u0000${inv}`, () => ({ section, name, inv, ops: [], count: 0 }));
    if (!entry.ops.includes(op.number)) entry.ops.push(op.number);
  }));
  const order = ["Инструмент", "Оборудование", "Расходники", "Матрицы", "Оснастка"];
  const rank = (section: string) => { const at = order.indexOf(section); return at < 0 ? order.length : at; };
  const body: TRow[] = [...sums.values()].sort((a, b) => rank(a.section) - rank(b.section))
    .map((entry, index) => ({ cells: [String(index + 1), entry.section, entry.name, entry.inv || "—", entry.ops.join(", ")] }));
  return tableSheets("Ведомость оснастки", cols, body, "Оснастки в ЭТК нет.");
}

function changeSheets(model: TdPrintModel): string[] {
  const cols: Col[] = [{ h: "Изм.", mm: 14 }, { h: "№ извещения", mm: 40 }, { h: "Причина", mm: 120 }, { h: "Дата", mm: 28 }, { h: "Разработал", mm: 58 }];
  const body: TRow[] = (model.changes ?? []).map((change) => ({ cells: [String(change.change_no), change.notice_no || "—", change.reason || "—", formatDate(change.approved_at), change.developer || ""] }));
  for (let i = 0; i < 10; i += 1) body.push({ cells: ["", "", "", "", ""], cls: "empty" });
  return tableSheets("Лист регистрации изменений", cols, body);
}

export function buildTdKitHtml(model: TdPrintModel): string {
  const issue = model.issue;
  const ops = operationsOf(model);
  const d = issue.designations;
  const other: Doc[] = [
    { code: "МК", name: "Маршрутная карта", designation: d.mk, sheets: routeSheets(model, ops, { ok: d.ok }) },
    { code: "ОК", name: "Операционные карты", designation: d.ok, sheets: operationCardSheets(model, ops, d.ok_tk) },
    { code: "КЭ", name: "Карты эскизов", designation: d.ke, sheets: sketchSheets(model, ops) },
    { code: "ОК ТК", name: "Операционная карта технического контроля", designation: d.ok_tk, sheets: controlSheets(model, ops) },
    { code: "ВМ", name: "Ведомость материалов", designation: d.vm, sheets: materialSheets(model, ops) },
    { code: "ВО", name: "Ведомость оснастки", designation: d.vo, sheets: toolingSheets(ops) },
    { code: "ЛР", name: "Лист регистрации изменений", designation: d.vtd, sheets: changeSheets(model) },
  ].filter((doc) => doc.sheets.length);
  const title: Doc = { code: "ТЛ", name: "Титульный лист", designation: d.vtd, sheets: [titleSheet(model)] };
  const vtd: Doc = { code: "ВТД", name: "Ведомость технологических документов", designation: d.vtd, sheets: [] };
  const listing = [title, vtd, ...other];
  // Число листов самой ВТД зависит только от числа строк таблицы, не от значений в них.
  let own = 1;
  for (let pass = 0; pass < 3; pass += 1) {
    const sheets = vtdSheets(listing, model, own);
    if (sheets.length === own) { vtd.sheets = sheets; break; }
    own = sheets.length;
    vtd.sheets = sheets;
  }
  const html = listing.flatMap((doc) => doc.sheets.map((content, index) =>
    sheetHtml(model, { designation: doc.designation, docName: doc.name, sheet: index + 1, sheets: doc.sheets.length }, content))).join("");
  const name = cleanCodes(text(model.card.product_name));
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>${esc(`Комплект ТД ${d.vtd} ${name}`.trim())}</title><style>${KIT_CSS}</style></head><body>${html}</body></html>`;
}
