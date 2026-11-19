import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { Ob019SetupPanel, differs, mm } from "./Ob019SetupPanel";
import { ApiError } from "./api";
import { equipmentSetupApi, type Ob019Context, type Ob019Proposal } from "./equipmentSetupApi";

vi.mock("./equipmentSetupApi", () => ({ equipmentSetupApi: { ob019Context: vi.fn(), ob019Proposal: vi.fn(), ob019Live: vi.fn(), ob019Correct: vi.fn(), ob019SaveVersion: vi.fn() } }));

const parameters = ([["length_mm", "Длина провода", "D32", false], ["strip_front_mm", "Зачистка начала", "D34", true], ["pull_front_mm", "Стягивание начала", "D36", true],
  ["strip_back_mm", "Зачистка конца", "D38", true], ["pull_back_mm", "Стягивание конца", "D40", true], ["diameter_mm", "Диаметр реза", "D42", true], ["knife_back_mm", "Отвод ножа", "D43", true]] as const)
  .map(([key, label, register, in_recipe]) => ({ key, label, register, in_recipe, unit: "мм" }));
const context = (groups: Ob019Context["groups"] = []): Ob019Context => ({ machine: { id: "unit-019", inventory_number: "ОБ.019", site: "ЦАР" }, parameters,
  wires: [{ label: "ПВАМ 0,35", conductor_mm: "0.770", has_recipe: true }, { label: "МГТФ 0,12", conductor_mm: "0.390", has_recipe: false }], groups });
const values: Record<string, string | null> = { length_mm: "250", strip_front_mm: "5.00", pull_front_mm: "5.00", strip_back_mm: "5.00", pull_back_mm: "5.00", diameter_mm: "0.80", knife_back_mm: "0.10" };
const proposal: Ob019Proposal = {
  wire: "ПВАМ 0,35", etk: null, publish: { recipe_id: "recipe-1", expected_version: 1 },
  recipe: { recipe_id: "recipe-1", version: 1, title: "ПВАМ 0,35 · ОБ.019", created_at: "2026-11-18T12:00:00+03:00", source: "Владелец" },
  values: parameters.map(item => ({ key: item.key, label: item.label, unit: "мм", register: item.register, value: values[item.key], source: item.key === "length_mm" ? "Задание" : "Рецепт, версия 1" })),
};
const viewer = { actor_id: "slava", site: "ЦАР", can_assign: true, writable: true };

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(equipmentSetupApi.ob019Context).mockResolvedValue(context());
  vi.mocked(equipmentSetupApi.ob019Proposal).mockResolvedValue(proposal);
  vi.mocked(equipmentSetupApi.ob019Correct).mockResolvedValue({});
  vi.mocked(equipmentSetupApi.ob019Live).mockResolvedValue({ link: { code: "ok", text: "На связи", tone: "ok" }, reported_at: "2026-11-18T12:00:05+03:00", age_seconds: 1,
    live: { state: { text: "Готов", tone: "ok" }, wire: { length_mm: 100, strip_front_mm: 5, pull_front_mm: 5, strip_back_mm: 5, pull_back_mm: 5, diameter_mm: 0.9, knife_back_mm: 0.1 } } });
});

async function manualProposal() {
  render(<Ob019SetupPanel machineId="unit-019" viewer={viewer} close={vi.fn()} />);
  fireEvent.change(await screen.findByLabelText("Провод"), { target: { value: "ПВАМ 0,35" } });
  fireEvent.change(screen.getByLabelText("Длина провода, мм"), { target: { value: "250" } });
  fireEvent.change(screen.getByLabelText("Количество, шт"), { target: { value: "20" } });
  fireEvent.click(screen.getByRole("button", { name: "Показать предложение" }));
  return screen.findByRole("table");
}

it("formats panel millimetres and compares at the machine's 0,01 step", () => {
  expect(mm("0.8")).toBe("0,80");
  expect(mm(null)).toBe("—");
  expect(differs("0,80", 0.8)).toBe(false);
  expect(differs("0,80", 0.9)).toBe(true);
  expect(differs(null, 0.9)).toBe(false);
});

it("outside a project: shows the recipe beside the machine's values and journals each correction", async () => {
  const table = await manualProposal();
  expect(equipmentSetupApi.ob019Proposal).toHaveBeenCalledWith("unit-019", { wire: "ПВАМ 0,35", length_mm: "250" });
  expect(screen.getByText(/рецепт «ПВАМ 0,35 · ОБ.019», версия 1/)).toBeInTheDocument();
  const dia = within(table).getByRole("rowheader", { name: "Диаметр реза" }).closest("tr")!;
  expect(dia).toHaveClass("ob019-diff");
  expect(within(dia).getByText("≠ 0,90")).toBeInTheDocument();
  expect(await screen.findByText("На связи")).toBeInTheDocument();
  const input = screen.getByLabelText("Диаметр реза, фактически");
  expect(input).toHaveValue("0,80");
  fireEvent.change(input, { target: { value: "0,78" } });
  fireEvent.blur(input);
  await waitFor(() => expect(equipmentSetupApi.ob019Correct).toHaveBeenCalledTimes(1));
  expect(vi.mocked(equipmentSetupApi.ob019Correct).mock.calls[0]).toEqual(["unit-019", { parameter: "diameter_mm", actual: "0.78", proposed: "0.80", live: "0.90",
    wire: "ПВАМ 0,35", context: "вне проекта · 20 шт", quantity: 20 }]);
  fireEvent.blur(input);  // the same value again is not a new correction
  expect(equipmentSetupApi.ob019Correct).toHaveBeenCalledTimes(1);
});

it("saves the actual values as the next version and works without live values", async () => {
  vi.mocked(equipmentSetupApi.ob019Live).mockRejectedValue(new ApiError("Not Found", 404));
  vi.mocked(equipmentSetupApi.ob019SaveVersion).mockResolvedValue({ recipe_id: "recipe-1", version: 2, title: "ПВАМ 0,35 · ОБ.019", created_at: "2026-11-18T13:00:00+03:00" });
  await manualProposal();
  expect(await screen.findByText(/Живые значения недоступны: станок ещё не подключён к ERP/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Взять значения со станка" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Сохранить версию 2" }));
  await screen.findByText(/Версия 2 сохранена/);
  expect(vi.mocked(equipmentSetupApi.ob019SaveVersion).mock.calls[0][1]).toEqual({ recipe_id: "recipe-1", expected_version: 1, wire: "ПВАМ 0,35",
    values: { strip_front_mm: "5", pull_front_mm: "5", strip_back_mm: "5", pull_back_mm: "5", diameter_mm: "0.8", knife_back_mm: "0.1" },
    context: "вне проекта · 20 шт", etk_strip_front_mm: null, etk_strip_back_mm: null });
});

it("a fitter without the master's right journals corrections but does not save versions", async () => {
  render(<Ob019SetupPanel machineId="unit-019" viewer={{ ...viewer, can_assign: false }} close={vi.fn()} />);
  fireEvent.change(await screen.findByLabelText("Провод"), { target: { value: "ПВАМ 0,35" } });
  fireEvent.click(screen.getByRole("button", { name: "Показать предложение" }));
  await screen.findByRole("table");
  expect(screen.queryByRole("button", { name: /Сохранить версию/ })).not.toBeInTheDocument();
  expect(screen.getByText("Версию рецепта сохраняет мастер площадки")).toBeInTheDocument();
});

it("by cutting job: the group and its length ask for the ETK-based proposal", async () => {
  vi.mocked(equipmentSetupApi.ob019Context).mockResolvedValue(context([{ key: "g1:120", group_id: "g1", number: "ГР-0007", project: "708К.", cable: "ПВАМ 0,35 красный",
    wire: "ПВАМ 0,35", length_mm: "120", quantity: "15", strip_front_mm: "5", strip_back_mm: "6.5" }]));
  render(<Ob019SetupPanel machineId="unit-019" viewer={viewer} close={vi.fn()} />);
  const select = await screen.findByLabelText(/Задание резки/);
  fireEvent.change(select, { target: { value: "g1:120" } });
  await screen.findByRole("table");
  expect(equipmentSetupApi.ob019Proposal).toHaveBeenCalledWith("unit-019", { group_id: "g1", length_mm: "120" });
  expect(screen.getByRole("option", { name: "ГР-0007 · 708К. · ПВАМ 0,35 · 120,00 мм · 15 шт" })).toBeInTheDocument();
});
