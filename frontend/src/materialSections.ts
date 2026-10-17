import type { TechOperationRecord } from "./api";

/* «Материалы» расформированы (owner 15.10.2026: «оболочки из материалов перенести и нормально отсортировать, остальное
   расформировать»). A БД.МАТ row is shown in exactly one tab: «Оболочки» when the server's classifier calls it a
   covering (`coverings.py`, its ids come with GET /coverings), else the tab its «Группа» names below.
   Owner 17.10.2026: only «Расходники» stay, and only solders and fluxes are in it («там должны быть только флюсы и
   припои»); «Крепёж и тройники», «Уплотнители и заглушки», «Гильзы и втулки» are gone. A row of any other group is not
   shown in a tab (it stays in the data — nothing is moved or rewritten; the tab is derived from the row's «Группа»). */

export type MaterialTab = "mat_use";
export const SOLDER_GROUP = "припой/флюс";
export const MATERIAL_TABS: Record<MaterialTab, { label: string; groups: string[] }> = {
  mat_use: { label: "Расходники", groups: [SOLDER_GROUP] },
};
export const MATERIAL_GROUP_FIELD = "Группа";

const norm = (value: string) => String(value ?? "").trim().toLocaleLowerCase("ru-RU").replace(/ё/g, "е");
const BY_GROUP: Record<string, MaterialTab> = Object.fromEntries(Object.entries(MATERIAL_TABS)
  .flatMap(([tab, def]) => def.groups.map((group) => [norm(group), tab as MaterialTab])));

export const isMaterialTab = (tab: string): tab is MaterialTab => tab in MATERIAL_TABS;

/** The tab of a БД.МАТ row that is not a covering: by its «Группа»; a group no tab names — no tab (null). */
export function materialTab(fields: Record<string, string>): MaterialTab | null {
  return BY_GROUP[norm(fields[MATERIAL_GROUP_FIELD] ?? "")] ?? null;
}

/** Rows of one tab: the БД.МАТ rows minus the coverings, by «Группа». */
export function materialRows(records: TechOperationRecord[], coverings: ReadonlySet<string>, tab: MaterialTab): TechOperationRecord[] {
  return records.filter((record) => !coverings.has(record.id) && materialTab(record.fields) === tab);
}

/** Counts on the tabs: a БД.МАТ row is counted once — in «Оболочки» (`cov`) or in its own tab, if it has one. */
export function materialCounts(records: TechOperationRecord[], coverings: ReadonlySet<string>): Record<MaterialTab | "cov", number> {
  const counts: Record<MaterialTab | "cov", number> = { cov: 0, mat_use: 0 };
  for (const record of records) {
    const tab = coverings.has(record.id) ? "cov" : materialTab(record.fields);
    if (tab) counts[tab] += 1;
  }
  return counts;
}
