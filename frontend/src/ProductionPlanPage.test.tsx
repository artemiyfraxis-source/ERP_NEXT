import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProductionPlanPage } from "./ProductionPlanPage";
import { groupSheetRows, markText, projectColor, sheetDays } from "./PlanLoadSheet";
import type { Principal, ProductionPlanResult } from "./api";

const principal: Principal = {
  user: { id: "1", email: "master@example.test", name: "Мастер", department: "Производство" },
  roles: ["master"],
  permissions: { production: "edit" },
};

const cell = (value: string, manual = false, auto = value) => ({ value, auto, manual });
const sheet = {
  comment: cell("ждём разъёмы"), status: cell("СТОП, В РАБОТЕ"), ready: cell("40"), in_plan: cell("да"),
  customer_supplied: cell("Ч/Т"), kit_checked: cell("нет"), kit_present: cell("нет"), zkp: cell("да"),
  kit_arrival: cell("2026-09-28"), tc_ready: cell("да"), test_sb: cell("да", true, "нет"), kit_picked: cell("нет"),
};

const plan: ProductionPlanResult = {
  today: "2026-09-22", calendar_from: "2026-09-15", calendar_to: "2026-11-21",
  shop_statuses: ["ХОЛД", "СТОП", "ПАУЗА", "ПРВ КОМПЛ", "К ЗАПУСК", "В РАБОТЕ", "ОЖИД КОМПЛ", "ОЖИД ИНФ", "ОТК", "НА ОТГР", "ГОТОВО", "ОТГРУЖЕН"],
  supplied_values: ["НЕТ", "ДА", "Ч/Т"], arrival_values: ["Склад", "Ждем", "???"],
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
    group: "hold", sheet,
    calendar: { work: ["2026-09-22", "2026-09-23"], starts: { "2026-09-22": 10 }, notes: { "2026-09-25": "П" } },
  }],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

/** Журнал проекта — по клику на организацию (№ проекта открывает карточку). */
async function openProject(project: string) {
  const row = (await screen.findByRole("button", { name: project })).closest("tr")!;
  fireEvent.click(row.querySelector(".zg-link")!);
}

function sheetRow(project: string) {
  return document.querySelector(`tr[data-project="${project}"]`) as HTMLTableRowElement;
}

it("shows the shop sheet «ЗАГРУЗ» columns in the sheet order and the project journal", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => String(input) === "/api/production/calendar" ? json({ holidays: [], absences: [], shift: {} }) : json(plan));
  render(<ProductionPlanPage principal={principal} />);

  expect(await screen.findByRole("heading", { name: "Производственный план" })).toBeInTheDocument();
  await screen.findByRole("button", { name: "708К.4" });
  const heads = Array.from(document.querySelectorAll(".zg-table thead th")).map((item) => item.textContent);
  expect(heads.slice(0, 16)).toEqual([
    "Комментарий", "Статус", "% Готов", "В плане", "№ Проекта", "Наименование организации", "Есть давал", "Пров компл",
    "Компл есть", "ЗКП", "Дата прихода компл.", "Тех кар", "Тест С/Б", "Компл набр", "Ост дн", "Дедлайн",
  ]);
  // Календарь: сегодня −7 … +60 дней, день недели и дд.мм.
  expect(heads[16]).toBe("вт15.09");
  expect(heads.length).toBe(16 + 68);
  const row = sheetRow("708К.4");
  expect(Array.from(row.querySelectorAll(".zg-chip")).map((item) => item.textContent)).toEqual(["СТОП", "В РАБОТЕ"]);
  expect(row.querySelector(".zg-col-ready")?.textContent).toBe("40%");
  expect(row.querySelector(".zg-col-kit_arrival")?.textContent).toBe("28.09");
  expect(row.querySelector(".zg-col-days_left")?.className).toContain("ok");
  expect(row.querySelector(".zg-col-deadline")?.textContent).toBe("30.09.26");
  expect(row.querySelector(".zg-col-test_sb")?.className).toContain("zg-manual");
  expect(screen.getByText("На паузе · 1")).toBeInTheDocument();
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
  return Array.from(document.querySelectorAll(".zg-row")).map((item) => item.getAttribute("data-project"));
}

it("groups the sheet: on hold on top, active by deadline, finished at the bottom, closed on demand; searches", async () => {
  const groupedBoard: ProductionPlanResult = { ...board, rows: [
    row({ project: "701К.1", customer: "Альфа", days_left: null, deadline: null, group: "active" }),
    row({ project: "702К.1", customer: "Бета", days_left: -2, deadline: "2026-09-20", group: "active" }),
    row({ project: "703К.1", customer: "Гамма", days_left: 5, deadline: "2026-09-27", group: "hold" }),
    row({ project: "704К.1", customer: "Дельта", days_left: 1, deadline: "2026-09-23", group: "active" }),
    row({ project: "705К.1", customer: "Эпсилон", terminal: true, stage: "Отказ", group: "closed" }),
    row({ project: "706К.1", customer: "Зета", stage: "Отгружено", group: "done" }),
  ] };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => String(input) === "/api/production/calendar"
    ? json({ holidays: ["2026-09-24"], absences: [], shift: {} }) : json(groupedBoard));
  render(<ProductionPlanPage principal={principal} />);
  await screen.findByText("Альфа");

  expect(projects()).toEqual(["703К.1", "702К.1", "704К.1", "701К.1", "706К.1"]);
  expect(Array.from(document.querySelectorAll(".zg-group-label")).map((item) => item.textContent)).toEqual([
    "На паузе · 1", "В работе · 3", "ОТК, отгрузка, готово · 1",
  ]);
  // A shop holiday (24.09) is grey like a weekend.
  await waitFor(() => expect(document.querySelector(".zg-table thead th:nth-child(26)")?.className).toContain("zg-off"));
  fireEvent.click(screen.getByLabelText("Закрытые проекты · 1"));
  expect(projects()).toContain("705К.1");
  fireEvent.change(screen.getByLabelText("Поиск проекта"), { target: { value: "эпс" } });
  expect(projects()).toEqual(["705К.1"]);
  fireEvent.change(screen.getByLabelText("Поиск проекта"), { target: { value: "нет такого" } });
  expect(screen.getByText("Под этот поиск проектов нет.")).toBeInTheDocument();
});

it("edits sheet cells inline: a checkbox, back to the ERP value, the status, a day note and the deadline", async () => {
  const payloads: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input) === "/api/production/plan/events") {
      payloads.push(JSON.parse(String(init?.body)));
      return json({ event: plan.rows[0].events[0] }, 201);
    }
    return json(plan);
  });
  render(<ProductionPlanPage principal={principal} />);
  await screen.findByRole("button", { name: "708К.4" });

  fireEvent.click(screen.getByLabelText("Пров компл · 708К.4"));
  await waitFor(() => expect(payloads[0]).toMatchObject({ project: "708К.4", kind: "mark", field: "kit_checked", value: "да" }));
  // Тест С/Б marked by hand «да» over «нет»: unticking returns the ERP value.
  await waitFor(() => expect(screen.getByLabelText("Тест С/Б · 708К.4")).not.toBeDisabled());
  fireEvent.click(screen.getByLabelText("Тест С/Б · 708К.4"));
  await waitFor(() => expect(payloads[1]).toMatchObject({ field: "test_sb", value: "авто" }));

  await waitFor(() => expect(screen.getByLabelText("Тест С/Б · 708К.4")).not.toBeDisabled());
  fireEvent.click(sheetRow("708К.4").querySelector(".zg-col-status")!);
  const dialog = await screen.findByRole("dialog", { name: "Правка: 708К.4" });
  fireEvent.click(dialog.querySelector("label.t-pause input")!);
  fireEvent.click(screen.getByRole("button", { name: "Поставить статус" }));
  await waitFor(() => expect(payloads[2]).toMatchObject({ field: "status", value: "СТОП, В РАБОТЕ, ПАУЗА" }));

  await waitFor(() => expect(screen.getByLabelText("Тест С/Б · 708К.4")).not.toBeDisabled());
  const day = Array.from(sheetRow("708К.4").querySelectorAll(".zg-day"))[10];
  expect(day.textContent).toBe("П");
  fireEvent.click(day);
  fireEvent.click(await screen.findByRole("button", { name: "Убрать отметку" }));
  await waitFor(() => expect(payloads[3]).toMatchObject({ field: "day:2026-09-25", value: "" }));

  await waitFor(() => expect(screen.getByLabelText("Тест С/Б · 708К.4")).not.toBeDisabled());
  fireEvent.click(sheetRow("708К.4").querySelector(".zg-col-deadline")!);
  fireEvent.change(await screen.findByLabelText("Дата дедлайна"), { target: { value: "2026-10-03" } });
  fireEvent.click(screen.getByRole("button", { name: "Поставить дедлайн 03.10.2026" }));
  await waitFor(() => expect(payloads[4]).toEqual({ project: "708К.4", kind: "deadline", blocker: "", text: "", deadline: "2026-10-03" }));
});

it("draws the calendar: work days in the project colour, quantity on the first day, deadline and arrival marks", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(plan));
  render(<ProductionPlanPage principal={{ ...principal, roles: ["engineer"], permissions: { production: "view" } }} />);
  await screen.findByRole("button", { name: "708К.4" });
  const days = Array.from(sheetRow("708К.4").querySelectorAll(".zg-day"));

  expect(days[7].className).toContain("zg-work-start");
  expect(days[7].className).toContain("zg-today");
  expect(days[7].textContent).toBe("10");
  expect(days[8].className).toContain("zg-work-end");
  expect(days[13].className).toContain("zg-arrival");
  expect(days[15].className).toContain("zg-deadline");
  // Read-only viewer: no inline editing.
  expect(screen.getByLabelText("В плане · 708К.4")).toBeDisabled();
  fireEvent.click(days[10]);
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("keeps sheet helpers stable", () => {
  expect(projectColor("708К.4")).toBe(projectColor("708К.4"));
  expect(projectColor("708К.4")).toBeGreaterThanOrEqual(0);
  expect(projectColor("708К.4")).toBeLessThan(12);
  expect(sheetDays("2026-10-02", "2026-10-05").map((day) => [day.label, day.weekday, day.off])).toEqual([
    ["02.10", "пт", false], ["03.10", "сб", true], ["04.10", "вс", true], ["05.10", "пн", false],
  ]);
  expect(sheetDays("2026-09-30", "2026-10-01")[1].monthStart).toBe(true);
  expect(groupSheetRows([row({ project: "1", terminal: true, group: undefined }), row({ project: "2", group: undefined })]).closed.map((item) => item.project)).toEqual(["1"]);
  expect(markText({ field: "status", value: "ХОЛД", text: "" })).toBe("Статус: ХОЛД");
  expect(markText({ field: "kit_arrival", value: "2026-10-09", text: "" })).toBe("Дата прихода компл.: 09.10.2026");
  expect(markText({ field: "in_plan", value: "авто", text: "" })).toBe("В плане: расчёт ERP");
  expect(markText({ field: "day:2026-10-06", value: "П", text: "" })).toBe("Отметка 06.10.2026: П");
  expect(markText({ field: "comment", value: "", text: "" })).toBe("Комментарий: очищено");
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
      ["ПЗ-0001 · 708К.4КБ1 · 3 ч", "gantt-bar done"],
      ["ПЗ-0001 · 708К.4КБ1 · 6 ч", "gantt-bar late"],
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

it("opens the project card from the project number, not a panel under the list", async () => {
  const opened: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(plan));
  render(<ProductionPlanPage principal={principal} openProjectCard={(project) => opened.push(project)} />);
  fireEvent.click(await screen.findByRole("button", { name: "708К.4" }));
  expect(opened).toEqual(["708К.4"]);
  expect(screen.queryByRole("dialog")).toBeNull();
});
