import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RfqKaitenInvoices } from "./RfqKaitenInvoices";
import type { KaitenInvoice, RfqLine, RfqMatrix } from "./purchasingRfqApi";

const line = (id: string, name: string, unit: string): RfqLine => ({
  id, procurement_line_id: `p-${id}`, legacy_id: `SM-${id}`, status: "В работе", version: 1, editable: true, name,
  article: null, quantity: unit === "м" ? "100" : "20", unit, need_by: null, budget_unit_gross: null, delta_pct: null,
  gate: { priced: 1, required: 3, has_non_key: true, require_non_key: true, passes: false },
  cheapest_quote_line_id: null, baseline_quote_line_id: null, decision: null, cells: {},
});
const m: RfqMatrix = {
  rfq: { id: "r1", number: "КП-2609-001", title: null, created_at: "2026-09-15T11:30:00", closed_at: null, mine: true },
  settings: { gate_min_quotes: 3, gate_require_non_key: true, default_vat_rate: "22" }, suppliers: [],
  lines: [line("a", "Кабель RG-316", "м"), line("b", "Разъём SMA-J", "шт")], quotes: [],
};
const invoice: KaitenInvoice = {
  invoice_id: "i1", title: "Счет № СЧ-78", number: "СЧ-78", date: "2026-09-16", status: "paid", seller_name: "Альфа", seller_inn: "7701000019",
  supplier_id: "s1", supplier_name: "Альфа", quote_number: null, same_document: false, total: "9300", vat_included: true, vat_rate: "22",
  delivery_gross: "500", matched: 2, ordered_lines: [], has_file: true,
  lines: [
    { seq: 1, name: "Кабель коаксиальный", article: "RG-316", qty: "0.1", unit: "км", price: "48000", sum: "4800", delivery: false, matched_by: "name", targets: [{ rfq_line_id: "a", quantity: "100" }] },
    { seq: 2, name: "Разъём SMA-J-01", article: "", qty: "20", unit: "шт", price: "150", sum: "3000", delivery: false, matched_by: "article", targets: [{ rfq_line_id: "b", quantity: "20" }] },
    { seq: 3, name: "Доставка", article: "", qty: "1", unit: "усл", price: "500", sum: "500", delivery: true, matched_by: null, targets: [] },
  ],
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

it("lists the invoice after the request; «Оформить» checks dry, asks the reasons, then sends the picks with the date", async () => {
  const posts: any[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      posts.push(body);
      if (body.dry_run) {
        return json({ dry_run: true, needs: { gate: 2, not_cheapest: 0, analog: 0 }, problems: [], has_file: true, expected_suggestion: "2026-09-23",
          lines: [{ rfq_line_id: "a", name: "Кабель RG-316", seq: 1, quantity: "100", unit_price: "48", offer: "invoice", decision: "new", quote_gross: null, invoice_gross: "48" }] });
      }
      return json({ dry_run: false, needs: { gate: 0, not_cheapest: 0, analog: 0 }, problems: [], has_file: true, expected_suggestion: null, lines: [],
        done: ["КП из счёта", "выбор поставщика по 2 строк", "запрос поставщику PO-001", "счёт № СЧ-78 в заказе PO-001 — «В оплату»"], orders: ["PO-001"] });
    }
    if (path === "/api/purchasing/rfqs/r1/kaiten") return json({ rfq_date: "2026-09-15", synced_at: null, unread: 1, invoices: [invoice] });
    return json({}, 404);
  });
  const onDone = vi.fn();
  render(<RfqKaitenInvoices m={m} editable onDone={onDone} />);
  const panel = await screen.findByRole("region", { name: "Счета из Kaiten" });
  expect(within(panel).getByText(/датой не раньше запроса \(15\.09\.2026\) · оформить: 1/)).toBeInTheDocument();
  expect(within(panel).getByText("2 из 2")).toBeInTheDocument();
  fireEvent.click(within(panel).getByRole("button", { name: "Оформить" }));
  const form = await screen.findByRole("region", { name: "Оформить счёт № СЧ-78" });
  await waitFor(() => expect(within(form).getByLabelText("Ожидаемая дата поставки")).toHaveValue("2026-09-23"));
  expect(posts[0]).toEqual({ picks: [{ seq: 1, rfq_line_id: "a", quantity: "100" }, { seq: 2, rfq_line_id: "b", quantity: "20" }], dry_run: true });
  const go = within(form).getByRole("button", { name: "Оформить счёт № СЧ-78 (2 строк)" });
  expect(go).toBeDisabled();
  expect(within(form).getByText("Укажите причину обхода правила КП")).toBeInTheDocument();
  fireEvent.change(within(form).getByLabelText("Причина обхода"), { target: { value: "urgent" } });
  expect(go).toBeEnabled();
  fireEvent.click(go);
  await waitFor(() => expect(onDone).toHaveBeenCalled());
  expect(posts.at(-1)).toEqual({
    picks: [{ seq: 1, rfq_line_id: "a", quantity: "100" }, { seq: 2, rfq_line_id: "b", quantity: "20" }],
    expected_arrival: "2026-09-23", gate_override_code: "urgent", gate_override_note: null,
  });
  expect(onDone.mock.calls[0][0]).toContain("счёт № СЧ-78 в заказе PO-001 — «В оплату»");
});
