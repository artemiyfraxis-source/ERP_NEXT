import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CoveringsTable, rangeText, shrinkTemperatureText, tempText } from "./Coverings";
import { referencesApi, type CoveringRow, type CoveringsResult, type CoveringTab, type ShrinkTemperature, type TechOperationRecord } from "./api";
import { blockLines, blockTables } from "./test/blocks";
import { ColumnViewsProvider } from "./ColumnViews";

/* «Оболочки» (owner 12.10.2026, 15.10.2026): a table per kind with its own fields in the owner's order (термоусадка,
   кембрик, гофра, оплётки, ленты), «Прочее» only when not empty, a manual move. Since 25.10.2026 one list in blocks, as
   every list of «База данных»: one search, one «Колонки» / «Excel», one row of chips; a block per kind with its own table. */

const base: CoveringRow = {
  id: "", name: "", group: "tut", group_source: "по группе", auto_group: "tut", datasheet: "", unit: "м",
  before_mm: null, after_mm: null, ratio: "", wall_mm: null, adhesive: "", nominal_mm: null, min_mm: null, max_mm: null,
  density: "", inner_mm: null, outer_mm: null, split: null, material: "", material_short: "", temp_min_c: null, temp_max_c: null,
  color: "", maker: "", series: "", series_family: "", notes: [],
};
const row = (patch: Partial<CoveringRow>): CoveringRow => ({ ...base, ...patch });
const titles = {
  tut: "ТУТ — термоусадочная трубка", ttk: "ТТК — термоусадочная трубка с клеем", cambric: "Кембрик — трубка ПВХ",
  corrugated: "Гофра", braid: "Оплётки", tape: "Ленты", other: "Прочее",
};
const DATA: CoveringsResult = {
  counts: { tut: 2, ttk: 1, cambric: 1, corrugated: 1, braid: 1, tape: 1, other: 0 },
  titles,
  rows: [
    row({ id: "t1", name: "ТУТнг 4/2 черная", series: "ТУТнг-LS", before_mm: 4, after_mm: 2, ratio: "2:1", material: "полиолефин", material_short: "полиолефин", temp_min_c: -55, temp_max_c: 125, color: "черный", maker: "КВТ Калуга" }),
    row({ id: "t2", name: "ТУТнг 12/6 черная", series: "ТУТнг-LS", before_mm: 12, after_mm: 6, ratio: "2:1" }),
    row({ id: "k1", name: "ТТК 6/2 черная", group: "ttk", auto_group: "ttk", series: "ТТК(3:1)", before_mm: 6, after_mm: 2, ratio: "3:1", adhesive: "да" }),
    row({ id: "b1", name: "Оплётка GTRPA-10", group: "braid", auto_group: "braid", series: "GTRPA-10", nominal_mm: 10, min_mm: 10, max_mm: 16, material: "полиамид (нейлон)", material_short: "ПА" }),
    row({ id: "g1", name: "Трубка гофрированная 8,5мм", group: "corrugated", auto_group: "corrugated", inner_mm: 8.5, outer_mm: 12, split: true, material: "полипропилен", material_short: "ПП" }),
    row({ id: "c1", name: "Трубка ТВ-40 ПВХ 4мм (кембрик) чёрная", group: "cambric", auto_group: "cambric", series: "ТВ-40", before_mm: 4 }),
    row({ id: "o1", name: "Изолента ПВХ 15х0,13х20м", group: "tape", auto_group: "tape", width_mm: 15 }),
  ],
};

const tabOf = (item: CoveringRow): CoveringTab => item.group === "tut" || item.group === "ttk" ? "heat_shrink" : "cov";
const rawRecord = (item: CoveringRow): TechOperationRecord => ({ id: item.id, tab: tabOf(item), display_text: item.name, source_sheet: "БД.МАТ", fields: { Наименование: item.name } });
function mockCoverings(data = DATA) {
  return vi.spyOn(referencesApi, "coverings").mockImplementation(async (tab = "cov") => ({
    ...data, rows: data.rows.filter((item) => item.group === "other" || tabOf(item) === tab),
  }));
}
beforeEach(() => {
  vi.spyOn(referencesApi, "techOperations").mockImplementation(async (tab) => ({ metadata: null, records: DATA.rows.filter((item) => tabOf(item) === tab).map(rawRecord) }));
});
afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });
// The table of a block (a kind, or a sub-kind of ТУТ / ТТК) by its title.
const section = (name: string) => blockTables().find((table) => table.getAttribute("aria-label") === name) ?? null;

it("formats ranges and temperatures the way the shop reads them", () => {
  expect(rangeText(6, 12)).toBe("6–12");
  expect(rangeText(6.4, null)).toBe("");
  expect(tempText(-55, 125)).toBe("−55 … +125");
  expect(tempText(null, null)).toBe("");
});

it("shows only non-shrink coverings with the shared block layout and their own columns", async () => {
  mockCoverings();
  render(<CoveringsTable canEdit={false} />);
  expect(await screen.findByText("Оплётка GTRPA-10")).toBeInTheDocument();
  expect(screen.queryByText("ТУТнг 4/2 черная")).toBeNull();
  expect(blockLines()).toEqual(["Кембрик — трубка ПВХ · 1",
    "Гофра · 1", "Оплётки · 1", "Ленты · 1"]);
  // one toolbar for the whole list (25.10): one search, one «Колонки», one «Excel»
  expect(screen.getAllByRole("button", { name: "Колонки" })).toHaveLength(1);
  expect(screen.getAllByRole("button", { name: "Excel" })).toHaveLength(1);
  const heads = (group: string) => within(section(group) as HTMLElement).getAllByRole("columnheader").map((head) => head.textContent);
  expect(heads("Оплётки")).toEqual(expect.arrayContaining(["Ø номинал, мм", "Диапазон Ø, мм"]));
  expect(heads("Оплётки")).not.toContain("Ø до усадки, мм"); // a block shows only the fields of its kind
  expect(heads("Гофра")).toEqual(expect.arrayContaining(["Ø внутр., мм", "Ø наружн., мм", "Разрезная"]));
  expect(heads("Кембрик — трубка ПВХ")).toEqual(expect.arrayContaining(["Ø, мм", "Серия"]));
  expect(heads("Ленты")).toEqual(expect.arrayContaining(["Ширина, мм"]));
  expect(screen.getByText("10–16")).toBeInTheDocument();
  // nothing behind a toggle any more: the tapes are on the screen, the series is a column, not a group row
  expect(within(section("Ленты") as HTMLElement).getByText("Изолента ПВХ 15х0,13х20м")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /прочее/i })).toBeNull();
  expect(screen.queryByText("Без серии")).toBeNull();
});

it("keeps the server's order inside a kind: size, then name", async () => {
  mockCoverings({
    ...DATA, rows: [
      row({ id: "a", name: "ТУТ 1,5/0,75 черная", series: "20-1506", before_mm: 1.5 }),
      row({ id: "b", name: "ТУТнг 2/1 черная", series: "ТУТнг-LS", before_mm: 2 }),
      row({ id: "c", name: "ТУТ(HF) 10/5 белая", series: "ТУТ(HF)", before_mm: 10 }),
    ],
  });
  render(<CoveringsTable tab="heat_shrink" canEdit={false} />);
  await screen.findByText("ТУТнг 2/1 черная");
  const names = [...(section("ТУТ — термоусадочная трубка") as HTMLElement).querySelectorAll("tbody tr")].map((item) => item.querySelector("strong")?.textContent);
  expect(names).toEqual(["ТУТ 1,5/0,75 черная", "ТУТнг 2/1 черная", "ТУТ(HF) 10/5 белая"]);
});

it("searches across the groups", async () => {
  mockCoverings();
  render(<CoveringsTable canEdit={false} />);
  await screen.findByText("Оплётка GTRPA-10");
  fireEvent.change(screen.getByRole("searchbox", { name: "Поиск по оболочкам" }), { target: { value: "gtrpa" } });
  expect(screen.getByText("Оплётка GTRPA-10")).toBeInTheDocument();
  expect(screen.queryByText("ТУТнг 4/2 черная")).toBeNull();
  expect(blockLines()).toEqual(["Оплётки · 1"]); // the empty blocks are not shown
});

it("moves a row to another group by hand and back to «Авто»", async () => {
  const load = mockCoverings();
  const save = vi.spyOn(referencesApi, "setCoveringGroup").mockResolvedValue({} as never);
  render(<CoveringsTable canEdit />);
  fireEvent.click(await screen.findByText("Гофра 8,5мм".replace("Гофра", "Трубка гофрированная")));
  const select = await screen.findByRole("combobox", { name: "Раздел оболочек" });
  expect(select).toHaveValue("");
  fireEvent.change(select, { target: { value: "Прочее" } });
  await waitFor(() => expect(save).toHaveBeenCalledWith("g1", "Прочее"));
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
});

it("edits a heat-shrink column with its old value; parsed-only values are not editable fields", async () => {
  const load = mockCoverings();
  vi.spyOn(referencesApi, "techOperations").mockResolvedValue({ metadata: null, records: [
    { id: "t1", tab: "heat_shrink", display_text: "ТУТнг 4/2 черная", source_sheet: "БД.МАТ", fields: { Наименование: "ТУТнг 4/2 черная", Цвет: "черный", "Размер до, мм": "4" } },
    { id: "t2", tab: "heat_shrink", display_text: "ТУТнг 12/6 черная", source_sheet: "БД.МАТ", fields: { Наименование: "ТУТнг 12/6 черная" } },
  ] });
  const edit = vi.spyOn(referencesApi, "editRecord").mockResolvedValue({ id: "t1", tab: "heat_shrink", display_text: "", source_sheet: "", fields: {} });
  render(<CoveringsTable tab="heat_shrink" canEdit />);
  await screen.findByText("ТУТнг 4/2 черная");
  const tut = section("ТУТ — термоусадочная трубка") as HTMLElement;
  const heads = within(tut).getAllByRole("columnheader").map((head) => head.textContent);
  const cellOf = (name: string, column: string) => within(within(tut).getByText(name).closest("tr") as HTMLElement).getAllByRole("cell")[heads.indexOf(column)] as HTMLElement;
  expect(cellOf("ТУТнг 4/2 черная", "Наименование")).not.toHaveClass("dt-editable");
  expect(cellOf("ТУТнг 12/6 черная", "Цвет")).not.toHaveClass("dt-editable"); // у записи нет колонки «Цвет»
  const colour = cellOf("ТУТнг 4/2 черная", "Цвет");
  fireEvent.click(colour);
  fireEvent.keyDown(colour, { key: "Enter" });
  const input = within(colour).getByRole("textbox");
  fireEvent.change(input, { target: { value: "белый" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(edit).toHaveBeenCalledWith("t1", [{ field: "Цвет", old: "черный", value: "белый" }]));
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
});

it("has «Без размера» and «Без производителя» chips over every group", async () => {
  mockCoverings({ ...DATA, rows: [...DATA.rows, row({ id: "t3", name: "ТУТ без размера" })] });
  render(<CoveringsTable tab="heat_shrink" canEdit={false} />);
  await screen.findByText("ТУТ без размера");
  fireEvent.click(screen.getByRole("button", { name: /^Без размера · 1/ }));
  expect(screen.queryByText("ТУТнг 4/2 черная")).toBeNull();
  expect(within(section("ТУТ — термоусадочная трубка") as HTMLElement).getByText("ТУТ без размера")).toBeInTheDocument();
  expect(blockLines()).toEqual(["ТУТ — термоусадочная трубка · 1"]);
  fireEvent.click(screen.getByRole("button", { name: /^Без производителя/ }));
  expect(screen.getByText("ТУТнг 12/6 черная")).toBeInTheDocument();
});

it("splits ТУТ into a table per kind, in the server's order, and search hides the empty ones", async () => {
  const rows = [
    row({ id: "p1", name: "ТУТ 10/5 черная", kind: "plain", before_mm: 10, after_mm: 5 }),
    row({ id: "n1", name: "ТУТнг 4/2 черная", kind: "ng", before_mm: 4, after_mm: 2 }),
    row({ id: "r1", name: "ТУТ(3:1)нг-LS 6/2 черная", kind: "r3", before_mm: 6, after_mm: 2, ratio: "3:1" }),
  ];
  mockCoverings({
    ...DATA, counts: { ...DATA.counts, tut: 3, ttk: 0 }, rows,
    kinds: { tut: [{ id: "plain", title: "ТУТ — обычная (2:1)" }, { id: "ng", title: "ТУТнг — не распространяющая горение" }, { id: "r3", title: "ТУТ 3:1 и более" }] },
  });
  render(<CoveringsTable tab="heat_shrink" canEdit={false} />);
  await screen.findByText("ТУТ 10/5 черная");
  // ТУТ is a section heading with all its rows; each kind — a block under it
  expect(screen.getByRole("heading", { level: 2, name: "ТУТ — термоусадочная трубка 3" })).toBeInTheDocument();
  expect([...document.querySelectorAll(".dt-sub-title")].map((item) => item.textContent?.replace(/^[▸▾]\s*/, ""))).toEqual([
    "ТУТ — обычная (2:1) 1", "ТУТнг — не распространяющая горение 1", "ТУТ 3:1 и более 1"]);
  expect(within(section("ТУТнг — не распространяющая горение") as HTMLElement).getByText("ТУТнг 4/2 черная")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Поиск по термоусадке"), { target: { value: "3:1" } });
  expect(document.querySelectorAll(".dt-sub-title")).toHaveLength(1);
  expect(screen.getByRole("heading", { level: 2, name: "ТУТ — термоусадочная трубка 1" })).toBeInTheDocument();
});

it("loads independent catalogs and schemas when switching tabs, with ТУТ and ТТК together", async () => {
  const load = mockCoverings();
  const view = render(<CoveringsTable tab="heat_shrink" canEdit={false} />);
  await screen.findByText("ТУТнг 4/2 черная");
  expect(screen.getByText("ТТК 6/2 черная")).toBeInTheDocument();
  expect(screen.queryByText("Оплётка GTRPA-10")).toBeNull();
  expect(load).toHaveBeenCalledWith("heat_shrink");
  expect(referencesApi.techOperations).toHaveBeenCalledWith("heat_shrink", "", 5000);
  view.rerender(<CoveringsTable tab="cov" canEdit={false} />);
  await screen.findByText("Оплётка GTRPA-10");
  expect(screen.queryByText("ТУТнг 4/2 черная")).toBeNull();
  expect(referencesApi.techOperations).toHaveBeenCalledWith("cov", "", 5000);
});

it("keeps unresolved rows visible with an explicit warning and filter", async () => {
  mockCoverings({ ...DATA, rows: [...DATA.rows, row({ id: "u", name: "Неопределённая трубка", group: "other", classification_status: "unknown" })] });
  render(<CoveringsTable tab="heat_shrink" canEdit={false} />);
  await screen.findByText("Неопределённая трубка");
  expect(screen.getByText(/Требует уточнения раздела/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^Уточнить раздел · 1/ }));
  expect(screen.getByText("Неопределённая трубка")).toBeInTheDocument();
  expect(screen.queryByText("ТУТнг 4/2 черная")).toBeNull();
});

it("creates a row in an empty heat-shrink table and can add its own columns", async () => {
  mockCoverings({ ...DATA, rows: [] });
  vi.mocked(referencesApi.techOperations).mockResolvedValue({ metadata: null, columns: ["Наименование", "Группа"], custom_columns: [], records: [] });
  const create = vi.spyOn(referencesApi, "createRecord").mockResolvedValue({ id: "new", tab: "heat_shrink", display_text: "ТУТ 6/3", source_sheet: "", fields: {} });
  const add = vi.spyOn(referencesApi, "addColumn").mockResolvedValue({ tab: "heat_shrink", name: "Полка", records: 0 });
  render(<CoveringsTable tab="heat_shrink" canEdit />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Столбец" }));
  fireEvent.change(screen.getByLabelText("Название столбца"), { target: { value: "Полка" } });
  fireEvent.click(screen.getByRole("button", { name: "Добавить столбец" }));
  await waitFor(() => expect(add).toHaveBeenCalledWith("heat_shrink", "Полка"));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Новый столбец" })).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "+ Запись" }));
  fireEvent.change(screen.getByLabelText("Поле Наименование"), { target: { value: "ТУТ 6/3" } });
  fireEvent.change(screen.getByLabelText("Поле Группа"), { target: { value: "ТУТ" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить запись" }));
  await waitFor(() => expect(create).toHaveBeenCalledWith("heat_shrink", { Наименование: "ТУТ 6/3", Группа: "ТУТ" }));
});

it("keeps fields removed from the list in the card and saves only changed values", async () => {
  mockCoverings();
  const original = { ...rawRecord(DATA.rows[0]), fields: { Наименование: DATA.rows[0].name, Цвет: "черный", "Старое поле": "сохранено" } };
  vi.mocked(referencesApi.techOperations).mockResolvedValue({ metadata: null, columns: ["Наименование", "Цвет"], records: [original] });
  const edit = vi.spyOn(referencesApi, "editRecord").mockResolvedValue(original);
  render(<CoveringsTable tab="heat_shrink" canEdit />);
  fireEvent.click(await screen.findByText(DATA.rows[0].name));
  fireEvent.click(screen.getByRole("button", { name: "Править запись" }));
  expect(screen.getByLabelText("Поле Старое поле")).toHaveValue("сохранено");
  fireEvent.change(screen.getByLabelText("Поле Цвет"), { target: { value: "белый" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить запись" }));
  await waitFor(() => expect(edit).toHaveBeenCalledWith("t1", [{ field: "Цвет", old: "черный", value: "белый" }]));
});

it("shows independent custom columns to viewers and removes one only from its own catalog", async () => {
  mockCoverings();
  const original = { ...rawRecord(DATA.rows[0]), fields: { Наименование: DATA.rows[0].name, Полка: "А-1" } };
  vi.mocked(referencesApi.techOperations).mockResolvedValue({ metadata: null, columns: ["Наименование", "Полка"], custom_columns: ["Полка"], records: [original] });
  const remove = vi.spyOn(referencesApi, "removeColumn").mockResolvedValue({ tab: "heat_shrink", name: "Полка", records: 1 });
  const view = render(<CoveringsTable tab="heat_shrink" canEdit={false} />);
  await screen.findByText("А-1");
  expect(screen.queryByRole("button", { name: "+ Столбец" })).toBeNull();
  view.rerender(<CoveringsTable tab="heat_shrink" canEdit />);
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  fireEvent.click(screen.getByRole("button", { name: "Удалить столбец: Полка" }));
  expect(screen.getByText(/Значения останутся в карточках/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Удалить столбец" }));
  await waitFor(() => expect(remove).toHaveBeenCalledWith("heat_shrink", "Полка"));
});

it("removes a computed column only from the active list while preserving its value in the card", async () => {
  const tube = row({ ...DATA.rows[0], wall_mm: 0.5 });
  mockCoverings({ ...DATA, rows: [tube] });
  const original = rawRecord(tube);
  let removed: string[] = [];
  vi.mocked(referencesApi.techOperations).mockImplementation(async () => ({ metadata: null, columns: ["Наименование"], removed_columns: removed, records: [original] }));
  const remove = vi.spyOn(referencesApi, "removeColumn").mockImplementation(async (tab, name) => {
    removed = [name];
    return { tab, name, records: 1 };
  });
  render(<CoveringsTable tab="heat_shrink" canEdit />);
  await screen.findByText(tube.name);
  expect(screen.getByRole("columnheader", { name: "Стенка, мм" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  fireEvent.click(screen.getByRole("button", { name: "Удалить столбец: Стенка, мм" }));
  fireEvent.click(screen.getByRole("button", { name: "Удалить столбец" }));
  await waitFor(() => expect(remove).toHaveBeenCalledWith("heat_shrink", "Стенка, мм"));
  await waitFor(() => expect(screen.queryByRole("columnheader", { name: "Стенка, мм" })).toBeNull());
  fireEvent.click(screen.getByText(tube.name));
  fireEvent.click(screen.getByRole("button", { name: "Править запись" }));
  const card = screen.getByRole("dialog", { name: "Правка записи" });
  expect(within(card).getByText("Стенка, мм")).toBeInTheDocument();
  expect(within(card).getByText("0,5")).toBeInTheDocument();
  expect(within(card).queryByLabelText("Поле Стенка, мм")).toBeNull();
});

it("requires confirmation before deleting a material record", async () => {
  mockCoverings();
  const remove = vi.spyOn(referencesApi, "deleteRecord").mockResolvedValue({ id: "g1" });
  render(<CoveringsTable canEdit />);
  fireEvent.click(await screen.findByText("Трубка гофрированная 8,5мм"));
  fireEvent.click(screen.getByRole("button", { name: "Править запись" }));
  fireEvent.click(screen.getByRole("button", { name: "Удалить запись" }));
  expect(remove).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Подтвердить удаление" }));
  await waitFor(() => expect(remove).toHaveBeenCalledWith("g1"));
});

it("applies migrated raw shared-view keys and saves only the active catalog's view", async () => {
  mockCoverings();
  vi.mocked(referencesApi.techOperations).mockImplementation(async (tab) => ({ metadata: null,
    columns: ["Наименование", "Производитель", "Цвет", "Полка"], custom_columns: ["Полка"],
    records: DATA.rows.filter((item) => tabOf(item) === tab).map((item) => ({ ...rawRecord(item), fields: { Наименование: item.name, Полка: "А-1" } })),
  }));
  vi.spyOn(referencesApi, "columnViews").mockResolvedValue({ can_edit: true, views: {
    heat_shrink: [{ key: "Наименование", visible: true }, { key: "Цвет", visible: false }, { key: "Полка", visible: true }, { key: "Производитель", visible: true }],
    cov: [{ key: "Наименование", visible: true }, { key: "Производитель", visible: false }, { key: "Цвет", visible: true }],
  } });
  const save = vi.spyOn(referencesApi, "saveColumnView").mockImplementation(async (view, columns) => ({ view, columns }));
  const view = render(<ColumnViewsProvider><CoveringsTable key="heat_shrink" tab="heat_shrink" canEdit /></ColumnViewsProvider>);
  const heads = (name: string) => within(section(name) as HTMLElement).getAllByRole("columnheader").map((head) => head.textContent);
  await screen.findByText("ТУТнг 4/2 черная");
  await waitFor(() => expect(heads(titles.tut)).toEqual(["Наименование", "Полка", "Производитель"]));
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  fireEvent.click(screen.getByRole("button", { name: "Сохранить для всех" }));
  await waitFor(() => expect(save).toHaveBeenCalledWith("heat_shrink", expect.arrayContaining([{ key: "color", visible: false }, { key: "field:Полка", visible: true }])));
  view.rerender(<ColumnViewsProvider><CoveringsTable key="cov" tab="cov" canEdit /></ColumnViewsProvider>);
  await screen.findByText("Оплётка GTRPA-10");
  await waitFor(() => expect(heads(titles.braid)).toContain("Цвет"));
  expect(heads(titles.braid)).not.toContain("Производитель");
  expect(save).toHaveBeenCalledTimes(1);
});

it("reports schema failure instead of silently disabling editing", async () => {
  mockCoverings();
  vi.mocked(referencesApi.techOperations).mockRejectedValue(new Error("Нет связи с ERP"));
  render(<CoveringsTable tab="heat_shrink" canEdit />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи с ERP");
  expect(screen.queryByRole("button", { name: "+ Запись" })).toBeNull();
});

const shrink: ShrinkTemperature = {
  lower_c: 85, upper_c: 125, kind: "оптимальная", characteristic_c: 110,
  source_url: "https://example.org/passport.pdf", document: "Паспорт ТУТ 2:1", page: "1–2",
  quote: "Температура усадки: +110; оптимальная: 85–125 °C",
};

it("formats single, open and unknown recovery temperatures without using the operating range", () => {
  expect(shrinkTemperatureText(shrink)).toBe("85…125");
  expect(shrinkTemperatureText({ ...shrink, lower_c: 105, upper_c: null, kind: "минимальная" })).toBe("≥105");
  expect(shrinkTemperatureText({ ...shrink, lower_c: 105, upper_c: null, kind: "" })).toBe("105…");
  expect(shrinkTemperatureText({ ...shrink, lower_c: null, upper_c: 120 })).toBe("≤120");
  expect(shrinkTemperatureText({ ...shrink, lower_c: 120, upper_c: 120 })).toBe("120");
  expect(shrinkTemperatureText({ ...shrink, lower_c: 90.5 })).toBe("90,5…125");
  expect(shrinkTemperatureText({ ...shrink, lower_c: null, upper_c: null })).toBe("");
  expect(shrinkTemperatureText()).toBe("");
});

it("shows one recovery column and lets viewers read both statements and provenance", async () => {
  const tube = { ...DATA.rows[0], shrink_temperature: shrink };
  mockCoverings({ ...DATA, rows: [tube] });
  render(<CoveringsTable tab="heat_shrink" canEdit={false} />);
  expect(await screen.findByRole("columnheader", { name: "Температура усадки, °C" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Рабочая температура, °C" })).toBeInTheDocument();
  expect(screen.getByText("85…125")).toHaveAttribute("title", expect.stringContaining("По характеристикам: 110 °C"));
  expect(screen.getByText("−55 … +125")).toBeInTheDocument();
  fireEvent.click(screen.getByText(tube.name));
  expect(screen.getByText(shrink.quote)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Открыть источник температуры усадки" })).toHaveAttribute("href", shrink.source_url);
  expect(screen.queryByRole("button", { name: "Править запись" })).toBeNull();
});

it("edits recovery fields absent from a legacy payload with empty old-value guards", async () => {
  mockCoverings({ ...DATA, rows: [DATA.rows[0]] });
  const original = rawRecord(DATA.rows[0]);
  vi.mocked(referencesApi.techOperations).mockResolvedValue({ metadata: null, columns: ["Наименование"], records: [original] });
  const edit = vi.spyOn(referencesApi, "editRecord").mockResolvedValue(original);
  render(<CoveringsTable tab="heat_shrink" canEdit />);
  fireEvent.click(await screen.findByText(original.display_text));
  fireEvent.click(screen.getByRole("button", { name: "Править запись" }));
  fireEvent.change(screen.getByLabelText("Температура усадки от, °C"), { target: { value: "105" } });
  fireEvent.change(screen.getByLabelText("Вид температуры усадки"), { target: { value: "минимальная" } });
  fireEvent.change(screen.getByLabelText("Источник температуры усадки"), { target: { value: shrink.source_url } });
  fireEvent.change(screen.getByLabelText("Цитата температуры усадки"), { target: { value: "Минимальная: 105 °C" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить запись" }));
  await waitFor(() => expect(edit).toHaveBeenCalledWith(original.id, [
    { field: "Температура усадки от, °C", old: "", value: "105" },
    { field: "Вид температуры усадки", old: "", value: "минимальная" },
    { field: "Источник температуры усадки", old: "", value: shrink.source_url },
    { field: "Цитата температуры усадки", old: "", value: "Минимальная: 105 °C" },
  ]));
});

it("keeps the new temperature and evidence out of the coverings table", async () => {
  mockCoverings();
  render(<CoveringsTable canEdit />);
  await screen.findByText("Оплётка GTRPA-10");
  expect(screen.queryByRole("columnheader", { name: "Температура усадки, °C" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "+ Запись" }));
  expect(screen.queryByLabelText("Температура усадки от, °C")).toBeNull();
});

it.each<{ raw: Record<string, string>; text: string }>([
  { raw: { "Температура усадки от, °C": "около 105", "Вид температуры усадки": "примерная" }, text: "около 105…" },
  { raw: { "Источник температуры усадки": "неизвестен" }, text: "Проверить" },
])("shows invalid stored recovery data unchanged and allows unrelated edits: $text", async ({ raw, text }) => {
  const legacy = { ...shrink, lower_c: null, upper_c: null, needs_review: true,
    validation_error: "Уточните температуру усадки", raw_fields: raw };
  const tube = { ...DATA.rows[0], shrink_temperature: legacy };
  mockCoverings({ ...DATA, rows: [tube] });
  const original = { ...rawRecord(tube), fields: { ...rawRecord(tube).fields, ...legacy.raw_fields, Цвет: "чёрный" } };
  vi.mocked(referencesApi.techOperations).mockResolvedValue({ metadata: null, records: [original] });
  const edit = vi.spyOn(referencesApi, "editRecord").mockResolvedValue(original);
  render(<CoveringsTable tab="heat_shrink" canEdit />);
  expect(await screen.findByText(text)).toBeInTheDocument();
  expect(screen.getByText("Проверить")).toBeInTheDocument();
  fireEvent.click(screen.getByText(tube.name));
  for (const value of Object.values(raw)) expect(screen.getByText(value)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Править запись" }));
  for (const [field, value] of Object.entries(raw)) expect(screen.getByLabelText(field)).toHaveValue(value);
  fireEvent.change(screen.getByLabelText("Поле Цвет"), { target: { value: "белый" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить запись" }));
  await waitFor(() => expect(edit).toHaveBeenCalledWith(tube.id, [{ field: "Цвет", old: "чёрный", value: "белый" }]));
});

it.each(["Температура, °C", "Рабочая температура, °C"])("restores an operating column hidden with %s", async (marker) => {
  mockCoverings({ ...DATA, rows: [DATA.rows[0]] });
  let removed = [marker];
  vi.mocked(referencesApi.techOperations).mockImplementation(async () => ({ metadata: null,
    removed_columns: removed, records: [rawRecord(DATA.rows[0])] }));
  const add = vi.spyOn(referencesApi, "addColumn").mockImplementation(async (tab, name) => {
    removed = [];
    return { tab, name, records: 0 };
  });
  const remove = vi.spyOn(referencesApi, "removeColumn").mockImplementation(async (tab, name) => {
    removed = [name];
    return { tab, name, records: 0 };
  });
  render(<CoveringsTable tab="heat_shrink" canEdit />);
  await screen.findByText(DATA.rows[0].name);
  expect(screen.queryByRole("columnheader", { name: "Рабочая температура, °C" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "+ Столбец" }));
  fireEvent.change(screen.getByLabelText("Название столбца"), { target: { value: "Рабочая температура, °C" } });
  fireEvent.click(screen.getByRole("button", { name: "Добавить столбец" }));
  await waitFor(() => expect(add).toHaveBeenCalledWith("heat_shrink", "Рабочая температура, °C"));
  expect(await screen.findByRole("columnheader", { name: "Рабочая температура, °C" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  fireEvent.click(screen.getByRole("button", { name: "Удалить столбец: Рабочая температура, °C" }));
  fireEvent.click(screen.getByRole("button", { name: "Удалить столбец" }));
  await waitFor(() => expect(remove).toHaveBeenCalledWith("heat_shrink", "Рабочая температура, °C"));
  await waitFor(() => expect(screen.queryByRole("columnheader", { name: "Рабочая температура, °C" })).toBeNull());
});
