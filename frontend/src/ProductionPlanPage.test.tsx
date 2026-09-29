import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProductionPlanPage } from "./ProductionPlanPage";
import type { Principal, ProductionPlanResult } from "./api";

const principal: Principal = {
  user: { id: "1", email: "master@example.test", name: "Мастер", department: "Производство" },
  roles: ["master"],
  permissions: { production: "edit" },
};

const plan: ProductionPlanResult = {
  blockers: ["ХОЛД", "СТОП"],
  stages: ["Новый", "ТК готова", "КБ набрана", "В работе", "КБ на складе", "Отгружено"],
  review_values: ["не проверена", "проверена", "с погрешностями"],
  rows: [{
    project: "708К.4", customer: "Заказчик", opened_on: "2026-09-01",
    deadline: "2026-09-30", days_left: 8, stage: "В работе", terminal: false, stage_manual: false,
    kits: [{ item_index: "КБ1", status: "В работе", manual: false, tc: true, kit: false, review: { value: "не проверена", text: "", who: "генератор", occurred_at: "2026-09-22T08:00:00+03:00" } }],
    supply: { total: 5, stock: 2, transit: 1, buying: 1, request: 1, customer_supplied: 1, customer_supplied_stock: 1, eta: "28.09.2026" },
    plan: { tasks: 1, assigned: 2, total: 3, start: "2026-09-22T09:00:00+03:00", finish: "2026-09-23T12:00:00+03:00" },
    blocker: { type: "СТОП", text: "Ждём разъёмы", since: "2026-09-22T09:00:00+03:00", who: "Мастер" },
    last: { occurred_at: "2026-09-22T09:00:00+03:00", who: "Мастер", text: "Ждём разъёмы" },
    events: [{ id: "e1", project: "708К.4", occurred_at: "2026-09-22T09:00:00+03:00", who: "Мастер", kind: "Блокер", blocker: "СТОП", text: "Ждём разъёмы", deadline: null, item_index: "", value: "" }],
  }],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

async function openProject(project: string) {
  fireEvent.click((await screen.findByText(project, { selector: "td strong" })).closest("tr")!);
}

it("shows the native project stage, supply, task plan and append-only journal", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => String(input) === "/api/production/calendar" ? json({ holidays: [], absences: [], shift: {} }) : json(plan));
  render(<ProductionPlanPage principal={principal} />);

  expect(await screen.findByRole("heading", { name: "Производственный план" })).toBeInTheDocument();
  expect(screen.getByText("КБ 1: ТК 1 · набрано 1 · на складе 0")).toBeInTheDocument();
  expect(screen.getByText("на складе 2/5 · в пути 1 · в закупке 1 · заявка 1 · ждём до 28.09 · давальч. 1/1")).toBeInTheDocument();
  expect(screen.getByText("22.09 – 23.09 · 1 зад. · назначено 2/3")).toBeInTheDocument();
  await openProject("708К.4");
  expect(screen.getAllByText("Ждём разъёмы").length).toBeGreaterThan(0);
  expect(screen.getByText("Всего 5: склад 2, в пути 1, в закупке 1, в заявках 1.")).toBeInTheDocument();
  expect(screen.getByText("1 заданий · назначено 2 из 3 этапов.")).toBeInTheDocument();
  expect(screen.getByText("ТК выдана · не набрано")).toBeInTheDocument();
});

it("sets the stage of a project without KBs through a journal mark with an empty KB", async () => {
  const bare = { ...plan.rows[0], project: "70К.26.13", stage: "Новый", kits: [], blocker: null, events: [] };
  let payload: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input) === "/api/production/plan/events") {
      payload = JSON.parse(String(init?.body));
      return json({ event: plan.rows[0].events[0] }, 201);
    }
    return json({ ...plan, rows: [bare] });
  });
  render(<ProductionPlanPage principal={principal} />);
  await openProject("70К.26.13");

  expect(screen.queryByText(/набрано/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Этап проекта"), { target: { value: "Отгружено" } });
  await waitFor(() => expect(payload).toMatchObject({
    project: "70К.26.13", kind: "stage", item_index: "", value: "Отгружено",
  }));
});

it("adds a deadline through the native project journal", async () => {
  let payload: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input) === "/api/production/plan/events") {
      payload = JSON.parse(String(init?.body));
      return json({ event: plan.rows[0].events[0] }, 201);
    }
    return json(plan);
  });
  render(<ProductionPlanPage principal={principal} />);
  await openProject("708К.4");
  fireEvent.change(screen.getByLabelText("Запись"), { target: { value: "deadline" } });
  fireEvent.change(screen.getByLabelText("Дата"), { target: { value: "2026-10-03" } });
  fireEvent.click(screen.getByRole("button", { name: "Записать" }));

  await waitFor(() => expect(payload).toEqual({
    project: "708К.4", kind: "deadline", blocker: "ХОЛД", text: "", deadline: "2026-10-03",
  }));
});

it("records manual kit stage and techcard review", async () => {
  const payloads: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input) === "/api/production/plan/events") {
      payloads.push(JSON.parse(String(init?.body)));
      return json({ event: plan.rows[0].events[0] }, 201);
    }
    return json(plan);
  });
  render(<ProductionPlanPage principal={principal} />);
  await openProject("708К.4");

  fireEvent.change(screen.getByLabelText("Этап КБ1"), { target: { value: "Отгружено" } });
  await waitFor(() => expect(payloads[0]).toMatchObject({
    project: "708К.4", kind: "stage", item_index: "КБ1", value: "Отгружено",
  }));
  fireEvent.change(screen.getByLabelText("Проверка ТК КБ1"), { target: { value: "проверена" } });
  await waitFor(() => expect(payloads[1]).toMatchObject({
    project: "708К.4", kind: "tcReview", item_index: "КБ1", value: "проверена",
  }));
});

function row(overrides: Partial<ProductionPlanResult["rows"][number]>) {
  return { ...plan.rows[0], blocker: null, events: [], last: null, ...overrides };
}

const board: ProductionPlanResult = {
  ...plan,
  rows: [
    row({ project: "701К.1", customer: "Альфа", days_left: null, deadline: null }),
    row({ project: "702К.1", customer: "Бета", days_left: -2, deadline: "2026-09-20" }),
    row({ project: "703К.1", customer: "Гамма", days_left: 5, deadline: "2026-09-27", blocker: { type: "ХОЛД", text: "оплата", since: "2026-09-21T09:00:00+03:00", who: "Мастер" } }),
    row({ project: "704К.1", customer: "Дельта", days_left: 1, deadline: "2026-09-23", plan: null, stage: "ТК готова" }),
    row({ project: "705К.1", customer: "Эпсилон", terminal: true, stage: "Закрыт" }),
    row({ project: "706К.1", customer: "Зета", stage: "Отгружено" }),
  ],
};

function projects() {
  return Array.from(document.querySelectorAll(".plan-table tbody tr")).map((item) => item.querySelector("strong")?.textContent).filter(Boolean);
}

it("filters like PLAN_FILTERS_ with counters, searches and sorts blocked then by deadline", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => String(input) === "/api/production/calendar"
    ? json({ holidays: ["2026-09-24"], absences: [], shift: {} }) : json(board));
  render(<ProductionPlanPage principal={principal} />);
  await screen.findByText("Альфа");

  // «В работе»: not terminal, not shipped; blocker first, burning deadline next, no deadline last.
  expect(projects()).toEqual(["703К.1", "702К.1", "704К.1", "701К.1"]);
  const group = screen.getByRole("group", { name: "Фильтр проектов" });
  expect(Array.from(group.querySelectorAll("button")).map((button) => button.textContent)).toEqual([
    "В работе4", "С блокером1", "Просрочено1", "Без плана цеха1", "Все6",
  ]);
  fireEvent.click(screen.getByRole("button", { name: "Просрочено1" }));
  expect(projects()).toEqual(["702К.1"]);
  fireEvent.click(screen.getByRole("button", { name: "Без плана цеха1" }));
  expect(projects()).toEqual(["704К.1"]);
  fireEvent.click(screen.getByRole("button", { name: "Все6" }));
  fireEvent.change(screen.getByLabelText("Поиск проекта"), { target: { value: "эпс" } });
  expect(projects()).toEqual(["705К.1"]);
  fireEvent.change(screen.getByLabelText("Поиск проекта"), { target: { value: "706к" } });
  expect(projects()).toEqual(["706К.1"]);
  fireEvent.change(screen.getByLabelText("Поиск проекта"), { target: { value: "нет такого" } });
  expect(screen.getByText("Под этот фильтр проектов нет.")).toBeInTheDocument();
});

it("shows people load under the projects: operations per operator, late, done, absence and unassigned", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 8, 22, 10));
  try {
    const operation = (id: string, position: number, operators: string[], start: string | null, finish: string | null, state = "не начата") => ({
      id, position, code: "", stage: `Этап ${position}`, description: "", seconds_per_unit: 60, setup_seconds: 0,
      norm_seconds: 7200, operator_ids: operators, planned_start: start, planned_finish: finish, state,
    });
    const tasks = [{
      id: "t1", number: "ПЗ-0001", version: 1, created_at: "2026-09-21T09:00:00", project: "708К.4", item: "КБ1", name: "Жгут",
      quantity: 10, fulfillment_request_id: null, deadline: "2026-09-22", status: "В работе", launched_by: "Мастер",
      planned_start: null, planned_finish: null, assigned: 2,
      operations: [
        operation("o1", 1, ["a"], "2026-09-22T09:00:00", "2026-09-22T12:00:00", "выполнена"),
        operation("o2", 2, ["a"], "2026-09-23T09:00:00", "2026-09-23T15:00:00"),
        operation("o3", 3, [], null, null),
      ],
    }];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/production/calendar") return json({ holidays: [], absences: [{ operator_id: "b", from_date: "2026-09-24", to_date: "2026-09-25", kind: "Отпуск" }], shift: {} });
      if (path === "/api/production/tasks") return json({ tasks, next_cursor: null });
      if (path === "/api/production/operators") return json({ operators: [{ id: "b", name: "Борисов Б.", site: "" }, { id: "a", name: "Алексеева А.", site: "" }] });
      return json(plan);
    });
    const { container } = render(<ProductionPlanPage principal={principal} />);
    const load = await screen.findByRole("region", { name: "Загрузка по людям" });

    await waitFor(() => expect(Array.from(load.querySelectorAll('[data-testid="gantt-row"] .gantt-label strong')).map((item) => item.textContent)).toEqual(["Алексеева А.", "Борисов Б."]));
    const bars = Array.from(load.querySelectorAll('[data-testid="gantt-row"] .gantt-bar'));
    expect(bars.map((bar) => [bar.textContent, bar.className])).toEqual([
      ["ПЗ-0001 · 708К.4КБ1", "gantt-bar done"],
      ["ПЗ-0001 · 708К.4КБ1", "gantt-bar late"],
      ["Отпуск", "gantt-bar absent"],
    ]);
    expect(screen.getByText("2 опер. · 4,0 ч по норме")).toBeInTheDocument();
    expect(screen.getByText("свободен")).toBeInTheDocument();
    expect(screen.getByText("Без исполнителя: 1 опер. в 1 зад. — назначить в «Ресурсном планировании»")).toBeInTheDocument();
    expect(container.querySelector(".plan-projects")).not.toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

it("opens the project card straight from the plan row, not a panel under the list", async () => {
  const opened: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(plan));
  render(<ProductionPlanPage principal={principal} openProjectCard={(project) => opened.push(project)} />);
  await openProject("708К.4");
  expect(opened).toEqual(["708К.4"]);
  expect(screen.queryByRole("dialog")).toBeNull();
});
