// Печатная форма электронной ТК (SUPPLYMAP PortalApp.html etkPrintHtml_ / etkPrint_): титул с маршрутом и
// журналом ревизий, затем по листу на операцию — шапка документа, безопасность, комплектующие, полуфабрикат,
// оснастка, ход операции, дефекты, результат, время, подписи. Идёт через общий контракт print.ts.
// Эскизы (etkDraw*) и схема жгута в печать не попадают: в ERP они рисуются React-компонентами, не SVG-строкой.
import type { ProjectTechcard, TechcardRevision } from "./api";
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

export function etkPrintPages(card: ProjectTechcard, revisions: TechcardRevision[]): PrintPage[] {
  const document = object(card.document);
  const kit = { project: card.project, index: card.item_index, name: card.product_name, ...object(document.kit) };
  const revision = card.revision || 1;
  const operations = rows(document.operations);
  const cover: PrintPage = {
    title: "Технологическая карта сборки",
    subtitle: `${text(kit.name)} · ${text(kit.project)} ${text(kit.index)} · ревизия ${revision}`,
    blocks: [
      ...head(kit, revision, ""),
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
          rows: rows(opCard.tooling).map((tool) => [
            text(tool.section), text(tool.number), text(tool.name), text(tool.inventory_number),
            [tool.program && tool.program !== "-" ? text(tool.program) : "", text(tool.parameters)].filter(Boolean).join(" · "),
            text(tool.norm), text(tool.unit),
          ]),
        } },
        { table: {
          columns: ["Тип", "№", "Описание операции, материалов и оснастки", "Допуск"],
          rows: rows(opCard.steps).map((step) => [text(step.kind), text(step.number), text(step.text), text(step.tolerance)]),
        } },
        { text: text(opCard.defects) },
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
