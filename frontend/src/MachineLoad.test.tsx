import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ProductionOperation, ProductionTask } from "./api";
import { MachineLoadGrid, MachineWarning, machineLevel, machineName, wallTime } from "./MachineLoad";
import { forgetOpenProject, MasterBoard } from "./MasterBoard";
import type { MachineLoad, MachineLoadDay } from "./machineLoadApi";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Call = { path: string; method: string; body: unknown };

function serve(answer: (call: Call) => Response) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const call = { path: String(input), method: (init?.method ?? "GET").toUpperCase(), body: init?.body ? JSON.parse(String(init.body)) : null };
    calls.push(call);
    return answer(call);
  });
  return calls;
}

afterEach(() => vi.restoreAllMocks());

const DAYS = ["2026-10-19", "2026-10-20", "2026-10-21"];
const day = (value: string, busy: number, capacity = 2 * 7 * 3600, extra: Partial<MachineLoadDay> = {}): MachineLoadDay => ({
  day: value, busy_seconds: busy, capacity_seconds: capacity, percent: capacity ? Math.round((busy * 100) / capacity) : null,
  peak: busy ? 1 : 0, over: false, ...extra,
});

const LOAD: MachineLoad = {
  date_from: DAYS[0], date_to: DAYS[2],
  machines: [{
    machine: "ОБ.027", label: "ОБ.027 Пресс", units: 2, known: true,
    days: [day(DAYS[0], 12_600), day(DAYS[1], 0), day(DAYS[2], 30_000, 2 * 7 * 3600, { peak: 3, over: true })],
    items: [{
      task_id: "t1", task_number: "ПЗ-0012", project: "708К", item: "КБ1", operation_id: "o1", position: 2, stage: "ПФ 2",
      etk_position: 3, etk_stage: "Опрессовка терминалов", operator_names: ["Анна"], start: "2026-10-19T09:00:00+03:00", finish: "2026-10-19T12:30:00+03:00",
    }],
  }, {
    machine: "ОБ.098", label: "ОБ.098 Автомат резки", units: 1, known: false,
    days: [day(DAYS[0], 0, 7 * 3600), day(DAYS[1], 0, 7 * 3600), day(DAYS[2], 0, 7 * 3600)], items: [],
  }],
  warnings: ["Станок ОБ.098 не найден в БД.ОБ — считаю, что он один"],
};

it("shows machines by day in percent of all units, overload, the day's work and the reference warning", async () => {
  const calls = serve(() => json(LOAD));
  render(<MachineLoadGrid holidays={[]} days={DAYS} />);

  const press = (await screen.findByText("ОБ.027 Пресс")).closest("tr")!;
  expect(calls[0].path).toBe("/api/production/machine-load?date_from=2026-10-19&date_to=2026-10-21");
  expect(within(press).getByText("2 ед. · 11,8 ч за 1 нед.")).toBeInTheDocument();
  const monday = within(press).getByRole("button", { name: "Загрузка ОБ.027 19.10" });
  expect(monday).toHaveTextContent("25 %");
  expect(monday.closest("td")).toHaveClass("mb-cell", "low");
  expect(monday).toHaveAttribute("title", "ОБ.027 Пресс, 19.10: занято 3,5 из 14 ч (25 %)");
  const wednesday = within(press).getByRole("button", { name: "Загрузка ОБ.027 21.10" });
  expect(wednesday.closest("td")).toHaveClass("over");
  expect(wednesday.getAttribute("title")).toContain("одновременно 3 из 2 ед.");

  const cutter = screen.getByText("ОБ.098 Автомат резки").closest("tr")!;
  expect(within(cutter).getByText("1 ед. · нет в БД.ОБ · свободен")).toBeInTheDocument();
  expect(screen.getByRole("note")).toHaveTextContent("Станок ОБ.098 не найден в БД.ОБ — считаю, что он один");

  fireEvent.click(monday);
  const detail = screen.getByRole("group", { name: "Что на станке: ОБ.027, 19.10" });
  expect(detail).toHaveTextContent("ПЗ-0012 · КБ1 этап 2 «ПФ 2» · оп. ЭТК 3 «Опрессовка терминалов» · 19.10 09:00–12:30 · Анна");
});

it("says when no machine is busy and shows a failure honestly", async () => {
  serve(() => json({ ...LOAD, machines: [], warnings: [] }));
  const { unmount } = render(<MachineLoadGrid holidays={[]} days={DAYS} />);
  expect(await screen.findByText("За 19.10–21.10 станки не заняты: в плане нет операций ЭТК со станком в «Оборудовании».")).toBeInTheDocument();
  unmount();
  vi.restoreAllMocks();

  serve(() => json({ detail: "Нет доступа" }, 403));
  render(<MachineLoadGrid holidays={[]} days={DAYS} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Нет доступа");
});

it("pages by week and shows the period when the screen does not give the days", async () => {
  const calls = serve((call) => {
    const query = new URL(call.path, "http://erp").searchParams;
    return json({ date_from: query.get("date_from"), date_to: query.get("date_to"), machines: [], warnings: [] });
  });
  const onWeekChange = vi.fn();
  render(<MachineLoadGrid holidays={[]} week={0} onWeekChange={onWeekChange} />);
  await screen.findByText(/станки не заняты/);
  expect(calls[0].path).toMatch(/date_from=\d{4}-\d{2}-\d{2}&date_to=\d{4}-\d{2}-\d{2}$/);
  expect(screen.getByTitle(/Период загрузки станков/)).toHaveTextContent("2 нед.");
  fireEvent.click(screen.getByRole("button", { name: "неделя →" }));
  expect(onWeekChange).toHaveBeenCalledWith(1);
});

it("names a machine by its kind, the model goes to the second line", () => {
  expect(machineName({ machine: "ОБ.005", label: "ОБ.005 SR-W01 | Машина крутки" })).toEqual({ title: "ОБ.005 Машина крутки", model: "SR-W01" });
  expect(machineName({ machine: "ОБ.027", label: "ОБ.027 Пресс" })).toEqual({ title: "ОБ.027 Пресс", model: "" });
});

it("levels: off day, free, part, full and overload", () => {
  expect(machineLevel(day(DAYS[0], 0, 0))).toBe("off");
  expect(machineLevel(day(DAYS[0], 0))).toBe("free");
  expect(machineLevel(day(DAYS[0], 0.5 * 50_400))).toBe("low");
  expect(machineLevel(day(DAYS[0], 0.95 * 50_400))).toBe("full");
  expect(machineLevel(day(DAYS[0], 100, 50_400, { over: true }))).toBe("over");
  expect(machineLevel(day(DAYS[0], 600, 0))).toBe("over");  // работа в нерабочий день
});

it("warns that the machine is busy in the chosen window without blocking anything", async () => {
  const text = "ОБ.027 занят 19.10 09:00–12:30: ПЗ-0012 · КБ1, этап 2 «ПФ 2» 19.10 09:00–12:30 (Анна)";
  const calls = serve(() => json({ conflicts: [{ machine: "ОБ.027", label: "ОБ.027 Пресс", units: 1, start: "", finish: "", text, busy: [] }] }));
  const start = new Date(2026, 9, 19, 9, 0).getTime();
  const finish = new Date(2026, 9, 19, 11, 30).getTime();
  render(<MachineWarning taskId="t2" operationId="o2" operatorId="u2" quantity={40} start={start} finish={finish} />);

  expect(await screen.findByRole("status")).toHaveTextContent(text);
  expect(calls[0]).toEqual({
    path: "/api/production/machine-load/check", method: "POST",
    body: { task_id: "t2", operation_id: "o2", operator_id: "u2", quantity: 40, start: "2026-10-19T09:00", finish: "2026-10-19T11:30" },
  });
});

it("stays silent when the machine is free, the window is empty or the check fails", async () => {
  const calls = serve(() => json({ conflicts: [] }));
  const start = new Date(2026, 9, 19, 9, 0).getTime();
  const { rerender } = render(<MachineWarning taskId="t2" operationId="o2" operatorId={null} quantity={null} start={start} finish={start + 3_600_000} />);
  await vi.waitFor(() => expect(calls).toHaveLength(1));
  expect(screen.queryByRole("status")).toBeNull();

  rerender(<MachineWarning taskId="t2" operationId="o2" operatorId={null} quantity={null} start={null} finish={null} />);
  expect(calls).toHaveLength(1);

  vi.restoreAllMocks();
  const failed = serve(() => json({ detail: "Ошибка" }, 500));
  rerender(<MachineWarning taskId="t2" operationId="o2" operatorId={null} quantity={null} start={start} finish={start + 7_200_000} />);
  await vi.waitFor(() => expect(failed).toHaveLength(1));
  expect(screen.queryByRole("status")).toBeNull();
  expect(wallTime(start)).toBe("2026-10-19T09:00");
});

it("master board: a day picked for a fitter warns that the press is busy and still offers to assign; the board shows the machines", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-05T12:00:00").getTime());
  const text = "ОБ.027 занят 06.10 09:00–10:00: ПЗ-0001 · КБ1, этап 1 «Опрессовка» 06.10 09:00–10:00 (Анна)";
  const calls = serve((call) => {
    if (call.path === "/api/production/machine-load/check") return json({ conflicts: [{ machine: "ОБ.027", label: "ОБ.027 Пресс", units: 1, start: "", finish: "", text, busy: [] }] });
    if (call.path.startsWith("/api/production/machine-load?")) {
      const query = new URL(call.path, "http://erp").searchParams;
      return json({ date_from: query.get("date_from"), date_to: query.get("date_to"), machines: [], warnings: [] });
    }
    return json({ items: [], statuses: [] });
  });
  const operation = {
    id: "o9", position: 1, code: "PRS", stage: "Опрессовка", description: "", seconds_per_unit: "6", setup_seconds: "600", norm_seconds: 1200,
    operator_ids: [], planned_start: null, planned_finish: null, assigned_by: null, shares: [], required_quantity: "100", state: "не начата",
  } as unknown as ProductionOperation;
  const board = { id: "t9", number: "ПЗ-0009", version: 1, created_at: "2026-10-01T08:00:00", project: "AAA", item: "КБ9", name: "", quantity: 100,
    fulfillment_request_id: null, deadline: null, status: "Запущено", launched_by: "Мастер", handed_to: "", planned_start: null, planned_finish: null,
    assigned: 0, operations: [operation] } as unknown as ProductionTask;
  forgetOpenProject();
  render(<MasterBoard tasks={[board]} operators={[{ id: "p1", name: "Волков Дмитрий", site: "ЦАР" }]} skills={[]} absences={[]} canEdit busy=""
    mutate={async () => {}} openTask={() => {}} />);

  expect(await screen.findByText(/станки не заняты/)).toBeInTheDocument();
  fireEvent.click(screen.getByText("AAA"));
  fireEvent.click(screen.getByRole("button", { name: "Опрессовка" }));
  fireEvent.click(screen.getByRole("button", { name: "День Волков Дмитрий 06.10" }));

  // Под кнопкой дня и у «Назначить только …» (сегодня с 12:00) — у каждого своё окно.
  const shown = await screen.findAllByText(text);
  expect(shown.some((item) => item.closest(".mb-day-hint"))).toBe(true);
  expect(shown.some((item) => item.closest(".mb-only"))).toBe(true);
  expect(screen.getByRole("button", { name: /^Назначить на 06\.10 с 09:00/ })).toBeEnabled();
  const checks = calls.filter((call) => call.path === "/api/production/machine-load/check").map((call) => call.body);
  expect(checks).toContainEqual(expect.objectContaining({ task_id: "t9", operation_id: "o9", operator_id: "p1", quantity: 100, start: "2026-10-06T09:00" }));
  expect(checks).toContainEqual(expect.objectContaining({ operator_id: "p1", quantity: 100, start: expect.stringMatching(/^2026-10-05T12:/) }));
  vi.useRealTimers();
});
