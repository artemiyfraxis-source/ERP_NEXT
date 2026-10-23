import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { presetRange } from "./ProcurementKpi";
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
  money: { spend: "1250.50", compared_spend: "1210.50", baseline_spend: "1290.50", savings: "40.00", savings_pct: "3.1", compared_lines: 1, surplus_spend: null, vs_last_purchase: null, delivery_spend: null },
  delivery: { due: 2, on_time: 1, on_time_pct: "50.0", avg_delay_days: "3.0", avg_request_to_order_days: "4.0", avg_request_to_delivery_days: null },
  quality: { lines: 2, lines_with_defect: null, defect_pct: null, defect_amount: null, returned_amount: null },
  competition: { decisions: null, avg_offers: null, with_non_key_pct: null, override_pct: null, top1_share: "60.0", top3_share: "100.0", new_suppliers: null },
};
const supplier = {
  supplier: "Альфа", is_key: null, asked: null, answered: null, response_pct: null, order_lines: 2, spend: "1250.50",
  on_time_pct: "50.0", defect_pct: null, price_vs_best_pct: null,
};
const highlight = {
  line_id: "l1", order_number: "ЗК-7", order_date: "2026-10-05", supplier: "Альфа", item: "Разъём DB9", quantity: "2", unit: "шт",
  unit_price: "100.00", baseline_unit_price: "120.00", savings: "40.00",
};

function mockServer(requests: string[] = []) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.startsWith("/api/procurement/kpi")) {
      requests.push(path);
      return json({ rows: [row("Петров", 2)], total: row("Итого", 2), buyers: ["Петров"], kpi: sheet, suppliers: [supplier], highlights: [highlight] });
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
