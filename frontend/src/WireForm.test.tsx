import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { GaugeTable, HookupWireTable } from "./CableStructure";
import { canonicalColor, normalizeColors, parseColors } from "./wireColors";
import { joinMakers, makerSuggestions, parseMakers } from "./wireMakers";
import { referencesApi } from "./api";

/* «Монтажные провода», the edit form (owner 12.10.2026): short sections, «Стандарт» (one) and «Производители» (a list of
   chips), «Цвета» as swatches from the shared palette. */

afterEach(() => vi.restoreAllMocks());

it.each([
  ["желто-зеленый", "жёлто-зелёный"], ["Жёлто-зелёный", "жёлто-зелёный"], ["ж/з", "жёлто-зелёный"], ["з-ж", "зелёно-жёлтый"],
  ["чёрн.", "чёрный"], ["черный", "чёрный"], ["кр", "красный"], ["красно-черный", "красно-чёрный"], ["бело-голубой", "бело-голубой"],
  ["Бесцветный", "натуральный"], ["синий", "синий"],
])("alias %s is the palette name %s", (typed, canonical) => {
  expect(canonicalColor(typed)).toBe(canonical);
});

it("keeps a colour outside the palette as typed and normalises a whole cell", () => {
  expect(canonicalColor("светло-синий")).toBeNull();
  expect(canonicalColor("к")).toBeNull(); // one letter alone is not guessed
  expect(parseColors("белый; желто-зеленый, КР, белый, светло-синий")).toEqual(["белый", "жёлто-зелёный", "красный", "светло-синий"]);
  expect(normalizeColors("черный,красный")).toBe("чёрный, красный");
  expect(normalizeColors("")).toBe("");
});

it("reads and writes the makers list", () => {
  expect(parseMakers("LAPP, Helukabel; Паритет (Подольск, РФ), lapp")).toEqual(["LAPP", "Helukabel", "Паритет (Подольск, РФ)"]);
  expect(joinMakers(["Cabeus", " ParLan ", "cabeus"])).toBe("Cabeus, ParLan");
  expect(makerSuggestions(["Cabeus, ParLan", "Cabeus", ""])).toEqual(["Cabeus", "ParLan"]);
});

const gauge = { id: "g1", awg: "", section_mm2: 0.5, conductor_d_mm: null, strands: "", label: "0,5 мм²", wires: 1 };
const FIELDS = {
  "Марка": "ПВАМ 0,50", "Тип провода": "силовой", "Число жил": "1", "Стандарт": "ТУ 16-505", "Производители": "Cabeus, ParLan",
  "Цвета": "белый", "Жила: проволок × диаметр, мм": "7×0,16", "Экран": "", "Поставщик": "", "Ссылка на datasheet": "", "Источник данных": "",
};
const sibling = { ...FIELDS, "Марка": "ПВАМ 0,75", "Цвета": "чёрный, желто-зеленый" };
const wireRow = (mark: string, fields: Record<string, string>) => ({
  id: mark, kind: "wire" as const, auto_kind: "wire" as const, reason: "", manual: false, cable: mark, dia: null, dia_hint: null,
  record: { id: mark, tab: "prov" as const, display_text: mark, source_sheet: "БД.ПРОВ", fields },
  hookup: { series: "ПВАМ", gauge_id: "g1", insulation: "", outer_d_mm: 2, voltage_v: 48, temp_min_c: -50, temp_max_c: 65 },
});
const data = {
  columns: [...Object.keys(FIELDS), "Материал жилы", "Материал оболочки"], counts: { coax: 0, wire: 2, multi: 0, other: 0 }, gauges: [gauge],
  rows: [wireRow("ПВАМ 0,50", FIELDS), wireRow("ПВАМ 0,75", sibling)],
} as unknown as Parameters<typeof HookupWireTable>[0]["data"];

function open() {
  const saved = vi.spyOn(referencesApi, "editRecord").mockResolvedValue(data.rows[0].record as never);
  vi.spyOn(referencesApi, "saveHookup").mockResolvedValue({} as never);
  render(<HookupWireTable data={data} canEdit onSaved={() => undefined} onError={() => undefined} kindSelect={() => null} />);
  fireEvent.click(screen.getByText("ПВАМ 0,50"));
  return saved;
}

it("shows the sections, the standard as one field and the makers as chips", () => {
  open();
  for (const title of ["Основное", "Материалы и режим", "Доп. параметры", "Закупка и источник"]) expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
  expect(screen.getByLabelText("Стандарт (ГОСТ / ТУ / UL)")).toHaveValue("ТУ 16-505");
  const makers = screen.getByRole("group", { name: "Производители" });
  expect(within(makers).getByText("Cabeus")).toBeInTheDocument();
  expect(within(makers).getByText("ParLan")).toBeInTheDocument();
  expect(screen.queryByLabelText("Стандарт / производитель")).toBeNull();
});

it("asks the core and jacket materials instead of the rudimentary «Изоляция» (owner 15.10)", async () => {
  const edit = open();
  expect(screen.queryByLabelText("Изоляция")).toBeNull();
  expect(screen.getByLabelText("Материал жилы")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("Материал оболочки"), { target: { value: "ПВХ" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(edit).toHaveBeenCalledWith("ПВАМ 0,50", [{ field: "Материал оболочки", old: "", value: "ПВХ" }]));
});

it("adds and removes makers and saves them as a comma-joined cell", async () => {
  const edit = open();
  const makers = screen.getByRole("group", { name: "Производители" });
  fireEvent.click(within(makers).getByRole("button", { name: "Убрать Cabeus" }));
  const input = within(makers).getByLabelText("Производители: добавить");
  fireEvent.change(input, { target: { value: "Helukabel" } });
  fireEvent.keyDown(input, { key: "Enter" });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(edit).toHaveBeenCalledWith("ПВАМ 0,50", [{ field: "Производители", old: "Cabeus, ParLan", value: "ParLan, Helukabel" }]));
});

it("picks colours by swatch, copies the colours of the series and takes another one", async () => {
  const edit = open();
  const colors = screen.getByRole("group", { name: "Цвета" });
  expect(screen.getByRole("button", { name: "белый" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(within(colors).getByRole("button", { name: "красный" }));
  fireEvent.click(within(colors).getByRole("button", { name: "белый" }));
  fireEvent.click(within(colors).getByRole("button", { name: "+ другой" }));
  const other = within(colors).getByLabelText("Другой цвет");
  fireEvent.change(other, { target: { value: "ж/з" } });
  fireEvent.keyDown(other, { key: "Enter" });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(edit).toHaveBeenCalledWith("ПВАМ 0,50", [{ field: "Цвета", old: "белый", value: "красный, жёлто-зелёный" }]));
});

it("«Все цвета серии» copies the colours the other sizes have", () => {
  open();
  fireEvent.click(screen.getByRole("button", { name: "Все цвета серии" }));
  const chips = within(screen.getByRole("group", { name: "Цвета" }));
  expect(chips.getByRole("button", { name: "Убрать чёрный" })).toBeInTheDocument();
  expect(chips.getByRole("button", { name: "Убрать жёлто-зелёный" })).toBeInTheDocument();
});

it("shows the stranding of the wire in its own column and form field, not in the gauge table", () => {
  render(<GaugeTable gauges={[{ ...gauge, strands: "7×0,16" }]} canEdit onSaved={() => undefined} onError={() => undefined} />);
  expect(screen.queryByText("Скрутка, проволок × Ø")).toBeNull();
  expect(screen.queryByText("7×0,16")).toBeNull();
});

it("puts «Структура жилы» under «Основное» of the wire form and as a table column", () => {
  open();
  expect(screen.getAllByText("7×0,16").length).toBeGreaterThan(0);
  const main = screen.getByRole("heading", { name: "Основное" }).closest("section") as HTMLElement;
  expect(within(main).getByLabelText("Структура жилы: проволок × Ø, мм")).toHaveValue("7×0,16");
});
