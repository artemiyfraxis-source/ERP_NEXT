import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PurchasingBuy, quotesText, sumText, type BuyCard, type BuyLine } from "./PurchasingBuy";
import type { Order, OrderLine } from "./purchasingOrdersApi";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const line = (over: Partial<BuyLine> = {}): BuyLine => ({
  line_id: "l1", legacy_id: "SM-1", version: 3, status: "Готов к запросу", stage: "ready", name: "Корпус разъёма",
  article: "SHR-02V-S-B", project: null, quantity: "200", unit: "шт", unit_gross: "3.66", total_gross: "732",
  currency: "RUB", vat_included: true, order_number: null, invoice_number: null, rfq_id: "r1", rfq_number: "КП-2609-001",
  quote_number: "КП-55", quote_date: "2026-09-14", delivery_point: "Москва", ...over,
});

const card = (over: Partial<BuyCard> = {}): BuyCard => ({
  key: "b:s1:", supplier_id: "s1", supplier: "ООО Элитан Трейд", email: "sales@elitan.test", mine: true, order_number: null,
  stage: "ready", tag: "Готов к запросу", archived: false, count: 1, sums: [{ currency: "RUB", sum: "732" }],
  quotes: [{ number: "КП-55", date: "2026-09-14", rfq_id: "r1", rfq_number: "КП-2609-001" }], invoices: [],
  decided_at: "2026-09-15T10:00:00", stage_counts: { ready: 1 }, lines: [line()], ...over,
});

const orderLine = (over: Partial<OrderLine> = {}): OrderLine => ({
  id: "l1", legacy_id: "SM-1", version: 7, status: "В оплату", name: "Корпус разъёма", calculated_name: "Корпус разъёма",
  invoice_item_name: null, article: null, project: null, quantity: "200", unit: "шт", price_with_vat: "3.66", actual_sum: "732",
  calculated_sum: null, currency: "RUB", source_currency: null, source_sum: null, expected_arrival: "2026-09-25",
  promised: "2026-09-25", invoice_number: "ЭТ-1", invoice_date: "2026-09-16", invoice_url: "/api/procurement/files/f",
  paid_on: null, actual_arrival: null, note: null, is_backorder: false, pay_missing: [], accepted_quantity: null,
  counted_quantity: null, inspected_on: null, ...over,
});

type Call = { path: string; method: string; body: unknown };

function server(cards: BuyCard[], calls: Call[], extra: (path: string, method: string, body: unknown) => Response | null = () => null) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = decodeURIComponent(String(input));
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ path, method, body });
    const own = extra(path, method, body);
    if (own) return own;
    if (path.startsWith("/api/purchasing/buy?")) return json({ scope: "mine", stages: [], cards, archived_total: cards.filter((item) => item.archived).length });
    if (path.startsWith("/api/purchasing/buy/letters?")) {
      const ordered = calls.some((call) => call.path === "/api/procurement/supplier-requests");
      return json({
        letters: [{
          supplier_id: "s1", supplier_name: "ООО Элитан Трейд", email: "sales@elitan.test", total: "732", cart: "",
          subject: ordered ? "Закупка по КП № КП-55 от 14.09.2026 — просим счёт, заказ № PO-7, 1 поз." : "Закупка по КП № КП-55 от 14.09.2026 — просим счёт, 1 поз.",
          body: "Добрый день!\n\nПо вашему КП № КП-55 от 14.09.2026 мы приняли решение о закупке", orders: ordered ? ["PO-7"] : [], quotes: [],
        }],
        skipped: [],
      });
    }
    return json({ detail: "нет" }, 404);
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("writes sums and КП references the buyer reads at a glance", () => {
  expect(sumText([{ currency: "RUB", sum: "2562" }, { currency: "USD", sum: "10.5" }]).replace(/\s/g, " ")).toBe("2 562,00 ₽ + 10,50 USD");
  expect(sumText([])).toBe("—");
  expect(quotesText({ quotes: [{ number: "сайт", date: "2026-09-14", rfq_id: "r", rfq_number: "К" }] })).toBe("КП от 14.09.2026");
  const many = ["1", "2", "3"].map((number) => ({ number, date: "2026-09-14", rfq_id: "r", rfq_number: "К" }));
  expect(quotesText({ quotes: many })).toBe("КП № 1 от 14.09.2026, КП № 2 от 14.09.2026 и ещё 1");
});

it("shows the cards by stage, the archive apart, and opens a card as a window with its positions", async () => {
  const calls: Call[] = [];
  server([
    card(),
    card({ key: "b:s2:PO-5", supplier: "ООО Контакт", order_number: "PO-5", stage: "to_pay", tag: "Ожидает оплаты" }),
    card({ key: "b:s3:PO-1", supplier: "ООО Старый", order_number: "PO-1", stage: "paid", tag: "Оплачено", archived: true }),
  ], calls);
  render(<PurchasingBuy />);
  const list = await screen.findByRole("list", { name: "Поставщики" });
  expect(within(list).getAllByRole("listitem")).toHaveLength(2);
  expect(screen.getByRole("button", { name: /Готов к запросу 1/ })).toBeTruthy();
  expect(within(list).getAllByText("732,00 ₽")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: /Архив 1/ }));
  expect(screen.getByText("ООО Старый")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Открыть в «Счета и поставки»" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Активные 2/}));
  fireEvent.click(screen.getByRole("button", { name: "ООО Элитан Трейд · Готов к запросу · 1 поз." }));
  const window = screen.getByRole("dialog", { name: "ООО Элитан Трейд · Готов к запросу" });
  expect(within(window).getByRole("table", { name: "Позиции" })).toBeTruthy();
  expect(within(window).getByText("SM-1")).toBeTruthy();
});

it("«Запросить счёт» shows the letter, forms the request with the line versions and rereads it with our order", async () => {
  const calls: Call[] = [];
  server([card()], calls, (path, method) => (path === "/api/procurement/supplier-requests" && method === "POST"
    ? json({ orders: [{ order_number: "PO-7" }], lines: [{}], skipped: [] }) : null));
  render(<PurchasingBuy />);
  fireEvent.click(await screen.findByRole("button", { name: "Запросить счёт" }));
  const window = await screen.findByRole("dialog", { name: "Запрос счёта · ООО Элитан Трейд" });
  expect(await within(window).findByDisplayValue("Закупка по КП № КП-55 от 14.09.2026 — просим счёт, 1 поз.")).toBeTruthy();
  fireEvent.click(within(window).getByRole("button", { name: "Запросить счёт (1 поз.)" }));
  expect(await within(window).findByDisplayValue(/заказ № PO-7/)).toBeTruthy();
  const request = calls.find((call) => call.path === "/api/procurement/supplier-requests")!;
  expect(request.body).toEqual({ line_ids: ["l1"], versions: { l1: 3 } });
  expect(await screen.findByText(/Запрос сформирован: PO-7 — «Жду счёт»/)).toBeTruthy();
  expect(within(window).queryByRole("button", { name: /Запросить счёт \(/ })).toBeNull();
});

it("a stale line on «Запросить счёт» closes the letter and says so", async () => {
  const calls: Call[] = [];
  server([card()], calls, (path, method) => (path === "/api/procurement/supplier-requests" && method === "POST"
    ? json({ detail: "SM-1: строка изменилась после загрузки — обновите данные" }, 409) : null));
  render(<PurchasingBuy />);
  fireEvent.click(await screen.findByRole("button", { name: "Запросить счёт" }));
  const window = await screen.findByRole("dialog", { name: "Запрос счёта · ООО Элитан Трейд" });
  await within(window).findByDisplayValue(/просим счёт/);
  fireEvent.click(within(window).getByRole("button", { name: "Запросить счёт (1 поз.)" }));
  expect(await screen.findByText(/строка изменилась после загрузки/)).toBeTruthy();
  expect(screen.queryByRole("dialog")).toBeNull();
});

const toPay = () => card({ key: "b:s1:PO-7", order_number: "PO-7", stage: "to_pay", tag: "Ожидает оплаты", lines: [line({ status: "В оплату", stage: "to_pay", order_number: "PO-7" })] });
const orderCard = (lines: OrderLine[]) => ({ order: { order_number: "PO-7", supplier: "ООО Элитан Трейд", lines } as unknown as Order, receiving: [], upds: null });

it("«Внести ПП» stores the payment order once, asks the rate when the CBR is silent, then pays the order", async () => {
  const calls: Call[] = [];
  let paidTries = 0;
  server([toPay()], calls, (path, method) => {
    if (path === "/api/purchasing/orders/PO-7") return json(orderCard([orderLine()]));
    if (path === "/api/procurement/l1/files" && method === "POST") return json({ file: { id: "f", file_name: "ПП 512.pdf", url: "/x" } });
    if (path === "/api/procurement/orders/PO-7/paid") {
      paidTries += 1;
      return paidTries === 1
        ? json({ detail: { code: "manual_rate_required", currency: "USD", date: "2026-09-16", message: "ЦБ не отдал курс USD на 16.09.2026 — введите курс вручную" } }, 409)
        : json({ lines: [] });
    }
    return null;
  });
  render(<PurchasingBuy />);
  fireEvent.click(await screen.findByRole("button", { name: "Внести ПП" }));
  const window = await screen.findByRole("dialog", { name: "Внести ПП · PO-7" });
  const submit = await within(window).findByRole("button", { name: "Внести ПП и оплатить (1)" });
  expect((submit as HTMLButtonElement).disabled).toBe(true); // no file yet
  const input = window.querySelector("input[type=file]") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["%PDF"], "ПП 512.pdf", { type: "application/pdf" })] } });
  fireEvent.click(submit);
  const rate = await within(window).findByLabelText(/Курс, ₽ за 1 USD/);
  expect(within(window).getByText("«ПП 512.pdf» приложено")).toBeTruthy();
  fireEvent.change(rate, { target: { value: "81,5" } });
  fireEvent.click(within(window).getByRole("button", { name: "Внести ПП и оплатить (1)" }));
  expect(await screen.findByText(/PO-7: оплачено, ПП «ПП 512.pdf» приложено — заказ ушёл в архив/)).toBeTruthy();
  const uploads = calls.filter((call) => call.path === "/api/procurement/l1/files");
  expect(uploads).toHaveLength(1);
  expect((uploads[0].body as { kind: string }).kind).toBe("payment");
  const pays = calls.filter((call) => call.path === "/api/procurement/orders/PO-7/paid");
  expect(pays.map((call) => call.body)).toEqual([
    { lines: [{ line_id: "l1", version: 7 }] },
    { lines: [{ line_id: "l1", version: 7 }], manual_exchange_rate: "81,5" },
  ]);
});

it("«Оплачено» needs no file and stays closed while the pay gate misses something", async () => {
  const calls: Call[] = [];
  server([toPay()], calls, (path) => (path === "/api/purchasing/orders/PO-7"
    ? json(orderCard([orderLine({ pay_missing: ["ЦЕНА С НДС (факт)"] })])) : null));
  render(<PurchasingBuy />);
  fireEvent.click(await screen.findByRole("button", { name: "Оплачено" }));
  const window = await screen.findByRole("dialog", { name: "Оплачено · PO-7" });
  expect(await within(window).findByText(/Оплата закрыта/)).toBeTruthy();
  expect(window.querySelector("input[type=file]")).toBeNull();
  expect((within(window).getByRole("button", { name: "Оплачено (1)" }) as HTMLButtonElement).disabled).toBe(true);
});

it("a viewer sees every buyer's cards without actions", async () => {
  const calls: Call[] = [];
  server([card({ mine: false })], calls);
  render(<PurchasingBuy readOnly />);
  await screen.findByRole("list", { name: "Поставщики" });
  expect(calls[0].path).toBe("/api/purchasing/buy?scope=all");
  expect(screen.queryByRole("button", { name: "Запросить счёт" })).toBeNull();
  expect(screen.queryByRole("group", { name: "Чьи позиции" })).toBeNull();
  await waitFor(() => expect(screen.getByText("ООО Элитан Трейд")).toBeTruthy());
});
