import { useEffect, useRef, useState, type ReactNode } from "react";
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
import { ETK_CONNECTION_COLUMNS, etkChips, etkConnectionRows, etkDrawHarness, etkFigure, type EtkTopologyData } from "./etkDraw";
import { etkPrintPages } from "./etkPrint";
import { etkRouteBranches, etkRouteChips, etkRouteFeedText, etkRouteOf, etkRouteSummary, formatMinutes } from "./etkRoute";
import { defectArt, defectGroupCode } from "./etkDefectArt";
import { parseDefects, recordCategory, solderMode, toolingSections, type SolderMode } from "./etkOperation";
import { openPrintDocument } from "./print";
import { SAFETY_HEAD, TcCardEditForm, type EditContext } from "./TcCardEditForm";
import { hasFullAccess } from "./navigation";

/* Электронная ТК сборки проекта и её генератор — вкладки «Генератор» и «ТК КБn» карточки проекта (SUPPLYMAP
   PortalApp kitCardRender_ → mountGen_ / renderTc_ → renderEtk_). */
type EtkRow = { name?: string; art?: string; kd?: string; grn?: string; norm?: string; unit?: string };
type EtkStep = { number?: string; kind?: string; text?: string; tolerance?: string };
type Housing = {
  title: string; art: string; rows: number; cols: number; n: number; color: string;
  conf: string; note?: string; grid: number[][]; round?: Array<{ pin: number; x: number; y: number }> | null;
};
type EtkWireField = { name?: string; art?: string; pins?: number[] };
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
  fields?: { wires?: EtkWireField[] };
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
    соединений провод за проводом, когда распиновка задана на обоих концах (перекрёст виден по номерам контактов). */
function HarnessView({ value, list, connections }: { value: EtkTopology | null; list: EtkOperation[]; connections: string[][] }) {
  const svg = etkDrawHarness(value as EtkTopologyData | null, harnessPins(list));
  if (!svg && !connections.length) return null;
  return <details className="etk-schema" open>
    <summary>{svg ? "Схема жгута" : "Таблица соединений"}{svg && connections.length > 0 ? " и таблица соединений" : ""}</summary>
    {svg && <div className="etk-harness" dangerouslySetInnerHTML={{ __html: svg }} />}
    {connections.length > 0 && <table className="etk-result-table etk-links" aria-label="Таблица соединений">
      <thead><tr>{ETK_CONNECTION_COLUMNS.map((column, index) => <th key={index}>{column}</th>)}</tr></thead>
      <tbody>{connections.map((row) => <tr key={row[0]}>{row.map((cell, index) =>
        <td key={index} className={index === 0 || index === 3 || index === 5 ? "num" : undefined}>{cell}</td>)}</tr>)}</tbody>
    </table>}
  </details>;
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

/** «2000 Шт.»; без нормы — пусто, а не «— Шт.». */
function quantity(row: EtkRow): string {
  return row.norm ? `${row.norm} ${row.unit || ""}`.trim() : "";
}

function Rows({ title, rows }: { title: string; rows?: EtkRow[] }) {
  if (!rows?.length) return null;
  return <div className="etk-block"><strong>{title}</strong>{rows.map((row, index) =>
    <span key={`${row.name || row.art}:${index}`}>{row.name || row.art || "—"}{quantity(row) && <small>{quantity(row)}</small>}</span>
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

/** 6 Результат: что операция отдаёт дальше (наименование, количество на партию) и расчётное время отдельной цифрой.
 *  Время по строкам (резка, опрессовка стороны А — на каждый провод) встаёт колонкой к своей строке, итог — в плитке;
 *  одно время на операцию — только в плитке, без повтора наименования (в Google оно дублировалось). */
function ResultView({ label, result = [], time = [], batch }: { label: string; result?: EtkRow[]; time?: EtkRow[]; batch: string }) {
  const byName = new Map(time.map((row) => [row.name || "", row.norm || ""]));
  const perRow = time.length > 1 && result.length === time.length && result.every((row) => byName.has(row.name || ""));
  const total = time.reduce((sum, row) => sum + minutes(row.norm), 0);
  const hours = hoursText(total);
  return <div className="etk-result">
    {result.length > 0 && <table className="etk-result-table">
      <thead><tr><th>{label}</th><th>Кол-во</th>{perRow && <th>Время, мин</th>}</tr></thead>
      <tbody>{result.map((row, index) => <tr key={`${row.name}:${index}`}>
        <td>{row.name || "—"}</td>
        <td className="num">{quantity(row)}</td>
        {perRow && <td className="num">{byName.get(row.name || "")}</td>}
      </tr>)}</tbody>
    </table>}
    {time.length > 0 && <div className="etk-time" aria-label="Расчётное время">
      <small>{perRow || time.length > 1 ? "Расчётное время, итого" : "Расчётное время"}</small>
      <b>{formatMinutes(total)} <span>мин</span></b>
      <small>{[hours, batch ? `на партию ${batch} шт.` : ""].filter(Boolean).join(" · ")}</small>
      {!perRow && time.length > 1 && <ul>{time.map((row, index) =>
        <li key={`${row.name}:${index}`}><span>{row.name}</span><span className="num">{row.norm}</span></li>)}</ul>}
    </div>}
  </div>;
}

/** Комплектующие (SUPPLYMAP etkPartRow_): артикул к применению, под ним «по КД», наименование по артикулу, ГРН партии. */
function PartRows({ rows }: { rows?: EtkRow[] }) {
  if (!rows?.length) return null;
  return <div className="etk-block"><strong>Комплектующие</strong>{rows.map((row, index) =>
    <span key={`${row.art || row.name}:${index}`}>
      {row.art || row.name || "—"}{row.kd && <small>по КД: {row.kd}, аналог</small>}
      {row.art && row.name && <small>{row.name}</small>}{row.grn && <small>ГРН {row.grn}</small>}
      <small>{row.norm || "—"} {row.unit || ""}</small>
    </span>
  )}</div>;
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
      <span key={label}><small>{label}</small>{value}</span>)}</div>}
    {safety && <details className="etk-safety"><summary>Требование безопасности</summary><div>{safety}</div></details>}
    {!operation.card && <div className="warning-inline">Карточка операции отсутствует в библиотеке.</div>}
    <section className="etk-sec" aria-label="Вход">
      <h4><span className="etk-sec-n">{next()}</span>Вход</h4>
      {hasInput ? <div className="etk-flow">
        <PartRows rows={operation.components} />
        <Rows title="Полуфабрикат на входе" rows={operation.semis} />
      </div> : <div className="empty">Комплектующих и полуфабрикатов на входе нет.</div>}
    </section>
    <section className="etk-sec" aria-label="Инструмент, оборудование, расходники">
      <h4><span className="etk-sec-n">{next()}</span>Инструмент, оборудование, расходники</h4>
      {solder && <SolderPanel mode={solder} />}
      <ToolingBySection tooling={tooling} />
    </section>
    {operation.card?.steps?.length ? <section className="etk-sec" aria-label="Ход операции">
      <h4><span className="etk-sec-n">{next()}</span>Ход операции</h4>
      <ol className="etk-steps">{operation.card.steps.map((step, index) =>
        <li key={`${step.number}:${index}`} className={step.kind && step.kind !== "Операция" ? "ctl" : ""}>{step.kind && step.kind !== "Операция" && <span>{step.kind}</span>}{step.text}{step.tolerance && step.tolerance.trim() !== "-" && <small>Допуск: {step.tolerance}</small>}</li>
      )}</ol></section> : null}
    {operation.card?.defects && <DefectsView text={operation.card.defects} code={operation.code || ""} number={next()} />}
    {(figure || sketch) && <section className="etk-sec" aria-label="Эскиз">
      <h4><span className="etk-sec-n">{next()}</span>Эскиз</h4>
      <div className="etk-fig-row">
        {figure && <div className="etk-fig" dangerouslySetInnerHTML={{ __html: figure }} />}
        {sketch}
      </div>
    </section>}
    {hasResult && <section className="etk-sec" aria-label="Результат">
      <h4><span className="etk-sec-n">{next()}</span>Результат</h4>
      <ResultView label={resultLabel} result={operation.result} time={operation.time} batch={batch} />
    </section>}
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

/** Типовые дефекты карточками: на каждый дефект рисунок «Норма | Брак» из общей библиотеки (в Google — сплошной
 *  текст). Рисунок выбирается при показе по названию дефекта и коду операции. */
function DefectsView({ text, code, number }: { text: string; code: string; number: number }) {
  const parsed = parseDefects(text);
  const count = parsed.groups.reduce((sum, group) => sum + group.items.length, 0);
  return <details className="etk-sec etk-defects" open aria-label="Типовые дефекты">
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
function OperationSketchView({ cardId, operationKey, canEdit, sketch, onChanged }: {
  cardId: string; operationKey: string; canEdit: boolean; sketch?: OperationSketch; onChanged: () => void;
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
    {sketch && <a href={techcardsApi.sketchUrl(cardId, operationKey, sketch.uploaded_at)} target="_blank" rel="noreferrer">
      <img src={techcardsApi.sketchUrl(cardId, operationKey, sketch.uploaded_at)} alt="Эскиз операции" />
    </a>}
    {canEdit && <div className="etk-sketch-bar">
      <input ref={input} type="file" accept={SKETCH_TYPES} hidden onChange={(event) => { upload(event.target.files?.[0]); event.target.value = ""; }} />
      <button className="secondary small" disabled={busy} onClick={() => input.current?.click()}>{sketch ? "Заменить картинку" : "Загрузить картинку эскиза"}</button>
      {sketch && <button className="secondary small" disabled={busy} onClick={() => void remove()}>Убрать</button>}
      <small className="muted">{sketch ? `${sketch.uploaded_by} · ${new Date(sketch.uploaded_at).toLocaleDateString("ru-RU")}` : "или вставьте скриншот: щёлкните здесь и Ctrl+V"}</small>
    </div>}
    {error && <div className="error-banner" role="alert">{error}</div>}
  </div>;
}

export function ToolingBySection({ tooling }: { tooling: EtkTool[] }) {
  if (!tooling.length) return <div className="empty">Инструмент, оборудование и расходники не указаны.</div>;
  const sections = toolingSections(tooling.map((tool) => tool.section || ""));
  return <div className="tr-tooling">{sections.map((section) => <div className="tr-sec" key={section}>
    {section && <div className="tr-sec-h">{section}</div>}
    {tooling.filter((tool) => (tool.section || "") === section).map((tool, index) => <div className="tr-tool" key={`${section}:${index}`}>
      <span className="tr-n">{tool.number}</span>
      <span>{tool.name}{tool.inventory_number && <span className="tr-inv">{tool.inventory_number}</span>}
        {toolSub(tool) && <small>{toolSub(tool)}</small>}</span>
    </div>)}
  </div>)}</div>;
}

/** Что в операции считает генератор (чипы параметров, вход, эскиз, результат) — в форме правки на своих местах, без правки. */
function editContext(operation: EtkOperation, batch: string, sketchUrl: string): EditContext {
  const raw = operation as Record<string, unknown>;
  const figure = etkFigure(raw);
  const chips = etkChips(raw);
  return {
    chips: chips.length ? <div className="etk-chips" aria-label="Параметры операции">{chips.map(([label, value]) =>
      <span key={label}><small>{label}</small>{value}</span>)}</div> : undefined,
    input: operation.components?.length || operation.semis?.length
      ? <div className="etk-flow"><PartRows rows={operation.components} /><Rows title="Полуфабрикат на входе" rows={operation.semis} /></div> : undefined,
    sketch: figure || sketchUrl ? <div className="etk-fig-row">
      {figure && <div className="etk-fig" dangerouslySetInnerHTML={{ __html: figure }} />}
      {sketchUrl && <div className="etk-sketch"><img src={sketchUrl} alt="Эскиз операции" /></div>}
    </div> : undefined,
    result: operation.result?.length || operation.time?.length
      ? <ResultView label={String(raw.result_label || "") || "Результат"} result={operation.result} time={operation.time} batch={batch} /> : undefined,
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

/** Электронная ТК: слева маршрут, справа выбранная операция, «← →» по маршруту и правка операции (SUPPLYMAP renderEtk_). */
function EtkView({ card, canEdit, onSaved, sketches, onSketches }: {
  card: ProjectTechcard; canEdit: boolean; onSaved: (updated: ProjectTechcard) => void;
  sketches: OperationSketch[]; onSketches: () => void;
}) {
  const list = operations(card.document);
  const [selected, setSelected] = useState(0);
  const [editing, setEditing] = useState(false);
  const main = useRef<HTMLDivElement>(null);
  if (!list.length) return null;
  const index = Math.min(selected, list.length - 1);
  const operation = list[index];
  const next = list[index + 1];
  const route = etkRouteOf(card.document);
  const uploaded = sketches.find((item) => item.operation_key === etkOperationKey(operation));

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

  const view = <div className="etk-wrap">
    <nav className="etk-nav-route" aria-label="Маршрут ЭТК">{list.map((item, position) =>
      <button key={`${etkOperationKey(item)}:${position}`} className={position === index ? "on" : ""} onClick={() => { setSelected(position); setEditing(false); }}>
        <span className="etk-no">{String(item.position || position + 1).padStart(2, "0")}</span>
        <span>{etkTitle(item)}{item.edited ? " ✏️" : ""}{navSub(item) && <small>{navSub(item)}</small>}</span>
      </button>)}
    </nav>
    <div className="etk-main" ref={main}>
      {editing && operation.card ? <TcCardEditForm
        code={opCode(operation)}
        number={operation.position || index + 1}
        nameEditable={false}
        initial={editDraft(etkTitle(operation) + (operation.connector ? ` — ${operation.connector}` : ""), operation.card)}
        saveNote={`Сохранение — ревизия ${card.revision + 1}`}
        context={editContext(operation, String(record(card.document?.kit).part_quantity ?? ""),
          uploaded ? techcardsApi.sketchUrl(card.id, etkOperationKey(operation), uploaded.uploaded_at) : "")}
        onCancel={() => setEditing(false)}
        onSave={save}
      /> : <>
        <EtkOperationCard operation={operation} number={operation.position || index + 1} total={list.length}
          batch={String(record(card.document?.kit).part_quantity ?? "")}
          actions={canEdit && operation.card && card.status === "Выдана" ? <button className="secondary small" onClick={() => setEditing(true)}>✏️ Править</button> : null}
          sketch={<OperationSketchView cardId={card.id} operationKey={etkOperationKey(operation)} canEdit={canEdit}
            sketch={sketches.find((item) => item.operation_key === etkOperationKey(operation))} onChanged={onSketches} />} />
        <div className="etk-nav">
          <button className="secondary small" disabled={!index} onClick={() => setSelected(index - 1)}>← Назад</button>
          <button className="secondary small" disabled={!next} onClick={() => setSelected(index + 1)}>Далее: {next ? etkTitle(next) : "—"} →</button>
        </div>
      </>}
    </div>
  </div>;
  // Линейная схема (и ЭТК без схемы): тот же элемент, что и раньше, без обёртки.
  return route && etkRouteBranches(route)
    ? <><RouteStrip route={route} current={index + 1} onPick={pick} />{view}</>
    : view;
}

function stamp(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
}

/** Вкладка «ТК КБn»: одна строка о карте и действия, схема жгута один раз, маршрут слева и операция справа, журнал
    ревизий свёрнут. Правят операции здесь («✏️ Править»); пересборка — в «Генераторе». */
export function EtkSheet({ card, principal, title, onChange, onRebuild }: {
  card: ProjectTechcard; principal: Principal; title?: ReactNode;
  onChange: (card: ProjectTechcard) => void; onRebuild?: () => void;
}) {
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
  const loadSketches = () => void techcardsApi.sketches(card.id).then((result) => setSketches(result?.items ?? [])).catch(() => setSketches([]));
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

  return <div className="etk-sheet">
    <div className="etk-top">
      <div className="etk-title">{title ?? <strong>{card.project} · {card.item_index}</strong>}
        <span className="muted">{list.length} операций · {issued ? `ревизия ${card.revision}${card.issued_at ? ` · выдана ${stamp(card.issued_at)}` : ""}` : "черновик, не выдана"}</span></div>
      {list.length > 0 && <button className="secondary small" onClick={() => openPrintDocument(`ТК ${card.project} ${card.item_index}`, etkPrintPages(card, revisions, Object.fromEntries(sketches.map((item) =>
        [item.operation_key, window.location.origin + techcardsApi.sketchUrl(card.id, item.operation_key, item.uploaded_at)]))))}>🖨 Печать / PDF</button>}
      {canGenerate && onRebuild && <button className="secondary small" onClick={onRebuild}>⚙️ Пересобрать</button>}
      {canEdit && !issued && card.document && <button className="primary small" disabled={busy} onClick={() => void issue()}>Выдать ЭТК</button>}
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {warnings(card.document).length > 0 && <details className="etk-warnings"><summary>⚠ Предупреждения сборки · {warnings(card.document).length}</summary>
      {warnings(card.document).map((warning) => <div key={warning}>{warning}</div>)}</details>}
    <HarnessView value={topology(card.document)} list={list} connections={etkConnectionRows(card.document?.connections)} />
    <EtkView key={card.id} card={card} canEdit={canEdit} sketches={sketches} onSketches={loadSketches} onSaved={(updated) => {
      onChange(updated);
      setNotice(`Операция сохранена${updated.status === "Выдана" ? ` · ревизия ${updated.revision}` : ""}`);
    }} />
    {!list.length && <div className="etk-empty">ЭТК ещё не собрана — открой «Генератор» и нажми «Создать».</div>}
    {revisions.length > 0 && <details className="etk-revs"><summary>Журнал ревизий · {revisions.length}</summary>
      {revisions.map((revision) => <div key={revision.id}><span>Ревизия {revision.revision} · {revision.reason}</span><span className="muted">{revision.actor_name} · {stamp(revision.occurred_at)}</span></div>)}
    </details>}
  </div>;
}

/** «Генератор» сборки: форма и ОДНА кнопка. Нет ТК — «Создать» (сборка ЭТК и выдача сразу, как файл техкарты в
    SUPPLYMAP: отдельного шага утверждения там нет). ТК уже выдана — «Пересобрать» с подтверждением, новой ревизией.
    Служебное — свод материалов, удаление черновика, JSON-конфигурация — свёрнуто в «Ещё». */
export function GeneratorPane({ card, principal, onChange, onDone }: {
  card: ProjectTechcard; principal: Principal; onChange: (card: ProjectTechcard) => void; onDone?: (card: ProjectTechcard) => void;
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

  async function build(config: Record<string, unknown>) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      let updated = await techcardsApi.autoBuild(card.id, card.version, config);
      const rebuilt = updated.status === "Выдана";
      if (!rebuilt && canIssue) {
        try { updated = await techcardsApi.issue(updated.id, updated.version); }
        catch (reason) {
          onChange(updated);
          setError(`Техкарта собрана черновиком, но не выдана: ${reason instanceof Error ? reason.message : "ошибка"}`);
          return;
        }
      }
      onChange(updated);
      const warningCount = warnings(updated.document).length;
      setNotice(`${rebuilt ? `Техкарта пересобрана · ревизия ${updated.revision}` : updated.status === "Выдана" ? "Техкарта создана" : "Черновик ЭТК собран — выдаёт технолог"}${warningCount ? ` · предупреждений: ${warningCount}` : ""}`);
      onDone?.(updated);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "ЭТК не собрана");
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
    <TcGenerator card={card} kit={kit.data} kitError={kit.error} busy={busy} onBuild={build} onDiscard={discardDraft}
      onConfig={(config) => { generatedConfig.current = config; }}
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
