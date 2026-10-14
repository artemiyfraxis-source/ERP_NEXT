import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PurchasingCatalog } from "./PurchasingCatalog";
import { PurchasingItems } from "./PurchasingItems";
import { resetStageOptions } from "./StageOptions";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const entry = {
  key: "a:XH2P", name: "Колодка XH 2P", article: "XH-2P", unit: "шт", purchases: 2, suppliers: 2, last_price: 8000,
  last_date: "2026-10-01", last_supplier: "ООО Бета", min_price: 8000, max_price: 10000,
};
const item = {
  id: "i1", name: "Разъём SMA", part_number: "SMA-F 01", manufacturer: null, category: "connector", item_type: null, unit: "шт",
  notes: null, archived: false, version: 1, last_price: null, purchases: 0, linked_lines: 0, demand: [],
};
const itemPrices = {
  unit: "шт",
  refs: [{ id: "r1", supplier_id: null, supplier_name: "Элитан", offered_part: "SMA-F", offered_manufacturer: "", currency: "CNY", unit_price4: 177, vat_included: false, quantity: null, lead_time: "2 нед", source: "прайс.xlsx" }],
  upd: [{ upd_id: "u1", number: "77", date: "2026-09-15", supplier_id: null, supplier_name: "ООО Альфа", line_name: "Разъём", quantity: "10", unit: "шт", unit_price_with_vat: 10000, item_unit_price_with_vat: 10000, needs_review: false }],
  invoices: [],
  summary: { count: 1, min: 10000, last: 10000, last_date: "2026-09-15", last_supplier: "ООО Альфа" },
};

function mockServer(options: boolean, calls: { method: string; path: string; body?: string }[] = []) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    calls.push({ method: (init?.method ?? "GET").toUpperCase(), path, body: init?.body as string | undefined });
    if (path === "/api/stage-options") {
      return json({ available: true, options: [{ key: "references.price_book", block: "", title: "", before: "", after: "", enabled: options }] });
    }
    if (path.startsWith("/api/purchasing/prices/catalog/points")) {
      return json([{ upd_id: "u2", number: "55", date: "2026-10-01", supplier_name: "ООО Бета", quantity: "10", unit: "шт", unit_price_with_vat: 8000 }]);
    }
    if (path.startsWith("/api/purchasing/prices/catalog")) return json({ total: 1, page: 1, page_size: 100, entries: [entry] });
    if (path === "/api/purchasing/prices/upd-items/plan") {
      return json({ candidates: [{ key: "n:x", name: "Лента изоляционная", article: "", unit: "шт", category: "other", lines: 3, suppliers: ["ООО Альфа"], last_date: null }], known: 4, skipped_services: 1, skipped_advances: 0, waiting_documents: 2 });
    }
    if (path === "/api/purchasing/prices/upd-items/apply") return json({ created: 1, aliases: 0 });
    if (path === "/api/purchasing/prices/item/i1") return json(itemPrices);
    if (path === "/api/purchasing/items/i1") return json({ ...item, aliases: [], history: [], price: { min: null, avg: null, last: null }, quotes: [] });
    if (path === "/api/purchasing/suppliers") return json([]);
    if (path.startsWith("/api/purchasing/items")) return json({ items: [item], counts: { cable: 0, connector: 1, ekb: 0, other: 0 }, total: 1, page: 1, page_size: 100 });
    return json({});
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
  resetStageOptions();
});

it("shows the catalog and opens the price points with a UPD button", async () => {
  mockServer(true);
  const opened = vi.fn();
  render(<PurchasingCatalog onOpenUpd={opened} />);
  const table = await screen.findByRole("table", { name: "Каталог закупок" });
  const row = within(table).getAllByRole("row")[1];
  expect(row.textContent).toContain("Колодка XH 2P");
  expect(row.textContent).toContain("80,00");
  expect(row.textContent).toContain("ООО Бета");
  fireEvent.click(row);
  fireEvent.click(await screen.findByRole("button", { name: "Открыть УПД 55" }));
  expect(opened).toHaveBeenCalledWith("u2");
});

it("previews the items from UPD, then creates the chosen ones", async () => {
  const calls: { method: string; path: string; body?: string }[] = [];
  mockServer(true, calls);
  render(<PurchasingCatalog />);
  fireEvent.click(await screen.findByRole("button", { name: "Завести позиции из УПД" }));
  const box = await screen.findByRole("checkbox", { name: "Завести Лента изоляционная" });
  expect(box).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Завести выбранные (1)" }));
  await waitFor(() => expect(calls.find((c) => c.path.endsWith("/apply"))?.body).toBe(JSON.stringify({ keys: ["n:x"] })));
  expect(await screen.findByText(/Создано позиций: 1/)).toBeInTheDocument();
});

it("hides the read-only user's creation button", async () => {
  mockServer(true);
  render(<PurchasingCatalog readOnly />);
  await screen.findByRole("table", { name: "Каталог закупок" });
  expect(screen.queryByRole("button", { name: "Завести позиции из УПД" })).toBeNull();
});

it("adds the price sections to the item card only with the option on", async () => {
  mockServer(true);
  render(<PurchasingItems onOpenUpd={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: "Разъём SMA" }));
  const refs = await screen.findByRole("table", { name: "Справочные цены" });
  expect(refs.textContent).toContain("¥");
  expect(refs.textContent).toContain("0,0177");
  expect(screen.getByRole("table", { name: "Покупки по УПД" }).textContent).toContain("100,00");
  expect(screen.getByRole("button", { name: "Открыть УПД 77" })).toBeInTheDocument();
});

it("leaves the item card as it was with the option off", async () => {
  const calls: { method: string; path: string }[] = [];
  mockServer(false, calls);
  render(<PurchasingItems />);
  fireEvent.click(await screen.findByRole("button", { name: "Разъём SMA" }));
  await screen.findByText("Названия");
  expect(screen.queryByRole("table", { name: "Справочные цены" })).toBeNull();
  expect(calls.some((c) => c.path.startsWith("/api/purchasing/prices"))).toBe(false);
});
