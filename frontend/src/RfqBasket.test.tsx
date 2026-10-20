import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { basketTotals, bestBasket, cheapestPicks, RfqBasket, type BasketLine, type Delivery } from "./RfqBasket";
import type { RfqCell, RfqLine, RfqMatrix, RfqSupplier } from "./purchasingRfqApi";

// Roller rfq-basket.test.ts: three lines, two suppliers — A is cheaper on two lines by little, B on one by little, and
// every supplier costs a delivery (kopecks).
const offer = (quoteLineId: string, supplierId: string, coverGross: number, analog = false) => ({ quoteLineId, supplierId, coverGross, analog, declined: false });
const LINES: BasketLine[] = [
  { rfqLineId: "1", offers: [offer("11", "1", 10_000), offer("21", "2", 10_500)] },
  { rfqLineId: "2", offers: [offer("12", "1", 20_000), offer("22", "2", 20_400)] },
  { rfqLineId: "3", offers: [offer("13", "1", 30_800), offer("23", "2", 30_000)] },
];
const flat = (cost: number) => (): Delivery => ({ cost, known: false });

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

it("the cheapest per line spreads the order; with deliveries counted one supplier wins", () => {
  const cheap = cheapestPicks(LINES, true);
  expect([...cheap.values()]).toEqual(["11", "12", "23"]);
  const spread = basketTotals(LINES, cheap, flat(150_000));
  expect([spread.goods, spread.delivery, spread.total, spread.suppliers.length]).toEqual([60_000, 300_000, 360_000, 2]);
  const best = bestBasket(LINES, flat(150_000), true);
  expect([...best.values()]).toEqual(["11", "12", "13"]); // all from A: 800 kopecks dearer, one delivery less
  expect(basketTotals(LINES, best, flat(150_000)).total).toBe(60_800 + 150_000);
  // Free delivery: nothing to save on it, the cheapest per line stays.
  expect([...bestBasket(LINES, flat(0), true).values()]).toEqual(["11", "12", "23"]);
});

it("analogs fill a line only where no original was offered, unless the buyer allows them", () => {
  const lines: BasketLine[] = [{ rfqLineId: "1", offers: [offer("11", "1", 9_000, true), offer("21", "2", 12_000)] }, { rfqLineId: "2", offers: [offer("12", "1", 5_000, true)] }];
  expect([...cheapestPicks(lines, true).values()]).toEqual(["21", "12"]);
  expect([...cheapestPicks(lines, false).values()]).toEqual(["11", "12"]);
  // A line nobody priced is not counted as missing; one with prices but no pick is.
  expect(basketTotals(lines, new Map([["1", "21"]]), flat(0)).unpicked).toBe(1);
});

const cell = (over: Partial<RfqCell>): RfqCell => ({
  asked: true, quote_line_id: "ql", quote_id: "q", received_on: "2026-10-01", valid_until: null, unit_price: "100",
  vat_included: true, vat_rate: "22", gross_unit: "100", cover_qty: "1", total: "100", moq: null, pack: null, tiers: [],
  lead_days: 5, in_stock: false, analog: false, analog_note: null, declined: false, expired: false, cheapest: false,
  baseline: false, decided: false, requires: [], ...over,
});
const supplier = (id: string, name: string, delivery: string | null = null): RfqSupplier => ({
  supplier_id: id, name, is_key: false, reason: "manual", sent_at: null, line_ids: null, declined_at: null, decline_note: null,
  answered: true, delivery_cost: delivery, min_order_sum: null, basket_total: null, basket_lines: 0, chosen_total: null,
  min_order_warning: null,
});
const line = (id: string, name: string, cells: RfqLine["cells"], cheapest: string): RfqLine => ({
  id, procurement_line_id: `p-${id}`, legacy_id: `SM-${id}`, status: "В работе", version: 3, editable: true, name,
  article: null, quantity: "100", unit: "м", need_by: null, budget_unit_gross: "11", delta_pct: null,
  gate: { priced: 3, required: 3, has_non_key: true, require_non_key: true, passes: true },
  cheapest_quote_line_id: cheapest, baseline_quote_line_id: null, decision: null, cells,
});

it("«Дешевле всего с доставкой» fills the set, asks the one reason it needs and saves all at once", async () => {
  const m: RfqMatrix = {
    rfq: { id: "r1", number: "КП-2610-001", title: null, created_at: "2026-10-01T10:00:00", closed_at: null, mine: true },
    settings: { gate_min_quotes: 3, gate_require_non_key: true, default_vat_rate: "22", delivery_default: "1500" },
    suppliers: [supplier("a", "Альфа"), supplier("b", "Бета")],
    lines: [
      line("x", "МС 16-13", { a: cell({ quote_line_id: "ax", total: "1000" }), b: cell({ quote_line_id: "bx", total: "1100" }) }, "ax"),
      line("y", "ПВАМ 0,35", { a: cell({ quote_line_id: "ay", total: "520" }), b: cell({ quote_line_id: "by", total: "500" }) }, "by"),
    ],
    quotes: [],
  };
  const posts: any[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    posts.push({ path: String(input), body: JSON.parse(String(init?.body)) });
    return new Response(JSON.stringify({ decided: 2, kept: 0 }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  let picks = new Map<string, string>();
  const onSaved = vi.fn();
  const { rerender } = render(<RfqBasket m={m} picks={picks} setPicks={(next) => { picks = next; }} onClose={() => undefined} onSaved={onSaved} />);
  fireEvent.click(screen.getByRole("button", { name: /Дешевле всего с доставкой · 3\s020,00 ₽ · 1 поставщик/ }));
  expect([...picks.entries()]).toEqual([["x", "ax"], ["y", "ay"]]);
  rerender(<RfqBasket m={m} picks={picks} setPicks={(next) => { picks = next; }} onClose={() => undefined} onSaved={onSaved} />);
  expect(screen.getByText(/Не самое дешёвое по 1 строк/)).toBeInTheDocument();
  expect(screen.getByText(/План по выбранным строкам 2\s200,00 ₽, по подбору 1\s520,00 ₽ без доставки: дешевле на 680,00 ₽/)).toBeInTheDocument();
  expect(screen.getByText(/доставка не указана в КП — принята 1\s500,00 ₽/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Выбрать поставщиков по подбору (2 строк)" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(posts[0].path).toBe("/api/purchasing/rfqs/r1/decisions/batch");
  expect(posts[0].body).toEqual({
    picks: [{ rfq_line_id: "x", quote_line_id: "ax", expected_version: 3 }, { rfq_line_id: "y", quote_line_id: "ay", expected_version: 3 }],
    not_cheapest_code: "other", not_cheapest_note: "Подбор: дешевле в сумме с доставкой — меньше поставщиков",
  });
});
