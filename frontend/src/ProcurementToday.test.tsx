import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProcurementPage } from "./ProcurementPage";
import { resetStageOptions } from "./StageOptions";

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

const OPTION = { key: "procurement.today", block: "Закупки", title: "«Сегодня»", before: "", after: "", enabled: true };

function mockServer(enabled: boolean, sections: unknown[], projects: Record<string, unknown[]> = {}) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/stage-options") return json({ available: true, options: [{ ...OPTION, enabled }] });
    if (path === "/api/procurement/today") return json({ sections });
    if (path.startsWith("/api/procurement/today/project?project=")) {
      return json({ lines: projects[decodeURIComponent(path.split("=")[1])] ?? [] });
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

it("has no «Сегодня» tab while the option is off", async () => {
  mockServer(false, []);
  render(<ProcurementPage />);
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith("/api/stage-options", expect.anything()));
  expect(screen.getByRole("button", { name: "Очереди" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Сегодня" })).not.toBeInTheDocument();
});

it("shows «Сегодня» first as a board of project cards; one project sits in several fields and opens its positions", async () => {
  const line = (id: string, status: string, field: string | null) => ({
    id, name: `Позиция ${id}`, article: null, supplier: "Рос", order_number: "ЗК-1", status, expected: "2026-10-01", quantity: "10", unit: "шт", field,
  });
  mockServer(true, [
    {
      key: "overdue", title: "Просрочена поставка", count: 2,
      items: [],
      projects: [{ project: "591К.3", count: 2, days: 4, date: "2026-10-01", suppliers: ["Рос"] }],
    },
    {
      key: "to_pay", title: "Ждёт оплаты", count: 1,
      items: [],
      projects: [{ project: "591К.3", count: 1, days: 2, date: "2026-10-03", suppliers: ["Рос"] }],
    },
  ], { "591К.3": [line("1", "В пути", "Просрочена поставка"), line("2", "В пути", "Просрочена поставка"), line("3", "В оплату", "Ждёт оплаты"), line("4", "В работе", null)] });
  render(<ProcurementPage />);
  const tab = await screen.findByRole("button", { name: "Сегодня" });
  expect(tab.parentElement?.firstElementChild).toBe(tab);
  const cards = await screen.findAllByRole("button", { name: /591К\.3/ });
  expect(cards).toHaveLength(2);
  expect(screen.getByText("просрочка 4 дн.")).toBeInTheDocument();
  fireEvent.click(cards[0]);
  expect(await screen.findByText("Позиция 1")).toBeInTheDocument();
  expect(screen.queryByText("Позиция 3")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Все позиции проекта/ }));
  expect(screen.getByText("Позиция 3")).toBeInTheDocument();
  expect(screen.getByText("Позиция 4")).toBeInTheDocument();
});

it("says everything is done when no section has rows", async () => {
  mockServer(true, []);
  render(<ProcurementPage />);
  expect(await screen.findByText("На сегодня всё сделано")).toBeInTheDocument();
});
