import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProcurementPage } from "./ProcurementPage";
import {
  REFUSE_BACK, REFUSE_FOREIGN, REFUSE_NOT_PAID, REFUSE_NO_INVOICE, REFUSE_RECEIVING, boardColumns, cardChips, dropResult,
} from "./PurchasingOrderBoard";
import { PurchasingOrders } from "./PurchasingOrders";
import type { Order, OrderLine } from "./purchasingOrdersApi";
import { resetStageOptions } from "./StageOptions";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const line = (over: Partial<OrderLine> = {}): OrderLine => ({
  id: "l1", legacy_id: "SM-1", version: 4, status: "В оплату", name: "Разъём", calculated_name: "Разъём", invoice_item_name: null,
  article: null, project: "ПР-1", quantity: "2", unit: "шт", price_with_vat: "1220", actual_sum: "2440", calculated_sum: "2000",
  currency: "RUB", source_currency: null, source_sum: null, expected_arrival: "2026-09-25", promised: "2026-09-25",
  invoice_number: "СЧ-7", invoice_date: "2026-09-16", invoice_url: "https://files.test/i.pdf", paid_on: null, actual_arrival: null,
  note: null, is_backorder: false, pay_missing: [], accepted_quantity: null, counted_quantity: null, inspected_on: null, ...over,
});

const order = (over: Partial<Order> = {}): Order => ({
  order_number: "PO-1", supplier: "ООО Поставка", buyer: "Петров", mine: true, order_date: "2026-09-15", column: "unpaid",
  stages: { "В оплату": 1 }, lines_count: 1, paid_count: 0, received_count: 0, invoiced_count: 1, quarantine_count: 0,
  invoices: [{ number: "СЧ-7", date: "2026-09-16", url: "https://files.test/i.pdf" }], invoice_date: "2026-09-16",
  expected: "2026-09-25", expected_last: "2026-09-25", promised: "2026-09-25", paid_on: null, stocked_on: null, late_days: 0,
  sum: "2440", sum_estimated: false, foreign: [], projects: ["ПР-1"], lines: [line()], backorders: [],
  sent_to_pay_on: null, shipped_on: null, shipment_note: "", delivery_cost: null, note: "", facts_version: 2,
  facts_updated_by: null, facts_updated_at: null, ...over,
});

afterEach(() => {
  cleanup();
  resetStageOptions();
  vi.restoreAllMocks();
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});

it("drops go forward only, own orders only, and never onto the warehouse columns", () => {
  const unpaid = order();
  expect(dropResult(unpaid, "unpaid")).toBeNull();
  expect(dropResult(unpaid, "to_pay")).toEqual({ kind: "sent_to_pay" });
  expect(dropResult(unpaid, "waiting")).toEqual({ kind: "pay" });
  expect(dropResult(unpaid, "transit")).toEqual({ kind: "refuse", text: REFUSE_NOT_PAID });
  expect(dropResult(unpaid, "awaiting")).toEqual({ kind: "refuse", text: REFUSE_BACK });
  expect(dropResult(unpaid, "stocked")).toEqual({ kind: "refuse", text: REFUSE_RECEIVING, receiving: true });
  expect(dropResult(order({ column: "transit" }), "receiving")).toEqual({ kind: "refuse", text: REFUSE_RECEIVING, receiving: true });
  expect(dropResult(order({ column: "awaiting" }), "unpaid")).toEqual({ kind: "invoice" });
  expect(dropResult(order({ column: "awaiting" }), "to_pay")).toEqual({ kind: "refuse", text: REFUSE_NO_INVOICE });
  expect(dropResult(order({ column: "to_pay" }), "waiting")).toEqual({ kind: "pay" });
  expect(dropResult(order({ column: "waiting" }), "transit")).toEqual({ kind: "shipped" });
  expect(dropResult(order({ column: "waiting" }), "to_pay")).toEqual({ kind: "refuse", text: REFUSE_BACK });
  expect(dropResult(order({ mine: false }), "to_pay")).toEqual({ kind: "refuse", text: REFUSE_FOREIGN });
});

it("puts each order in its column and says what the column hides", () => {
  const columns = boardColumns([order(), order({ order_number: "PO-2", column: "stocked" }), order({ order_number: "PO-3", column: "unpaid" })]);
  expect(columns.get("unpaid")!.map((item) => item.order_number)).toEqual(["PO-1", "PO-3"]);
  expect(columns.get("stocked")!.map((item) => item.order_number)).toEqual(["PO-2"]);
  expect(columns.get("awaiting")).toEqual([]);
  const mixed = order({ column: "to_pay", lines_count: 5, paid_count: 2, quarantine_count: 1, mine: false, backorders: [line({ id: "b", legacy_id: "SM-1-Д", status: "Согласование" })] });
  expect(cardChips(mixed).map((chip) => chip.text)).toEqual(["оплачено 2 из 5", "карантин 1 из 5", "дозавоз 1", "Петров"]);
});

type Call = { path: string; method: string; body: unknown };

function server(orders: Order[], calls: Call[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = decodeURIComponent(String(input));
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (path.startsWith("/api/purchasing/orders?")) return json({ today: "2026-09-20", scope: "mine", orders });
    if (path === "/api/purchasing/orders/followups") return json({ awaiting_quotes: [], late: [], arriving: [], unpaid: [], awaiting_invoice: [] });
    if (path.endsWith("/facts") && method === "PATCH") {
      const body = JSON.parse(String(init!.body));
      return json({ order: { ...orders[0], facts_version: body.version + 1, sent_to_pay_on: body.sent_to_pay_on ?? null } });
    }
    if (path.endsWith("/paid")) return json({ lines: [] });
    const card = orders.find((item) => path === `/api/purchasing/orders/${item.order_number}`);
    if (card) return json({ order: card, receiving: [], upds: null });
    if (path === "/api/stage-options") return json({ available: true, options: [] });
    return json({});
  });
}

const dataTransfer = () => ({ setData: () => undefined, effectAllowed: "" });

function drag(card: string, column: string) {
  const source = screen.getByRole("button", { name: new RegExp(`Заказ ${card}`) }).closest(".order-card")!;
  const target = screen.getByRole("listitem", { name: column });
  fireEvent.dragStart(source, { dataTransfer: dataTransfer() });
  fireEvent.dragOver(target, { dataTransfer: dataTransfer() });
  fireEvent.drop(target, { dataTransfer: dataTransfer() });
}

it("dropping into «В оплате» records sent-to-pay today and «Отменить» clears it on the new version", async () => {
  const calls: Call[] = [];
  server([order()], calls);
  render(<PurchasingOrders />);
  await screen.findByRole("button", { name: /Заказ PO-1/ });
  drag("PO-1", "В оплате");
  const toast = await screen.findByRole("status");
  expect(toast).toHaveTextContent("PO-1: передан в оплату сегодня");
  const patch = calls.find((call) => call.method === "PATCH")!;
  expect(patch.path).toBe("/api/purchasing/orders/PO-1/facts");
  expect(patch.body).toEqual({ version: 2, sent_to_pay_on: "2026-09-20" });
  fireEvent.click(within(toast).getByRole("button", { name: "Отменить" }));
  await waitFor(() => expect(calls.filter((call) => call.method === "PATCH")).toHaveLength(2));
  expect(calls.filter((call) => call.method === "PATCH")[1].body).toEqual({ version: 3, sent_to_pay_on: null });
  await waitFor(() => expect(within(toast).queryByRole("button", { name: "Отменить" })).toBeNull());
});

it("dropping into «Ждём отгрузку» asks to confirm the payment and pays every line with its version", async () => {
  const calls: Call[] = [];
  server([order()], calls);
  render(<PurchasingOrders />);
  await screen.findByRole("button", { name: /Заказ PO-1/ });
  drag("PO-1", "Ждём отгрузку");
  const dialog = await screen.findByRole("dialog", { name: "Оплата PO-1" });
  expect(dialog).toHaveTextContent("Оплату нельзя отменить");
  expect(within(dialog).getByRole("table", { name: "Строки к оплате" })).toHaveTextContent("SM-1");
  expect(calls.some((call) => call.path.endsWith("/paid"))).toBe(false);
  fireEvent.click(within(dialog).getByRole("button", { name: "Оплачено (1)" }));
  await waitFor(() => expect(calls.some((call) => call.path.endsWith("/paid"))).toBe(true));
  const paid = calls.find((call) => call.path.endsWith("/paid"))!;
  expect(paid.path).toBe("/api/procurement/orders/PO-1/paid");
  expect(paid.body).toEqual({ lines: [{ line_id: "l1", version: 4 }] });
  expect(await screen.findByRole("status")).toHaveTextContent("PO-1: оплачено, строк в пути: 1");
});

it("the payment is refused up front when a line misses what the pay gate needs", async () => {
  const calls: Call[] = [];
  server([order({ lines: [line({ pay_missing: ["ВАЛЮТА", "ЦЕНА (факт)"] })] })], calls);
  render(<PurchasingOrders />);
  await screen.findByRole("button", { name: /Заказ PO-1/ });
  drag("PO-1", "Ждём отгрузку");
  const dialog = await screen.findByRole("dialog", { name: "Оплата PO-1" });
  expect(dialog).toHaveTextContent("не хватает: ВАЛЮТА, ЦЕНА (факт)");
  expect(within(dialog).getByRole("button", { name: "Оплачено (1)" })).toBeDisabled();
  expect(within(dialog).getByRole("button", { name: "Открыть заказ" })).toBeInTheDocument();
});

it("a refused drop says why and writes nothing; someone else's order does not drag", async () => {
  const calls: Call[] = [];
  server([order(), order({ order_number: "PO-2", mine: false, buyer: "Сидоров" })], calls);
  render(<PurchasingOrders />);
  await screen.findByRole("button", { name: /Заказ PO-1/ });
  drag("PO-1", "На складе");
  const toast = await screen.findByRole("alert");
  expect(toast).toHaveTextContent(REFUSE_RECEIVING);
  expect(within(toast).getByRole("button", { name: "Открыть «Приёмку»" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Заказ PO-2/ }).closest(".order-card")).toHaveAttribute("draggable", "false");
  expect(calls.some((call) => call.method !== "GET")).toBe(false);
});

it("the table view keeps Roller_Buyer's filters and a deep link opens the filter", async () => {
  const calls: Call[] = [];
  window.history.replaceState({}, "", "/?section=procurement&tab=orders&filter=stocked");
  server([order(), order({ order_number: "PO-9", column: "stocked", stocked_on: "2026-09-19" })], calls);
  render(<PurchasingOrders />);
  const table = await screen.findByRole("region", { name: "Заказы поставщикам" });
  expect(within(table).getByText("PO-9")).toBeInTheDocument();
  expect(within(table).queryByText("PO-1")).toBeNull();
  for (const label of ["Не оплачены", "В оплате", "Ждём отгрузку", "В доставке", "Просрочка", "На складе"]) {
    expect(screen.getByRole("button", { name: new RegExp(`^${label}`) })).toBeInTheDocument();
  }
});

const option = (key: string, enabled: boolean) => ({ key, block: "b", title: "t", before: "", after: "", enabled });

it("the procurement page has «Счета и поставки» only with the option on", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = decodeURIComponent(String(input));
    if (path === "/api/stage-options") return json({ available: true, options: [option("procurement.orders", true)] });
    if (path.startsWith("/api/purchasing/orders?")) return json({ today: "2026-09-20", scope: "mine", orders: [order()] });
    if (path === "/api/purchasing/orders/followups") return json({ awaiting_quotes: [], late: [], arriving: [], unpaid: [], awaiting_invoice: [] });
    return json({ lines: [], buyers: [], stages: [], next_cursor: null });
  });
  render(<ProcurementPage />);
  const tabs = await screen.findByRole("tablist", { name: "Разделы закупки" });
  fireEvent.click(await within(tabs).findByRole("tab", { name: "Счета и поставки" }));
  expect(await screen.findByRole("list", { name: "Доска заказов" })).toBeInTheDocument();
  cleanup();
  resetStageOptions();
  vi.restoreAllMocks();
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = decodeURIComponent(String(input));
    if (path === "/api/stage-options") return json({ available: true, options: [option("procurement.orders", false)] });
    return json({ lines: [], buyers: [], stages: [], next_cursor: null });
  });
  render(<ProcurementPage />);
  const off = await screen.findByRole("tablist", { name: "Разделы закупки" });
  await screen.findAllByRole("tab");
  expect(within(off).queryByRole("tab", { name: "Счета и поставки" })).toBeNull();
});

it("«Новый счёт» says why the decided quantity is above the need", async () => {
  const { surplusHint } = await import("./PurchasingNewOrder");
  expect(surplusHint({ need: "309", decided_quantity: "310", moq: null, pack: "10" })).toBe("нужно 309 — кратность");
  expect(surplusHint({ need: "20", decided_quantity: "50", moq: "50", pack: null })).toBe("нужно 20 — мин. партия");
  expect(surplusHint({ need: "20", decided_quantity: "60", moq: "50", pack: "10" })).toBe("нужно 20 — мин. партия и кратность");
  expect(surplusHint({ need: "20", decided_quantity: "20", moq: null, pack: null })).toBe("");
});
