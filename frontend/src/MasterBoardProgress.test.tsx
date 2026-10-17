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
  it("считает сдано, ждут ОТК и процент из полей API", () => {
    const progress = opProgress(tasks[0], tasks[0].operations[0], NOW);
    expect(progress).toMatchObject({ required: 500, good: 250, waiting: 50, percent: 50, flags: ["wait"] });
  });

  it("не начато и план истёк только без факта и без «взял»", () => {
    expect(opProgress(tasks[0], tasks[0].operations[1], NOW).flags).toEqual(["late"]);
    const taken = op({ id: "x", position: 1, stage: "Резка", planned_start: "2026-10-04T09:00:00", shares: [{ operator_id: "u", quantity: null, planned_start: null, planned_finish: null, started_at: "2026-10-04T09:05:00", finished_at: null }] });
    expect(opProgress(tasks[0], taken, NOW).flags).toEqual([]);
    expect(opProgress(tasks[0], tasks[0].operations[0], NOW).flags).not.toContain("late");
  });

  it("позже срока задания — плановый конец после даты задания; у выполненной флагов нет", () => {
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
    expect(within(strip).getByRole("button", { name: /ждёт ОТК: 1/ })).toBeInTheDocument();
    expect(within(strip).getByRole("button", { name: /позже срока задания: 1/ })).toBeInTheDocument();
  });

  it("в строке операции: «сдано 250/500 · 50 ждут ОТК», полоса и флаги", () => {
    mount();
    openProject();
    expect(screen.getByText("сдано 250/500 · 50 ждут ОТК")).toBeInTheDocument();
    expect(screen.getAllByRole("progressbar")[0]).toHaveAttribute("aria-valuenow", "50");
    expect(screen.getAllByText("не начато, план истёк").some((node) => node.className.includes("mb-flag danger"))).toBe(true);
    expect(screen.getAllByText("ждёт ОТК").some((node) => node.className.includes("mb-flag warn"))).toBe(true);
    expect(screen.getAllByText("позже срока задания").some((node) => node.className.includes("mb-flag danger"))).toBe(true);
  });

  it("клик по отметке оставляет только проекты и операции с ней, повторный клик снимает", () => {
    mount();
    expect(screen.getByText("BBB")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /позже срока задания/ }));
    expect(screen.queryByText("BBB")).not.toBeInTheDocument();
    openProject();
    expect(screen.getByText("Пайка")).toBeInTheDocument();
    expect(screen.queryByText("Резка")).not.toBeInTheDocument();
    expect(screen.queryByText("Опрессовка")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /позже срока задания/ }));
    expect(screen.getByText("Резка")).toBeInTheDocument();
    expect(screen.getByText("Опрессовка")).toBeInTheDocument();
  });
});

describe("экран мастера: сроки и доска монтажников (аудит 10.10, Б2)", () => {
  const shift = { weekdays: [0, 1, 2, 3, 4], start: "09:00", finish: "17:30", breaks: [["13:00", "13:30"]] as [string, string][], effective_hours: "8" };
  const operators = [{ id: "p1", name: "Волков Дмитрий", site: "ЦАР" }];
  function mountBoard() {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [], statuses: [] }), { status: 200, headers: { "Content-Type": "application/json" } })));
    return render(<MasterBoard tasks={tasks} operators={operators} skills={[]} absences={[]} shift={shift} canEdit busy="" mutate={async () => {}} openTask={() => {}}
      projectDeadlines={{ aaa: { deadline: "2026-11-04", days_left: 30 } }} />);
  }

  it("плитка: «ДЛ проекта» из плана и отдельно «задание до» — не одно «ДЛ» на два разных срока (РП-1, РП-2)", () => {
    mountBoard();
    const tile = screen.getByText("AAA").closest(".kit-tile") as HTMLElement;
    expect(within(tile).getByText("ДЛ проекта 04.11")).toHaveAttribute("data-tone", "neutral");
    expect(within(tile).getByText("задание до 10.10")).toHaveAttribute("title", "Срок задания, указанный при запуске, 10.10.2026 — через 5 дн");
    // Проекта BBB нет в плане — только срок задания, и он не красный за 5 дней.
    const other = screen.getByText("BBB").closest(".kit-tile") as HTMLElement;
    expect(within(other).queryByText(/ДЛ проекта/)).toBeNull();
    expect(within(other).getByText("задание до 10.10")).toHaveAttribute("data-tone", "neutral");
    fireEvent.click(screen.getByText("AAA"));
    expect(screen.getByText("ДЛ проекта 04.11")).toBeInTheDocument();
  });

  it("доска монтажников: период словами, навигация как в плане, ячейка и легенда — общие с «Загрузкой по людям» (РП-3, РП-4)", () => {
    mountBoard();
    const board = screen.getByRole("complementary", { name: "Монтажники" });
    expect(within(board).getByText("5–11 октября 2026")).toBeInTheDocument();
    expect(within(board).getByRole("button", { name: "Сегодня" })).toBeDisabled();
    fireEvent.click(within(board).getByRole("button", { name: "неделя →" }));
    expect(within(board).getByText("12–18 октября 2026")).toBeInTheDocument();
    fireEvent.click(within(board).getByRole("button", { name: "Сегодня" }));
    // Свободный рабочий день — пустая ячейка (не «0/8»), выходной — без «вых»; легенда по смене 8 ч, без «/7».
    const free = within(board).getByRole("button", { name: "День Волков Дмитрий 07.10" });
    expect(free.textContent).toBe("");
    expect(free.closest("td")).toHaveClass("mb-cell", "free");
    expect(within(board).getByTitle("Волков Дмитрий, 11.10: нерабочий день").textContent).toBe("");
    const legend = board.querySelector(".mb-legend") as HTMLElement;
    expect(legend).toHaveAttribute("title", expect.stringContaining("Смена 8 ч"));
    expect(legend.textContent).toContain("8/8");
    expect(legend.textContent).not.toContain("/7");
  });
});

describe("экран мастера: операция по ПФ", () => {
  it("показывает ПФ и номера его операций, обычная операция как раньше", () => {
    const pf = op({ id: "p1", position: 3, stage: "Опрессовка", code: "OP-5", etk_ops: [{ position: 5, code: "OP-5", stage: "Зачистка" }, { position: 6, code: "OP-6", stage: "Опрессовка" }] });
    const plain = op({ id: "p2", position: 4, stage: "Пайка", code: "SOL-1" });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [], statuses: [] }), { status: 200, headers: { "Content-Type": "application/json" } })));
    render(<MasterBoard tasks={[task("9", "PFP", [pf, plain])]} operators={[]} skills={[]} absences={[]} canEdit={false} busy="" mutate={async () => {}} openTask={() => {}} />);
    fireEvent.click(screen.getByText("PFP"));
    expect(screen.getByText("ПФ-3 · Опрессовка")).toBeInTheDocument();
    expect(screen.getByText("оп. 05·06")).toBeInTheDocument();
    expect(screen.getByText("Пайка")).toBeInTheDocument();
    expect(screen.getByText("SOL-1")).toBeInTheDocument();
  });
});
