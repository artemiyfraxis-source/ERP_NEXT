import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProcurementBySupplier, ProcurementSheetView } from "./ProcurementSheet";
import type { ProcurementLine } from "./api";

const base = {
  revision: "A", version: 1, request_date: "2026-09-16", initiator: "Иванов", department: "КБ",
  designation: "D-1", project_reference: null, priority: "Срочно", delivery_point: "Москва",
  planned_date: "2026-09-30", link: null, item_type: "Комплектующие", design_article: null,
  category: "Общепром", calculated_quantity: "2", calculated_unit: "шт", site_name: null,
  actual_quantity: null, actual_unit: null, quality_document: "Нет", supplier_request_reference: null,
  order_date: null, expected_arrival: null, invoice_url: null, paid_at: null, currency: null,
  actual_price: null, actual_vat_rate: null, actual_price_with_vat: null, actual_sum: null,
  source_currency: null, source_price: null, source_price_with_vat: null, source_sum: null,
  exchange_rate: null, exchange_rate_date: null, exchange_rate_manual: false, invoice_item_name: null,
  invoice_number: null, invoice_date: null, pay_missing: [], is_backorder: false, replacement: null,
  replacement_decision: null, replacement_open: false, permission_number: null, permission_date: null,
} satisfies Partial<ProcurementLine>;

function line(id: number, fields: Partial<ProcurementLine>): ProcurementLine {
  return {
    ...base, id: `40000000-0000-0000-0000-00000000000${id}`, legacy_id: `SM-${id}`, status: "Новый",
    calculated_name: `Позиция ${id}`, buyer: null, supplier: null, order_number: null, ...fields,
  } as ProcurementLine;
}

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

it("PR-11: groups open lines by supplier with stage chips and drills into one supplier", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/by-supplier") {
      return json({
        stages: ["Новый", "В работе", "Готов к запросу", "Жду счет", "В оплату"],
        lines: [
          line(1, { supplier: "ЧипДип", status: "В работе", buyer: "Петров" }),
          line(2, { supplier: "ЧипДип", status: "Жду счет", buyer: "Сидоров", order_number: "PO-001" }),
          line(3, { status: "Новый" }),
        ],
      });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementBySupplier />);
  const tile = await screen.findByRole("button", { name: /ЧипДип/ });
  expect(tile).toHaveTextContent("В работе · 1");
  expect(tile).toHaveTextContent("заказов: 1");
  expect(screen.getByRole("button", { name: /Без поставщика/ })).toHaveTextContent("заказ не сформирован");

  fireEvent.click(tile);
  expect(await screen.findByRole("cell", { name: "Сидоров" })).toBeInTheDocument();
  expect(screen.getByText("Позиция 1")).toBeInTheDocument();
  expect(screen.queryByText("Позиция 3")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "← Все поставщики" }));
  fireEvent.change(screen.getByLabelText("Поиск по поставщикам"), { target: { value: "po-001" } });
  expect(screen.getByRole("button", { name: /ЧипДип/ })).toHaveTextContent("поз.: 1");
  expect(screen.queryByRole("button", { name: /Без поставщика/ })).not.toBeInTheDocument();
});

it("loads the whole sheet with server filters and pages by cursor", async () => {
  const requested: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    requested.push(path);
    if (path === "/api/procurement/meta") return json({ units: [], statuses: ["Новый", "В пути", "Готово"] });
    if (path === "/api/procurement/lines") return json({ lines: [line(1, {})], next_cursor: "c1", buyers: ["Петров"] });
    if (path === "/api/procurement/lines?after=c1") return json({ lines: [line(2, { status: "Готово" })], next_cursor: null, buyers: ["Петров"] });
    if (path === "/api/procurement/lines?q=%D1%80%D0%B0%D0%B7%D1%8A%D1%91%D0%BC&status=%D0%93%D0%BE%D1%82%D0%BE%D0%B2%D0%BE&buyer=%D0%9F%D0%B5%D1%82%D1%80%D0%BE%D0%B2") {
      return json({ lines: [line(2, { status: "Готово" })], next_cursor: null, buyers: ["Петров"] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementSheetView />);
  expect(await screen.findByText("Позиция 1")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Показать ещё" }));
  expect(await screen.findByText("Позиция 2")).toBeInTheDocument();

  const status = screen.getByLabelText("Статус") as HTMLSelectElement;
  await waitFor(() => expect(Array.from(status.options).map((option) => option.value)).toContain("Готово"));
  fireEvent.change(screen.getByLabelText("Поиск"), { target: { value: "разъём" } });
  fireEvent.change(status, { target: { value: "Готово" } });
  fireEvent.change(screen.getByLabelText("Закупщик"), { target: { value: "Петров" } });
  fireEvent.click(screen.getByRole("button", { name: "Показать" }));
  await waitFor(() => expect(screen.queryByText("Позиция 1")).not.toBeInTheDocument());
  expect(requested.some((path) => path.includes("status="))).toBe(true);
});
