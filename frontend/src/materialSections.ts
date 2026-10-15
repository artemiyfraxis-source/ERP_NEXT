import type { TechOperationRecord } from "./api";

/* «Материалы» расформированы (owner 15.10.2026: «оболочки из материалов перенести и нормально отсортировать, остальное
   расформировать»). A БД.МАТ row is shown in exactly one tab: «Оболочки» when the server's classifier calls it a
   covering (`coverings.py`, its ids come with GET /coverings), else the tab its «Группа» names below. A group no tab
   names (or an empty one) lands in «Расходники», where the chip «Без группы» finds the empty ones. Nothing is moved or
   rewritten in the data: the tab is derived from the row's own «Группа». */

export type MaterialTab = "mat_fix" | "mat_seal" | "mat_sleeve" | "mat_use";
export const MATERIAL_TABS: Record<MaterialTab, { label: string; groups: string[] }> = {
  // Тройники — для гофры (Schlemmer NW…), рядом с хомутами и стяжками.
  mat_fix: { label: "Крепёж и тройники", groups: ["стяжка", "хомут", "тройник"] },
  mat_seal: { label: "Уплотнители и заглушки", groups: ["уплотнитель", "заглушка"] },
  mat_sleeve: { label: "Гильзы и втулки", groups: ["гильза", "втулка"] },
  mat_use: { label: "Расходники", groups: ["клей/герметик", "лак", "припой/флюс", "нитки", "бирка", "феррит"] },
};
export const MATERIAL_FALLBACK: MaterialTab = "mat_use";
export const MATERIAL_GROUP_FIELD = "Группа";

const norm = (value: string) => String(value ?? "").trim().toLocaleLowerCase("ru-RU").replace(/ё/g, "е");
const BY_GROUP: Record<string, MaterialTab> = Object.fromEntries(Object.entries(MATERIAL_TABS)
  .flatMap(([tab, def]) => def.groups.map((group) => [norm(group), tab as MaterialTab])));

export const isMaterialTab = (tab: string): tab is MaterialTab => tab in MATERIAL_TABS;

/** The tab of a БД.МАТ row that is not a covering: by its «Группа», unknown or empty → «Расходники». */
export function materialTab(fields: Record<string, string>): MaterialTab {
  return BY_GROUP[norm(fields[MATERIAL_GROUP_FIELD] ?? "")] ?? MATERIAL_FALLBACK;
}

/** Rows of one tab: the БД.МАТ rows minus the coverings, by «Группа». */
export function materialRows(records: TechOperationRecord[], coverings: ReadonlySet<string>, tab: MaterialTab): TechOperationRecord[] {
  return records.filter((record) => !coverings.has(record.id) && materialTab(record.fields) === tab);
}

/** Counts on the tabs: every БД.МАТ row is counted once — in «Оболочки» (`cov`) or in its own tab. */
export function materialCounts(records: TechOperationRecord[], coverings: ReadonlySet<string>): Record<MaterialTab | "cov", number> {
  const counts: Record<MaterialTab | "cov", number> = { cov: 0, mat_fix: 0, mat_seal: 0, mat_sleeve: 0, mat_use: 0 };
  for (const record of records) counts[coverings.has(record.id) ? "cov" : materialTab(record.fields)] += 1;
  return counts;
}
