import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AutoplanPanel, addWorkingDays } from "./AutoplanPanel";
import type { AutoplanProposal, ProductionTask } from "./api";

const A = "10000000-0000-0000-0000-00000000000a";
const B = "10000000-0000-0000-0000-00000000000b";

function proposal(n: number, operator: string, name: string, setup: string, start: string, finish: string, changeover: number): AutoplanProposal {
  return {
    task_id: `70000000-0000-0000-0000-00000000000${n}`, task_version: 1, task_number: `ПЗ-000${n}`,
    operation_id: `71000000-0000-0000-0000-00000000000${n}`, position: 1, stage: "Опрессовка терминалов", code: "CRIMP-1",
    operator_id: operator, operator_name: name, start, finish, machine: "Станок 1", setup_label: setup,
    changeover_seconds: changeover, setup_seconds: 600, batched: false,
  };
}

const preview = {
  period: { start: "2026-10-06T00:00:00+03:00", finish: "2026-10-13T00:00:00+03:00" },
  proposals: [
    proposal(1, A, "Иванов И.", "Терминал T1", "2026-10-06T09:00:00+03:00", "2026-10-06T10:00:00+03:00", 600),
    proposal(2, A, "Иванов И.", "Терминал T1", "2026-10-06T10:00:00+03:00", "2026-10-06T11:00:00+03:00", 0),
    proposal(3, B, "Петров П.", "Коакс 2", "2026-10-06T09:00:00+03:00", "2026-10-06T09:30:00+03:00", 0),
  ],
  batches: [],
  unplaced: [{ operation_id: "x", task_id: "y", task_number: "ПЗ-0009", position: 2, reason: "Нет допуска у монтажников" }],
  warnings: ["Станок занят"],
  stats: { changeovers: 2, changeovers_naive: 5, saved_seconds: 5400 },
  fingerprint: "abcdef0123456789abcdef",
};

const tasks = [{ id: "70000000-0000-0000-0000-000000000001", number: "ПЗ-0001", item: "591K.3.КБ1", name: "Жгут", status: "Запущено" }] as ProductionTask[];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

function mockApi(applyResponse: () => Response) {
  const calls: Array<{ path: string; body: Record<string, unknown> }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    calls.push({ path, body });
    if (path === "/api/production/autoplan/preview") return json(preview);
    if (path === "/api/production/autoplan/apply") return applyResponse();
    throw new Error(`Unexpected request: ${path}`);
  });
  return calls;
}

async function openPreview() {
  fireEvent.change(screen.getByLabelText("Период с"), { target: { value: "2026-10-06" } });
  fireEvent.change(screen.getByLabelText("Период по"), { target: { value: "2026-10-13" } });
  fireEvent.click(screen.getByRole("button", { name: "Предложить план на 06.10–13.10" }));
  await screen.findByText(/Переналадок 2 вместо 5/);
}

it("skips weekends when adding working days", () => {
  expect(addWorkingDays(new Date(2026, 9, 5), 6).getDate()).toBe(13);
});

it("shows the period in the button and groups the preview by fitter and setup batch", async () => {
  mockApi(() => json({ applied: 3, tasks: [] }));
  render(<AutoplanPanel tasks={tasks} close={() => undefined} onApplied={() => undefined} />);
  await openPreview();
  expect(screen.getByText("Переналадок 2 вместо 5, сэкономлено 1,5 ч")).toBeInTheDocument();
  expect(screen.getByLabelText("Монтажник Иванов И.")).toBeInTheDocument();
  expect(screen.getByLabelText("Монтажник Петров П.")).toBeInTheDocument();
  expect(screen.getByText("Наладка: Терминал T1 — 2 операции из 2 ПЗ")).toBeInTheDocument();
  expect(screen.getByText("Наладка: Коакс 2 — 1 операция из 1 ПЗ")).toBeInTheDocument();
  expect(screen.getByText("переналадка 10 мин")).toBeInTheDocument();
  expect(screen.getAllByText("без переналадки")).toHaveLength(2);
  expect(screen.getByText("06.10 09:00–10:00")).toBeInTheDocument();
  expect(screen.getByText("Станок занят")).toBeInTheDocument();
  expect(screen.getByText(/Нет допуска у монтажников/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Применить выбранное (3)" })).toBeInTheDocument();
});

it("leaves unticked batches out of the apply payload and sends the preview's scope and fingerprint", async () => {
  const calls = mockApi(() => json({ applied: 1, tasks: [] }));
  const applied = vi.fn();
  render(<AutoplanPanel tasks={tasks} close={() => undefined} onApplied={applied} />);
  fireEvent.click(screen.getByLabelText("Пересчитать прежний автоплан"));
  fireEvent.click(screen.getByText(/Задания:/));
  fireEvent.click(screen.getByLabelText(/ПЗ-0001/));
  await openPreview();
  fireEvent.click(screen.getByLabelText("Наладка: Терминал T1"));
  expect(screen.getByRole("button", { name: "Применить выбранное (1)" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Применить выбранное (1)" }));
  await waitFor(() => expect(applied).toHaveBeenCalledWith(1));
  const apply = calls.find((call) => call.path.endsWith("/apply"))!.body;
  expect(apply.items).toEqual([{ task_id: preview.proposals[2].task_id, operation_id: preview.proposals[2].operation_id, operator_id: B }]);
  expect(apply.fingerprint).toBe(preview.fingerprint);
  expect(apply.replan_auto).toBe(true);
  expect(apply.task_ids).toEqual([tasks[0].id]);
  expect(calls[0].body.task_ids).toEqual([tasks[0].id]);
});

it("shows the stale message on 409 and recalculates the preview", async () => {
  const calls = mockApi(() => json({ detail: "Отпечаток плана устарел" }, 409));
  const applied = vi.fn();
  render(<AutoplanPanel tasks={tasks} close={() => undefined} onApplied={applied} />);
  await openPreview();
  fireEvent.click(screen.getByRole("button", { name: "Применить выбранное (3)" }));
  expect(await screen.findByText("План изменился — обновите предложение")).toBeInTheDocument();
  expect(calls.filter((call) => call.path.endsWith("/preview"))).toHaveLength(2);
  expect(applied).not.toHaveBeenCalled();
});

it("shows the server message when apply fails otherwise", async () => {
  mockApi(() => json({ detail: "Нет права на план" }, 403));
  render(<AutoplanPanel tasks={tasks} close={() => undefined} onApplied={() => undefined} />);
  await openPreview();
  fireEvent.click(screen.getByRole("button", { name: "Применить выбранное (3)" }));
  expect(await screen.findByText("Нет права на план")).toBeInTheDocument();
});
