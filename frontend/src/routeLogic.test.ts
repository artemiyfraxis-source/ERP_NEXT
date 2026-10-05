import { expect, it } from "vitest";
import {
  deriveRoute, documentMinutes, mergeBlocks, moveOp, parseRoute, routeChain, routeLayout, routePayload, routeProblem, routeTotals,
  splitBlock, type Route, type RouteOp,
} from "./routeLogic";

const op = (position: number, type: string, label = `Операция ${position}`, minutes: number | null = null): RouteOp =>
  ({ position, key: `k${position}`, type, code: "", label, minutes });
const block = (key: string, ops: number[], final = false) => ({ key, name: "", ops, final, components: [] });
const arrows = (route: Route) => route.edges.map((edge) => `${edge.from}>${edge.to}`);

it("derives the chain of a wire harness and the side branch of TUT pieces like the ETK builder", () => {
  expect(arrows(deriveRoute([op(1, "cutWire"), op(2, "prsTerm"), op(3, "prsTerm")]))).toEqual(["b1>b2", "b2>b3"]);
  const tut = deriveRoute([op(1, "cutWire"), op(2, "cutTut"), op(3, "insTut"), op(4, "solderConn")]);
  expect(arrows(tut)).toEqual(["b1>b3", "b2>b3", "b3>b4"]);
  expect(tut.blocks.map((item) => item.final)).toEqual([false, false, false, true]);
  // две резки до первой операции — обе ветки сходятся в ней; резка по коду карточки тоже начинает ветку
  expect(arrows(deriveRoute([op(1, "cutWire"), { ...op(2, "other"), code: "CUT_X" }, op(3, "sdr")]))).toEqual(["b1>b3", "b2>b3"]);
});

it("computes block time, inputs and the rolled-up time per piece the way the backend does", () => {
  // build_etk: блок b1 — 10,54 мин на партию 5 → 2,108 мин/шт
  const route = deriveRoute([op(1, "cutWire", "Резка", 10.54), op(2, "prsTerm", "Опрессовка", 5)]);
  const totals = routeTotals(route, [op(1, "cutWire", "Резка", 10.54), op(2, "prsTerm", "Опрессовка", 5)], 5);
  expect(totals.get("b1")).toMatchObject({ minutes: 10.54, inputs: 0, opCount: 1 });
  expect(totals.get("b1")?.unit).toBeCloseTo(2.108, 6);
  expect(totals.get("b1")?.rolled).toBeCloseTo(2.108, 6);
  expect(totals.get("b2")?.inputs).toBe(1);
  expect(totals.get("b2")?.rolled).toBeCloseTo(1 + 2.108, 6);
  expect(totals.get("b2")?.opCount).toBe(2);
  // два входа по 2 штуки с блока: время входа считается дважды
  const edited = { ...route, edges: [{ from: "b1", to: "b2", per_unit: 2 }] };
  expect(routeTotals(edited, [op(1, "cutWire", "", 10.54), op(2, "prsTerm", "", 5)], 5).get("b2")).toMatchObject({ inputs: 2 });
  expect(routeTotals(edited, [op(1, "cutWire", "", 10.54), op(2, "prsTerm", "", 5)], 5).get("b2")?.rolled).toBeCloseTo(1 + 2 * 2.108, 6);
});

it("explains in Russian what is wrong with a scheme, the way the server does", () => {
  const ops = [op(1, "cutWire", "Резка"), op(2, "cutTut", "Резка ТУТ"), op(3, "insTut", "Надеть ТУТ")];
  const fine: Route = { blocks: [block("b1", [1]), block("b2", [2]), block("b3", [3], true)], edges: [{ from: "b1", to: "b3", per_unit: 1 }, { from: "b2", to: "b3", per_unit: 1 }] };
  expect(routeProblem(fine, ops)).toBeNull();
  expect(routeProblem({ ...fine, edges: fine.edges.slice(0, 1) }, ops)).toBe("блок «Резка ТУТ» не доходит до итогового блока");
  expect(routeProblem({ ...fine, blocks: fine.blocks.map((item) => (item.key === "b3" ? { ...item, final: false } : item)) }, ops)).toBe("не отмечен итоговый блок (изделие)");
  expect(routeProblem({ ...fine, blocks: [block("b1", [1]), block("b2", [2]), block("b3", [3, 2], true)] }, ops)).toBe("операция 2 входит в несколько блоков — «Резка ТУТ» и «Надеть ТУТ»");
  expect(routeProblem({ ...fine, blocks: [block("b1", [1]), block("b3", [3], true)] }, ops)).toBe("операция 2 не входит ни в один блок");
  expect(routeProblem({ ...fine, blocks: [block("b1", [1]), block("b2", [2, 9]), block("b3", [3], true)] }, ops)).toBe("операции 9 нет в техкарте (операций 3)");
  const loop: Route = { blocks: [block("b1", [1]), block("b2", [2]), block("b3", [3], true)],
    edges: [{ from: "b1", to: "b2", per_unit: 1 }, { from: "b2", to: "b1", per_unit: 1 }, { from: "b2", to: "b3", per_unit: 1 }] };
  expect(routeProblem(loop, ops)).toBe("схема зациклена — «Резка» → «Резка ТУТ» → «Резка»");
});

it("merges adjacent blocks: operations together, the downstream block stays, shared arrows add up", () => {
  const route: Route = { blocks: [block("a", [1]), block("b", [2]), block("c", [3], true)],
    edges: [{ from: "a", to: "b", per_unit: 1 }, { from: "a", to: "c", per_unit: 2 }, { from: "b", to: "c", per_unit: 3 }] };
  const merged = mergeBlocks(route, "a", "b");
  expect(merged.blocks.map((item) => [item.key, item.ops])).toEqual([["b", [1, 2]], ["c", [3]]]);
  expect(merged.edges).toEqual([{ from: "b", to: "c", per_unit: 5 }]);
});

it("splits and moves operations without losing any", () => {
  const route: Route = { blocks: [block("b1", [1]), block("b2", [2, 3, 4], true)], edges: [{ from: "b1", to: "b2", per_unit: 2 }] };
  const split = splitBlock(route, "b2", 3);
  expect(split.blocks.map((item) => [item.key, item.ops, item.final])).toEqual([["b1", [1], false], ["b3", [2, 3], false], ["b2", [4], true]]);
  expect(arrows(split).sort()).toEqual(["b1>b3", "b3>b2"]);
  expect(split.edges.find((edge) => edge.from === "b1")?.per_unit).toBe(2);
  expect(splitBlock(route, "b2", 4)).toBe(route); // после последней операции делить нечего
  const moved = moveOp(route, 2, "b1");
  expect(moved.blocks.map((item) => item.ops)).toEqual([[1, 2], [3, 4]]);
  expect(moveOp(moved, 1, "b1")).toBe(moved); // в тот же блок не двигаем
  expect(moveOp({ blocks: [block("x", [1]), block("y", [2], true)], edges: [] }, 1, "y").blocks.map((item) => item.ops)).toEqual([[1], [2]]); // единственную операцию блока не переносим
});

it("wraps a level that has too many blocks instead of widening the scheme", () => {
  const inputs = ["a", "b", "c", "d", "e", "f"];
  const route: Route = { blocks: [...inputs.map((key, index) => block(key, [index + 1])), block("z", [9], true)], edges: inputs.map((from) => ({ from, to: "z", per_unit: 1 })) };
  const layout = routeLayout(route, 4);
  expect(layout.cols).toBe(3);
  expect(layout.layers).toBe(3); // 3 + 3 входов и итог
  expect(inputs.map((key) => layout.cells.get(key)!.layer)).toEqual([0, 0, 0, 1, 1, 1]);
  expect(inputs.map((key) => layout.cells.get(key)!.level)).toEqual([1, 1, 1, 1, 1, 1]);
  expect(layout.cells.get("z")!.layer).toBe(2);
});

it("draws a seven-block scheme narrow: a direct input sits right above the final block, siblings are centred over their target", () => {
  const route: Route = {
    blocks: [block("b1", [1]), block("b2", [2]), block("b3", [3]), block("b4", [4]), block("b5", [5]), block("b6", [6], true), block("b7", [7])],
    edges: [["b1", "b4"], ["b2", "b4"], ["b3", "b5"], ["b4", "b5"], ["b5", "b6"], ["b7", "b6"]].map(([from, to]) => ({ from, to, per_unit: 1 })),
  };
  const layout = routeLayout(route);
  const level = (key: string) => layout.cells.get(key)!.level;
  expect(["b6", "b5", "b7", "b4", "b3", "b1", "b2"].map(level)).toEqual([0, 1, 1, 2, 2, 3, 3]);
  expect(layout.cells.get("b7")!.layer).toBe(layout.cells.get("b6")!.layer - 1); // прямой вход — строкой над итогом
  expect(layout.cells.get("b5")!.layer).toBe(layout.cells.get("b7")!.layer);
  expect(layout.layers).toBe(4);
  expect(layout.cols).toBe(2); // ни одна строка не шире своих веток
  for (const layer of [0, 1, 2, 3]) {
    const xs = [...layout.cells.values()].filter((cell) => cell.layer === layer).map((cell) => cell.x).sort((a, b) => a - b);
    expect(xs.length).toBeLessThanOrEqual(2);
    xs.forEach((x, index) => { expect(x).toBeGreaterThanOrEqual(0); expect(x).toBeLessThanOrEqual(1); if (index) expect(x - xs[index - 1]).toBeGreaterThanOrEqual(1); });
  }
  expect(layout.cells.get("b6")!.x).toBeCloseTo(0.5, 6); // итог по центру
});

it("lays blocks out top to bottom by the distance to the final block and recognises a single chain", () => {
  const tut = deriveRoute([op(1, "cutWire"), op(2, "cutTut"), op(3, "insTut"), op(4, "solderConn")]);
  const layout = routeLayout(tut);
  expect([...layout.cells].map(([key, cell]) => `${key}:${cell.level}.${cell.layer}.${cell.row}`)).toEqual(["b1:2.0.0", "b2:2.0.1", "b3:1.1.0", "b4:0.2.0"]);
  expect(layout.layers).toBe(3);
  expect(layout.rows).toBe(2);
  expect(layout.cols).toBe(2);
  expect(routeChain(tut)).toBeNull();
  expect(routeChain(deriveRoute([op(1, "cutWire"), op(2, "prsTerm"), op(3, "prsTerm")]))).toEqual(["b1", "b2", "b3"]);
  expect(routeChain(deriveRoute([op(1, "cutWire")]))).toBeNull(); // один блок — нечего рисовать цепочкой
});

it("sends the names only when they were typed and reads back what it sent", () => {
  const route: Route = { blocks: [{ ...block("b1", [1]), name: "  Нарезанный  " }, block("b2", [2], true)], edges: [{ from: "b1", to: "b2", per_unit: 1.5 }] };
  const payload = routePayload(route);
  expect(payload).toEqual({
    blocks: [{ key: "b1", name: "Нарезанный", ops: [1], final: false }, { key: "b2", ops: [2], final: true }],
    edges: [{ from: "b1", to: "b2", per_unit: 1.5 }],
  });
  expect(parseRoute(payload)).toEqual({ blocks: [{ ...block("b1", [1]), name: "Нарезанный" }, block("b2", [2], true)], edges: route.edges });
  expect(parseRoute(null)).toBeNull();
  expect(parseRoute({ blocks: [] })).toBeNull();
});

it("takes operation norms from the stored ETK only while it was built from the same operations", () => {
  const document = {
    kit: { part_quantity: "5" },
    build_config: { partQty: 5 },
    operations: [
      { type: "cutWire", time: [{ name: "Провод", norm: "10,54" }, { name: "Провод 2", norm: "1" }] },
      { type: "prsTerm", times: { operation_seconds: "6", setup_seconds: "60" } },
    ],
  };
  expect(documentMinutes(document, ["cutWire", "prsTerm"])).toEqual({ minutes: [11.54, 1.5], quantity: 5 });
  expect(documentMinutes(document, ["cutWire"])).toBeNull();
  expect(documentMinutes(document, ["cutWire", "insTerm"])).toBeNull();
  expect(documentMinutes(null, ["cutWire"])).toBeNull();
});
