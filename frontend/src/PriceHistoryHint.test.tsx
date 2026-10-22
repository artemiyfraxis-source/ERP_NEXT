import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PriceHint, usePriceHistory } from "./PriceHistoryHint";

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

const HISTORY = {
  purchases: [{ line_id: "p1", date: "2026-09-03", supplier: "ООО Х", price_with_vat: "12.30", unit: "шт", quantity: "2", order_number: "ЗК-1" }],
  delta_pct: "8.0",
};

afterEach(() => { vi.restoreAllMocks(); });

function Probe() {
  const history = usePriceHistory(["a"]);
  return <PriceHint info={history.a} />;
}

function mockServer() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ a: HISTORY }));
}

it("shows the last purchase with a red delta", async () => {
  mockServer();
  render(<Probe />);
  expect(await screen.findByText(/Покупали: 12,30 ₽ \/ шт · ООО Х · 03\.09\.2026/)).toBeInTheDocument();
  expect(screen.getByText(/\+8%/)).toHaveClass("price-hint-up");
});
