// @vitest-environment node
import { expect, it } from "vitest";
import { buildConfigDrawing, planFromConfig } from "./configToHarness";
import { configDrawingProblem } from "./GenDrawing";
import { parseHarnessDesignDocument } from "./harness/editor/model";

/* «Построить чертёж» на «Коакс» и «Ручной сборке»: план по конфигурации сборки → тот же документ чертежа. */

const coax = (sideB: Record<string, unknown> | null, ops: string[] = ["coaxCut", "coaxCutTut", "coaxInsTut"]) => ({
  coax: { cableLength: 150, tutLength: 20, tutArt: "ТУТ 3/1", cableArt: "RG316", sideA: { article: "SMA-J" }, sideB },
  ops: ops.map((type) => ({ type })),
});

it("draws a coax assembly: connector to connector, centre contact to centre contact, TUT at both ends", async () => {
  const plan = planFromConfig(coax({ article: "SMA-K" }));
  expect(plan.connectors.map((c) => [c.designation, c.article, c.side])).toEqual([["XS1", "SMA-J", "A"], ["XS2", "SMA-K", "B"]]);
  expect(plan.wires).toMatchObject([{ mark: "RG316", lengthMm: 150, from: { connector: "A", pin: 1 }, to: { connector: "B", pin: 1 } }]);
  expect(plan.coverings.map((c) => [c.name, c.lengthMm, c.at])).toEqual([["ТУТ 3/1 на XS1", 20, "A"], ["ТУТ 3/1 на XS2", 20, "B"]]);
  const { document, connectors, wires } = await buildConfigDrawing(coax({ article: "SMA-K" }), {}, null);
  expect([connectors, wires]).toEqual([2, 1]);
  expect(document.connectors.map((c) => c.partNumber)).toEqual(["SMA-J", "SMA-K"]);
  expect(document.wires[0].lengthMm).toBe(150);
  expect(document.physicalTopology!.coverings).toHaveLength(2);
  expect(() => parseHarnessDesignDocument(JSON.parse(JSON.stringify(document)))).not.toThrow();
  // Без ТУТ — без оболочек.
  expect(planFromConfig(coax({ article: "SMA-K" }, ["coaxCut"])).coverings).toEqual([]);
});

it("tells why a coax or a manual assembly cannot be drawn", () => {
  expect(configDrawingProblem(coax({ article: "SMA-K" }))).toBe("");
  expect(configDrawingProblem(coax(null))).toBe("Сторона В без разъёма — чертёж строится от разъёма до разъёма");
  expect(configDrawingProblem({ coax: { sideA: null, sideB: null } })).toBe("Не выбран разъём стороны А");
  expect(configDrawingProblem({ mode: "manual", ops: [] })).toBe("В сборке нет соединений по распиновке");
  expect(() => planFromConfig(coax(null))).toThrow("Сторона В без разъёма — чертёж строится от разъёма до разъёма.");
});

const end = (endKey: string, conn: string, pin: number, label = "") => ({ end: endKey, conn, label, pin });
const manual = {
  mode: "manual", ops: [], partQty: 1,
  wires: [
    { art: "МГТФ 0,12 красный", name: "МГТФ 0,12 красный", qty: 1, length: 310 },
    { art: "МГТФ 0,12 черный", name: "МГТФ 0,12 черный", qty: 1, length: 310 },
    { art: "МГТФ 0,12 белый", name: "МГТФ 0,12 белый", qty: 1, length: 310 },
  ],
  twist: { enabled: true, mode: "pairs", pairs: [[0, 1, 2]], wireIndices: [0, 1, 2], pitch: "20" },
  connections: [
    { wire: "МГТФ 0,12 красный", from: end("А1", "PHR-3", 1), to: end("В1", "XHP-3", 1) },
    { wire: "МГТФ 0,12 черный", from: end("А1", "PHR-3", 2), to: end("В1", "XHP-3", 3) },
    { wire: "МГТФ 0,12 белый", from: end("А1", "PHR-3", 3), to: end("В1", "XHP-3", 2) },
  ],
};

it("draws a manual assembly by the connections it was opened with, twist group included", async () => {
  expect(configDrawingProblem(manual)).toBe("");
  const plan = planFromConfig(manual);
  expect(plan.connectors.map((c) => [c.key, c.designation, c.article, c.side])).toEqual([["А1", "XS1", "PHR-3", "A"], ["В1", "XS2", "XHP-3", "B"]]);
  expect(plan.wires.map((w) => [w.mark, w.from.pin, w.to.pin, w.color, w.lengthMm])).toEqual([
    ["МГТФ 0,12 красный", 1, 1, "#D7322D", 310], ["МГТФ 0,12 черный", 2, 3, "#222222", 310], ["МГТФ 0,12 белый", 3, 2, expect.any(String), 310],
  ]);
  expect(plan.twistGroups).toEqual([["c1", "c2", "c3"]]);
  expect(plan.warnings).toEqual(["Длины проводов — длины реза из сборки (с припусками на концы); проверьте их по КД."]);
  const { document, warnings } = await buildConfigDrawing(manual, {}, null);
  expect(document.wires).toHaveLength(3);
  expect(document.drawingDocuments!.graphics![0].text).toBe("Свить вместе 3 провода: XS1:1, XS1:2, XS1:3 · шаг 20 мм");
  expect(warnings.some((w) => w.startsWith("XS1 (PHR-3): нет в библиотеке компонентов"))).toBe(true);
  expect(() => parseHarnessDesignDocument(JSON.parse(JSON.stringify(document)))).not.toThrow();
});
