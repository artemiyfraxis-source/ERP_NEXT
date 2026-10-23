import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PurchasingInvoices } from "./PurchasingInvoices";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const base = {
  id: "inv1", title: "Счет № 43851 от 15.09.2025", number: "43851", date: "2025-09-15", seller_name: 'ООО "СнабКабель"',
  seller_inn: "2128037304", supplier_id: null, supplier_name: null, total: 481500, status: "paid",
  payments: [{ number: "880", date: "2025-09-16", sum: 481500 }], invoice_file_id: "f-pdf",
};
const files = [
  { id: "f-pay", name: "Платежное_поручение_№880_от_16.09.2025_на_сумму_4815.pdf", kind: "payment", size: 1, downloaded: true },
  { id: "f-pdf", name: "Счет 43851.pdf", kind: "invoice", size: 1, downloaded: true },
  { id: "f-xls", name: "Спецификация.xlsx", kind: "other", size: 1, downloaded: false },
];
const row = {
  ...base, column_title: "Оплачено", card_created: "2025-09-16T08:00:00+00:00", vat: 80250, vat_free: false, parse_issues: "",
  line_count: 1, payment_numbers: "880", payment_date: "2025-09-16", payment_sum: 481500, payment_matches: true, files,
  chain: { state: "closed", problem: false, upds: 1, closed_gross: 481500, checks: [] },
};
const card = {
  ...base, board_title: "В работе", column_title: "Оплачено", card_created: "2025-09-16T08:00:00+00:00", vat: 80250, vat_free: false,
  parse_issues: "", lines: [{ seq: 1, name: "Кабель РК 50-3-11", article: "", qty: 15, unit: "м", price: 32100, sum: 481500 }],
  payment_numbers: "880", payment_date: "2025-09-16", payment_sum: 481500, payment_matches: true, files, receiving_available: true,
  chain: {
    key: "invoice:inv1", invoice: base, suggestions: [], closed_gross: 481500, state: "closed", problem: false, checks: [],
    upds: [{
      id: "g-1", file_name: "g-1.pdf", number: "9", date: "2025-09-18", kind: "УПД", origin: "google", status: "parsed",
      seller_name: 'ООО "СнабКабель"', seller_inn: "2128037304", supplier_id: null, total_gross: 481500, services_only: false,
      method: "basis_date", basis: "Счет № 43851 от 15.09.2025", payment_doc: "", buyer_kind: "ours", buyer_org_inn: null,
      receiving: {
        lines: 1, grns: ["FRX.25.0000101"], statuses: [{ status: "Принято", count: 1 }],
        items: [{
          id: "r1", procurement_line_id: "pl1", document_number: "9", document_date: "2025-09-18", document_name: "УПД", supplier: "СнабКабель",
          name: "Кабель РК 50-3-11", article: "", unit: "м", accepted_quantity: "15", fact_quantity: "15", grn: "FRX.25.0000101", package: "", status: "Принято",
        }],
      },
    }],
  },
};

function mockServer(calls: string[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = decodeURIComponent(String(input));
    calls.push(path);
    if (path === "/api/purchasing/invoices/inv1") return json(card);
    if (path.startsWith("/api/purchasing/invoices")) {
      return json({ items: [row], total: 1, page: 1, page_size: 100, counts: { to_pay: 2, paid: 1, return: 0, other: 0 }, sum: 481500 });
    }
    return json({});
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

it("lists invoices with the payment check, the closing state and the files", async () => {
  const calls: string[] = [];
  mockServer(calls);
  render(<PurchasingInvoices />);
  const list = await screen.findByRole("list", { name: "Счета" });
  const cells = within(list).getAllByRole("listitem")[0];
  expect(cells.textContent).toContain("15.09.2025");
  expect(cells.textContent).toContain("43851");
  expect(cells.textContent).toContain("СнабКабель");
  expect(cells.textContent).toContain("Оплачен");
  expect(cells.textContent).toContain("16.09.2025");
  expect(cells.textContent).toContain("✓");
  expect(cells.textContent).toContain("Закрыт");
  expect(within(cells).getByRole("link", { name: "счёт" }).getAttribute("href")).toBe("/api/purchasing/invoices/inv1/files/f-pdf");
  // A file not downloaded from Kaiten is named, not linked.
  expect(within(cells).queryByRole("link", { name: "файл" })).toBeNull();
  expect(screen.getByRole("button", { name: "Все 3" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "В оплату 2" }));
  await waitFor(() => expect(calls.at(-1)).toBe("/api/purchasing/invoices?status=to_pay"));
  fireEvent.click(screen.getByLabelText("Нет закрывающих"));
  await waitFor(() => expect(calls.at(-1)).toBe("/api/purchasing/invoices?status=to_pay&closing=none"));
});

it("the card: header, positions, payment orders, files to open in a new tab, the chain down to receiving", async () => {
  mockServer([]);
  render(<PurchasingInvoices />);
  fireEvent.click(await screen.findByRole("button", { name: "43851" }));
  const dialog = await screen.findByRole("dialog", { name: "Карточка счёта" });
  await within(dialog).findByText("Позиции");
  expect(within(dialog).getByRole("heading", { name: "Счёт № 43851 от 15.09.2025" })).toBeInTheDocument();
  expect(within(dialog).getByRole("table", { name: "Позиции" }).textContent).toContain("Кабель РК 50-3-11");
  expect(within(dialog).getByRole("list", { name: "Платёжные поручения" }).textContent).toContain("№ 880 от 16.09.2025");
  const open = within(dialog).getAllByRole("link", { name: "Открыть" });
  expect(open.map((a) => a.getAttribute("href"))).toEqual([
    "/api/purchasing/invoices/inv1/files/f-pay", "/api/purchasing/invoices/inv1/files/f-pdf",
  ]);
  expect(open.every((a) => a.getAttribute("target") === "_blank")).toBe(true);
  expect(within(dialog).getByText("не скачан из Kaiten")).toBeInTheDocument();
  expect(within(dialog).getByText(/по основанию и дате/)).toBeInTheDocument();
  const receiving = within(dialog).getByRole("table", { name: "Строки приёмки" });
  expect(receiving.textContent).toContain("FRX.25.0000101");
  expect(receiving.textContent).toContain("Принято");
  fireEvent.click(within(dialog).getByRole("button", { name: "Закрыть" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});
