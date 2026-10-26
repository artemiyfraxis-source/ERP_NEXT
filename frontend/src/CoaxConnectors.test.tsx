import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { CoaxConnectors, coaxAngle, hasHex, coaxGender, coaxSeries, seriesRank, coaxSupplier, coaxTypeGroup } from "./CoaxConnectors";
import type { TechOperationRecord } from "./api";
import { blockLines, blockRows, blockTables, listHead, listHeads } from "./test/blocks";

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

/** The series open folded (owner 26.10.2026); the tests below look inside them. */
function unfolded(ui: ReactElement) {
  const view = render(ui);
  const all = screen.queryByRole("button", { name: "Развернуть все" });
  if (all) fireEvent.click(all);
  else document.querySelectorAll<HTMLElement>('[aria-expanded="false"]').forEach((heading) => fireEvent.click(heading));
  return view;
}

it("opens with every series folded, like the cables; a heading opens its series (владелец 26.10)", () => {
  render(<CoaxConnectors records={RECORDS} canEdit onEdit={() => undefined} />);
  const n = screen.getByRole("button", { name: /^N-male 2$/ });
  expect(n).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("RG-316")).toBeNull();
  fireEvent.click(n);
  expect(n).toHaveAttribute("aria-expanded", "true");
  expect(screen.getAllByText("RG-316").length).toBeGreaterThan(0);
});

it("reads the series, gender and angle from the card columns or from the type", () => {
  expect(coaxSeries({ "Тип/Серия": "RP-SMA-male R/A" })).toBe("SMA");
  expect(coaxSeries({ coaxType: "N-female flange" })).toBe("N");
  expect(coaxSeries({ coaxType: "Fakra-female" })).toBe("Fakra");
  expect(coaxSeries({ coaxType: "U.FL-female (IPX-female)" })).toBe("U.FL");
  expect(coaxSeries({ coaxType: "IPEX-female" })).toBe("MHF1");
  expect(coaxSeries({ coaxType: "MHF1-plug" })).toBe("MHF1");
  expect(coaxSeries({ coaxType: "W.FL-female" })).toBe("MHF1");
  expect(coaxSeries({ coaxType: "u.fl-female" })).toBe("U.FL");
  expect(coaxSeries({ coaxType: "DIN-male", Серия: "7/16 DIN" })).toBe("7/16 DIN");
  expect(coaxSeries({ coaxType: "СР-50-male" })).toBe("СР-50");
  expect(coaxSeries({ coaxType: "2.92mm-male", Серия: "2.92mm" })).toBe("2.92mm");
  expect(coaxSeries({ coaxType: "2.92mm-male" })).toBe("2.92mm");
  expect(coaxSeries({ coaxType: "3.5 mm-male" })).toBe("3.5mm");
  expect(coaxSeries({ coaxType: "SMP-female R/A" })).toBe("SMP");
  expect(seriesRank("2.92mm")).toBeLessThan(seriesRank("U.FL"));
  expect(coaxSeries({})).toBe("Без серии");
  expect(coaxGender({ coaxType: "SMA-female" })).toBe("гнездо");
  expect(coaxGender({ coaxType: "MHF1-plug" })).toBe("штекер");
  expect(coaxGender({ Пол: "гнездо", coaxType: "N-male" })).toBe("гнездо");
  expect(coaxAngle({ coaxType: "MCX-male R/A" })).toBe("угловой");
  expect(coaxAngle({ coaxType: "N-male" })).toBe("прямой");
});

it("groups the connectors by type in the shop's series order with the stripping lengths, HEX right after L3 and no D1–D3", () => {
  unfolded(<CoaxConnectors records={RECORDS} canEdit onEdit={() => undefined} />);
  const groups = blockLines();
  expect(groups).toEqual(["RP-SMA-male R/A · 1", "N-male · 2", "Без серии · 1"]);
  const n316 = screen.getAllByRole("row").find((row) => row.textContent?.includes("RG-316"))!;
  // Разделка — тремя колонками L1 · L2 · L3 (правятся по одной), HEX — следующей; D1–D3 — у кабеля, здесь их нет.
  const heads = listHeads().map((head) => head.textContent);
  const cells = within(n316).getAllByRole("cell").map((cell) => cell.textContent);
  expect(["L1", "L2", "L3"].map((name) => cells[heads.indexOf(name)])).toEqual(["4", "2", "6"]);
  // owner 23.10: the stripping and crimp group — L1–L3, HEX, depth — then the electrical one: frequency, VSWR, Ω
  expect(heads.slice(heads.indexOf("L3") + 1, heads.indexOf("L3") + 6)).toEqual(["HEX, мм", "Заглубление, мм", "Частота, ГГц", "КСВ", "Ом"]);
  expect(cells[heads.indexOf("HEX, мм")]).toBe("3,25");
  expect(cells[heads.indexOf("Ом")]).toBe("50");
  expect(heads.some((head) => /D1/.test(head ?? ""))).toBe(false);
  expect(within(n316).getByRole("link", { name: "gsm-repiteri.ru" })).toHaveAttribute("href", "https://gsm-repiteri.ru/n-111l");
  expect(cells[heads.indexOf("Цена, ₽")]).toBe("420");
  fireEvent.click(screen.getByRole("button", { name: /Угловые/ }));
  expect(screen.getByText("SMA-C174P")).toBeInTheDocument();
  expect(screen.queryByText("N-111L")).toBeNull();
});

it("shows every column of the table by group — nothing hidden, the owner's own before the supply (владелец 25.10)", () => {
  unfolded(<CoaxConnectors records={RECORDS} canEdit onEdit={() => undefined} ownColumns={["Своё поле"]} />);
  const heads = listHeads().map((head) => head.textContent);
  expect(heads).toEqual([
    "Артикул", "Тип", "Кабель",
    "Тип пина", "Тип экрана", "L1", "L2", "L3", "HEX, мм", "Заглубление, мм",
    "Частота, ГГц", "КСВ", "Ом",
    "Производитель", "Своё поле",
    "Поставщик", "Артикул поставщика", "Карточка на сайте", "Цена, ₽",
    "Наименование",
  ]);
  // «Колонки» offers exactly the table's columns, «Показать все» has nothing to add
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  const group = screen.getByRole("group", { name: "Показывать колонки" });
  const offered = within(group).getAllByRole("checkbox").map((box) => box.parentElement?.textContent?.replace(/Удалить$/, "") ?? "");
  expect(offered).toEqual(heads);
  expect(within(group).getAllByRole("checkbox").every((box) => (box as HTMLInputElement).checked)).toBe(true);
  expect(screen.getByRole("button", { name: "Показать все" })).toBeDisabled();
});

it("has no unchecked column the owner removed 25.10 (пол, исполнение, полярность, монтаж, дата цены, примечание, источник) nor L+ / L−, program, size, body material", () => {
  unfolded(<CoaxConnectors records={RECORDS} canEdit onEdit={() => undefined} ownColumns={["Дата цены", "Примечание"]} />);
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  const offered = within(screen.getByRole("group", { name: "Показывать колонки" })).getAllByRole("checkbox").map((box) => box.parentElement?.textContent ?? "");
  for (const gone of ["Пол", "Исполнение", "Полярность", "Монтаж", "Дата цены", "Примечание", "Источник данных",
    "L+", "L−", "L-", "Программа", "Габариты", "Материал корпуса"]) expect(offered).not.toContain(gone);
  // the price keeps its date in the tooltip
  expect(screen.getByText("420")).toHaveAttribute("title", "Цена на 17.10.2026");
});

it("keeps the quick filters on the fields: «Штекеры» / «Гнёзда» by «Пол» or the type, «Угловые» by «Исполнение» or R/A", () => {
  const rows = [
    record("m", { Артикул: "N-M", "Тип/Серия": "N-male", Провод: "RG-58" }),
    record("f", { Артикул: "N-F", "Тип/Серия": "N", Провод: "RG-58", Пол: "гнездо" }),
    record("ra", { Артикул: "SMA-RA", "Тип/Серия": "SMA-male", Провод: "RG-174", Исполнение: "угловой" }),
  ];
  unfolded(<CoaxConnectors records={rows} canEdit onEdit={() => undefined} />);
  const shown = () => blockRows().map((row) => row.querySelector("strong")?.textContent);
  fireEvent.click(screen.getByRole("button", { name: /^Гнёзда/ }));
  expect(shown()).toEqual(["N-F"]);
  fireEvent.click(screen.getByRole("button", { name: /^Гнёзда/ }));
  fireEvent.click(screen.getByRole("button", { name: /^Штекеры/ }));
  expect([...shown()].sort()).toEqual(["N-M", "SMA-RA"]);
  fireEvent.click(screen.getByRole("button", { name: /^Штекеры/ }));
  fireEvent.click(screen.getByRole("button", { name: /^Угловые/ }));
  expect(shown()).toEqual(["SMA-RA"]);
});

it("shows the contact depth as a short number with its source as a hint; the long source column is hidden — владелец 19.10", () => {
  const source = "radiolab.ru, SMA series connectors, габаритно-присоединительные размеры, розетка (мама): H + I + J";
  const rows = [record("f1", { Артикул: "S-211/5D", "Тип/Серия": "SMA-female", Провод: "5D-FB",
    "Заглубление контакта, мм": "1,88…2,41", "Заглубление контакта, источник": source })];
  unfolded(<CoaxConnectors records={rows} canEdit onEdit={() => undefined}
    ownColumns={["Заглубление контакта, мм", "Заглубление контакта, источник"]} />);
  const heads = listHeads().map((head) => head.textContent);
  expect(heads.filter((head) => head?.startsWith("Заглубление"))).toEqual(["Заглубление, мм"]);
  expect(screen.getByText("1,88…2,41")).toHaveAttribute("title", source);
  expect(screen.queryByText(source)).toBeNull();
});

it("opens the connector's card on a row click: what the technologist still has to fill, the link, the other cables", () => {
  const onEdit = vi.fn();
  unfolded(<CoaxConnectors records={RECORDS} canEdit onEdit={onEdit} />);
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getAllByRole("row").find((row) => row.textContent?.includes("RG-174") && row.textContent.includes("N-111L"))!);
  let card = screen.getByRole("dialog", { name: "Карточка разъёма N-111L" });
  expect(within(card).getByText("RG-174")).toBeInTheDocument();
  expect(within(card).getAllByText("не задано")).toHaveLength(3); // разделка, пин, экран
  expect(within(card).getByText("gsm-repiteri.ru, 12.10.2026")).toBeInTheDocument(); // «Источник данных»: no column, in the card
  expect(within(card).getByText("Пол").nextSibling).toHaveTextContent("штекер");

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
  unfolded(<CoaxConnectors records={RECORDS} canEdit onEdit={() => undefined} />);
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
  unfolded(<CoaxConnectors records={records} canEdit onEdit={() => undefined} />);
  expect(listHead().getByRole("columnheader", { name: "Производитель" })).toBeInTheDocument();
  expect(listHead().getByRole("columnheader", { name: "Поставщик" })).toBeInTheDocument();
  const mcx = screen.getAllByRole("row").find((row) => row.textContent?.includes("MCX-1"))!;
  expect(within(mcx).getByText("Amphenol")).toBeInTheDocument();
  const n316 = screen.getAllByRole("row").find((row) => row.textContent?.includes("N-111L") && row.textContent.includes("RG-316"))!;
  expect(within(n316).getAllByText("gsm-repiteri.ru")).toHaveLength(2); // поставщик и ссылка на карточку
});

it("shows the card without the edit button to a viewer", () => {
  unfolded(<CoaxConnectors records={RECORDS} canEdit={false} onEdit={() => undefined} />);
  fireEvent.click(screen.getByText("SMA-C174P"));
  const card = screen.getByRole("dialog", { name: "Карточка разъёма SMA-C174P" });
  expect(within(card).getByText("угловой")).toBeInTheDocument();
  expect(within(card).queryByRole("button", { name: "Править запись" })).toBeNull();
});

it("shows the owner's own columns at once and edits their cells with the old value", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(RECORDS[0]), { status: 200, headers: { "Content-Type": "application/json" } }));
  const edited = vi.fn();
  unfolded(<CoaxConnectors records={RECORDS.map((item) => ({ ...item, fields: { ...item.fields, Полка: item.id === "n1" ? "А-1" : "" } }))} canEdit onEdit={() => undefined} ownColumns={["Полка"]} onEdited={edited} />);
  expect(listHead().getByRole("columnheader", { name: "Полка" })).toBeInTheDocument();
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
  unfolded(<CoaxConnectors records={RECORDS} canEdit onEdit={() => undefined} onEdited={edited} />);
  const heads = listHeads().map((head) => head.textContent);
  const n316 = screen.getAllByRole("row").find((row) => row.textContent?.includes("N-111L") && row.textContent.includes("RG-316"))!;
  const cellOf = (name: string) => within(n316).getAllByRole("cell")[heads.indexOf(name)] as HTMLElement;
  expect(cellOf("Артикул")).not.toHaveClass("dt-editable");
  expect(cellOf("Кабель")).not.toHaveClass("dt-editable");
  expect(cellOf("Поставщик")).not.toHaveClass("dt-editable");
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
  unfolded(<CoaxConnectors records={RECORDS} canEdit={false} onEdit={() => undefined} />);
  expect(document.querySelector("td.dt-editable")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /^Без L1–L3/ }));
  expect(screen.queryByText("RG-316")).toBeNull();
  expect(screen.getByText("SMA-C174P")).toBeInTheDocument();
});

it("offers «Удалить» for an own column in «Колонки»", () => {
  const remove = vi.fn();
  unfolded(<CoaxConnectors records={RECORDS} canEdit onEdit={() => undefined} ownColumns={["Полка"]} removeColumn={() => remove} />);
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  expect(screen.getAllByRole("button", { name: /^Удалить столбец/ })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Удалить столбец: Полка" }));
  expect(remove).toHaveBeenCalled();
});

it("orders the type groups as the owner asked (19.10): series, then the plain polarity before RP, female before male, bulkhead, flange and R/A as their own groups after the straight ones", () => {
  expect(coaxTypeGroup({ coaxType: "SMA-female flange" })).toBe("SMA-female flange");
  expect(coaxTypeGroup({ coaxType: "SMA-female bulkhead" })).toBe("SMA-female bulkhead");
  expect(coaxTypeGroup({ coaxType: "RP-SMA-male R/A" })).toBe("RP-SMA-male R/A");
  expect(coaxTypeGroup({ coaxType: "SMA-male", Исполнение: "угловой" })).toBe("SMA-male R/A");
  expect(coaxTypeGroup({ coaxType: "FAKRA-female (тип Z)" })).toBe("Fakra-female");
  expect(coaxTypeGroup({ coaxType: "MHF1-plug (Lock)" })).toBe("MHF1-plug");
  expect(coaxTypeGroup({ coaxType: "QMA" })).toBe("QMA");
  expect(coaxTypeGroup({})).toBe("Без серии");
  const rows = ["N-female", "RP-SMA-female", "SMA-male R/A", "SMA-male", "SMA-female R/A", "RP-SMA-male", "SMA-female flange", "SMA-female", "SMA-female bulkhead"]
    .map((type, index) => record(`r${index}`, { Артикул: `A${index}`, "Тип/Серия": type, Провод: "RG-58" }));
  unfolded(<CoaxConnectors records={rows} canEdit={false} onEdit={() => undefined} />);
  const groups = blockLines();
  expect(groups).toEqual(["SMA-female · 1", "SMA-female bulkhead · 1", "SMA-female flange · 1", "SMA-female R/A · 1", "SMA-male · 1", "SMA-male R/A · 1", "RP-SMA-female · 1", "RP-SMA-male · 1", "N-female · 1"]);
  const types = blockRows().map((row) => within(row).getAllByRole("cell")[1].textContent);
  expect(types.slice(0, 6)).toEqual(["SMA-female", "SMA-female bulkhead", "SMA-female flange", "SMA-female R/A", "SMA-male", "SMA-male R/A"]);
});

it("a header click sorts inside every type group; the order of the groups stays (owner 19.10)", () => {
  const rows = [["SMA-female", "B1", "RG-58"], ["SMA-female", "A1", "RG-316"], ["F-male", "F1", "RG-6"], ["F-male", "F2", "RG-11"]]
    .map(([type, art, wire], index) => record(`g${index}`, { Артикул: art, "Тип/Серия": type, Провод: wire }));
  unfolded(<CoaxConnectors records={rows} canEdit={false} onEdit={() => undefined} />);
  // a block per type group (25.10): «[name]», then the cable of each row of its table
  const order = () => blockTables().flatMap((table, index) => [`[${blockLines()[index].split(" · ")[0]}]`,
    ...[...table.querySelectorAll("tbody tr")].map((row) => within(row as HTMLElement).getAllByRole("cell")[2].textContent ?? "")]);
  const groups = (list: string[]) => list.filter((item) => item.startsWith("["));
  const inside = (list: string[], name: string) => { const at = list.indexOf(`[${name}]`); return list.slice(at + 1, at + 3); };
  expect(groups(order())).toEqual(["[SMA-female]", "[F-male]"]);
  fireEvent.click(within(blockTables()[0]).getByRole("button", { name: "Кабель" }));
  const up = order();
  fireEvent.click(within(blockTables()[1]).getByRole("button", { name: /Кабель/ }));
  const down = order();
  // Группы на своих местах; внутри каждой порядок по кабелю разворачивается.
  expect(groups(up)).toEqual(["[SMA-female]", "[F-male]"]);
  expect(groups(down)).toEqual(["[SMA-female]", "[F-male]"]);
  for (const name of ["SMA-female", "F-male"]) expect(inside(down, name)).toEqual([...inside(up, name)].reverse());
});

it("offers the HEX cell only to a crimped or not yet typed shield — not to compression, twist-on, Push-On, solder or clamp", () => {
  const has = (shield: string) => hasHex({ "Тип экрана": shield });
  expect(["", "обжим", "компрессия", "накрутка", "самообжим", "пайка", "прижим", "корпус", "корпусом", "обжим корпуса"].map(has)).toEqual([true, true, false, false, false, false, false, false, false, false]);
});
