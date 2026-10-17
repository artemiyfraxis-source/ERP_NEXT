// @vitest-environment node
import { expect, it } from "vitest";
import { genSnapshot, restoreFromSnapshot } from "./genBuildState";
import { initState, makeGen, setSplMethod, splMethodFromConfig, type GenData, type GenState } from "./tcGenLogic";
import { markLength, markTubeShrinks, newMarkRow } from "./tcGenMarks";
import { DSA, fullHarness, harness, R } from "./tcGenRealHarness.fixture";

/*
 * «Сложная сборка» строит схему ПФ сама (владелец 16.10, 713К.4 КБ1): ПФ-комплекты (партии резки, резка всех трубок, резка
 * оплётки по ветвям, печать и резка маркировки) → ветви разъёмов А (монтаж контактов в разъём → надеть оплётку → надеть
 * ТТК → надеть маркировку → усадка ТТК на разъёме → усадка маркировки → свивка своей пары) → группы сростков по разъёмам
 * А, с которых приходят провода (пайка → трубка на готовый сросток → усадка, владелец 17.10) → «ТУТ на месте Б» (трубки на
 * жгут → усадка) → изделие (разъёмы В). Операции стоят в порядке схемы; схема уходит в build_config.route с короткими
 * разными именами ПФ.
 */
const labels = (S: GenState, data: GenData = DSA) => makeGen(data, S).getActiveOps().map((o) => o.label);
const keys = (S: GenState, data: GenData = DSA) => makeGen(data, S).getActiveOps().map((o) => o.opKey);
type Route = { source: string; blocks: Array<{ key: string; name?: string; ops: number[]; final: boolean; kit?: boolean }>;
  edges: Array<{ from: string; to: string; per_unit: number; item?: string }> };
const routeOf = (S: GenState, data: GenData = DSA) => (makeGen(data, S).buildConfig() as { route: Route }).route;

const GRAPH_ORDER = [
  // 1. ПФ-комплекты
  "Резка с обжимом А (SYM-001T-P0.6) и зачисткой В — 4 кабеля",
  "Резка с обжимом В (T-DS1071-SC600) и зачисткой А — 3 кабеля",
  "Резка с зачисткой двух концов и лужением — 4 кабеля",
  "Резка с обжимом А (SHF-001T-0.8BS) и зачисткой В — 2 кабеля",
  "Резка с обжимом А и В (SYM-001T-P0.6 / T-DS1071-SC600) — 3 кабеля",
  "Резка трубок — ТТК 8/2 3×15 мм, ТТК 12/4 2×25 мм, ТТК 3/1 4×20 мм",
  "Резка оплётки XP-3 — X1 «5» 120 мм, X2 «I3» 110 мм, X3 «C» 110 мм, X4 «A6» 120 мм",
  "Печать и резка маркировки — «5» 15 мм, «I3» 15 мм, «C» 15 мм, «A6» 15 мм",
  // 2. Ветви X1–X4: монтаж → свивка → оплётка → ТТК → маркировка → усадка ТТК → усадка маркировки
  "Монтаж контактов в разъём X1 «5» — SMR-02V-B",
  "Надеть оплётку X1 «5» — XP-3, 120 мм",
  "Надеть ТТК X1 «5» — ТТК 8/2",
  "Надеть маркировку X1 «5» — «5»",
  "Усадка ТТК на разъёме X1 «5» — ТТК 8/2",
  "Усадка маркировки X1 «5» — «5»",
  "Монтаж контактов в разъём X2 «I3» — SMR-02V-B",
  "Свивка X2 «I3» — 1 пара",
  "Надеть оплётку X2 «I3» — XP-3, 110 мм",
  "Надеть ТТК X2 «I3» — ТТК 8/2",
  "Надеть маркировку X2 «I3» — «I3»",
  "Усадка ТТК на разъёме X2 «I3» — ТТК 8/2",
  "Усадка маркировки X2 «I3» — «I3»",
  "Монтаж контактов в разъём X3 «C» — SMP-02V-BC",
  "Свивка X3 «C» — 1 пара",
  "Надеть оплётку X3 «C» — XP-3, 110 мм",
  "Надеть ТТК X3 «C» — ТТК 8/2",
  "Надеть маркировку X3 «C» — «C»",
  "Усадка ТТК на разъёме X3 «C» — ТТК 8/2",
  "Усадка маркировки X3 «C» — «C»",
  "Монтаж контактов в разъём X4 «A6» — SMR-04V-B",
  "Надеть оплётку X4 «A6» — XP-3, 120 мм",
  "Надеть маркировку X4 «A6» — «A6»",
  "Усадка маркировки X4 «A6» — «A6»",
  // 3. Группы сростков: пайка → трубка на готовый сросток → усадка
  "Сростки С1, С2 — 2 шт по 4 провода",
  "Надеть трубку на сростки С1, С2 — 2 шт",
  "Усадка трубки на сростках С1, С2 — 2 шт",
  "Сростки С3, С4 — 2 шт по 3 провода",
  "Надеть трубку на сростки С3, С4 — 2 шт",
  "Усадка трубки на сростках С3, С4 — 2 шт",
  // 4. ТУТ на месте Б
  "Надеть трубки: ТТК 12/4мм, черная на жгут — 2 шт",
  "Усадка термоусадки на жгуте — 2 шт",
  // 5. Изделие — только разъёмы стороны В
  "Монтаж контактов в разъём X5 — I-DS1071-SCB01X3",
  "Монтаж контактов в разъём X6 — I-DS1071-SCB02X3",
];

it("builds 713К.4 КБ1 as the ПФ graph: kits, branches X1–X4, splice groups, the product — ops in the graph's order", () => {
  expect(labels(fullHarness())).toEqual(GRAPH_ORDER);
});

it("names a branch by its marking text when the connector's own label is just its «X» designation (713К.4 КБ1, 17.10)", () => {
  const S0 = fullHarness();
  // Как на КБ1: разъём подписан «X1», маркировка «5» привязана к «Разъём X1 (…)».
  const rename = new Map(S0.endpoints.filter((ep) => ep.side === "A").map((ep, i) => [ep.label, `X${i + 1}`]));
  const S = {
    ...S0,
    endpoints: S0.endpoints.map((ep) => (ep.side === "A" ? { ...ep, label: rename.get(ep.label)! } : ep)),
    markRows: S0.markRows.map((row) => ({ ...row, target: row.target.replace(/^Разъём (\S+)/, (all, old) => `Разъём ${rename.get(old) ?? old}`) })),
  };
  const names = routeOf(S).blocks.map((b) => b.name);
  expect(names).toContain("Ветвь X1 «5», сторона А");
  expect(names).toContain("Ветвь X2 «I3», сторона А");
  expect(names.some((name) => /«X\d»/.test(name ?? ""))).toBe(false);
});

it("the build config carries the graph as a generated route: every op in one block, kits marked, edges say what flows", () => {
  const S = fullHarness();
  const route = routeOf(S);
  const opKeys = keys(S);
  expect(route.source).toBe("generated");
  const branch = (n: number, twist = true, tube = true) => [`insTermA${n}`, ...(twist ? [`twistA${n}`] : []), `braidA${n}`, ...(tube ? [`onTubesA${n}`] : []), `onMarkA${n}`,
    ...(tube ? [`heatShrA${n}`] : []), `heatMarkA${n}`];
  // Короткие разные имена ПФ: что делает станок и на какие разъёмы по КД (X1…X6: сторона А, потом В).
  expect(route.blocks.map((b) => [b.key, b.name ?? "", b.ops.map((p) => opKeys[p - 1]), !!b.kit, b.final])).toEqual([
    ["pf-cutWire", "Резка + обжим SYM, зачистка — X1, X2", ["cutWire"], true, false],
    ["pf-cutWire1", "Резка + обжим T-DS1071, зачистка — X5, X6", ["cutWire1"], true, false],
    ["pf-cutWire2", "Резка хвостов + лужение", ["cutWire2"], true, false],
    ["pf-cutWire3", "Резка + обжим SHF, зачистка — X3", ["cutWire3"], true, false],
    ["pf-cutWire4", "Резка + обжим А и В — X4→X6", ["cutWire4"], true, false],
    ["pf-tubes", "Резка трубок", ["cutTubes"], true, false], ["pf-braid", "Резка оплётки", ["cutBraid"], true, false],
    ["pf-mark", "Маркировка", ["cutMark"], true, false],
    ["pf-A1", "Ветвь X1 «5», сторона А", branch(1, false), false, false],
    ["pf-A2", "Ветвь X2 «I3», сторона А", branch(2), false, false],
    ["pf-A3", "Ветвь X3 «C», сторона А", branch(3), false, false],
    ["pf-A4", "Ветвь X4 «A6», сторона А", branch(4, false, false), false, false],
    ["pf-spl1", "Пайка С1, С2", ["splice1", "insSpl1", "heatSpl1"], false, false],
    ["pf-spl2", "Пайка С3, С4", ["splice2", "insSpl2", "heatSpl2"], false, false],
    ["pf-hs", "ТУТ на месте Б", ["insSleeves", "heatHs"], false, false],
    ["pf-final", "Кабельная сборка Жгут НВ4", ["insTermB1", "insTermB2"], false, true],
  ]);
  expect(route.blocks.every((b) => (b.name ?? "").length <= 41)).toBe(true);
  expect(route.blocks.flatMap((b) => b.ops).sort((a, b) => a - b)).toEqual(opKeys.map((_, i) => i + 1));
  // Каждая операция ветви — своя карта: монтаж контактов, свивка, оплётка, ТТК, маркировка, усадка ТТК, усадка маркировки.
  const gen = makeGen(DSA, S);
  const ops = gen.getActiveOps();
  expect(branch(2).map((key) => gen.templateIdFor(ops.find((o) => o.opKey === key)!))).toEqual([
    "ins-terminal-2-54-6-3-монтаж-терминалов-a", "twist-свивка", "ins-braid-монтаж-оплётки", "ins-tut-монтаж", "ins-tut-монтаж",
    "heat-ttk-short-усадка", "heat-tyt-short-усадка"]);
  const edge = (from: string, to: string) => route.edges.find((e) => e.from === from && e.to === to)?.item;
  // Партия SYM отдаёт провода двум ветвям; провода с обжимом В и хвосты — в группы сростков. Провода партии — одной строкой.
  expect(edge("pf-cutWire", "pf-A1")).toBe("НВ4 0,5 красный, черный — 2 шт");
  expect(edge("pf-cutWire", "pf-A2")).toBe("НВ4 0,12 белый, синий — 2 шт");
  expect(edge("pf-cutWire1", "pf-spl1")).toBe("НВ4 0,2 красный ×2, черный ×2 — 4 шт");
  expect(edge("pf-cutWire2", "pf-spl2")).toBe("НВ4 0,12 белый, синий — 2 шт");
  expect(edge("pf-cutWire4", "pf-A4")).toBe("НВ4 0,2 белый, зеленый, желтый — 3 шт");
  // Каждая трубка — своей стрелкой к тому, кто её надевает.
  expect(["pf-A1", "pf-A4", "pf-spl1", "pf-hs", "pf-final"].map((to) => edge("pf-tubes", to)))
    .toEqual(["ТТК 8/2 — 1 шт", undefined, "ТТК 3/1 — 2 шт", "ТТК 12/4 — 2 шт", undefined]);
  expect(["pf-A1", "pf-A2"].map((to) => edge("pf-braid", to))).toEqual(["Оплётка XP-3 120 мм — 1 шт", "Оплётка XP-3 110 мм — 1 шт"]);
  expect(edge("pf-mark", "pf-A4")).toBe("Маркировка «A6» — 1 шт");
  expect(edge("pf-A2", "pf-spl2")).toBe("Ветвь X2 «I3», сторона А");
  // Ветви X1–X3 — в сростки, X4 и группы сростков — в «ТУТ на месте Б», оттуда — в изделие (владелец 17.10).
  expect(route.edges.filter((e) => /^pf-(A|spl|hs)/.test(e.from)).map((e) => `${e.from}>${e.to}`).sort()).toEqual([
    "pf-A1>pf-spl1", "pf-A2>pf-spl2", "pf-A3>pf-spl2", "pf-A4>pf-hs", "pf-hs>pf-final", "pf-spl1>pf-hs", "pf-spl2>pf-hs"]);
  expect(route.edges.filter((e) => e.to === "pf-final").map((e) => `${e.from}: ${e.item}`)).toEqual(["pf-hs: ТУТ на месте Б"]);
  expect(edge("pf-spl1", "pf-hs")).toBe("Пайка С1, С2");
  expect(route.edges.every((e) => e.per_unit === 1)).toBe(true);
});

it("connectors without tubes, braid, marking or a pair of their own are no branches: they are mounted in the product", () => {
  // Тот же жгут без трубок, оплётки и маркировки: ПФ — партии резки → группы сростков → изделие (все разъёмы).
  const S = harness(DSA);
  const route = routeOf(S);
  const opKeys = keys(S);
  expect(route.blocks.map((b) => b.key)).toEqual(["pf-cutWire", "pf-cutWire1", "pf-cutWire2", "pf-cutWire3", "pf-cutWire4", "pf-spl1", "pf-spl2", "pf-final"]);
  expect(route.blocks.find((b) => b.final)!.ops.map((p) => opKeys[p - 1]))
    .toEqual(["insTermA1", "insTermA2", "insTermA3", "insTermA4", "insMark", "insTermB1", "insTermB2"]);
  // Провода с разъёмами на обоих концах — прямо в изделие; провода в сростки — в свою группу.
  const pairs = route.edges.map((e) => `${e.from}>${e.to}`);
  expect(pairs).toEqual(expect.arrayContaining(["pf-cutWire4>pf-final", "pf-cutWire1>pf-spl1", "pf-spl1>pf-final", "pf-spl2>pf-final"]));
  expect(pairs.filter((p) => p.startsWith("pf-cutWire>"))).toEqual(["pf-cutWire>pf-spl1", "pf-cutWire>pf-spl2"]);
  // Без трубки на сростки — у группы только сам сросток.
  expect(route.blocks.find((b) => b.key === "pf-spl1")!.ops.map((p) => opKeys[p - 1])).toEqual(["splice1"]);
});

it("a twisted pair that runs to side В is twisted in the product, where the generator put it: before side В is mounted", () => {
  const S0 = fullHarness();
  const S = { ...S0, twist: { ...S0.twist, pairs: [{ a: String(R.w2), b: String(R.g2) }] } };
  const order = keys(S);
  expect(order.filter((key) => /^twist/.test(key))).toEqual(["twist"]);
  expect(order.indexOf("twist")).toBe(order.indexOf("insTermB1") - 1);
  const final = routeOf(S).blocks.find((b) => b.final)!;
  expect(final.ops).toContain(order.indexOf("twist") + 1);
});

it("a pair from two connectors into one splice group is twisted in that group, before the tube goes on", () => {
  // Пары белый/синий разведены крест-накрест: белый с «I3» и синий с «C» (и наоборот) — каждая пара с двух разъёмов,
  // сходится в сростках С3/С4.
  const S0 = fullHarness();
  const crossed = { ...S0, endpoints: S0.endpoints.map((e) => (e.label === "I3" ? { ...e, pins: { [R.w12]: "1", [R.b12]: "_, 2" } }
    : e.label === "C" ? { ...e, pins: { [R.w12]: "_, 1", [R.b12]: "2" } } : e)) };
  const order = keys(crossed);
  expect(order.filter((key) => /^twist/.test(key))).toEqual(["twistS2"]);
  expect(order.indexOf("twistS2")).toBe(order.indexOf("splice2") - 1);
  expect(labels(crossed)).toContain("Свивка перед сростками С3, С4 — 2 пары");
  expect(routeOf(crossed).blocks.find((b) => b.key === "pf-spl2")!.ops).toContain(order.indexOf("twistS2") + 1);
  // В родной разводке пара лежит внутри ветви — свивка у ветви.
  expect(keys(S0).filter((key) => /^twist/.test(key))).toEqual(["twistA2", "twistA3"]);
});

it("each twist carries only its own pairs into the config; the pitch stays the form's (713К.4 КБ1, 17.10)", () => {
  // Вторая пара — хвосты белый/синий (из сростков С3, С4 на облуженные концы): свивается в группе сростков, не в ветвях.
  const S0 = fullHarness();
  const S = { ...S0, twist: { ...S0.twist, pairs: [...S0.twist.pairs, { a: String(R.w12t), b: String(R.b12t) }] } };
  const cfg = makeGen(DSA, S).buildConfig() as { ops: Array<Record<string, unknown>>; twist: { pairs: number[][]; pitch: string } };
  expect(cfg.twist).toMatchObject({ pairs: [[R.w12, R.b12], [R.w12t, R.b12t]], pitch: "20" });
  expect(cfg.ops.filter((o) => o.type === "twist").map((o) => [o.opKey, o.units, o.twistPairs])).toEqual([
    ["twistA2", 1, [[R.w12, R.b12]]], ["twistA3", 1, [[R.w12, R.b12]]], ["twistS2", 1, [[R.w12t, R.b12t]]]]);
  // 713К.4 как есть: у каждой свивки ветви — одна своя пара.
  const own = makeGen(DSA, S0).buildConfig() as { ops: Array<Record<string, unknown>> };
  expect(own.ops.filter((o) => o.type === "twist").map((o) => o.twistPairs)).toEqual([[[R.w12, R.b12]], [[R.w12, R.b12]]]);
});

it("no harness shrink — no «ТУТ на месте Б»: splice groups and branches go straight into the product", () => {
  const S0 = fullHarness();
  const S = { ...S0, hs: { ...S0.hs, enabled: false } };
  const route = routeOf(S);
  const opKeys = keys(S);
  expect(route.blocks.map((b) => b.key)).not.toContain("pf-hs");
  expect(route.blocks.find((b) => b.final)!.ops.map((p) => opKeys[p - 1])).toEqual(["insTermB1", "insTermB2"]);
  expect(route.edges.filter((e) => e.to === "pf-final").map((e) => e.from).sort()).toEqual(["pf-A4", "pf-spl1", "pf-spl2"]);
});

it("the technologist's own order still wins; the route follows the new positions", () => {
  const S = { ...fullHarness(), opOrder: ["onTubesA1", "braidA1"] };
  const order = keys(S);
  expect(order.indexOf("onTubesA1")).toBeLessThan(order.indexOf("braidA1"));
  expect(order.filter((key) => key !== "braidA1" && key !== "onTubesA1")).toEqual(keys(fullHarness()).filter((key) => key !== "braidA1" && key !== "onTubesA1"));
  const a1 = routeOf(S).blocks.find((b) => b.key === "pf-A1")!;
  expect(a1.ops.map((p) => order[p - 1])).toEqual(["insTermA1", "onTubesA1", "braidA1", "onMarkA1", "heatShrA1", "heatMarkA1"]);
});

it("marking tubes from the kit: printed and cut on INS_MARK_TUBE_AUTO, put on and shrunk with their branch; tags stay in «Маркировка»", () => {
  const S0 = fullHarness();
  const S = { ...S0, markRows: [...S0.markRows, newMarkRow({ target: "Изделие КБ1", text: "КБ1", material: "Хлястик", place: "item" })] };
  const gen = makeGen(DSA, S);
  const ops = gen.getActiveOps();
  expect(ops.find((o) => o.opKey === "cutMark")).toMatchObject({ type: "cutTut", tmplHint: "INS_MARK_TUBE_AUTO", tutCount: 4, tutSegs: [{ len: "15", count: 4 }],
    tutParts: [{ art: "Маркировка \"5\" 4/2мм", count: 1, len: "15" }, { art: "Маркировка \"I3\" 4/2мм", count: 1, len: "15" },
      { art: "Маркировка \"C\" 4/2мм", count: 1, len: "15" }, { art: "Маркировка \"A6\" 4/2мм", count: 1, len: "15" }].map((p) => ({ ...p, name: p.art })) });
  expect(gen.templateIdFor(ops.find((o) => o.opKey === "cutMark")!)).toBe("ins-mark-tube-auto-печать-и-резка-маркировочной-трубки");
  expect(ops.some((o) => o.opKey === "onMark" || o.opKey === "heatMark")).toBe(false);
  // Хлястик — операция «Маркировка» в изделии, как раньше; трубок в ней нет.
  const mark = ops.find((o) => o.type === "insMark")!;
  expect(mark).toMatchObject({ label: "Маркировка — КБ1", pf: "pf-final" });
  expect(mark.marks).toHaveLength(1);
  const cfg = gen.buildConfig() as { ops: Array<Record<string, unknown>> };
  // Каждая ветвь надевает и усаживает свою маркировку своими операциями (после ТТК).
  expect(cfg.ops.filter((o) => o.markTube).map((o) => o.opKey)).toEqual(["cutMark", ...[1, 2, 3, 4].flatMap((n) => [`onMarkA${n}`, `heatMarkA${n}`])]);
  expect(cfg.ops.find((o) => o.opKey === "onMarkA2")).toMatchObject({ type: "insTut", tutCount: 1, branch: "X2 «I3»", templateId: "ins-tut-монтаж" });
  expect(cfg.ops.find((o) => o.opKey === "heatMarkA2")).toMatchObject({ type: "heatTut", tutCount: 1, branch: "X2 «I3»", templateId: "heat-tyt-short-усадка" });
  // Трубка без длины по КД — «?» в названии; трубка из справочника (её печатают) — в «Маркировке».
  const noLen = { ...S0, markRows: S0.markRows.map((row, i) => (i === 0 ? { ...row, len: "" } : row)) };
  expect(labels(noLen)).toContain("Печать и резка маркировки — «5» ? мм, «I3» 15 мм, «C» 15 мм, «A6» 15 мм");
  const printed = { ...S0, markRows: [newMarkRow({ target: "Разъём 5", text: "5", material: "Термотрубка", place: "conn", len: "15" })] };
  expect(keys(printed)).not.toContain("cutMark");
  expect(keys(printed)).toContain("insMark");
  // Без карты маркиратора в библиотеке — резка трубки, как раньше.
  const noCard: GenData = { ...DSA, templates: DSA.templates.filter((t) => !t.title.startsWith("INS_MARK_TUBE_AUTO")) };
  expect(labels(S0, noCard)).toContain("Резка маркировки — «5» 15 мм, «I3» 15 мм, «C» 15 мм, «A6» 15 мм");
});

it("marking tubes split only on «Сложная сборка»: on «Межплатная» every mark stays in the one «Маркировка» operation", () => {
  const S0 = fullHarness();
  const inter = { ...S0, tab: "inter" as const };
  const gen = makeGen(DSA, inter);
  const ops = gen.getActiveOps();
  expect(ops.some((o) => o.markTube || o.pf)).toBe(false);
  expect(ops.find((o) => o.type === "insMark")!.marks).toHaveLength(4);
  expect(gen.buildConfig()).not.toHaveProperty("route");
});

it("tube length by КД «10–15» takes the upper value; «4/2мм» is a heat-shrink tube", () => {
  expect([markLength("10–15"), markLength("10-15"), markLength("12,5"), markLength("–"), markLength("")]).toEqual([15, 15, 12.5, undefined, undefined]);
  expect([markTubeShrinks("Маркировка \"5\" 4/2мм"), markTubeShrinks("Кембрик ПВХ 3 мм"), markTubeShrinks("Хлястик 4/2мм")]).toEqual([true, false, false]);
});

it("ultrasonic welding: US_WELD card per splice group, no sleeves, the splice tube goes on «на сростки»", () => {
  const S = setSplMethod(fullHarness(), "ultrasonic");
  const gen = makeGen(DSA, S);
  const splice = gen.getActiveOps().find((o) => o.opKey === "splice1")!;
  expect(splice).toMatchObject({ tmplHint: "US_WELD", cat: "УЗ сварка", label: "Сростки С1, С2 ультразвуковой сваркой — 2 шт по 4 провода" });
  expect(routeOf(S).blocks.find((b) => b.key === "pf-spl1")!.name).toBe("Сварка С1, С2");
  const cfg = gen.buildConfig() as { ops: Array<Record<string, unknown>> };
  expect(cfg.ops.find((o) => o.opKey === "splice1")).toMatchObject({ method: "ultrasonic", sleeves: [], templateId: "us-weld-уз-сварка", branch: "С1, С2" });
  expect(cfg.ops.filter((o) => o.spliceWeld).map((o) => o.opKey)).toEqual(["insSpl1", "heatSpl1", "insSpl2", "heatSpl2"]);
  expect(splMethodFromConfig(cfg)).toBe("ultrasonic");
  expect(makeGen(DSA, S).spliceSleeves_()).toEqual({ items: [], missing: [] });
  // Карты US_WELD в библиотеке нет — подсказка остаётся, в названии видно, что её надо уточнить.
  const noCard: GenData = { ...DSA, templates: DSA.templates.filter((t) => !t.title.startsWith("US_WELD")) };
  expect(makeGen(noCard, S).getActiveOps().find((o) => o.opKey === "splice1")!.label)
    .toBe("Сростки С1, С2 ультразвуковой сваркой — 2 шт по 4 провода (карточка — уточнить)");
  // Снимок формы хранит способ.
  expect(restoreFromSnapshot(DSA.components, initState(DSA), genSnapshot(DSA.components, S, {}, {})).S.spl.method).toBe("ultrasonic");
});

it("braid pieces per branch: own length, an equal share of the length per item, «0» — no braid on that branch", () => {
  const S0 = fullHarness();
  const gen0 = makeGen(DSA, S0);
  expect(gen0.braidLenDefault_()).toBe("115");
  const S = { ...S0, endpoints: S0.endpoints.map((e) => (e.label === "A6" ? { ...e, braidLen: "0" } : e.label === "C" ? { ...e, braidLen: "" } : e)) };
  const cut = makeGen(DSA, S).getActiveOps().find((o) => o.opKey === "cutBraid")!;
  // Без А4 оплётку делят на три ветви: 460 / 3 ≈ 153 мм — это у «C», у остальных свои длины.
  expect(cut.label).toBe("Резка оплётки XP-3 — X1 «5» 120 мм, X2 «I3» 110 мм, X3 «C» 153 мм");
  expect(cut.tutParts).toEqual([{ art: "XP-3", name: "XP-3", count: 1, len: "120" }, { art: "XP-3", name: "XP-3", count: 1, len: "110" },
    { art: "XP-3", name: "XP-3", count: 1, len: "153" }]);
  expect(keys(S)).not.toContain("braidA4");
  const cfg = makeGen(DSA, S0).buildConfig() as { ops: Array<Record<string, unknown>> };
  expect(cfg.ops.find((o) => o.opKey === "cutBraid")).toMatchObject({ type: "cutTut", wrapKind: "braid", tutArt: "XP-3", tutCount: 4 });
  expect(cfg.ops.find((o) => o.opKey === "braidA1")).toMatchObject({ type: "insWrap", wrapKind: "braid", wrapCut: true, wrapLen: "120", branch: "X1 «5»" });
});

it("a stuck template of another code than the rule asks is re-picked; a hand-picked one stays (713К.4 КБ1 marking, 17.10)", () => {
  const S0 = fullHarness();
  const mark = makeGen(DSA, S0).getActiveOps().find((o) => o.opKey === "cutMark")!;
  expect(makeGen(DSA, S0).templateIdFor(mark)).toBe("ins-mark-tube-auto-печать-и-резка-маркировочной-трубки");
  const stuck = { ...S0, selectedTemplates: { ...S0.selectedTemplates, cutMark: "cut-tut-short-резка" } };
  expect(makeGen(DSA, stuck).templateIdFor(mark)).toBe("ins-mark-tube-auto-печать-и-резка-маркировочной-трубки");
  const byHand = { ...stuck, manualTemplates: ["cutMark"] };
  expect(makeGen(DSA, byHand).templateIdFor(mark)).toBe("cut-tut-short-резка");
});
