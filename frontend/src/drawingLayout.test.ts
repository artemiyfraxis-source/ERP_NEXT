// @vitest-environment node
import { expect, it } from "vitest";
import { indexLabelBoxes, openEndLabelBoxes, orderByPartners, type LabelBox } from "./drawingLayout";
import { buildHarnessDocument, planFromGenerator, type ConnectorSource, type GeneratorDrawingPlan } from "./generatorToHarness";
import { designToScene } from "./harness/editor/HarnessDesignEditor";
import { physicalNodePoint } from "./harness/editor/physical-ports";
import { D, harness } from "./tcGenRealHarness.fixture";

// Чертёж заказчика «Жгут НВ4» (tcGenRealHarness.fixture.ts): 4 разъёма А, 2 разъёма В, 4 сростка с хвостами по 60 мм.
const freeSources = (plan: GeneratorDrawingPlan) =>
  Object.fromEntries(plan.connectors.map((c) => [c.key, { kind: "free", contactCount: null, libraryCode: null } as ConnectorSource]));
const build = () => {
  const plan = planFromGenerator(D, harness(D));
  return { plan, drawing: buildHarnessDocument(plan, freeSources(plan)) };
};

it("orders side-B connectors by their partners on side A; without partners — last, in their own order", () => {
  const links = [["a1", "b2"], ["a3", "b1"], ["a2", "b1"], ["a1", "b3"], ["a3", "b3"]] as const;
  expect(orderByPartners(["a1", "a2", "a3"], ["b1", "b2", "b3", "b4"], (b) => b, links)).toEqual(["b2", "b3", "b1", "b4"]);
});

it("the owner's harness: A connectors in a column on the left, B on the right, one trunk with the splices on it", () => {
  const { drawing } = build();
  const doc = drawing.document, topology = doc.physicalTopology!;
  const at = (designation: string) => doc.connectors.find((c) => c.designation === designation)!.positions.drawing;
  const aCol = ["5", "I3", "C", "A6"].map(at), bCol = doc.connectors.filter((c) => c.schematic.orientation === "contacts-left").map((c) => c.positions.drawing);
  expect(new Set(aCol.map((p) => p.x)).size).toBe(1);
  expect(aCol.map((p) => p.y)).toEqual([...aCol.map((p) => p.y)].sort((x, y) => x - y));
  expect(new Set(bCol.map((p) => p.x)).size).toBe(1);
  expect(Math.min(...bCol.map((p) => p.x))).toBeGreaterThan(aCol[0]!.x + 600);
  // Сход жгутов А — один узел (gen-j0), от него ствол через четыре сростка до узла ветвления к разъёмам В (gen-j1).
  const point = (id: string) => physicalNodePoint(doc, topology.nodes.find((n) => n.id === id)!);
  const splices = doc.junctions.map((j) => point(`gen-n-${j.id}`));
  const trunkY = point("gen-j0").y;
  expect([point("gen-j1").y, ...splices.map((p) => p.y)]).toEqual(Array(5).fill(trunkY));
  const xs = [point("gen-j0").x, ...splices.map((p) => p.x).sort((a, b) => a - b), point("gen-j1").x];
  expect(xs).toEqual([...xs].sort((a, b) => a - b));
  // Хвост — поперёк ствола от своего сростка; у соседних сростков — в разные стороны.
  const tips = doc.wires.filter((w) => "freeEnd" in w.to).map((w) => point(`gen-n-${w.id}`));
  expect(tips.every((tip) => splices.some((sp) => sp.x === tip.x && Math.abs(sp.y - tip.y) === 80))).toBe(true);
  expect(new Set(tips.map((tip) => Math.sign(tip.y - trunkY)))).toEqual(new Set([-1, 1]));
});

it("the owner's harness: splice and junction nodes stand apart, labels do not overlap", () => {
  const { drawing } = build();
  const doc = drawing.document, topology = doc.physicalTopology!;
  const plain = topology.nodes.filter((n) => !n.connectorId).map((n) => ({ id: n.id, ...physicalNodePoint(doc, n) }));
  for (const [i, a] of plain.entries()) for (const b of plain.slice(i + 1)) {
    expect(Math.hypot(a.x - b.x, a.y - b.y), `${a.id} ↔ ${b.id}`).toBeGreaterThanOrEqual(60);
  }
  const scene = designToScene(doc, "drawing");
  const boxes: LabelBox[] = [...indexLabelBoxes(scene), ...openEndLabelBoxes(scene)];
  // Подписи всех 17 проводов, 6 разъёмов и 4 хвостов.
  expect(boxes).toHaveLength(17 + 6 + 4);
  const crossing = boxes.flatMap((a, i) => boxes.slice(i + 1).filter((b) =>
    Math.min(a.x + a.width, b.x + b.width) > Math.max(a.x, b.x) && Math.min(a.y + a.height, b.y + b.height) > Math.max(a.y, b.y)).map((b) => `${a.id} ↔ ${b.id}`));
  expect(crossing).toEqual([]);
  // Сдвиги подписей — обычные сдвиги редактора (их же он хранит, когда подпись двигают руками).
  expect(Object.keys(doc.drawingDocuments!.indexOffsets!).length).toBeGreaterThan(0);
});

it("layout only: every wire, splice, tail and length stays as the generator built it", () => {
  const { drawing } = build();
  const doc = drawing.document;
  expect(doc.wires.map((w) => w.lengthMm!).sort((a, b) => a - b)).toEqual([60, 60, 60, 60, 150, 150, 150, 150, 150, 150, 160, 160, 160, 180, 330, 330, 330]);
  expect(doc.junctions.map((j) => j.wireIds.length)).toEqual([4, 4, 3, 3]);
  expect(doc.wires.every((w) => doc.physicalTopology!.routes.some((r) => r.wireId === w.id))).toBe(true);
});
