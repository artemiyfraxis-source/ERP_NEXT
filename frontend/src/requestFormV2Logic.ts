/* Stage option `requests.form`: logic of the reworked «Оформить заявку» form. No React here.
   Idea: nothing is pre-filled except док. качества «Нет». The header («Общее для всех позиций») is the default, a line
   value is the override; an empty line value means «as in the header». The server fills what is omitted
   (apply_draft_defaults). The purpose is a property of the PROJECT, never chosen here; there is no категория. */
import type { InternalProjectBrief } from "./api";
import { normalizeProjectNumber } from "./projectNumber";
import {
  isBlankRow, validateRow, type Common, type Draft, type RowErrors,
} from "./requestGridLogic";

/** The purpose of every commercial (customer) project; the stored value, shown as «Коммерческий проект (заказ клиента)». */
export const COMMERCIAL_PURPOSE = "Коммерческий проект";
export const PROJECT_ERROR = "Выберите проект: заказчика или внутренний";
export const POINT_ERROR = "Выберите склад";

/** Short names of the two warehouses; the stored value stays the address. */
const POINT_CODES: Record<string, string> = {
  "г. Москва, ул. 6-я Радиальная, д. 17": "ЦАР",
  "г. Раменское, ул. 100-й Свирской Дивизии, д. 52": "ЖУК",
};
export function pointLabel(address: string): string {
  const code = POINT_CODES[address];
  return code ? `${code} — ${address}` : address;
}

/** Header = the common fields plus the chosen project: «c:<номер>» (customer), «i:<назначение>» (internal by purpose,
 *  option projects.internal off) or «p:<ВП-номер>» (an internal project, option on). */
export type Header = Common & { project: string };

export function emptyHeader(): Header {
  return {
    project: "", purpose: "", project_reference: "", delivery_point: "",
    category: "", quality_document: "Нет", procurement_deadline: "", planned_date: "",
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
export const internalProjectKey = (number: string) => `p:${number}`;
/** The «Создать внутренний проект…» item of the project list (no internal project exists yet). */
export const CREATE_INTERNAL_KEY = "new:";
export const projectKind = (header: Header): "customer" | "internal" | "" =>
  header.project.startsWith("c:") ? "customer" : header.project.startsWith("i:") || header.project.startsWith("p:") ? "internal" : "";

/** A customer project sets the number and the purpose «Коммерческий проект»; an internal purpose clears the number and
 *  sets the purpose (option projects.internal off); an internal project «ВП-001» sets its number and its own purpose.
 *  The purpose is shown read-only. */
export function chooseProject(header: Header, key: string, internal: InternalProjectBrief[] = []): Header {
  if (key.startsWith("p:")) {
    const project = internal.find((item) => item.number === key.slice(2));
    return { ...header, project: key, project_reference: key.slice(2), purpose: project?.purpose ?? "" };
  }
  if (key.startsWith("i:")) return { ...header, project: key, project_reference: "", purpose: key.slice(2) };
  if (key.startsWith("c:")) return { ...header, project: key, project_reference: key.slice(2), purpose: COMMERCIAL_PURPOSE };
  return { ...header, project: "", project_reference: "", purpose: "" };
}

/** `?project=` of «Новая заявка» in «Заявки» закупщика: the project option it names — a customer project by its number, an
 *  internal «ВП-…» (option projects.internal on; an internal purpose → its only internal project), or an internal purpose
 *  (option off). "" — nothing matches, the field stays empty. */
export function projectKeyFor(
  param: string, projects: string[], purposes: string[], internal: InternalProjectBrief[], internalOn: boolean,
): string {
  const text = param.trim();
  if (!text) return "";
  const number = normalizeProjectNumber(text);
  if (internalOn) {
    const found = internal.find((item) => item.number.toUpperCase() === number);
    if (found) return internalProjectKey(found.number);
    const byPurpose = internal.filter((item) => item.purpose.toLowerCase() === text.toLowerCase());
    if (byPurpose.length === 1) return internalProjectKey(byPurpose[0].number);
  }
  const customer = projects.find((item) => normalizeProjectNumber(item) === number && !(internalOn && /^ВП-\d+$/.test(item)));
  if (customer) return customerKey(customer);
  if (!internalOn) {
    const purpose = purposes.find((item) => item.toLowerCase() === text.toLowerCase());
    if (purpose) return internalKey(purpose);
  }
  return "";
}

/** «Применить ко всем строкам»: the lines forget their own док. качества / план. дату and follow the header. */
export function applyHeaderToAll(lines: Draft[]): Draft[] {
  return lines.map((line) => ({ ...line, quality_document: "", planned_date: "" }));
}

/** The line as it will be sent: its own value, else the header's. No категория: the server default stays. */
export function resolveLine(line: Draft, header: Header): Draft {
  return {
    ...line,
    purpose: line.purpose || header.purpose,
    project_reference: line.project_reference || header.project_reference,
    delivery_point: line.delivery_point || header.delivery_point,
    procurement_deadline: line.procurement_deadline || header.procurement_deadline,
    planned_date: line.planned_date || header.planned_date,
    category: "",
    quality_document: line.quality_document || header.quality_document,
  };
}

/** Which line fields hold their own value that differs from the header (shown as «своё»). */
export function ownFields(line: Draft, header: Header): Array<"quality_document" | "planned_date"> {
  const keys = ["quality_document", "planned_date"] as const;
  return keys.filter((key) => line[key] && line[key] !== header[key]);
}

export type FormErrors = { project?: string; delivery_point?: string; rows: RowErrors[] };

/** Errors of the whole form as the server will judge it, plus the form-level rules: project and warehouse. */
export function validateForm(lines: Draft[], header: Header, units: string[]): FormErrors {
  const rows = lines.map((line) => {
    if (isBlankRow(line)) return {} as RowErrors;
    const resolved = resolveLine(line, header);
    const errors = validateRow(resolved, []);
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
