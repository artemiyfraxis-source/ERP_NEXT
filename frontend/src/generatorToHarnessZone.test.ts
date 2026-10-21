// @vitest-environment node
import { describe, expect, it } from "vitest";
import golden from "./fixtures/gen713k4Rev17.json";
import catalog from "./fixtures/connectorCatalog.json";
import { restoreFromSnapshot, snapshotFromConfig } from "./genBuildState";
import { ensureSeparateLengths, initState, makeGen, type Comp, type GenData } from "./tcGenLogic";
import { DSA } from "./tcGenRealHarness.fixture";
import { buildHarnessDocument, planFromGenerator, type ConnectorSource } from "./generatorToHarness";
import type { ConnectorHousing } from "./connectorArt";
import { designToScene } from "./harness/editor/HarnessDesignEditor";
import { connectorContactPosition, parseHarnessDesignDocument } from "./harness/editor/model";
import { HOUSING_PX_PER_MM, housingFace, housingSizeMm } from "./harness/editor/housing-face";
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
    // Жгут свободных концов идёт вправо вместе с ветвями к разъёмам В: третья ветвь веера от конца ствола, ниже нижнего разъёма В.
    expect(bundle.from).toBe("gen-j1");
    const exits = topology.nodes.filter((n) => n.connectorId && doc.connectors.find((c) => c.id === n.connectorId)?.schematic.orientation === "contacts-left");
    const tip = physicalNodePoint(doc, topology.nodes.find((n) => n.id === "gen-z1-tail")!);
    expect(tip.y).toBeGreaterThan(Math.max(...exits.map((n) => physicalNodePoint(doc, n).y)));
    expect(tip.x).toBeGreaterThan(Math.max(...exits.map((n) => physicalNodePoint(doc, n).x)));
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
    // Одинаковые оболочки — выноска «Трубка ТТК 8/2, 2 места» с линиями к каждому месту; места, до которых чисто не достать, берёт
    // следующая подпись той же оболочки. Сумма мест по подписям — все места формы.
    const places = (prefix: string) => callouts.filter((text) => text.startsWith(prefix)).reduce((sum, text) => sum + Number(/, (\d+) мест/.exec(text)?.[1] ?? 1), 0);
    expect([places("Трубка ТТК 8/2"), places("Трубка ТТК 12/4, 15"), places("Оплётка XP-3"), places("Маркировка")]).toEqual([3, 1, 4, 4]);
    expect(callouts).toContain("Трубка ТТК 12/4, 30 мм");
    expect(callouts).toEqual(expect.arrayContaining(["Маркировка «5»", "Маркировка «A6»"]));
    // Линия на каждое место: 3 + 1 + 4 + 4 + зона.
    expect(doc.drawingDocuments!.graphics!.filter((g) => g.kind === "line")).toHaveLength(3 + 1 + 4 + 4 + 1);
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

describe("разъёмы из базы разъёмов — вид сверху, как в ПФ 2", () => {
  it("every connector of the scheme is a top view; side A leaves its wires to the right, side B mirrored to the left", () => {
    const { document: doc } = build(true);
    expect(doc.connectors.every((c) => c.housingView === "face" && c.housing)).toBe(true);
    const scene = designToScene(doc, "drawing");
    for (const connector of doc.connectors) {
      const left = connector.schematic.orientation === "contacts-left";
      const object = scene.find((o) => o.id === connector.id)!;
      const svg = object.metadata!.faceSvg!;
      const face = housingFace(connector.housing!, null, left)!;
      expect(face.top).toBe(true);
      // Тот же рисунок, что в ПФ 2 (connectorTopView): сторона А — оси меняются местами, сторона В — поворот на 90° по часовой.
      expect(svg).toContain('data-view="top"');
      expect(svg).toContain(left ? 'transform="matrix(0,1,-1,0,' : 'transform="matrix(0,1,1,0,0,0)');
      expect([object.width, object.height]).toEqual([face.width, face.height]);
      // Провода встают на край торца со стороны жгута: А — справа, В — слева; контакт 1 — сверху, дальше вниз.
      const entries = connector.contacts.map((contact) => connectorContactPosition(connector, contact.id, "drawing")!);
      expect(new Set(entries.map((p) => p.x))).toEqual(new Set([connector.positions.drawing.x + (left ? 0 : face.width)]));
      const byNumber = [...connector.contacts].sort((a, b) => a.number - b.number).map((contact) => entries[connector.contacts.indexOf(contact)]!.y);
      expect(byNumber).toEqual([...byNumber].sort((a, b) => a - b));
    }
    // Зеркало: тот же корпус на стороне А и В — рисунок отражён по горизонтали, ряд контактов — на тех же высотах.
    const housing = housingOf("SMR-04V-B");
    const [a, b] = [housingFace(housing, null, false)!, housingFace(housing, null, true)!];
    expect([a.width, a.height]).toEqual([b.width, b.height]);
    expect(a.contacts.get(1)!.x + b.contacts.get(1)!.x).toBe(a.width);
    expect([...a.contacts].map(([pin, p]) => [pin, p.y])).toEqual([...b.contacts].map(([pin, p]) => [pin, p.y]));
    expect(a.contacts.get(1)!.y).toBeLessThan(a.contacts.get(4)!.y);
  });

  it("the wire colours of the form reach the connector's top view", () => {
    const { document: doc } = build(true);
    const x5 = doc.connectors.find((c) => c.designation === "X5")!;
    const scene = designToScene(doc, "drawing").find((o) => o.id === x5.id)!;
    expect(scene.metadata?.faceSvg?.toLowerCase()).toContain("#d7322d");
  });

  it("a connector without a catalog body stays a plain frame", () => {
    const { document: doc } = build(false);
    expect(doc.connectors.every((c) => c.housingView === undefined && c.housing === undefined)).toBe(true);
  });
});

describe("размеры ветвей по КД и точка «Б»", () => {
  const withKd = (kd: Record<string, string>) => {
    const S = form();
    const plan = planFromGenerator(data, { ...S, endpoints: S.endpoints.map((e) => ({ ...e, kdBranch: kd[e.label] ?? "" })) });
    const sources = Object.fromEntries(plan.connectors.map((c) => [c.key, { kind: "free", contactCount: null, libraryCode: null } as ConnectorSource]));
    return { S, plan, ...buildHarnessDocument(plan, sources) };
  };

  it("«Ветвь по КД» sets the branch dimension exactly: 120 / 30 / 150 / 170 / 50 as on the СБ", () => {
    const { document: doc } = withKd({ X1: "120", X2: "120", X3: "120", X4: "120", X5: "150", X6: "170" });
    const dimensions = doc.drawingDocuments!.dimensions!;
    expect(dimensions.map((d) => d.lengthMm).sort((a, b) => a! - b!)).toEqual([30, 50, 120, 150, 170]);
    expect(dimensions.every((d) => d.auxiliary === true)).toBe(true);
  });

  it("the field only draws: cut lengths, operations and the form stay as they were", () => {
    const base = build();
    const { plan, document: doc } = withKd({ X1: "120", X5: "150", X6: "170" });
    expect(doc.wires.map((w) => w.lengthMm)).toEqual(base.document.wires.map((w) => w.lengthMm));
    expect(plan.wires.map((w) => w.lengthMm)).toEqual(base.plan.wires.map((w) => w.lengthMm));
    const ops = (state: ReturnType<typeof form>) => JSON.stringify((makeGen(data, state).buildConfig() as { ops: unknown[] }).ops);
    const S = form();
    expect(ops({ ...S, endpoints: S.endpoints.map((e) => ({ ...e, kdBranch: "999" })) })).toBe(ops(S));
  });

  it("without the field the branch is measured to the face the СБ names: B side loses the housing depth when the catalog knows it", () => {
    const plan = planFromGenerator(data, form());
    const withDepth = Object.fromEntries(plan.connectors.map((c) => [c.key, { kind: "free", contactCount: null, libraryCode: null,
      housing: c.side === "B" ? { ...housingOf("SMR-04V-B"), n: 6, cols: 6, grid: [[1, 2, 3, 4, 5, 6]] } : undefined } as ConnectorSource]));
    const { document: doc } = buildHarnessDocument(plan, withDepth);
    const lengths = doc.drawingDocuments!.dimensions!.map((d) => d.lengthMm!).sort((a, b) => a - b);
    // Глубина корпуса — body.len серии (JST SM, 18,2 мм); сторона А и жгут хвостов её не вычитают.
    expect(lengths).toEqual([30, 50, 122, 146.8, 166.8]);
  });

  it("point «Б» sits on the lower edge of the zone, taken from the «ТУТ на месте Б» step", () => {
    const { document: doc } = build();
    const graphics = doc.drawingDocuments!.graphics!;
    const dot = graphics.find((g) => g.kind === "ellipse")!;
    expect(graphics.some((g) => g.kind === "text" && g.text === "Б")).toBe(true);
    const topology = doc.physicalTopology!;
    const point = (id: string) => physicalNodePoint(doc, topology.nodes.find((n) => n.id === id)!);
    const [left, right] = [point("gen-z1-in"), point("gen-z1-out")];
    const x = (dot.points[0]!.x + dot.points[1]!.x) / 2, y = (dot.points[0]!.y + dot.points[1]!.y) / 2;
    expect(x).toBeGreaterThan(left.x);
    expect(x).toBeLessThan(right.x);
    expect(y).toBeGreaterThan(left.y);
    // Нет общей трубки на жгуте — нет шага «ТУТ на месте Б» и нет точки.
    const S = form();
    const without = planFromGenerator(data, { ...S, hs: { ...S.hs, enabled: false } });
    const sources = Object.fromEntries(without.connectors.map((c) => [c.key, { kind: "free", contactCount: null, libraryCode: null } as ConnectorSource]));
    expect(buildHarnessDocument(without, sources).document.drawingDocuments!.graphics!.some((g) => g.kind === "ellipse")).toBe(false);
  });
});

describe("выноски и масштаб разъёмов", () => {
  type Pt = { x: number; y: number };
  const cross = (a: Pt, b: Pt, c: Pt, d: Pt) => {
    const o = (p: Pt, q: Pt, r: Pt) => (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
    return o(a, b, c) * o(a, b, d) < -1e-9 && o(c, d, a) * o(c, d, b) < -1e-9;
  };

  it("no leader line crosses a branch, another leader or a label; each reaches its place from the edge of its label", () => {
    const { document: doc } = build(true);
    const topology = doc.physicalTopology!;
    const point = (id: string) => physicalNodePoint(doc, topology.nodes.find((n) => n.id === id)!);
    const branches = topology.segments.map((segment) => [point(segment.from), point(segment.to)] as const);
    const graphics = doc.drawingDocuments!.graphics!;
    const leaders = graphics.filter((g) => g.kind === "line").map((g) => [g.points[0]!, g.points[1]!] as const);
    expect(leaders.length).toBeGreaterThan(10);
    // Линия подходит к своей ветви с края — на последних 2 px (точка на самой ветви) пересечения не считаются.
    const shortened = leaders.map(([from, to]) => {
      const length = Math.hypot(to.x - from.x, to.y - from.y) || 1, t = Math.max(0, 1 - 2 / length);
      return [from, { x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t }] as const;
    });
    const crossingBranch = shortened.flatMap(([a, b], i) => branches.filter(([c, d]) => cross(a, b, c, d)).map(() => `leader ${i}`));
    expect(crossingBranch).toEqual([]);
    const crossingLeaders = shortened.flatMap(([a, b], i) => shortened.slice(i + 1).filter(([c, d]) => cross(a, b, c, d)).map(() => `leaders ${i}`));
    expect(crossingLeaders).toEqual([]);
    // Линия не идёт сквозь подписи и корпуса разъёмов.
    const boxes = [
      ...graphics.filter((g) => g.kind === "text").map((g) => ({ x: g.points[0]!.x, y: g.points[0]!.y - 12, width: g.text!.length * 6.2, height: 14 })),
      ...designToScene(doc, "drawing").filter((o) => o.kind === "connector").map((o) => ({ x: o.x, y: o.y, width: o.width, height: o.height })),
    ];
    const through = leaders.filter(([from, to]) => boxes.some((box) => Array.from({ length: 12 }, (_, k) => ({ x: from.x + ((to.x - from.x) * (k + 2)) / 14, y: from.y + ((to.y - from.y) * (k + 2)) / 14 }))
      .some((p) => p.x > box.x + 1 && p.x < box.x + box.width - 1 && p.y > box.y + 1 && p.y < box.y + box.height - 1)));
    expect(through).toEqual([]);
    // Веер: у подписи с несколькими местами линии выходят из одной точки её края.
    const origins = new Map<string, number>();
    leaders.forEach(([from]) => origins.set(`${from.x},${from.y}`, (origins.get(`${from.x},${from.y}`) ?? 0) + 1));
    expect([...origins.values()].some((n) => n > 1)).toBe(true);
  });

  it("every connector is drawn at one mm-to-px scale from its real housing size, so relative sizes match reality", () => {
    const { document: doc } = build(true);
    const faces = doc.connectors.map((connector) => {
      const left = connector.schematic.orientation === "contacts-left";
      const face = housingFace(connector.housing!, null, left)!;
      const size = housingSizeMm(connector.housing!)!;
      return { name: connector.designation, face, size };
    });
    expect(faces).toHaveLength(6);
    // Один масштаб на всех шестерых.
    expect(new Set(faces.map(({ face }) => face.pxPerMm))).toEqual(new Set([HOUSING_PX_PER_MM]));
    for (const { name, face, size } of faces) {
      // Ширина поперёк контактов — точно в масштабе; длина вдоль проводов — в масштабе с допуском на тень и фаски модели.
      expect(face.height / size.width, `${name} width`).toBeCloseTo(HOUSING_PX_PER_MM, 0);
      expect(face.width / size.length / HOUSING_PX_PER_MM, `${name} length`).toBeGreaterThan(0.85);
      expect(face.width / size.length / HOUSING_PX_PER_MM, `${name} length`).toBeLessThan(1.35);
    }
    // Размеры по чертежу: DS1071 — корпус 14 мм, SM — 18,2 мм (вилка) и 11,7 мм (розетка): отношения те же, что у деталей.
    const by = Object.fromEntries(faces.map((f) => [f.name, f]));
    expect(by.X5!.size.length).toBe(14);
    expect(by.X6!.size.length).toBe(14);
    // Одна длина корпуса (14 мм) — почти одна длина рисунка (у двухрядного ещё виден торец толщиной 5,2 мм).
    expect(Math.abs(by.X5!.face.width - by.X6!.face.width) / by.X5!.face.width).toBeLessThan(0.25);
    expect(by.X5!.face.width).toBeLessThan(by.X1!.face.width);
    expect(by.X5!.face.height).toBeLessThan(by.X4!.face.height);
    // Не «в разы больше»: X5/X6 не крупнее самых больших X1–X4.
    expect(Math.max(by.X5!.face.width, by.X6!.face.width)).toBeLessThan(Math.max(...["X1", "X2", "X3", "X4"].map((n) => by[n]!.face.width)));
  });

  it("the rear-face dimension of side B uses the real DS1071 depth (14 мм from the maker drawing)", () => {
    const plan = planFromGenerator(data, form());
    const sources = Object.fromEntries(plan.connectors.map((c) => [c.key, { kind: "free", contactCount: null, libraryCode: null, housing: housingOf(c.article) } as ConnectorSource]));
    const { document: doc } = buildHarnessDocument(plan, sources);
    const lengths = doc.drawingDocuments!.dimensions!.map((d) => d.lengthMm!).sort((a, b) => a - b);
    // Провод 180 и 200 мм минус половина зоны 15 и глубина корпуса 14: 151 и 171; без глубины было бы 165 и 185.
    expect(lengths.filter((value) => value === 151 || value === 171)).toEqual([151, 171]);
    expect(lengths.filter((value) => value === 165 || value === 185)).toEqual([]);
  });
});
