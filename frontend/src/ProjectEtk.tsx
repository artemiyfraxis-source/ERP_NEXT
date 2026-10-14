import { createContext, Fragment, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { TcGenerator } from "./TcGenerator";
import {
  kitsetsApi,
  techcardsApi,
  type KitsetBom,
  type OperationRecordEdit,
  type OperationSketch,
  type Principal,
  type EtkRoute,
  type ProjectTechcard,
  type TechcardRevision,
  failureText,
} from "./api";
import { ETK_CONNECTION_COLUMNS, ETK_NOT_SET, etkChips, etkConnectionRows, etkContactSpecs, etkDrawHarness, etkFigure, type EtkTopologyData } from "./etkDraw";
import { etkMarkLabelsPage, etkPrintPages } from "./etkPrint";
import { hasMarkSerial, markSerialSpan, markSerialTemplate, type MarkSerialRange } from "./markSerial";
import { etkRouteBranches, etkRouteChips, etkRouteFeedText, etkRouteOf, etkRouteSummary, formatMinutes } from "./etkRoute";
import { defectArt, defectGroupCode } from "./etkDefectArt";
import { ETK_TOOLING_SECTIONS, parseDefects, recordCategory, solderMode, toolingSections, type SolderMode } from "./etkOperation";
import { countLabel, formatDateTime, formatNumber, formatQty } from "./format";
import { openPrintDocument } from "./print";
import { SAFETY_HEAD, TcCardEditForm, type EditContext } from "./TcCardEditForm";
import { hasFullAccess } from "./navigation";
import { EtkFold, EtkFoldScope, useEtkFold } from "./etkFold";
import { EtkHarnessE4, wireMapModel } from "./EtkHarnessE4";
import { EtkPfList } from "./EtkPfList";

/* Электронная ТК сборки проекта и её генератор — вкладки «Генератор» и «ТК КБn» карточки проекта (SUPPLYMAP
   PortalApp kitCardRender_ → mountGen_ / renderTc_ → renderEtk_). */
type EtkRow = { name?: string; art?: string; kd?: string; grn?: string; norm?: string; unit?: string };
type EtkStep = { number?: string; kind?: string; text?: string; tolerance?: string };
type Housing = {
  title: string; art: string; rows: number; cols: number; n: number; color: string;
  conf: string; note?: string; grid: number[][]; round?: Array<{ pin: number; x: number; y: number }> | null;
};
type EtkWireField = { name?: string; art?: string; pins?: number[] };
/** Метка по КД (etk_builder `_mark_rows`): что, надпись, материал, где, на изделие и на партию. */
type EtkMark = { target?: string; text?: string; material?: string; place?: string; qty?: string; batch?: string; length?: string };
type EtkTool = {
  section?: string; number?: string; name?: string; inventory_number?: string; program?: string;
  parameters?: string; norm?: string; unit?: string;
};
type EtkOperation = {
  position?: number;
  type?: string;
  code?: string;
  side?: string;
  title?: string;
  connector?: string;
  connector_article?: string;
  edited?: { at?: string; by?: string };
  card?: null | { name?: string; safety?: string; defects?: string; steps?: EtkStep[]; tooling?: EtkTool[] };
  components?: EtkRow[];
  semis?: EtkRow[];
  result?: EtkRow[];
  time?: EtkRow[];
  fields?: { wires?: EtkWireField[]; marks?: EtkMark[] };
  housing?: Housing;
};
type EtkTopology = {
  nodes?: Array<{ id: string; conn: string; side?: string; kind?: string; housing?: Housing }>;
  edges?: Array<{ from: string; to: string; wires: string[]; count: number; length: number }>;
};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function operations(document: ProjectTechcard["document"]): EtkOperation[] {
  const value = document?.operations;
  return Array.isArray(value) ? value as EtkOperation[] : [];
}

function warnings(document: ProjectTechcard["document"]): string[] {
  const value = document?.warnings;
  return Array.isArray(value) ? value.map(String) : [];
}

function topology(document: ProjectTechcard["document"]): EtkTopology | null {
  const value = document?.topology;
  return value && typeof value === "object" ? value as EtkTopology : null;
}

/** Схема жгута один раз, компактной SVG-картинкой, как в SUPPLYMAP (etkDrawHarness_): разъёмы, пучок жил, число
    проводов и длина. Раскладку контактов показывает только операция монтажа, где она нужна. Под схемой — таблица
    соединений провод за проводом, когда распиновка задана на обоих концах (перекрёст виден по номерам контактов).
    Свёрнута, пока её не откроют (владелец 13.10). В задании монтажника (`task`) — вид «Схема Э4», как в редакторе
    чертежа: сохранённый чертёж КБ только для просмотра или плоская схема по ЭТК; таблица соединений — под ней, свёрнута. */
function HarnessView({ value, list, connections, raw, task }: {
  value: EtkTopology | null; list: EtkOperation[]; connections: string[][]; raw?: unknown; task?: { project: string; index: string; taskId?: string };
}) {
  const table = connections.length > 0 && <table className="etk-result-table etk-links" aria-label="Таблица соединений">
    <thead><tr>{ETK_CONNECTION_COLUMNS.map((column, index) => <th key={index}>{column}</th>)}</tr></thead>
    <tbody>{connections.map((row) => <tr key={row[0]}>{row.map((cell, index) =>
      <td key={index} className={index === 0 || index === 3 || index === 5 ? "num" : undefined}>{cell}</td>)}</tr>)}</tbody>
  </table>;
  if (task) {
    return <EtkFold block="schema" className="etk-schema" lazy summary={`Схема Э4${connections.length ? " и таблица соединений" : ""}`}>
      <EtkHarnessE4 project={task.project} index={task.index} taskId={task.taskId} model={wireMapModel(raw, value as EtkTopologyData | null, harnessPins(list))} />
      {table && <EtkFold block="links" className="etk-links-fold" summary={`Таблица соединений · ${connections.length}`}>{table}</EtkFold>}
    </EtkFold>;
  }
  const svg = etkDrawHarness(value as EtkTopologyData | null, harnessPins(list));
  if (!svg && !connections.length) return null;
  return <EtkFold block="schema" className="etk-schema" summary={`${svg ? "Схема жгута" : "Таблица соединений"}${svg && connections.length > 0 ? " и таблица соединений" : ""}`}>
    {svg && <div className="etk-harness" dangerouslySetInnerHTML={{ __html: svg }} />}
    {table}
  </EtkFold>;
}

/** Распиновка из операций монтажа — по узлу схемы «артикул разъёма::сторона»: провода видны в своих гнёздах. */
export function harnessPins(list: EtkOperation[]): Record<string, Record<number, string>> {
  const out: Record<string, Record<number, string>> = {};
  list.forEach((operation) => {
    const article = String(operation.connector_article || "").trim();
    if (!article) return;
    (operation.fields?.wires ?? []).forEach((wire) => (wire.pins ?? []).forEach((pin) => {
      (out[`${article}::${operation.side || ""}`] ||= {})[pin] = wire.art || wire.name || "";
    }));
  });
  return out;
}

function defaultConfig(card: ProjectTechcard): Record<string, unknown> {
  const saved = record(card.document?.build_config);
  if (Object.keys(saved).length) return saved;
  return {
    projectNo: card.project,
    assemblyIndex: card.item_index,
    assemblyName: card.product_name,
    partQty: 1,
    wires: [],
    ops: [],
  };
}

/** «2000 шт», «184,8 м» — число по-русски, единица строчными (аудит 10.10, ЭТК-2); без нормы — пусто, а не «— шт». */
export function quantity(row: EtkRow): string {
  return row.norm ? formatQty(row.norm, row.unit) : "";
}

/** Строка входа: название слева, количество справа крупной цифрой — монтажник видит число с первого взгляда. */
function InputLine({ children, qty }: { children: ReactNode; qty: string }) {
  return <div className="etk-in-row">
    <span className="etk-in-name">{children}</span>
    <span className="etk-in-qty">{qty || "—"}</span>
  </div>;
}

function Rows({ title, rows }: { title: string; rows?: EtkRow[] }) {
  if (!rows?.length) return null;
  return <div className="etk-block"><strong>{title}</strong>{rows.map((row, index) =>
    <InputLine key={`${row.name || row.art}:${index}`} qty={quantity(row)}>{row.name || row.art || "—"}</InputLine>
  )}</div>;
}

function minutes(value: string | undefined): number {
  const number = Number(String(value ?? "").replace(",", "."));
  return Number.isFinite(number) ? number : 0;
}

/** «2 ч 50 мин» для времени от часа. */
function hoursText(value: number): string {
  const total = Math.round(value);
  if (total < 60) return "";
  const rest = total % 60;
  return `${Math.floor(total / 60)} ч${rest ? ` ${rest} мин` : ""}`;
}

/** 6 Результат: что операция отдаёт дальше (наименование, количество) и расчётное время — две равные цифры одного
 *  размера и вида (владелец 12.10: «одно большое, а другое маленькое»; подпись «на партию» убрана — путала рядом с
 *  количеством полуфабриката). Одна строка результата — название и две цифры рядом; несколько строк — плотная таблица
 *  (время по строкам — колонкой, если у каждой своё) и итоговая цифра времени тем же видом. */
function ResultView({ label, result = [], time = [] }: { label: string; result?: EtkRow[]; time?: EtkRow[] }) {
  const byName = new Map(time.map((row) => [row.name || "", row.norm || ""]));
  const perRow = time.length > 1 && result.length === time.length && result.every((row) => byName.has(row.name || ""));
  const total = time.reduce((sum, row) => sum + minutes(row.norm), 0);
  const hours = hoursText(total);
  const timeFig = time.length > 0 && <div className="etk-fig etk-time" aria-label="Расчётное время">
    <small>{perRow || time.length > 1 ? "Расчётное время, итого" : "Расчётное время"}</small>
    <b>{formatMinutes(total)} <span>мин{hours ? ` · ${hours}` : ""}</span></b>
    {!perRow && time.length > 1 && <ul>{time.map((row, index) =>
      <li key={`${row.name}:${index}`}><span>{row.name}</span><span className="num">{row.norm}</span></li>)}</ul>}
  </div>;
  if (result.length === 1) {
    const [row] = result;
    return <div className="etk-result etk-result-one">
      <div className="etk-result-name"><small>{label}</small><span>{row.name || "—"}</span></div>
      <div className="etk-figs">
        <div className="etk-fig"><small>Количество</small><b>{quantity(row) || "—"}</b></div>
        {timeFig}
      </div>
    </div>;
  }
  return <div className="etk-result">
    {result.length > 0 && <table className="etk-result-table">
      <thead><tr><th>{label}</th><th>Кол-во</th>{perRow && <th>Время, мин</th>}</tr></thead>
      <tbody>{result.map((row, index) => <tr key={`${row.name}:${index}`}>
        <td>{row.name || "—"}</td>
        <td className="num">{quantity(row)}</td>
        {perRow && <td className="num">{formatNumber(byName.get(row.name || ""))}</td>}
      </tr>)}</tbody>
    </table>}
    {timeFig}
  </div>;
}

/** «не задано» в тексте нормы — тоном опасности (erp-screens: пустая норма видна, а не угадывается). */
function MarkMissing({ text }: { text: string }) {
  const parts = text.split(ETK_NOT_SET);
  return <>{parts.map((part, index) => <Fragment key={index}>{part}{index < parts.length - 1 && <b className="tone-danger">{ETK_NOT_SET}</b>}</Fragment>)}</>;
}

/** Комплектующие (SUPPLYMAP etkPartRow_): артикул к применению, под ним «по КД», наименование по артикулу, ГРН партии;
 *  у обжимаемого контакта — строка спецификации на свой конец: аппликатор, высота обжима, отрыв, зачистка. */
function PartRows({ rows, fields }: { rows?: EtkRow[]; fields?: unknown }) {
  if (!rows?.length) return null;
  const crimp = fields && typeof fields === "object" ? fields as Record<string, unknown> : {};
  return <div className="etk-block"><strong>Комплектующие</strong>{rows.map((row, index) =>
    <InputLine key={`${row.art || row.name}:${index}`} qty={quantity(row)}>
      {row.art || row.name || "—"}{row.kd && <small>по КД: {row.kd}, аналог</small>}
      {row.art && row.name && <small>{row.name}</small>}{row.grn && <small>ГРН {row.grn}</small>}
      {etkContactSpecs(crimp, row.art).map((spec) => <small key={spec} className="etk-spec"><MarkMissing text={spec} /></small>)}
    </InputLine>
  )}</div>;
}

/** Допуск шага, которого нет в данных («длина не задана» у нарезки трубок-маркеров) — красным, как «не задано» пайки. */
const NOT_SET = /не задан/i;

/** Номера изделий задания для «№ изделия» в надписях и печать их списка; вне задания — нет (номер «по партии»). */
const MarkSerialContext = createContext<{ range: MarkSerialRange; print: () => void } | null>(null);

/** Надпись метки: без «№ изделия» — как есть; с ним — шаблон и номера задания «КБ1-{№} → КБ1-001 … КБ1-010». */
function MarkText({ text, range }: { text?: string; range?: MarkSerialRange }) {
  if (!text || !hasMarkSerial(text)) return <b>{text || "—"}</b>;
  return <><b>{markSerialTemplate(text)}</b> <span className="etk-mark-serial">→ {markSerialSpan(text, range)}</span></>;
}

/** Метки по КД в «Ходе операции» маркировки: монтажник видит, какую надпись, чем и где делать, сколько штук. */
function MarksView({ marks, batch }: { marks: EtkMark[]; batch: string }) {
  const serial = useContext(MarkSerialContext);
  const lengths = marks.some((mark) => mark.length);
  const numbered = marks.some((mark) => hasMarkSerial(mark.text));
  const count = serial ? serial.range.last - serial.range.first + 1 : 0;
  const table = <table className="etk-marks" aria-label="Метки по КД">
    <caption>Метки по КД</caption>
    <thead><tr><th>№</th><th>Что маркируем</th><th>Надпись</th><th>Материал</th><th>Где</th>{lengths && <th className="num">Длина, мм</th>}<th className="num">На изделие</th><th className="num">{batch ? `На партию ${formatQty(batch, "шт")}` : "На партию"}</th></tr></thead>
    <tbody>{marks.map((mark, index) => <tr key={index}>
      <td className="num">{index + 1}</td><td>{mark.target || "—"}</td><td><MarkText text={mark.text} range={serial?.range} /></td><td>{mark.material || "—"}</td>
      <td>{mark.place || "—"}</td>{lengths && <td className="num">{mark.length || ""}</td>}<td className="num">{mark.qty || ""}</td><td className="num">{mark.batch || ""}</td>
    </tr>)}</tbody>
  </table>;
  if (!numbered || !serial || count < 1) return table;
  return <>{table}<div className="etk-marks-print">
    <button type="button" className="secondary small" onClick={serial.print}>Печать надписей № {serial.range.first}–{serial.range.last}</button>
    <span className="muted">{countLabel(count, ["изделие", "изделия", "изделий"])} задания</span>
  </div></>;
}

/** Операция ЭТК в порядке, который задал производство (SUPPLYMAP renderEtk_, доведённый): безопасность (свёрнута) →
 *  1 вход: комплектующие и полуфабрикаты → 2 инструмент, оборудование, расходники + режим пайки (жало, температура) →
 *  3 ход операции → 4 типовые дефекты с пиктограммами → 5 эскиз → 6 результат и расчётное время. */
function EtkOperationCard({ operation, number, total, batch = "", actions, sketch }: {
  operation: EtkOperation; number: number; total: number; batch?: string; actions?: ReactNode; sketch?: ReactNode;
}) {
  const raw = operation as Record<string, unknown>;
  // Эскизы — SVG-строки etkDraw (как в печати); монтаж в разъём — торец серии с номерами контактов всегда, даже без
  // распиновки в генераторе (тогда под ним список проводов «по КД»). `sketch` — своя картинка технолога и кнопки.
  const figure = etkFigure(raw);
  const tooling = operation.card?.tooling ?? [];
  const solder = solderMode(raw);
  const safety = String(operation.card?.safety || "").replace(SAFETY_HEAD, "").trim();
  const resultLabel = String(raw.result_label || "") || "Результат";
  const marks = operation.fields?.marks ?? [];
  const hasInput = Boolean(operation.components?.length || operation.semis?.length);
  const hasResult = Boolean(operation.result?.length || operation.time?.length);
  // Номера разделов подряд по показанным блокам (эскиза бывает нет).
  let shown = 0;
  const next = () => ++shown;
  return <article className="etk-op">
    <header className="etk-op-head">
      <span className="operation-number">{number}</span>
      <div>
        <h3>{etkTitle(operation)}{operation.connector && !/^\d+$/.test(operation.connector) && <span className="etk-conn"> — {operation.connector}</span>}</h3>
        <small>{opCode(operation) && <><code>{opCode(operation)}</code> · </>}операция {number} из {total}
          {operation.edited && <b className="tr-edited"> · правлена {operation.edited.at}{operation.edited.by ? ` · ${operation.edited.by}` : ""}</b>}</small>
      </div>
      {actions}
    </header>
    {etkChips(raw).length > 0 && <div className="etk-chips" aria-label="Параметры операции">{etkChips(raw).map(([label, value]) =>
      <span key={label}><small>{label}</small><MarkMissing text={value} /></span>)}</div>}
    {safety && <EtkFold block="safety" className="etk-safety" summary="Требование безопасности"><div>{safety}</div></EtkFold>}
    {!operation.card && <div className="warning-inline">Карточка операции отсутствует в библиотеке.</div>}
    <EtkSection label="Вход" number={next()}>
      {hasInput ? <div className="etk-flow">
        <PartRows rows={operation.components} fields={raw.fields} />
        <Rows title="Полуфабрикат на входе" rows={operation.semis} />
      </div> : <div className="empty">Комплектующих и полуфабрикатов на входе нет.</div>}
    </EtkSection>
    <EtkSection label="Оснастка" number={next()}>
      {solder && <SolderPanel mode={solder} />}
      <ToolingBySection tooling={tooling} fixed />
    </EtkSection>
    {operation.card?.steps?.length || marks.length ? <EtkSection label="Ход операции" number={next()}>
      {marks.length > 0 && <MarksView marks={marks} batch={batch} />}
      {operation.card?.steps?.length ? <ol className="etk-steps">{operation.card.steps.map((step, index) =>
        <li key={`${step.number}:${index}`} className={step.kind && step.kind !== "Операция" ? "ctl" : ""}>{step.kind && step.kind !== "Операция" && <span>{step.kind}</span>}{step.text}{step.tolerance && step.tolerance.trim() !== "-" && <small className={NOT_SET.test(step.tolerance) ? "tone-danger" : undefined}>Допуск: {step.tolerance}</small>}</li>
      )}</ol> : null}</EtkSection> : null}
    {operation.card?.defects && <DefectsView text={operation.card.defects} code={operation.code || ""} number={next()} />}
    {(figure || sketch) && <EtkSection label="Эскиз" number={next()}>
      <div className="etk-fig-row">
        {figure && <div className="etk-fig" dangerouslySetInnerHTML={{ __html: figure }} />}
        {sketch}
      </div>
    </EtkSection>}
    {hasResult && <EtkSection label="Результат" number={next()}>
      <ResultView label={resultLabel} result={operation.result} time={operation.time} />
    </EtkSection>}
  </article>;
}

/** Режим пайки: жало и температура обязательны. Не из карты операции — помечено «по умолчанию ERP». */
function SolderPanel({ mode }: { mode: SolderMode }) {
  const assumed = <span className="etk-assumed" title="В карте операции не указано — значение по умолчанию ERP для Sn63Pb37. Уточнить технологу и вписать в карту операции.">по умолчанию, уточнить</span>;
  return <div className="etk-solder" aria-label="Режим пайки">
    <div><small>{mode.method === "bath" ? "Паяльник" : "Жало паяльника"}</small><b>{mode.tip}</b>{mode.tipAssumed && assumed}</div>
    <div><small>{mode.method === "bath" ? "Температура ванны" : mode.method === "induction" ? "Нагрев" : "Температура жала"}</small>
      <b>{mode.temperature}{mode.tolerance && ` (${mode.tolerance})`}</b>{mode.temperatureAssumed && assumed}</div>
  </div>;
}

/** Блок ЭТК, который сворачивается щелчком по заголовку, как «Типовые дефекты» (владелец, 12.10: «не могу скрыть»).
 *  Закрыт по умолчанию, открытое помнится (владелец 13.10: «по дефолту всё свёрнуто»); печать (etkPrint.ts) печатает
 *  все блоки. */
function EtkSection({ label, number, children }: { label: string; number: number; children: ReactNode }) {
  const fold = useEtkFold(`sec:${label}`);
  return <section className="etk-sec" aria-label={label}>
    <details className="etk-fold" open={fold.open} onToggle={fold.onToggle}>
      <summary><h4><span className="etk-sec-n">{number}</span>{label}</h4></summary>
      {children}
    </details>
  </section>;
}

/** Типовые дефекты карточками: на каждый дефект рисунок «Норма | Брак» из общей библиотеки (в Google — сплошной
 *  текст). Рисунок выбирается при показе по названию дефекта и коду операции. */
function DefectsView({ text, code, number }: { text: string; code: string; number: number }) {
  const parsed = parseDefects(text);
  const count = parsed.groups.reduce((sum, group) => sum + group.items.length, 0);
  const fold = useEtkFold("sec:Типовые дефекты");
  return <details className="etk-sec etk-defects etk-fold" open={fold.open} onToggle={fold.onToggle} aria-label="Типовые дефекты">
    <summary><h4><span className="etk-sec-n">{number}</span>Типовые дефекты{count ? ` · ${count}` : ""}</h4></summary>
    {count ? <>
      {parsed.intro.length > 0 && <small className="etk-def-intro">{parsed.intro.join(" · ")}</small>}
      {parsed.groups.map((group, index) => <div className="etk-def-group" key={`${group.title}:${index}`}>
        {group.title && <div className="tr-sec-h">{group.title}</div>}
        <div className="etk-def-grid">{group.items.map((item, position) => <div className="etk-def" key={`${item.name}:${position}`}>
          <span className="etk-def-pic" dangerouslySetInnerHTML={{ __html: defectArt(item.name, item.text, defectGroupCode(code, group.title)) }} />
          <div><b>{item.name}</b><small>{item.text}</small></div>
        </div>)}</div>
      </div>)}
    </> : <div className="etk-def-text">{text}</div>}
  </details>;
}

/** Same key as backend project_cards.operation_key: code::connector::side. */
function etkOperationKey(operation: EtkOperation): string {
  return `${String(operation.code ?? "").trim()}::${String(operation.connector ?? "").trim()}::${String(operation.side ?? "").trim()}`;
}

/** Вторая строка пункта маршрута: код карты операции и сторона/разъём; номер разъёма без букв не показываем. */
function navSub(operation: EtkOperation): string {
  const connector = String(operation.connector || "").trim();
  return [opCode(operation), /^\d+$/.test(connector) ? "" : connector].filter(Boolean).join(" · ");
}

/** Код карты операции; «БЕЗ КОДА» из старых ТК — это пустой код. */
function opCode(operation: EtkOperation): string {
  const code = String(operation.code || "").trim();
  return /^без кода$/i.test(code) ? "" : code;
}

/** Русское название операции: имя карты → название шаблона без кода («CUT_WIRE+1crimp | Автомат резка…») →
 *  категория по коду карты. Код и тип операции (cutWireCrimp) названием не бывают. */
function etkTitle(operation: EtkOperation): string {
  const title = String(operation.title || "").split("|").map((part) => part.trim()).filter(Boolean).at(-1) || "";
  const category = opCode(operation) ? recordCategory(opCode(operation)) : "";
  return operation.card?.name || title || (category && category !== "Прочее" ? category : "") || "Операция";
}

function toolSub(tool: EtkTool): string {
  return [
    tool.program && tool.program !== "-" ? `прогр. ${tool.program}` : "",
    tool.parameters || "",
    tool.norm ? `${tool.norm}${tool.unit ? ` ${tool.unit}` : ""}` : "",
  ].filter(Boolean).join(" · ");
}

/** Оснастка по разделам: инструмент → оборудование → расходники → прочее; номер считается внутри раздела
 *  (SUPPLYMAP renderEtk_ / renderCard). */
const SKETCH_TYPES = "image/png,image/jpeg,image/webp,image/gif";

/** Своя картинка эскиза к операции (владелец 30.09: «нет схемы и некуда её добавлять»): хранится в базе ERP, одна на
 *  операцию; «Загрузить» — файлом или вставкой скриншота (Ctrl+V), «Заменить», «Убрать». */
function OperationSketchView({ cardId, operationKey, canEdit, sketch, onChanged, url }: {
  cardId: string; operationKey: string; canEdit: boolean; sketch?: OperationSketch; onChanged: () => void; url: (key: string, at: string) => string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!sketch && !canEdit) return null;

  function upload(file: File | null | undefined) {
    if (!file || busy) return;
    if (!SKETCH_TYPES.split(",").includes(file.type)) { setError("Эскиз — картинка PNG, JPG, WEBP или GIF"); return; }
    const reader = new FileReader();
    reader.onload = () => {
      setBusy(true); setError("");
      void techcardsApi.putSketch(cardId, operationKey, {
        file_name: file.name && file.name !== "image.png" ? file.name : "", content_type: file.type, content_base64: String(reader.result || ""),
      }).then(onChanged).catch((reason) => setError(failureText(reason, "Картинка не загружена"))).finally(() => setBusy(false));
    };
    reader.readAsDataURL(file);
  }

  async function remove() {
    if (busy || !window.confirm("Убрать картинку эскиза из этой операции?")) return;
    setBusy(true); setError("");
    try { await techcardsApi.deleteSketch(cardId, operationKey); onChanged(); }
    catch (reason) { setError(failureText(reason, "Картинка не убрана")); }
    finally { setBusy(false); }
  }

  return <div className="etk-sketch" tabIndex={canEdit ? 0 : undefined}
    onPaste={canEdit ? (event) => upload(Array.from(event.clipboardData.files).find((file) => file.type.startsWith("image/"))) : undefined}>
    {sketch && <a href={url(operationKey, sketch.uploaded_at)} target="_blank" rel="noreferrer">
      <img src={url(operationKey, sketch.uploaded_at)} alt="Эскиз операции" />
    </a>}
    {canEdit && <div className="etk-sketch-bar">
      <input ref={input} type="file" accept={SKETCH_TYPES} hidden onChange={(event) => { upload(event.target.files?.[0]); event.target.value = ""; }} />
      <button className="secondary small" disabled={busy} onClick={() => input.current?.click()}>{sketch ? "Заменить картинку" : "Загрузить картинку эскиза"}</button>
      {sketch && <button className="secondary small" disabled={busy} onClick={() => void remove()}>Убрать</button>}
      <small className="muted">{sketch ? `${sketch.uploaded_by} · ${formatDateTime(sketch.uploaded_at)}` : "или вставьте скриншот: щёлкните здесь и Ctrl+V"}</small>
    </div>}
    {error && <div className="error-banner" role="alert">{error}</div>}
  </div>;
}

/** Оснастка по разделам. `fixed` — операция ЭТК (erp-screens §6): три раздела Инструмент · Оборудование · Расходники
 *  всегда, пустой — «нет» серым; без `fixed` (шаблон в базе) — только заполненные разделы. */
export function ToolingBySection({ tooling, fixed = false }: { tooling: EtkTool[]; fixed?: boolean }) {
  if (!tooling.length && !fixed) return <div className="empty">Инструмент, оборудование и расходники не указаны.</div>;
  const sections = toolingSections([...(fixed ? ETK_TOOLING_SECTIONS : []), ...tooling.map((tool) => tool.section || "")]);
  return <div className="tr-tooling">{sections.map((section) => {
    const tools = tooling.filter((tool) => (tool.section || "") === section);
    return <div className="tr-sec" key={section}>
      {section && <div className="tr-sec-h">{section}</div>}
      {!tools.length && <div className="tr-none">нет</div>}
      {tools.map((tool, index) => <div className="tr-tool" key={`${section}:${index}`}>
        <span className="tr-n">{tool.number}</span>
        <span>{tool.name}{tool.inventory_number && <span className="tr-inv">{tool.inventory_number}</span>}
          {toolSub(tool) && <small>{toolSub(tool)}</small>}</span>
      </div>)}
    </div>;
  })}</div>;
}

/** Что в операции считает генератор (чипы параметров, вход, эскиз, результат) — в форме правки на своих местах, без правки. */
function editContext(operation: EtkOperation, batch: string, sketchUrl: string): EditContext {
  const raw = operation as Record<string, unknown>;
  const figure = etkFigure(raw);
  const chips = etkChips(raw);
  return {
    chips: chips.length ? <div className="etk-chips" aria-label="Параметры операции">{chips.map(([label, value]) =>
      <span key={label}><small>{label}</small><MarkMissing text={value} /></span>)}</div> : undefined,
    input: operation.components?.length || operation.semis?.length
      ? <div className="etk-flow"><PartRows rows={operation.components} fields={raw.fields} /><Rows title="Полуфабрикат на входе" rows={operation.semis} /></div> : undefined,
    sketch: figure || sketchUrl ? <div className="etk-fig-row">
      {figure && <div className="etk-fig" dangerouslySetInnerHTML={{ __html: figure }} />}
      {sketchUrl && <div className="etk-sketch"><img src={sketchUrl} alt="Эскиз операции" /></div>}
    </div> : undefined,
    result: operation.result?.length || operation.time?.length
      ? <ResultView label={String(raw.result_label || "") || "Результат"} result={operation.result} time={operation.time} /> : undefined,
  };
}

export function editDraft(name: string, card: { safety?: string; defects?: string; revision?: string; steps?: EtkStep[]; tooling?: EtkTool[] }): OperationRecordEdit {
  return {
    name,
    safety: String(card.safety || "").replace(SAFETY_HEAD, ""),
    defects: card.defects || "",
    revision: card.revision || "",
    steps: (card.steps || []).map((step) => ({ kind: step.kind || "Операция", text: step.text || "", tolerance: step.tolerance || "" })),
    tooling: (card.tooling || []).map((tool) => ({
      section: tool.section || "Инструмент", name: tool.name || "", inventory_number: tool.inventory_number || "",
      program: tool.program || "", parameters: tool.parameters || "", norm: tool.norm || "", unit: tool.unit || "",
    })),
  };
}


/** Полоса «Маршрут» над ЭТК, только у ветвящейся схемы полуфабрикатов (линейная ЭТК её не показывает): блоки по порядку
 *  «источники → потребители» чипами «№ имя · N оп. · X мин/шт», под именем — что блок берёт на вход (с ×N, когда на штуку
 *  нужно не одна), у финального блока метка «итог». Чип открывает первую операцию блока; блок открытой операции подсвечен. */
function RouteStrip({ route, current, onPick }: { route: EtkRoute; current: number; onPick: (position: number) => void }) {
  const chips = etkRouteChips(route);
  const active = route.blocks.find((block) => block.ops.includes(current))?.key;
  return <section className="etk-route" aria-label="Маршрут полуфабрикатов">
    <div className="etk-route-head"><strong>Маршрут</strong><small>{etkRouteSummary(chips)}</small></div>
    <ol className="etk-route-chips">{chips.map((chip) => {
      const meta = [chip.ops ? `${chip.ops} оп.` : "", chip.perUnit].filter(Boolean);
      return <li key={chip.key}>
        <button type="button" className={`etk-route-chip${chip.key === active ? " on" : ""}${chip.final ? " final" : ""}`}
          disabled={chip.first == null} aria-current={chip.key === active ? "step" : undefined}
          onClick={() => chip.first != null && onPick(chip.first)}>
          <span className="etk-route-n">{chip.number}</span>
          <span className="etk-route-text">
            <span><b>{chip.name}</b>{meta.map((part) => <small key={part} className="etk-route-meta"> · <span>{part}</span></small>)}</span>
            {chip.feeds.length > 0 && <small className="etk-route-feed" aria-label="Берёт на вход">← {chip.feeds.map((feed, position) =>
              <span key={`${feed.number}:${position}`}>{position > 0 && " · "}{etkRouteFeedText(feed)}</span>)}</small>}
          </span>
          {chip.final && <em className="etk-route-final">итог</em>}
        </button>
      </li>;
    })}</ol>
  </section>;
}

/** Операция, на которой открыть ЭТК (экран «Мои задания»): по коду, иначе по номеру этапа. `positions` — задание по
 *  ПФ: только эти операции ЭТК (1-based позиции, в порядке работы ПФ), каждая свёрнута. */
export interface EtkFocus { code?: string; position?: number; positions?: number[] }

/** Операция ЭТК задания: код и номер этапа вместе (один код бывает у двух операций — «Монтаж терминалов» стороны А
 *  и В), иначе по коду, иначе по номеру этапа; не нашлась — первая. */
export function focusIndex(list: EtkOperation[], focus?: EtkFocus): number {
  if (!focus) return 0;
  const both = list.findIndex((item) => focus.code && opCode(item) === focus.code && item.position === focus.position);
  if (both >= 0) return both;
  const byCode = list.findIndex((item) => focus.code && opCode(item) === focus.code);
  const position = focus.position ?? focus.positions?.[0];
  return byCode >= 0 ? byCode : Math.max(0, list.findIndex((item) => item.position === position));
}

/** Операции задания по ПФ: позиции `focus.positions`, которые есть в ЭТК, в их порядке; меньше двух — не ПФ-задание. */
export function focusOperations(list: EtkOperation[], focus?: EtkFocus): number[] {
  const positions = (focus?.positions ?? []).filter((position, at, all) => position >= 1 && position <= list.length && all.indexOf(position) === at);
  return positions.length > 1 ? positions : [];
}

/** Электронная ТК: слева маршрут, справа выбранная операция, «← →» по маршруту и правка операции (SUPPLYMAP renderEtk_).
 *  В задании монтажника (`task`) — только операция задания: без списка операций слева, без «← Назад / Далее» и
 *  полосы маршрута (владелец 13.10: «слева не должно быть следующих операций»). */
function EtkView({ card, canEdit, onSaved, onRouteSaved, sketches, onSketches, sketchUrl, focus, task = false }: {
  card: ProjectTechcard; canEdit: boolean; onSaved: (updated: ProjectTechcard) => void; onRouteSaved: (updated: ProjectTechcard) => void;
  sketches: OperationSketch[]; onSketches: () => void; sketchUrl: (key: string, at: string) => string; focus?: EtkFocus;
  task?: boolean;
}) {
  const list = operations(card.document);
  const [selected, setSelected] = useState(() => focusIndex(list, focus));
  const [editing, setEditing] = useState(false);
  const main = useRef<HTMLDivElement>(null);
  if (!list.length) return null;
  const index = Math.min(selected, list.length - 1);
  const operation = list[index];
  const next = list[index + 1];
  const route = etkRouteOf(card.document);
  const uploaded = sketches.find((item) => item.operation_key === etkOperationKey(operation));
  const batch = String(record(card.document?.kit).part_quantity ?? "");
  const sketchOf = (item: EtkOperation) => {
    const found = sketches.find((sketch) => sketch.operation_key === etkOperationKey(item));
    return found ? sketchUrl(etkOperationKey(item), found.uploaded_at) : "";
  };
  const several = task ? focusOperations(list, focus) : [];

  // Задание по ПФ: только его операции в порядке работы, каждая свёрнута до щелчка (инструкция не мешает списку).
  if (several.length) {
    return <div className="etk-task-ops" aria-label="Операции задания">{several.map((position) => {
      const item = list[position - 1];
      return <details key={`${etkOperationKey(item)}:${position}`} className="etk-task-op">
        <summary><span className="etk-no">{String(item.position || position).padStart(2, "0")}</span><b>{etkTitle(item)}</b>{navSub(item) && <small>{navSub(item)}</small>}</summary>
        <EtkOperationCard operation={item} number={item.position || position} total={list.length} batch={batch}
          sketch={<OperationSketchView cardId={card.id} operationKey={etkOperationKey(item)} canEdit={false} url={sketchUrl}
            sketch={sketches.find((sketch) => sketch.operation_key === etkOperationKey(item))} onChanged={onSketches} />} />
      </details>;
    })}</div>;
  }

  /** Чип блока: открыть его первую операцию (1-based позиция в ЭТК) и подвести к ней экран. */
  function pick(position: number) {
    setSelected(Math.min(Math.max(position, 1), list.length) - 1);
    setEditing(false);
    requestAnimationFrame(() => main.current?.scrollIntoView?.({ behavior: "smooth", block: "nearest" }));
  }

  async function save(draft: OperationRecordEdit) {
    const updated = await techcardsApi.editOperation(card.id, card.version, etkOperationKey(operation), {
      ...operation,
      card: { ...operation.card, safety: draft.safety, defects: draft.defects, steps: draft.steps, tooling: draft.tooling },
    });
    setEditing(false);
    onSaved(updated);
  }

  const grouped = !task && route !== null;
  const view = <div className={task ? "etk-wrap etk-wrap-task" : grouped ? "etk-wrap etk-wrap-pf" : "etk-wrap"}>
    {grouped && <EtkPfList card={card} list={list} selected={index} canEdit={canEdit} onSaved={onRouteSaved}
      onPick={(position) => { setSelected(position); setEditing(false); }}
      title={etkTitle} sub={(item) => [navSub(item), item.edited ? "правлена" : ""].filter(Boolean).join(" · ")} sketch={sketchOf} />}
    {!task && !grouped && <nav className="etk-nav-route" aria-label="Маршрут ЭТК">{list.map((item, position) =>
      <button key={`${etkOperationKey(item)}:${position}`} className={position === index ? "on" : ""} onClick={() => { setSelected(position); setEditing(false); }}>
        <span className="etk-no">{String(item.position || position + 1).padStart(2, "0")}</span>
        <span>{etkTitle(item)}{(navSub(item) || item.edited) && <small>{[navSub(item), item.edited ? "правлена" : ""].filter(Boolean).join(" · ")}</small>}</span>
      </button>)}
    </nav>}
    <div className="etk-main" ref={main}>
      {editing && operation.card ? <TcCardEditForm
        code={opCode(operation)}
        number={operation.position || index + 1}
        nameEditable={false}
        initial={editDraft(etkTitle(operation) + (operation.connector ? ` — ${operation.connector}` : ""), operation.card)}
        saveNote={`Сохранение — ревизия ${card.revision + 1}`}
        context={editContext(operation, String(record(card.document?.kit).part_quantity ?? ""),
          uploaded ? sketchUrl(etkOperationKey(operation), uploaded.uploaded_at) : "")}
        onCancel={() => setEditing(false)}
        onSave={save}
      /> : <>
        <EtkOperationCard operation={operation} number={operation.position || index + 1} total={list.length}
          batch={batch}
          actions={canEdit && operation.card && card.status === "Выдана" ? <button className="secondary small" onClick={() => setEditing(true)}>Править операцию</button> : null}
          sketch={<OperationSketchView cardId={card.id} operationKey={etkOperationKey(operation)} canEdit={canEdit} url={sketchUrl}
            sketch={sketches.find((item) => item.operation_key === etkOperationKey(operation))} onChanged={onSketches} />} />
        {!task && <div className="etk-nav">
          <button className="secondary small" disabled={!index} onClick={() => setSelected(index - 1)}>← Назад</button>
          <button className="secondary small" disabled={!next} onClick={() => setSelected(index + 1)}>Далее: {next ? etkTitle(next) : "—"} →</button>
        </div>}
      </>}
    </div>
  </div>;
  // Линейная схема (и ЭТК без схемы): тот же элемент, что и раньше, без обёртки.
  return !task && route && etkRouteBranches(route)
    ? <><RouteStrip route={route} current={index + 1} onPick={pick} />{view}</>
    : view;
}

function stamp(value: string | null | undefined) {
  return formatDateTime(value);
}

/** Вкладка «ТК КБn»: одна строка о карте и действия, схема жгута один раз, маршрут слева и операция справа, журнал
    ревизий свёрнут. Правят операции здесь («Править операцию»); пересборка — в «Генераторе». */
export type EtkSketchSource = { list: () => Promise<{ items: OperationSketch[] }>; url: (operationKey: string, at: string) => string };

export function EtkSheet({ card, principal, title, focus, task = false, taskId, serial, onChange, onRebuild, sketchSource }: {
  card: ProjectTechcard; principal: Principal; title?: ReactNode; focus?: EtkFocus;
  /** Задание монтажника: только операция `focus`, схема — вид Э4. */
  task?: boolean;
  /** Задание, чьей карточке принадлежит ЭТК: схема Э4 читает чертёж маршрутом заданий. */
  taskId?: string;
  /** Номера изделий задания: «№ изделия» в надписях меток разворачивается в них; без задания — «№ по партии». */
  serial?: MarkSerialRange;
  onChange: (card: ProjectTechcard) => void; onRebuild?: () => void;
  /** Откуда брать эскизы; по умолчанию раздел техкарт. Монтажнику экран «Мои задания» даёт свой источник. */
  sketchSource?: EtkSketchSource;
}) {
  const source: EtkSketchSource = sketchSource ?? {
    list: () => techcardsApi.sketches(card.id),
    url: (operationKey, at) => techcardsApi.sketchUrl(card.id, operationKey, at),
  };
  const [revisions, setRevisions] = useState<TechcardRevision[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const canEdit = principal.permissions.techcards === "edit";
  const canGenerate = Boolean(principal.permissions.tcgen);
  const list = operations(card.document);
  const issued = card.status === "Выдана";
  const [sketches, setSketches] = useState<OperationSketch[]>([]);
  useEffect(() => {
    void techcardsApi.revisions(card.id).then((result) => setRevisions(result.items)).catch(() => setRevisions([]));
  }, [card.id, card.version]);
  const loadSketches = () => void source.list().then((result) => setSketches(result?.items ?? [])).catch(() => setSketches([]));
  useEffect(loadSketches, [card.id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function issue() {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      onChange(await techcardsApi.issue(card.id, card.version));
      setNotice("ЭТК выдана, ревизия 1 зафиксирована");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "ЭТК не выдана");
    } finally { setBusy(false); }
  }

  const sketchLinks = () => Object.fromEntries(sketches.map((item) =>
    [item.operation_key, window.location.origin + source.url(item.operation_key, item.uploaded_at)]));
  const labelsPage = serial ? etkMarkLabelsPage(card, serial) : null;
  const serialContext = serial && labelsPage ? {
    range: serial, print: () => openPrintDocument(`Надписи ${card.project} ${card.item_index}`, [labelsPage]),
  } : null;

  return <MarkSerialContext.Provider value={serialContext}><EtkFoldScope user={principal.user.id}><div className="etk-sheet">
    <div className="etk-top">
      <div className="etk-title">{title ?? <strong>{card.project} · {card.item_index}</strong>}
        <span className="muted">{countLabel(list.length, ["операция", "операции", "операций"])} · {issued ? `ревизия ${card.revision}${card.issued_at ? ` · выдана ${stamp(card.issued_at)}` : ""}` : "черновик, не выдана"}</span></div>
      {list.length > 0 && <button className="secondary small etk-print-btn" onClick={() => openPrintDocument(`ТК ${card.project} ${card.item_index}`, etkPrintPages(card, revisions, sketchLinks(), serial))}>Печать / PDF</button>}
      {canGenerate && onRebuild && <button className="secondary small" onClick={onRebuild}>Пересобрать</button>}
      {canEdit && !issued && card.document && <button className="primary small" disabled={busy} onClick={() => void issue()}>Выдать ЭТК</button>}
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {warnings(card.document).length > 0 && <details className="etk-warnings"><summary>Предупреждения сборки · {warnings(card.document).length}</summary>
      {warnings(card.document).map((warning) => <div key={warning}>{warning}</div>)}</details>}
    <HarnessView value={topology(card.document)} list={list} connections={etkConnectionRows(card.document?.connections)}
      raw={card.document?.connections} task={task ? { project: card.project, index: card.item_index, taskId } : undefined} />
    <EtkView key={card.id} card={card} canEdit={canEdit} focus={focus} task={task} sketches={sketches} onSketches={loadSketches} sketchUrl={source.url} onSaved={(updated) => {
      onChange(updated);
      setNotice(`Операция сохранена${updated.status === "Выдана" ? ` · ревизия ${updated.revision}` : ""}`);
    }} onRouteSaved={(updated) => {
      onChange(updated);
      setNotice(`ПФ сохранены${updated.status === "Выдана" ? ` · ревизия ${updated.revision}` : ""}`);
    }} />
    {!list.length && <div className="etk-empty">ЭТК ещё не собрана — откройте «Генератор» и нажмите «Создать».</div>}
    {revisions.length > 0 && <EtkFold block="revs" className="etk-revs" summary={`Журнал ревизий · ${revisions.length}`}>
      {revisions.map((revision) => <div key={revision.id}><span>Ревизия {revision.revision} · {revision.reason}</span><span className="muted">{revision.actor_name} · {stamp(revision.occurred_at)}</span></div>)}
    </EtkFold>}
  </div></EtkFoldScope></MarkSerialContext.Provider>;
}

/** «Генератор» сборки: форма и ОДНА кнопка. Нет ТК — «Создать» (сборка ЭТК и выдача сразу, как файл техкарты в
    SUPPLYMAP: отдельного шага утверждения там нет). ТК уже выдана — «Пересобрать» с подтверждением, новой ревизией.
    Служебное — свод материалов, удаление черновика, JSON-конфигурация — свёрнуто в «Ещё». */
export function GeneratorPane({ card, principal, onChange, onDone, builtId, onBuiltChange, onOpenDrawing }: {
  card: ProjectTechcard; principal: Principal; onChange: (card: ProjectTechcard) => void; onDone?: (card: ProjectTechcard) => void;
  /** Черновик, по которому собрали и чей урок ещё не записан (хранит владелец вкладок). */
  builtId?: string; onBuiltChange?: (draftId: string) => void;
  /** «Открыть чертёж» после «Построить чертёж» в генераторе. */
  onOpenDrawing?: () => void;
}) {
  const [configText, setConfigText] = useState("{}");
  const generatedConfig = useRef<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [kit, setKit] = useState<{ data: KitsetBom | null; error: string }>({ data: null, error: "" });
  const canIssue = principal.permissions.techcards === "edit";
  useEffect(() => { setConfigText(JSON.stringify(defaultConfig(card), null, 2)); }, [card.id, card.version]);
  useEffect(() => {
    // SUPPLYMAP portalTcGenData → tcLoadBomFromKit: состав из набора проекта + строки листа КБn расчёта;
    // набора или сборки нет — генератор показывает ошибку.
    setKit({ data: null, error: "" });
    let live = true;
    void kitsetsApi.bom(card.project, card.item_index)
      .then((result) => { if (live) setKit({ data: result, error: "" }); })
      .catch((reason) => { if (live) setKit({ data: null, error: failureText(reason, "Состав сборки не прочитан") }); });
    return () => { live = false; };
  }, [card.project, card.item_index]);

  /** true — техкарта собрана (в том числе черновиком), false — нет; от этого зависит, завершён ли черновик с чертежа. */
  async function build(config: Record<string, unknown>, draftId?: string, generatorOrigin?: Record<string, unknown>): Promise<boolean> {
    if (busy) return false;
    setBusy(true); setError(""); setNotice("");
    try {
      let updated = await techcardsApi.autoBuild(card.id, card.version, config, draftId, generatorOrigin);
      const rebuilt = updated.status === "Выдана";
      if (!rebuilt && canIssue) {
        try { updated = await techcardsApi.issue(updated.id, updated.version); }
        catch (reason) {
          onChange(updated);
          setError(`Техкарта собрана черновиком, но не выдана: ${reason instanceof Error ? reason.message : "ошибка"}`);
          return true;
        }
      }
      onChange(updated);
      const warningCount = warnings(updated.document).length;
      setNotice(`${rebuilt ? `Техкарта пересобрана · ревизия ${updated.revision}` : updated.status === "Выдана" ? "Техкарта создана" : "Черновик ЭТК собран — выдаёт технолог"}${warningCount ? ` · предупреждений: ${warningCount}` : ""}`);
      onDone?.(updated);
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "ЭТК не собрана");
      return false;
    } finally { setBusy(false); }
  }

  async function fromJson() {
    let config: Record<string, unknown>;
    try {
      config = JSON.parse(configText) as Record<string, unknown>;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Конфигурация содержит неверный JSON");
      return;
    }
    await build(config);
  }

  async function discardDraft() {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      onChange(await techcardsApi.discardDraft(card.id, card.version));
      setNotice("Черновик ЭТК удалён");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Черновик не удалён");
    } finally { setBusy(false); }
  }

  return <div className="gen-pane">
    <TcGenerator card={card} kit={kit.data} kitError={kit.error} busy={busy} onBuild={build} onDiscard={discardDraft} builtId={builtId} onBuiltChange={onBuiltChange} onOpenDrawing={onOpenDrawing}
      userId={principal.user.id} onConfig={(config) => { generatedConfig.current = config; }}
      /* JSON-конфигурация — служебная, только админу (владелец 29.09: «что это такое и зачем»). */
      more={!hasFullAccess(principal) ? undefined : <div className="build-config">
        <label>JSON-конфигурация генератора (для эксперта)<textarea aria-label="Конфигурация автоматической сборки" rows={12} value={configText} onChange={(event) => setConfigText(event.target.value)} /></label>
        <div className="form-actions">
          <button className="secondary small" onClick={() => { if (generatedConfig.current) setConfigText(JSON.stringify(generatedConfig.current, null, 2)); }}>Взять из формы</button>
          <button className="secondary small" disabled={busy} onClick={() => void fromJson()}>Собрать по JSON</button>
        </div>
      </div>} />
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
  </div>;
}

/** Старая ссылка `?section=techcards&card=`: та же ТК и её генератор одной страницей. */
export function ProjectEtk({ card, principal, onChange }: { card: ProjectTechcard; principal: Principal; onChange: (card: ProjectTechcard) => void }) {
  return <article className="panel project-etk">
    <EtkSheet card={card} principal={principal} onChange={onChange} />
    {principal.permissions.tcgen && <GeneratorPane card={card} principal={principal} onChange={onChange} />}
  </article>;
}

/** Карточка ТК по id — из общего списка (отдельной ручки на одну карту нет). */
export function useTechcard(cardId: string) {
  const [card, setCard] = useState<ProjectTechcard | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    setCard(null); setError("");
    if (!cardId) return;
    void techcardsApi.get(cardId)
      .then((found) => { if (live) setCard(found); })
      .catch((reason) => { if (live) setError(failureText(reason, "Техкарта не открылась")); });
    return () => { live = false; };
  }, [cardId]);
  return { card, setCard, error };
}
