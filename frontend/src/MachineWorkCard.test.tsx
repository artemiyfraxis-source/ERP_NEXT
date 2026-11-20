import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { MachineQueueRow } from "./machineQueueApi";
import type { MachineRow } from "./machinesApi";
import { MachineWorkCard } from "./MachineWorkCard";
import type { MachineDowntime, ManualJob } from "./manualJobsApi";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
afterEach(() => { vi.restoreAllMocks(); });

const ROW: MachineRow = {
  key: "machine:u71", kind: "machine", equipment_id: "u71", inventory: "ОБ.071.1", name: "Паяльная станция", pc: "", ip: "", service: "",
  version: "", link: { code: "not_connected", text: "Нет подключения к ERP", tone: "neutral" }, last_seen: null, device: "",
  state: { text: "—", tone: "neutral" }, card: false, others: "", article: "T21", site: "ЦАР", operations: ["Пайка"], family: "Пайка и усадка",
  controlled: false, setup: { code: "none", label: "нет" }, pending_setup: 0,
};
const QROW: MachineQueueRow = {
  key: "q1", task_id: "t1", operation_id: "o1", task_number: "ПЗ-1", project: "708К.", item: "КБ3", pf: "ПФ-3", etk_position: 4,
  step: "Пайка", planned: "40", done: "3", left: "37",
  etk: [{ label: "Жало", value: "T21-BC2", source: "из ЭТК" }, { label: "Температура", value: "", source: "" }],
  warnings: ["нет в ЭТК: температура"], job: null,
};
const JOB: ManualJob = {
  id: "j1", number: "Р-000123", plc_number: "R-000123", equipment_id: "u71", inventory: "ОБ.071.1", machine: "Паяльная станция", status: "running",
  status_text: "Идёт", tone: "info", purpose: "708К. · КБ3 · Пайка", wire: "", contact: "", length_mm: null, quantity_planned: 40, reference_id: null,
  count_source: "manual", count_text: "вручную", good: null, defect: null, defect_reason: "", machine_count: null, material: "pending",
  material_text: "", material_tone: "neutral", material_note: "", cancel_reason: "", operator: "Петров", finished_by: "",
  started_at: "2026-11-21T10:12:00+03:00", finished_at: null, version: 2, progress_quantity: 18, progress_at: null, progress_over_plan: false,
  source: { task_id: "t1", operation_id: "o1", etk_position: 4, label: "708К. · КБ3 · Пайка" },
};
const DOWNTIME: MachineDowntime = {
  id: "d1", equipment_id: "u71", machine: "ОБ.071.1", reason: "no_material", reason_name: "Нет материала", comment: "",
  started_at: "2026-11-21T11:40:00+03:00", started_by: "Петров", ended_at: null, ended_by: "", open: true, seconds: 60,
};

interface Setup { rows?: MachineQueueRow[]; jobs?: ManualJob[]; downtimes?: MachineDowntime[]; cut?: number; canEdit?: boolean; queueStatus?: number }
type Post = { url: string; body: unknown };

function cutRow(index: number) {
  return { key: `c${index}`, task_id: "t", operation_id: "o", task_number: "", project: "801К.", item: "КБ1", group_number: null, wire: null,
    wire_name: null, length_mm: "100", planned: "10", done: "0", left: "10", strip_a: null, strip_a_mode: null, strip_b: null, strip_b_mode: null,
    laser_mark: null, marking_mode: null, operator_names: [], warnings: [], blocked: null, setup_change: false, setup_text: "" };
}

function mock(setup: Setup = {}, posts: Post[] = []) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = decodeURIComponent(String(input));
    if (init?.method === "POST") {
      posts.push({ url, body: init.body ? JSON.parse(String(init.body)) : null });
      return json(url.endsWith("/downtime") ? { downtime: DOWNTIME } : JOB);
    }
    if (url.includes("/machine-queue")) {
      return setup.queueStatus ? json({ detail: "Нет справочников" }, setup.queueStatus)
        : json({ equipment_id: "u71", inventory: "ОБ.071.1", version: "v", rows: setup.rows ?? [] });
    }
    if (url.includes("/cutting-queue")) {
      return json({ machine_id: null, version: "v", rows: Array.from({ length: setup.cut ?? 0 }, (_, index) => cutRow(index)) });
    }
    if (url.includes("/manual-jobs/options")) {
      return json({ machines: [{ id: "u71", inventory: "ОБ.071.1", name: "Паяльная станция", running: null }], wires: [], contacts: [],
        references: [], can_edit: true, reasons: [{ key: "no_material", name: "Нет материала" }, { key: "other", name: "Другое" }] });
    }
    if (url.includes("/manual-jobs")) {
      return json({ date_from: "", date_to: "", rows: setup.jobs ?? [], can_edit: setup.canEdit ?? true, downtimes: setup.downtimes ?? [] });
    }
    return json({});
  });
}
const open = (canWrite = true, row = ROW) => render(<MachineWorkCard row={row} canWrite={canWrite} close={() => undefined} openSetup={() => undefined} />);
const status = () => screen.getByRole("status", { name: "Состояние станка" });

it("free machine: «Свободен», the queue with warnings and the amber «не задано» ETK chip", async () => {
  mock({ rows: [QROW] });
  open();
  await screen.findByText("708К.");
  expect(status().textContent).toContain("Свободен · без связи с ERP — счёт вручную");
  expect(screen.getByRole("tab", { name: "Очередь станка (1)" })).toBeTruthy();
  expect(screen.queryByRole("tab", { name: /Очередь резки/ })).toBeNull();  // family «Пайка и усадка», nothing to cut
  expect(screen.getByText("нет в ЭТК: температура")).toBeTruthy();
  const etk = screen.getByRole("region", { name: "Режим по ЭТК" });
  expect(within(etk).getByText("T21-BC2")).toBeTruthy();
  const missing = within(etk).getByText("не задано").closest("li")!;
  expect(missing.className).toContain("is-missing");
  expect(missing.textContent).toContain("Температура");
});

it("«Начать» starts a manual job with the queue row as its source and planned = left", async () => {
  const posts: Post[] = [];
  mock({ rows: [QROW] }, posts);
  open();
  fireEvent.click(await screen.findByRole("button", { name: "Начать" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].url).toBe("/api/production/manual-jobs");
  expect(posts[0].body).toEqual({ equipment_id: "u71", purpose: "708К. · КБ3 · Пайка", wire: "", contact: "", length_mm: "", quantity_planned: 37,
    reference_id: null, source: { task_id: "t1", operation_id: "o1", etk_position: 4 } });
});

it("running job: status line, over-plan warns but is accepted, «Записать» posts the total, «Готово» finishes without a material issue", async () => {
  const posts: Post[] = [];
  mock({ rows: [{ ...QROW, job: { id: "j1", number: "Р-000123", progress_quantity: 18 } }], jobs: [JOB] }, posts);
  open();
  await screen.findByText("в работе Р-000123");
  expect(status().textContent).toContain("Работает: Р-000123 · 708К. · КБ3 · Пайка · с 10:12 (Петров)");
  const field = screen.getByRole("spinbutton", { name: "Сделано, шт" }) as HTMLInputElement;
  expect(field.value).toBe("18");
  expect(screen.getByText("из 40")).toBeTruthy();
  expect(screen.getByText("Сдать ПФ — в «Заданиях»")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "+10" }));
  expect(field.value).toBe("28");
  fireEvent.change(field, { target: { value: "45" } });
  expect(screen.getByText(/Больше плана \(40\)/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "Записать" }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Записать" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toEqual({ url: "/api/production/manual-jobs/j1/progress", body: { version: 2, quantity: 45 } });
  fireEvent.click(screen.getByRole("button", { name: "Готово" }));
  const form = await screen.findByRole("form", { name: "Завершить Р-000123" });
  fireEvent.change(within(form).getByLabelText("Брак"), { target: { value: "2" } });
  expect((within(form).getByRole("button", { name: "Завершить работу" }) as HTMLButtonElement).disabled).toBe(true);  // нужна причина брака
  fireEvent.change(within(form).getByLabelText("Причина"), { target: { value: "перегрев" } });
  fireEvent.click(within(form).getByRole("button", { name: "Завершить работу" }));
  await waitFor(() => expect(posts).toHaveLength(2));
  expect(posts[1].url).toBe("/api/production/manual-jobs/j1/finish");
  expect(posts[1].body).toEqual({ version: 2, good: 18, defect: 2, defect_reason: "перегрев", material: "not_issued", material_note: "Материал из задания" });
});

it("downtime: the status says why and since when; «Закончить простой» stops it", async () => {
  const posts: Post[] = [];
  mock({ downtimes: [DOWNTIME] }, posts);
  open();
  await waitFor(() => expect(status().textContent).toContain("Простой: Нет материала с 11:40"));
  fireEvent.click(screen.getByRole("button", { name: "Закончить простой" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].url).toBe("/api/production/manual-jobs/downtime/d1/stop");
});

it("a downtime reason «Другое» needs a comment", async () => {
  const posts: Post[] = [];
  mock({}, posts);
  open();
  await screen.findByText("В очереди станка пусто", { exact: false });
  fireEvent.click(screen.getByRole("button", { name: "Простой" }));
  const form = await screen.findByRole("form", { name: "Простой станка" });
  await within(form).findByRole("option", { name: "Другое" });
  fireEvent.change(within(form).getByLabelText("Причина"), { target: { value: "other" } });
  const submit = within(form).getByRole("button", { name: "Начать простой" }) as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  fireEvent.change(within(form).getByLabelText("Комментарий"), { target: { value: "ждём наладчика" } });
  expect(submit.disabled).toBe(false);
  fireEvent.click(submit);
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toEqual({ equipment_id: "u71", reason: "other", comment: "ждём наладчика" });
});

it("a viewer sees the queue without write buttons; a queue error is shown", async () => {
  mock({ rows: [QROW], canEdit: false });
  const { unmount } = open(false);
  await screen.findByText("708К.");
  expect(screen.queryByRole("button", { name: "Начать" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Простой" })).toBeNull();
  unmount();
  vi.restoreAllMocks();
  mock({ queueStatus: 503 });
  open();
  expect((await screen.findAllByRole("alert"))[0].textContent).toContain("Нет справочников");
});

it("shows the cutting-queue tab when the unit has cuts, or always for a cutting family", async () => {
  mock({ cut: 2 });
  const { unmount } = open();
  expect(await screen.findByRole("tab", { name: "Очередь резки (2)" })).toBeTruthy();
  unmount();
  vi.restoreAllMocks();
  mock({ cut: 0 });
  open(true, { ...ROW, family: "Резка" });
  expect(await screen.findByRole("tab", { name: "Очередь резки (0)" })).toBeTruthy();
});
