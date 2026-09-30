// Печатная форма электронной ТК (SUPPLYMAP PortalApp.html etkPrintHtml_ / etkPrint_): титул с маршрутом и
// журналом ревизий, затем по листу на операцию — шапка документа, безопасность, комплектующие, полуфабрикат,
// оснастка, ход операции, дефекты, эскиз, результат, время, подписи. Идёт через общий контракт print.ts.
// Схема жгута (титул) и эскизы операций — SVG-строки из etkDraw.ts (порт etkDrawHarness_ / etkDraw*).
import type { ProjectTechcard, TechcardRevision } from "./api";
import { ETK_CONNECTION_COLUMNS, etkConnectionRows, etkDrawHarness, etkFigure, type EtkTopologyData } from "./etkDraw";
import { solderMode, toolingSections } from "./etkOperation";
import type { PrintBlock, PrintPage } from "./print";

type Row = Record<string, unknown>;

const SIGN =
  "Разработал: ____________ / ________ Дата: ______    Проверил: ____________ / ________ Дата: ______    " +
  "Задание выполнил: ____________ / ________ Дата: ______\n" +
  "Утвердил: ____________ / ________ Дата: ______    Задание принял: ____________ / ________ Дата: ______    " +
  "ОТК провёл: ____________ / ________ Дата: ______";

function text(value: unknown): string {
  return value == null ? "" : String(value);
}

function rows(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") as Row[] : [];
}

function object(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

/** SUPPLYMAP etkUnwrap_ keeps the text; the safety heading stays in print (only the screen folds it). */
export function etkOperationTitle(operation: Row): string {
  return text(object(operation.card).name) || text(operation.title) || text(operation.type) || text(operation.code);
}

function head(kit: Row, revision: number, code: string): PrintBlock[] {
  const project = text(kit.project);
  return [
    { table: { columns: ["ПРЕДПРИЯТИЕ", "ОБОЗНАЧЕНИЕ ДОКУМЕНТА", "РЕВИЗИЯ", "РМ"], rows: [[
      "ООО «ЗЭП «ФРАКСИС»", `ТК.${project.replace(/\.$/, "")}${code ? `.${code}` : ""}`, String(revision), "",
    ]] } },
    { table: { columns: ["НАИМЕНОВАНИЕ ИЗДЕЛИЯ (СБОРКИ ПО КД)", "КОД ИЗДЕЛИЯ", "НОМЕР ПРОЕКТА"], rows: [[
      text(kit.name), text(kit.index), project,
    ]] } },
  ];
}

function facts(fields: Row): string {
  const twist = object(fields.twist);
  return [
    fields.applicator && `Аппликатор: ${text(fields.applicator)}`,
    "crimpHeight" in fields && `H обжима: ${fields.crimpHeight ? `${text(fields.crimpHeight)} мм` : "⚠ нет данных"}`,
    fields.pullForce && `Отрыв: ${text(fields.pullForce)} Н`,
    fields.lengthKd && `L по КД: ${text(fields.lengthKd)}`,
    fields.cutTolerance && `Допуск реза: ${text(fields.cutTolerance)}`,
    fields.twist && `Шаг свивки: ${twist.pitch ? `${text(twist.pitch)} мм` : "по КД"}`,
  ].filter(Boolean).join(" · ");
}

/** Режим пайки строками таблицы оснастки: жало и температура обязательны (значение ERP по умолчанию помечено). */
function solderRows(operation: Row): string[][] {
  const mode = solderMode(operation);
  if (!mode) return [];
  const mark = (assumed: boolean) => assumed ? " (по умолчанию ERP, уточнить)" : "";
  return [
    ["Режим пайки", "", `${mode.method === "bath" ? "Паяльник" : "Жало паяльника"}: ${mode.tip}${mark(mode.tipAssumed)}`, "", "", "", ""],
    ["Режим пайки", "", `${mode.method === "bath" ? "Температура ванны" : mode.method === "induction" ? "Нагрев" : "Температура жала"}: ${mode.temperature}${mode.tolerance ? ` (${mode.tolerance})` : ""}${mark(mode.temperatureAssumed)}`, "", "", "", ""],
  ];
}

function operationKeyOf(operation: Row): string {
  return `${text(operation.code).trim()}::${text(operation.connector).trim()}::${text(operation.side).trim()}`;
}

function sketchImg(url: string | undefined): string {
  return url ? `<img class="etk-sketch-print" src="${url.replaceAll("&", "&amp;").replaceAll('"', "&quot;")}" alt="Эскиз" style="max-width:100%;max-height:120mm;display:block;margin-top:4mm"/>` : "";
}

/** Распиновка по узлу схемы «артикул::сторона» — как harnessPins на экране. */
function harnessPinsOf(operations: Row[]): Record<string, Record<number, string>> {
  const out: Record<string, Record<number, string>> = {};
  operations.forEach((operation) => {
    const article = text(operation.connector_article).trim();
    const wires = rows(object(operation.fields).wires);
    if (!article) return;
    wires.forEach((wire) => (Array.isArray(wire.pins) ? wire.pins as number[] : []).forEach((pin) => {
      (out[`${article}::${text(operation.side)}`] ||= {})[pin] = text(wire.art || wire.name);
    }));
  });
  return out;
}

function sortedTooling(items: Row[]): Row[] {
  const order = toolingSections(items.map((tool) => text(tool.section)));
  return order.flatMap((section) => items.filter((tool) => text(tool.section) === section));
}

/** `sketches` — своя картинка эскиза по ключу операции (код::разъём::сторона), абсолютной ссылкой на файл в ERP. */
export function etkPrintPages(card: ProjectTechcard, revisions: TechcardRevision[], sketches: Record<string, string> = {}): PrintPage[] {
  const document = object(card.document);
  const kit = { project: card.project, index: card.item_index, name: card.product_name, ...object(document.kit) };
  const revision = card.revision || 1;
  const operations = rows(document.operations);
  const harness = etkDrawHarness(document.topology as EtkTopologyData | undefined, harnessPinsOf(operations));
  const connections = etkConnectionRows(document.connections);
  const cover: PrintPage = {
    title: "Технологическая карта сборки",
    subtitle: `${text(kit.name)} · ${text(kit.project)} ${text(kit.index)} · ревизия ${revision}`,
    blocks: [
      ...head(kit, revision, ""),
      ...(harness ? [{ heading: "Схема жгута" }, { figure: harness }] as PrintBlock[] : []),
      ...(connections.length ? [{ heading: "Таблица соединений" }, { table: { columns: ETK_CONNECTION_COLUMNS, rows: connections } }] as PrintBlock[] : []),
      { heading: "Маршрут" },
      { table: {
        columns: ["№", "Код", "Операция", "Разъём / сторона", "Время операции, с", "Подгот., с"],
        rows: operations.map((operation, index) => {
          const times = object(operation.times);
          return [
            text(operation.position || index + 1), text(operation.code),
            etkOperationTitle(operation) + (operation.edited ? " ✏" : ""), text(operation.connector),
            text(times.operation_seconds), text(times.setup_seconds),
          ];
        }),
      } },
      ...(revisions.length ? [
        { heading: "Журнал ревизий" },
        { table: {
          columns: ["Рев.", "Дата", "Кто", "Изменение"],
          rows: revisions.map((item) => [
            String(item.revision), new Date(item.occurred_at).toLocaleString("ru-RU"), item.actor_name, item.reason,
          ]),
        } },
      ] as PrintBlock[] : []),
    ],
    sign: `${SIGN}\nДокумент составлен в соответствии с ГОСТ 3.1407-86 · ревизия ${revision}`,
  };
  const pages = operations.map((operation, index): PrintPage => {
    const opCard = object(operation.card);
    const numbered = (items: Row[], pick: (item: Row) => string[]) => items.map((item, row) => [String(row + 1), ...pick(item)]);
    return {
      title: `Операция ${text(operation.position || index + 1)}. ${etkOperationTitle(operation)}${operation.connector ? ` — ${text(operation.connector)}` : ""}`,
      subtitle: facts(object(operation.fields)),
      blocks: [
        ...head(kit, revision, text(operation.code)),
        { text: text(opCard.safety) },
        { table: {
          columns: ["№", "Комплектующие: артикул", "Наименование", "ГРН", "Норма", "Факт", "Ед."],
          rows: numbered(rows(operation.components), (item) => [
            text(item.art) + (item.kd ? ` (по КД: ${text(item.kd)}, аналог)` : ""), text(item.name), text(item.grn),
            text(item.norm), "", text(item.unit),
          ]),
        } },
        { table: {
          columns: ["№", "Полуфабрикат на входе", "Норма", "Факт", "Ед."],
          rows: numbered(rows(operation.semis), (item) => [text(item.name), text(item.norm), "", text(item.unit)]),
        } },
        { table: {
          columns: ["Раздел", "№", "Наименование", "Инв. №", "№ прог. / параметры", "Норма", "Ед."],
          rows: [...solderRows(operation), ...sortedTooling(rows(opCard.tooling)).map((tool) => [
            text(tool.section), text(tool.number), text(tool.name), text(tool.inventory_number),
            [tool.program && tool.program !== "-" ? text(tool.program) : "", text(tool.parameters)].filter(Boolean).join(" · "),
            text(tool.norm), text(tool.unit),
          ])],
        } },
        { table: {
          columns: ["Тип", "№", "Описание операции, материалов и оснастки", "Допуск"],
          rows: rows(opCard.steps).map((step) => [text(step.kind), text(step.number), text(step.text), text(step.tolerance)]),
        } },
        { text: text(opCard.defects) },
        { figure: etkFigure(operation) + sketchImg(sketches[operationKeyOf(operation)]) },
        { table: {
          columns: ["№", text(operation.result_label) || "Результат", "Норма", "Факт / брак", "Ед."],
          rows: numbered(rows(operation.result), (item) => [text(item.name), text(item.norm), "", text(item.unit)]),
        } },
        { table: {
          columns: ["№", "Расчётное время", "Норма, мин", "Факт"],
          rows: numbered(rows(operation.time), (item) => [text(item.name), text(item.norm), ""]),
        } },
      ],
      sign: `${SIGN}\nДокумент составлен в соответствии с ГОСТ 3.1407-86 · ревизия ${revision}`,
    };
  });
  return [cover, ...pages];
}
