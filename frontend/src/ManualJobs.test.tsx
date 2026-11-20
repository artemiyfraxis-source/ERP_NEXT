import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { AccessMode, Principal } from "./api";
import type { MachineRow } from "./machinesApi";
import { MachinesPage } from "./MachinesPage";
import { ManualJobsPage } from "./ManualJobsPage";
import { ManualJobWindow } from "./ManualJobWindow";
import type { ManualJob, ManualJobOptions } from "./manualJobsApi";
import { visibleNav } from "./navigation";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.restoreAllMocks(); });

const RUNNING: ManualJob = {
  id: "j1", number: "Р-000001", plc_number: "R-000001", equipment_id: "u19", inventory: "ОБ.019", machine: "Автомат резки",
  status: "running", status_text: "Идёт", tone: "info", purpose: "Проба на образцах", wire: "ПВАМ 0,35", contact: "",
  length_mm: "325,5", quantity_planned: 20, reference_id: null, count_source: "manual", count_text: "Вручную",
  good: null, defect: null, defect_reason: "", machine_count: null, material: "pending", material_text: "",
  material_tone: "warn", material_note: "", cancel_reason: "", operator: "Слава", finished_by: "",
  started_at: "2026-11-19T10:00:00+03:00", finished_at: null, version: 1,
};
const DONE: ManualJob = { ...RUNNING, id: "j2", number: "Р-000002", inventory: "ОБ.099", status: "done", status_text: "Завершена",
  tone: "ok", good: 18, defect: 2, defect_reason: "надрез", material: "not_issued", material_text: "Не списывать",
  material_tone: "neutral", material_note: "обрезки", finished_at: "2026-11-19T11:00:00+03:00", version: 2 };
const OPTIONS: ManualJobOptions = {
  machines: [{ id: "u19", inventory: "ОБ.019", name: "Автомат резки", running: null },
    { id: "u99", inventory: "ОБ.099", name: "Пресс BZW", running: "Р-000002" }],
  wires: ["ПВАМ 0,35"], contacts: [{ label: "DJ621-E2.8B", maker: "Китай" }],
  references: [{ id: "r1", equipment_id: "u19", wire: "ПВАМ 0,5", contact: "", version: 3, label: "ПВАМ 0,5 · версия 3" }],
  can_edit: true,
};

function serve(calls: { url: string; body?: unknown }[], list: ManualJob[] = [RUNNING, DONE]) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = decodeURIComponent(String(input));
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.includes("/setup-model/")) return json({ detail: "нет" }, 404);
    if (url.includes("/manual-jobs/options")) return json(OPTIONS);
    if (url.endsWith("/finish")) return json({ ...DONE, id: "j1", number: "Р-000001" });
    if (url.endsWith("/cancel")) return json({ ...RUNNING, status: "cancelled", status_text: "Отменена", tone: "neutral", cancel_reason: "брак провода", version: 2 });
    if (url.endsWith("/manual-jobs") && init?.method === "POST") return json({ ...RUNNING, id: "j9", number: "Р-000009" });
    if (url.includes("/setup-model/")) return json({ detail: "нет" }, 404);
    if (url.includes("/manual-jobs?")) return json({ date_from: "2026-11-16", date_to: "2026-11-19", rows: list, can_edit: true });
    return json({ now: "2026-11-19T10:00:00+03:00", rows: [] });
  });
}

it("lists the jobs of the period and finishes a running one with the material decision", async () => {
  const calls: { url: string; body?: unknown }[] = [];
  serve(calls);
  render(<ManualJobsPage />);
  expect((await screen.findAllByText("Р-000001")).length).toBeGreaterThan(0);
  expect(screen.getByRole("group", { name: "Период" })).toBeTruthy();
  expect(screen.getByLabelText("Начало периода")).toBeTruthy();
  expect(calls[0].url).toMatch(/date_from=\d{4}-\d{2}-\d{2}&date_to=\d{4}-\d{2}-\d{2}$/);
  expect(screen.getAllByText("Не списывать").length).toBeGreaterThan(0);
  fireEvent.click(screen.getAllByText("Р-000001")[0]);
  const dialog = await screen.findByRole("dialog", { name: "Ручная работа Р-000001" });
  const finish = within(dialog).getByRole("button", { name: "Завершить работу" }) as HTMLButtonElement;
  expect(finish.disabled).toBe(true);
  const issue = within(dialog).getByRole("radio", { name: /Списать с партии/ }) as HTMLInputElement;
  expect(issue.disabled).toBe(true);
  expect(within(dialog).getByText("списывает склад")).toBeTruthy();
  fireEvent.change(within(dialog).getByLabelText("Годные, шт"), { target: { value: "18" } });
  fireEvent.change(within(dialog).getByLabelText("Брак, шт"), { target: { value: "2" } });
  fireEvent.click(within(dialog).getByRole("radio", { name: "Не списывать" }));
  expect(within(dialog).getByText("Укажите причину брака")).toBeTruthy();
  fireEvent.change(within(dialog).getByLabelText("Причина брака"), { target: { value: "надрез" } });
  expect(within(dialog).getByText("Укажите, почему материал не списывается")).toBeTruthy();
  fireEvent.change(within(dialog).getByLabelText("Почему не списывать"), { target: { value: "обрезки" } });
  expect(finish.disabled).toBe(false);
  fireEvent.click(finish);
  await waitFor(() => expect(calls.some((call) => call.url.endsWith("/manual-jobs/j1/finish"))).toBe(true));
  expect(calls.find((call) => call.url.endsWith("/finish"))?.body).toEqual({ version: 1, good: 18, defect: 2,
    defect_reason: "надрез", material: "not_issued", material_note: "обрезки" });
  await waitFor(() => expect(within(dialog).queryByRole("button", { name: "Завершить работу" })).toBeNull());
  expect(within(dialog).getByText("Не списывать: обрезки")).toBeTruthy();
});

it("cancels a running job only with a reason", async () => {
  const calls: { url: string; body?: unknown }[] = [];
  serve(calls);
  render(<ManualJobWindow job={RUNNING} canEdit close={() => undefined} />);
  fireEvent.click(screen.getByRole("button", { name: "Отменить работу…" }));
  const cancel = screen.getByRole("button", { name: "Отменить работу" }) as HTMLButtonElement;
  expect(cancel.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Причина отмены"), { target: { value: "брак провода" } });
  fireEvent.click(cancel);
  await waitFor(() => expect(calls.find((call) => call.url.endsWith("/j1/cancel"))?.body).toEqual({ version: 1, reason: "брак провода" }));
  expect(await screen.findByText("брак провода")).toBeTruthy();
});

it("starts a job on the machine it was opened from, with an эталон filling the wire", async () => {
  const calls: { url: string; body?: unknown }[] = [];
  serve(calls);
  const saved = vi.fn();
  render(<ManualJobWindow equipmentId="u19" close={() => undefined} onSaved={saved} />);
  const machine = await screen.findByRole("combobox", { name: "Станок" }) as HTMLInputElement;
  expect(machine.value).toBe("ОБ.019 · Автомат резки");
  const start = screen.getByRole("button", { name: "Начать работу" }) as HTMLButtonElement;
  expect(start.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Эталон наладки"), { target: { value: "r1" } });
  expect((screen.getByRole("combobox", { name: "Провод" }) as HTMLInputElement).value).toBe("ПВАМ 0,5");
  fireEvent.change(screen.getByLabelText("Длина, мм"), { target: { value: "120" } });
  fireEvent.change(screen.getByLabelText("Зачем"), { target: { value: "Образцы для ОТК" } });
  fireEvent.click(start);
  await waitFor(() => expect(saved).toHaveBeenCalled());
  expect(calls.find((call) => call.url.endsWith("/manual-jobs"))?.body).toEqual({ equipment_id: "u19", purpose: "Образцы для ОТК",
    wire: "ПВАМ 0,5", contact: "", length_mm: "120", quantity_planned: null, reference_id: "r1" });
  expect(await screen.findByRole("dialog", { name: "Ручная работа Р-000009" })).toBeTruthy();
});

it("refuses a second job on a busy machine before sending", async () => {
  serve([]);
  render(<ManualJobWindow inventory="ОБ.099" close={() => undefined} />);
  expect(await screen.findByText(/На станке уже идёт ручная работа Р-000002/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Зачем"), { target: { value: "Проба" } });
  expect((screen.getByRole("button", { name: "Начать работу" }) as HTMLButtonElement).disabled).toBe(true);
});

it("opens «Ручная работа» from a machine's details on «Оборудование»", async () => {
  const machine: MachineRow = {
    key: "machine:u37", kind: "machine", equipment_id: "u37", inventory: "ОБ.037", name: "Автомат резки BZW-880", pc: "", ip: "",
    service: "Агент ERP · станок", version: "", link: { code: "not_installed", text: "Агент не установлен", tone: "neutral" },
    last_seen: null, device: "ПЛК линии резки", state: { text: "—", tone: "neutral" }, card: false, others: "",
    article: "", site: "ЦАР", operations: [], family: "Резка и зачистка", controlled: false,
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = decodeURIComponent(String(input));
    if (url.includes("/setup-model/")) return json({ detail: "нет" }, 404);
    if (url.includes("/manual-jobs/options")) return json({ ...OPTIONS, machines: [...OPTIONS.machines, { id: "u37", inventory: "ОБ.037", name: "BZW-880", running: null }] });
    return json({ now: "2026-11-19T10:00:00+03:00", rows: [machine] });
  });
  render(<MachinesPage />);
  fireEvent.click(await screen.findByRole("tab", { name: /Без управления/ }));
  fireEvent.click(await screen.findByText("ОБ.037"));
  fireEvent.click(await screen.findByRole("button", { name: "Ручная работа" }));
  const dialog = await screen.findByRole("dialog", { name: "Новая ручная работа" });
  expect((await within(dialog).findByRole("combobox", { name: "Станок" }) as HTMLInputElement).value).toBe("ОБ.037 · BZW-880");
});

it("opens the job already running on the machine instead of a new one", async () => {
  const machine: MachineRow = {
    key: "machine:u19", kind: "machine", equipment_id: "u19", inventory: "ОБ.019", name: "Автомат резки", pc: "", ip: "",
    service: "Агент ERP · станок", version: "", link: { code: "not_installed", text: "Агент не установлен", tone: "neutral" },
    last_seen: null, device: "ПЛК линии резки", state: { text: "—", tone: "neutral" }, card: false, others: "",
    article: "", site: "ЦАР", operations: [], family: "Резка и зачистка", controlled: false,
  };
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = decodeURIComponent(String(input));
    calls.push(url);
    if (url.includes("/setup-model/")) return json({ detail: "нет" }, 404);
    if (url.includes("/manual-jobs?")) return json({ date_from: "2026-11-19", date_to: "2026-11-19", rows: [DONE, RUNNING], can_edit: true });
    return json({ now: "2026-11-19T10:00:00+03:00", rows: [machine] });
  });
  render(<MachinesPage />);
  fireEvent.click(await screen.findByRole("tab", { name: /Без управления/ }));
  fireEvent.click(await screen.findByText("ОБ.019"));
  fireEvent.click(await screen.findByRole("button", { name: "Ручная работа" }));
  const dialog = await screen.findByRole("dialog", { name: "Ручная работа Р-000001" });
  expect(within(dialog).getByRole("button", { name: "Завершить работу" })).toBeTruthy();
  expect(calls.some((url) => url.includes("/manual-jobs?") && url.endsWith("equipment_id=u19"))).toBe(true);
});

it("shows «Ручная работа» to Производство, Задания or Склад", () => {
  const user = { id: "1", email: "u@example.test", name: "Сотрудник", department: "Цех" };
  const as = (permissions: Record<string, AccessMode>): Principal => ({ user, roles: ["operator"], access_level: "operator", permissions });
  const item = visibleNav(as({ myops: "view" })).find((entry) => entry.id === "manualjobs");
  expect(item?.label).toBe("Ручная работа");
  expect(visibleNav(as({ production: "edit" })).some((entry) => entry.id === "manualjobs")).toBe(true);
  // The warehouse finds there the jobs whose material waits for it (S3).
  expect(visibleNav(as({ warehouse: "view" })).some((entry) => entry.id === "manualjobs")).toBe(true);
  expect(visibleNav(as({ projects: "edit" })).some((entry) => entry.id === "manualjobs")).toBe(false);
});
