// @vitest-environment node
import { expect, it } from "vitest";
import { addEndpoint, complexByHand, initState, makeGen, updateEndpoint, type Comp, type GenData } from "./tcGenLogic";
import { fullHarness, DSA } from "./tcGenRealHarness.fixture";

/*
 * 663К.3 КБ1 (владелец 25.10, доработка двигателя): два провода с наконечниками ПМ6-6 на концах и две трубки ТУТ на жгут. Трубки надевают
 * до пайки концов, усадка — после; своего полуфабриката «ТУТ на месте Б» у такой сборки нет (он нужен, только когда под трубку
 * сходятся ветви или сростки — 713К.4).
 */
const C = (id: string, type: string, art: string, qty: number): Comp => ({ id, type, name: art, art, qty });
const data: GenData = {
  assemblyInfo: { "Индекс": "КБ1", "Наименование": "Кабельная сборка БЦТС.002.01.07181.200.00", "Кол-во": "1" }, templates: [], ops: [], coaxRecords: [],
  wireDia: null, coaxCableDia: null, calcOps: [], terRecords: [],
  components: [C("w1", "Провод", "ПВ3 6,0 красный", 1), C("w2", "Провод", "ПВ3 6,0 черный", 1), C("pm", "Соед", "ПМ6-6", 4),
    C("t1", "ТУТ", "ТУТ 6/3мм, бел", 1), C("t2", "ТУТ", "ТУТ 12/6мм, бел", 1)],
};

function motor() {
  let S = complexByHand(initState(data));
  S = addEndpoint(addEndpoint(S, "A"), "B");
  for (const ep of S.endpoints) S = updateEndpoint(data, S, ep.id, { connId: "pm" });
  S = { ...S, wires: [{ idx: 0, compId: "w1", length: 500, qty: 1 }, { idx: 1, compId: "w2", length: 500, qty: 1 }], nextWireIdx: 2 };
  return { ...S, hs: { ...S.hs, enabled: true, manual: true, rows: [{ compId: "t1", len: "100", cnt: "1" }, { compId: "t2", len: "100", cnt: "1" }] } };
}

it("simple assembly: harness tubes go on before the ends are soldered, shrink after, no separate «ТУТ на месте Б» ПФ", () => {
  const ops = makeGen(data, motor()).getActiveOps();
  const keys = ops.map((o) => o.opKey);
  expect(ops.every((o) => o.pf !== "pf-hs")).toBe(true);
  const on = keys.indexOf("insSleeves"), shrink = keys.indexOf("heatHs");
  const firstSolder = keys.findIndex((k) => k.startsWith("solderConn"));
  const lastSolder = keys.length - 1 - [...keys].reverse().findIndex((k) => k.startsWith("solderConn"));
  expect(on).toBeGreaterThanOrEqual(0);
  expect(on).toBeLessThan(firstSolder);
  expect(shrink).toBeGreaterThan(lastSolder);
  expect(ops[on].label).toContain("ТУТ 6/3мм, бел на жгут — 1 шт");
  expect(ops[on].label).toContain("ТУТ 12/6мм, бел на жгут — 1 шт");
  expect(ops[on].pf).toBe("pf-final");
  expect(ops[shrink].pf).toBe("pf-final");
});

it("a lug on the end is soldered as a lug, ends are named in Cyrillic А / В", () => {
  const labels = makeGen(data, motor()).getActiveOps().filter((o) => o.opKey.startsWith("solderConn")).map((o) => o.label);
  expect(labels).toEqual(["Пайка наконечника А1 — ПМ6-6", "Пайка наконечника А2 — ПМ6-6", "Пайка наконечника В1 — ПМ6-6", "Пайка наконечника В2 — ПМ6-6"]);
  expect(labels.join(" ")).not.toMatch(/\b[AB]\d/);
});

it("branched harness (713К.4) keeps its «ТУТ на месте Б» ПФ", () => {
  const ops = makeGen(DSA, fullHarness()).getActiveOps();
  expect(ops.filter((o) => o.pf === "pf-hs").map((o) => o.opKey)).toEqual(["insSleeves", "heatHs"]);
});
