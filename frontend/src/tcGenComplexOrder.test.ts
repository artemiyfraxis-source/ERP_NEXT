// @vitest-environment node
import { expect, it } from "vitest";
import { genSnapshot, restoreFromSnapshot } from "./genBuildState";
import { initState, makeGen, setSplMethod, splMethodFromConfig, type GenData, type GenState } from "./tcGenLogic";
import { markLength, markTubeShrinks, newMarkRow } from "./tcGenMarks";
import { DSA, fullHarness, R } from "./tcGenRealHarness.fixture";

/*
 * Порядок операций «Сложной сборки» по умолчанию (владелец 16.10, проверен на 713К.4 КБ1): резка (с партиями обжима) →
 * резка всех трубок → свивка проводов, что уходят в сростки → сростки → хвосты → оплётка → надеть трубки и маркировку →
 * разъёмы А → усадки → разъёмы В. Маркировка трубками из состава — своими операциями; сростки — и ультразвуковой сваркой.
 */
const labels = (S: GenState, data: GenData = DSA) => makeGen(data, S).getActiveOps().map((o) => o.label);
const keys = (S: GenState, data: GenData = DSA) => makeGen(data, S).getActiveOps().map((o) => o.opKey);

const OWNER_ORDER = [
  "Резка с обжимом А (SYM-001T-P0.6) — 4 кабеля",
  "Резка с обжимом В (T-DS1071-SC600) — 3 кабеля",
  "Резка — 4 кабеля",
  "Резка с обжимом А (SHF-001T-0.8BS) — 2 кабеля",
  "Резка с обжимом А и В (SYM-001T-P0.6 / T-DS1071-SC600) — 3 кабеля",
  "Резка ТТК поверх разъёма А1, А2, А3, А4 — 4×15 мм",
  "Резка маркировки — «5» 15 мм, «I3» 15 мм, «C» 15 мм, «A6» 15 мм",
  "Резка трубки на сростки — 4×20 мм",
  "Свивка",
  "Зачистка концов под сростки — 14 конц.",
  "Надеть трубку на провода сростка — 4 шт",
  "Сростки — 4 шт по 4, 4, 3, 3 провода",
  "Усадка трубки на сростках — 4 шт",
  "Зачистка хвостов (3 мм) — 4 конц.",
  "Лужение хвостов — 4 конц.",
  "Монтаж оплётки — XP-3, 300 мм",
  "Надеть трубки (стороны А и В): ТТК 8/2мм, черная поверх разъёма А1, А2, А3, А4 — 4 шт",
  "Надеть маркировку у разъёмов — «5», «I3», «C», «A6»",
  "Монтаж в разъём А1 — SMR-02V-B",
  "Монтаж в разъём А2 — SMR-02V-B",
  "Монтаж в разъём А3 — SMP-02V-BC",
  "Монтаж в разъём А4 — SMR-04V-B",
  "Усадка ТТК поверх разъёма А1",
  "Усадка ТТК поверх разъёма А2",
  "Усадка ТТК поверх разъёма А3",
  "Усадка ТТК поверх разъёма А4",
  "Усадка маркировки — 4 шт",
  "Монтаж в разъём В1 — I-DS1071-SCB01X3",
  "Монтаж в разъём В2 — I-DS1071-SCB02X3",
];

it("builds 713К.4 КБ1 in the owner's order: cut, tubes, twist, splices, tails, braid, tubes on, side А, shrinks, side В", () => {
  expect(labels(fullHarness())).toEqual(OWNER_ORDER);
});

it("twisted wires that do not go into a splice keep the twist where it was: after side А, before side В", () => {
  const S0 = fullHarness();
  const S = { ...S0, twist: { ...S0.twist, pairs: [{ a: String(R.w2), b: String(R.g2) }] } };
  const order = keys(S);
  expect(order.indexOf("twist")).toBeGreaterThan(order.indexOf("heatMark"));
  expect(order.indexOf("twist")).toBe(order.indexOf("insTermB1") - 1);
  expect(order.indexOf("splice")).toBeLessThan(order.indexOf("twist"));
});

it("the technologist's own order still wins over the default one", () => {
  const S = { ...fullHarness(), opOrder: ["onMark", "insWrap"] };
  const order = keys(S);
  expect(order.indexOf("onMark")).toBeLessThan(order.indexOf("insWrap"));
  expect(order.filter((key) => key !== "onMark" && key !== "insWrap")).toEqual(keys(fullHarness()).filter((key) => key !== "onMark" && key !== "insWrap"));
});

it("marking tubes from the kit: cut with the tubes, put on before the connectors, shrunk with the shrinks; tags stay in «Маркировка»", () => {
  const S0 = fullHarness();
  const S = { ...S0, markRows: [...S0.markRows, newMarkRow({ target: "Изделие КБ1", text: "КБ1", material: "Хлястик", place: "item" })] };
  const gen = makeGen(DSA, S);
  const ops = gen.getActiveOps();
  expect(ops.find((o) => o.opKey === "cutMark")).toMatchObject({ type: "cutTut", tmplHint: "CUT_TUT_short", tutCount: 4, tutSegs: [{ len: "15", count: 4 }],
    tutParts: [{ art: "Маркировка \"5\" 4/2мм", count: 1, len: "15" }, { art: "Маркировка \"I3\" 4/2мм", count: 1, len: "15" },
      { art: "Маркировка \"C\" 4/2мм", count: 1, len: "15" }, { art: "Маркировка \"A6\" 4/2мм", count: 1, len: "15" }].map((p) => ({ ...p, name: p.art })) });
  expect(ops.find((o) => o.opKey === "onMark")).toMatchObject({ type: "insTut", tmplHint: "INS_TUT", tutCount: 4 });
  expect(ops.find((o) => o.opKey === "heatMark")).toMatchObject({ type: "heatTut", tmplHint: "HEAT_TYT_short", tutCount: 4 });
  // Хлястик — операция «Маркировка», как раньше; трубок в ней нет.
  const mark = ops.find((o) => o.type === "insMark")!;
  expect(mark.label).toBe("Маркировка — КБ1");
  expect(mark.marks).toHaveLength(1);
  const cfg = gen.buildConfig() as { ops: Array<Record<string, unknown>> };
  expect(cfg.ops.filter((o) => o.markTube).map((o) => o.opKey)).toEqual(["cutMark", "onMark", "heatMark"]);
  // Трубка без длины по КД — «?» в названии; трубка из справочника (её печатают) — в «Маркировке».
  const noLen = { ...S0, markRows: S0.markRows.map((row, i) => (i === 0 ? { ...row, len: "" } : row)) };
  expect(labels(noLen)).toContain("Резка маркировки — «5» ? мм, «I3» 15 мм, «C» 15 мм, «A6» 15 мм");
  const printed = { ...S0, markRows: [newMarkRow({ target: "Разъём 5", text: "5", material: "Термотрубка", place: "conn", len: "15" })] };
  expect(keys(printed)).not.toContain("cutMark");
  expect(keys(printed)).toContain("insMark");
});

it("marking tubes split only on «Сложная сборка»: on «Межплатная» every mark stays in the one «Маркировка» operation", () => {
  const S0 = fullHarness();
  const inter = { ...S0, tab: "inter" as const };
  const ops = makeGen(DSA, inter).getActiveOps();
  expect(ops.some((o) => o.markTube)).toBe(false);
  expect(ops.find((o) => o.type === "insMark")!.marks).toHaveLength(4);
});

it("tube length by КД «10–15» takes the upper value; «4/2мм» is a heat-shrink tube", () => {
  expect([markLength("10–15"), markLength("10-15"), markLength("12,5"), markLength("–"), markLength("")]).toEqual([15, 15, 12.5, undefined, undefined]);
  expect([markTubeShrinks("Маркировка \"5\" 4/2мм"), markTubeShrinks("Кембрик ПВХ 3 мм"), markTubeShrinks("Хлястик 4/2мм")]).toEqual([true, false, false]);
});

it("ultrasonic welding: US_WELD card, no sleeves, the splice tube goes on «на сростки»", () => {
  const S = setSplMethod(fullHarness(), "ultrasonic");
  const gen = makeGen(DSA, S);
  const splice = gen.getActiveOps().find((o) => o.opKey === "splice")!;
  expect(splice).toMatchObject({ tmplHint: "US_WELD", cat: "УЗ сварка", label: "Сростки ультразвуковой сваркой — 4 шт по 4, 4, 3, 3 провода" });
  const cfg = gen.buildConfig() as { ops: Array<Record<string, unknown>> };
  expect(cfg.ops.find((o) => o.opKey === "splice")).toMatchObject({ method: "ultrasonic", sleeves: [], templateId: "us-weld-уз-сварка" });
  expect(cfg.ops.filter((o) => o.spliceWeld).map((o) => o.opKey)).toEqual(["insSpl", "heatSpl"]);
  expect(splMethodFromConfig(cfg)).toBe("ultrasonic");
  expect(makeGen(DSA, S).spliceSleeves_()).toEqual({ items: [], missing: [] });
  // Карты US_WELD в библиотеке нет — подсказка остаётся, в названии видно, что её надо уточнить.
  const noCard: GenData = { ...DSA, templates: DSA.templates.filter((t) => !t.title.startsWith("US_WELD")) };
  expect(makeGen(noCard, S).getActiveOps().find((o) => o.opKey === "splice")!.label)
    .toBe("Сростки ультразвуковой сваркой — 4 шт по 4, 4, 3, 3 провода (карточка — уточнить)");
  // Снимок формы хранит способ.
  expect(restoreFromSnapshot(DSA.components, initState(DSA), genSnapshot(DSA.components, S, {}, {})).S.spl.method).toBe("ultrasonic");
});
