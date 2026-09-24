import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { NotificationsPage } from "./NotificationsPage";
import { OperationsPage } from "./OperationsPage";

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.restoreAllMocks());

it("shows only the current user's inbox and marks a notification read", async () => {
  let payload: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/notifications/read") {
      payload = JSON.parse(String(init?.body));
      return json({ unread: 0 });
    }
    if (path === "/api/notifications") return json({
      unread: 1,
      notifications: [{
        id: "10000000-0000-0000-0000-000000000001",
        created_at: "2026-09-22T12:00:00+03:00",
        type: "status",
        reference: "SM-1",
        title: "Статус изменён",
        body: "Заявка передана в закупку",
        read: false,
      }],
    });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<NotificationsPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Отметить прочитанным" }));

  await waitFor(() => expect(payload).toEqual({ ids: ["10000000-0000-0000-0000-000000000001"] }));
  expect(screen.queryByRole("button", { name: "Отметить прочитанным" })).not.toBeInTheDocument();
});

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

  expect(await screen.findByText("shipping.closed")).toBeInTheDocument();
  expect(screen.getByText("warehouse.received")).toBeInTheDocument();
  expect(screen.getByText("invalid payload")).toBeInTheDocument();
  expect(screen.getByText("ОТГ-1", { exact: false })).toBeInTheDocument();
});
