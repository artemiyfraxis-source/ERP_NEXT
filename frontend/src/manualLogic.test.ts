import { describe, expect, it } from "vitest";
import {
  addCustomOp,
  addLibraryOp,
  cardLabel,
  emptyManual,
  manualConfig,
  manualFromConfig,
  manualProblem,
  moveOp,
  removeOp,
  setRoute,
  stepsPayload,
  toolingPayload,
  updateOp,
  type LibraryCard,
  type ManualState,
} from "./manualLogic";
import type { Route } from "./routeLogic";

const CUT: LibraryCard = { id: "cut", code: "CUT_WIRE_hand", side: "", title: "CUT_WIRE_hand | Резка", name: "Резка", category: "Резка" };
const CRIMP: LibraryCard = { id: "prsA", code: "PRS_TERMINAL_auto", side: "A", title: "PRS_TERMINAL_auto | Опрессовка терминалов (A)", name: "", category: "" };
const OPS = [{ opNumber: "CUT_WIRE_hand", opName: "Резка", tPrep: "120", tOp: "6", tMachine: "" }];

function three(): ManualState {
  let state = addLibraryOp(emptyManual(), CUT, OPS);
  state = addCustomOp(state);
  state = updateOp(state, 2, { label: "Пайка экрана", opSec: "45" });
  return addLibraryOp(state, CRIMP, OPS);
}

const base = { assemblyIndex: "КБ1", assemblyName: "Провод", partQty: 10 };

describe("manual assembly list", () => {
  it("names library cards in Russian with their side and takes the norms from БД.ОП", () => {
    expect(cardLabel(CRIMP)).toBe("Опрессовка терминалов (А)");
    const state = three();
    expect(state.ops.map((op) => [op.seq, op.kind, op.label, op.prepMin, op.opSec])).toEqual([
      [1, "library", "Резка", "2", "6"], [2, "custom", "Пайка экрана", "", "45"], [3, "library", "Опрессовка терминалов (А)", "", ""],
    ]);
    expect(state.nextSeq).toBe(4);
  });

  it("keeps a hand-edited scheme on its operations through a reorder, a removal and an addition", () => {
    // Резка и пайка — один блок, опрессовка — итог.
    const merged: Route = {
      blocks: [{ key: "b2", name: "Провод с лепестком", ops: [1, 2], final: false, components: [] }, { key: "b3", name: "", ops: [3], final: true, components: [] }],
      edges: [{ from: "b2", to: "b3", per_unit: 2 }],
    };
    let state = setRoute(three(), merged);
    state = moveOp(state, 3, -1); // опрессовка — второй в списке, блоки при своих операциях
    expect(state.route?.blocks.map((block) => [block.key, block.ops, block.final])).toEqual([["b2", [1, 3], false], ["b3", [2], true]]);
    state = removeOp(state, 1); // резка ушла из блока, блок остался с пайкой
    expect(state.route?.blocks.map((block) => [block.key, block.ops])).toEqual([["b3", [1]], ["b2", [2]]]);
    expect(state.route?.edges).toEqual([{ from: "b2", to: "b3", per_unit: 2 }]);
    state = addCustomOp(state); // новая — новым итоговым блоком после прежнего итога
    expect(state.route?.blocks.map((block) => [block.key, block.ops, block.final])).toEqual([["b3", [1], false], ["b2", [2], false], ["b4", [3], true]]);
    expect(state.route?.edges).toEqual([{ from: "b2", to: "b3", per_unit: 2 }, { from: "b3", to: "b4", per_unit: 1 }]);
    expect(state.notice).toBe("");
  });

  it("drops the hand edits it cannot carry over and says so", () => {
    // Итог с двумя входами: убрать его — схема не сходится, снова цепочка.
    const route: Route = {
      blocks: [{ key: "b1", name: "", ops: [1], final: false, components: [] }, { key: "b2", name: "", ops: [2], final: false, components: [] }, { key: "b3", name: "", ops: [3], final: true, components: [] }],
      edges: [{ from: "b1", to: "b3", per_unit: 1 }, { from: "b2", to: "b3", per_unit: 1 }],
    };
    const state = removeOp(setRoute(three(), route), 3);
    expect(state.route).toBeNull();
    expect(state.notice).toMatch(/схема снова цепочкой по списку/);
  });

  it("builds the payload: seconds, own steps with controls, tooling by section, the chain scheme with block components", () => {
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
    expect(config.route).toEqual({
      blocks: [
        { key: "b1", ops: [1], final: false, components: [{ id: "kit-0", art: "МГТФ 0,35", name: "МГТФ", qty: "0.6" }] },
        { key: "b2", ops: [2], final: false }, { key: "b3", ops: [3], final: true },
      ],
      edges: [{ from: "b1", to: "b2", per_unit: 1 }, { from: "b2", to: "b3", per_unit: 1 }],
    });
  });

  it("comes back from the saved build config with the same list, numbers and hand-edited scheme", () => {
    let state = updateOp(three(), 2, { steps: "Зачистить", controls: "Пайка | IPC", tooling: "Оборудование: станция", prepMin: "5" });
    state = setRoute(state, {
      blocks: [{ key: "b1", name: "Заготовка", ops: [1, 2], final: false, components: [] }, { key: "b3", name: "", ops: [3], final: true, components: [] }],
      edges: [{ from: "b1", to: "b3", per_unit: 3 }],
    });
    const restored = manualFromConfig(manualConfig(state, base));
    expect(restored?.ops).toEqual(state.ops);
    expect(restored?.nextSeq).toBe(4);
    expect(restored?.route).toEqual(state.route);
    // Без правок схема — цепочка, ручной не считается.
    expect(manualFromConfig(manualConfig(three(), base))?.route).toBeNull();
    expect(manualFromConfig({ ops: [] })).toBeNull();
    expect(stepsPayload(state.ops[1])).toHaveLength(2);
    expect(toolingPayload(state.ops[1])).toEqual([{ section: "Оборудование", name: "станция" }]);
  });

  it("says in Russian what keeps the list from being built", () => {
    expect(manualProblem(emptyManual())).toBe("Ручная сборка: добавьте хотя бы одну операцию — из библиотеки или свою.");
    const state = three();
    expect(manualProblem(state)).toBeNull();
    expect(manualProblem(updateOp(state, 2, { label: " " }))).toBe("Ручная сборка: операция 2 — укажите название своей операции.");
    expect(manualProblem(updateOp(state, 1, { opSec: "шесть" }))).toBe("Ручная сборка: операция 1 «Резка» — время на штуку должно быть числом секунд не меньше нуля.");
    expect(manualProblem(updateOp(state, 1, { prepMin: "-1" }))).toBe("Ручная сборка: операция 1 «Резка» — подготовка должна быть числом минут не меньше нуля.");
    expect(manualProblem(updateOp(state, 3, { qty: "0" }))).toBe("Ручная сборка: операция 3 «Опрессовка терминалов (А)» — количество на изделие должно быть числом больше нуля.");
  });
});
