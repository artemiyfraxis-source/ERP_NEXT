import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { machineGood, type CuttingMember } from "./cuttingGroups";
import type { MachineControl, MachineRun, MachineRunState, MachineWindow } from "./machineControlApi";
import type { Ob019Live } from "./machinesApi";
import type { ManualJob } from "./manualJobsApi";
import { Ob019SettingsPanel } from "./Ob019SettingsPanel";

/* «Выполнение задания» на карточке ОБ.019 (владелец 19.11.2026). */

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const WINDOW: MachineWindow = { inventory: "ОБ.019", settings: [
  { title: "Задание", fields: [{ label: "Тираж", value: 12, unit: "шт", read: true, key: "total_set", kind: "u32" }] },
  { title: "Провод и зачистка", fields: [
    { label: "Длина провода", value: 325, unit: "мм", read: true, key: "length_mm", kind: "u32" },
    { label: "Зачистка начала", value: 5, unit: "мм", read: true, key: "strip_front_mm", kind: "u32" }] },
] };
const CONTROL: MachineControl = { problem: "", state: 102, commands: [], offsets: [], can_edit: true, can_control: true };
const MANUAL: ManualJob = {
  id: "m1", number: "Р-000003", plc_number: "R-000003", equipment_id: "u19", inventory: "ОБ.019", machine: "Линия",
  status: "running", status_text: "Идёт", tone: "info", purpose: "Проба для ОТК", wire: "ПВАМ 0,35", contact: "", length_mm: "100",
  quantity_planned: 5, reference_id: null, count_source: "manual", count_text: "Вручную", good: null, defect: null, defect_reason: "",
  machine_count: null, material: "pending", material_text: "", material_tone: "warn", material_note: "", cancel_reason: "",
  operator: "Слава", finished_by: "", started_at: "2026-11-19T09:40:00+03:00", finished_at: null, version: 2,
};

function makeRun(stage: MachineRun["stage"], extra: Partial<MachineRun> = {}): MachineRun {
  return { id: "w1", kind: "cutting", label: "ГР-0007 · ПВАМ 0,35 · 325,00 мм", group_id: "g1", length_mm: "325", job_id: null,
    quantity: 12, write: { status: "done", status_text: "Записано и проверено", tone: "ok", error: "" },
    verified_at: "2026-11-19T09:58:00+03:00", closed_at: null, closed_why: "", machine_count: null, closed_reset: false,
    created_by: "Слава", created_at: "2026-11-19T09:58:00+03:00", stage, counter_start: 100, counter: 100, done: 0, reset: false,
    state: 102, alarm: 0, total_set: 12, ...extra };
}

function live(code: number, text: string, alarm = 0): Ob019Live {
  return { state: { code, text, tone: "ok" }, alarm: { code: alarm, text: alarm === 5 ? "Тираж готов" : "Аварий нет", tone: "neutral" },
    mode: { code: 2, text: "Автомат" }, job: { total_set: 12, total_finished: 0, batch_set: 0, batch_finished: 0, uph: 0, program: 1,
      order_quantity: 12, order_finished: 0, order_number: null, product: null, order_running: false },
    wire: { length_mm: 325, strip_front_mm: 5, pull_front_mm: 0, strip_back_mm: 5, pull_back_mm: 0, diameter_mm: 0.8, knife_back_mm: 0.1 } };
}

function server(state: Partial<MachineRunState> = {}) {
  const posts: { url: string; body: unknown }[] = [];
  const run: MachineRunState = { run: null, manual: null, link_ok: true, problem: "", can_edit: true, can_control: true, ...state };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = decodeURIComponent(String(input));
    if (init?.method === "POST") {
      posts.push({ url, body: JSON.parse(String(init.body)) });
      if (url.endsWith("/run/close")) return json({ run: { ...run.run, stage: "closed", machine_count: 5 }, manual: null });
      return json({ id: "c1", status: "queued", status_text: "Ждёт агента станка", tone: "info", purpose: "transfer",
        summary: "Передать на станок", fields: [], error: "", created_by: "Слава", created_at: "2026-11-19T10:00:00+03:00", finished_at: null });
    }
    if (url.endsWith("/control")) return json(CONTROL);
    if (url.endsWith("/run")) return json(run);
    if (url.endsWith("/transfer")) return json({ choices: [{ key: "g1:325", group_id: "g1", number: "ГР-0007", project: "708К.",
      wire: "ПВАМ 0,35", length_mm: "325", quantity: "12" }] });
    return json(WINDOW);
  });
  return posts;
}

afterEach(() => { vi.restoreAllMocks(); });

it("pult open: the run block does not repeat the status banner; the empty «Задание» shows the last known job greyed", async () => {
  const text = "Открыт пульт станка на ПК — закройте его, чтобы ERP могла управлять станком";
  server({ link_ok: false, problem: text });
  const last = live(102, "Готов, ждёт пуска");
  render(<Ob019SettingsPanel inventory="ОБ.019" old live={{ ...last, job: { ...last.job, order_number: "GR-0007", product: "PVAM 0,35 L325,00" } }} />);
  const pick = await screen.findByLabelText("Задание");
  await waitFor(() => expect(within(pick).getAllByRole("option")[0]).toHaveTextContent("последнее: GR-0007 · PVAM 0,35 L325,00"));
  expect(pick).toHaveClass("is-last");
  // No job on the machine, none picked: no empty «Выполнение задания» panel, no second banner.
  expect(screen.queryByRole("region", { name: "Выполнение задания" })).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByText(text)).toBeNull();
  expect(screen.queryByText(/PXS_HMI/)).toBeNull();
});

it("after a job is picked the run block says its write, not an instruction", async () => {
  server();
  let release: (value: Response) => void = () => undefined;
  const base = vi.mocked(globalThis.fetch).getMockImplementation()!;
  vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
    if (decodeURIComponent(String(input)).includes("/transfer?group_id")) return new Promise<Response>((resolve) => { release = resolve; });
    return base(input, init);
  });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const pick = await screen.findByLabelText("Задание");
  await waitFor(() => expect(within(pick).getByRole("option", { name: /ГР-0007/ })).toBeInTheDocument());
  fireEvent.change(pick, { target: { value: "g1:325" } });
  const block = await screen.findByRole("region", { name: "Выполнение задания" }, { timeout: 3000 });
  expect(await within(block).findByText("записывается на станок…", {}, { timeout: 3000 })).toBeInTheDocument();
  expect(screen.queryByText(/Значения задания уходят на станок сразу при выборе/)).toBeNull();
  release(json({ detail: "нет" }, 404));
});

it("transfers the running manual job at once when picked: the server writes its length, quantity and order number", async () => {
  const posts = server({ manual: MANUAL });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const pick = await screen.findByLabelText("Задание");
  await waitFor(() => expect(within(pick).getByRole("option", { name: /Р-000003/ })).toBeInTheDocument());
  await waitFor(() => expect(screen.getByLabelText("Длина провода")).toHaveValue("325,00"));
  fireEvent.change(pick, { target: { value: "manual:m1" } });
  expect(screen.getByText("На станок: заказ R-000003")).toBeInTheDocument();
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toEqual({ url: "/api/production/machines/card/ОБ.019/run",
    body: { kind: "manual", job_id: "m1", confirm_state: 102 } });
  expect(screen.queryByRole("alertdialog")).toBeNull();
});

it("offers a new manual job inline only while none runs on the machine", async () => {
  server({ manual: MANUAL });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const pick = await screen.findByLabelText("Задание");
  await waitFor(() => expect(within(pick).getByRole("option", { name: /Новая ручная работа — сначала завершите/ })).toBeDisabled());
});

it("a verified job says ✓ on the machine; the run block keeps only «Завершить» (Стоп and Пуск are the command row's)", async () => {
  const posts = server({ run: makeRun("ready") });
  const { rerender } = render(<Ob019SettingsPanel inventory="ОБ.019" live={live(102, "Готов, ждёт пуска")} />);
  const now = await screen.findByRole("status", { name: "Задание на станке" });
  expect(now).toHaveTextContent("✓ на станке, проверено · счёт с 100");
  for (const name of ["Пуск", "Стоп", "Сброс"]) expect(within(now).queryByRole("button", { name })).toBeNull();
  expect(within(now).getByRole("button", { name: "Завершить" })).toBeInTheDocument();
  expect(posts).toEqual([]);

  vi.restoreAllMocks();
  server({ run: makeRun("running", { done: 5, counter: 105, state: 103 }) });
  rerender(<Ob019SettingsPanel inventory="ОБ.019" key="2" live={live(103, "Работает")} />);
  // The count once in the block — the bar; the line says how it goes, not «сделано 5 из 12» again.
  expect(await screen.findByText("5 из 12 шт")).toBeInTheDocument();
  expect(screen.getByRole("progressbar", { name: "Сделано" })).toHaveAttribute("aria-valuenow", "5");
  const running = screen.getByRole("status", { name: "Задание на станке" });
  expect(running).toHaveTextContent("идёт · счёт по станку");
  expect(running).not.toHaveTextContent(/сделано 5 из 12/);
  expect(within(running).queryByRole("button", { name: "Стоп" })).toBeNull();
});

it("finishes a manual job with the machine count when the machine says «Тираж готов»", async () => {
  const posts = server({ manual: { ...MANUAL, count_source: "plc", count_text: "Счётчик станка" },
    run: makeRun("finished", { kind: "manual", job_id: "m1", group_id: null, label: "Р-000003 · ПВАМ 0,35 · 100 мм", quantity: 5,
      done: 5, counter: 105, state: 107, alarm: 5 }) });
  render(<Ob019SettingsPanel inventory="ОБ.019" live={live(107, "Тираж готов", 5)} />);
  const finish = await screen.findByRole("group", { name: "Завершение задания" });
  expect(within(finish).getByText("По станку: 5 шт")).toBeInTheDocument();
  expect(within(finish).getByLabelText("Годные, шт")).toHaveValue("5");
  fireEvent.change(within(finish).getByLabelText("Годные, шт"), { target: { value: "4" } });
  fireEvent.change(within(finish).getByLabelText("Брак, шт"), { target: { value: "1" } });
  expect(within(finish).getByRole("button", { name: "Завершить работу" })).toBeDisabled();
  fireEvent.change(within(finish).getByLabelText("Причина брака"), { target: { value: "надрез" } });
  fireEvent.click(within(finish).getByRole("button", { name: "Завершить работу" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toEqual({ url: "/api/production/machines/card/ОБ.019/run/close", body: { run_id: "w1", version: 2, good: 4,
    defect: 1, defect_reason: "надрез", material: "pending", material_note: "" } });
});

it("asks for a manual count when the machine counter was reset, and sends a cutting job to «Сдать группу»", async () => {
  const posts = server({ run: makeRun("reset", { done: null, counter: 3, reset: true, state: 100 }) });
  render(<Ob019SettingsPanel inventory="ОБ.019" live={live(100, "Не сброшен — нужен сброс")} />);
  expect(await screen.findByText(/Счётчик станка сброшен — введите количество вручную; «Пуск» — только после новой передачи/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Пуск" })).toBeNull();
  const finish = await screen.findByRole("group", { name: "Завершение задания" });
  expect(within(finish).getByText(/количество вручную/)).toBeInTheDocument();
  fireEvent.click(within(finish).getByRole("button", { name: "Завершить задание" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toEqual({ run_id: "w1" });
  expect(await screen.findByText(/подтвердите в «Мои задания» → «Сдать группу»/)).toBeInTheDocument();
});

it("spreads the machine count over the КБ of that length, never above what each still needs", () => {
  const row = (id: string, length: string, planned: string, blanks: string, good = "0"): CuttingMember => ({
    id, cut_length_mm: length, operation_quantity: planned, blank_quantity: blanks, good, defects: "0" } as CuttingMember);
  const rows = [row("a", "325", "10", "10", "2"), row("b", "325,00", "4", "8"), row("c", "150", "5", "5")];
  expect(machineGood(rows, "325", 12)).toEqual({ a: "8", b: "2" });
  expect(machineGood(rows, "325", 3)).toEqual({ a: "3", b: "0" });
});

it("voids a run whose counter moved while it was written: a refusal, no «Пуск», no «Завершить»", async () => {
  server({ run: makeRun("moved") });
  render(<Ob019SettingsPanel inventory="ОБ.019" live={live(102, "Готов, ждёт пуска")} />);
  const now = await screen.findByRole("status", { name: "Задание на станке" });
  expect(now).toHaveTextContent("отказ: счётчик станка сдвинулся, пока задание записывалось, — выберите задание ещё раз");
  expect(within(now).queryByRole("button")).toBeNull();
});

it("writes a manual job again only for the rest and says how much is done", async () => {
  const posts = server({ manual: { ...MANUAL, quantity_planned: 10 },
    run: makeRun("running", { kind: "manual", job_id: "m1", group_id: null, label: "Р-000003", quantity: 10, done: 4, state: 102 }) });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const pick = await screen.findByLabelText("Задание");
  await waitFor(() => expect(within(pick).getByRole("option", { name: /Р-000003/ })).toBeInTheDocument());
  await waitFor(() => expect(screen.getByLabelText("Длина провода")).toHaveValue("325,00"));
  fireEvent.change(pick, { target: { value: "manual:m1" } });
  // The server writes only the rest (production/machine_runs.py done_before); the card says how much is done.
  expect(await screen.findByText("Уже сделано 4 из 10 — на станок уходит остаток", {}, { timeout: 3000 })).toBeInTheDocument();
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toEqual({ kind: "manual", job_id: "m1", confirm_state: 102 });
});
