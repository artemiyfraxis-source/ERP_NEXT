import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PriceHint, usePriceHistory } from "./PriceHistoryHint";
import { resetStageOptions } from "./StageOptions";

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

const OPTION = { key: "procurement.price_history", block: "Закупки", title: "Покупали ранее", before: "", after: "", enabled: false };
const HISTORY = {
  purchases: [{ line_id: "p1", date: "2026-09-03", supplier: "ООО Х", price_with_vat: "12.30", unit: "шт", quantity: "2", order_number: "ЗК-1" }],
  delta_pct: "8.0",
};

afterEach(() => { vi.restoreAllMocks(); resetStageOptions(); });

function Probe() {
  const history = usePriceHistory(["a"]);
  return <PriceHint info={history.a} />;
}

function mockServer(enabled: boolean) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
    String(input).includes("/api/stage-options")
      ? json({ available: true, options: [{ ...OPTION, enabled }] })
      : json({ a: HISTORY }));
}

it("shows nothing and makes no history request when the option is off", async () => {
  const spy = mockServer(false);
  render(<Probe />);
  await waitFor(() => expect(spy).toHaveBeenCalled());
  expect(screen.queryByText(/Покупали/)).toBeNull();
  expect(spy.mock.calls.some(([url]) => String(url).includes("price-history"))).toBe(false);
});

it("shows the last purchase with a red delta when the option is on", async () => {
  mockServer(true);
  render(<Probe />);
  expect(await screen.findByText(/Покупали: 12,30 ₽ \/ шт · ООО Х · 03\.09\.2026/)).toBeInTheDocument();
  expect(screen.getByText(/\+8%/)).toHaveClass("price-hint-up");
});
