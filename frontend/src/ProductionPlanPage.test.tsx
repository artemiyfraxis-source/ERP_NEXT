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
  stages: ["Новый", "В работе", "Отгружено"],
  review_values: ["не проверена", "проверена", "с погрешностями"],
  rows: [{
    project: "708К.4", customer: "Заказчик", opened_on: "2026-09-01",
    deadline: "2026-09-30", days_left: 8, stage: "В работе", stage_manual: false,
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

it("shows the native project stage, supply, task plan and append-only journal", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json(plan));
  render(<ProductionPlanPage principal={principal} />);

  expect(await screen.findByRole("heading", { name: "Производственный план" })).toBeInTheDocument();
  expect(await screen.findByText("Ждём разъёмы")).toBeInTheDocument();
  expect(screen.getByText("Всего 5: склад 2, в пути 1, в закупке 1, в заявках 1.")).toBeInTheDocument();
  expect(screen.getByText("1 заданий · назначено 2 из 3 этапов.")).toBeInTheDocument();
  expect(screen.getByText("ТК ✓ · не проверено 1 · набрано 0/1")).toBeInTheDocument();
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
  await screen.findByText("Заказчик");

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
  await screen.findByText("Заказчик");
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
  await screen.findByText("Заказчик");

  fireEvent.change(screen.getByLabelText("Этап КБ1"), { target: { value: "Отгружено" } });
  await waitFor(() => expect(payloads[0]).toMatchObject({
    project: "708К.4", kind: "stage", item_index: "КБ1", value: "Отгружено",
  }));
  fireEvent.change(screen.getByLabelText("Проверка ТК КБ1"), { target: { value: "проверена" } });
  await waitFor(() => expect(payloads[1]).toMatchObject({
    project: "708К.4", kind: "tcReview", item_index: "КБ1", value: "проверена",
  }));
});
