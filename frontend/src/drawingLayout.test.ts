// @vitest-environment node
import { expect, it } from "vitest";
import { dimensionLabelBoxes, dimensionLineBoxes, indexLabelBoxes, openEndLabelBoxes, orderByPartners, placeDrawingLabels, type LabelBox } from "./drawingLayout";
import { buildHarnessDocument, planFromGenerator, type ConnectorSource, type GeneratorDrawingPlan } from "./generatorToHarness";
import { segmentDimensionKey } from "./harness/editor/drawing-dimensions";
import { designToScene } from "./harness/editor/HarnessDesignEditor";
import { physicalNodePoint } from "./harness/editor/physical-ports";
import { D, harness } from "./tcGenRealHarness.fixture";
import { initState, updateEndpoint, type GenData, type GenState, type TerRec } from "./tcGenLogic";

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
  const { plan, drawing } = build();
  const doc = drawing.document, topology = doc.physicalTopology!;
  const at = (designation: string) => doc.connectors.find((c) => c.designation === designation)!.positions.drawing;
  const aCol = ["5", "I3", "C", "A6"].map(at), bCol = doc.connectors.filter((c) => c.schematic.orientation === "contacts-left").map((c) => c.positions.drawing);
  expect(new Set(aCol.map((p) => p.x)).size).toBe(1);
  expect(aCol.map((p) => p.y)).toEqual([...aCol.map((p) => p.y)].sort((x, y) => x - y));
  expect(new Set(bCol.map((p) => p.x)).size).toBe(1);
  expect(Math.min(...bCol.map((p) => p.x))).toBeGreaterThan(aCol[0]!.x + 600);
  // Сход жгутов А — один узел (gen-j0), от него ствол через зоны сростков (вход и выход, между ними трубка) до узла ветвления
  // к разъёмам В (gen-j1). Сростки не стоят цепочкой на стволе: на СБ они одной зоной. Без общей трубки на жгут зона — сростки
  // с одних разъёмов А (одна группа ПФ): здесь две зоны — «С1, С2» и «С3, С4».
  const point = (id: string) => physicalNodePoint(doc, topology.nodes.find((n) => n.id === id)!);
  expect(plan.zones).toHaveLength(2);
  expect(topology.nodes.filter((n) => n.id.startsWith("gen-n-gen-sp"))).toEqual([]);
  const trunkY = point("gen-j0").y;
  const trunk = ["gen-j0", "gen-z1-in", "gen-z1-out", "gen-z2-in", "gen-z2-out", "gen-j1"].map(point);
  expect(trunk.map((p) => p.y)).toEqual(Array(6).fill(trunkY));
  const inner = trunk.slice(1, 5).map((p) => p.x);
  expect(Math.min(...inner)).toBeGreaterThan(trunk[0]!.x);
  expect(Math.max(...inner)).toBeLessThan(trunk[5]!.x);
  expect(trunk[1]!.x).toBeLessThan(trunk[2]!.x);
  expect(trunk[3]!.x).toBeLessThan(trunk[4]!.x);
  // Хвосты зоны — одним жгутом, а не по одному от каждого сростка; жгут уходит вправо вместе с ветвями к разъёмам В (СБ): третья
  // ветвь веера от конца ствола, ниже нижнего выхода В и правее выходов; жгуты соседних зон — друг под другом.
  expect(topology.nodes.filter((n) => n.id.startsWith("gen-n-gen-w") || n.id.startsWith("gen-n-gen-t"))).toEqual([]);
  const exits = topology.nodes.filter((n) => n.connectorId && doc.connectors.find((c) => c.id === n.connectorId)?.schematic.orientation === "contacts-left").map((n) => physicalNodePoint(doc, n));
  const tips = ["gen-z1", "gen-z2"].map((zone) => point(`${zone}-tail`));
  tips.forEach((tip) => {
    expect(tip.y).toBeGreaterThan(Math.max(...exits.map((p) => p.y)));
    expect(tip.x).toBeGreaterThan(Math.max(...exits.map((p) => p.x)));
  });
  expect(new Set(tips.map((tip) => tip.y)).size).toBe(2);
  expect(topology.segments.filter((s) => s.to.endsWith("-tail")).map((s) => s.from)).toEqual(["gen-j1", "gen-j1"]);
});

it("the owner's harness: splice and junction nodes stand apart, labels do not overlap", () => {
  const { drawing } = build();
  const doc = drawing.document, topology = doc.physicalTopology!;
  const plain = topology.nodes.filter((n) => !n.connectorId).map((n) => ({ id: n.id, ...physicalNodePoint(doc, n) }));
  for (const [i, a] of plain.entries()) for (const b of plain.slice(i + 1)) {
    expect(Math.hypot(a.x - b.x, a.y - b.y), `${a.id} ↔ ${b.id}`).toBeGreaterThanOrEqual(60);
  }
  const scene = designToScene(doc, "drawing");
  // Обзорный чертёж без индексов проводов W1… и оболочек: остаются подписи 6 разъёмов; размеры длины хвостов — размером жгута.
  const boxes: LabelBox[] = [...indexLabelBoxes(scene)];
  expect(boxes).toHaveLength(6);
  expect(scene.filter((o) => o.kind === "object-index" && o.metadata?.indexObjectId?.startsWith("gen-w"))).toEqual([]);
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

it("a wire label is moved off the dimension text («N мм») it would otherwise cover", () => {
  const { drawing } = build();
  const doc = drawing.document;
  const sideOf = (id: string) => (doc.connectors.find((c) => c.id === id)?.schematic.orientation === "contacts-left" ? "B" : "A") as "A" | "B";
  const overlaps = (a: Omit<LabelBox, "id">, b: Omit<LabelBox, "id">) =>
    Math.min(a.x + a.width, b.x + b.width) > Math.max(a.x, b.x) && Math.min(a.y + a.height, b.y + b.height) > Math.max(a.y, b.y);
  const withDimension = (segmentId: string) => {
    const segment = doc.physicalTopology!.segments.find((s) => s.id === segmentId)!;
    const dimension = { id: "t-d1", segmentId, from: 0, to: segment.path.points.length + 1, pointCount: segment.path.points.length + 2,
      routeKey: segmentDimensionKey(doc, segmentId), mode: "aligned" as const, modeOverride: false, offset: 40, lengthMm: 300 };
    return { ...doc, drawingDocuments: { ...doc.drawingDocuments!, showDimensions: true, dimensions: [dimension] } };
  };
  // Берём размер там, где подпись провода без размера встаёт на его текст: размер на каждом участке по очереди.
  let proven = 0;
  for (const segment of doc.physicalTopology!.segments) {
    const base = withDimension(segment.id);
    const dimBoxes = [...dimensionLabelBoxes(designToScene(base, "drawing")), ...dimensionLineBoxes(designToScene(base, "drawing"))];
    if (!dimBoxes.length) continue;
    // Подписи, посчитанные без знания о размере (как раньше), и с ним.
    const placed = placeDrawingLabels(base, sideOf);
    const labels = indexLabelBoxes(designToScene(placed, "drawing"));
    expect(labels.filter((l) => dimBoxes.some((d) => overlaps(l, d))).map((l) => l.id), segment.id).toEqual([]);
    proven++;
  }
  expect(proven).toBeGreaterThan(0);
});

// Прямой жгут А → В с одним размером «N мм» над стволом: подписи проводов не лежат ни на тексте размера, ни на его линии
// и выносных (рамки — те, что рисует поле), при любой длине, числе проводов и длине подписи.
const ter = (article: string): TerRec => ({ article, step: "", strip: "", lPlus: "", lMinus: "", applicator: "", awgFrom: "", awgTo: "", mm2From: "", mm2To: "" });
const COLOURS = ["красный", "чёрный", "белый", "зелёный", "синий", "жёлтый", "серый", "коричневый", "оранжевый", "фиолетовый", "розовый", "голубой"];
const straight = (wires: number, length: number) => {
  const components = [
    ...COLOURS.slice(0, wires).map((c, i) => ({ id: `w${i}`, type: "Провод", name: `МГТФ 0,12 ${c}`, art: `МГТФ 0,12 ${c}`, qty: 0.3 })),
    { id: "ca", type: "Соед", name: "Колодка PH", art: "PHR-12", qty: 1 }, { id: "cb", type: "Соед", name: "Колодка XH", art: "XHP-12", qty: 1 },
    { id: "ta", type: "Терминал", name: "Контакт PH", art: "SPH-002T-P0.5S", qty: wires }, { id: "tb", type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6", qty: wires },
  ];
  const data: GenData = { components, assemblyInfo: { "Индекс": "КБ1" }, templates: [], ops: [], terRecords: [ter("SPH-002T-P0.5S")], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [] };
  const seeded = initState(data);
  let S: GenState = { ...seeded, drawingLength: length, sharedLengthText: String(length), twist: { ...seeded.twist, enabled: false, pairs: [] } };
  const pins = Object.fromEntries(S.wires.filter((w) => w.compId.startsWith("w")).map((w, i) => [w.idx, String(i + 1)]));
  S = updateEndpoint(data, S, "a1", { pins });
  S = updateEndpoint(data, S, "b1", { pins });
  const plan = planFromGenerator(data, S);
  return buildHarnessDocument(plan, freeSources(plan)).document;
};
const hits = (a: Omit<LabelBox, "id">, b: Omit<LabelBox, "id">) =>
  Math.min(a.x + a.width, b.x + b.width) > Math.max(a.x, b.x) && Math.min(a.y + a.height, b.y + b.height) > Math.max(a.y, b.y);

it.each([[2, 300], [4, 300], [4, 40], [8, 1500], [12, 120]])("a straight harness of %i wires, %i мм: no wire label touches the dimension text or line", (wires, length) => {
  const doc = straight(wires, length);
  const scene = designToScene(doc, "drawing");
  const text = dimensionLabelBoxes(scene), lines = dimensionLineBoxes(scene), labels = indexLabelBoxes(scene).filter((l) => l.id.includes(":gen-w"));
  expect(text).toHaveLength(1);
  expect(labels).toHaveLength(wires);
  expect(labels.filter((l) => [...text, ...lines].some((d) => hits(l, d))).map((l) => l.id)).toEqual([]);
});
