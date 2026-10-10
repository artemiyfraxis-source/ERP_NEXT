import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OperationsPage, ago } from "./OperationsPage";

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
  expect(screen.queryByRole("button", { name: "Повторить" })).not.toBeInTheDocument(); // not an administrator
});

it("shows the worker heartbeats, a stopped group, the old queue and retries a stopped job", async () => {
  const calls: string[] = [];
  const now = Date.now();
  const iso = (secondsAgo: number) => new Date(now - secondsAgo * 1000).toISOString();
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    calls.push(`${init?.method ?? "GET"} ${path}`);
    if (path === "/api/operations/audit") return json({ events: [] });
    if (path === "/api/operations/outbox/m-1/retry") return json({ id: "m-1", requeued: true });
    if (path === "/api/operations/outbox") return json({
      total: 2, published: 0, pending: 1, dead: 1, ok: false, can_retry: true, tls_exceptions: ["old-shop.example"],
      failures: [{ id: "m-1", topic: "warehouse.received", attempts: 5, error: "invalid payload", dead: true }],
      groups: [
        { key: "dispatch", label: "Очередь событий модулей", status: "ok", success_at: iso(3), failure_at: null, error: null, duration_ms: 41, failures: 0, max_silence_seconds: 300 },
        { key: "autofill", label: "Автозаполнение строк закупки", status: "red", success_at: null, failure_at: iso(2), error: "autofill: boom", duration_ms: 15040, failures: 7, max_silence_seconds: 900 },
      ],
      topics: [{ topic: "receiving.expected", count: 1, oldest_at: iso(1320), age_seconds: 1320, status: "red" }],
      oldest_pending_seconds: 1320,
    });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<OperationsPage />);

  expect(await screen.findByText("Фоновые процессы")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("Не работает: Автозаполнение строк закупки. «Ожидается поставка» ждёт 22 мин");
  expect(screen.getByText("Стоит · 7 подряд")).toHaveClass("tone-danger");
  expect(screen.getByText("Работает")).toHaveClass("tone-ok");
  expect(screen.getByText("ни разу")).toBeInTheDocument();
  expect(screen.getByText("15,0 с")).toBeInTheDocument();
  expect(screen.getByText("22 мин")).toHaveClass("tone-danger");
  expect(screen.getByText(/Сайты поставщиков без проверки сертификата: old-shop.example/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
  await waitFor(() => expect(calls).toContain("POST /api/operations/outbox/m-1/retry"));
  await waitFor(() => expect(calls.filter((call) => call === "GET /api/operations/outbox")).toHaveLength(2));
});

it("formats how long ago", () => {
  expect([ago(3), ago(240), ago(7500)]).toEqual(["3 с", "4 мин", "2 ч 05 мин"]);
});
