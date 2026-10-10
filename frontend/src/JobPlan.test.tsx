import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { JobPanel } from "./JobPlan";
import type { ProductionFlow, ProductionTask } from "./api";

const task: ProductionTask = {
  id: "t1", number: "ПЗ-0007", version: 3, created_at: "2026-10-09T09:00:00+03:00", project: "591K.3", item: "КБ1",
  name: "Жгут", quantity: 20, fulfillment_request_id: null, deadline: "2026-10-12", status: "Запущено",
  launched_by: "Мастер", handed_to: "", planned_start: null, planned_finish: null, assigned: 0, operations: [],
  flow_mode: "mixed", transfer_batch: null, early_quantity: null, team: [], techcard_revision: 1, delivered: 0,
};

function flow(overrides: Partial<ProductionFlow> = {}, taskPatch: Partial<ProductionTask> = {}): ProductionFlow {
  return {
    task: { ...task, ...taskPatch },
    modes: [
      { value: "sequential", name: "Последовательно — вся партия целиком" },
      { value: "parallel", name: "Параллельно — каждая партия сразу" },
      { value: "mixed", name: "Параллельно-последовательно — без простоев" },
    ],
    thresholds: { notice: "5", limit: "15" },
    plan: {
      anchor: "2026-10-10T09:00:00+03:00", transfer_batch: 20, first_ready: "2026-10-10T15:00:00+03:00", first_quantity: 20,
      finish: "2026-10-13T15:00:00+03:00", baseline_finish: "2026-10-13T15:00:00+03:00", baseline_early_ready: null,
      delay_percent: null, level: "", work_seconds: 7200, warnings: ["Всё готово 13.10 — позже срока 12.10"],
      steps: [{
        operation_id: "o1", position: 1, stage: "Резка", code: "OP-01", people: "2", operator_ids: ["p1", "p2"],
        start: "2026-10-10T09:00:00+03:00", first_out: "2026-10-10T11:00:00+03:00", finish: "2026-10-10T12:00:00+03:00",
        wait_seconds: 0,
      }],
    },
    deliveries: [], delivered: 0,
    norms: { techcard_id: "c1", revision: 1, current_revision: 2, outdated: true },
    ...overrides,
  };
}

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.restoreAllMocks(); });

const operators = [{ id: "p1", name: "Иванов", site: "ЦАР" }, { id: "p2", name: "Петров", site: "ЦАР" }];

it("shows the job plan in dates, offers the ETK norms and saves mode, batch, early pieces and team", async () => {
  const calls: { url: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    return json(url.endsWith("/flow") && !init?.method ? flow() : { task });
  });
  const mutate = vi.fn(async (_key: string, operation: () => Promise<unknown>) => { await operation(); });

  render(<JobPanel task={task} operators={operators} canEdit busy={false} mutate={mutate} />);

  expect(await screen.findByRole("heading", { name: "План задания" })).toBeInTheDocument();
  expect(screen.getByText("13.10 15:00")).toBeInTheDocument();
  expect(screen.getByText("Всё готово 13.10 — позже срока 12.10")).toBeInTheDocument();
  expect(screen.getByText(/В ЭТК ревизия 2 — нормы задания от ревизии 1/)).toBeInTheDocument();
  expect(screen.queryByText(/mixed|sequential/)).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Обновить нормы из ЭТК" }));
  await waitFor(() => expect(calls.some((call) => call.url.endsWith("/norms/refresh"))).toBe(true));

  fireEvent.change(screen.getByLabelText("Передача между этапами"), { target: { value: "parallel" } });
  fireEvent.change(screen.getByLabelText("Передаточная партия"), { target: { value: "5" } });
  fireEvent.change(screen.getByLabelText("Первые раньше"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "Иванов" }));
  fireEvent.click(screen.getByRole("button", { name: "Сохранить план" }));

  await waitFor(() => expect(calls.find((call) => call.url.endsWith("/flow") && call.body)?.body).toEqual({
    version: 3, flow_mode: "parallel", transfer_batch: 5, early_quantity: 2, team: ["p1"],
  }));
});

it("refuses early pieces equal to the run before saving", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(flow({ norms: { techcard_id: "c1", revision: 1, current_revision: 1, outdated: false } })));

  render(<JobPanel task={task} operators={operators} canEdit busy={false} mutate={vi.fn()} />);
  fireEvent.change(await screen.findByLabelText("Первые раньше"), { target: { value: "20" } });

  expect(screen.getByRole("alert")).toHaveTextContent("Первые раньше — от 1 до 19 шт");
  expect(screen.getByRole("button", { name: "Сохранить план" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Обновить нормы из ЭТК" })).toBeNull();
});

it("zhuk hands over the whole remainder by default and can deliver part of it", async () => {
  const handed = { ...task, handed_to: "ЖУК" };
  const calls: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (init?.body) calls.push(JSON.parse(String(init.body)));
    return json(String(input).endsWith("/flow") ? flow({
      delivered: 8,
      deliveries: [{ id: "d1", site: "ЖУК", quantity: 8, delivered_at: "2026-10-09T16:00:00+03:00", delivered_by: "Степан", cancelled_at: null, cancelled_by: null }],
    }, { handed_to: "ЖУК" }) : { task: handed });
  });
  const mutate = vi.fn(async (_key: string, operation: () => Promise<unknown>) => { await operation(); });

  render(<JobPanel task={handed} operators={operators} canEdit busy={false} mutate={mutate} />);

  expect(await screen.findByRole("heading", { name: "Сдача ЖУК" })).toBeInTheDocument();
  expect(screen.getByText(/сдано 8 из 20 шт · осталось 12/)).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "План задания" })).toBeNull();
  // Весь остаток — одним разом (quantity null), часть — числом.
  fireEvent.click(screen.getByRole("button", { name: "Сдано 12 шт" }));
  await waitFor(() => expect(calls).toEqual([{ version: 3, quantity: null }]));
  fireEvent.change(screen.getByLabelText("Сдано, шт"), { target: { value: "5" } });
  fireEvent.click(screen.getByRole("button", { name: "Сдано 5 шт" }));
  await waitFor(() => expect(calls[1]).toEqual({ version: 3, quantity: 5 }));
  fireEvent.change(screen.getByLabelText("Сдано, шт"), { target: { value: "13" } });
  expect(screen.getByRole("button", { name: "Сдано" })).toBeDisabled();
});
