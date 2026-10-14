import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CoveringsTable, rangeText, tempText } from "./Coverings";
import { referencesApi, type CoveringRow, type CoveringsResult } from "./api";

/* «Оболочки» (owner 12.10.2026): a table per kind with its own fields, «Прочее» behind a toggle, a manual move. */

const base: CoveringRow = {
  id: "", name: "", group: "tut", group_source: "по группе", auto_group: "tut", datasheet: "", unit: "м",
  before_mm: null, after_mm: null, ratio: "", wall_mm: null, adhesive: "", nominal_mm: null, min_mm: null, max_mm: null,
  density: "", inner_mm: null, outer_mm: null, split: null, material: "", material_short: "", temp_min_c: null, temp_max_c: null,
  color: "", maker: "", series: "", series_family: "", notes: [],
};
const row = (patch: Partial<CoveringRow>): CoveringRow => ({ ...base, ...patch });
const titles = { tut: "ТУТ — термоусадочная трубка", ttk: "ТТК — термоусадочная трубка с клеем", braid: "Оплётки", corrugated: "Гофра", other: "Прочее" };
const DATA: CoveringsResult = {
  counts: { tut: 2, ttk: 1, braid: 1, corrugated: 1, other: 1 },
  titles,
  rows: [
    row({ id: "t1", name: "ТУТнг 4/2 черная", series: "ТУТнг-LS", before_mm: 4, after_mm: 2, ratio: "2:1", material: "полиолефин", material_short: "полиолефин", temp_min_c: -55, temp_max_c: 125, color: "черный", maker: "КВТ Калуга" }),
    row({ id: "t2", name: "ТУТнг 12/6 черная", series: "ТУТнг-LS", before_mm: 12, after_mm: 6, ratio: "2:1" }),
    row({ id: "k1", name: "ТТК 6/2 черная", group: "ttk", auto_group: "ttk", series: "ТТК(3:1)", before_mm: 6, after_mm: 2, ratio: "3:1", adhesive: "да" }),
    row({ id: "b1", name: "Оплётка GTRPA-10", group: "braid", auto_group: "braid", series: "GTRPA-10", nominal_mm: 10, min_mm: 10, max_mm: 16, material: "полиамид (нейлон)", material_short: "ПА" }),
    row({ id: "g1", name: "Трубка гофрированная 8,5мм", group: "corrugated", auto_group: "corrugated", inner_mm: 8.5, outer_mm: 12, split: true, material: "полипропилен", material_short: "ПП" }),
    row({ id: "o1", name: "Изолента ПВХ 15х0,13х20м", group: "other", auto_group: "other" }),
  ],
};

afterEach(() => vi.restoreAllMocks());
const section = (name: string) => document.querySelector(`section.cov-group[aria-label="${name}"]`);

it("formats ranges and temperatures the way the shop reads them", () => {
  expect(rangeText(6, 12)).toBe("6–12");
  expect(rangeText(6.4, null)).toBe("");
  expect(tempText(-55, 125)).toBe("−55 … +125");
  expect(tempText(null, null)).toBe("");
});

it("shows the four groups with their own columns and keeps «Прочее» behind a toggle", async () => {
  vi.spyOn(referencesApi, "coverings").mockResolvedValue(DATA);
  render(<CoveringsTable canEdit={false} />);
  expect(await screen.findByText("ТУТнг 4/2 черная")).toBeInTheDocument();
  for (const group of ["ТУТ — термоусадочная трубка", "ТТК — термоусадочная трубка с клеем", "Оплётки", "Гофра"]) {
    expect(section(group)).not.toBeNull();
  }
  const heads = (group: string) => within(section(group) as HTMLElement).getAllByRole("columnheader").map((head) => head.textContent);
  expect(heads("ТУТ — термоусадочная трубка")).toEqual(expect.arrayContaining(["Ø до усадки, мм", "Ø после, мм", "Усадка"]));
  expect(heads("ТТК — термоусадочная трубка с клеем")).toContain("Клей");
  expect(heads("Оплётки")).toEqual(expect.arrayContaining(["Ø номинал, мм", "Диапазон Ø, мм"]));
  expect(heads("Гофра")).toEqual(expect.arrayContaining(["Ø внутр., мм", "Ø наружн., мм", "Разрезная"]));
  expect(screen.getByText("10–16")).toBeInTheDocument();
  // «Прочее» is hidden until asked for; nothing is dropped
  expect(screen.queryByText("Изолента ПВХ 15х0,13х20м")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Показать прочее · 1" }));
  expect(await screen.findByText("Изолента ПВХ 15х0,13х20м")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Скрыть прочее · 1" })).toHaveAttribute("aria-expanded", "true");
});

it("searches across the groups", async () => {
  vi.spyOn(referencesApi, "coverings").mockResolvedValue(DATA);
  render(<CoveringsTable canEdit={false} />);
  await screen.findByText("ТУТнг 4/2 черная");
  fireEvent.change(screen.getByRole("searchbox", { name: "Поиск по оболочкам" }), { target: { value: "gtrpa" } });
  expect(screen.getByText("Оплётка GTRPA-10")).toBeInTheDocument();
  expect(screen.queryByText("ТУТнг 4/2 черная")).toBeNull();
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

it("has «Без Ø» and «Без производителя» chips in every group", async () => {
  vi.spyOn(referencesApi, "coverings").mockResolvedValue({ ...DATA, rows: [...DATA.rows, row({ id: "t3", name: "ТУТ без размера" })] });
  render(<CoveringsTable canEdit={false} />);
  await screen.findByText("ТУТ без размера");
  const tut = section("ТУТ — термоусадочная трубка") as HTMLElement;
  fireEvent.click(within(tut).getByRole("button", { name: /^Без Ø · 1/ }));
  expect(within(tut).queryByText("ТУТнг 4/2 черная")).toBeNull();
  expect(within(tut).getByText("ТУТ без размера")).toBeInTheDocument();
  expect(within(section("Оплётки") as HTMLElement).getByRole("button", { name: /^Без производителя · 1/ })).toBeInTheDocument();
  expect(within(section("Оплётки") as HTMLElement).queryByRole("button", { name: /^Без Ø/ })).toBeNull(); // пустой отбор не показывается
});
