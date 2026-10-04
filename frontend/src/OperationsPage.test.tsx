import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OperationsPage } from "./OperationsPage";

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.restoreAllMocks());

it("shows audit records and outbox failures", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/operations/audit") return json({ events: [{
      id: "20000000-0000-0000-0000-000000000001",
      aggregate_id: "30000000-0000-0000-0000-000000000001",
      event_type: "shipping.closed",
      actor: "Администратор",
      department: "ERP",
      occurred_at: "2026-09-22T12:00:00+03:00",
      details: { number: "ОТГ-1" },
    }] });
    if (path === "/api/operations/outbox") return json({
      total: 3,
      published: 1,
      pending: 1,
      dead: 1,
      failures: [{ id: "x", topic: "warehouse.received", attempts: 5, error: "invalid payload", dead: true }],
    });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<OperationsPage />);

  expect(await screen.findByText("Отправка закрыта")).toBeInTheDocument();
  expect(screen.getByText("shipping.closed")).toBeInTheDocument();
  expect(screen.getByText("Приход на склад")).toBeInTheDocument();
  expect(screen.getByText("invalid payload")).toBeInTheDocument();
  expect(screen.getByText("№: ОТГ-1")).toBeInTheDocument();
  expect(screen.queryByText(/30000000-0000/)).not.toBeInTheDocument();
});
