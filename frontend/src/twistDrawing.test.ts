// @vitest-environment node
import { expect, it } from "vitest";
import { initState, makeGen, updateEndpoint, type GenData, type GenState } from "./tcGenLogic";
import { buildHarnessDocument, planFromGenerator, type ConnectorSource, type GeneratorDrawingPlan } from "./generatorToHarness";
import { parseHarnessDesignDocument } from "./harness/editor/model";

/* Свивка трёх проводов (группа): в сборку — одной группой, на чертёж — надписью (свитая пара редактора — только из двух). */

const COMPONENTS = [
  { id: "r", type: "Провод", name: "МГТФ 0,12 красный", art: "МГТФ 0,12 красный", qty: 0.3 },
  { id: "k", type: "Провод", name: "МГТФ 0,12 черный", art: "МГТФ 0,12 черный", qty: 0.3 },
  { id: "w", type: "Провод", name: "МГТФ 0,12 белый", art: "МГТФ 0,12 белый", qty: 0.3 },
  { id: "g", type: "Провод", name: "МГТФ 0,12 зеленый", art: "МГТФ 0,12 зеленый", qty: 0.3 },
  { id: "ca", type: "Соед", name: "Колодка PH 4", art: "PHR-4", qty: 1 },
  { id: "cb", type: "Соед", name: "Колодка XH 4", art: "XHP-4", qty: 1 },
];
const D: GenData = {
  components: COMPONENTS, assemblyInfo: { "Индекс": "КБ1", "Наименование": "Жгут 300мм" }, templates: [{ id: "t", title: "TWIST | Свивка", category: "Свивка" }],
  ops: [], terRecords: [], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [],
};
const idx = (S: GenState, compId: string) => S.wires.find((w) => w.compId === compId)!.idx;

function pinned(twist: Partial<GenState["twist"]>): GenState {
  const seeded = initState(D);
  let S: GenState = { ...seeded, drawingLength: 300, sharedLengthText: "300", twist: { ...seeded.twist, enabled: true, mode: "pairs", pairs: [], pitch: "25", ...twist } };
  const pins = Object.fromEntries(["r", "k", "w", "g"].map((comp, i) => [idx(S, comp), String(i + 1)]));
  S = updateEndpoint(D, S, "a1", { pins });
  return updateEndpoint(D, S, "b1", { pins });
}
const free = (plan: GeneratorDrawingPlan): Record<string, ConnectorSource> =>
  Object.fromEntries(plan.connectors.map((c) => [c.key, { kind: "free", contactCount: 4, libraryCode: null }]));

it("twists three wires as one group: config, check and drawing note", () => {
  const S = pinned({ pairs: [{ a: "0", b: "1", more: ["2"] }] });
  const gen = makeGen(D, S);
  expect(gen.getTwistPairs()).toEqual([[0, 1, 2]]);
  // В сборку — одной группой; сервер пишет её строкой «1) красный + черный + белый».
  expect((gen.buildConfig().twist as { pairs: number[][] }).pairs).toEqual([[0, 1, 2]]);
  expect(gen.twistProblems_()).toEqual({ error: "", warnings: [] });

  const plan = planFromGenerator(D, S);
  expect(plan.pairs).toEqual([]);
  expect(plan.twistGroups).toEqual([plan.wires.slice(0, 3).map((w) => w.key)]);
  const { document, warnings } = buildHarnessDocument(plan, free(plan));
  expect(document.diffPairs).toEqual([]);
  const notes = document.drawingDocuments!.graphics!;
  expect(notes.map((g) => [g.id, g.view, g.kind, g.text])).toEqual([
    ["gen-tw1", "drawing", "text", "Свить вместе 3 провода: XS1:1, XS1:2, XS1:3 · шаг 25 мм"],
    ["gen-tw1-e4", "e4", "text", "Свить вместе 3 провода: XS1:1, XS1:2, XS1:3 · шаг 25 мм"],
  ]);
  // Надпись — под разъёмами, в положительных координатах.
  const lowest = Math.max(...document.connectors.map((c) => c.positions.drawing.y));
  expect(notes[0].points[0].y).toBeGreaterThan(lowest);
  expect(warnings).toContain("Свитые группы (проводов: 3) показаны надписью — свитая пара в редакторе только из двух проводов.");
  expect(() => parseHarnessDesignDocument(JSON.parse(JSON.stringify(document)))).not.toThrow();
});

it("draws a pair next to a group of three as a twisted pair", () => {
  const S = pinned({ pairs: [{ a: "0", b: "1", more: ["2"] }, { a: "3", b: "" }] });
  expect(makeGen(D, S).twistProblems_().warnings).toEqual(["Свивка 2: выбран один провод — в свивку не войдёт."]);
  const two = pinned({ pairs: [{ a: "0", b: "1" }, { a: "2", b: "3" }] });
  const plan = planFromGenerator(D, two);
  const { document } = buildHarnessDocument(plan, free(plan));
  expect(document.diffPairs.map((p) => p.wireIds)).toEqual([["gen-w1", "gen-w2"], ["gen-w3", "gen-w4"]]);
  expect(document.drawingDocuments!.graphics).toBeUndefined();
});

it("does not build with a wire twisted into two groups", () => {
  const S = pinned({ pairs: [{ a: "0", b: "1", more: ["2"] }, { a: "2", b: "3" }] });
  expect(makeGen(D, S).validate().error).toBe("Свивка: «МГТФ 0,12 белый» — в свивках 1 и 2. Провод свивается только в одной группе.");
});
