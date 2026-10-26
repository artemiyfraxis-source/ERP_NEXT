import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CoveringsTable, rangeText, tempText } from "./Coverings";
import { referencesApi, type CoveringRow, type CoveringsResult } from "./api";
import { blockLines, blockTables } from "./test/blocks";

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

afterEach(() => vi.restoreAllMocks());
// The table of a block (a kind, or a sub-kind of ТУТ / ТТК) by its title.
const section = (name: string) => blockTables().find((table) => table.getAttribute("aria-label") === name) ?? null;

it("formats ranges and temperatures the way the shop reads them", () => {
  expect(rangeText(6, 12)).toBe("6–12");
  expect(rangeText(6.4, null)).toBe("");
  expect(tempText(-55, 125)).toBe("−55 … +125");
  expect(tempText(null, null)).toBe("");
});

it("shows every kind in the owner's order with its own columns; «Прочее» only when a row is there", async () => {
  vi.spyOn(referencesApi, "coverings").mockResolvedValue(DATA);
  render(<CoveringsTable canEdit={false} />);
  expect(await screen.findByText("ТУТнг 4/2 черная")).toBeInTheDocument();
  expect(blockLines()).toEqual(["ТУТ — термоусадочная трубка · 2", "ТТК — термоусадочная трубка с клеем · 1", "Кембрик — трубка ПВХ · 1",
    "Гофра · 1", "Оплётки · 1", "Ленты · 1"]);
  // one toolbar for the whole list (25.10): one search, one «Колонки», one «Excel»
  expect(screen.getAllByRole("button", { name: "Колонки" })).toHaveLength(1);
  expect(screen.getAllByRole("button", { name: "Excel" })).toHaveLength(1);
  const heads = (group: string) => within(section(group) as HTMLElement).getAllByRole("columnheader").map((head) => head.textContent);
  expect(heads("ТУТ — термоусадочная трубка")).toEqual(expect.arrayContaining(["Ø до усадки, мм", "Ø после, мм", "Усадка"]));
  expect(heads("ТТК — термоусадочная трубка с клеем")).toContain("Клей");
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
  vi.spyOn(referencesApi, "coverings").mockResolvedValue({
    ...DATA, rows: [
      row({ id: "a", name: "ТУТ 1,5/0,75 черная", series: "20-1506", before_mm: 1.5 }),
      row({ id: "b", name: "ТУТнг 2/1 черная", series: "ТУТнг-LS", before_mm: 2 }),
      row({ id: "c", name: "ТУТ(HF) 10/5 белая", series: "ТУТ(HF)", before_mm: 10 }),
    ],
  });
  render(<CoveringsTable canEdit={false} />);
  await screen.findByText("ТУТнг 2/1 черная");
  const names = [...(section("ТУТ — термоусадочная трубка") as HTMLElement).querySelectorAll("tbody tr")].map((item) => item.querySelector("strong")?.textContent);
  expect(names).toEqual(["ТУТ 1,5/0,75 черная", "ТУТнг 2/1 черная", "ТУТ(HF) 10/5 белая"]);
});

it("searches across the groups", async () => {
  vi.spyOn(referencesApi, "coverings").mockResolvedValue(DATA);
  render(<CoveringsTable canEdit={false} />);
  await screen.findByText("ТУТнг 4/2 черная");
  fireEvent.change(screen.getByRole("searchbox", { name: "Поиск по оболочкам" }), { target: { value: "gtrpa" } });
  expect(screen.getByText("Оплётка GTRPA-10")).toBeInTheDocument();
  expect(screen.queryByText("ТУТнг 4/2 черная")).toBeNull();
  expect(blockLines()).toEqual(["Оплётки · 1"]); // the empty blocks are not shown
});

it("moves a row to another group by hand and back to «Авто»", async () => {
  const load = vi.spyOn(referencesApi, "coverings").mockResolvedValue(DATA);
  const save = vi.spyOn(referencesApi, "setCoveringGroup").mockResolvedValue({} as never);
  render(<CoveringsTable canEdit />);
  fireEvent.click(await screen.findByText("Гофра 8,5мм".replace("Гофра", "Трубка гофрированная")));
  const select = await screen.findByRole("combobox", { name: "Раздел оболочек" });
  expect(select).toHaveValue("");
  fireEvent.change(select, { target: { value: "Прочее" } });
  await waitFor(() => expect(save).toHaveBeenCalledWith("g1", "Прочее"));
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
});

it("edits a БД.МАТ column right in the cell with the record's old value; a parsed-only value is not a column to edit", async () => {
  const load = vi.spyOn(referencesApi, "coverings").mockResolvedValue(DATA);
  vi.spyOn(referencesApi, "techOperations").mockResolvedValue({ metadata: null, records: [
    { id: "t1", tab: "mat", display_text: "ТУТнг 4/2 черная", source_sheet: "БД.МАТ", fields: { Наименование: "ТУТнг 4/2 черная", Цвет: "черный", "Размер до, мм": "4" } },
    { id: "t2", tab: "mat", display_text: "ТУТнг 12/6 черная", source_sheet: "БД.МАТ", fields: { Наименование: "ТУТнг 12/6 черная" } },
  ] });
  const edit = vi.spyOn(referencesApi, "editRecord").mockResolvedValue({ id: "t1", tab: "mat", display_text: "", source_sheet: "", fields: {} });
  render(<CoveringsTable canEdit />);
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
  vi.spyOn(referencesApi, "coverings").mockResolvedValue({ ...DATA, rows: [...DATA.rows, row({ id: "t3", name: "ТУТ без размера" })] });
  render(<CoveringsTable canEdit={false} />);
  await screen.findByText("ТУТ без размера");
  fireEvent.click(screen.getByRole("button", { name: /^Без размера · 1/ }));
  expect(screen.queryByText("ТУТнг 4/2 черная")).toBeNull();
  expect(within(section("ТУТ — термоусадочная трубка") as HTMLElement).getByText("ТУТ без размера")).toBeInTheDocument();
  expect(blockLines()).toEqual(["ТУТ — термоусадочная трубка · 1"]);
  fireEvent.click(screen.getByRole("button", { name: /^Без производителя/ }));
  expect(within(section("Оплётки") as HTMLElement).getByText("Оплётка GTRPA-10")).toBeInTheDocument();
});

it("splits ТУТ into a table per kind, in the server's order, and search hides the empty ones", async () => {
  const rows = [
    row({ id: "p1", name: "ТУТ 10/5 черная", kind: "plain", before_mm: 10, after_mm: 5 }),
    row({ id: "n1", name: "ТУТнг 4/2 черная", kind: "ng", before_mm: 4, after_mm: 2 }),
    row({ id: "r1", name: "ТУТ(3:1)нг-LS 6/2 черная", kind: "r3", before_mm: 6, after_mm: 2, ratio: "3:1" }),
  ];
  vi.spyOn(referencesApi, "coverings").mockResolvedValue({
    ...DATA, counts: { ...DATA.counts, tut: 3, ttk: 0 }, rows,
    kinds: { tut: [{ id: "plain", title: "ТУТ — обычная (2:1)" }, { id: "ng", title: "ТУТнг — не распространяющая горение" }, { id: "r3", title: "ТУТ 3:1 и более" }] },
  });
  render(<CoveringsTable canEdit={false} />);
  await screen.findByText("ТУТ 10/5 черная");
  // ТУТ is a section heading with all its rows; each kind — a block under it
  expect(screen.getByRole("heading", { level: 2, name: "ТУТ — термоусадочная трубка 3" })).toBeInTheDocument();
  expect([...document.querySelectorAll(".dt-sub-title")].map((item) => item.textContent)).toEqual([
    "ТУТ — обычная (2:1) 1", "ТУТнг — не распространяющая горение 1", "ТУТ 3:1 и более 1"]);
  expect(within(section("ТУТнг — не распространяющая горение") as HTMLElement).getByText("ТУТнг 4/2 черная")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Поиск по оболочкам"), { target: { value: "3:1" } });
  expect(document.querySelectorAll(".dt-sub-title")).toHaveLength(1);
  expect(screen.getByRole("heading", { level: 2, name: "ТУТ — термоусадочная трубка 1" })).toBeInTheDocument();
});
