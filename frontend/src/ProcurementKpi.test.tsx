import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { defectText, presetRange } from "./ProcurementKpi";
import { ProcurementPage } from "./ProcurementPage";
import * as print from "./print";

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

const row = (buyer: string, lines: number) => ({
  buyer, lines, paid_sum: "1250.50", received: 1,
  on_time: { n: 2, ok: 1, percent: "50.0" },
  invoice_to_paid: { days: "1.5", n: 2 },
  paid_to_received: { days: null, n: 0 },
  savings: { sum: "40.00", n: 1, excluded: 1 },
});

const sheet = {
  money: { spend: "1250.50", compared_spend: "1210.50", baseline_spend: "1290.50", savings: "40.00", savings_pct: "3.1", compared_lines: 1, kp_savings: null, kp_savings_pct: null, kp_compared_lines: 0, surplus_spend: null, vs_last_purchase: null, compared_with_last: 0, delivery_spend: null },
  delivery: { due: 2, on_time: 1, on_time_pct: "50.0", avg_delay_days: "3.0", avg_request_to_order_days: "4.0", avg_request_to_delivery_days: null },
  quality: { lines: 2, received_lines: null, lines_with_defect: null, defect_pct: null, defect_amount: null, defect_without_quantity: null, returned_amount: null },
  competition: { decisions: null, avg_offers: null, with_non_key_pct: null, override_pct: null, top1_share: "60.0", top3_share: "100.0", new_suppliers: null },
};
const withFacts = {
  ...sheet,
  money: { ...sheet.money, kp_savings: "600.00", kp_savings_pct: "12.0", kp_compared_lines: 1, vs_last_purchase: "200.00", compared_with_last: 1, delivery_spend: "1500.00" },
  quality: { lines: 2, received_lines: 2, lines_with_defect: 1, defect_pct: "50.0", defect_amount: "132.00", defect_without_quantity: 1, returned_amount: "220.00" },
  competition: { ...sheet.competition, decisions: 4, avg_offers: "2.0", with_non_key_pct: "50.0", override_pct: "25.0", new_suppliers: 1 },
};
const supplier = {
  supplier: "Альфа", is_key: null, asked: null, answered: null, response_pct: null, order_lines: 2, spend: "1250.50",
  on_time_pct: "50.0", defect_pct: null, price_vs_best_pct: null,
};
const highlight = {
  line_id: "l1", order_number: "ЗК-7", order_date: "2026-10-05", supplier: "Альфа", item: "Разъём DB9", quantity: "2", unit: "шт",
  unit_price: "100.00", baseline_unit_price: "120.00", savings: "40.00",
};

function mockServer(requests: string[] = [], filled = false) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.startsWith("/api/procurement/kpi")) {
      requests.push(path);
      return json({
        rows: [row("Петров", 2)], total: row("Итого", 2), buyers: ["Петров"], kpi: filled ? withFacts : sheet,
        suppliers: [filled ? { ...supplier, is_key: true, asked: 2, answered: 1, response_pct: "50.0", defect_pct: "50.0", defect_amount: "132.00", price_vs_best_pct: "10.0" } : supplier],
        highlights: [highlight],
      });
    }
    if (path === "/api/procurement/today") return json({ today: "2026-10-21", sections: [], calm: true });
    if (path === "/api/procurement/meta") return json({ units: ["шт"], statuses: [] });
    return json({ lines: [], buyers: [], stages: [], next_cursor: null });
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  cleanup();
});

it("shows the table with buyer rows and a total, and refetches on a buyer filter", async () => {
  const requests: string[] = [];
  mockServer(requests);
  window.history.replaceState(null, "", "/?section=procurement&tab=stats"); // «Показатели» live in «Статистика»
  render(<ProcurementPage />);
  fireEvent.click(await screen.findByRole("tab", { name: "Показатели" }));
  expect(await screen.findByRole("cell", { name: "Петров" })).toBeInTheDocument();
  expect(screen.getByText("Итого")).toBeInTheDocument();
  expect(screen.getAllByText(/50,0% \(1 из 2\)/)).toHaveLength(2);
  expect(requests[0]).toMatch(/from=\d{4}-\d{2}-01&to=\d{4}-\d{2}-\d{2}$/);
  fireEvent.change(screen.getByLabelText("Закупщик"), { target: { value: "Петров" } });
  await waitFor(() => expect(requests.some((path) => path.includes("buyer="))).toBe(true));
});

it("shows the cards, the supplier rating and the best savings; a metric without a source reads «нет данных»", async () => {
  mockServer();
  window.history.replaceState(null, "", "/?section=procurement&tab=stats"); // «Показатели» live in «Статистика»
  render(<ProcurementPage />);
  fireEvent.click(await screen.findByRole("tab", { name: "Показатели" }));
  const money = (await screen.findByRole("heading", { name: "Деньги" })).closest("section")!;
  expect(within(money).getByText(/40,00 ₽/)).toBeInTheDocument();
  expect(within(money).getByText(/3,1 %/)).toBeInTheDocument();
  const quality = screen.getByRole("heading", { name: "Качество" }).closest("section")!;
  expect(within(quality).getAllByText("нет данных")).toHaveLength(3);
  const competition = screen.getByRole("heading", { name: "Конкуренция" }).closest("section")!;
  expect(within(competition).getByText("60,0 %")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Рейтинг поставщиков" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "Разъём DB9" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "05.10.2026 · ЗК-7" })).toBeInTheDocument();
});

it("a period button refetches the range; the print button prints the sheet", async () => {
  const requests: string[] = [];
  mockServer(requests);
  const printed = vi.spyOn(print, "openPrintDocument").mockImplementation(() => {});
  window.history.replaceState(null, "", "/?section=procurement&tab=stats"); // «Показатели» live in «Статистика»
  render(<ProcurementPage />);
  fireEvent.click(await screen.findByRole("tab", { name: "Показатели" }));
  await screen.findByRole("heading", { name: "Деньги" });
  fireEvent.click(screen.getByRole("button", { name: "Год" }));
  await waitFor(() => expect(requests.some((path) => path.includes("-01-01&to="))).toBe(true));
  await screen.findByRole("heading", { name: "Деньги" });
  fireEvent.click(screen.getByRole("button", { name: "Итоги на печать" }));
  expect(printed).toHaveBeenCalledTimes(1);
  expect(printed.mock.calls[0]![1][0]!.title).toBe("Итоги работы закупщика за период");
});

it("period presets follow Roller's: month and year up to today, last month whole", () => {
  const now = new Date(2026, 9, 23);
  expect(presetRange("month", now)).toEqual({ from: "2026-10-01", to: "2026-10-23" });
  expect(presetRange("last", now)).toEqual({ from: "2026-09-01", to: "2026-09-30" });
  expect(presetRange("quarter", now)).toEqual({ from: "2026-10-01", to: "2026-10-23" });
  expect(presetRange("year", now)).toEqual({ from: "2026-01-01", to: "2026-10-23" });
});

it("shows the figures that purchasing and receiving add: КП savings, last purchase, delivery, defects, decisions, quote answers", async () => {
  mockServer([], true);
  window.history.replaceState(null, "", "/?section=procurement&tab=stats"); // «Показатели» live in «Статистика»
  render(<ProcurementPage />);
  fireEvent.click(await screen.findByRole("tab", { name: "Показатели" }));
  const money = (await screen.findByRole("heading", { name: "Деньги" })).closest("section")!;
  expect(within(money).getByText(/600,00 ₽/)).toBeInTheDocument();
  expect(within(money).getByText(/12,0 %, строк 1/)).toBeInTheDocument();
  expect(within(money).getByText(/200,00 ₽/)).toBeInTheDocument();
  expect(within(money).getByText("1 500,00 ₽")).toBeInTheDocument();
  const quality = screen.getByRole("heading", { name: "Качество" }).closest("section")!;
  expect(within(quality).getByText(/1 из 2/)).toBeInTheDocument();
  expect(within(quality).getByText("220,00 ₽")).toBeInTheDocument();
  // «Брак, ₽» = кол-во брака × цена оплаты; a brak line accepted without a quantity is named, not a zero.
  expect(within(quality).getByText("132,00 ₽")).toBeInTheDocument();
  expect(within(quality).getByText(/без количества: 1/)).toBeInTheDocument();
  expect(within(quality).queryByText("нет данных")).toBeNull();
  expect(screen.getAllByText("132,00 ₽")).toHaveLength(2); // and in the supplier rating, under its %
  const competition = screen.getByRole("heading", { name: "Конкуренция" }).closest("section")!;
  expect(within(competition).getByText("4")).toBeInTheDocument();
  expect(within(competition).getByText("25,0 %")).toBeInTheDocument();
  expect(screen.getByText("ключевой")).toBeInTheDocument();
  expect(screen.getByText("+10,0 %")).toBeInTheDocument();
});

it("«Брак, ₽» on the printed sheet: the sum, the brak lines without a quantity, or «нет данных»", () => {
  const quality = { lines: 3, received_lines: 3, lines_with_defect: 2, defect_pct: "66.7", returned_amount: "0.00" };
  expect(defectText({ ...quality, defect_amount: "132.00", defect_without_quantity: 0 })).toBe("132,00 ₽");
  expect(defectText({ ...quality, defect_amount: "132.00", defect_without_quantity: 1 })).toBe("132,00 ₽ (без количества: 1 строка)");
  expect(defectText({ ...quality, defect_amount: null, defect_without_quantity: 2 })).toBe("нет данных (без количества: 2 строки)");
  expect(defectText({ ...quality, defect_amount: null, defect_without_quantity: null })).toBe("нет данных");
});
