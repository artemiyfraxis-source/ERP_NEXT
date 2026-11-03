import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { EquipmentSetupPage } from "./EquipmentSetupPage";
import { EquipmentRecipeEditor } from "./EquipmentRecipeEditor";
import { EquipmentSetupTrials } from "./EquipmentSetupTrials";
import { EquipmentSetupForm } from "./EquipmentSetupForm";
import { equipmentSetupApi, type SetupTask } from "./equipmentSetupApi";
import { productionApi } from "./api";

vi.mock("./equipmentSetupApi", () => ({ equipmentSetupApi: { viewer: vi.fn(), tasks: vi.fn(), machines: vi.fn(), moreProduction: vi.fn(), assignmentContext: vi.fn(), recipes: vi.fn(), assign: vi.fn(), executors: vi.fn(), recipe: vi.fn(), trials: vi.fn(), transition: vi.fn(), publish: vi.fn(), recordTrial: vi.fn(), task: vi.fn() } }));
vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), productionApi: { list: vi.fn() } }));
const task: SetupTask = { id: "setup-1", version: 2, operation: { card_id: "card", card_revision: 3, production_task_id: "production", production_operation_id: "operation", etk_position: 2 }, machine_id: "ОБ.027.2", technician_id: "person", recipe: { recipe_id: "recipe", version: 1 }, planned_start: "2026-11-04T08:00:00+03:00", planned_finish: "2026-11-04T09:00:00+03:00", created_at: "2026-11-03T08:00:00+03:00", updated_at: "2026-11-03T08:00:00+03:00", status: "InProgress", blocked_reason: "", blocked_machine_available: null };
const result = <T,>(items: T[]) => ({ items, limit: 100, offset: 0 });
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(equipmentSetupApi.viewer).mockResolvedValue({ actor_id: "person", site: "ЦАР", can_assign: false, writable: true });
  vi.mocked(equipmentSetupApi.tasks).mockResolvedValue(result([task]));
  vi.mocked(equipmentSetupApi.machines).mockResolvedValue(result([{ id: "ОБ.027.2", name: "Автомат резки", model: "EW-1165", site: "ЦАР" }]));
  vi.mocked(equipmentSetupApi.executors).mockResolvedValue(result([{ id: "person", name: "Иван Петров", site: "ЦАР" }]));
  vi.mocked(equipmentSetupApi.trials).mockResolvedValue(result([]));
  vi.mocked(equipmentSetupApi.recipe).mockResolvedValue({ recipe_id: "recipe", version: 1, machine_id: "ОБ.027.2", title: "Ручная настройка", geometry: [], panel_settings: [{ label: "Скорость", value: "12", unit: "" }], source: "Панель станка", created_at: task.created_at, created_by: "person" });
  vi.mocked(productionApi.list).mockResolvedValue({ tasks: [], next_cursor: null });
});
it("shows exact machine, visible window and honest missing operation instead of UUID", async () => {
  render(<EquipmentSetupPage />);
  expect(await screen.findByText("ОБ.027.2")).toBeInTheDocument();
  expect(screen.getByText(/Данные операции недоступны/)).toBeInTheDocument();
  expect(screen.queryByText("production")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Назначить наладку" })).not.toBeInTheDocument();
});
it("has an explicit loading error instead of empty success", async () => {
  vi.mocked(equipmentSetupApi.tasks).mockRejectedValue(new Error("Нет связи"));
  render(<EquipmentSetupPage />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи");
});
it("does not label a pinned task using a newer production card revision", async () => {
  vi.mocked(productionApi.list).mockResolvedValue({ next_cursor: null, tasks: [{ id: "production", project: "ПРОЕКТ", item: "КБ1", techcard_id: "card", techcard_revision: 4, operations: [{ id: "operation", position: 2, stage: "Новое название" }] }] } as never);
  render(<EquipmentSetupPage />);
  expect(await screen.findByText(/Данные операции недоступны · ЭТК 2, рев. 3/)).toBeInTheDocument();
  expect(screen.queryByText(/Новое название/)).not.toBeInTheDocument();
});
it("publishes manual sections separately without guessed presets", async () => {
  vi.mocked(equipmentSetupApi.publish).mockResolvedValue({} as never);
  render(<EquipmentRecipeEditor machine={{ id: "ОБ.027.2", name: "Автомат", model: "EW-1165", site: "ЦАР" }} saved={vi.fn()} close={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Название рецепта"), { target: { value: "Ручная настройка" } });
  fireEvent.change(screen.getByLabelText("Источник настроек"), { target: { value: "Панель станка" } });
  fireEvent.change(screen.getByLabelText("Панель: параметр 1"), { target: { value: "Скорость" } });
  fireEvent.change(screen.getByLabelText("Панель: значение 1"), { target: { value: "12" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить версию" }));
  await waitFor(() => expect(equipmentSetupApi.publish).toHaveBeenCalled());
  expect(vi.mocked(equipmentSetupApi.publish).mock.calls[0][1]).toMatchObject({ geometry: [], panel_settings: [{ label: "Скорость", value: "12", unit: "" }], expected_version: 0 });
});
it("records exact decimal text and preserves fields on conflict", async () => {
  vi.mocked(equipmentSetupApi.recordTrial).mockRejectedValue(new Error("Задание уже изменилось"));
  render(<EquipmentSetupTrials task={task} writable changed={vi.fn()} />);
  await screen.findByText(/Пробные измерения пока не записаны/);
  fireEvent.change(screen.getByLabelText("Параметр измерения"), { target: { value: "Диаметр" } });
  fireEvent.change(screen.getByLabelText("Измеренное значение"), { target: { value: "2,0100" } });
  fireEvent.change(screen.getByLabelText("Единица измерения"), { target: { value: "мм" } });
  fireEvent.click(screen.getByRole("button", { name: "Записать измерение" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Задание уже изменилось");
  expect(screen.getByLabelText("Измеренное значение")).toHaveValue("2,0100");
  expect(vi.mocked(equipmentSetupApi.recordTrial).mock.calls[0][2].value).toBe("2.0100");
});

it("requires explicit physical machine and submits the pinned recipe and visible Moscow window", async () => {
  vi.mocked(equipmentSetupApi.assignmentContext).mockResolvedValue(result([{ id: task.machine_id, name: "Автомат", model: "", site: "ЦАР" }]));
  vi.mocked(equipmentSetupApi.recipes).mockResolvedValue(result([await equipmentSetupApi.recipe("recipe", 1)]));
  vi.mocked(equipmentSetupApi.assign).mockResolvedValue(task);
  render(<EquipmentSetupForm choices={[{ key: "operation", label: "Проект · Резка · ЭТК 2, рев. 3", operation: task.operation }]} saved={vi.fn()} close={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Выданная операция ЭТК"), { target: { value: "operation" } });
  await screen.findByRole("option", { name: /ОБ.027.2 · Автомат/ });
  expect(screen.getByLabelText("Физический станок")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("Физический станок"), { target: { value: task.machine_id } });
  await screen.findByRole("option", { name: "Ручная настройка · версия 1" });
  fireEvent.change(screen.getByLabelText("Наладчик"), { target: { value: "person" } });
  fireEvent.change(screen.getByLabelText("Ручной рецепт и версия"), { target: { value: "recipe:1" } });
  fireEvent.change(screen.getByLabelText("Начало наладки · МСК"), { target: { value: "2026-11-05T10:00" } });
  fireEvent.change(screen.getByLabelText("Окончание наладки · МСК"), { target: { value: "2026-11-05T11:00" } });
  fireEvent.click(screen.getByRole("button", { name: "Назначить на 05.11.2026" }));
  await waitFor(() => expect(equipmentSetupApi.assign).toHaveBeenCalled());
  expect(vi.mocked(equipmentSetupApi.assign).mock.calls[0][0]).toMatchObject({ operation: task.operation, machine_id: task.machine_id, recipe: task.recipe, planned_start: "2026-11-05T10:00:00+03:00", planned_finish: "2026-11-05T11:00:00+03:00" });
});

it("block requires separate explicit machine availability and retains task version", async () => {
  vi.mocked(equipmentSetupApi.transition).mockResolvedValue({ ...task, version: 3, status: "Blocked", blocked_reason: "Проверка оснастки", blocked_machine_available: false });
  render(<EquipmentSetupPage />);
  fireEvent.click(await screen.findByText(task.machine_id));
  fireEvent.click(await screen.findByRole("button", { name: "Заблокировать" }));
  expect(screen.getByLabelText("Станок доступен другим работам?")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("Причина блокировки"), { target: { value: "Проверка оснастки" } });
  fireEvent.change(screen.getByLabelText("Станок доступен другим работам?"), { target: { value: "no" } });
  fireEvent.click(screen.getByRole("button", { name: "Подтвердить блокировку" }));
  await waitFor(() => expect(equipmentSetupApi.transition).toHaveBeenCalled());
  expect(vi.mocked(equipmentSetupApi.transition).mock.calls[0]).toEqual([task, "block", expect.any(String), "Проверка оснастки", false]);
});

it("saving a nested recipe never submits the filled assignment form", async () => {
  const recipe = await equipmentSetupApi.recipe("recipe", 1);
  vi.mocked(equipmentSetupApi.assignmentContext).mockResolvedValue(result([{ id: task.machine_id, name: "Автомат", model: "", site: "ЦАР" }]));
  vi.mocked(equipmentSetupApi.recipes).mockResolvedValue(result([recipe]));
  vi.mocked(equipmentSetupApi.publish).mockResolvedValue({ ...recipe, recipe_id: "new-recipe" });
  render(<EquipmentSetupForm choices={[{ key: "operation", label: "Резка · ЭТК 2, рев. 3", operation: task.operation }]} saved={vi.fn()} close={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Выданная операция ЭТК"), { target: { value: "operation" } });
  await screen.findByRole("option", { name: /ОБ.027.2 · Автомат/ });
  fireEvent.change(screen.getByLabelText("Физический станок"), { target: { value: task.machine_id } });
  await screen.findByRole("option", { name: "Ручная настройка · версия 1" });
  fireEvent.change(screen.getByLabelText("Наладчик"), { target: { value: "person" } });
  fireEvent.change(screen.getByLabelText("Ручной рецепт и версия"), { target: { value: "recipe:1" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать ручной рецепт" }));
  fireEvent.change(screen.getByLabelText("Название рецепта"), { target: { value: "Новый рецепт" } });
  fireEvent.change(screen.getByLabelText("Источник настроек"), { target: { value: "Запись оператора" } });
  fireEvent.change(screen.getByLabelText("Панель: параметр 1"), { target: { value: "Поле" } });
  fireEvent.change(screen.getByLabelText("Панель: значение 1"), { target: { value: "12" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить версию" }));
  await waitFor(() => expect(equipmentSetupApi.publish).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.queryByLabelText("Название рецепта")).not.toBeInTheDocument());
  expect(equipmentSetupApi.assign).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Ручной рецепт и версия")).toHaveValue("new-recipe:1");
});

it("view-as has no mutation controls even when server reports assignment capability", async () => {
  vi.mocked(equipmentSetupApi.viewer).mockResolvedValue({ actor_id: "person", site: "ЦАР", can_assign: true, writable: false });
  render(<EquipmentSetupPage />);
  fireEvent.click(await screen.findByText(task.machine_id));
  await screen.findByText("Ручная настройка · версия 1");
  expect(screen.queryByRole("button", { name: "Назначить наладку" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Подтвердить готовность наладки" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Записать измерение" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Отменить наладку" })).not.toBeInTheDocument();
});

it("loads later production pages into assignment choices without replacing the first page", async () => {
  const first = { id: "first", project: "ПЕРВЫЙ", item: "КБ1", techcard_id: "card", techcard_revision: 3, operations: [{ id: "first-op", position: 2, stage: "Резка" }] };
  vi.mocked(equipmentSetupApi.viewer).mockResolvedValue({ actor_id: "person", site: "ЦАР", can_assign: true, writable: true });
  vi.mocked(productionApi.list).mockResolvedValue({ tasks: [first], next_cursor: "cursor-first" } as never);
  vi.mocked(equipmentSetupApi.moreProduction).mockResolvedValue({ tasks: [{ ...first, id: "second", project: "ВТОРОЙ", operations: [{ id: "second-op", position: 2, stage: "Разделка" }] }], next_cursor: null } as never);
  render(<EquipmentSetupPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Назначить наладку" }));
  expect(screen.getByRole("option", { name: /ПЕРВЫЙ/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Ещё производственные задания" }));
  await screen.findByRole("option", { name: /ВТОРОЙ/ });
  expect(screen.getByRole("option", { name: /ПЕРВЫЙ/ })).toBeInTheDocument();
  expect(equipmentSetupApi.moreProduction).toHaveBeenCalledWith("cursor-first");
  expect(screen.queryByRole("button", { name: "Ещё производственные задания" })).not.toBeInTheDocument();
});
