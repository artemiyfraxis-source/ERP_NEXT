import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

it("shows the request follow-up fields before «В работе»; a drill-down row carries the hint, opens the request and its history", async () => {
  const options = [
    { ...OPTION, enabled: true },
    { ...OPTION, key: "procurement.rfq", enabled: true },
    { ...OPTION, key: "procurement.history", enabled: true },
  ];
  const row = {
    id: "9", name: "Разъём", article: null, supplier: null, order_number: null, status: "В работе", expected: null, quantity: "2", unit: "шт",
    field: "Нет ответа на запрос КП", fields: ["Нет ответа на запрос КП", "В работе: запросить, выбрать поставщика"],
    rfq: { id: "rfq-1", number: "КП-2610-001", hint: "КП-2610-001: нет ответа на запрос — ООО Молчит" },
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/stage-options") return json({ available: true, options });
    if (path === "/api/procurement/today") {
      return json({ sections: [
        { key: "rfq_no_answer", title: "Нет ответа на запрос КП", count: 1, items: [], projects: [{ project: "Без проекта", count: 1, days: 4, date: "2026-10-01", suppliers: [] }] },
        { key: "rfq_to_decide", title: "Выбрать поставщика", count: 2, items: [], projects: [{ project: "591К.3", count: 2, days: 1, date: "2026-10-04", suppliers: [] }] },
        { key: "in_work", title: "В работе: запросить, выбрать поставщика", count: 1, items: [], projects: [{ project: "Без проекта", count: 1, days: 6, date: "2026-09-29", suppliers: [] }] },
      ] });
    }
    if (path.startsWith("/api/procurement/today/project?project=")) return json({ lines: [row] });
    if (path === "/api/procurement/9/history") {
      return json({ line: { id: "9", legacy_id: "SM-1", name: "Разъём", status: "В работе" }, events: [
        { at: "2026-09-14T09:00:00", time: true, title: "Заявка SM-1 создана", details: "Разъём", who: "Иванов", source: "заявка" },
        { at: null, time: false, title: "Ожидаемая дата поставки", details: "03.10.2026; дата не хранится", who: null, source: "закупка" },
      ] });
    }
    return json({ lines: [], buyers: [], stages: [], next_cursor: null });
  });
  render(<ProcurementPage />);
  const board = await screen.findByRole("list", { name: "Доска «Сегодня»" });
  const titles = Array.from(board.querySelectorAll(":scope > [role=listitem] h2")).map((node) => node.textContent);
  expect(titles).toEqual(["Нет ответа на запрос КП", "Выбрать поставщика", "В работе: запросить, выбрать поставщика"]);

  fireEvent.click(screen.getAllByRole("button", { name: /Без проекта/ })[0]);
  expect(await screen.findByText(/нет ответа на запрос — ООО Молчит/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Открыть запрос КП-2610-001" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "История Разъём" }));
  const panel = await screen.findByRole("dialog", { name: "История Разъём" });
  expect(await within(panel).findByText("Заявка SM-1 создана")).toBeInTheDocument();
  expect(within(panel).getByText("14.09.2026 09:00")).toBeInTheDocument();
  expect(within(panel).getByText("03.10.2026; дата не хранится")).toBeInTheDocument();
  expect(within(panel).getByText("без даты")).toBeInTheDocument();
});
