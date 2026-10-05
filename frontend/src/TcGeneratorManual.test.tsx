import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import type { KitsetBom, ProjectTechcard } from "./api";

/* «Ручная сборка» (владелец 04.10): кастомный провод, под который типовые генераторы не подходят. */
const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000007", version: 1, project: "P-300", item_index: "КБ7", product_name: "Провод нетиповой",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-10-04T10:00:00+03:00", updated_at: "2026-10-04T10:00:00+03:00", issued_at: null,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function mockApi() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cut", code: "CUT_WIRE_hand", side: "", title: "CUT_WIRE_hand | Резка", name: "Резка", category: "Резка", steps: 8, tooling: 3,
        first_step: "Подготовить инструмент резки" },
      { id: "prsA", code: "PRS_TERMINAL_hand", side: "A", title: "PRS_TERMINAL_hand | Опрессовка терминалов (A)", name: "Опрессовка терминалов",
        category: "Опрессовка терминалов", steps: 8, tooling: 3, norms: { operation_seconds: "20", setup_seconds: "300" } },
      { id: "heat", code: "HEAT_TYT_short", side: "", title: "HEAT_TYT_short | Усадка", name: "Усадка", category: "Усадка", steps: 4, tooling: 2 },
    ] });
    if (path.startsWith("/api/references/tech-operations?")) {
      const tab = new URLSearchParams(path.split("?")[1]).get("tab");
      return json({ metadata: null, columns: [], records: tab === "op"
        ? [{ id: "o1", tab: "op", display_text: "", source_sheet: "", fields: { opNumber: "CUT_WIRE_hand", opName: "Резка", tPrep: "120", tOp: "6" } }] : [] });
    }
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "", crimp_height: "", pull_force: "", by_section: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
}

afterEach(() => vi.restoreAllMocks());

const kit = (): KitsetBom => ({
  project: "P-300", index: "КБ7", name: "Провод нетиповой 300мм", run: "10", part_qty: "10", calc_source: "", calc_ops: [],
  components: [
    { id: "kit-0", kd: "", type: "Провод", name: "МГТФ 0,35", art: "МГТФ 0,35", qty: 0.6 },
    { id: "kit-1", kd: "", type: "Терминал", name: "Лепесток", art: "ЛП-3", qty: 1 },
  ],
});

function mount(props: Partial<Parameters<typeof TcGenerator>[0]> = {}) {
  mockApi();
  const onBuild = vi.fn();
  render(<TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={kit()} {...props} />);
  return onBuild;
}

async function openManual() {
  fireEvent.click(await screen.findByRole("tab", { name: "Ручная сборка" }, { timeout: 5000 }));
  expect(screen.getByRole("tab", { name: "Ручная сборка", selected: true })).toBeInTheDocument();
}

function addFromLibrary(name: RegExp, label: string) {
  const picker = screen.getByRole("region", { name: "Библиотека операций" });
  fireEvent.click(within(picker).getByText(name, { selector: "b" }));
  fireEvent.click(within(picker).getByRole("button", { name: `Добавить «${label}»` }));
}

it("adds operations from the library and an own one, reorders them and builds the manual config with a chain scheme", async () => {
  const onBuild = mount();
  await openManual();
  // Формы кабелей и типовые операции на этой вкладке не нужны.
  expect(screen.queryByText("Кабели")).not.toBeInTheDocument();
  expect(screen.getByText("Операций нет — добавьте карточку из библиотеки или свою операцию.")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "+ Операция из библиотеки" }));
  const picker = screen.getByRole("region", { name: "Библиотека операций" });
  // Категории — чипами, поиск по коду.
  expect(within(picker).getByRole("button", { name: /^Резка · 1/ })).toBeInTheDocument();
  fireEvent.change(within(picker).getByLabelText("Поиск карточки"), { target: { value: "CUT_WIRE" } });
  expect(within(picker).queryByText("Усадка")).not.toBeInTheDocument();
  addFromLibrary(/^Резка$/, "Резка");
  expect(screen.getByRole("status")).toHaveTextContent("Добавлена операция 1: «Резка»");
  fireEvent.change(within(picker).getByLabelText("Поиск карточки"), { target: { value: "" } });
  addFromLibrary(/^Опрессовка терминалов \(А\)$/, "Опрессовка терминалов (А)");

  // Норма — из БД.ОП по коду карточки, иначе из библиотеки.
  expect(screen.getByLabelText("Подготовка, мин: Резка")).toHaveValue("2");
  expect(screen.getByLabelText("Операция, сек/шт: Резка")).toHaveValue("6");
  expect(screen.getByLabelText("Подготовка, мин: Опрессовка терминалов (А)")).toHaveValue("5");
  fireEvent.change(screen.getByLabelText("На изделие: Опрессовка терминалов (А)"), { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText("Параметры: Резка"), { target: { value: "L = 300 мм" } });

  fireEvent.click(screen.getByRole("button", { name: "+ Своя операция" }));
  fireEvent.change(screen.getByLabelText("Название операции 3"), { target: { value: "Пайка экрана" } });
  fireEvent.change(screen.getByLabelText("Операция, сек/шт: Пайка экрана"), { target: { value: "45" } });
  fireEvent.change(screen.getByLabelText("Ход операции: Пайка экрана"), { target: { value: "Зачистить экран на 8 мм\nОблудить" } });
  fireEvent.change(screen.getByLabelText("Контроль: Пайка экрана"), { target: { value: "Пайка без наплывов | IPC-A-620" } });
  fireEvent.change(screen.getByLabelText("Оснастка: Пайка экрана"), { target: { value: "Оборудование: паяльная станция" } });
  const own = screen.getByLabelText("Операция 3: Пайка экрана");
  fireEvent.click(within(own).getByText("Комплектующие из КБ"));
  fireEvent.click(within(own).getByRole("checkbox", { name: /ЛП-3/ }));
  expect(within(own).getByLabelText("Количество ЛП-3 на изделие: Пайка экрана")).toHaveValue("1");

  // Своя операция — выше опрессовки: вторая в списке.
  fireEvent.click(screen.getByRole("button", { name: "Выше: Пайка экрана" }));
  expect(screen.getByLabelText("Операция 2: Пайка экрана")).toBeInTheDocument();

  // Схема — цепочка по списку, показана сразу.
  expect(screen.getByText(/3 блока · 3 оп\./)).toBeInTheDocument();
  expect(within(screen.getByRole("list", { name: "Блоки схемы" })).getAllByRole("listitem")).toHaveLength(3);

  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  const config = onBuild.mock.calls[0][0];
  expect(config).toMatchObject({ mode: "manual", assemblyIndex: "КБ7", assemblyName: "Провод нетиповой 300мм", partQty: 10, materialsSheet: false });
  expect(config.ops.map((op: Record<string, unknown>) => [op.type, op.seq, op.label, op.qty, op.tPrep, op.tOp])).toEqual([
    ["libraryOp", 1, "Резка", "1", "120", "6"],
    ["customOp", 3, "Пайка экрана", "1", "", "45"],
    ["libraryOp", 2, "Опрессовка терминалов (А)", "2", "300", "20"],
  ]);
  expect(config.ops[0]).toMatchObject({ templateId: "cut", opNum: "CUT_WIRE_hand", params: "L = 300 мм" });
  expect(config.ops[1]).toMatchObject({
    steps: [
      { kind: "Операция", text: "Зачистить экран на 8 мм", tolerance: "" }, { kind: "Операция", text: "Облудить", tolerance: "" },
      { kind: "Контроль", text: "Пайка без наплывов", tolerance: "IPC-A-620" },
    ],
    tooling: [{ section: "Оборудование", name: "паяльная станция" }],
    components: [{ id: "kit-1", art: "ЛП-3", name: "Лепесток", qty: "1", unit: "Шт." }],
  });
  expect(config.route).toEqual({
    blocks: [
      { key: "b1", ops: [1], final: false },
      { key: "b2", ops: [2], final: false, components: [{ id: "kit-1", art: "ЛП-3", name: "Лепесток", qty: "1" }] },
      { key: "b3", ops: [3], final: true },
    ],
    edges: [{ from: "b1", to: "b2", per_unit: 1 }, { from: "b2", to: "b3", per_unit: 1 }],
  });
});

it("does not build an empty list or an own operation without a name", async () => {
  const onBuild = mount();
  await openManual();
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Ручная сборка: добавьте хотя бы одну операцию — из библиотеки или свою.");
  fireEvent.click(screen.getByRole("button", { name: "+ Своя операция" }));
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Ручная сборка: операция 1 — укажите название своей операции.");
  expect(onBuild).not.toHaveBeenCalled();
});

const saved = {
  mode: "manual", assemblyIndex: "КБ7", assemblyName: "Провод нетиповой", partQty: 10,
  ops: [
    { type: "libraryOp", templateId: "cut", opNum: "CUT_WIRE_hand", side: "", seq: 4, label: "Резка", qty: "1", tPrep: "120", tOp: "6" },
    { type: "customOp", seq: 7, label: "Пайка экрана", qty: "1", tPrep: "300", tOp: "45",
      steps: [{ kind: "Операция", text: "Облудить", tolerance: "" }], tooling: [] },
  ],
  route: { blocks: [{ key: "b1", name: "Провод с лепестком", ops: [1, 2], final: true }], edges: [] },
};
const builtCard: ProjectTechcard = {
  ...card, version: 3, status: "Выдана", revision: 1,
  document: { kit: { part_quantity: "10", mode: "manual" }, operations: [], warnings: [], build_config: saved },
};

it("opens a card built by hand on its tab with its list and scheme, keeps them across tabs and asks before building it typically", async () => {
  const onBuild = mount({ card: builtCard });
  expect(await screen.findByRole("tab", { name: "Ручная сборка", selected: true })).toBeInTheDocument();
  expect(screen.getByLabelText("Название операции 1")).toHaveValue("Резка");
  expect(screen.getByLabelText("Подготовка, мин: Пайка экрана")).toHaveValue("5");
  expect(screen.getByText("правлена вручную")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Провод с лепестком/ })).toHaveTextContent("2 оп.");

  fireEvent.click(screen.getByRole("tab", { name: "Межплатная" }));
  expect(screen.queryByLabelText("Название операции 1")).not.toBeInTheDocument();
  // Типовая вкладка по карте, собранной вручную, — только после подтверждения.
  const confirm = vi.spyOn(window, "confirm").mockImplementation((text) => !String(text).includes("ручной сборкой"));
  fireEvent.click(screen.getByRole("button", { name: "Пересобрать" }));
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Техкарта КБ7 собрана ручной сборкой.\n\nСобрать по вкладке «Межплатная»?"));
  expect(screen.getByRole("alert")).toHaveTextContent("Сборка отменена — техкарта осталась ручной сборкой.");
  expect(onBuild).not.toHaveBeenCalled();

  fireEvent.click(await screen.findByRole("tab", { name: "Ручная сборка" }, { timeout: 5000 }));
  expect(screen.getByLabelText("Название операции 2")).toHaveValue("Пайка экрана");
  confirm.mockImplementation(() => true);
  fireEvent.click(screen.getByRole("button", { name: "Пересобрать" }));
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(onBuild.mock.calls[0][0].ops.map((op: Record<string, unknown>) => op.seq)).toEqual([4, 7]);
  expect(onBuild.mock.calls[0][0].route).toEqual({ blocks: [{ key: "b1", name: "Провод с лепестком", ops: [1, 2], final: true }], edges: [] });
});
