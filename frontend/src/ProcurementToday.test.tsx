import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProcurementPage } from "./ProcurementPage";
import { resetStageOptions } from "./StageOptions";

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

const OPTION = { key: "procurement.today", block: "Закупки", title: "«Сегодня»", before: "", after: "", enabled: true };

function mockServer(enabled: boolean, sections: unknown[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/stage-options") return json({ available: true, options: [{ ...OPTION, enabled }] });
    if (path === "/api/procurement/today") return json({ sections });
    if (path === "/api/procurement/meta") return json({ units: ["шт"], statuses: [] });
    return json({ lines: [], buyers: [], stages: [], next_cursor: null });
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  resetStageOptions();
});

it("has no «Сегодня» tab while the option is off", async () => {
  mockServer(false, []);
  render(<ProcurementPage />);
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith("/api/stage-options", expect.anything()));
  expect(screen.getByRole("button", { name: "Очереди" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Сегодня" })).not.toBeInTheDocument();
});

it("shows «Сегодня» first and opens it by default when on, hiding empty sections", async () => {
  mockServer(true, [
    {
      key: "overdue", title: "Просрочена поставка", count: 1,
      items: [{ id: "1", name: "Разъём", article: "D-1", supplier: "Рос", order_number: "ЗК-1", date: "2026-10-01", days: 4, hint: "Позвоните поставщику" }],
    },
  ]);
  render(<ProcurementPage />);
  const tab = await screen.findByRole("button", { name: "Сегодня" });
  expect(tab.parentElement?.firstElementChild).toBe(tab);
  expect(await screen.findByText("Просрочена поставка")).toBeInTheDocument();
  expect(screen.getByText("Позвоните поставщику")).toBeInTheDocument();
  expect(screen.queryByText("Ждём счёт")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Очереди" }));
  await waitFor(() => expect(screen.queryByText("Позвоните поставщику")).not.toBeInTheDocument());
});

it("says everything is done when no section has rows", async () => {
  mockServer(true, []);
  render(<ProcurementPage />);
  expect(await screen.findByText("На сегодня всё сделано")).toBeInTheDocument();
});
