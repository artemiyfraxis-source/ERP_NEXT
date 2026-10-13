import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PlanBaseline, delayText, readBaseline, type BaselineResult, type BaselineRow } from "./PlanBaseline";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const row = (over: Partial<BaselineRow>): BaselineRow => ({
  task_id: "t1", task_number: "ПЗ-0001", project: "591К.3", item: "591К.3.КБ1", quantity: 10,
  planned_start: "2026-10-13T09:00:00+03:00", planned_finish: "2026-10-14T17:30:00+03:00", status: "Запущено",
  started_at: null, finished_at: null, done_quantity: "0", start_delay_minutes: null, finish_delay_minutes: null,
  late_start: false, late_finish: false, shortfall: "0", short: false, started: false, finished: false, ...over,
});

const fixed: BaselineResult = {
  tolerance_minutes: 60,
  baseline: { id: "b2", number: 2, created_at: "2026-10-13T08:30:00+03:00", created_by: "Мастер", skipped: 0, tasks: 3, outside: 1 },
  rows: [
    row({ task_id: "t1", task_number: "ПЗ-0001", started: true, started_at: "2026-10-13T12:00:00+03:00", late_start: true, start_delay_minutes: 180 }),
    row({ task_id: "t2", task_number: "ПЗ-0002", status: "Выполнено", started: true, finished: true, started_at: "2026-10-13T09:00:00+03:00",
      finished_at: "2026-10-16T10:00:00+03:00", late_finish: true, finish_delay_minutes: 2 * 24 * 60 + 4 * 60 + 30, done_quantity: "8", short: true, shortfall: "2" }),
    row({ task_id: "t3", task_number: "ПЗ-0003" }),
  ],
};

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });

it("words a delay in minutes, hours and days", () => {
  expect(delayText(null)).toBe("");
  expect(delayText(0)).toBe("");
  expect(delayText(40)).toBe("на 40 мин");
  expect(delayText(180)).toBe("на 3 ч");
  expect(delayText(2 * 24 * 60)).toBe("на 2 дн");
  expect(delayText(2 * 24 * 60 + 4 * 60 + 30)).toBe("на 2 дн 4 ч");
});

it("reads a foreign or empty answer as «not fixed»", () => {
  expect(readBaseline({ rows: [{ project: "708К.4" }] })).toEqual({ tolerance_minutes: 60, baseline: null, rows: [], summary: undefined });
  expect(readBaseline(null).baseline).toBeNull();
  expect(readBaseline(fixed).rows).toHaveLength(3);
});

it("shows the snapshot, the totals and only deviating tasks by default", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(fixed));
  render(<PlanBaseline canEdit />);

  expect(await screen.findByText("ПЗ-0001")).toBeInTheDocument();
  expect(screen.getByText(/Базовый план №2 от/)).toBeInTheDocument();
  expect(screen.getByText("вне базового плана: 1")).toBeInTheDocument();
  expect(screen.getByText("Начато позже: 1")).toBeInTheDocument();
  expect(screen.getByText("Закончено позже: 1")).toBeInTheDocument();
  expect(screen.getByText("Недодано: 1")).toBeInTheDocument();
  expect(screen.getByText("По плану: 1")).toBeInTheDocument();
  expect(screen.getByText("Начато позже на 3 ч")).toBeInTheDocument();
  expect(screen.getByText("Закончено позже на 2 дн 4 ч")).toBeInTheDocument();
  expect(screen.getByText("Недодано 2 шт")).toBeInTheDocument();
  expect(screen.getByText("8 / 10")).toBeInTheDocument();
  // По плану — под чипом «Все», не в отклонениях.
  expect(screen.queryByText("ПЗ-0003")).toBeNull();
});

it("fixes a new baseline with one click and shows it", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) =>
    init?.method === "POST" ? json(fixed, 201) : json({ tolerance_minutes: 60, baseline: null, rows: [] }));
  render(<PlanBaseline canEdit />);

  expect(await screen.findByText(/Базовый план не зафиксирован/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Зафиксировать базовый план" }));

  await waitFor(() => expect(screen.getByText(/Базовый план №2 от/)).toBeInTheDocument());
  expect(fetch).toHaveBeenCalledWith("/api/production/plan/baseline", expect.objectContaining({ method: "POST" }));
});

it("shows the refusal of an empty fix and hides the button from a viewer", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => init?.method === "POST"
    ? json({ detail: "Фиксировать нечего: у запущенных заданий нет плановых сроков — сначала назначьте операции" }, 422)
    : json({ tolerance_minutes: 60, baseline: null, rows: [] }));
  const { unmount } = render(<PlanBaseline canEdit />);
  fireEvent.click(await screen.findByRole("button", { name: "Зафиксировать базовый план" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Фиксировать нечего");
  unmount();

  render(<PlanBaseline canEdit={false} />);
  expect(await screen.findByText(/его фиксирует мастер планирования/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Зафиксировать базовый план" })).toBeNull();
});
