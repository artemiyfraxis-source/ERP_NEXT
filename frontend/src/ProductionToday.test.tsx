import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProductionToday, REFRESH_MS } from "./ProductionToday";
import type { ShopToday, TodayLine } from "./productionTodayApi";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

const MASTER = { user: { id: "m", name: "Степан" }, roles: ["foreman"], permissions: { production: "edit", techcards: "view" } } as never;

const OP = {
  task_id: "t1", task_number: "ПЗ-0012", project: "801К.1", item: "КБ1", name: "Жгут", operation_id: "o1", position: 1,
  stage: "Опрессовка терминалов", code: "PRS_TERMINAL_hand",
};

function line(overrides: Partial<TodayLine>): TodayLine {
  return {
    ...OP, operator_id: "p1", operator_name: "Оператор", crew: false,
    planned_start: "2026-10-29T09:00:00+03:00", planned_finish: "2026-10-29T11:30:00+03:00",
    started_at: null, finished_at: null, quantity: 10, done: 4, remaining: 6, state: "в работе", late: false,
    ...overrides,
  };
}

const DATA: ShopToday = {
  today: "2026-10-29", now: "2026-10-29T10:31:00+03:00", working_day: true, shift: { start: "09:00", finish: "17:30" },
  site: "ЦАР", sites: ["ЦАР", "ЖУК"], can_switch: false,
  people: [
    { id: "p1", name: "Оператор", state: "работает", work: { ...OP, since: "2026-10-29T09:05:00+03:00", paused_since: null },
      downtime: null, absence: null, planned: 2, done: 1, next: null },
    { id: "p2", name: "Байков", state: "свободен", work: null, downtime: null, absence: null, planned: 1, done: 0,
      next: { ...OP, operation_id: "o2", position: 2, stage: "Пайка разъёма", planned_start: "2026-10-29T11:30:00+03:00" } },
    { id: "p3", name: "Яковлева", state: "отсутствует", work: null, downtime: null,
      absence: { kind: "Отпуск", from: "2026-10-27", to: "2026-10-31" }, planned: 0, done: 0, next: null },
  ],
  plan: [
    line({}),
    line({ operation_id: "o2", position: 2, stage: "Пайка разъёма", operator_id: "p2", operator_name: "Байков",
      planned_start: "2026-10-29T11:30:00+03:00", planned_finish: "2026-10-29T13:00:00+03:00", done: 0, remaining: 10, state: "не начата" }),
  ],
  handed: [],
  overdue: {
    operations: [line({ operation_id: "o0", stage: "Резка", planned_finish: "2026-10-28T16:00:00+03:00", late: true })],
    tasks: [{ task_id: "t1", task_number: "ПЗ-0012", project: "801К.1", item: "КБ1", name: "Жгут", deadline: "2026-10-27", days: 2, site: "ЦАР" }],
    projects: [{ project: "776К.1", customer: "ООО «ЯНДЕКС»", deadline: "2026-10-26", ready: "2026-11-20", slack_days: -25, hours: 30 }],
  },
  stuck: [
    { kind: "skill", project: "801К.1", item: "КБ1", reason: "Нет допуска у Байков к «Пайка разъёма»", task_id: "t1", task_number: "ПЗ-0012",
      operation_id: "o2", position: 2, stage: "Пайка разъёма", operator_name: "Байков", since: null, fix: "skills", fix_label: "Компетенции" },
    { kind: "etk", project: "801К.1", item: "КБ2", reason: "ЭТК не выдана — задание не запустить (Кабель)", task_id: null, task_number: "",
      operation_id: null, position: null, stage: "", operator_name: "", since: null, fix: "techcards", fix_label: "Техкарты проекта" },
    { kind: "kit", project: "801К.1", item: "КБ1", reason: "Комплект КБ не набран: запроса на комплектацию нет", task_id: "t1", task_number: "ПЗ-0012",
      operation_id: null, position: null, stage: "", operator_name: "", since: null, fix: "issues", fix_label: "Запросы на комплектацию" },
  ],
};

it("shows who does what, the plan of the day, the overdue and what stands with the date of the day", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(DATA));
  const openSkills = vi.fn();
  const openTask = vi.fn();
  render(<ProductionToday principal={MASTER} openSkills={openSkills} openTask={openTask} />);
  expect(await screen.findByText("29.10.2026")).toBeInTheDocument();
  expect(screen.getByText("Участок ЦАР")).toBeInTheDocument();
  expect(screen.queryByRole("radiogroup", { name: "Участок" })).toBeNull();
  const people = screen.getByRole("table", { name: "Монтажники участка" });
  expect(within(people).getByText("работает")).toBeInTheDocument();
  expect(within(people).getByText("Отпуск до 31.10.2026")).toBeInTheDocument();
  expect(within(people).getByText(/дальше в 11:30/)).toBeInTheDocument();
  expect(within(people).getByText("09:05")).toBeInTheDocument();
  const plan = screen.getByRole("table", { name: "План на сегодня" });
  expect(within(plan).getByText("09:00–11:30")).toBeInTheDocument();
  expect(within(plan).getByText("4 из 10")).toBeInTheDocument();
  expect(screen.getByRole("table", { name: "Просроченные операции" })).toHaveTextContent("28.10 16:00");
  expect(screen.getByRole("table", { name: "Задания со сроком в прошлом" })).toHaveTextContent("2 дня");
  expect(screen.getByRole("table", { name: "Проекты «не успевает»" })).toHaveTextContent("776К.1");
  const stuck = screen.getByRole("table", { name: "Что стоит" });
  expect(within(stuck).getByText("Нет допуска у Байков к «Пайка разъёма»")).toBeInTheDocument();
  fireEvent.click(within(stuck).getByRole("button", { name: "Компетенции" }));
  expect(openSkills).toHaveBeenCalled();
  // Комплектация не открыта мастеру — место названо, но без кнопки.
  expect(within(stuck).queryByRole("button", { name: "Запросы на комплектацию" })).toBeNull();
  expect(within(stuck).getByText("Запросы на комплектацию")).toBeInTheDocument();
  fireEvent.click(within(people).getAllByRole("button", { name: /Опрессовка терминалов/ })[0]);
  expect(openTask).toHaveBeenCalledWith("t1", "o1");
});

it("lets full access switch to ЖУК: its people and plan like ЦАР plus the tasks handed whole", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("site=%D0%96%D0%A3%D0%9A")) {
      return json({ ...DATA, site: "ЖУК", can_switch: true, people: [], plan: [], stuck: [],
        overdue: { operations: [], tasks: [], projects: [] },
        handed: [{ task_id: "t9", task_number: "ПЗ-0020", project: "802К.1", item: "КБ1", name: "Кабель", quantity: 20, delivered: 5, remaining: 15, deadline: "2026-11-02" }] });
    }
    return json({ ...DATA, can_switch: true });
  });
  render(<ProductionToday principal={MASTER} />);
  fireEvent.click(await screen.findByRole("radio", { name: "ЖУК" }));
  expect(await screen.findByText("ПЗ-0020")).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/api/production/today?site=%D0%96%D0%A3%D0%9A"))).toBe(true);
  expect(screen.getByRole("table", { name: "Отдано участку целиком" })).toHaveTextContent("15");
  expect(screen.getByRole("heading", { name: /План на/ })).toBeInTheDocument(); // план людей ЖУК, как у ЦАР
  expect(screen.getByText("На участке ЖУК нет монтажников — отметьте «Монтажник» в карточке сотрудника.")).toBeInTheDocument();
  expect(screen.getByText("Не стоит ничего: запланированное может начаться.")).toBeInTheDocument();
  expect(screen.getByText("Просроченного нет.")).toBeInTheDocument();
});

it("says what to do on an empty day and on a day off", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({
    ...DATA, working_day: false, plan: [], stuck: [], overdue: { operations: [], tasks: [], projects: [] },
  }));
  render(<ProductionToday principal={MASTER} />);
  expect(await screen.findByText("На 29.10.2026 операций не запланировано — назначьте их на «Доске».")).toBeInTheDocument();
  expect(screen.getByText("29.10.2026 — нерабочий день по календарю цеха.")).toBeInTheDocument();
});

it("refreshes every minute and keeps the last data when a refresh fails", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(DATA));
  render(<ProductionToday principal={MASTER} />);
  expect(await screen.findByText("29.10.2026")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fetchMock.mockImplementation(async () => json({ detail: "Нет связи" }, 503));
  await act(async () => { await vi.advanceTimersByTimeAsync(REFRESH_MS); });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(await screen.findByText(/не обновилось/)).toBeInTheDocument();
  expect(screen.getByRole("table", { name: "План на сегодня" })).toBeInTheDocument();
});

it("says what went wrong when the first load fails", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ detail: "Раздел не открыт" }, 403));
  render(<ProductionToday principal={MASTER} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Раздел не открыт");
});

it("folds the operations done today behind a switch", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({
    ...DATA, plan: [...DATA.plan, line({ operation_id: "o5", stage: "Резка", done: 10, remaining: 0, state: "сделано" })],
  }));
  render(<ProductionToday principal={MASTER} />);
  const plan = await screen.findByRole("table", { name: "План на сегодня" });
  expect(within(plan).queryByText("Резка")).toBeNull();
  expect(screen.getByText("1 из 3")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "показать сделанные (1)" }));
  expect(within(screen.getByRole("table", { name: "План на сегодня" })).getByText("Резка")).toBeInTheDocument();
});
