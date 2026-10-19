import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CoaxConnectors, coaxAngle, coaxGender, coaxSeries, coaxSupplier, coaxTypeGroup } from "./CoaxConnectors";
import type { TechOperationRecord } from "./api";

const record = (id: string, fields: Record<string, string>): TechOperationRecord => ({
  id, tab: "coax", display_text: id, source_sheet: "БД.КОАКС", fields,
});

// Mirror rows (sheet columns) and rows of the gsm-repiteri.ru load (schema keys + card columns).
const RECORDS = [
  record("n1", { coaxArticle: "N-111L", coaxType: "N-male", coaxWire: "RG-316", Артикул: "N-111L", "Тип/Серия": "N-male",
    Провод: "RG-316", L1: "4", L2: "2", L3: "6", "Тип пина": "пайка", "Тип экрана": "обжим", Серия: "N", Пол: "штекер",
    Наименование: "Разъём N-111L (N-male, обжимной, на кабель RG-174, RG-316)", Ссылка: "https://gsm-repiteri.ru/n-111l",
    "Волновое сопротивление, Ом": "50", "HEX обжима, мм": "3,25", "Цена, ₽": "420", "Дата цены": "17.10.2026" }),
  record("n2", { coaxArticle: "N-111L", coaxType: "N-male", coaxWire: "RG-174", Серия: "N", Пол: "штекер",
    "Источник данных": "gsm-repiteri.ru, 12.10.2026" }),
  record("s1", { Артикул: "SMA-C174P", "Тип/Серия": "RP-SMA-male R/A", Провод: "RG-174" }),
  record("x1", { Артикул: "Без типа", Провод: "RG-58" }),
];

afterEach(() => { window.localStorage.clear(); });

it("reads the series, gender and angle from the card columns or from the type", () => {
  expect(coaxSeries({ "Тип/Серия": "RP-SMA-male R/A" })).toBe("SMA");
  expect(coaxSeries({ coaxType: "N-female flange" })).toBe("N");
  expect(coaxSeries({ coaxType: "Fakra-female" })).toBe("Fakra");
  expect(coaxSeries({ coaxType: "U.FL-female (IPX-female)" })).toBe("U.FL");
  expect(coaxSeries({ coaxType: "DIN-male", Серия: "7/16 DIN" })).toBe("7/16 DIN");
  expect(coaxSeries({ coaxType: "СР-50-male" })).toBe("СР-50");
  expect(coaxSeries({})).toBe("Без серии");
  expect(coaxGender({ coaxType: "SMA-female" })).toBe("гнездо");
  expect(coaxGender({ coaxType: "MHF1-plug" })).toBe("штекер");
  expect(coaxGender({ Пол: "гнездо", coaxType: "N-male" })).toBe("гнездо");
  expect(coaxAngle({ coaxType: "MCX-male R/A" })).toBe("угловой");
  expect(coaxAngle({ coaxType: "N-male" })).toBe("прямой");
});

it("groups the connectors by type in the shop's series order with the stripping lengths, HEX right after L3 and no D1–D3", () => {
  render(<CoaxConnectors records={RECORDS} canEdit onEdit={() => undefined} />);
  const groups = screen.getAllByRole("row").filter((row) => row.classList.contains("group-row")).map((row) => row.textContent);
  expect(groups).toEqual(["RP-SMA-male · 1", "N-male · 2", "Без серии · 1"]);
  const n316 = screen.getAllByRole("row").find((row) => row.textContent?.includes("RG-316"))!;
  // Разделка — тремя колонками L1 · L2 · L3 (правятся по одной), HEX — следующей; D1–D3 — у кабеля, здесь их нет.
  const heads = screen.getAllByRole("columnheader").map((head) => head.textContent);
  const cells = within(n316).getAllByRole("cell").map((cell) => cell.textContent);
  expect(["L1", "L2", "L3"].map((name) => cells[heads.indexOf(name)])).toEqual(["4", "2", "6"]);
  expect(heads[heads.indexOf("L3") + 1]).toBe("HEX, мм");
  expect(cells[heads.indexOf("HEX, мм")]).toBe("3,25");
  expect(heads.some((head) => /D1/.test(head ?? ""))).toBe(false);
  expect(within(n316).getByRole("link", { name: "gsm-repiteri.ru" })).toHaveAttribute("href", "https://gsm-repiteri.ru/n-111l");
  expect(cells[heads.indexOf("Цена, ₽")]).toBe("420");
  fireEvent.click(screen.getByRole("button", { name: /Угловые/ }));
  expect(screen.getByText("SMA-C174P")).toBeInTheDocument();
  expect(screen.queryByText("N-111L")).toBeNull();
});

it("opens the connector's card on a row click: what the technologist still has to fill, the link, the other cables", () => {
  const onEdit = vi.fn();
  render(<CoaxConnectors records={RECORDS} canEdit onEdit={onEdit} />);
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getAllByRole("row").find((row) => row.textContent?.includes("RG-174") && row.textContent.includes("N-111L"))!);
  let card = screen.getByRole("dialog", { name: "Карточка разъёма N-111L" });
  expect(within(card).getByText("RG-174")).toBeInTheDocument();
  expect(within(card).getAllByText("не задано")).toHaveLength(3); // разделка, пин, экран
  expect(within(card).getByText("gsm-repiteri.ru, 12.10.2026")).toBeInTheDocument();

  fireEvent.click(within(card).getByRole("button", { name: "RG-316" }));
  card = screen.getByRole("dialog", { name: "Карточка разъёма N-111L" });
  expect(within(card).getByText("Разъём N-111L (N-male, обжимной, на кабель RG-174, RG-316)")).toBeInTheDocument();
  expect(within(card).getByText("420 ₽ на 17.10.2026")).toBeInTheDocument();
  expect(within(card).getByText("50 Ом")).toBeInTheDocument();
  expect(within(card).queryByText("не задано")).toBeNull();
  expect(within(card).getByRole("link", { name: "Страница на сайте поставщика" })).toHaveAttribute("href", "https://gsm-repiteri.ru/n-111l");

  fireEvent.click(within(card).getByRole("button", { name: "Править запись" }));
  expect(onEdit).toHaveBeenCalledWith(RECORDS[0]);
  expect(screen.queryByRole("dialog")).toBeNull(); // правка открывается своим окном, карточка уходит
});

it("closes the card with the close button and with Esc", () => {
  render(<CoaxConnectors records={RECORDS} canEdit onEdit={() => undefined} />);
  fireEvent.click(screen.getByText("SMA-C174P"));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Закрыть карточку" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByText("SMA-C174P"));
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("shows the manufacturer and the supplier columns; the supplier is a field or the domain of the link / source", () => {
  expect(coaxSupplier({ Поставщик: "Чип и Дип" })).toBe("Чип и Дип");
  expect(coaxSupplier({ Ссылка: "https://www.gsm-repiteri.ru/n-111l" })).toBe("gsm-repiteri.ru");
  expect(coaxSupplier({ "Источник данных": "gsm-repiteri.ru, 12.10.2026" })).toBe("gsm-repiteri.ru");
  expect(coaxSupplier({ "Источник данных": "зеркало БД.КОАКС" })).toBe("");
  const records = [...RECORDS, record("m1", { Артикул: "MCX-1", Провод: "RG-316", Производитель: "Amphenol" })];
  render(<CoaxConnectors records={records} canEdit onEdit={() => undefined} />);
  expect(screen.getByRole("columnheader", { name: "Производитель" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Поставщик" })).toBeInTheDocument();
  const mcx = screen.getAllByRole("row").find((row) => row.textContent?.includes("MCX-1"))!;
  expect(within(mcx).getByText("Amphenol")).toBeInTheDocument();
  const n316 = screen.getAllByRole("row").find((row) => row.textContent?.includes("N-111L") && row.textContent.includes("RG-316"))!;
  expect(within(n316).getAllByText("gsm-repiteri.ru")).toHaveLength(2); // поставщик и ссылка на карточку
});

it("shows the card without the edit button to a viewer", () => {
  render(<CoaxConnectors records={RECORDS} canEdit={false} onEdit={() => undefined} />);
  fireEvent.click(screen.getByText("SMA-C174P"));
  const card = screen.getByRole("dialog", { name: "Карточка разъёма SMA-C174P" });
  expect(within(card).getByText("угловой")).toBeInTheDocument();
  expect(within(card).queryByRole("button", { name: "Править запись" })).toBeNull();
});

it("shows the owner's own columns at once and edits their cells with the old value", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(RECORDS[0]), { status: 200, headers: { "Content-Type": "application/json" } }));
  const edited = vi.fn();
  render(<CoaxConnectors records={RECORDS.map((item) => ({ ...item, fields: { ...item.fields, Полка: item.id === "n1" ? "А-1" : "" } }))} canEdit onEdit={() => undefined} ownColumns={["Полка"]} onEdited={edited} />);
  expect(screen.getByRole("columnheader", { name: "Полка" })).toBeInTheDocument();
  const cell = screen.getByText("А-1").closest("td") as HTMLElement;
  fireEvent.click(cell);
  fireEvent.keyDown(cell, { key: "Enter" });
  const input = within(cell).getByRole("textbox");
  fireEvent.change(input, { target: { value: "Б-2" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(edited).toHaveBeenCalled());
  const [path, init] = fetchMock.mock.calls[0];
  expect(path).toBe("/api/references/tech-operations/n1");
  expect(JSON.parse(String(init?.body))).toEqual({ edits: [{ field: "Полка", old: "А-1", value: "Б-2" }] });
  fetchMock.mockRestore();
});

it("edits the plain cells right in the table with the sheet column's old value; the article, cable and supplier stay in the window", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(RECORDS[0]), { status: 200, headers: { "Content-Type": "application/json" } }));
  const edited = vi.fn();
  render(<CoaxConnectors records={RECORDS} canEdit onEdit={() => undefined} onEdited={edited} />);
  const heads = screen.getAllByRole("columnheader").map((head) => head.textContent);
  const n316 = screen.getAllByRole("row").find((row) => row.textContent?.includes("N-111L") && row.textContent.includes("RG-316"))!;
  const cellOf = (name: string) => within(n316).getAllByRole("cell")[heads.indexOf(name)] as HTMLElement;
  expect(cellOf("Артикул")).not.toHaveClass("dt-editable");
  expect(cellOf("Кабель")).not.toHaveClass("dt-editable");
  expect(cellOf("Поставщик")).not.toHaveClass("dt-editable");
  expect(cellOf("Исполнение")).not.toHaveClass("dt-editable");
  const l2 = cellOf("L2");
  expect(l2).toHaveClass("dt-editable");
  fireEvent.click(l2);
  fireEvent.keyDown(l2, { key: "Enter" });
  const input = within(l2).getByRole("textbox");
  fireEvent.change(input, { target: { value: "2,5" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(edited).toHaveBeenCalled());
  const [path, init] = fetchMock.mock.calls[0];
  expect(path).toBe("/api/references/tech-operations/n1");
  expect(JSON.parse(String(init?.body))).toEqual({ edits: [{ field: "L2", old: "2", value: "2,5" }] });
  fetchMock.mockRestore();
});

it("has no editable cells for a viewer and a «Без L1–L3» chip like «Без D1–D3» of the cables", () => {
  render(<CoaxConnectors records={RECORDS} canEdit={false} onEdit={() => undefined} />);
  expect(document.querySelector("td.dt-editable")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /^Без L1–L3/ }));
  expect(screen.queryByText("RG-316")).toBeNull();
  expect(screen.getByText("SMA-C174P")).toBeInTheDocument();
});

it("offers «Удалить» for an own column in «Колонки»", () => {
  const remove = vi.fn();
  render(<CoaxConnectors records={RECORDS} canEdit onEdit={() => undefined} ownColumns={["Полка"]} removeColumn={() => remove} />);
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  expect(screen.getAllByRole("button", { name: /^Удалить столбец/ })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Удалить столбец: Полка" }));
  expect(remove).toHaveBeenCalled();
});

it("orders the type groups as the owner asked (19.10): series, then the plain polarity before RP, female before male; variants stay inside", () => {
  expect(coaxTypeGroup({ coaxType: "SMA-female flange" })).toBe("SMA-female");
  expect(coaxTypeGroup({ coaxType: "RP-SMA-male R/A" })).toBe("RP-SMA-male");
  expect(coaxTypeGroup({ coaxType: "FAKRA-female (тип Z)" })).toBe("Fakra-female");
  expect(coaxTypeGroup({ coaxType: "MHF1-plug (Lock)" })).toBe("MHF1-plug");
  expect(coaxTypeGroup({ coaxType: "QMA" })).toBe("QMA");
  expect(coaxTypeGroup({})).toBe("Без серии");
  const rows = ["N-female", "RP-SMA-female", "SMA-male R/A", "SMA-male", "SMA-female R/A", "RP-SMA-male", "SMA-female"]
    .map((type, index) => record(`r${index}`, { Артикул: `A${index}`, "Тип/Серия": type, Провод: "RG-58" }));
  render(<CoaxConnectors records={rows} canEdit={false} onEdit={() => undefined} />);
  const groups = screen.getAllByRole("row").filter((row) => row.classList.contains("group-row")).map((row) => row.textContent);
  expect(groups).toEqual(["SMA-female · 2", "SMA-male · 2", "RP-SMA-female · 1", "RP-SMA-male · 1", "N-female · 1"]);
  const types = screen.getAllByRole("row").filter((row) => !row.classList.contains("group-row") && row.querySelector("td"))
    .map((row) => within(row).getAllByRole("cell")[1].textContent);
  expect(types.slice(0, 4)).toEqual(["SMA-female", "SMA-female R/A", "SMA-male", "SMA-male R/A"]);
});
