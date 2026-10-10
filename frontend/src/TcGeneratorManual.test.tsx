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
    if (path.startsWith("/api/techcards/assembly/drafts?")) return json({ items: [] });
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
  fireEvent.click(await screen.findByText("Ручная сборка", { selector: "[role=tab]" }, { timeout: 10000 }));
  expect(await screen.findByText("Ручная сборка", { selector: "[role=tab][aria-selected=true]" }, { timeout: 10000 })).toBeInTheDocument();
}

/** Одна карточка библиотеки — кликом по строке, в выбранный блок. */
function addFromLibrary(name: RegExp) {
  const picker = screen.getByRole("region", { name: "Библиотека операций" });
  fireEvent.click(within(picker).getByText(name, { selector: "b" }));
}

const diagram = () => screen.queryByRole("list", { name: "Блоки схемы" }) ?? screen.getByRole("group", { name: "Блоки схемы" });
/** Сам блок схемы (не его «+» и «→»). */
const blockButton = (name: RegExp) => {
  const found = within(diagram()).getAllByRole("button", { name }).filter((button) => button.classList.contains("route-block"));
  expect(found).toHaveLength(1);
  return found[0];
};
const blockCount = () => diagram().querySelectorAll(".route-block").length;
/** Развернуть свёрнутую операцию по её номеру. */
const unfold = (position: number) => fireEvent.click(screen.getByRole("button", { name: new RegExp(`^Развернуть операцию ${position}:`) }));

it("draws the diagram first: adds a block, puts library operations into it by a row click, own ones into the product, and builds", async () => {
  const onBuild = mount();
  await openManual();
  // Формы кабелей и типовые операции на этой вкладке не нужны; схема — только итоговый блок, он выбран.
  expect(screen.queryByText("Кабели")).not.toBeInTheDocument();
  expect(blockCount()).toBe(1);
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

  // Операции из библиотеки — свёрнутые строки с нормой; поля открываются нажатием.
  expect(screen.getByLabelText("Норма: Резка")).toBeInTheDocument();
  expect(screen.queryByLabelText("Подготовка, мин: Резка")).not.toBeInTheDocument();
  unfold(1); unfold(2);
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
  // Число штук — «N шт» на стрелке; нажатие открывает поле.
  fireEvent.click(screen.getByTitle("Штук из «Провод с лепестком» на один «Пайка экрана»"));
  expect(screen.getByLabelText("Штук — Штук из «Провод с лепестком» на один «Пайка экрана»")).toHaveValue("1");
  fireEvent.change(screen.getByLabelText("Штук — Штук из «Провод с лепестком» на один «Пайка экрана»"), { target: { value: "2" } });
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
  fireEvent.click(within(picker).getByRole("checkbox", { name: "Выбрать: Резка" }));
  fireEvent.click(within(picker).getByRole("checkbox", { name: "Выбрать: Усадка" }));
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
  expect(blockCount()).toBe(1);
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
  expect(screen.queryByRole("button", { name: "Добавить вход" })).not.toBeInTheDocument();
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
  expect(await screen.findByText("Ручная сборка", { selector: "[role=tab][aria-selected=true]" }, { timeout: 10000 })).toBeInTheDocument();
  // Собранная карта открывается схемой; панель блока — по нажатию на блок.
  expect(screen.queryByLabelText("Название операции 1")).not.toBeInTheDocument();
  fireEvent.click(blockButton(/Провод с лепестком/));
  expect(screen.getByLabelText("Название операции 1")).toHaveValue("Резка");
  unfold(2);
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

  fireEvent.click(await screen.findByText("Ручная сборка", { selector: "[role=tab]" }, { timeout: 10000 }));
  fireEvent.click(blockButton(/Провод с лепестком/));
  expect(screen.getByLabelText("Название операции 2")).toHaveValue("Пайка экрана");
  confirm.mockImplementation(() => true);
  fireEvent.click(screen.getByRole("button", { name: "Пересобрать" }));
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(onBuild.mock.calls[0][0].ops.map((op: Record<string, unknown>) => op.seq)).toEqual([4, 7]);
  expect(onBuild.mock.calls[0][0].route).toEqual({ blocks: [{ key: "b1", name: "Провод с лепестком", ops: [1, 2], final: true }], edges: [] });
});

/** Блок «Провод» и «Трубка» — входы итога; «Трубка» выбрана. */
async function twoBlocks() {
  mount();
  await openManual();
  fireEvent.click(screen.getByRole("button", { name: "+ Блок" }));
  fireEvent.change(screen.getByLabelText("Название блока"), { target: { value: "Провод" } });
  fireEvent.click(blockButton(/Провод нетиповой 300мм/));
  fireEvent.click(screen.getByRole("button", { name: "+ Блок" }));
  fireEvent.change(screen.getByLabelText("Название блока"), { target: { value: "Трубка" } });
}

it("«+» on a block makes its new input, opens the library aimed at it and names the block after the picked operation", async () => {
  mount();
  await openManual();
  fireEvent.click(screen.getByRole("button", { name: "Добавить вход в блок «Провод нетиповой 300мм»" }));
  expect(blockCount()).toBe(2);
  const picker = screen.getByRole("region", { name: "Библиотека операций" });
  expect(within(picker).getByText("Операция из библиотеки → в блок «Блок 2»")).toBeInTheDocument();
  addFromLibrary(/^Резка$/);
  expect(blockButton(/Резка/)).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByLabelText("Название блока")).toHaveValue("Резка");
  expect(screen.getByLabelText("Операция 1: Резка")).toBeInTheDocument();
  // Своё название второй раз не затирается.
  fireEvent.change(screen.getByLabelText("Название блока"), { target: { value: "Нарезанный провод" } });
  addFromLibrary(/^Усадка$/);
  expect(screen.getByLabelText("Название блока")).toHaveValue("Нарезанный провод");
});

it("connects two blocks by two taps on «→» and a block, and refuses a cycle in Russian", async () => {
  await twoBlocks();
  // «Провод» ещё не берёт «Трубку»: Трубка → Провод.
  fireEvent.click(screen.getByRole("button", { name: "Соединить блок «Трубка» с другим блоком" }));
  expect(screen.getByRole("status")).toHaveTextContent("Нажмите блок, куда идёт «Трубка».");
  fireEvent.click(blockButton(/Провод(?! нетиповой)/));
  expect(within(screen.getByRole("region", { name: "Блок «Трубка»" })).queryByRole("button", { name: /Убрать вход/ })).not.toBeInTheDocument();
  fireEvent.click(blockButton(/Провод(?! нетиповой)/));
  expect(within(screen.getByRole("region", { name: "Блок «Провод»" })).getByRole("button", { name: "Убрать вход «Трубка»" })).toBeInTheDocument();
  // Обратная связь «Провод» → «Трубка» замкнула бы схему.
  fireEvent.click(screen.getByRole("button", { name: "Соединить блок «Провод» с другим блоком" }));
  fireEvent.click(blockButton(/Трубка/));
  expect(screen.getByRole("alert")).toHaveTextContent(/^Так нельзя:/);
});

it("changes the number on the arrow pill and removes the link from it", async () => {
  await twoBlocks();
  const label = "Штук из «Трубка» на один «Провод нетиповой 300мм»";
  fireEvent.click(screen.getByTitle(label));
  fireEvent.change(screen.getByLabelText(`Штук — ${label}`), { target: { value: "3" } });
  expect(screen.getByTitle(label)).toHaveTextContent("3 шт");
  fireEvent.click(screen.getByRole("button", { name: "Убрать связь" }));
  // Трубка без связи не доходит до изделия — убрать нельзя.
  expect(screen.getByRole("alert")).toHaveTextContent(/^Так нельзя:/);
  expect(screen.getByTitle(label)).toHaveTextContent("3 шт");
});

it("adds an input from the list at once, selects a block from the summary and folds an operation card", async () => {
  await twoBlocks();
  fireEvent.click(screen.getByRole("button", { name: "+ Операция из библиотеки" }));
  addFromLibrary(/^Резка$/);
  expect(screen.getByLabelText("Операция 1: Резка")).toBeInTheDocument();
  // Карточка свёрнута; нажатие открывает, ещё одно — сворачивает.
  expect(screen.queryByLabelText("Параметры: Резка")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Развернуть операцию 1: Резка" }));
  expect(screen.getByLabelText("Параметры: Резка")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Свернуть операцию 1: Резка" }));
  expect(screen.queryByLabelText("Параметры: Резка")).not.toBeInTheDocument();
  // Блок из «Итога» выбирается нажатием.
  const order = screen.getByRole("group", { name: "Порядок операций" });
  fireEvent.click(within(order).getByRole("button", { name: "Провод нетиповой 300мм" }));
  expect(screen.getByRole("region", { name: "Блок «Провод нетиповой 300мм»" })).toBeInTheDocument();
  expect(blockButton(/Провод нетиповой 300мм/)).toHaveAttribute("aria-pressed", "true");
});

/* UML-схема порядка операций (H-T3): блоки трёх видов, «проверено мастером», операции из КД — «нет в КД» и «было → стало». */
const fromKd = {
  mode: "manual", assemblyIndex: "КБ7", assemblyName: "Провод нетиповой", partQty: 10,
  ops: [
    { type: "libraryOp", templateId: "cut", opNum: "CUT_WIRE_hand", side: "", seq: 1, label: "Резка в размер", qty: "1", tPrep: "120", tOp: "8",
      origin: "harness:w1:CUT", edited: ["label"], kdBase: { label: "\"Резка\"" },
      kdChanges: [{ field: "opSec", was: "6", now: "8" }, { field: "label", was: "Резка", now: "Резка провода" }] },
    { type: "customOp", seq: 2, label: "Пайка экрана", qty: "1", tPrep: "", tOp: "45", steps: [], tooling: [], origin: "harness:w1:SOLDER", kdGone: true },
    { type: "customOp", seq: 3, label: "Контроль", qty: "1", tPrep: "", tOp: "30", steps: [], tooling: [] },
  ],
  route: {
    blocks: [
      { key: "b3", name: "Провод W1", ops: [1], final: false, checked: true, refs: ["w1"] },
      { key: "b2", name: "Провод с экраном", ops: [2], final: false, kind: "assembly", quantity: 2 },
      { key: "b1", ops: [3], final: true },
    ],
    edges: [{ from: "b3", to: "b2", per_unit: 1 }, { from: "b2", to: "b1", per_unit: 1 }],
  },
};

it("shows block kinds, the master's check and the КД marks of operations, and keeps them on build", async () => {
  const onBuild = mount({ card: { ...builtCard, document: { ...builtCard.document!, build_config: fromKd } } });
  expect(await screen.findByText("Ручная сборка", { selector: "[role=tab][aria-selected=true]" }, { timeout: 10000 })).toBeInTheDocument();
  expect(blockButton(/Провод W1/)).toHaveTextContent("п/ф");
  expect(within(blockButton(/Провод W1/)).getByTitle("Проверено мастером")).toBeInTheDocument();
  expect(blockButton(/Провод с экраном/)).toHaveTextContent("сборка");
  expect(blockButton(/Провод с экраном/)).toHaveTextContent("2 шт · 1 оп.");
  expect(blockButton(/Провод нетиповой/)).toHaveTextContent("итог");
  // «нет в КД» — в «Итоге» у операции, ключа которой больше нет в КД.
  expect(within(screen.getByRole("group", { name: "Порядок операций" })).getAllByText("нет в КД")).toHaveLength(1);

  fireEvent.click(blockButton(/Провод W1/));
  const panel = screen.getByRole("region", { name: "Блок «Провод W1»" });
  expect(within(panel).getByText(/Название: в КД было «Резка» → стало «Резка провода» \(оставлено значение мастера\)/)).toBeInTheDocument();
  expect(within(panel).getByText(/Операция, с\/шт: в КД было «6» → стало «8»/)).toBeInTheDocument();
  expect(within(panel).getByText("Объекты КД: 1")).toBeInTheDocument();
  expect(within(panel).getByLabelText("Вид блока")).toHaveValue("semiFinished");
  fireEvent.change(within(panel).getByLabelText("Вид блока"), { target: { value: "assembly" } });
  expect(blockButton(/Провод W1/)).toHaveTextContent("сборка");
  fireEvent.click(within(panel).getByLabelText("Проверено мастером"));
  expect(within(blockButton(/Провод W1/)).queryByTitle("Проверено мастером")).not.toBeInTheDocument();

  fireEvent.click(blockButton(/Провод с экраном/));
  expect(within(screen.getByRole("region", { name: "Блок «Провод с экраном»" })).getByText("нет в КД")).toBeInTheDocument();
  fireEvent.click(blockButton(/Провод нетиповой/));
  expect(screen.queryByLabelText("Вид блока")).not.toBeInTheDocument(); // итог — всегда изделие

  vi.spyOn(window, "confirm").mockImplementation(() => true);
  fireEvent.click(screen.getByRole("button", { name: "Пересобрать" }));
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  const config = onBuild.mock.calls[0][0];
  expect(config.route.blocks).toEqual([
    { key: "b3", name: "Провод W1", ops: [1], final: false, kind: "assembly", refs: ["w1"] },
    { key: "b2", name: "Провод с экраном", ops: [2], final: false, kind: "assembly", quantity: 2 },
    { key: "b1", ops: [3], final: true },
  ]);
  expect(config.ops[0]).toMatchObject({ origin: "harness:w1:CUT", edited: ["label"], label: "Резка в размер" });
  expect(config.ops[1]).toMatchObject({ origin: "harness:w1:SOLDER", kdGone: true });
  expect(config.ops[2]).not.toHaveProperty("origin");
});

/* Владелец 10.10: «как тут порядок поменять — я не могу, и выйти из раздела не могу», «редактирование пока очень неудобное». */
/** «Провод» (резка) и «Трубка» (усадка) — входы итога; номера операций: 1 — резка, 2 — усадка. */
async function twoBlocksWithOps() {
  await twoBlocks();
  fireEvent.click(screen.getByRole("button", { name: "+ Операция из библиотеки" }));
  addFromLibrary(/^Усадка$/);
  fireEvent.click(blockButton(PROVOD)); // библиотека остаётся открытой — теперь в «Провод»
  addFromLibrary(/^Резка$/);
}
const PROVOD = /Провод(?! нетиповой)/;
const numberOf = (name: RegExp) => blockButton(name).querySelector(".route-block-no")?.textContent;

it("the block pane closes with «Готово», × and Esc and opens again on a block; the diagram stays", async () => {
  await twoBlocks();
  expect(screen.getByRole("region", { name: "Блок «Трубка»" })).toBeInTheDocument();
  fireEvent.click(within(screen.getByRole("region", { name: "Блок «Трубка»" })).getByRole("button", { name: "Готово" }));
  expect(screen.queryByRole("region", { name: /^Блок «/ })).not.toBeInTheDocument();
  expect(blockCount()).toBe(3);
  fireEvent.click(blockButton(/Трубка/));
  fireEvent.click(screen.getByRole("button", { name: "Закрыть панель блока" }));
  expect(screen.queryByRole("region", { name: /^Блок «/ })).not.toBeInTheDocument();
  fireEvent.click(blockButton(/Трубка/));
  fireEvent.keyDown(blockButton(/Трубка/), { key: "Escape" });
  expect(screen.queryByRole("region", { name: /^Блок «/ })).not.toBeInTheDocument();
});

it("↑ / ↓ on a block reorder the blocks and the operation numbers; a block cannot pass the block that takes it", async () => {
  await twoBlocksWithOps();
  expect([numberOf(PROVOD), numberOf(/Трубка/)]).toEqual(["1", "2"]);
  fireEvent.click(screen.getByRole("button", { name: "Выше: блок «Трубка»" }));
  expect([numberOf(PROVOD), numberOf(/Трубка/)]).toEqual(["2", "1"]);
  expect(screen.getByRole("button", { name: "Выше: блок «Трубка»" })).toBeDisabled();
  // Ниже итога блок встать не может — итог берёт его полуфабрикат: слова, схема прежняя.
  fireEvent.click(screen.getByRole("button", { name: "Ниже: блок «Провод»" }));
  expect(screen.getAllByRole("alert").at(-1)).toHaveTextContent("Так нельзя: блок «Провод нетиповой 300мм» берёт полуфабрикат блока «Провод» — ниже него этот блок встать не может");
  expect([numberOf(PROVOD), numberOf(/Трубка/)]).toEqual(["2", "1"]);
  // В панели блока — те же «Выше / Ниже».
  fireEvent.click(within(screen.getByRole("region", { name: "Блок «Провод»" })).getByRole("button", { name: "↑ Выше" }));
  expect([numberOf(PROVOD), numberOf(/Трубка/)]).toEqual(["1", "2"]);
});

it("keyboard on the diagram: ↑/↓ pick the neighbour block, Alt+↑/↓ move the picked one", async () => {
  await twoBlocksWithOps();
  fireEvent.keyDown(blockButton(PROVOD), { key: "ArrowDown" });
  expect(screen.getByRole("region", { name: "Блок «Трубка»" })).toBeInTheDocument();
  fireEvent.keyDown(blockButton(/Трубка/), { key: "ArrowUp", altKey: true });
  expect(numberOf(/Трубка/)).toBe("1");
  // В поле ввода стрелки остаются полю.
  fireEvent.keyDown(screen.getByLabelText("Название блока"), { key: "ArrowDown" });
  expect(screen.getByRole("region", { name: "Блок «Трубка»" })).toBeInTheDocument();
});

it("«Перенести в блок…» moves an operation to another block", async () => {
  await twoBlocksWithOps();
  fireEvent.click(blockButton(/Трубка/));
  fireEvent.change(screen.getByLabelText("Перенести в блок: Усадка"), { target: { value: "b2" } });
  expect(blockButton(/Трубка/)).toHaveTextContent("нет операций");
  expect(blockButton(PROVOD)).toHaveTextContent("2 оп.");
});

it("a long generated block name shows short by its operations, codes first, the full name in the tooltip", async () => {
  await twoBlocksWithOps();
  const long = "Разъём XHP-2 смонтирован (ст. А, 4 пров.) (со свивкой) (маркирован)";
  fireEvent.change(screen.getByLabelText("Название блока"), { target: { value: long } });
  const block = within(diagram()).getByTitle(long);
  expect(block.querySelector(".route-block-name")).toHaveTextContent(/^Резка$/);
  expect(block.querySelector(".route-block-code")).toHaveTextContent("CUT_WIRE_hand");
  expect(screen.getByRole("button", { name: "Добавить вход в блок «Резка»" })).toHaveAttribute("title", "Добавить вход — новый блок, который идёт в этот");
});
