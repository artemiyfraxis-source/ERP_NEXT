// @vitest-environment node
import { describe, expect, it } from "vitest";
import golden from "./fixtures/gen713k4Rev17.json";
import catalog from "./fixtures/connectorCatalog.json";
import { restoreFromSnapshot, snapshotFromConfig } from "./genBuildState";
import { ensureSeparateLengths, initState, type Comp, type GenData } from "./tcGenLogic";
import { DSA } from "./tcGenRealHarness.fixture";
import { buildHarnessDocument, planFromGenerator, type ConnectorSource } from "./generatorToHarness";
import type { ConnectorHousing } from "./connectorArt";
import { designToScene } from "./harness/editor/HarnessDesignEditor";
import { connectorContactPosition, parseHarnessDesignDocument } from "./harness/editor/model";
import { housingFace } from "./harness/editor/housing-face";
import { physicalNodePoint } from "./harness/editor/physical-ports";
import { collectStripCallouts } from "./harness/editor/strip-callouts";

/*
 * Чертёж 713К.4 КБ1 (техкарта ревизия 17 с прода) по состоянию формы: как на СБ ЮТНВ.713К.4КБ1 — одна зона сростков «С1–С4» под
 * общей трубкой ТТК 12/4, один жгут свободных концов, размеры от центра ветвления из данных генератора, выноски на трубки,
 * оплётку и маркировку, без индексов проводов W1… (владелец 18.10: «чертёж генерируется прям плохой по сравнению с тем что
 * на самом деле»). Операции и ПФ не трогаются — их держит tcGen713k4Golden.test.ts.
 */
const data: GenData = { ...DSA, components: golden.components as Comp[] };
const snap = snapshotFromConfig({ gen_state: golden.gen_state })!;
const form = () => ensureSeparateLengths(restoreFromSnapshot(data.components, initState(data), snap).S);

const family = (article: string) => (/^SMR/.test(article) ? "jst-sm-r" : /^SMP/.test(article) ? "jst-sm-p" : "connfly-ds1071");
/** Корпус серии из каталога, подогнанный под число контактов артикула (в приложении его даёт база разъёмов по артикулу). */
function housingOf(article: string): ConnectorHousing {
  const base = (catalog as Array<{ family: string; housing: ConnectorHousing }>).find((entry) => entry.family === family(article))!.housing;
  const n = /^SM/.test(article) ? Number(/-(\d+)V/.exec(article)![1]) : /SCB01X3/.test(article) ? 3 : 6;
  const rows = /^SM/.test(article) || /SCB01/.test(article) ? 1 : 2;
  const grid = rows === 1 ? [Array.from({ length: n }, (_, i) => i + 1)] : [[1, 3, 5], [2, 4, 6]];
  return { ...base, art: article, n, cols: n / rows, rows, grid } as ConnectorHousing;
}
const build = (withHousing = false) => {
  const plan = planFromGenerator(data, form());
  const sources = Object.fromEntries(plan.connectors.map((c) => [c.key, { kind: "free", contactCount: null, libraryCode: null,
    ...(withHousing ? { housing: housingOf(c.article) } : {}) } as ConnectorSource]));
  return { plan, ...buildHarnessDocument(plan, sources) };
};

describe("713К.4 КБ1: схема жгута как на СБ", () => {
  it("one splice zone С1–С4 under the shared ТТК 12/4 tube, not four nodes in series", () => {
    const { plan, document: doc } = build();
    expect(plan.zones).toHaveLength(1);
    expect(plan.zones![0]).toHaveLength(4);
    const topology = doc.physicalTopology!;
    expect(doc.junctions).toHaveLength(4);
    expect(topology.nodes.filter((n) => n.id.startsWith("gen-n-gen-sp"))).toEqual([]);
    const point = (id: string) => physicalNodePoint(doc, topology.nodes.find((n) => n.id === id)!);
    const [near, far] = [point("gen-z1-in"), point("gen-z1-out")];
    expect(near.y).toBe(far.y);
    expect(far.x).toBeGreaterThan(near.x);
    const zone = topology.segments.find((s) => s.from === "gen-z1-in" && s.to === "gen-z1-out")!;
    const tube = topology.coverings!.find((c) => c.spans.some((s) => s.segmentId === zone.id))!;
    expect([tube.name, tube.lengthMm, tube.lengthMode]).toEqual(["ТТК 12/4мм, черная", 30, "manual"]);
    // Каждый провод сростка приходит в зону: с разъёмов А — ко входу, к разъёмам В и хвосты — от выхода.
    expect(doc.wires.every((w) => topology.routes.some((r) => r.wireId === w.id))).toBe(true);
  });

  it("the four tails leave the zone as one bundle «Своб. концы от С1–С4», 65 − 30/2 = 50 мм", () => {
    const { document: doc } = build();
    const topology = doc.physicalTopology!;
    expect(topology.nodes.filter((n) => n.id.endsWith("-tail"))).toHaveLength(1);
    expect(topology.nodes.filter((n) => n.id.startsWith("gen-n-gen-w"))).toEqual([]);
    const bundle = topology.segments.find((s) => s.to === "gen-z1-tail")!;
    expect(bundle.from).toBe("gen-z1-out");
    const dimension = doc.drawingDocuments!.dimensions!.find((d) => d.segmentId === bundle.id)!;
    expect([dimension.lengthMm, dimension.auxiliary]).toEqual([50, true]);
    const texts = doc.drawingDocuments!.graphics!.filter((g) => g.kind === "text").map((g) => g.text);
    expect(texts).toContain("Своб. концы от С1–С4");
    expect(texts).toContain("С1–С4");
    // Подпись длины на свободном конце не дублирует размер жгута.
    expect(designToScene(doc, "drawing").filter((o) => o.kind === "wire").flatMap((o) => [o.metadata?.openFrom, o.metadata?.openTo]).filter((v) => v !== undefined && v !== "")).toEqual([]);
  });

  it("dimensions come from the generator data: zone 30, A branch 122, B branches 165 and 185, tails 50 — and never change the wire lengths", () => {
    const { plan, document: doc } = build();
    const dimensions = doc.drawingDocuments!.dimensions!;
    expect(dimensions.every((d) => d.auxiliary === true)).toBe(true);
    expect(dimensions.map((d) => d.lengthMm).sort((a, b) => a! - b!)).toEqual([30, 50, 122, 165, 185]);
    // Длины проводов — как в плане: размеры вспомогательные, длин резки не пересчитывают.
    const planned = [...plan.wires.map((w) => w.lengthMm), ...plan.splices!.flatMap((sp) => [...sp.wires, ...sp.tails].map((w) => w.lengthMm)), ...(plan.tails ?? []).map((w) => w.lengthMm)];
    expect(doc.wires.map((w) => w.lengthMm).sort()).toEqual(planned.sort());
  });

  it("the branches carry the tube, the braid and the marking of the form; no W labels, no ТУ/ОПЛ indices", () => {
    const { document: doc } = build();
    const names = doc.physicalTopology!.coverings!.map((c) => [c.name, c.kind, c.lengthMm]);
    const of = (prefix: string) => names.filter(([name]) => String(name).startsWith(prefix));
    expect(of("ТТК 8/2").map(([, , mm]) => mm)).toEqual([15, 15, 15]);
    expect(of("ТТК 12/4").map(([, , mm]) => mm).sort()).toEqual([15, 30]);
    expect(of("XP-3").map(([, kind, mm]) => [kind, mm])).toEqual(Array(4).fill(["braid", 110]));
    expect(of("Маркировка")).toHaveLength(4);
    expect(doc.drawingDocuments!.hideObjectIndices).toBe(true);
    const scene = designToScene(doc, "drawing");
    const labels = scene.filter((o) => o.kind === "object-index");
    expect(labels.map((o) => o.label).sort()).toEqual(["X1", "X2", "X3", "X4", "X5", "X6"]);
    const callouts = doc.drawingDocuments!.graphics!.filter((g) => g.kind === "text").map((g) => g.text!);
    expect(callouts).toEqual(expect.arrayContaining(["Трубка ТТК 8/2, 15 мм", "Трубка ТТК 12/4, 30 мм", "Оплётка XP-3, 110 мм", "Маркировка «5»", "Маркировка «A6»"]));
  });

  it("callouts and notes do not overlap each other or the connectors", () => {
    const { document: doc } = build(true);
    const scene = designToScene(doc, "drawing");
    const boxes = [
      ...doc.drawingDocuments!.graphics!.filter((g) => g.kind === "text").map((g) => ({ id: g.text!, x: g.points[0]!.x, y: g.points[0]!.y - 12, width: g.text!.length * 6.2, height: 14 })),
      ...scene.filter((o) => o.kind === "connector").map((o) => ({ id: o.label, x: o.x, y: o.y, width: o.width, height: o.height })),
    ];
    const crossing = boxes.flatMap((a, i) => boxes.slice(i + 1).filter((b) =>
      Math.min(a.x + a.width, b.x + b.width) > Math.max(a.x, b.x) && Math.min(a.y + a.height, b.y + b.height) > Math.max(a.y, b.y)).map((b) => `${a.id} ↔ ${b.id}`));
    expect(crossing).toEqual([]);
  });

  it("the strip text is one per point: the splice zone and the tail bundle", () => {
    const { document: doc } = build();
    const callouts = collectStripCallouts(designToScene(doc, "drawing"));
    expect(callouts.filter((c) => c.kind === "tail").map((c) => c.text)).toEqual(["зач. 3 мм, луж."]);
    expect(callouts.filter((c) => c.kind === "splice").length).toBeLessThanOrEqual(2);
  });

  it("the document stays readable by the editor", () => {
    const { document: doc } = build(true);
    expect(() => parseHarnessDesignDocument(JSON.parse(JSON.stringify(doc)))).not.toThrow();
  });
});

describe("разъёмы из базы разъёмов вместо пустых рамок", () => {
  it("a connector of the catalog is drawn by the shared face drawer: wires enter at its edge at the height of their pin", () => {
    const { document: doc } = build(true);
    const x5 = doc.connectors.find((c) => c.designation === "X5")!;
    const x1 = doc.connectors.find((c) => c.designation === "X1")!;
    expect([x1.housingView, x5.housingView]).toEqual(["face", "face"]);
    const face = housingFace(x5.housing!)!;
    const origin = x5.positions.drawing;
    // Сторона В — провод входит у левого края, по высоте гнезда; сторона А — у правого.
    const left = connectorContactPosition(x5, x5.contacts[0]!.id, "drawing")!;
    expect([left.x, left.y]).toEqual([origin.x, origin.y + face.contacts.get(x5.contacts[0]!.number)!.y]);
    const right = connectorContactPosition(x1, x1.contacts[0]!.id, "drawing")!;
    expect(right.x).toBe(x1.positions.drawing.x + housingFace(x1.housing!)!.width);
    const scene = designToScene(doc, "drawing").find((o) => o.id === x5.id)!;
    expect(scene.metadata?.faceSvg).toContain("<svg");
    expect(scene.width).toBe(face.width);
    // Цвета жил — на гнёздах торца.
    expect(scene.metadata?.faceSvg).toContain("#d7322d");
  });

  it("a connector without a catalog body stays a plain frame", () => {
    const { document: doc } = build(false);
    expect(doc.connectors.every((c) => c.housingView === undefined && c.housing === undefined)).toBe(true);
  });
});
