// Печатная форма электронной ТК (SUPPLYMAP PortalApp.html etkPrintHtml_ / etkPrint_): титул с маршрутом и
// журналом ревизий, затем по листу на операцию — шапка документа, безопасность и разделы в порядке экрана ЭТК
// (1 Вход · 2 Оснастка · 3 Ход операции · 4 Типовые дефекты · 5 Эскиз · 6 Результат), подписи. Через общий print.ts.
// Схема жгута (титул) и эскизы операций — SVG-строки из etkDraw.ts (порт etkDrawHarness_ / etkDraw*).
import type { ProjectTechcard, TechcardRevision } from "./api";
import {
  ETK_CONNECTION_COLUMNS, etkConnectionRows, etkContactSpecs, etkCrimpChips, etkDrawHarness, etkFigure, etkTolerance,
  type EtkTopologyData,
} from "./etkDraw";
import { ETK_TOOLING_SECTIONS, solderMode, toolingSections } from "./etkOperation";
import { formatDateTime, unitLabel } from "./format";
import { etkRouteLine, etkRouteOf } from "./etkRoute";
import { hasMarkSerial, markSerialList, markSerialText, type MarkSerialRange } from "./markSerial";
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
    // Аппликатор, высота обжима, отрыв — как чипы экрана: два разных контакта — по концам с именем контакта.
    ...etkCrimpChips(fields).map(([label, value]) => `${label}: ${value}`),
    fields.lengthKd && `L по КД: ${text(fields.lengthKd)}`,
    fields.cutTolerance && `Допуск реза: ${etkTolerance(fields.cutTolerance)}`,
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

/** Строки оснастки по разделам; Инструмент · Оборудование · Расходники есть всегда — пустой раздел строкой «нет»
 *  (erp-screens §6, как на экране). */
function toolingRows(items: Row[]): string[][] {
  const order = toolingSections([...ETK_TOOLING_SECTIONS, ...items.map((tool) => text(tool.section))]);
  return order.flatMap((section) => {
    const tools = items.filter((tool) => text(tool.section) === section);
    if (!tools.length) return [[section, "", "нет", "", "", "", ""]];
    return tools.map((tool) => [
      text(tool.section), text(tool.number), text(tool.name), text(tool.inventory_number),
      [tool.program && tool.program !== "-" ? text(tool.program) : "", text(tool.parameters)].filter(Boolean).join(" · "),
      text(tool.norm), unitLabel(text(tool.unit)),
    ]);
  });
}

function table(columns: string[], body: string[][]): PrintBlock[] {
  return body.length ? [{ table: { columns, rows: body } }] : [];
}

/** `sketches` — своя картинка эскиза по ключу операции (код::разъём::сторона), абсолютной ссылкой на файл в ERP.
 *  `serial` — номера изделий задания: надписи с «№ изделия» печатаются диапазоном, в конце — лист «Надписи меток»;
 *  без задания — «№ по партии». Надпись без номера печатается как раньше. */
export function etkPrintPages(card: ProjectTechcard, revisions: TechcardRevision[], sketches: Record<string, string> = {},
  serial?: MarkSerialRange | null): PrintPage[] {
  const document = object(card.document);
  const kit = { project: card.project, index: card.item_index, name: card.product_name, ...object(document.kit) };
  const revision = card.revision || 1;
  const operations = rows(document.operations);
  const harness = etkDrawHarness(document.topology as EtkTopologyData | undefined, harnessPinsOf(operations));
  const connections = etkConnectionRows(document.connections);
  const routeLine = etkRouteLine(etkRouteOf(card.document));
  const cover: PrintPage = {
    title: "Технологическая карта сборки",
    subtitle: `${text(kit.name)} · ${text(kit.project)} ${text(kit.index)} · ревизия ${revision}`,
    blocks: [
      ...head(kit, revision, ""),
      // Схема полуфабрикатов одной строкой под шапкой — только у ветвящегося маршрута.
      ...(routeLine ? [{ text: routeLine }] as PrintBlock[] : []),
      ...(harness ? [{ heading: "Схема жгута" }, { figure: harness }] as PrintBlock[] : []),
      ...(connections.length ? [{ heading: "Таблица соединений" }, { table: { columns: ETK_CONNECTION_COLUMNS, rows: connections } }] as PrintBlock[] : []),
      { heading: "Маршрут" },
      { table: {
        columns: ["№", "Код", "Операция", "Разъём / сторона", "Время операции, с", "Подгот., с"],
        rows: operations.map((operation, index) => {
          const times = object(operation.times);
          return [
            text(operation.position || index + 1), text(operation.code),
            etkOperationTitle(operation) + (operation.edited ? " (правлена)" : ""), text(operation.connector),
            text(times.operation_seconds), text(times.setup_seconds),
          ];
        }),
      } },
      ...(revisions.length ? [
        { heading: "Журнал ревизий" },
        { table: {
          columns: ["Рев.", "Дата", "Кто", "Изменение"],
          rows: revisions.map((item) => [
            String(item.revision), formatDateTime(item.occurred_at), item.actor_name, item.reason,
          ]),
        } },
      ] as PrintBlock[] : []),
    ],
    sign: `${SIGN}\nДокумент составлен в соответствии с ГОСТ 3.1407-86 · ревизия ${revision}`,
  };
  const pages = operations.map((operation, index): PrintPage => {
    const opCard = object(operation.card);
    const numbered = (items: Row[], pick: (item: Row) => string[]) => items.map((item, row) => [String(row + 1), ...pick(item)]);
    // Разделы — в порядке экрана ЭТК (erp-screens §6): Вход → Оснастка → Ход операции → Типовые дефекты → Эскиз →
    // Результат; номер считается по напечатанным разделам, как на экране.
    let shown = 0;
    const section = (title: string): PrintBlock => ({ heading: `${++shown}. ${title}` });
    const components = numbered(rows(operation.components), (item) => [
      text(item.art) + (item.kd ? ` (по КД: ${text(item.kd)}, аналог)` : ""),
      // Обжимаемый контакт: под наименованием — спецификация обжима своего конца (как на экране).
      [text(item.name), ...etkContactSpecs(object(operation.fields), item.art)].filter(Boolean).join("\n"), text(item.grn),
      text(item.norm), "", unitLabel(text(item.unit)),
    ]);
    const semis = numbered(rows(operation.semis), (item) => [text(item.name), text(item.norm), "", unitLabel(text(item.unit))]);
    const markRows = rows(object(operation.fields).marks);
    const withLength = markRows.some((mark) => text(mark.length));
    const marks = numbered(markRows, (mark) => [
      text(mark.target), markSerialText(text(mark.text), serial), text(mark.material), text(mark.place), ...(withLength ? [text(mark.length)] : []), text(mark.qty), text(mark.batch),
    ]);
    // Допуска нет в данных («длина не задана») — со знаком внимания, как «нет данных» у высоты обжима.
    const steps = rows(opCard.steps).map((step) => [text(step.kind), text(step.number), text(step.text),
      /не задан/i.test(text(step.tolerance)) ? `⚠ ${text(step.tolerance)}` : text(step.tolerance)]);
    const defects = text(opCard.defects);
    const figure = etkFigure(operation) + sketchImg(sketches[operationKeyOf(operation)]);
    const result = numbered(rows(operation.result), (item) => [text(item.name), text(item.norm), "", unitLabel(text(item.unit))]);
    const time = numbered(rows(operation.time), (item) => [text(item.name), text(item.norm), ""]);
    return {
      title: `Операция ${text(operation.position || index + 1)}. ${etkOperationTitle(operation)}${operation.connector ? ` — ${text(operation.connector)}` : ""}`,
      subtitle: facts(object(operation.fields)),
      blocks: [
        ...head(kit, revision, text(operation.code)),
        { text: text(opCard.safety) },
        section("Вход"),
        ...table(["№", "Комплектующие: артикул", "Наименование", "ГРН", "Норма", "Факт", "Ед."], components),
        ...table(["№", "Полуфабрикат на входе", "Норма", "Факт", "Ед."], semis),
        ...(components.length || semis.length ? [] : [{ text: "Комплектующих и полуфабрикатов на входе нет." }]),
        section("Оснастка: инструмент, оборудование, расходники"),
        ...table(["Раздел", "№", "Наименование", "Инв. №", "№ прог. / параметры", "Норма", "Ед."],
          [...solderRows(operation), ...toolingRows(rows(opCard.tooling))]),
        ...(marks.length || steps.length ? [
          section("Ход операции"),
          ...table(["№", "Что маркируем", "Надпись", "Материал", "Где", ...(withLength ? ["Длина, мм"] : []), "На изделие", "На партию"], marks),
          ...table(["Тип", "№", "Описание операции, материалов и оснастки", "Допуск"], steps),
        ] : []),
        ...(defects ? [section("Типовые дефекты"), { text: defects }] : []),
        ...(figure ? [section("Эскиз"), { figure }] : []),
        ...(result.length || time.length ? [
          section("Результат"),
          ...table(["№", text(operation.result_label) || "Результат", "Норма", "Факт / брак", "Ед."], result),
          ...table(["№", "Расчётное время", "Норма, мин", "Факт"], time),
        ] : []),
      ],
      sign: `${SIGN}\nДокумент составлен в соответствии с ГОСТ 3.1407-86 · ревизия ${revision}`,
    };
  });
  const labels = serial ? etkMarkLabelsPage(card, serial) : null;
  return labels ? [cover, ...pages, labels] : [cover, ...pages];
}

/** Метки с «№ изделия» во всех операциях ЭТК, по порядку. */
export function etkSerialMarks(document: unknown): Row[] {
  return rows(object(document).operations).flatMap((operation) => rows(object(operation.fields).marks))
    .filter((mark) => hasMarkSerial(text(mark.text)));
}

/** «Надписи меток» задания: строка — изделие, столбец — метка с «№ изделия»; надпись развёрнута для каждого номера.
 *  Нет таких меток — null. Один лист на задание: его печатают к маркировке (принтер хлястиков, трубок). */
export function etkMarkLabelsPage(card: ProjectTechcard, serial: MarkSerialRange): PrintPage | null {
  const marks = etkSerialMarks(card.document);
  if (!marks.length || serial.last < serial.first) return null;
  const lists = marks.map((mark) => markSerialList(text(mark.text), serial));
  const kit = { project: card.project, index: card.item_index, name: card.product_name, ...object(object(card.document).kit) };
  const columns = ["№ изделия", ...marks.map((mark) => [text(mark.target) || "Метка", text(mark.material),
    text(mark.qty) && text(mark.qty) !== "1" ? `${text(mark.qty)} шт на изделие` : ""].filter(Boolean).join(" · "))];
  return {
    title: "Надписи меток по КД",
    subtitle: [text(kit.name), `${text(kit.project)} ${text(kit.index)}`.trim(), serial.task ? `задание ${serial.task}` : "",
      `изделия № ${serial.first}–${serial.last}`].filter(Boolean).join(" · "),
    blocks: [{ table: { columns, rows: lists[0].map((_, index) => [String(serial.first + index), ...lists.map((list) => list[index])]) } }],
    sign: "Напечатал: ____________ / ________ Дата: ______    Проверил: ____________ / ________ Дата: ______",
  };
}
