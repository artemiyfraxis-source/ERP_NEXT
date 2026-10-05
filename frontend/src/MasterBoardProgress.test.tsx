import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ProductionOperation, ProductionTask } from "./api";
import { forgetOpenProject, MasterBoard, opProgress } from "./MasterBoard";

const NOW = new Date("2026-10-05T12:00:00").getTime();

function op(patch: Partial<ProductionOperation> & { id: string; position: number; stage: string }): ProductionOperation {
  return {
    code: "", description: "", seconds_per_unit: "1", setup_seconds: "0", norm_seconds: 600, operator_ids: [],
    planned_start: "2026-10-06T09:00:00", planned_finish: "2026-10-06T10:00:00", assigned_by: null, shares: [],
    required_quantity: "500", fact: { good: "0", defect: "0", processed: "0" }, wip: { ok: "0", closed: "0" }, state: "не начата",
    ...patch,
  };
}

function task(id: string, project: string, operations: ProductionOperation[], patch: Partial<ProductionTask> = {}): ProductionTask {
  return {
    id, number: `ПЗ-${id}`, version: 1, created_at: "2026-10-01T08:00:00", project, item: "КБ1", name: "", quantity: 500,
    fulfillment_request_id: null, deadline: "2026-10-10", status: "Запущено", launched_by: "Мастер", handed_to: "",
    planned_start: null, planned_finish: null, assigned: 0, operations, ...patch,
  } as ProductionTask;
}

const tasks = [
  task("1", "AAA", [
    op({ id: "o1", position: 1, stage: "Резка", fact: { good: "250", defect: "0", processed: "250" }, wip: { ok: "0", closed: "50" }, state: "в работе" }),
    op({ id: "o2", position: 2, stage: "Опрессовка", planned_start: "2026-10-04T09:00:00", planned_finish: "2026-10-04T12:00:00" }),
    op({ id: "o3", position: 3, stage: "Пайка", planned_finish: "2026-10-12T10:00:00" }),
    op({ id: "o4", position: 4, stage: "Сдача", fact: { good: "500", defect: "0", processed: "500" }, state: "выполнена" }),
  ]),
  task("2", "BBB", [op({ id: "o5", position: 1, stage: "Маркировка" })]),
];

function mount() {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [], statuses: [] }), { status: 200, headers: { "Content-Type": "application/json" } })));
  return render(<MasterBoard tasks={tasks} operators={[]} skills={[]} absences={[]} canEdit={false} busy="" mutate={async () => {}} openTask={() => {}} />);
}

beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["Date"] }); vi.setSystemTime(NOW); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); forgetOpenProject(); });

describe("opProgress", () => {
  it("считает сдано, ждут подтверждения и процент из полей API", () => {
    const progress = opProgress(tasks[0], tasks[0].operations[0], NOW);
    expect(progress).toMatchObject({ required: 500, good: 250, waiting: 50, percent: 50, flags: ["wait"] });
  });

  it("не начато и план истёк только без факта и без «взял»", () => {
    expect(opProgress(tasks[0], tasks[0].operations[1], NOW).flags).toEqual(["late"]);
    const taken = op({ id: "x", position: 1, stage: "Резка", planned_start: "2026-10-04T09:00:00", shares: [{ operator_id: "u", quantity: null, planned_start: null, planned_finish: null, started_at: "2026-10-04T09:05:00", finished_at: null }] });
    expect(opProgress(tasks[0], taken, NOW).flags).toEqual([]);
    expect(opProgress(tasks[0], tasks[0].operations[0], NOW).flags).not.toContain("late");
  });

  it("позже дедлайна — плановый конец после даты задания; у выполненной флагов нет", () => {
    expect(opProgress(tasks[0], tasks[0].operations[2], NOW).flags).toEqual(["over"]);
    expect(opProgress(tasks[0], tasks[0].operations[3], NOW)).toMatchObject({ percent: 100, flags: [] });
  });
});

describe("экран мастера: прогресс и «Сегодня»", () => {
  function openProject() { fireEvent.click(screen.getByText("AAA")); }

  it("в полосе «Сегодня» счётчики по трём отметкам", () => {
    mount();
    const strip = screen.getByRole("group", { name: "Сегодня" });
    expect(within(strip).getByRole("button", { name: /не начато, план истёк: 1/ })).toBeInTheDocument();
    expect(within(strip).getByRole("button", { name: /ждёт подтверждения: 1/ })).toBeInTheDocument();
    expect(within(strip).getByRole("button", { name: /позже дедлайна: 1/ })).toBeInTheDocument();
  });

  it("в строке операции: «сдано 250/500 · 50 ждут подтверждения», полоса и флаги", () => {
    mount();
    openProject();
    expect(screen.getByText("сдано 250/500 · 50 ждут подтверждения")).toBeInTheDocument();
    expect(screen.getAllByRole("progressbar")[0]).toHaveAttribute("aria-valuenow", "50");
    expect(screen.getAllByText("не начато, план истёк").some((node) => node.className.includes("mb-flag danger"))).toBe(true);
    expect(screen.getAllByText("ждёт подтверждения").some((node) => node.className.includes("mb-flag warn"))).toBe(true);
    expect(screen.getAllByText("позже дедлайна").some((node) => node.className.includes("mb-flag danger"))).toBe(true);
  });

  it("клик по отметке оставляет только проекты и операции с ней, повторный клик снимает", () => {
    mount();
    expect(screen.getByText("BBB")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /позже дедлайна/ }));
    expect(screen.queryByText("BBB")).not.toBeInTheDocument();
    openProject();
    expect(screen.getByText("Пайка")).toBeInTheDocument();
    expect(screen.queryByText("Резка")).not.toBeInTheDocument();
    expect(screen.queryByText("Опрессовка")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /позже дедлайна/ }));
    expect(screen.getByText("Резка")).toBeInTheDocument();
    expect(screen.getByText("Опрессовка")).toBeInTheDocument();
  });
});
