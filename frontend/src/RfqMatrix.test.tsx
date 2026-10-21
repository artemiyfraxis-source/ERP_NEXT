import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MatrixTable, nobodyHint, type MatrixActions } from "./RfqMatrix";
import type { RfqCell, RfqLine, RfqMatrix, RfqSupplier } from "./purchasingRfqApi";

const cell = (over: Partial<RfqCell>): RfqCell => ({
  asked: true, quote_line_id: "ql", quote_id: "q", received_on: "2026-10-01", valid_until: null, unit_price: "100",
  vat_included: true, vat_rate: "22", gross_unit: "100", cover_qty: "2", total: "200", moq: null, pack: null, tiers: [],
  lead_days: 5, in_stock: false, analog: false, analog_note: null, declined: false, expired: false, cheapest: false,
  baseline: false, decided: false, requires: [], ...over,
});
const supplier = (id: string, name: string, over: Partial<RfqSupplier> = {}): RfqSupplier => ({
  supplier_id: id, name, is_key: false, reason: "manual", sent_at: "2026-10-01T10:00:00", line_ids: null, declined_at: null,
  decline_note: null, answered: true, delivery_cost: null, min_order_sum: null, basket_total: null, basket_lines: 0,
  chosen_total: null, min_order_warning: null, ...over,
});
const line = (id: string, name: string, cells: RfqLine["cells"], over: Partial<RfqLine> = {}): RfqLine => ({
  id, procurement_line_id: `p-${id}`, legacy_id: `SM-${id}`, status: "В работе", version: 1, editable: true, name,
  article: null, quantity: "87", unit: "м", need_by: null, budget_unit_gross: "110", delta_pct: null,
  gate: { priced: 1, required: 3, has_non_key: true, require_non_key: true, passes: false },
  cheapest_quote_line_id: null, baseline_quote_line_id: null, decision: null, cells, ...over,
});
const actions: MatrixActions = {
  onOpen: vi.fn(), onBasket: vi.fn(), onLines: vi.fn(), onSent: vi.fn(), onQuote: vi.fn(), onDeclined: vi.fn(), onRemove: vi.fn(),
};
const matrixOf = (suppliers: RfqSupplier[], lines: RfqLine[]): RfqMatrix => ({
  rfq: { id: "r1", number: "КП-2610-001", title: null, created_at: "2026-10-01T10:00:00", closed_at: null, mine: true },
  settings: { gate_min_quotes: 3, gate_require_non_key: true, default_vat_rate: "22", delivery_default: "1500" },
  suppliers, lines, quotes: [],
});

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

it("a cell reads how the seller sells it: the reel, the breaks, the break applied, what is bought, the plan", () => {
  const reel = cell({
    quote_line_id: "q1", gross_unit: "97.6", base_gross: "122", cover_qty: "100", total: "9760", pack: "50", packs: null,
    tiers_gross: [{ from: "100", gross: "97.6" }], applied_from: "100", plan_pct: "-11.3", baseline: true, cheapest: true,
  });
  const exact = cell({ quote_line_id: "q2", gross_unit: "130", cover_qty: "100", total: "13000", pack: "50", packs: "2", plan_pct: "18.2" });
  const m = matrixOf([supplier("s1", "ООО Бухты"), supplier("s2", "ООО Метры", { basket_total: "14500", delivery_known: false, basket_lines: 1 })],
    [line("a", "Кабель RG-316", { s1: reel, s2: exact }, { project: "591К.3", designation: "Опытная партия", plan_supplier: "ООО План", plan_link: "https://plan.example/x" })]);
  render(<MatrixTable matrix={m} lines={m.lines} editable={false} picked={new Set()} onPick={() => undefined} basket={null} actions={actions} />);
  const first = screen.getByRole("button", { name: "Кабель RG-316 — ООО Бухты" });
  expect(first).toHaveTextContent("ступень от 100 м");
  expect(first).toHaveTextContent("бухта 50 м");
  expect(first).toHaveTextContent(/до 100 м — 122,00 ₽; от 100 — 97,60 ₽/);
  expect(first).toHaveTextContent("−11 % к плану");
  expect(within(first).getByText("база")).toBeInTheDocument();
  const row = first.closest("tr") as HTMLElement;
  expect(row).toHaveTextContent("берём 100 м");
  expect(row).toHaveTextContent("берём 100 м = 2 × 50 м");
  expect(row).toHaveTextContent("+18 % к плану");
  expect(row).toHaveTextContent("591К.3 · Опытная партия");
  expect(screen.getByRole("link", { name: "ООО План ↗" })).toHaveAttribute("href", "https://plan.example/x");
  expect(screen.getByText(/всё у него/).closest("span")).toHaveAttribute("title", expect.stringMatching(/1\s500,00 ₽/));
});

it("nobody has it — the hint says «Перезапрос»; a chosen line out of work is locked", () => {
  const refusals = line("a", "Клемма", { s1: cell({ declined: true, gross_unit: null, total: null, cover_qty: null }), s2: { asked: false } });
  const waiting = line("b", "Разъём", { s1: cell({ declined: true, gross_unit: null, total: null, cover_qty: null }) });
  const m = matrixOf([supplier("s1", "Альфа"), supplier("s2", "Бета", { answered: false })], [refusals, waiting]);
  expect(nobodyHint(m, refusals)).toBe("ни у кого нет — уберите в «Перезапрос»");
  expect(nobodyHint(m, waiting)).toBe("пока нет цен");
  const decided = line("c", "Провод", { s1: cell({ decided: true, quote_line_id: "q9" }) }, {
    editable: false, status: "Жду счет",
    decision: { id: "d", quote_line_id: "q9", supplier_id: "s1", cover_quantity: "2", unit_gross: "100", total_gross: "200",
      baseline_unit_gross: null, cheapest_unit_gross: null, priced_quotes: 1, gate_override_reason: null, not_cheapest_reason: null,
      analog_approved_by: null, decided_at: "2026-10-02T10:00:00", applied_at: null },
  });
  const locked = matrixOf([supplier("s1", "Альфа")], [decided]);
  render(<MatrixTable matrix={locked} lines={locked.lines} editable picked={new Set()} onPick={() => undefined} basket={null} actions={{ ...actions, onSplit: vi.fn() }} />);
  expect(screen.getAllByText(/🔒 заказано/).length).toBe(2);
  expect(screen.queryByLabelText("Выбрать SM-c")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Разделить SM-c" })).not.toBeInTheDocument();
});

it("in «Подбор» a click takes the offer instead of opening the decision", () => {
  const onBasket = vi.fn();
  const onOpen = vi.fn();
  const m = matrixOf([supplier("s1", "Альфа")], [line("a", "Разъём", { s1: cell({ quote_line_id: "q1" }) })]);
  render(<MatrixTable matrix={m} lines={m.lines} editable picked={new Set()} onPick={() => undefined} basket={new Map()} actions={{ ...actions, onBasket, onOpen }} />);
  fireEvent.click(screen.getByRole("button", { name: "Разъём — Альфа" }));
  expect(onBasket).toHaveBeenCalledWith("a", "q1");
  expect(onOpen).not.toHaveBeenCalled();
});
