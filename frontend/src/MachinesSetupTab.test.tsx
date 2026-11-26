import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { productionApi } from "./api";
import { equipmentSetupApi, type SetupStatus, type SetupTask } from "./equipmentSetupApi";
import { MachinesPage } from "./MachinesPage";

// «Запросы на наладку» (владелец 19.11.2026): каждый шаг бывшего экрана «Наладка оборудования» доступен из «Оборудования».
vi.mock("./equipmentSetupApi", () => ({ equipmentSetupApi: { viewer: vi.fn(), tasks: vi.fn(), machines: vi.fn(), moreProduction: vi.fn(), assignmentContext: vi.fn(), recipes: vi.fn(), assign: vi.fn(), executors: vi.fn(), recipe: vi.fn(), trials: vi.fn(), transition: vi.fn(), publish: vi.fn(), recordTrial: vi.fn(), task: vi.fn() } }));
vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), productionApi: { list: vi.fn() } }));

const base: Omit<SetupTask, "id" | "machine_id" | "status"> = {
  version: 2, operation: { card_id: "card", card_revision: 3, production_task_id: "production", production_operation_id: "operation", etk_position: 2 },
  technician_id: "person", recipe: { recipe_id: "recipe", version: 1 }, planned_start: "2026-11-19T08:00:00+03:00",
  planned_finish: "2026-11-19T09:00:00+03:00", created_at: "2026-11-18T08:00:00+03:00", updated_at: "2026-11-18T08:00:00+03:00",
  blocked_reason: "", blocked_machine_available: null,
};
const task = (id: string, machine: string, status: SetupStatus): SetupTask => ({ ...base, id, machine_id: machine, status,
  ...(status === "Blocked" ? { blocked_reason: "Нет ножей", blocked_machine_available: true } : {}) });
const TASKS = [task("t-new", "m1", "New"), task("t-work", "m2", "InProgress"), task("t-block", "m3", "Blocked")];
const page = <T,>(items: T[]) => ({ items, limit: 100, offset: 0 });

beforeEach(() => {
  window.history.replaceState({}, "", "/");
  vi.resetAllMocks();
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ now: "", rows: [], families: [] }),
    { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.mocked(equipmentSetupApi.viewer).mockResolvedValue({ actor_id: "person", site: "ЦАР", can_assign: true, writable: true });
  vi.mocked(equipmentSetupApi.tasks).mockResolvedValue(page(TASKS));
  vi.mocked(equipmentSetupApi.machines).mockResolvedValue(page(["m1", "m2", "m3"].map((id, index) => (
    { id, name: "Автомат резки", model: "EW-1165", site: "ЦАР", inventory_number: `ОБ.02${index + 1}` }))));
  vi.mocked(equipmentSetupApi.executors).mockResolvedValue(page([{ id: "person", name: "Иван Петров", site: "ЦАР" }]));
  vi.mocked(equipmentSetupApi.trials).mockResolvedValue(page([]));
  vi.mocked(equipmentSetupApi.recipe).mockResolvedValue({ recipe_id: "recipe", version: 1, machine_id: "m1", title: "Ручная настройка",
    geometry: [], panel_settings: [{ label: "Скорость", value: "12", unit: "" }], source: "Панель станка", created_at: base.created_at, created_by: "person" });
  vi.mocked(productionApi.list).mockResolvedValue({ tasks: [], next_cursor: null });
});

async function openTask(inventory: string) {
  fireEvent.click(await screen.findByText(inventory));
  return screen.findByRole("dialog", { name: "Задание наладки" });
}

it("hosts the setup task list with every step of the old «Наладка» screen", async () => {
  render(<MachinesPage setupViewer canWrite />);
  const tab = await screen.findByRole("tab", { name: "Запросы на наладку" });
  expect(screen.getAllByRole("tab").map((item) => item.textContent?.replace(/\d+$/, ""))).toEqual(
    ["Запросы на наладку", "Всё оборудование", "Журнал работ"]);
  fireEvent.click(tab);
  expect(tab.getAttribute("aria-selected")).toBe("true");
  expect(await screen.findByRole("button", { name: "Назначить наладку" })).toBeTruthy();
  expect(screen.queryByRole("searchbox", { name: "Поиск оборудования" })).toBeNull();

  let dialog = await openTask("ОБ.021");
  expect(within(dialog).getByRole("button", { name: "Начать наладку" })).toBeTruthy();
  expect(within(dialog).getByRole("button", { name: "Отменить наладку" })).toBeTruthy();
  expect(await within(dialog).findByRole("button", { name: "Записать новую версию" })).toBeTruthy();
  expect(within(dialog).getByText("Ручная настройка · версия 1")).toBeTruthy();
  expect(await within(dialog).findByText(/Пробные измерения пока не записаны/)).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", { name: "Закрыть" }));

  dialog = await openTask("ОБ.022");
  expect(within(dialog).getByRole("button", { name: "Подтвердить готовность наладки" })).toBeTruthy();
  expect(within(dialog).getByRole("button", { name: "Заблокировать" })).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", { name: "Закрыть" }));

  dialog = await openTask("ОБ.023");
  expect(within(dialog).getByRole("button", { name: "Продолжить наладку" })).toBeTruthy();
});

it("opens on «Запросы на наладку» when open setup tasks wait, otherwise on «Всё оборудование»", async () => {
  const first = render(<MachinesPage setupViewer />);
  await waitFor(() => expect(screen.getByRole("tab", { name: "Запросы на наладку" }).getAttribute("aria-selected")).toBe("true"));
  first.unmount();
  vi.mocked(equipmentSetupApi.tasks).mockClear().mockResolvedValue(page([task("t-done", "m1", "Ready"), task("t-off", "m2", "Cancelled")]));
  render(<MachinesPage setupViewer />);
  await waitFor(() => expect(equipmentSetupApi.tasks).toHaveBeenCalledTimes(1));
  expect((await screen.findByRole("tab", { name: "Всё оборудование" })).getAttribute("aria-selected")).toBe("true");
});

it("a tab chosen by the link wins over open setup tasks", async () => {
  render(<MachinesPage setupViewer initialView="equipment" />);
  expect((await screen.findByRole("tab", { name: "Всё оборудование" })).getAttribute("aria-selected")).toBe("true");
  expect(equipmentSetupApi.tasks).not.toHaveBeenCalled();
});

it("the master of the machine's site may confirm readiness of another technician's task, not block it", async () => {
  vi.mocked(equipmentSetupApi.viewer).mockResolvedValue({ actor_id: "master", site: "ЦАР", can_assign: true, writable: true, site_master: true });
  render(<MachinesPage setupViewer initialView="setup" />);
  const dialog = await openTask("ОБ.022");
  expect(within(dialog).getByRole("button", { name: "Подтвердить готовность наладки" })).toBeTruthy();
  expect(within(dialog).queryByRole("button", { name: "Заблокировать" })).toBeNull();
  expect(within(dialog).queryByRole("button", { name: "Начать наладку" })).toBeNull();
});

it("opens on «Запросы на наладку» from the old setup link", async () => {
  render(<MachinesPage setupViewer initialView="setup" />);
  expect((await screen.findByRole("tab", { name: "Запросы на наладку" })).getAttribute("aria-selected")).toBe("true");
  expect(await screen.findByText("ОБ.021")).toBeTruthy();
});
