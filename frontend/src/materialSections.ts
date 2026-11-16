import type { TechOperationRecord } from "./api";

/* Heat-shrink and coverings have their own storage tables. Remaining БД.МАТ rows use the purpose tabs below.
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

/** Purpose tabs read only the remaining material table. */
export function materialRows(records: TechOperationRecord[], tab: MaterialTab): TechOperationRecord[] {
  return records.filter((record) => record.tab === "mat" && materialTab(record.fields) === tab);
}

/** Physical covering catalog counts come from /bases, never a second client-side classification. */
export function materialCounts(records: TechOperationRecord[]): Record<MaterialTab, number> {
  const counts: Record<MaterialTab, number> = { mat_use: 0 };
  for (const record of records) {
    const tab = record.tab === "mat" ? materialTab(record.fields) : null;
    if (tab) counts[tab] += 1;
  }
  return counts;
}
