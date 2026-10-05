import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PurchasingRfq } from "./PurchasingRfq";
import { resetStageOptions } from "./StageOptions";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const cell = (over: Record<string, unknown>) => ({
  quote_line_id: "ql", quote_id: "q", received_on: "2026-10-01", valid_until: null, unit_price: "100",
  vat_included: true, vat_rate: "22", gross_unit: "100", cover_qty: "2", total: "200", moq: null, pack: null, tiers: [],
  lead_days: 5, in_stock: false, analog: false, analog_note: null, declined: false, expired: false, cheapest: false,
  baseline: false, decided: false, requires: [], ...over,
});

const supplier = (id: string, name: string, isKey: boolean) => ({
  supplier_id: id, name, is_key: isKey, reason: isKey ? "key" : "manual", sent_at: "2026-10-01T10:00:00", declined_at: null,
  decline_note: null, answered: true, delivery_cost: null, min_order_sum: null, basket_total: "200", basket_lines: 1,
  chosen_total: null, min_order_warning: null,
});

const matrix = {
  rfq: { id: "r1", number: "КП-2610-001", title: "Разъёмы", created_at: "2026-10-01T10:00:00", closed_at: null, mine: true },
  settings: { gate_min_quotes: 3, gate_require_non_key: true, default_vat_rate: "22" },
  suppliers: [supplier("s1", "ООО Альфа", true), supplier("s2", "ООО Бета", false)],
  lines: [{
    id: "l1", procurement_line_id: "p1", legacy_id: "SM-1", status: "В работе", version: 3, editable: true,
    name: "Разъём", article: "SHR-02", quantity: "2", unit: "шт", need_by: "2026-10-20", budget_unit_gross: "110",
    delta_pct: "-9.1", gate: { priced: 2, required: 3, has_non_key: true, require_non_key: true, passes: false },
    cheapest_quote_line_id: "ql1", baseline_quote_line_id: "ql1", decision: null,
    cells: {
      s1: cell({ quote_line_id: "ql1", cheapest: true, baseline: true, requires: ["gate_override_reason"] }),
      s2: cell({ quote_line_id: "ql2", gross_unit: "120", total: "240", requires: ["gate_override_reason", "not_cheapest_reason"] }),
    },
  }],
  quotes: [],
};

function mockServer(posts: { path: string; body: unknown }[] = []) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      posts.push({ path, body: JSON.parse(String(init.body)) });
      return json({ id: "d1" }, 201);
    }
    if (path === "/api/purchasing/rfqs?scope=mine") {
      return json({ rfqs: [{ id: "r1", number: "КП-2610-001", title: "Разъёмы", created_at: "2026-10-01T10:00:00", closed_at: null, mine: true, lines: 1, suppliers: 2, sent: 2, quotes: 2, decided: 0 }] });
    }
    if (path === "/api/purchasing/rfqs/directory") return json({ suppliers: [] });
    if (path === "/api/purchasing/rfqs/r1") return json(matrix);
    return json({}, 404);
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  cleanup(); // unmount before the reset: a live tree would refetch the options without the mock
  resetStageOptions();
});

it("renders the matrix with the cheapest offer marked and the gate chip", async () => {
  mockServer();
  render(<PurchasingRfq />);
  const cheap = await screen.findByRole("button", { name: "Разъём — ООО Альфа" });
  expect(within(cheap).getByText("мин")).toBeInTheDocument();
  const dear = screen.getByRole("button", { name: "Разъём — ООО Бета" });
  expect(within(dear).queryByText("мин")).not.toBeInTheDocument();
  expect(screen.getByText("КП 2/3")).toBeInTheDocument();
  expect(screen.getByText("ООО Альфа ★")).toBeInTheDocument();
  expect(screen.getByText(/Δ −?-?9,1%/)).toBeInTheDocument();
});

it("asks only the reasons the choice needs and sends them with the decision", async () => {
  const posts: { path: string; body: unknown }[] = [];
  mockServer(posts);
  render(<PurchasingRfq />);
  fireEvent.click(await screen.findByRole("button", { name: "Разъём — ООО Бета" }));
  const dialog = await screen.findByRole("dialog", { name: "Разъём: выбор поставщика" });
  expect(within(dialog).getByLabelText("Причина обхода")).toBeInTheDocument();
  expect(within(dialog).getByLabelText("Почему не самое дешёвое")).toBeInTheDocument();
  expect(within(dialog).queryByLabelText("С кем согласован аналог")).not.toBeInTheDocument();
  const choose = within(dialog).getByRole("button", { name: /Выбрать «ООО Бета»/ });
  expect(choose).toBeDisabled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Срочно" }));
  fireEvent.change(within(dialog).getByLabelText("Почему не самое дешёвое"), { target: { value: "Срок" } });
  expect(choose).toBeEnabled();
  fireEvent.click(choose);
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].path).toBe("/api/purchasing/rfqs/r1/decisions");
  expect(posts[0].body).toEqual({
    rfq_line_id: "l1", quote_line_id: "ql2", expected_version: 3, gate_override_reason: "Срочно", not_cheapest_reason: "Срок",
  });
});

it("the cheapest offer asks only for the gate reason when too few quotes came", async () => {
  mockServer();
  render(<PurchasingRfq />);
  fireEvent.click(await screen.findByRole("button", { name: "Разъём — ООО Альфа" }));
  const dialog = await screen.findByRole("dialog", { name: "Разъём: выбор поставщика" });
  expect(within(dialog).getByLabelText("Причина обхода")).toBeInTheDocument();
  expect(within(dialog).queryByLabelText("Почему не самое дешёвое")).not.toBeInTheDocument();
});
