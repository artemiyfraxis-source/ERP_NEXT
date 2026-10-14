import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CoaxConnectors, coaxAngle, coaxGender, coaxSeries, coaxSupplier } from "./CoaxConnectors";
import type { TechOperationRecord } from "./api";

const record = (id: string, fields: Record<string, string>): TechOperationRecord => ({
  id, tab: "coax", display_text: id, source_sheet: "БД.КОАКС", fields,
});

// Mirror rows (sheet columns) and rows of the gsm-repiteri.ru load (schema keys + card columns).
const RECORDS = [
  record("n1", { coaxArticle: "N-111L", coaxType: "N-male", coaxWire: "RG-316", Артикул: "N-111L", "Тип/Серия": "N-male",
    Провод: "RG-316", L1: "4", L2: "2", L3: "6", "Тип пина": "пайка", "Тип экрана": "обжим", Серия: "N", Пол: "штекер",
    Наименование: "Разъём N-111L (N-male, обжимной, на кабель RG-174, RG-316)", Ссылка: "https://gsm-repiteri.ru/n-111l",
    "Волновое сопротивление, Ом": "50" }),
  record("n2", { coaxArticle: "N-111L", coaxType: "N-male", coaxWire: "RG-174", Серия: "N", Пол: "штекер",
    "Источник данных": "gsm-repiteri.ru, 12.10.2026" }),
  record("s1", { Артикул: "SMA-C174P", "Тип/Серия": "RP-SMA-male R/A", Провод: "RG-174" }),
  record("x1", { Артикул: "Без типа", Провод: "RG-58" }),
];
const DIA: Record<string, Record<string, string>> = { rg316: { D1: "0,5", D2: "1,48", D3: "1,95" } };
const cableOf = (item: TechOperationRecord) => DIA[(item.fields.coaxWire ?? item.fields["Провод"] ?? "").toLowerCase().replace(/[\s-]/g, "")];

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

it("groups the connectors by series in the shop's order with the cable's D1–D3 and the stripping lengths", () => {
  render(<CoaxConnectors records={RECORDS} cableOf={cableOf} canEdit onEdit={() => undefined} />);
  const groups = screen.getAllByRole("row").filter((row) => row.classList.contains("group-row")).map((row) => row.textContent);
  expect(groups).toEqual(["SMA · 1", "N · 2", "Без серии · 1"]);
  const n316 = screen.getAllByRole("row").find((row) => row.textContent?.includes("RG-316"))!;
  expect(within(n316).getByText("0,5 · 1,48 · 1,95")).toBeInTheDocument();
  expect(within(n316).getByText("4 · 2 · 6")).toBeInTheDocument();
  expect(screen.getAllByText("нет у кабеля")).toHaveLength(3);
  expect(screen.getByRole("columnheader", { name: "Разделка L1 · L2 · L3" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Угловые/ }));
  expect(screen.getByText("SMA-C174P")).toBeInTheDocument();
  expect(screen.queryByText("N-111L")).toBeNull();
});

it("opens the connector's card on a row click: what the technologist still has to fill, the link, the other cables", () => {
  const onEdit = vi.fn();
  render(<CoaxConnectors records={RECORDS} cableOf={cableOf} canEdit onEdit={onEdit} />);
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getAllByRole("row").find((row) => row.textContent?.includes("RG-174") && row.textContent.includes("N-111L"))!);
  let card = screen.getByRole("dialog", { name: "Карточка разъёма N-111L" });
  expect(within(card).getByText("RG-174")).toBeInTheDocument();
  expect(within(card).getAllByText("не задано")).toHaveLength(3); // разделка, пин, экран
  expect(within(card).getByText("нет у кабеля")).toBeInTheDocument();
  expect(within(card).getByText("gsm-repiteri.ru, 12.10.2026")).toBeInTheDocument();

  fireEvent.click(within(card).getByRole("button", { name: "RG-316" }));
  card = screen.getByRole("dialog", { name: "Карточка разъёма N-111L" });
  expect(within(card).getByText("Разъём N-111L (N-male, обжимной, на кабель RG-174, RG-316)")).toBeInTheDocument();
  expect(within(card).getByText("0,5 · 1,48 · 1,95")).toBeInTheDocument();
  expect(within(card).getByText("50 Ом")).toBeInTheDocument();
  expect(within(card).queryByText("не задано")).toBeNull();
  expect(within(card).getByRole("link", { name: "Страница на сайте поставщика" })).toHaveAttribute("href", "https://gsm-repiteri.ru/n-111l");

  fireEvent.click(within(card).getByRole("button", { name: "Править запись" }));
  expect(onEdit).toHaveBeenCalledWith(RECORDS[0]);
  expect(screen.queryByRole("dialog")).toBeNull(); // правка открывается своим окном, карточка уходит
});

it("closes the card with the close button and with Esc", () => {
  render(<CoaxConnectors records={RECORDS} cableOf={cableOf} canEdit onEdit={() => undefined} />);
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
  render(<CoaxConnectors records={records} cableOf={cableOf} canEdit onEdit={() => undefined} />);
  expect(screen.getByRole("columnheader", { name: "Производитель" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Поставщик" })).toBeInTheDocument();
  const mcx = screen.getAllByRole("row").find((row) => row.textContent?.includes("MCX-1"))!;
  expect(within(mcx).getByText("Amphenol")).toBeInTheDocument();
  const n316 = screen.getAllByRole("row").find((row) => row.textContent?.includes("N-111L") && row.textContent.includes("RG-316"))!;
  expect(within(n316).getByText("gsm-repiteri.ru")).toBeInTheDocument();
});

it("shows the card without the edit button to a viewer", () => {
  render(<CoaxConnectors records={RECORDS} cableOf={cableOf} canEdit={false} onEdit={() => undefined} />);
  fireEvent.click(screen.getByText("SMA-C174P"));
  const card = screen.getByRole("dialog", { name: "Карточка разъёма SMA-C174P" });
  expect(within(card).getByText("угловой")).toBeInTheDocument();
  expect(within(card).queryByRole("button", { name: "Править запись" })).toBeNull();
});

it("shows the owner's own columns at once and edits their cells with the old value", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(RECORDS[0]), { status: 200, headers: { "Content-Type": "application/json" } }));
  const edited = vi.fn();
  render(<CoaxConnectors records={RECORDS.map((item) => ({ ...item, fields: { ...item.fields, Полка: item.id === "n1" ? "А-1" : "" } }))} cableOf={cableOf} canEdit onEdit={() => undefined} ownColumns={["Полка"]} onEdited={edited} />);
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
