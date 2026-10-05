import { describe, expect, it } from "vitest";
import {
  addBlock,
  addCustomOp,
  addInput,
  addInputBlock,
  addLibraryOp,
  blockOrder,
  cardLabel,
  emptyManual,
  genParams,
  manualConfig,
  manualFromConfig,
  manualProblem,
  moveOp,
  nameBlockAfter,
  orderedOps,
  removeBlock,
  removeInput,
  removeOp,
  renameBlock,
  selectBlock,
  stepsPayload,
  toolingPayload,
  updateOp,
  type LibraryCard,
  type ManualState,
} from "./manualLogic";

const CUT: LibraryCard = { id: "cut", code: "CUT_WIRE_hand", side: "", title: "CUT_WIRE_hand | Резка", name: "Резка", category: "Резка" };
const CRIMP: LibraryCard = { id: "prsA", code: "PRS_TERMINAL_auto", side: "A", title: "PRS_TERMINAL_auto | Опрессовка терминалов (A)", name: "", category: "" };
const TUT: LibraryCard = { id: "tut", code: "CUT_TUT_short", side: "", title: "CUT_TUT_short | Резка ТУТ", name: "Резка ТУТ", category: "Резка" };
const HEAT: LibraryCard = { id: "heat", code: "HEAT_TYT_short", side: "", title: "HEAT_TYT_short | Усадка", name: "Усадка", category: "Усадка" };
const OPS = [{ opNumber: "CUT_WIRE_hand", opName: "Резка", tPrep: "120", tOp: "6", tMachine: "" }];

/** Все три операции — в итоговом блоке, по порядку: резка, своя «Пайка экрана», опрессовка. */
function three(): ManualState {
  let state = addLibraryOp(emptyManual(), CUT, OPS);
  state = addCustomOp(state);
  state = updateOp(state, 2, { label: "Пайка экрана", opSec: "45" });
  return addLibraryOp(state, CRIMP, OPS);
}

/** Две ветки в изделие: «Провод» (резка, опрессовка) и «Трубка» (резка ТУТ) → итог (усадка, контроль). */
function twoBranches(): ManualState {
  let state = addLibraryOp(emptyManual(), HEAT, OPS); // seq 1 — в итог
  state = addBlock(state, "Провод"); // b2 → b1, выбран b2
  state = addLibraryOp(state, CUT, OPS); // seq 2
  state = addLibraryOp(state, CRIMP, OPS); // seq 3
  state = selectBlock(state, "b1");
  state = addBlock(state, "Трубка"); // b3 → b1
  state = addLibraryOp(state, TUT, OPS); // seq 4
  state = addCustomOp(state, "b1"); // seq 5 — в итог после усадки
  return updateOp(state, 5, { label: "Контроль" });
}

const base = { assemblyIndex: "КБ1", assemblyName: "Провод", partQty: 10 };
const order = (state: ManualState) => orderedOps(state).map((op) => op.label);

describe("manual assembly: diagram first", () => {
  it("starts with the final block only, selected", () => {
    const state = emptyManual();
    expect(state.blocks).toEqual([{ key: "b1", name: "", final: true, ops: [] }]);
    expect(state.selected).toBe("b1");
    expect(manualProblem(state)).toBe("Ручная сборка: добавьте хотя бы одну операцию — из библиотеки или свою.");
  });

  it("names library cards in Russian with their side, takes the norms from БД.ОП and adds into the selected block", () => {
    expect(cardLabel(CRIMP)).toBe("Опрессовка терминалов (А)");
    const state = three();
    expect(state.ops.map((op) => [op.seq, op.kind, op.label, op.prepMin, op.opSec])).toEqual([
      [1, "library", "Резка", "2", "6"], [2, "custom", "Пайка экрана", "", "45"], [3, "library", "Опрессовка терминалов (А)", "", ""],
    ]);
    expect(state.blocks[0].ops).toEqual([1, 2, 3]);
    expect(state.nextSeq).toBe(4);
  });

  it("adds a block feeding the selected block, selects it and puts the next operations into it", () => {
    let state = addBlock(three(), "Заготовка");
    expect(state.blocks.map((block) => block.key)).toEqual(["b1", "b2"]);
    expect(state.edges).toEqual([{ from: "b2", to: "b1", per_unit: 1 }]);
    expect(state.selected).toBe("b2");
    state = addLibraryOp(state, TUT, OPS);
    expect(state.blocks[1].ops).toEqual([4]);
    // Блок-вход идёт раньше итога: резка ТУТ — операция 1.
    expect(order(state)).toEqual(["Резка ТУТ", "Резка", "Пайка экрана", "Опрессовка терминалов (А)"]);
  });

  it("numbers operations by the diagram: inputs before consumers, ties by block creation order, then the order in a block", () => {
    const state = twoBranches();
    expect(blockOrder(state)).toEqual(["b2", "b3", "b1"]);
    expect(order(state)).toEqual(["Резка", "Опрессовка терминалов (А)", "Резка ТУТ", "Усадка", "Контроль"]);
    // «Зачистка» — вход «Провода», создана последней: «Трубка» готова раньше и создана раньше — идёт первой.
    let deeper = addBlock(selectBlock(state, "b2"), "Зачистка");
    deeper = addCustomOp(deeper);
    deeper = updateOp(deeper, 6, { label: "Зачистка" });
    expect(blockOrder(deeper)).toEqual(["b3", "b4", "b2", "b1"]);
    expect(order(deeper)).toEqual(["Резка ТУТ", "Зачистка", "Резка", "Опрессовка терминалов (А)", "Усадка", "Контроль"]);
  });

  it("moves an operation only inside its block and removes it from its block", () => {
    let state = moveOp(twoBranches(), 3, -1);
    expect(order(state).slice(0, 2)).toEqual(["Опрессовка терминалов (А)", "Резка"]);
    expect(moveOp(state, 3, -1)).toEqual(state); // уже первая в блоке
    state = removeOp(state, 3);
    expect(state.blocks.find((block) => block.key === "b2")?.ops).toEqual([2]);
    expect(state.ops.some((op) => op.seq === 3)).toBe(false);
  });

  it("deletes a block with its operations, hands its inputs to its consumer and keeps the final block", () => {
    let state = addBlock(selectBlock(twoBranches(), "b2"), "Зачистка"); // b4 → b2
    state = addCustomOp(state);
    state = { ...state, edges: state.edges.map((edge) => (edge.from === "b4" ? { ...edge, per_unit: 2 } : edge.from === "b2" ? { ...edge, per_unit: 3 } : edge)) };
    const removed = removeBlock(state, "b2");
    expect(removed.blocks.map((block) => block.key)).toEqual(["b1", "b3", "b4"]);
    expect(removed.ops.map((op) => op.seq)).toEqual([1, 4, 5, 6]);
    expect(removed.edges).toEqual([{ from: "b3", to: "b1", per_unit: 1 }, { from: "b4", to: "b1", per_unit: 6 }]);
    expect(removeBlock(removed, "b1")).toBe(removed);
    // Удалён выбранный блок — выбран тот, что он питал.
    expect(removeBlock(selectBlock(state, "b2"), "b2").selected).toBe("b1");
  });

  it("edits inputs only so that the diagram stays a tree into the product", () => {
    const state = twoBranches();
    const cycle = addInput(state, "b2", "b3"); // «Провод» → «Трубка»: можно, обе дальше идут в итог
    expect(cycle.problem).toBeNull();
    expect(cycle.state.edges).toContainEqual({ from: "b2", to: "b3", per_unit: 1 });
    const back = addInput(cycle.state, "b3", "b2");
    expect(back.problem).toMatch(/схема зациклена/);
    expect(back.state).toBe(cycle.state);
    const lost = removeInput(state, "b3", "b1");
    expect(lost.problem).toBe("блок «Трубка» не доходит до итогового блока");
    expect(removeInput(cycle.state, "b2", "b3").problem).toBeNull();
  });

  it("will not build an empty block and says which one", () => {
    const state = addBlock(three(), "Трубка");
    expect(manualProblem(state)).toBe("Ручная сборка: блок «Трубка» без операций — добавьте операцию или удалите блок.");
    expect(manualProblem(renameBlock(state, "b2", ""))).toBe("Ручная сборка: блок «Блок 2» без операций — добавьте операцию или удалите блок.");
    expect(manualProblem(addCustomOp(updateOp(state, 2, {}), "b2"))).toBe("Ручная сборка: операция 1 — укажите название своей операции.");
  });

  it("says in Russian what keeps the operations from being built, numbered by the diagram", () => {
    const state = three();
    expect(manualProblem(state)).toBeNull();
    expect(manualProblem(updateOp(state, 2, { label: " " }))).toBe("Ручная сборка: операция 2 — укажите название своей операции.");
    expect(manualProblem(updateOp(state, 1, { opSec: "шесть" }))).toBe("Ручная сборка: операция 1 «Резка» — время на штуку должно быть числом секунд не меньше нуля.");
    expect(manualProblem(updateOp(state, 1, { prepMin: "-1" }))).toBe("Ручная сборка: операция 1 «Резка» — подготовка должна быть числом минут не меньше нуля.");
    expect(manualProblem(updateOp(state, 3, { qty: "0" }))).toBe("Ручная сборка: операция 3 «Опрессовка терминалов (А)» — количество на изделие должно быть числом больше нуля.");
  });

  it("builds the payload: seconds, own steps with controls, tooling by section, one final block", () => {
    let state = three();
    state = updateOp(state, 1, { prepMin: "2,5", qty: "2", components: [{ id: "kit-0", art: "МГТФ 0,35", name: "МГТФ", qty: "0,6", unit: "М." }] });
    state = updateOp(state, 2, {
      steps: "Зачистить экран\n\nОблудить", controls: "Пайка без наплывов | IPC-A-620\nЦелостность",
      tooling: "Пинцет\nОборудование: паяльная станция\nрасходники: припой", result: "Провод с лепестком",
    });
    const config = manualConfig(state, base);
    expect(config).toMatchObject({ mode: "manual", assemblyIndex: "КБ1", assemblyName: "Провод", partQty: 10 });
    const ops = config.ops as Array<Record<string, unknown>>;
    expect(ops[0]).toEqual({
      type: "libraryOp", templateId: "cut", opNum: "CUT_WIRE_hand", side: "", seq: 1, label: "Резка", qty: "2", tPrep: "150", tOp: "6",
      components: [{ id: "kit-0", art: "МГТФ 0,35", name: "МГТФ", qty: "0.6", unit: "М." }],
    });
    expect(ops[1]).toMatchObject({ type: "customOp", seq: 2, label: "Пайка экрана", tPrep: "", tOp: "45", result: "Провод с лепестком" });
    expect(ops[1].steps).toEqual([
      { kind: "Операция", text: "Зачистить экран", tolerance: "" }, { kind: "Операция", text: "Облудить", tolerance: "" },
      { kind: "Контроль", text: "Пайка без наплывов", tolerance: "IPC-A-620" }, { kind: "Контроль", text: "Целостность", tolerance: "" },
    ]);
    expect(ops[1].tooling).toEqual([
      { section: "Инструмент", name: "Пинцет" }, { section: "Оборудование", name: "паяльная станция" }, { section: "Расходники", name: "припой" },
    ]);
    expect(ops[2]).toMatchObject({ type: "libraryOp", side: "A", label: "Опрессовка терминалов (А)" });
    expect(ops.some((op) => "parallel" in op)).toBe(false);
    expect(config.route).toEqual({
      blocks: [{ key: "b1", ops: [1, 2, 3], final: true, components: [{ id: "kit-0", art: "МГТФ 0,35", name: "МГТФ", qty: "0.6" }] }],
      edges: [],
    });
  });

  it("sends a two-branch diagram: ops in diagram order, blocks by positions, components on their blocks", () => {
    let state = twoBranches();
    state = updateOp(state, 4, { components: [{ id: "kit-2", art: "ТУТ 3/1,5", name: "Трубка", qty: "0,04", unit: "М." }] });
    state = { ...state, edges: state.edges.map((edge) => (edge.from === "b3" ? { ...edge, per_unit: 2 } : edge)) };
    const config = manualConfig(state, base);
    expect((config.ops as Array<Record<string, unknown>>).map((op) => [op.seq, op.label])).toEqual([
      [2, "Резка"], [3, "Опрессовка терминалов (А)"], [4, "Резка ТУТ"], [1, "Усадка"], [5, "Контроль"],
    ]);
    expect(config.route).toEqual({
      blocks: [
        { key: "b2", name: "Провод", ops: [1, 2], final: false },
        { key: "b3", name: "Трубка", ops: [3], final: false, components: [{ id: "kit-2", art: "ТУТ 3/1,5", name: "Трубка", qty: "0.04" }] },
        { key: "b1", ops: [4, 5], final: true },
      ],
      edges: [{ from: "b2", to: "b1", per_unit: 1 }, { from: "b3", to: "b1", per_unit: 2 }],
    });
    expect(manualProblem(state, "Провод")).toBeNull();
  });

  it("comes back from its own payload with the same blocks, operations and numbers", () => {
    let state = updateOp(twoBranches(), 5, { steps: "Осмотреть", controls: "Пайка | IPC", tooling: "Оборудование: лупа", prepMin: "5" });
    state = { ...state, edges: state.edges.map((edge) => (edge.from === "b3" ? { ...edge, per_unit: 2 } : edge)) };
    const restored = manualFromConfig(manualConfig(state, base))!;
    expect(order(restored)).toEqual(order(state));
    expect(orderedOps(restored)).toEqual(orderedOps(state));
    expect(restored.nextSeq).toBe(6);
    expect(restored.blocks.map((block) => [block.key, block.name, block.final, block.ops])).toEqual([
      ["b2", "Провод", false, [2, 3]], ["b3", "Трубка", false, [4]], ["b1", "", true, [1, 5]],
    ]);
    expect(restored.edges).toEqual([{ from: "b2", to: "b1", per_unit: 1 }, { from: "b3", to: "b1", per_unit: 2 }]);
    expect(restored.selected).toBe("b1");
    expect(manualConfig(restored, base)).toEqual(manualConfig(state, base));
    expect(stepsPayload(restored.ops.find((op) => op.seq === 5)!)).toHaveLength(2);
    expect(toolingPayload(restored.ops.find((op) => op.seq === 5)!)).toEqual([{ section: "Оборудование", name: "лупа" }]);
    expect(manualFromConfig({ ops: [] })).toBeNull();
  });

  it("round-trips generator operations: their fields and the assembly data stay, the norm is editable, the name is Russian", () => {
    const wires = [{ name: "МГТФ", art: "МГТФ", qty: 2, length: 160 }];
    const generated = {
      mode: "manual", assemblyIndex: "КБ1", assemblyName: "Жгут", partQty: 10, wires, twist: { enabled: false }, bigAssembly: false,
      ops: [
        { type: "cutWireStrip", seq: 1, templateId: "cut", opNum: "CUT_WIRE_auto", tPrep: "60", tOp: "4", tMachine: "2", wires, strip: { a: true, lenA: "5", b: false, lenB: "" } },
        { type: "prsTerm", seq: 2, side: "A", label: "Опрессовка терминалов А1", templateId: "prs", opNum: "PRS", tPrep: "", tOp: "6", ep: { termArt: "SXH", connArt: "XHP-2" }, wires },
        { type: "libraryOp", seq: 3, templateId: "heat", opNum: "HEAT", side: "", label: "Усадка", qty: "1", tPrep: "30", tOp: "5" },
      ],
      route: { blocks: [{ key: "b1", name: "Жгут", ops: [1, 2, 3], final: true }], edges: [] },
    };
    const state = manualFromConfig(generated)!;
    // Название — русское (по типу, когда в конфиге его нет), тип как заголовок не показывается.
    expect(state.ops.map((op) => [op.label, op.gen?.type])).toEqual([["Резка с разделкой", "cutWireStrip"], ["Опрессовка терминалов А1", "prsTerm"], ["Усадка", undefined]]);
    expect(genParams(state.ops[0].gen!)).toBe("1 пров. · L 160 мм · зачистка А 5 мм");
    expect(genParams(state.ops[1].gen!)).toBe("сторона А · 1 пров. · L 160 мм · терминал SXH · разъём XHP-2");
    expect(state.ops[0].prepMin).toBe("1");
    const config = manualConfig(state, { assemblyIndex: "КБ1", assemblyName: "Жгут", partQty: 10 });
    expect(config).toMatchObject({ mode: "manual", wires, twist: { enabled: false }, bigAssembly: false });
    expect(config.ops).toEqual([
      { ...generated.ops[0], label: "Резка с разделкой" }, generated.ops[1], generated.ops[2],
    ]);
    expect(manualFromConfig(config)).toEqual(state);
    // Норма правится, остальные поля генератора не трогаются; операцию можно убрать и переставить.
    const edited = moveOp(updateOp(state, 1, { prepMin: "2", opSec: "5" }), 2, -1);
    const next = manualConfig(edited, { partQty: 10 }).ops as Array<Record<string, unknown>>;
    expect(next.map((op) => op.seq)).toEqual([2, 1, 3]);
    expect(next[1]).toMatchObject({ type: "cutWireStrip", tPrep: "120", tOp: "5", tMachine: "2", strip: { a: true } });
    expect(manualConfig(removeOp(state, 2), { partQty: 10 }).ops).toHaveLength(2);
    expect(manualProblem(state, "Жгут")).toBeNull();
  });

  it("loads an old list without a scheme as a chain of blocks, one per operation", () => {
    const old = {
      mode: "manual", partQty: 10,
      ops: [
        { type: "libraryOp", templateId: "cut", opNum: "CUT_WIRE_hand", seq: 4, label: "Резка", qty: "1", tPrep: "120", tOp: "6" },
        { type: "customOp", seq: 7, label: "Пайка", qty: "1", tPrep: "", tOp: "45", steps: [], tooling: [] },
      ],
    };
    const state = manualFromConfig(old)!;
    expect(state.blocks).toEqual([{ key: "b1", name: "", final: false, ops: [4] }, { key: "b2", name: "", final: true, ops: [7] }]);
    expect(state.edges).toEqual([{ from: "b1", to: "b2", per_unit: 1 }]);
    expect(state.selected).toBe("b2");
    expect(order(state)).toEqual(["Резка", "Пайка"]);
    // Схема, которая не сходится со списком, — тоже цепочкой.
    expect(manualFromConfig({ ...old, route: { blocks: [{ key: "x", ops: [1], final: true }], edges: [] } })!.blocks).toHaveLength(2);
  });

  it("turns old «одновременно с предыдущей» flags into branches that the next operation joins", () => {
    const old = {
      mode: "manual", partQty: 10,
      ops: [
        { type: "libraryOp", templateId: "cut", opNum: "CUT_WIRE_hand", seq: 1, label: "Резка", qty: "1" },
        { type: "customOp", seq: 2, label: "Пайка", qty: "1", steps: [], tooling: [] },
        { type: "libraryOp", templateId: "heat", opNum: "HEAT_TYT_short", seq: 3, label: "Усадка", qty: "1", parallel: true },
        { type: "customOp", seq: 4, label: "Контроль", qty: "1", steps: [], tooling: [] },
      ],
    };
    const state = manualFromConfig(old)!;
    expect(state.edges).toEqual([{ from: "b1", to: "b2", per_unit: 1 }, { from: "b2", to: "b4", per_unit: 1 }, { from: "b3", to: "b4", per_unit: 1 }]);
    expect(order(state)).toEqual(["Резка", "Пайка", "Усадка", "Контроль"]);
    expect(state.ops.some((op) => "parallel" in op)).toBe(false);
    expect((manualConfig(state, base).ops as Array<Record<string, unknown>>).some((op) => "parallel" in op)).toBe(false);
    // Связь «Одновременно» (link) из прошлой версии вкладки — та же ветка.
    const linked = { ...old, ops: old.ops.map(({ parallel, ...op }) => (parallel ? { ...op, link: "parallel" } : op)) };
    expect(manualFromConfig(linked)!.edges).toEqual(state.edges);
  });

  it("loads a saved hand-edited scheme as its blocks", () => {
    const saved = {
      mode: "manual", partQty: 10,
      ops: [
        { type: "libraryOp", templateId: "cut", opNum: "CUT_WIRE_hand", seq: 4, label: "Резка", qty: "1" },
        { type: "customOp", seq: 7, label: "Пайка экрана", qty: "1", steps: [], tooling: [] },
      ],
      route: { blocks: [{ key: "b1", name: "Провод с лепестком", ops: [1, 2], final: true }], edges: [] },
    };
    const state = manualFromConfig(saved)!;
    expect(state.blocks).toEqual([{ key: "b1", name: "Провод с лепестком", final: true, ops: [4, 7] }]);
    expect(manualConfig(state, base).route).toEqual(saved.route);
  });
});

describe("manual assembly: editing on the diagram", () => {
  it("adds an empty input block to the chosen block and selects it", () => {
    const state = addInputBlock(emptyManual(), "b1");
    expect(state.blocks.map((block) => block.key)).toEqual(["b1", "b2"]);
    expect(state.edges).toEqual([{ from: "b2", to: "b1", per_unit: 1 }]);
    expect(state.selected).toBe("b2");
    // Не выбранному блоку — вход именно ему.
    const deeper = addInputBlock(addInputBlock(emptyManual(), "b1"), "b1");
    expect(deeper.edges).toEqual([{ from: "b2", to: "b1", per_unit: 1 }, { from: "b3", to: "b1", per_unit: 1 }]);
    const chain = addInputBlock(selectBlock(deeper, "b1"), "b2");
    expect(chain.edges.at(-1)).toEqual({ from: "b4", to: "b2", per_unit: 1 });
    expect(addInputBlock(emptyManual(), "nope")).toEqual(emptyManual());
  });

  it("names a block after its first operation only while the name is the default one", () => {
    const state = addInputBlock(emptyManual(), "b1");
    expect(nameBlockAfter(state, "b2", "Резка").blocks[1].name).toBe("Резка");
    const numbered = renameBlock(state, "b2", "Блок 2");
    expect(nameBlockAfter(numbered, "b2", "Усадка").blocks[1].name).toBe("Усадка");
    const own = renameBlock(state, "b2", "Провод с лепестком");
    expect(nameBlockAfter(own, "b2", "Резка")).toBe(own);
    expect(nameBlockAfter(state, "b2", "  ")).toBe(state);
  });

  it("refuses a connection that makes a cycle and keeps the diagram", () => {
    let state = addInputBlock(emptyManual(), "b1");
    state = addInputBlock(state, "b2");
    const result = addInput(state, "b1", "b3");
    expect(result.problem).toBeTruthy();
    expect(result.state).toBe(state);
  });
});

