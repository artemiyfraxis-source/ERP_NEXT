import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import type { KitsetBom, ProjectTechcard } from "./api";

/* «Ручная сборка» (владелец 04.10, схема первой — 05.10): кастомный провод, под который типовые генераторы не подходят. */
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

/** Одна карточка библиотеки — кликом по строке, в выбранный блок. */
function addFromLibrary(name: RegExp) {
  const picker = screen.getByRole("region", { name: "Библиотека операций" });
  fireEvent.click(within(picker).getByText(name, { selector: "b" }));
}

const diagram = () => screen.queryByRole("list", { name: "Блоки схемы" }) ?? screen.getByRole("group", { name: "Блоки схемы" });
const blockButton = (name: RegExp) => within(diagram()).getByRole("button", { name });

it("draws the diagram first: adds a block, puts library operations into it by a row click, own ones into the product, and builds", async () => {
  const onBuild = mount();
  await openManual();
  // Формы кабелей и типовые операции на этой вкладке не нужны; схема — только итоговый блок, он выбран.
  expect(screen.queryByText("Кабели")).not.toBeInTheDocument();
  expect(within(diagram()).getAllByRole("button")).toHaveLength(1);
  expect(blockButton(/Провод нетиповой 300мм/)).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("Выберите блок и добавьте операцию из библиотеки или свою.")).toBeInTheDocument();
  expect(screen.getByText(/Добавьте блок и операции в него/)).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Библиотека операций" })).not.toBeInTheDocument();

  // «+ Блок» — полуфабрикат, который входит в итог; он выбран, операции идут в него.
  fireEvent.click(screen.getByRole("button", { name: "+ Блок" }));
  fireEvent.change(screen.getByLabelText("Название блока"), { target: { value: "Провод с лепестком" } });
  expect(screen.getByRole("region", { name: "Блок «Провод с лепестком»" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "+ Операция из библиотеки" }));
  const picker = screen.getByRole("region", { name: "Библиотека операций" });
  expect(within(picker).getByText("Операция из библиотеки → в блок «Провод с лепестком»")).toBeInTheDocument();
  // Категории — чипами, поиск по коду, первый шаг — в строке.
  expect(within(picker).getByRole("button", { name: /^Резка · 1/ })).toBeInTheDocument();
  expect(within(picker).getByText("Подготовить инструмент резки")).toBeInTheDocument();
  fireEvent.change(within(picker).getByLabelText("Поиск карточки"), { target: { value: "CUT_WIRE" } });
  expect(within(picker).queryByText("Усадка")).not.toBeInTheDocument();
  addFromLibrary(/^Резка$/);
  expect(screen.getByRole("status")).toHaveTextContent("Добавлено в «Провод с лепестком»: «Резка»");
  fireEvent.change(within(picker).getByLabelText("Поиск карточки"), { target: { value: "" } });
  addFromLibrary(/^Опрессовка терминалов \(А\)$/);

  // Норма — из БД.ОП по коду карточки, иначе из библиотеки.
  expect(screen.getByLabelText("Подготовка, мин: Резка")).toHaveValue("2");
  expect(screen.getByLabelText("Операция, сек/шт: Резка")).toHaveValue("6");
  expect(screen.getByLabelText("Подготовка, мин: Опрессовка терминалов (А)")).toHaveValue("5");
  fireEvent.change(screen.getByLabelText("На изделие: Опрессовка терминалов (А)"), { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText("Параметры: Резка"), { target: { value: "L = 300 мм" } });
  // ↑/↓ — внутри блока.
  fireEvent.click(screen.getByRole("button", { name: "Выше: Опрессовка терминалов (А)" }));
  expect(screen.getByLabelText("Операция 1: Опрессовка терминалов (А)")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Ниже: Опрессовка терминалов (А)" }));

  // Итоговый блок: своя операция — после операций входа, номер 3.
  fireEvent.click(blockButton(/Провод нетиповой 300мм/));
  expect(screen.getByText("В блоке «Провод нетиповой 300мм» нет операций — добавьте операцию из библиотеки или свою.")).toBeInTheDocument();
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
  expect(screen.queryByLabelText(/Одновременно с предыдущей/)).not.toBeInTheDocument();

  // Схема — два блока цепочкой, у блоков номера операций; справа — порядок по блокам.
  expect(within(diagram()).getAllByRole("listitem")).toHaveLength(2);
  expect(blockButton(/Провод с лепестком/)).toHaveTextContent("1–2");
  expect(screen.getByLabelText("Штук из «Провод с лепестком» на один «Провод нетиповой 300мм»")).toHaveValue("1");
  fireEvent.change(screen.getByLabelText("Штук из «Провод с лепестком» на один «Провод нетиповой 300мм»"), { target: { value: "2" } });
  const order = screen.getByRole("group", { name: "Порядок операций" });
  expect(within(order).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
    expect.stringMatching(/^1Резка/), expect.stringMatching(/^2Опрессовка терминалов \(А\)/), expect.stringMatching(/^3Пайка экрана/),
  ]);

  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  const config = onBuild.mock.calls[0][0];
  expect(config).toMatchObject({ mode: "manual", assemblyIndex: "КБ7", assemblyName: "Провод нетиповой 300мм", partQty: 10, materialsSheet: false });
  expect(config.ops.map((op: Record<string, unknown>) => [op.type, op.seq, op.label, op.qty, op.tPrep, op.tOp])).toEqual([
    ["libraryOp", 1, "Резка", "1", "120", "6"],
    ["libraryOp", 2, "Опрессовка терминалов (А)", "2", "300", "20"],
    ["customOp", 3, "Пайка экрана", "1", "", "45"],
  ]);
  expect(config.ops[0]).toMatchObject({ templateId: "cut", opNum: "CUT_WIRE_hand", params: "L = 300 мм" });
  expect(config.ops[2]).toMatchObject({
    steps: [
      { kind: "Операция", text: "Зачистить экран на 8 мм", tolerance: "" }, { kind: "Операция", text: "Облудить", tolerance: "" },
      { kind: "Контроль", text: "Пайка без наплывов", tolerance: "IPC-A-620" },
    ],
    tooling: [{ section: "Оборудование", name: "паяльная станция" }],
    components: [{ id: "kit-1", art: "ЛП-3", name: "Лепесток", qty: "1", unit: "Шт." }],
  });
  expect(config.route).toEqual({
    blocks: [
      { key: "b2", name: "Провод с лепестком", ops: [1, 2], final: false },
      { key: "b1", ops: [3], final: true, components: [{ id: "kit-1", art: "ЛП-3", name: "Лепесток", qty: "1" }] },
    ],
    edges: [{ from: "b2", to: "b1", per_unit: 2 }],
  });
});

it("adds several cards at once into the selected block and deletes a block with its operations after a confirm", async () => {
  mount();
  await openManual();
  fireEvent.click(screen.getByRole("button", { name: "+ Блок" }));
  fireEvent.click(screen.getByRole("button", { name: "+ Операция из библиотеки" }));
  const picker = screen.getByRole("region", { name: "Библиотека операций" });
  fireEvent.click(within(picker).getByRole("checkbox", { name: "Выбрать cut" }));
  fireEvent.click(within(picker).getByRole("checkbox", { name: "Выбрать heat" }));
  fireEvent.click(within(picker).getByRole("button", { name: "Добавить выбранные" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Добавлено в «Блок 2»: 2 оп.");
  expect(blockButton(/Усадка/)).toHaveTextContent("2 оп.");

  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByRole("button", { name: "Удалить блок" }));
  expect(confirm).toHaveBeenCalledWith("Удалить блок «Усадка» и его операции (2)?");
  expect(screen.getByLabelText("Название операции 1")).toHaveValue("Резка");
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Удалить блок" }));
  expect(screen.queryByLabelText("Название операции 1")).not.toBeInTheDocument();
  expect(within(diagram()).getAllByRole("button")).toHaveLength(1);
  // Итоговый блок не удаляется.
  expect(screen.queryByRole("button", { name: "Удалить блок" })).not.toBeInTheDocument();
});

it("does not build an empty list, an own operation without a name or an empty block", async () => {
  const onBuild = mount();
  await openManual();
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Ручная сборка: добавьте хотя бы одну операцию — из библиотеки или свою.");
  fireEvent.click(screen.getByRole("button", { name: "+ Своя операция" }));
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Ручная сборка: операция 1 — укажите название своей операции.");
  fireEvent.change(screen.getByLabelText("Название операции 1"), { target: { value: "Сборка" } });
  fireEvent.click(screen.getByRole("button", { name: "+ Блок" }));
  fireEvent.change(screen.getByLabelText("Название блока"), { target: { value: "Трубка" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Ручная сборка: блок «Трубка» без операций — добавьте операцию или удалите блок.");
  expect(onBuild).not.toHaveBeenCalled();
});

it("will not take an input that loops the diagram or cut a block off the product", async () => {
  mount();
  await openManual();
  fireEvent.click(screen.getByRole("button", { name: "+ Блок" }));
  fireEvent.change(screen.getByLabelText("Название блока"), { target: { value: "Провод" } });
  fireEvent.click(blockButton(/Провод нетиповой 300мм/));
  fireEvent.click(screen.getByRole("button", { name: "+ Блок" }));
  fireEvent.change(screen.getByLabelText("Название блока"), { target: { value: "Трубка" } });
  // «Трубка» берёт «Провод» — можно; потом «Провод» не может взять «Трубку» — в списке её уже нет.
  fireEvent.change(screen.getByLabelText("Добавить вход из блока"), { target: { value: "b2" } });
  fireEvent.click(screen.getByRole("button", { name: "Добавить вход" }));
  expect(within(screen.getByRole("region", { name: "Блок «Трубка»" })).getByRole("button", { name: "Убрать вход «Провод»" })).toBeInTheDocument();
  fireEvent.click(blockButton(/Провод нетиповой 300мм/));
  fireEvent.click(screen.getByRole("button", { name: "Убрать вход «Трубка»" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Так нельзя: блок «Трубка» не доходит до итогового блока");
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

it("opens a card built by hand on its tab with its blocks, keeps them across tabs and asks before building it typically", async () => {
  const onBuild = mount({ card: builtCard });
  expect(await screen.findByRole("tab", { name: "Ручная сборка", selected: true })).toBeInTheDocument();
  expect(screen.getByLabelText("Название операции 1")).toHaveValue("Резка");
  expect(screen.getByLabelText("Подготовка, мин: Пайка экрана")).toHaveValue("5");
  expect(blockButton(/Провод с лепестком/)).toHaveTextContent("2 оп.");
  expect(screen.getByLabelText("Название блока")).toHaveValue("Провод с лепестком");

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
