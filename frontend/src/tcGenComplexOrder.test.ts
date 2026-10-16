// @vitest-environment node
import { expect, it } from "vitest";
import { genSnapshot, restoreFromSnapshot } from "./genBuildState";
import { initState, makeGen, setSplMethod, splMethodFromConfig, type GenData, type GenState } from "./tcGenLogic";
import { markLength, markTubeShrinks, newMarkRow } from "./tcGenMarks";
import { DSA, fullHarness, harness, R } from "./tcGenRealHarness.fixture";

/*
 * «Сложная сборка» строит схему ПФ сама (владелец 16.10, 713К.4 КБ1): ПФ-комплекты (партии резки, резка всех трубок, резка
 * оплётки по ветвям, печать и резка маркировки) → ветви разъёмов А (надеть трубки и маркировку → оплётка → монтаж →
 * усадка → свивка своей пары) → группы сростков по разъёмам А, с которых приходят провода (трубка → пайка → усадка) →
 * изделие (трубки на ствол → усадка → разъёмы В). Операции стоят в порядке схемы; схема уходит в build_config.route.
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
  "Резка оплётки XP-3 — А1 «5» 120 мм, А2 «I3» 110 мм, А3 «C» 110 мм, А4 «A6» 120 мм",
  "Печать и резка маркировки — «5» 15 мм, «I3» 15 мм, «C» 15 мм, «A6» 15 мм",
  // 2. Ветви X1–X4
  "Надеть трубки А1 «5»: ТТК 8/2 ×1, маркировка «5»",
  "Монтаж оплётки А1 «5» — XP-3, 120 мм",
  "Монтаж в разъём А1 — SMR-02V-B",
  "Усадка трубок А1 «5»: ТТК 8/2, маркировка «5»",
  "Надеть трубки А2 «I3»: ТТК 8/2 ×1, маркировка «I3»",
  "Монтаж оплётки А2 «I3» — XP-3, 110 мм",
  "Монтаж в разъём А2 — SMR-02V-B",
  "Усадка трубок А2 «I3»: ТТК 8/2, маркировка «I3»",
  "Свивка А2 «I3» — 1 пара",
  "Надеть трубки А3 «C»: ТТК 8/2 ×1, маркировка «C»",
  "Монтаж оплётки А3 «C» — XP-3, 110 мм",
  "Монтаж в разъём А3 — SMP-02V-BC",
  "Усадка трубок А3 «C»: ТТК 8/2, маркировка «C»",
  "Свивка А3 «C» — 1 пара",
  "Надеть трубки А4 «A6»: маркировка «A6»",
  "Монтаж оплётки А4 «A6» — XP-3, 120 мм",
  "Монтаж в разъём А4 — SMR-04V-B",
  "Усадка трубок А4 «A6»: маркировка «A6»",
  // 3. Группы сростков
  "Надеть трубку на провода сростков С1, С2 — 2 шт",
  "Сростки С1, С2 — 2 шт по 4 провода",
  "Усадка трубки на сростках С1, С2 — 2 шт",
  "Надеть трубку на провода сростков С3, С4 — 2 шт",
  "Сростки С3, С4 — 2 шт по 3 провода",
  "Усадка трубки на сростках С3, С4 — 2 шт",
  // 4. Изделие
  "Надеть трубки: ТТК 12/4мм, черная на жгут — 2 шт",
  "Усадка термоусадки на жгуте — 2 шт",
  "Монтаж в разъём В1 — I-DS1071-SCB01X3",
  "Монтаж в разъём В2 — I-DS1071-SCB02X3",
];

it("builds 713К.4 КБ1 as the ПФ graph: kits, branches X1–X4, splice groups, the product — ops in the graph's order", () => {
  expect(labels(fullHarness())).toEqual(GRAPH_ORDER);
});

it("the build config carries the graph as a generated route: every op in one block, kits marked, edges say what flows", () => {
  const S = fullHarness();
  const route = routeOf(S);
  const opKeys = keys(S);
  expect(route.source).toBe("generated");
  expect(route.blocks.map((b) => [b.key, b.name ?? "", b.ops.map((p) => opKeys[p - 1]), !!b.kit, b.final])).toEqual([
    ["pf-cutWire", "", ["cutWire"], true, false], ["pf-cutWire1", "", ["cutWire1"], true, false],
    ["pf-cutWire2", "", ["cutWire2"], true, false], ["pf-cutWire3", "", ["cutWire3"], true, false],
    ["pf-cutWire4", "", ["cutWire4"], true, false],
    ["pf-tubes", "Нарезанные трубки", ["cutTubes"], true, false], ["pf-braid", "Нарезанная оплётка", ["cutBraid"], true, false],
    ["pf-mark", "Маркировка", ["cutMark"], true, false],
    ["pf-A1", "Ветвь А1 «5»", ["onTubesA1", "braidA1", "insTermA1", "heatShrA1"], false, false],
    ["pf-A2", "Ветвь А2 «I3»", ["onTubesA2", "braidA2", "insTermA2", "heatShrA2", "twistA2"], false, false],
    ["pf-A3", "Ветвь А3 «C»", ["onTubesA3", "braidA3", "insTermA3", "heatShrA3", "twistA3"], false, false],
    ["pf-A4", "Ветвь А4 «A6»", ["onTubesA4", "braidA4", "insTermA4", "heatShrA4"], false, false],
    ["pf-spl1", "Пайка С1, С2", ["insSpl1", "splice1", "heatSpl1"], false, false],
    ["pf-spl2", "Пайка С3, С4", ["insSpl2", "splice2", "heatSpl2"], false, false],
    ["pf-final", "", ["insSleeves", "heatHs", "insTermB1", "insTermB2"], false, true],
  ]);
  expect(route.blocks.flatMap((b) => b.ops).sort((a, b) => a - b)).toEqual(opKeys.map((_, i) => i + 1));
  const edge = (from: string, to: string) => route.edges.find((e) => e.from === from && e.to === to)?.item;
  // Партия SYM отдаёт провода двум ветвям; провода с обжимом В и хвосты — в группы сростков.
  expect(edge("pf-cutWire", "pf-A1")).toBe("НВ4 0,5 красный ×1; НВ4 0,5 черный ×1");
  expect(edge("pf-cutWire", "pf-A2")).toBe("НВ4 0,12 белый ×1; НВ4 0,12 синий ×1");
  expect(edge("pf-cutWire1", "pf-spl1")).toBe("НВ4 0,2 красный ×2; НВ4 0,2 черный ×2");
  expect(edge("pf-cutWire2", "pf-spl2")).toBe("НВ4 0,12 белый ×1; НВ4 0,12 синий ×1");
  // Каждая трубка — своей стрелкой к тому, кто её надевает.
  expect(["pf-A1", "pf-A4", "pf-spl1", "pf-final"].map((to) => edge("pf-tubes", to))).toEqual(["ТТК 8/2 ×1", undefined, "ТТК 3/1 ×2", "ТТК 12/4 ×2"]);
  expect(["pf-A1", "pf-A2"].map((to) => edge("pf-braid", to))).toEqual(["XP-3 120 мм ×1", "XP-3 110 мм ×1"]);
  expect(edge("pf-mark", "pf-A4")).toBe("маркировка «A6» ×1");
  // Ветви X1–X3 — в сростки, X4 — прямо в изделие; группы — в изделие.
  expect(route.edges.filter((e) => /^pf-(A|spl)/.test(e.from)).map((e) => `${e.from}>${e.to}`).sort()).toEqual([
    "pf-A1>pf-spl1", "pf-A2>pf-spl2", "pf-A3>pf-spl2", "pf-A4>pf-final", "pf-spl1>pf-final", "pf-spl2>pf-final"]);
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
  expect(order.indexOf("twistS2")).toBe(order.indexOf("insSpl2") - 1);
  expect(labels(crossed)).toContain("Свивка перед сростками С3, С4 — 2 пары");
  expect(routeOf(crossed).blocks.find((b) => b.key === "pf-spl2")!.ops).toContain(order.indexOf("twistS2") + 1);
  // В родной разводке пара лежит внутри ветви — свивка у ветви.
  expect(keys(S0).filter((key) => /^twist/.test(key))).toEqual(["twistA2", "twistA3"]);
});

it("the technologist's own order still wins; the route follows the new positions", () => {
  const S = { ...fullHarness(), opOrder: ["braidA1", "onTubesA1"] };
  const order = keys(S);
  expect(order.indexOf("braidA1")).toBeLessThan(order.indexOf("onTubesA1"));
  expect(order.filter((key) => key !== "braidA1" && key !== "onTubesA1")).toEqual(keys(fullHarness()).filter((key) => key !== "braidA1" && key !== "onTubesA1"));
  const a1 = routeOf(S).blocks.find((b) => b.key === "pf-A1")!;
  expect(a1.ops.map((p) => order[p - 1])).toEqual(["braidA1", "onTubesA1", "insTermA1", "heatShrA1"]);
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
  expect(cfg.ops.filter((o) => o.markTube).map((o) => o.opKey)).toEqual(["cutMark"]);
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
  expect(cut.label).toBe("Резка оплётки XP-3 — А1 «5» 120 мм, А2 «I3» 110 мм, А3 «C» 153 мм");
  expect(cut.tutParts).toEqual([{ art: "XP-3", name: "XP-3", count: 1, len: "120" }, { art: "XP-3", name: "XP-3", count: 1, len: "110" },
    { art: "XP-3", name: "XP-3", count: 1, len: "153" }]);
  expect(keys(S)).not.toContain("braidA4");
  const cfg = makeGen(DSA, S0).buildConfig() as { ops: Array<Record<string, unknown>> };
  expect(cfg.ops.find((o) => o.opKey === "cutBraid")).toMatchObject({ type: "cutTut", wrapKind: "braid", tutArt: "XP-3", tutCount: 4 });
  expect(cfg.ops.find((o) => o.opKey === "braidA1")).toMatchObject({ type: "insWrap", wrapKind: "braid", wrapCut: true, wrapLen: "120", branch: "А1 «5»" });
});
