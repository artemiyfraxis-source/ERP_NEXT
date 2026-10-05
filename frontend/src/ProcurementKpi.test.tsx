import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProcurementPage } from "./ProcurementPage";
import { resetStageOptions } from "./StageOptions";

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

const row = (buyer: string, lines: number) => ({
  buyer, lines, paid_sum: "1250.50", received: 1,
  on_time: { n: 2, ok: 1, percent: "50.0" },
  invoice_to_paid: { days: "1.5", n: 2 },
  paid_to_received: { days: null, n: 0 },
  savings: { sum: "40.00", n: 1, excluded: 1 },
});

function mockServer(enabled: boolean, requests: string[] = []) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/stage-options") {
      return json({ available: true, options: [{ key: "procurement.kpi", block: "Закупки", title: "Показатели", before: "", after: "", enabled }] });
    }
    if (path.startsWith("/api/procurement/kpi")) {
      requests.push(path);
      return json({ rows: [row("Петров", 2)], total: row("Итого", 2), buyers: ["Петров"] });
    }
    if (path === "/api/procurement/meta") return json({ units: ["шт"], statuses: [] });
    return json({ lines: [], buyers: [], stages: [], next_cursor: null });
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  cleanup(); // unmount before the reset: a live tree would refetch the options without the mock
  resetStageOptions();
});

it("has no «Показатели» tab while the option is off", async () => {
  mockServer(false);
  render(<ProcurementPage />);
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith("/api/stage-options", expect.anything()));
  expect(screen.queryByRole("button", { name: "Показатели" })).not.toBeInTheDocument();
});

it("shows the table with buyer rows and a total, and refetches on a buyer filter", async () => {
  const requests: string[] = [];
  mockServer(true, requests);
  render(<ProcurementPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показатели" }));
  expect(await screen.findByRole("cell", { name: "Петров" })).toBeInTheDocument();
  expect(screen.getByText("Итого")).toBeInTheDocument();
  expect(screen.getAllByText(/50,0% \(1 из 2\)/)).toHaveLength(2);
  expect(requests[0]).toMatch(/from=\d{4}-\d{2}-01&to=\d{4}-\d{2}-\d{2}$/);
  fireEvent.change(screen.getByLabelText("Закупщик"), { target: { value: "Петров" } });
  await waitFor(() => expect(requests.some((path) => path.includes("buyer="))).toBe(true));
});
