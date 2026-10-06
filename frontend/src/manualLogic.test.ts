import { describe, expect, it } from "vitest";
import {
  addBlock,
  addCustomOp,
  addInput,
  addInputBlock,
  addLibraryOp,
  blockKind,
  blockOrder,
  cardLabel,
  kdChangeText,
  kdKey,
  emptyManual,
  fillKdCards,
  genParams,
  isEmptyManual,
  kdOpSpec,
  kdStale,
  kdSummary,
  proposeFromKd,
  manualConfig,
  manualFromConfig,
  manualProblem,
  moveOp,
  nameBlockAfter,
  orderedOps,
  removeBlock,
  removeInput,
  removeOp,
  rederive,
  renameBlock,
  selectBlock,
  setBlockChecked,
  setBlockKind,
  setBlockQuantity,
  setPerUnit,
  stepsPayload,
  toolingPayload,
  updateOp,
  type KdCard,
  type LibraryCard,
  type ManualOp,
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

  it("genParams: side Б in Cyrillic, empty values omitted instead of a dash", () => {
    expect(genParams({ side: "B", strip: { a: true, lenA: "", b: true, lenB: "5" } })).toBe("сторона Б · зачистка Б 5 мм");
    expect(genParams({ strip: { a: true, lenA: "", b: true, lenB: "" } })).toBe("");
    expect(genParams({ side: "B" })).not.toMatch(/[BВ]/);
    expect(genParams({ strip: { a: true, lenA: "4", b: true, lenB: "6" } })).toBe("зачистка А 4, Б 6 мм");
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

/* Схема из КД: итог «Жгут» (усадка) ← блок b2 «Провод W1» (резка, опрессовка). Ключи операций — по объекту КД и коду. */
const CUT_W1 = kdKey("w1", "CUT"), PRS_W1 = kdKey("w1", "PRS"), STRIP_W1 = kdKey("w1", "STRIP"), HEAT_H = kdKey("h1", "HEAT"), CUT_W2 = kdKey("w2", "CUT");

function kd(state: ManualState, origin: string, label: string, patch: Partial<ManualOp> = {}, block = state.selected): ManualState {
  const next = addCustomOp(state, block);
  return updateOp(next, state.nextSeq, { origin, label, opSec: "6", ...patch });
}

function derivedScheme(): ManualState {
  let state = kd(emptyManual(), HEAT_H, "Усадка", { opSec: "20" }); // seq 1 — итог
  state = addBlock(state, "Провод W1"); // b2 → b1
  state = kd(state, CUT_W1, "Резка"); // seq 2
  return kd(state, PRS_W1, "Опрессовка"); // seq 3
}

const opBy = (state: ManualState, origin: string) => state.ops.find((op) => op.origin === origin)!;
const labelsIn = (state: ManualState, key: string) => state.blocks.find((block) => block.key === key)!.ops.map((seq) => state.ops.find((op) => op.seq === seq)!.label);

describe("manual assembly: re-derive from the КД, the master wins", () => {
  it("keeps the КД key on an operation and marks the fields the master edits", () => {
    const state = derivedScheme();
    expect(opBy(state, CUT_W1)).toMatchObject({ origin: "harness:w1:CUT", label: "Резка", opSec: "6" });
    expect(opBy(state, CUT_W1).edited).toBeUndefined(); // заданное при создании — не правка мастера
    const edited = updateOp(state, 2, { label: "Резка в размер", opSec: "6" });
    expect(opBy(edited, CUT_W1).edited).toEqual(["label"]); // opSec тот же — не правка
    expect(opBy(edited, CUT_W1).kdBase).toEqual({ label: JSON.stringify("Резка") });
    // Вторая правка того же поля не переписывает значение из КД.
    expect(opBy(updateOp(edited, 2, { label: "Резка 2" }), CUT_W1).kdBase).toEqual({ label: JSON.stringify("Резка") });
    // Своя операция мастера (без ключа КД) правится без отметок.
    const own = updateOp(addCustomOp(state, "b1"), 4, { label: "Контроль" });
    expect(own.ops.find((op) => op.seq === 4)).not.toHaveProperty("edited");
  });

  it("1) adds a new КД key into the block the КД proposes, next to its КД neighbour", () => {
    const manual = derivedScheme();
    const kdNext = kd(derivedScheme(), STRIP_W1, "Зачистка", {}, "b2"); // seq 4
    // В КД зачистка между резкой и опрессовкой.
    const derived = { ...kdNext, blocks: kdNext.blocks.map((block) => (block.key === "b2" ? { ...block, ops: [2, 4, 3] } : block)) };
    const next = rederive(manual, derived);
    expect(labelsIn(next, "b2")).toEqual(["Резка", "Зачистка", "Опрессовка"]);
    expect(opBy(next, STRIP_W1)).toMatchObject({ seq: 4, origin: STRIP_W1 });
    expect(next.nextSeq).toBe(5);
    expect(next.blocks.map((block) => block.key)).toEqual(["b1", "b2"]);
    // Новая операция итога — в итоговый блок.
    const toFinal = rederive(manual, kd(derivedScheme(), kdKey("h1", "MARK"), "Маркировка", {}, "b1"));
    expect(labelsIn(toFinal, "b1")).toEqual(["Усадка", "Маркировка"]);
  });

  it("1) puts a new КД key into «Новое из КД» when the master removed the proposed block, and never returns deleted ones", () => {
    const manual = removeBlock(derivedScheme(), "b2"); // мастер убрал блок вместе с резкой и опрессовкой
    expect(manual.kdDropped).toEqual([CUT_W1, PRS_W1]);
    const derived = kd(derivedScheme(), STRIP_W1, "Зачистка", {}, "b2");
    const next = rederive(manual, derived);
    const spare = next.blocks.find((block) => block.name === "Новое из КД")!;
    expect(spare).toMatchObject({ key: "b2", final: false });
    expect(labelsIn(next, spare.key)).toEqual(["Зачистка"]);
    expect(next.edges).toEqual([{ from: "b2", to: "b1", per_unit: 1 }]);
    expect(next.ops.map((op) => op.origin)).toEqual([HEAT_H, STRIP_W1]); // резка и опрессовка не вернулись
    expect(manualProblem(next)).toBeNull();
    // Второй новый ключ — в тот же «Новое из КД», не в новый блок.
    const again = rederive(next, kd(addBlock(selectBlock(derived, "b1"), "Провод W2"), CUT_W2, "Резка W2")); // в КД — блок b3
    expect(again.blocks.filter((block) => block.name === "Новое из КД")).toHaveLength(1);
    expect(labelsIn(again, spare.key)).toEqual(["Зачистка", "Резка W2"]);
  });

  it("2) refreshes the КД fields of a known key except the edited ones and keeps «было → стало»", () => {
    const manual = updateOp(derivedScheme(), 2, { label: "Резка в размер" });
    const derived = updateOp(derivedScheme(), 2, { label: "Резка провода", opSec: "8", params: "L = 300 мм" });
    const next = rederive(manual, derived);
    const op = opBy(next, CUT_W1);
    expect(op).toMatchObject({ label: "Резка в размер", opSec: "8", params: "L = 300 мм", edited: ["label"] });
    expect(op.kdBase).toEqual({ label: JSON.stringify("Резка провода") });
    expect(op.kdChanges!.map(kdChangeText)).toEqual([
      "Название: в КД было «Резка» → стало «Резка провода»",
      "Операция, с/шт: в КД было «6» → стало «8»",
      "Параметры: в КД было «пусто» → стало «L = 300 мм»",
    ]);
    // Тот же КД ещё раз — ничего не меняется, подсказки остаются.
    expect(rederive(next, derived)).toEqual(next);
    expect(opBy(rederive(next, derived), CUT_W1)).toBe(op);
    // Операция без изменений в КД — тот же объект.
    expect(opBy(next, HEAT_H)).toBe(opBy(manual, HEAT_H));
  });

  it("3) never touches the master's blocks, order and edges", () => {
    let manual = moveOp(derivedScheme(), 3, -1); // опрессовка раньше резки
    manual = renameBlock(setPerUnit(manual, "b2", "b1", 2), "b2", "Провод с терминалом");
    manual = addBlock(selectBlock(manual, "b1"), "Трубка"); // b3 — свой блок мастера
    manual = setBlockChecked(setBlockKind(manual, "b2", "assembly"), "b2", true);
    const derived = updateOp(derivedScheme(), 3, { opSec: "9" });
    const next = rederive(manual, derived);
    expect(next.blocks).toEqual(manual.blocks);
    expect(next.edges).toEqual(manual.edges);
    expect(labelsIn(next, "b2")).toEqual(["Опрессовка", "Резка"]);
    expect(opBy(next, PRS_W1).opSec).toBe("9");
  });

  it("4) marks a key that left the КД as «нет в КД» instead of deleting it, and clears the mark when it comes back", () => {
    const manual = derivedScheme();
    const derived = removeOp(derivedScheme(), 3); // в КД больше нет опрессовки
    const next = rederive(manual, derived);
    expect(opBy(next, PRS_W1)).toMatchObject({ kdGone: true, label: "Опрессовка" });
    expect(labelsIn(next, "b2")).toEqual(["Резка", "Опрессовка"]);
    expect(rederive(next, derived)).toEqual(next);
    expect(opBy(rederive(next, derivedScheme()), PRS_W1)).not.toHaveProperty("kdGone");
  });

  it("5) leaves the master's own operations alone", () => {
    const manual = updateOp(addCustomOp(derivedScheme(), "b2"), 4, { label: "Прозвонка", opSec: "30" });
    const own = manual.ops.find((op) => op.seq === 4)!;
    const next = rederive(manual, updateOp(derivedScheme(), 2, { label: "Резка провода" }));
    expect(next.ops.find((op) => op.seq === 4)).toBe(own);
    expect(labelsIn(next, "b2")).toEqual(["Резка провода", "Опрессовка", "Прозвонка"]);
  });

  it("block kind: final is the product, otherwise set by the master or by the scheme", () => {
    let state = derivedScheme();
    expect(blockKind(state, "b1")).toBe("final");
    expect(blockKind(state, "b2")).toBe("semiFinished");
    state = addBlock(state, "Заготовка"); // b3 → b2: у b2 теперь вход — сборка
    expect(blockKind(state, "b2")).toBe("assembly");
    state = setBlockKind(state, "b2", "semiFinished");
    expect(blockKind(state, "b2")).toBe("semiFinished");
    expect(setBlockKind(state, "b1", "assembly")).toEqual(state);
    expect(setBlockQuantity(state, "b3", 4).blocks[2].quantity).toBe(4);
    expect(setBlockQuantity(setBlockQuantity(state, "b3", 4), "b3", 1).blocks[2]).not.toHaveProperty("quantity");
    expect(setBlockQuantity(state, "b3", 0)).toBe(state);
  });

  it("saves and loads the КД links, block kind, quantity, КД objects and the master's check", () => {
    let state = updateOp(derivedScheme(), 2, { label: "Резка в размер" });
    state = rederive(state, updateOp(removeOp(derivedScheme(), 3), 2, { opSec: "8" }));
    state = setBlockChecked(setBlockQuantity(setBlockKind(state, "b2", "assembly"), "b2", 4), "b2", true);
    state = { ...state, blocks: state.blocks.map((block) => (block.key === "b2" ? { ...block, refs: ["w1", "x1"] } : block)) };
    state = removeOp(updateOp(addCustomOp(state, "b1"), 4, { origin: kdKey("h1", "MARK"), label: "Маркировка" }), 4);
    const config = manualConfig(state, base);
    const route = config.route as { blocks: Array<Record<string, unknown>> };
    expect(route.blocks.find((block) => block.key === "b2")).toMatchObject({ kind: "assembly", quantity: 4, refs: ["w1", "x1"], checked: true });
    expect(route.blocks.find((block) => block.key === "b1")).not.toHaveProperty("kind");
    expect((config.ops as Array<Record<string, unknown>>).find((op) => op.origin === PRS_W1)).toMatchObject({ kdGone: true });
    expect(config.kdDropped).toEqual([kdKey("h1", "MARK")]);
    const restored = manualFromConfig(config)!;
    const byKey = (item: ManualState) => [...item.blocks].sort((a, b) => a.key.localeCompare(b.key));
    expect(byKey(restored)).toEqual(byKey(state));
    expect(restored.kdDropped).toEqual(state.kdDropped);
    expect(restored.extra).toBeUndefined();
    expect(opBy(restored, CUT_W1)).toEqual(opBy(state, CUT_W1));
    expect(opBy(restored, PRS_W1).kdGone).toBe(true);
    expect(manualConfig(restored, base)).toEqual(config);
    // Операция генератора: связь с КД не попадает в её поля генератора.
    const gen = manualFromConfig({ mode: "manual", ops: [{ type: "cutWireStrip", seq: 1, origin: CUT_W1, edited: ["opSec", "nope"], kdGone: true, wires: [] }] })!;
    expect(gen.ops[0].gen).toEqual({ type: "cutWireStrip", wires: [] });
    expect(gen.ops[0]).toMatchObject({ origin: CUT_W1, edited: ["opSec"], kdGone: true });
    expect((manualConfig(gen, base).ops as Array<Record<string, unknown>>)[0]).toMatchObject({ type: "cutWireStrip", origin: CUT_W1, edited: ["opSec"], kdGone: true });
  });

  it("loads an old build_config unchanged: no new keys on blocks and operations, the same payload back", () => {
    const old = {
      mode: "manual", assemblyIndex: "КБ1", assemblyName: "Провод", partQty: 10,
      ops: [
        { type: "libraryOp", templateId: "cut", opNum: "CUT_WIRE_hand", side: "", seq: 4, label: "Резка", qty: "1", tPrep: "120", tOp: "6" },
        { type: "customOp", seq: 7, label: "Пайка экрана", qty: "1", tPrep: "", tOp: "45", steps: [], tooling: [] },
      ],
      route: { blocks: [{ key: "b2", name: "Заготовка", ops: [1], final: false }, { key: "b1", ops: [2], final: true }], edges: [{ from: "b2", to: "b1", per_unit: 2 }] },
    };
    const state = manualFromConfig(old)!;
    expect(state.blocks.map((block) => Object.keys(block).sort())).toEqual([["final", "key", "name", "ops"], ["final", "key", "name", "ops"]]);
    expect(state.ops.some((op) => ["origin", "edited", "kdBase", "kdChanges", "kdGone"].some((key) => key in op))).toBe(false);
    expect(state).not.toHaveProperty("kdDropped");
    expect(blockKind(state, "b2")).toBe("semiFinished");
    const config = manualConfig(state, base);
    expect(config.route).toEqual(old.route);
    expect(config.ops).toEqual(old.ops);
    expect(config).not.toHaveProperty("kdDropped");
    // Пересчёт по КД не трогает старую схему без ключей КД.
    expect(rederive(state, derivedScheme()).ops.slice(0, 2)).toEqual(state.ops);
  });
});

/* «Предложить из КД»: build_config, как его отдаёт POST …/harness/{index}/derive — блоки по маршруту КД (`route:<строка>`),
   операции генератора без карточек и операция маршрута («Сборка жгута»). */
const fromHarness = () => ({
  mode: "manual", assemblyIndex: "КБ7", assemblyName: "Жгут", partQty: 10,
  ops: [
    { type: "cutWireStrip", side: "", tPrep: "", tOp: "", wires: [{ name: "W1", art: "МГТФ 0,35", qty: 1, length: 300 }], strip: { a: true, b: true },
      label: "Резка с разделкой: W1", kdData: [{ wire_id: "w1" }], origin: "harness:r1:cutWireStrip.AB", seq: 1 } as Record<string, unknown>,
    { type: "prsTerm", side: "B", tPrep: "", tOp: "", ep: { connName: "X2", connArt: "", label: "X2", termArt: "ЛП-3", termName: "" },
      wires: [{ name: "W1", art: "МГТФ 0,35", qty: 1 }], label: "Опрессовка терминала (В) — X2", kdData: [{}], origin: "harness:X2:prsTerm.B", seq: 2 },
    { type: "solderConn", side: "A", tPrep: "", tOp: "", ep: { connName: "XT60", connArt: "XT60-M", label: "XT60", termArt: "", termName: "" },
      wires: [{ name: "W1", art: "МГТФ 0,35", qty: 1 }], label: "Пайка разъёма (А) — XT60", kdData: [{}], origin: "harness:X1:solderConn.A", seq: 3 },
    { type: "heatTut", side: "", tPrep: "", tOp: "", tutArt: "ТУТ 6/3", tutName: "ТУТ 6/3", tutCount: 1, label: "Усадка ТУТ — H1", kdData: [{}],
      origin: "harness:h1:heatTut", seq: 4 },
    { type: "customOp", label: "Сборка жгута", qty: "1", tPrep: "", tOp: "", opNum: "", steps: [], tooling: [], origin: "harness:s1:assembly", seq: 5 },
  ] as Array<Record<string, unknown>>,
  route: {
    blocks: [
      { key: "route:r1", name: "Провод W1", ops: [1, 2, 3], final: false, kind: "semiFinished" },
      { key: "route:r2", name: "Жгут", ops: [4, 5], final: true },
    ],
    edges: [{ from: "route:r1", to: "route:r2", per_unit: 1 }],
  },
});

describe("manual assembly: «Предложить из КД»", () => {
  it("takes the КД scheme as is into an empty scheme instead of dumping it into «Новое из КД»", () => {
    const derived = manualFromConfig(fromHarness())!;
    expect(isEmptyManual(emptyManual())).toBe(true);
    const next = proposeFromKd(emptyManual(), derived, "v1");
    expect(next).toEqual({ ...derived, kdVersion: "v1" });
    expect(next.blocks.map((block) => [block.key, block.name])).toEqual([["route:r1", "Провод W1"], ["route:r2", "Жгут"]]);
    expect(next.blocks.some((block) => block.name === "Новое из КД")).toBe(false);
    // Слияние в пустую схему так и сделало бы: всё, кроме итога, — в «Новое из КД».
    expect(rederive(emptyManual(), derived).blocks.some((block) => block.name === "Новое из КД")).toBe(true);
    expect(manualProblem(next, "Жгут")).toBeNull();
  });

  it("merges into a non-empty scheme by rederive and counts what will be added, changed and gone from the КД", () => {
    const derived = manualFromConfig(fromHarness())!;
    // Мастер взял схему, правил название резки, удалил пайку и добавил свою операцию; в КД потом — новая резка ТУТ,
    // другая ТУТ у усадки и нет опрессовки.
    let manual = proposeFromKd(emptyManual(), derived, "v1");
    manual = updateOp(manual, 1, { label: "Резка W1 в размер" });
    manual = removeOp(manual, 3);
    manual = updateOp(addCustomOp(manual, "route:r2"), manual.nextSeq, { label: "Прозвонка" });
    const config = fromHarness();
    config.ops = config.ops.filter((op) => op.seq !== 2).map((op) => (op.seq === 4 ? { ...op, tutArt: "ТУТ 9/3" } : op));
    config.ops.push({ type: "cutTut", side: "", tPrep: "", tOp: "", tutArt: "ТУТ 6/3", tutName: "ТУТ 6/3", tutCount: 1, label: "Резка ТУТ — H1",
      kdData: [{}], origin: "harness:h1:cutTut", seq: 6 });
    config.route.blocks[0].ops = [1, 3];
    config.route.blocks[1].ops = [4, 5, 6];
    const fresh = manualFromConfig(config)!;
    expect(isEmptyManual(manual)).toBe(false);
    const next = proposeFromKd(manual, fresh, "v2");
    expect(next).toEqual({ ...rederive(manual, fresh), kdVersion: "v2" });
    expect(kdSummary(manual, next)).toEqual({ added: 1, changed: 1, gone: 1 });
    expect(next.ops.find((op) => op.origin === "harness:r1:cutWireStrip.AB")!.label).toBe("Резка W1 в размер"); // правка мастера осталась
    expect(next.ops.some((op) => op.origin === "harness:X1:solderConn.A")).toBe(false); // удалённая мастером не вернулась
    // Тот же КД ещё раз — менять нечего.
    expect(kdSummary(next, proposeFromKd(next, fresh, "v2"))).toEqual({ added: 0, changed: 0, gone: 0 });
    // Мастер удалил всё: схема без операций, но с удалёнными из КД — не пустая, удалённые не вернутся.
    const cleared = manual.ops.reduce((state, op) => removeOp(state, op.seq), manual);
    expect(cleared.ops).toHaveLength(0);
    expect(isEmptyManual(cleared)).toBe(false);
    expect(proposeFromKd(cleared, derived).ops.some((op) => op.origin === "harness:X1:solderConn.A")).toBe(false);
  });

  it("saves the КД version it was proposed from and marks the scheme when the drawing changed since", () => {
    const state = proposeFromKd(emptyManual(), manualFromConfig(fromHarness())!, "v1");
    const config = manualConfig(state, base);
    expect(config.kdHarnessVersion).toBe("v1");
    const back = manualFromConfig(config)!;
    expect(back.kdVersion).toBe("v1");
    expect(back.extra).toBeUndefined();
    expect(kdStale(back, "v1")).toBe(false);
    expect(kdStale(back, "v2")).toBe(true);
    expect(kdStale(back, null)).toBe(false); // чертежа нет или ещё не прочитан — не отмечаем
    // Схема не из КД — без версии и без отметки.
    const own = manualFromConfig(manualConfig(three(), base))!;
    expect(own).not.toHaveProperty("kdVersion");
    expect(manualConfig(own, base)).not.toHaveProperty("kdHarnessVersion");
    expect(kdStale(own, "v2")).toBe(false);
  });

  it("asks the generator's card pick with the generator's hint or category per operation and fills code and БД.ОП norms", () => {
    const derived = manualFromConfig(fromHarness())!;
    const specs = derived.ops.map(kdOpSpec);
    expect(specs.map((spec) => spec && { tmplHint: spec.tmplHint, cat: spec.cat, side: spec.side, termArt: spec.termArt })).toEqual([
      { tmplHint: "CUT_WIRE+2strip", cat: undefined, side: undefined, termArt: undefined },
      { tmplHint: undefined, cat: "Опрессовка терминалов (В)", side: "B", termArt: "ЛП-3" },
      { tmplHint: "SDR_XT60", cat: undefined, side: "A", termArt: undefined },
      { tmplHint: "HEAT_TTK_short", cat: "Усадка", side: undefined, termArt: undefined },
      null, // операция маршрута — своя, карточка ей не нужна
    ]);
    const cards: Record<string, KdCard> = {
      "CUT_WIRE+2strip": { templateId: "cut2", code: "CUT_WIRE+2strip", prepSec: "600", opSec: "5" },
      "Опрессовка терминалов (В)": { templateId: "prsB", code: "PRS_TERMINAL_hand", prepSec: "", opSec: "" },
    };
    const filled = fillKdCards(derived, (spec) => cards[spec.tmplHint || spec.cat || ""] ?? null);
    const op = (seq: number) => filled.ops.find((item) => item.seq === seq)!;
    expect(op(1)).toMatchObject({ templateId: "cut2", code: "CUT_WIRE+2strip", prepMin: "10", opSec: "5" });
    expect(op(1).gen).toMatchObject({ type: "cutWireStrip", templateId: "cut2", opNum: "CUT_WIRE+2strip" });
    expect(op(2)).toMatchObject({ templateId: "prsB", code: "PRS_TERMINAL_hand", prepMin: "", opSec: "" });
    // Не подобралась — остаётся без карточки, как пришла.
    expect(op(3)).toBe(derived.ops[2]);
    expect(op(5)).toBe(derived.ops[4]);
    const payload = manualConfig(filled, base).ops as Array<Record<string, unknown>>;
    expect(payload[0]).toMatchObject({ type: "cutWireStrip", templateId: "cut2", opNum: "CUT_WIRE+2strip", tPrep: "600", tOp: "5", origin: "harness:r1:cutWireStrip.AB" });
    expect(payload[2]).not.toHaveProperty("templateId");
    // Карточка уже есть — не перебирается.
    expect(fillKdCards(filled, () => ({ templateId: "other", code: "X", prepSec: "", opSec: "" })).ops[0]).toBe(op(1));
  });
});
