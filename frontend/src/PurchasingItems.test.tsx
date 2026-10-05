import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PurchasingItems } from "./PurchasingItems";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const row = (over: Record<string, unknown> = {}) => ({
  id: "i1", name: "Разъём SMA", part_number: "SMA-F 01", manufacturer: "Amphenol", category: "connector", item_type: null,
  unit: "шт", notes: null, archived: false, version: 1, last_price: "14", purchases: 2, linked_lines: 3,
  demand: [{ unit: "шт", quantity: "5.5", lines: 2 }], ...over,
});
const counts = { cable: 1, connector: 1, ekb: 0, other: 0 };
const card = {
  ...row(), aliases: [{ id: "a1", text: "Штекер SMA", supplier_id: "s1", supplier_name: "ООО Ромашка" }],
  history: [{ line_id: "l1", date: "2026-09-20", supplier: "ООО Б", unit_price_with_vat: "14", quantity: "3", unit: "шт", order_number: null }],
  price: { min: "10", avg: "12", last: "14" }, quotes: [],
};
const group = (over: Record<string, unknown> = {}) => ({
  kind: "article", key: "SMAF01", name: "Разъём SMA-F", part_number: "SMA-F 01", unit: "шт", category: "connector", manufacturer: null,
  lines: 3, action: "create", reason: "new", item_id: "x", ...over,
});

function mockServer(calls: { method: string; path: string; body?: string }[] = [], readOnlyList = false) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, path, body: init?.body as string | undefined });
    if (path.startsWith("/api/purchasing/items/import-from-procurement")) {
      const dry = JSON.parse(String(init?.body)).dry_run;
      return json({ groups: [group(), group({ kind: "name", key: "k", name: "Кабель МГТФ", part_number: null, category: "cable", lines: 2, action: "link", reason: "alias", existing_name: "Кабель" })], created: 1, linked_groups: 1, aliases: 2, lines: 5, dry_run: dry });
    }
    if (path === "/api/purchasing/items/i1") return json(card);
    if (path === "/api/purchasing/suppliers") return json([{ id: "s1", name: "ООО Ромашка" }]);
    if (path.startsWith("/api/purchasing/items")) {
      const category = new URLSearchParams(path.split("?")[1] ?? "").get("category");
      const items = category === "cable" ? [row({ id: "i2", name: "Кабель МГТФ", category: "cable", part_number: null, last_price: null, purchases: 0, demand: [] })] : [row()];
      return json({ items, counts, total: items.length, page: 1, page_size: 100 });
    }
    return json({});
  });
  void readOnlyList;
}

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

it("lists items with last price, purchases and demand, and filters by category chip", async () => {
  const calls: { method: string; path: string }[] = [];
  mockServer(calls);
  render(<PurchasingItems />);
  const table = await screen.findByRole("table", { name: "Номенклатура" });
  const cells = within(table).getAllByRole("row")[1];
  expect(within(cells).getByText("Разъём SMA")).toBeInTheDocument();
  expect(within(cells).getByText("SMA-F 01")).toBeInTheDocument();
  expect(cells.textContent).toContain("14,00");
  expect(cells.textContent).toContain("5,5 шт");
  expect(screen.getByRole("button", { name: "Кабель 1" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Кабель 1" }));
  await screen.findByText("Кабель МГТФ");
  expect(calls.some((call) => call.path.includes("category=cable"))).toBe(true);
});

it("searches on the server by typed words", async () => {
  const calls: { method: string; path: string }[] = [];
  mockServer(calls);
  render(<PurchasingItems />);
  await screen.findByRole("table", { name: "Номенклатура" });
  fireEvent.change(screen.getByLabelText("Поиск по номенклатуре"), { target: { value: "sma штекер" } });
  await waitFor(() => expect(calls.some((call) => new URLSearchParams(call.path.split("?")[1] ?? "").get("q") === "sma штекер")).toBe(true));
});

it("previews «Собрать из закупок» without writing, then applies", async () => {
  const calls: { method: string; path: string; body?: string }[] = [];
  mockServer(calls);
  render(<PurchasingItems />);
  await screen.findByRole("table", { name: "Номенклатура" });
  fireEvent.click(screen.getByRole("button", { name: "Собрать из закупок" }));
  const dialog = await screen.findByRole("dialog", { name: "Собрать из закупок" });
  await within(dialog).findByText(/создано позиций — 1, связано с существующими — 1/);
  expect(within(dialog).getByText("создать позицию")).toBeInTheDocument();
  expect(within(dialog).getByText("связать с «Кабель»")).toBeInTheDocument();
  const imports = () => calls.filter((call) => call.path.includes("import-from-procurement"));
  expect(imports().map((call) => JSON.parse(call.body ?? "{}").dry_run)).toEqual([true]);
  fireEvent.click(within(dialog).getByRole("button", { name: "Применить" }));
  await within(dialog).findByText(/Готово:/);
  expect(imports().map((call) => JSON.parse(call.body ?? "{}").dry_run)).toEqual([true, false]);
});

it("opens the card: aliases by supplier and the price history", async () => {
  mockServer();
  render(<PurchasingItems />);
  fireEvent.click(await screen.findByRole("button", { name: "Разъём SMA" }));
  const dialog = await screen.findByRole("dialog", { name: "Карточка позиции" });
  await within(dialog).findByText("Штекер SMA");
  expect(within(dialog).getByText(/— ООО Ромашка/)).toBeInTheDocument();
  const history = within(dialog).getByRole("table", { name: "История цен" });
  expect(history.textContent).toContain("20.09.2026");
  expect(history.textContent).toContain("14,00");
  expect(dialog.textContent).toContain("мин. 10,00 ₽, средняя 12,00 ₽, последняя 14,00 ₽");
  expect(within(dialog).getByRole("button", { name: "Объединить с…" })).toBeInTheDocument();
});

it("read-only has no editing buttons", async () => {
  mockServer();
  render(<PurchasingItems readOnly />);
  await screen.findByRole("table", { name: "Номенклатура" });
  expect(screen.queryByRole("button", { name: "+ Позиция" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Собрать из закупок" })).not.toBeInTheDocument();
});
