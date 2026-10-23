/* T7: logic of the request-creation grid — one row per position. No React here, so it is tested on its own.
   The generic paste pieces live in gridPaste.ts and are re-exported from here. */
import { applyGridPaste, dateToIso, hasHeaderRow as hasHeader, normalizeHeader, parseTsv, type PasteResult as GenericPasteResult } from "./gridPaste";

export { dateToIso, normalizeHeader };

export type Draft = {
  purpose: string;
  project_reference: string;
  delivery_point: string;
  name: string;
  item_type: string;
  design_article: string;
  quantity: string;
  unit: string;
  link: string;
  category: string;
  quality_document: string;
  procurement_deadline: string;
  planned_date: string;
  price: string;
  price_with_vat: string;
  comment: string;
};

/** The server takes at most this many positions in one request (`CreateRequestsInput`). */
export const GRID_LIMIT = 100;

/* SUPPLYMAP rcOpenForm RC_BULK_: filled once above the grid and applied to every position. */
export const COMMON_KEYS = [
  "purpose",
  "project_reference",
  "delivery_point",
  "category",
  "quality_document",
  "procurement_deadline",
  "planned_date",
] as const;
export type CommonKey = (typeof COMMON_KEYS)[number];
export type Common = Pick<Draft, CommonKey>;

export function dateAfter(days: number) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function emptyCommon(): Common {
  return {
    purpose: "RND",
    project_reference: "",
    delivery_point: "",
    category: "Общепром",
    quality_document: "Нет",
    procurement_deadline: dateAfter(7),
    planned_date: dateAfter(14),
  };
}

export function emptyDraft(common: Common = emptyCommon()): Draft {
  return {
    ...common,
    name: "",
    item_type: "Комплектующие",
    design_article: "",
    quantity: "1",
    unit: "шт",
    link: "",
    price: "",
    price_with_vat: "",
    comment: "",
  };
}

/* rcCategoryRule / Schema.gs passportRule_: ОП → «Да» жёстко; ИП → «Да», Общепром → «Нет» по умолчанию. */
export function applyCategoryRule<T extends Pick<Draft, "category" | "quality_document">>(value: T): T {
  if (value.category === "ОП") return { ...value, quality_document: "Да" };
  if (value.category === "ИП" && !value.quality_document) return { ...value, quality_document: "Да" };
  if (value.category === "Общепром" && !value.quality_document) return { ...value, quality_document: "Нет" };
  return value;
}

/* Field labels of the form, in SUPPLYMAP REQUEST_FORM_FIELDS order. Excel headers are recognized by the
   label or by the SUPPLYMAP sheet header (rcNormH); a trailing «*» (required marker on the template) is
   ignored, so a copied template recognizes. */
export const IMPORT_FIELDS: Array<{ key: keyof Draft; label: string; aliases: string[]; required: boolean }> = [
  { key: "purpose", label: "Назначение", aliases: [], required: true },
  { key: "project_reference", label: "Цель", aliases: ["Проект", "№ проекта"], required: false },
  { key: "delivery_point", label: "Точка отгрузки", aliases: [], required: false },
  { key: "name", label: "Наименование", aliases: [], required: true },
  { key: "item_type", label: "Тип", aliases: [], required: false },
  { key: "category", label: "Категория", aliases: [], required: false },
  { key: "design_article", label: "Артикул КД", aliases: [], required: false },
  { key: "quantity", label: "Количество", aliases: ["Кол-во"], required: true },
  { key: "unit", label: "Единица", aliases: ["Ед.изм", "Ед. изм."], required: true },
  { key: "link", label: "Ссылка", aliases: [], required: false },
  { key: "quality_document", label: "Документ качества", aliases: ["Док. качества"], required: false },
  { key: "procurement_deadline", label: "Срок закупки", aliases: ["Дедлайн на закупку", "Дедлайн"], required: false },
  { key: "planned_date", label: "Плановая дата", aliases: ["План. дата"], required: false },
  { key: "price", label: "Цена", aliases: [], required: false },
  { key: "price_with_vat", label: "Цена с НДС", aliases: [], required: false },
  { key: "comment", label: "Комментарий", aliases: [], required: false },
];
export const LABEL_BY_KEY = Object.fromEntries(IMPORT_FIELDS.map((field) => [field.key, field.label])) as Record<keyof Draft, string>;
export const DATE_KEYS = new Set<keyof Draft>(["procurement_deadline", "planned_date"]);
export const TEMPLATE_HEADERS = IMPORT_FIELDS.map((field) => field.label + (field.required ? " *" : ""));

/* ── Grid columns: what a fitter sees per position, in the order a pasted block fills them. ── */
export type GridKey =
  | "name" | "item_type" | "design_article" | "quantity" | "unit" | "category"
  | "quality_document" | "planned_date" | "link" | "price" | "price_with_vat" | "comment";

export const GRID_COLUMNS: Array<{ key: GridKey; label: string; required: boolean }> = [
  { key: "name", label: "Наименование", required: true },
  { key: "item_type", label: "Тип", required: false },
  { key: "design_article", label: "Артикул КД", required: false },
  { key: "quantity", label: "Кол-во", required: true },
  { key: "unit", label: "Ед.", required: true },
  { key: "category", label: "Категория", required: false },
  { key: "quality_document", label: "Док. качества", required: false },
  { key: "planned_date", label: "План. дата", required: false },
  { key: "link", label: "Ссылка", required: false },
  { key: "price", label: "Цена", required: false },
  { key: "price_with_vat", label: "Цена с НДС", required: false },
  { key: "comment", label: "Примечание", required: false },
];

export type TsvParse =
  | { rows: Array<Partial<Draft>>; recognized: string[]; unknown: string[] }
  | { error: string };

const DATE_KEY_SET: ReadonlySet<keyof Draft> = DATE_KEYS;
const MESSAGES = {
  short: "Нужны строка заголовков и хотя бы одна строка данных.",
  noHeaders:
    "Ни один заголовок не распознан. Названия колонок должны совпадать с полями формы " +
    "(Наименование, Количество, Единица …).",
};

/** A pasted block whose first line names at least one known column is read by headers. */
export function hasHeaderRow(text: string): boolean {
  return hasHeader(text, IMPORT_FIELDS);
}

export function parseExcelTsv(text: string): TsvParse {
  return parseTsv<keyof Draft>(text, IMPORT_FIELDS, DATE_KEY_SET, MESSAGES) as TsvParse;
}

/** A pasted value in the shape the cell keeps: «1,5» → «1.5», «05.11.2026» → ISO. */
function cellValue(key: GridKey, raw: string): string {
  const value = raw.trim();
  if (key === "planned_date") return dateToIso(value);
  if (key === "quantity" || key === "price" || key === "price_with_vat") return value.replace(/\s/g, "").replace(",", ".");
  return value;
}

/** A row nobody touched: nothing typed except the defaults. It is not sent and not validated. */
export function isBlankRow(draft: Draft): boolean {
  return !draft.name.trim() && !draft.design_article.trim() && !draft.link.trim() && !draft.price.trim()
    && !draft.price_with_vat.trim() && !draft.comment.trim();
}

export type PasteResult = GenericPasteResult<Draft>;

/** Paste of a block copied from Excel/Sheets into the grid.
 *  With a header row (Наименование, Количество …) columns are matched by name and the rows go after the filled
 *  ones (blank rows are reused). Without headers the block lands from the focused cell down and to the right in
 *  the order of the grid columns. */
export function applyPaste(
  positions: Draft[],
  rowIndex: number,
  colKey: GridKey,
  text: string,
  seed: Draft,
  columns: GridKey[] = GRID_COLUMNS.map((column) => column.key),
): PasteResult | { error: string } {
  return applyGridPaste<keyof Draft, Draft>(positions, rowIndex, colKey as keyof Draft, text, {
    fields: IMPORT_FIELDS,
    dateKeys: DATE_KEY_SET,
    columns,
    limit: GRID_LIMIT,
    seed,
    isBlank: isBlankRow,
    cellValue: (key, raw) => cellValue(key as GridKey, raw),
    finalize: applyCategoryRule,
    overwriteEmpty: ["name"],
    messages: MESSAGES,
  });
}

export type RowErrors = Partial<Record<keyof Draft, string>>;

/** Per-cell validation of one position, as the server will judge it. Only name, quantity, unit and purpose are
 *  mandatory; type, quality document, point and dates are prefilled and, if cleared, filled in by the server. */
export function validateRow(draft: Draft, categories: string[]): RowErrors {
  const errors: RowErrors = {};
  if (!draft.name.trim()) errors.name = "Укажите наименование";
  const quantity = Number(draft.quantity.replace(",", "."));
  if (!draft.quantity.trim() || !Number.isFinite(quantity) || quantity <= 0) errors.quantity = "Количество больше нуля";
  if (!draft.unit.trim()) errors.unit = "Укажите единицу";
  if (draft.category.trim() && categories.length && !categories.includes(draft.category)) {
    errors.category = `Категория «${draft.category}» не из списка`;
  }
  for (const key of ["price", "price_with_vat"] as const) {
    const value = draft[key].trim();
    if (value && !(Number.isFinite(Number(value)) && Number(value) >= 0)) errors[key] = "Число, например 120.50";
  }
  if (!draft.purpose.trim()) errors.purpose = "Укажите назначение";
  return errors;
}

/** «1 позиция», «3 позиции», «5 позиций» (nominative) */
export function positionsWord(count: number): string {
  const mod100 = count % 100;
  const mod10 = count % 10;
  if (mod100 >= 11 && mod100 <= 14) return `${count} позиций`;
  if (mod10 === 1) return `${count} позиция`;
  if (mod10 >= 2 && mod10 <= 4) return `${count} позиции`;
  return `${count} позиций`;
}

/** «Оформить 1 позицию», «Оформить 3 позиции», «Оформить 100 позиций» (accusative) */
export function submitLabel(count: number): string {
  const mod100 = count % 100;
  const mod10 = count % 10;
  if (mod100 < 11 || mod100 > 14) {
    if (mod10 === 1) return `Оформить ${count} позицию`;
  }
  return `Оформить ${positionsWord(count)}`;
}

export function payloadOf(draft: Draft): Record<string, string> {
  const entries = Object.entries(draft)
    .map(([key, value]) => [key, key === "quantity" || key === "price" || key === "price_with_vat" ? value.replace(",", ".") : value] as const)
    .filter(([, value]) => value.trim() !== "");
  return Object.fromEntries(entries);
}
