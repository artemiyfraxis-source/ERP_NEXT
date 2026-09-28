import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProcurementPage } from "./ProcurementPage";
import type { ProcurementLine } from "./api";

const line: ProcurementLine = {
  id: "30000000-0000-0000-0000-000000000001",
  legacy_id: "REQ-1",
  revision: "A",
  status: "В работе",
  version: 3,
  request_date: "2026-09-16",
  initiator: "Иванов",
  department: "КБ",
  designation: "D-1",
  project_reference: "591К.3",
  priority: "Обычный",
  delivery_point: "Москва",
  planned_date: "2026-09-30",
  link: null,
  item_type: "Комплектующие",
  design_article: null,
  category: "Общепром",
  calculated_name: "Разъём",
  calculated_quantity: "2",
  calculated_unit: "шт",
  buyer: null,
  supplier: null,
  site_name: null,
  actual_quantity: null,
  actual_unit: null,
  quality_document: "Нет",
  supplier_request_reference: null,
  expected_arrival: null,
  invoice_url: null,
  paid_at: null,
  currency: null,
  actual_price: null,
  actual_vat_rate: null,
  actual_price_with_vat: null,
  actual_sum: null,
  source_currency: null,
  source_price: null,
  source_price_with_vat: null,
  source_sum: null,
  exchange_rate: null,
  exchange_rate_date: null,
  exchange_rate_manual: false,
  invoice_item_name: null,
  invoice_number: null,
  invoice_date: null,
  pay_missing: ["ВАЛЮТА", "ЦЕНА (факт)", "ЦЕНА С НДС (факт)", "СУММА (факт)", "НАИМЕНОВАНИЕ (Счет)", "ОЖИД. ДАТА"],
};

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.restoreAllMocks());

it("takes a line and saves facts with its optimistic version", async () => {
  let taken = false;
  let savedBody: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") {
      return json({ lines: taken ? [] : [line] });
    }
    if (path === "/api/procurement/mine") {
      return json({ lines: taken ? [{ ...line, buyer: "Закупщик" }] : [] });
    }
    if (path === "/api/procurement/take" && init?.method === "POST") {
      taken = true;
      return json({ lines: [{ ...line, buyer: "Закупщик" }] });
    }
    if (path.endsWith("/facts") && init?.method === "PATCH") {
      savedBody = JSON.parse(String(init.body));
      return json({ line: { ...line, supplier: "Поставщик", version: 4 } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Взять" }));
  fireEvent.change(await screen.findByLabelText(/Поставщик/), {
    target: { value: "Поставщик" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

  await waitFor(() => expect(savedBody).toEqual(expect.objectContaining({
    version: 3,
    supplier: "Поставщик",
    actual_quantity: "2",
    actual_unit: "шт",
  })));
});

it("blocks duplicate page loads and reports refresh failures", async () => {
  let pageCalls = 0;
  let failRefresh = false;
  let releasePage: (() => void) | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (failRefresh && !path.includes("after=")) throw new TypeError("network offline");
    if (path === "/api/procurement/available") {
      return json({ lines: [line], next_cursor: "next-page" });
    }
    if (path === "/api/procurement/mine") {
      return json({ lines: [], next_cursor: null });
    }
    if (path === "/api/procurement/available?after=next-page") {
      pageCalls += 1;
      await new Promise<void>((resolve) => { releasePage = resolve; });
      return json({ lines: [line], next_cursor: null });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const more = await screen.findByRole("button", { name: "Ещё неразобранные" });
  fireEvent.click(more);
  fireEvent.click(more);
  expect(pageCalls).toBe(1);
  releasePage?.();
  await waitFor(() => expect(screen.queryByText("Загружаем…")).not.toBeInTheDocument());
  expect(screen.getAllByText("REQ-1")).toHaveLength(1);

  failRefresh = true;
  fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("network offline");
});

const awaitingInvoice: ProcurementLine = {
  ...line,
  status: "Жду счет",
  buyer: "Закупщик",
  supplier: "Поставщик",
  actual_quantity: "2",
  actual_unit: "шт",
};

function problem(status: number, detail: unknown) {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

it("keeps the invoice closed until fact money is saved with the edited field as driver", async () => {
  let current = awaitingInvoice;
  let moneyBody: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine") return json({ lines: [current], next_cursor: null });
    if (path.endsWith("/money") && init?.method === "PATCH") {
      moneyBody = JSON.parse(String(init.body));
      current = {
        ...current, version: 5, currency: "RUB", actual_price: "100", actual_vat_rate: "22",
        actual_price_with_vat: "122", actual_sum: "244", invoice_item_name: "Разъём", pay_missing: ["ОЖИД. ДАТА"],
      };
      return json({ line: current });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const toPay = await screen.findByRole("button", { name: "В оплату" });
  expect(toPay).toBeDisabled();
  expect(screen.getByText(/Для счёта не хватает: ВАЛЮТА/)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Цена без НДС"), { target: { value: "100" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить суммы" }));

  await waitFor(() => expect(moneyBody).toEqual(expect.objectContaining({
    version: 3, driver: "net", currency: "RUB", price: "100", vat_rate: "22", invoice_item_name: "Разъём",
  })));
  await waitFor(() => expect(screen.getByRole("button", { name: "В оплату" })).toBeEnabled());
});

it("asks for a manual rate when the CBR is down and resends the money with it", async () => {
  const bodies: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine") return json({ lines: [awaitingInvoice], next_cursor: null });
    if (path.endsWith("/money") && init?.method === "PATCH") {
      const body = JSON.parse(String(init.body));
      bodies.push(body);
      if (!body.manual_exchange_rate) {
        return problem(409, {
          code: "manual_rate_required", currency: "USD", date: "2026-09-28",
          message: "ЦБ не отдал курс USD на 28.09.2026 — введите курс вручную",
        });
      }
      return json({ line: { ...awaitingInvoice, exchange_rate_manual: true } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  fireEvent.change(await screen.findByLabelText(/Валюта/), { target: { value: "USD" } });
  fireEvent.change(screen.getByLabelText("Цена без НДС"), { target: { value: "10" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить суммы" }));

  fireEvent.change(await screen.findByLabelText(/Курс, ₽ за 1 USD/), { target: { value: "95,5" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить суммы" }));

  await waitFor(() => expect(bodies).toHaveLength(2));
  expect(bodies[1]).toEqual(expect.objectContaining({ currency: "USD", manual_exchange_rate: "95,5" }));
});
