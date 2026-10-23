import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { firstSupplier, parseTiers, QuoteEntry } from "./RfqQuoteDialog";
import type { RfqLine, RfqMatrix, RfqSupplier } from "./purchasingRfqApi";

const line = (id: string, name: string): RfqLine => ({
  id, procurement_line_id: `p-${id}`, legacy_id: `SM-${id}`, status: "В работе", version: 1, editable: true, name,
  article: null, quantity: "1500", unit: "шт", need_by: null, budget_unit_gross: null, delta_pct: null,
  gate: { priced: 0, required: 3, has_non_key: false, require_non_key: true, passes: false },
  cheapest_quote_line_id: null, baseline_quote_line_id: null, decision: null, cells: {},
});
const supplier = (id: string, name: string): RfqSupplier => ({
  supplier_id: id, name, is_key: false, reason: "manual", sent_at: null, line_ids: null, declined_at: null,
  decline_note: null, answered: false, delivery_cost: null, min_order_sum: null, basket_total: null, basket_lines: 0,
  chosen_total: null, min_order_warning: null,
});
const matrix: RfqMatrix = {
  rfq: { id: "r1", number: "КП-2610-001", title: null, created_at: "2026-10-01T10:00:00", closed_at: null, mine: true },
  settings: { gate_min_quotes: 3, gate_require_non_key: true, default_vat_rate: "22" },
  suppliers: [supplier("s1", "ООО Альфа")], lines: [line("a", "Разъём")], quotes: [],
};

function server(posts: { path: string; body: any }[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      posts.push({ path, body });
      if (path.endsWith("/suppliers")) return new Response(JSON.stringify({ supplier_id: "s9", name: "ООО Новый", is_key: false, categories: [] }), { status: 201 });
      return new Response(JSON.stringify({ id: "q1" }), { status: 201 });
    }
    return new Response(JSON.stringify({ available: false, options: [] }), { status: 200 });
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

it("reads price breaks as Roller does", () => {
  expect(parseTiers("500: 90; 1 000: 80,50")).toEqual([{ from: "500", price: "90" }, { from: "1000", price: "80.5" }]);
  expect(parseTiers("от 500 шт — 90 ₽\nот 1000 — 80")).toEqual([{ from: "500", price: "90" }, { from: "1000", price: "80" }]);
  expect(parseTiers("")).toEqual([]);
  expect(parseTiers("500: 90; дорого")).toBeNull();
  expect(parseTiers("0: 90")).toBeNull();
});

it("sends the quote number, the note and the price breaks; a broken break stops the save", async () => {
  const posts: { path: string; body: any }[] = [];
  server(posts);
  const onSaved = vi.fn();
  render(<QuoteEntry matrix={matrix} supplierId="s1" onClose={() => undefined} onSaved={onSaved} />);
  fireEvent.change(screen.getByLabelText("Номер КП"), { target: { value: "КП-17" } });
  fireEvent.change(screen.getByLabelText("Примечание к КП"), { target: { value: "цены до конца месяца" } });
  fireEvent.change(screen.getByLabelText("Цена Разъём"), { target: { value: "100" } });
  fireEvent.change(screen.getByLabelText("Ступени цены Разъём"), { target: { value: "500: 90; много" } });
  expect(screen.getByLabelText("Ступени цены Разъём")).toHaveAttribute("aria-invalid", "true");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить КП" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Ступени цены не прочитаны: Разъём");
  expect(posts).toHaveLength(0);
  fireEvent.change(screen.getByLabelText("Ступени цены Разъём"), { target: { value: "500: 90; 1000: 80" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить КП" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith("ООО Альфа"));
  expect(posts[0].body).toMatchObject({
    supplier_id: "s1", number: "КП-17", note: "цены до конца месяца",
    lines: [{ rfq_line_id: "a", unit_price: "100", tiers: [{ from: "500", price: "90" }, { from: "1000", price: "80" }] }],
  });
});

const two: RfqMatrix = {
  ...matrix,
  suppliers: [{ ...supplier("s1", "ООО Альфа"), answered: true }, { ...supplier("s2", "ООО Бета"), line_ids: [] }],
  lines: [line("a", "Разъём"), line("b", "Клемма")],
};

it("opens on the column asked for, else on the first who has not answered", () => {
  expect(firstSupplier(two, "s1")).toBe("s1");
  expect(firstSupplier(two, null)).toBe("s2");
  expect(firstSupplier({ ...two, suppliers: [{ ...two.suppliers[0] }] }, null)).toBe("s1");
  expect(firstSupplier({ ...two, suppliers: [] }, null)).toBe("");
});

it("an inline list, not a window: the supplier is picked from the request's own, the rows follow what he was asked", async () => {
  const posts: { path: string; body: any }[] = [];
  server(posts);
  const onSaved = vi.fn();
  render(<QuoteEntry matrix={two} supplierId={null} onClose={() => undefined} onSaved={onSaved} />);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  const entry = screen.getByRole("region", { name: "Внести КП" });
  const picker = within(entry).getByLabelText("Поставщик КП");
  // Only the request's suppliers — no free text, no directory search.
  expect(within(picker).getAllByRole("option").map((option) => option.textContent)).toEqual(["ООО Альфа — КП есть", "ООО Бета"]);
  expect(picker).toHaveValue("s2");
  expect(within(entry).queryByLabelText("Цена Разъём")).not.toBeInTheDocument(); // ООО Бета was asked nothing
  fireEvent.change(picker, { target: { value: "s1" } });
  fireEvent.change(within(entry).getByLabelText("Цена Клемма"), { target: { value: "95" } });
  fireEvent.click(within(entry).getByRole("button", { name: "Сохранить КП" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith("ООО Альфа"));
  expect(posts.map((post) => post.path)).toEqual(["/api/purchasing/rfqs/r1/quotes"]);
  expect(posts[0].body).toMatchObject({ supplier_id: "s1", lines: [{ rfq_line_id: "b", unit_price: "95" }] });
});
