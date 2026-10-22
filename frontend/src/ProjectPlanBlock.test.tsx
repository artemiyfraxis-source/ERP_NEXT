import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { forgetOpenProject, MasterBoard } from "./MasterBoard";
import { ProjectPlanBlock } from "./ProjectPlanBlock";
import type { ProductionPlanResult, ProductionTask } from "./api";

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
    deadline: "2026-09-30", days_left: 8, work_days_left: 6, stage: "В работе", terminal: false, stage_manual: false,
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

const stagePreview = (over: Record<string, unknown> = {}) => ({
  project: "70К.26.13", value: "Отгружено", kb: "", direction: "forward", reason_required: false, error: "", message: "",
  terminal: "", blocker: "", warnings: [], preview_token: "tok-plan",
  kbs: [{ kb: "КБ1", label: "КБ1", from: "Новый", to: "Отгружено", skipped: [], change: true, note: "", warnings: [] }],
  ...over,
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => { window.localStorage.setItem("erp-mb-project-block", "1"); });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); window.history.replaceState({}, "", "/"); forgetOpenProject(); window.localStorage.removeItem("erp-mb-project-block"); });

it("is collapsed by default with one summary line, remembers the choice and opens to the full block", async () => {
  window.localStorage.removeItem("erp-mb-project-block");
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(plan));
  const first = render(<ProjectPlanBlock project="708К.4" canEdit />);
  const toggle = await screen.findByRole("button", { name: /Проект/ });
  await waitFor(() => expect(toggle.textContent).toContain("КБ1: В работе, ТК не проверена"));
  expect(toggle.textContent).toContain("Снабжение: склад 2, в пути 1, в закупке 1");
  expect(toggle.textContent).toContain("последняя запись: 22.09 09:00 Блокер");
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText(/Заказчик: Заказчик/)).toBeNull();
  fireEvent.click(toggle);
  expect(await screen.findByText(/Заказчик: Заказчик/)).toBeInTheDocument();
  expect(window.localStorage.getItem("erp-mb-project-block")).toBe("1");
  first.unmount();
  render(<ProjectPlanBlock project="708К.4" canEdit />);
  expect(await screen.findByText(/Заказчик: Заказчик/)).toBeInTheDocument();
});

it("shows the project block with customer, KB, supply and history, and a link to the project card", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(plan));
  const opened: string[] = [];
  render(<ProjectPlanBlock project="708К.4" canEdit openProjectCard={(project) => opened.push(project)} />);
  expect(await screen.findByText(/Заказчик: Заказчик/)).toBeInTheDocument();
  expect(screen.getByText("Всего 5: склад 2, в пути 1, в закупке 1, в заявках 1.")).toBeInTheDocument();
  expect(screen.getByText("ТК выдана · не набрано")).toBeInTheDocument();
  expect(screen.getAllByText("Ждём разъёмы").length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "Открыть карточку проекта" }));
  expect(opened).toEqual(["708К.4"]);
  // Блок сворачивается.
  fireEvent.click(screen.getByRole("button", { name: /Проект/ }));
  expect(screen.queryByText(/Заказчик: Заказчик/)).toBeNull();
  expect(window.localStorage.getItem("erp-mb-project-block")).toBe("0");
});

it("has no edit controls without edit access and no card link without access to «Проекты»", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(plan));
  render(<ProjectPlanBlock project="708К.4" canEdit={false} />);
  await screen.findByText(/Заказчик: Заказчик/);
  expect(screen.queryByLabelText("Этап КБ1")).toBeNull();
  expect(screen.queryByRole("button", { name: "Записать" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Открыть карточку проекта" })).toBeNull();
});

it("writes a comment into the project journal through the plan API", async () => {
  let payload: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input) === "/api/production/plan/events") { payload = JSON.parse(String(init?.body)); return json({ event: plan.rows[0].events[0] }, 201); }
    return json(plan);
  });
  render(<ProjectPlanBlock project="708К.4" canEdit />);
  await screen.findByLabelText("Запись");
  expect(screen.getByRole("button", { name: "Записать" })).toBeDisabled();
  fireEvent.change(document.querySelector(".plan-note-text textarea")!, { target: { value: "нужна проверка" } });
  fireEvent.click(screen.getByRole("button", { name: "Записать" }));
  await waitFor(() => expect(payload).toEqual({ project: "708К.4", kind: "note", blocker: "ХОЛД", text: "нужна проверка", deadline: "2026-09-30" }));
});

it("adds a deadline through the project journal", async () => {
  let payload: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input) === "/api/production/plan/events") { payload = JSON.parse(String(init?.body)); return json({ event: plan.rows[0].events[0] }, 201); }
    return json(plan);
  });
  render(<ProjectPlanBlock project="708К.4" canEdit />);
  fireEvent.change(await screen.findByLabelText("Запись"), { target: { value: "deadline" } });
  fireEvent.change(screen.getByLabelText("Дата"), { target: { value: "2026-10-03" } });
  fireEvent.click(screen.getByRole("button", { name: "Записать" }));
  await waitFor(() => expect(payload).toEqual({ project: "708К.4", kind: "deadline", blocker: "ХОЛД", text: "", deadline: "2026-10-03" }));
});

it("sets the stage of a project without KBs through a journal mark with an empty KB", async () => {
  const bare = { ...plan.rows[0], project: "70К.26.13", stage: "Новый", kits: [], blocker: null, events: [] };
  let payload: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input).startsWith("/api/production/plan/stage-preview")) return json(stagePreview());
    if (String(input) === "/api/production/plan/events") { payload = JSON.parse(String(init?.body)); return json({ event: plan.rows[0].events[0] }, 201); }
    return json({ ...plan, rows: [bare] });
  });
  render(<ProjectPlanBlock project="70К.26.13" canEdit />);
  fireEvent.change(await screen.findByLabelText("Этап проекта"), { target: { value: "Отгружено" } });
  fireEvent.click(await screen.findByRole("button", { name: "Сменить этап" }));
  await waitFor(() => expect(payload).toMatchObject({ project: "70К.26.13", kind: "stage", item_index: "", value: "Отгружено", reason: "", preview_token: "tok-plan" }));
});

it("records manual kit stage and techcard review", async () => {
  const payloads: Record<string, unknown>[] = [];
  const previews: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input).startsWith("/api/production/plan/stage-preview")) {
      previews.push(decodeURIComponent(String(input)));
      return json(stagePreview({ direction: "back", reason_required: true }));
    }
    if (String(input) === "/api/production/plan/events") { payloads.push(JSON.parse(String(init?.body))); return json({ event: plan.rows[0].events[0] }, 201); }
    return json(plan);
  });
  render(<ProjectPlanBlock project="708К.4" canEdit />);
  fireEvent.change(await screen.findByLabelText("Этап КБ1"), { target: { value: "Отгружено" } });
  const confirm = await screen.findByRole("button", { name: "Сменить этап" });
  expect(previews[0]).toBe("/api/production/plan/stage-preview?project=708К.4&value=Отгружено&kb=КБ1");
  expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Причина смены этапа"), { target: { value: "ошиблись кнопкой" } });
  fireEvent.click(confirm);
  await waitFor(() => expect(payloads[0]).toMatchObject({ project: "708К.4", kind: "stage", item_index: "КБ1", value: "Отгружено", reason: "ошиблись кнопкой" }));
  fireEvent.change(screen.getByLabelText("Проверка ТК КБ1"), { target: { value: "проверена" } });
  await waitFor(() => expect(payloads[1]).toMatchObject({ project: "708К.4", kind: "tcReview", item_index: "КБ1", value: "проверена" }));
});

it("opens the project window of the board from ?project= with the «Проект» block inside", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).startsWith("/api/production/plan") ? json(plan) : json({ items: [], statuses: [] })));
  window.history.replaceState({}, "", "/?section=production&project=708%D0%9A.4");
  const task = { id: "1", number: "ПЗ-1", version: 1, created_at: "2026-09-01T08:00:00", project: "708К.4", item: "КБ1", name: "", quantity: 5,
    fulfillment_request_id: null, deadline: "2026-09-30", status: "Запущено", launched_by: "Мастер", handed_to: "", planned_start: null, planned_finish: null,
    assigned: 0, operations: [] } as unknown as ProductionTask;
  render(<MasterBoard tasks={[task]} operators={[]} skills={[]} absences={[]} canEdit busy="" mutate={async () => {}} openTask={() => {}} />);
  expect(await screen.findByRole("dialog", { name: "Проект 708К.4" })).toBeInTheDocument();
  expect(await screen.findByText(/Заказчик: Заказчик/)).toBeInTheDocument();
  expect(screen.getByLabelText("Этап КБ1")).toBeInTheDocument();
  expect(screen.getByLabelText("Проверка ТК КБ1")).toBeInTheDocument();
  // Закрытие окна убирает параметр из адреса.
  fireEvent.click(screen.getAllByRole("button", { name: "Закрыть" })[0]);
  await waitFor(() => expect(window.location.search).toBe("?section=production"));
});
