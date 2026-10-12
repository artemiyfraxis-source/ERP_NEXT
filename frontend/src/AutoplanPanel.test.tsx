import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AutoplanPanel } from "./AutoplanPanel";
import type { AutoplanContext, AutoplanPreview, AutoplanProposal } from "./api";

const A = "10000000-0000-0000-0000-00000000000a";
const B = "10000000-0000-0000-0000-00000000000b";
const TASK = "70000000-0000-0000-0000-000000000001";
const CARD = "80000000-0000-0000-0000-000000000001";
const NO_RUN = "80000000-0000-0000-0000-000000000002";

function proposal(n: number, operator: string, name: string, setup: string, start: string, finish: string, changeover: number, isNew = false): AutoplanProposal {
  return {
    task_id: `70000000-0000-0000-0000-00000000000${n}`, task_version: 1, task_number: isNew ? "Новое · 708К КБ2" : `ПЗ-000${n}`,
    operation_id: `71000000-0000-0000-0000-00000000000${n}`, position: 1, stage: "Опрессовка терминалов", code: "CRIMP-1",
    operator_id: operator, operator_name: name, start, finish, machine: "Станок 1", setup_label: setup,
    changeover_seconds: changeover, setup_seconds: 600, batched: false, new: isNew,
  };
}

const preview: AutoplanPreview = {
  period: { start: "2026-10-06T09:00:00+03:00", last_day: "2026-10-13" },
  scope: { tasks: 1, new: 1, fitters: 2 },
  new_tasks: [{ task_id: "70000000-0000-0000-0000-000000000003", techcard_id: CARD, project: "708К", index: "КБ2", name: "Жгут", quantity: 10, deadline: "2026-10-10" }],
  proposals: [
    proposal(1, A, "Иванов И.", "Терминал T1", "2026-10-06T09:00:00+03:00", "2026-10-06T10:00:00+03:00", 600),
    proposal(2, A, "Иванов И.", "Терминал T1", "2026-10-06T10:00:00+03:00", "2026-10-06T11:00:00+03:00", 0),
    proposal(3, B, "Петров П.", "Коакс 2", "2026-10-06T09:00:00+03:00", "2026-10-06T09:30:00+03:00", 0, true),
  ],
  batches: [],
  unplaced: [
    { operation_id: "x", task_id: "y", task_number: "ПЗ-0009", position: 2, reason: "Нет допуска у монтажников" },
    { operation_id: null, task_id: null, techcard_id: NO_RUN, task_number: "708К КБ3", position: null, reason: "Нет тиража — запустите через «+ Запустить задание»" },
  ],
  warnings: ["Станок занят"],
  stats: { changeovers: 2, changeovers_naive: 5, saved_seconds: 5400 },
  fingerprint: "abcdef0123456789abcdef",
};

const context: AutoplanContext = {
  period: { start: "2026-10-06T09:00:00+03:00", last_day: "2026-10-13", auto: true },
  max_days: 56,
  tasks: [{ id: TASK, number: "ПЗ-0001", project: "591K.3", item: "КБ1", name: "Жгут", deadline: "2026-10-09" }],
  new: [
    { techcard_id: CARD, project: "708К", index: "КБ2", name: "Кабель", run: "10", deadline: "2026-10-10", operations: 3, skipped: "" },
    { techcard_id: NO_RUN, project: "708К", index: "КБ3", name: "Кабель", run: "", deadline: null, operations: 3, skipped: "Нет тиража — запустите через «+ Запустить задание»" },
  ],
  projects: [
    { project: "591K.3", deadline: "2026-10-09", tasks: 1, new: 0 },
    { project: "708К", deadline: "2026-10-10", tasks: 0, new: 1 },
  ],
  fitters: [
    { id: A, name: "Иванов И.", site: "ЦАР", free_seconds: 36_000 },
    { id: B, name: "Петров П.", site: "ЦАР", free_seconds: 7_200 },
  ],
};

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
    if (path === "/api/production/autoplan/context") return json(context);
    if (path === "/api/production/autoplan/preview") return json(preview);
    if (path === "/api/production/autoplan/apply") return applyResponse();
    throw new Error(`Unexpected request: ${path}`);
  });
  return calls;
}

const bodies = (calls: Array<{ path: string; body: Record<string, unknown> }>, end: string) => calls.filter((call) => call.path.endsWith(end)).map((call) => call.body);

async function openPreview() {
  fireEvent.click(await screen.findByRole("button", { name: "Предложить план на 06.10–13.10" }));
  await screen.findByText(/Переналадок 2 вместо 5/);
}

it("needs no dates: whole shop by default, the period and the scope in one line", async () => {
  mockApi(() => json({ applied: 3, tasks: [], created: [] }));
  render(<AutoplanPanel close={() => undefined} onApplied={() => undefined} />);
  expect(await screen.findByText("1 задание, 1 новая КБ · 2 монтажника · план 06.10–13.10")).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Весь цех" })).toHaveAttribute("aria-checked", "true");
  expect(screen.getByText("06.10–13.10", { selector: "b" })).toBeInTheDocument();
  expect(screen.queryByLabelText("План до")).not.toBeInTheDocument();
  expect(screen.queryByText("Пересчитать прежний автоплан")).not.toBeInTheDocument();
});

it("groups the preview by fitter and setup batch and marks the work of a new task", async () => {
  mockApi(() => json({ applied: 3, tasks: [], created: [] }));
  render(<AutoplanPanel close={() => undefined} onApplied={() => undefined} />);
  await openPreview();
  expect(screen.getByText("Переналадок 2 вместо 5, сэкономлено 1,5 ч")).toBeInTheDocument();
  expect(screen.getByText("При применении создадутся задания: 708К · КБ2 × 10")).toBeInTheDocument();
  expect(screen.getByLabelText("Монтажник Иванов И.")).toBeInTheDocument();
  expect(screen.getByLabelText("Монтажник Петров П.")).toBeInTheDocument();
  expect(screen.getByText("Наладка: Терминал T1 — 2 операции из 2 ПЗ")).toBeInTheDocument();
  expect(within(screen.getByLabelText("Монтажник Петров П.")).getByText("новое задание")).toBeInTheDocument();
  expect(screen.getByText("переналадка 10 мин")).toBeInTheDocument();
  expect(screen.getByText("06.10 09:00–10:00")).toBeInTheDocument();
  expect(screen.getByText("Станок занят")).toBeInTheDocument();
  expect(screen.getByText(/Нет допуска у монтажников/)).toBeInTheDocument();
  expect(screen.getByText("708К КБ3")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Применить выбранное (3)" })).toBeInTheDocument();
});

it("leaves unticked batches out of the apply payload and reports the created tasks", async () => {
  const calls = mockApi(() => json({ applied: 1, tasks: [], created: [{ id: "n", number: "ПЗ-0004" }] }));
  const applied = vi.fn();
  render(<AutoplanPanel close={() => undefined} onApplied={applied} />);
  await openPreview();
  fireEvent.click(screen.getByLabelText("Наладка: Терминал T1"));
  fireEvent.click(screen.getByRole("button", { name: "Применить выбранное (1)" }));
  await waitFor(() => expect(applied).toHaveBeenCalledWith(1, 1));
  const apply = bodies(calls, "/apply")[0];
  expect(apply.items).toEqual([{ task_id: preview.proposals[2].task_id, operation_id: preview.proposals[2].operation_id, operator_id: B }]);
  expect(apply.fingerprint).toBe(preview.fingerprint);
  expect(apply.mode).toBe("shop");
  expect(apply.exclude).toBeUndefined();
  expect(bodies(calls, "/preview")[0]).toEqual({ mode: "shop" });
});

it("excludes tasks and new КБ from the whole-shop plan in a collapsed list", async () => {
  const calls = mockApi(() => json({ applied: 0, tasks: [], created: [] }));
  render(<AutoplanPanel close={() => undefined} onApplied={() => undefined} />);
  fireEvent.click(await screen.findByText("Исключить задания"));
  expect(screen.getByText("в плане 2 из 2")).toBeInTheDocument();
  expect(screen.getByText("Нет тиража — запустите через «+ Запустить задание»")).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("ПЗ-0001 · 591K.3 · КБ1"));
  fireEvent.click(screen.getByLabelText("708К · КБ2 · новое задание"));
  expect(screen.getByText("в плане 0 из 2")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Предложить план/ })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Выбрать все" }));
  fireEvent.click(screen.getByLabelText("708К · КБ2 · новое задание"));
  expect(screen.getByText("1 задание · 2 монтажника · план 06.10–13.10")).toBeInTheDocument();
  await openPreview();
  expect(bodies(calls, "/preview")[0]).toEqual({ mode: "shop", exclude: [CARD] });
});

it("plans one project on the chosen people", async () => {
  const calls = mockApi(() => json({ applied: 3, tasks: [], created: [] }));
  render(<AutoplanPanel close={() => undefined} onApplied={() => undefined} />);
  await screen.findByText(/1 задание, 1 новая КБ/);
  fireEvent.click(screen.getByRole("radio", { name: "По проекту" }));
  const select = await screen.findByLabelText("Проект");
  await waitFor(() => expect(select).toHaveValue("591K.3"));
  expect(screen.getByRole("option", { name: "591K.3 · ДЛ 09.10 · 1 задание" })).toBeInTheDocument();
  expect(screen.getByText("свободно 10 ч")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Предложить план/ })).toBeDisabled();
  fireEvent.change(select, { target: { value: "708К" } });
  fireEvent.click(screen.getByLabelText("Петров П."));
  expect(screen.getByText(/выбрано 1 из 2/)).toBeInTheDocument();
  await openPreview();
  expect(bodies(calls, "/context").at(-1)).toEqual({ mode: "project", project: "708К", date_to: null });
  expect(bodies(calls, "/preview")[0]).toEqual({ mode: "project", project: "708К", people: [B] });
  fireEvent.click(screen.getByRole("button", { name: "Применить выбранное (3)" }));
  await waitFor(() => expect(bodies(calls, "/apply")).toHaveLength(1));
  expect(bodies(calls, "/apply")[0]).toMatchObject({ mode: "project", project: "708К", people: [B] });
});

it("takes an optional end date behind «изменить» and goes back to the work horizon", async () => {
  const calls = mockApi(() => json({ applied: 0, tasks: [], created: [] }));
  render(<AutoplanPanel close={() => undefined} onApplied={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: "изменить" }));
  fireEvent.change(screen.getByLabelText("План до"), { target: { value: "2026-10-08" } });
  await waitFor(() => expect(bodies(calls, "/context").at(-1)).toEqual({ mode: "shop", date_to: "2026-10-08" }));
  await openPreview();
  expect(bodies(calls, "/preview")[0]).toEqual({ mode: "shop", date_to: "2026-10-08" });
  fireEvent.click(screen.getByRole("button", { name: "по работе" }));
  await waitFor(() => expect(bodies(calls, "/context").at(-1)).toEqual({ mode: "shop", date_to: null }));
  expect(screen.queryByLabelText("План до")).not.toBeInTheDocument();
});

it("is a centred modal dialog: Escape and Отмена close it", async () => {
  mockApi(() => json({ applied: 0, tasks: [], created: [] }));
  const close = vi.fn();
  render(<AutoplanPanel close={close} onApplied={() => undefined} />);
  const dialog = screen.getByRole("dialog", { name: "Автоплан" });
  expect(dialog).toHaveAttribute("aria-modal", "true");
  await screen.findByText(/1 задание, 1 новая КБ/);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(close).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(close).toHaveBeenCalledTimes(2);
  fireEvent.mouseDown(dialog.parentElement!);
  expect(close).toHaveBeenCalledTimes(3);
});

it("shows the stale message on 409 and recalculates the preview", async () => {
  const calls = mockApi(() => json({ detail: "Данные изменились" }, 409));
  const applied = vi.fn();
  render(<AutoplanPanel close={() => undefined} onApplied={applied} />);
  await openPreview();
  fireEvent.click(screen.getByRole("button", { name: "Применить выбранное (3)" }));
  expect(await screen.findByText("План изменился — обновите предложение")).toBeInTheDocument();
  expect(bodies(calls, "/preview")).toHaveLength(2);
  expect(applied).not.toHaveBeenCalled();
});

it("shows the server message when apply fails otherwise", async () => {
  mockApi(() => json({ detail: "Нет права на план" }, 403));
  render(<AutoplanPanel close={() => undefined} onApplied={() => undefined} />);
  await openPreview();
  fireEvent.click(screen.getByRole("button", { name: "Применить выбранное (3)" }));
  expect(await screen.findByText("Нет права на план")).toBeInTheDocument();
});

it("orders projects by the production plan deadline when the plan has one", async () => {
  mockApi(() => json({ applied: 0, tasks: [], created: [] }));
  render(<AutoplanPanel close={() => undefined} onApplied={() => undefined} projectDeadlines={{ "708к": { deadline: "2026-10-07", days_left: 1 } }} />);
  await screen.findByText(/1 задание, 1 новая КБ/);
  fireEvent.click(screen.getByRole("radio", { name: "По проекту" }));
  await waitFor(() => expect(screen.getByLabelText("Проект")).toHaveValue("708К"));
  expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
    "708К · ДЛ 07.10 · 1 новая КБ", "591K.3 · ДЛ 09.10 · 1 задание",
  ]);
});
