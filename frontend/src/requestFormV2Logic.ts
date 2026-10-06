/* Stage option `requests.form`: logic of the reworked «Оформить заявку» form. No React here.
   Idea: nothing is pre-filled. The header («Общее для всех позиций») is the default, a line value is the override;
   an empty line value means «as in the header». The server fills what is omitted (apply_draft_defaults). */
import {
  isBlankRow, validateRow, type Common, type Draft, type RowErrors,
} from "./requestGridLogic";

export const DEFAULT_PURPOSE = "Обеспечение производства";
export const CATEGORY_HINT = "Определяет документ качества: ОП — обязателен, ИП — по умолчанию да, Общепром — по умолчанию нет";
export const PROJECT_ERROR = "Выберите проект: заказчика или внутренний";
export const POINT_ERROR = "Выберите склад";
export const DEFAULT_CATEGORY = "Общепром";

/** Short names of the two warehouses; the stored value stays the address. */
const POINT_CODES: Record<string, string> = {
  "г. Москва, ул. 6-я Радиальная, д. 17": "ЦАР",
  "г. Раменское, ул. 100-й Свирской Дивизии, д. 52": "ЖУК",
};
export function pointLabel(address: string): string {
  const code = POINT_CODES[address];
  return code ? `${code} — ${address}` : address;
}

/** Header = the common fields plus the chosen project: «c:<номер>» (customer) or «i:<назначение>» (internal). */
export type Header = Common & { project: string };

export function emptyHeader(): Header {
  return {
    project: "", purpose: "", project_reference: "", delivery_point: "",
    category: "", quality_document: "", procurement_deadline: "", planned_date: "",
  };
}

export function emptyLine(): Draft {
  return {
    purpose: "", project_reference: "", delivery_point: "", name: "", item_type: "", design_article: "",
    quantity: "", unit: "", link: "", category: "", quality_document: "", procurement_deadline: "",
    planned_date: "", price: "", price_with_vat: "", comment: "",
  };
}

export const customerKey = (number: string) => `c:${number}`;
export const internalKey = (purpose: string) => `i:${purpose}`;
export const projectKind = (header: Header): "customer" | "internal" | "" =>
  header.project.startsWith("c:") ? "customer" : header.project.startsWith("i:") ? "internal" : "";

/** A customer project sets the number and leaves Назначение editable (default «Обеспечение производства»);
 *  an internal project clears the number and sets Назначение (the form locks it). */
export function chooseProject(header: Header, key: string, purposes: string[]): Header {
  if (key.startsWith("i:")) return { ...header, project: key, project_reference: "", purpose: key.slice(2) };
  if (key.startsWith("c:")) {
    const wasInternal = projectKind(header) === "internal";
    const fallback = purposes.includes(DEFAULT_PURPOSE) ? DEFAULT_PURPOSE : "";
    return { ...header, project: key, project_reference: key.slice(2), purpose: wasInternal || !header.purpose ? fallback : header.purpose };
  }
  return { ...header, project: "", project_reference: "", purpose: "" };
}

/** Header category changed: ОП forces the quality document to «Да». */
export function setHeaderCategory(header: Header, category: string): Header {
  return { ...header, category, quality_document: category === "ОП" ? "Да" : header.quality_document };
}

/** «Применить ко всем строкам»: the lines forget their own категория / док. качества / план. дату and follow the header. */
export function applyHeaderToAll(lines: Draft[]): Draft[] {
  return lines.map((line) => ({ ...line, category: "", quality_document: "", planned_date: "" }));
}

/** The line as it will be sent: its own value, else the header's. */
export function resolveLine(line: Draft, header: Header): Draft {
  const category = line.category || header.category;
  let quality = line.quality_document || (line.category ? "" : header.quality_document);
  if (category === "ОП") quality = "Да";
  return {
    ...line,
    purpose: line.purpose || header.purpose,
    project_reference: line.project_reference || header.project_reference,
    delivery_point: line.delivery_point || header.delivery_point,
    procurement_deadline: line.procurement_deadline || header.procurement_deadline,
    planned_date: line.planned_date || header.planned_date,
    category,
    quality_document: quality,
  };
}

/** Which line fields hold their own value that differs from the header (shown as «своё»). */
export function ownFields(line: Draft, header: Header): Array<"category" | "quality_document" | "planned_date"> {
  const keys = ["category", "quality_document", "planned_date"] as const;
  return keys.filter((key) => line[key] && line[key] !== header[key]);
}

export type FormErrors = { project?: string; delivery_point?: string; rows: RowErrors[] };

/** Errors of the whole form as the server will judge it, plus the form-level rules: project and warehouse. */
export function validateForm(
  lines: Draft[], header: Header, categories: string[], units: string[],
): FormErrors {
  const rows = lines.map((line) => {
    if (isBlankRow(line)) return {} as RowErrors;
    const resolved = resolveLine(line, header);
    const errors = validateRow(resolved, categories);
    delete errors.purpose; // a missing purpose is the form-level «Выберите проект»
    if (resolved.unit.trim() && units.length && !units.includes(resolved.unit)) errors.unit = "Выберите единицу из списка";
    return errors;
  });
  const sent = lines.filter((line) => !isBlankRow(line)).map((line) => resolveLine(line, header));
  return {
    project: sent.some((line) => !line.purpose.trim()) ? PROJECT_ERROR : undefined,
    delivery_point: sent.some((line) => !line.delivery_point.trim()) ? POINT_ERROR : undefined,
    rows,
  };
}

export const hasErrors = (errors: FormErrors) =>
  Boolean(errors.project || errors.delivery_point || errors.rows.some((row) => Object.keys(row).length));

/** Empty optional fields are left out of the payload — the server applies its defaults to omitted ones. */
export function payloadV2(line: Draft, header: Header): Record<string, string> {
  const resolved = resolveLine(line, header);
  const entries = Object.entries(resolved)
    .map(([key, value]) => [key, key === "quantity" || key === "price" || key === "price_with_vat" ? value.replace(",", ".") : value.trim()] as const)
    .filter(([, value]) => value !== "");
  return Object.fromEntries(entries);
}
