// @vitest-environment node
import { expect, it } from "vitest";
import { initState, makeGen, updateEndpoint, type Comp, type GenData, type GenState, type OpSpec } from "./tcGenLogic";

// Владелец 15.10: «на многожильном должна быть разделка — это вроде есть, но жилы не зачищаются; так же вопрос по
// термоусадке на место зачистки: она вешается опционально с опциональной длиной, если длина не указывается — 25 мм»;
// «даже до обжимки она одевается, потому что с контактами неудобно; то же самое с оплёткой»; «термоусадки должны
// одеваться сразу на две стороны; … у пайки должна быть функция сборки корпуса после пайки».
const DS = { id: "c1", type: "Соед", name: "I-DS1071-SCB01X5", art: "I-DS1071-SCB01X5", qty: 2 };
const TERM = { id: "t1", type: "Терминал", name: "T-DS1071-SC600", art: "T-DS1071-SC600", qty: 8 };
const UL = { id: "w1", type: "Провод", name: "UL2575 26AWG", art: "UL2575 26AWG", qty: 0.42 };
const TUT = { id: "h1", type: "ТУТ", name: "ТУТ 6/3мм", art: "ТУТ 6/3мм", qty: 0.06 };
const DB9 = { id: "d1", type: "Соед", name: "DB-9F", art: "DB-9F", qty: 1 };

function data(components: Comp[]): GenData {
  return {
    components, assemblyInfo: { "Индекс": "КБ1", "Наименование": "Кабель TC-BLS-5-BLS-5-400-RLB", "Кол-во": "50" },
    templates: [], ops: [], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [], cableRecords: [],
    terRecords: [{ article: "T-DS1071-SC600", step: "2.54", strip: "3.5", lPlus: "5", lMinus: "", applicator: "M21/A14", awgFrom: "", awgTo: "", mm2From: "", mm2To: "" }],
  };
}
const ownerBom = () => data([DS, TERM, UL, TUT]);
const keysOf = (ops: OpSpec[]) => ops.map((o) => o.opKey);
const at = (ops: OpSpec[], test: (o: OpSpec) => boolean) => ops.findIndex(test);
const lastAt = (ops: OpSpec[], test: (o: OpSpec) => boolean) => ops.reduce((n, o, i) => (test(o) ? i : n), -1);
/** Сторона В — паяльный DB-9F (обжим на А). */
function mixed(): { D: GenData; S: GenState } {
  const D = data([DS, { ...TERM, qty: 4 }, UL, TUT, DB9]);
  let S = initState(D);
  const b = S.endpoints.find((e) => e.side === "B")!;
  S = updateEndpoint(D, S, b.id, { connId: "d1", method: "solder", methodAuto: false, termId: "" });
  return { D, S };
}

it("rotor on: the cut strips the jacket AND the cores — both sizes in the strip data and in the name", () => {
  const D = ownerBom();
  const S = initState(D);
  expect(S.rotor).toBe(true);
  const cut = makeGen(D, S).getActiveOps().find((o) => o.type === "cutWireStrip")!;
  expect(cut.label).toBe("Резка с разделкой на роторе (2 ст.: оболочка 20 мм, жилы 3,5 мм) — UL2575 26AWG");
  expect(cut.strip).toEqual({ a: true, lenA: "20", b: true, lenB: "20", coreA: "3.5", coreB: "3.5" });
  // «Разделка» строки «Кабели» — длина оболочки; своя зачистка у разъёма — длина жил.
  const own = updateEndpoint(D, { ...S, wires: [{ ...S.wires[0], jacket: "25" }] }, S.endpoints[1].id, { stripLen: "4" });
  expect(makeGen(D, own).getActiveOps().find((o) => o.type === "cutWireStrip")!.strip).toEqual({ a: true, lenA: "25", b: true, lenB: "25", coreA: "3.5", coreB: "4" });
  expect(makeGen(D, own).getActiveOps().find((o) => o.type === "cutWireStrip")!.label).toContain("(2 ст.: оболочка 25 мм, жилы А 3,5 мм, В 4 мм)");
});

it("rotor off: «Разделка кабеля» at the connector strips the cores too, with the terminal's strip length", () => {
  const D = ownerBom();
  const S = { ...initState(D), rotor: false };
  const strips = makeGen(D, S).getActiveOps().filter((o) => o.opKey.startsWith("stripCable"));
  expect(strips.map((o) => [o.label, o.strip])).toEqual([
    ["Разделка кабеля под обжим А1 (оболочка 20 мм, жилы 3,5 мм) — 1 конц.", { a: true, lenA: "20", b: false, lenB: "", coreA: "3.5" }],
    ["Разделка кабеля под обжим В1 (оболочка 20 мм, жилы 3,5 мм) — 1 конц.", { a: false, lenA: "", b: true, lenB: "20", coreB: "3.5" }],
  ]);
  // Резка без ротора концы кабеля не трогает.
  expect(makeGen(D, S).getActiveOps().find((o) => /^cutWire/.test(o.type))!.type).toBe("cutWire");
});

it("rotor on, a solder end: the jacket and the cores under the solder strip", () => {
  const { D, S } = mixed();
  const cut = makeGen(D, S).getActiveOps().find((o) => o.type === "cutWireStrip")!;
  expect(cut.strip).toEqual({ a: true, lenA: "20", b: true, lenB: "20", coreA: "3.5", coreB: "5" });
});

it("heat-shrink on the jacket cut: 25 mm by default, cut ×cables, put on before any crimp with the other tubes, shrunk per side", () => {
  const D = ownerBom();
  const base = initState(D);
  expect(base.jacketShrink).toBeUndefined();
  expect(makeGen(D, base).getActiveOps().some((o) => /Js|Sleeves/.test(o.opKey))).toBe(false);
  const S: GenState = { ...base, jacketShrink: { A: { on: true, mat: "", len: "" }, B: { on: true, mat: "", len: "" } } };
  const g = makeGen(D, S);
  const ops = g.getActiveOps();
  expect(ops.find((o) => o.opKey === "cutJs1")).toMatchObject({ type: "cutTut", tutArt: "ТУТ 6/3мм", tutCount: 2, tutSegs: [{ len: "25", count: 2 }],
    label: "Резка ТУТ на место разделки А, В — 2×25 мм" });
  expect(ops.filter((o) => o.opKey === "insSleeves")).toHaveLength(1);
  expect(ops.find((o) => o.opKey === "insSleeves")).toMatchObject({ type: "insTut", harness: true, tutCount: 2,
    label: "Надеть трубки (стороны А и В): ТУТ 6/3мм на место разделки А, В — 2 шт" });
  const put = keysOf(ops).indexOf("insSleeves");
  expect(keysOf(ops).indexOf("cutJs1")).toBeLessThan(put);
  // Надевают до обжима обеих сторон, усаживают после монтажа своей стороны.
  expect(put).toBeLessThan(at(ops, (o) => o.type === "prsTerm"));
  expect(keysOf(ops).indexOf("heatJsA")).toBe(lastAt(ops, (o) => o.side === "A" && !!o.epId) + 1);
  expect(keysOf(ops).indexOf("heatJsB")).toBe(lastAt(ops, (o) => o.side === "B" && !!o.epId) + 1);
  expect(ops.find((o) => o.opKey === "heatJsA")).toMatchObject({ type: "heatTut", tutCount: 1, label: "Усадка ТУТ на месте разделки А — 1 шт" });
  // Своя длина; та же трубка не считается ещё раз термоусадкой на жгут.
  const own: GenState = { ...S, jacketShrink: { A: { on: true, mat: "h1", len: "30" } } };
  expect(makeGen(D, own).getActiveOps().find((o) => o.opKey === "cutJs1")!.tutSegs).toEqual([{ len: "30", count: 1 }]);
  expect(g.harnessSleeveComps_()).toEqual([]);
  expect(makeGen(D, base).harnessSleeveComps_().map((c) => c.id)).toEqual(["h1"]);
});

it("one put-on for both sides: connector sleeves, jacket tubes and the harness heat-shrink together, before the first crimp", () => {
  const D = ownerBom();
  let S: GenState = { ...initState(D), jacketShrink: { B: { on: true, mat: "", len: "" } } };
  S = updateEndpoint(D, S, S.endpoints[0].id, { shrinkOn: true, shrinkLen: "15" });
  const ops = makeGen(D, S).getActiveOps();
  expect(ops.filter((o) => o.type === "insTut").map((o) => o.opKey)).toEqual(["insSleeves"]);
  expect(ops.find((o) => o.opKey === "insSleeves")!.label).toBe("Надеть трубки (стороны А и В): ТУТ 6/3мм на место разделки В — 1 шт; ТУТ 6/3мм поверх разъёма А1 — 1 шт");
  expect(keysOf(ops).indexOf("insSleeves")).toBeLessThan(at(ops, (o) => o.type === "prsTerm" || o.type === "stripCable"));
  expect(keysOf(ops).indexOf("heatShrA1")).toBe(lastAt(ops, (o) => o.epId === S.endpoints[0].id) + 1);
});

it("braid goes on before the crimp too", () => {
  const BRAID = { id: "br", type: "Оплётка", name: "Оплётка ПЭТ 6мм", art: "Оплётка ПЭТ 6мм", qty: 0.4 };
  const D = data([DS, TERM, UL, BRAID]);
  const S: GenState = { ...initState(D), wrap: { enabled: true, slots: [{ mat: "br", kind: "braid", len: "400" }, ...initState(D).wrap.slots.slice(1)] } };
  const ops = makeGen(D, S).getActiveOps();
  const braid = at(ops, (o) => o.type === "insWrap");
  expect(braid).toBeGreaterThan(-1);
  expect(braid).toBeLessThan(at(ops, (o) => o.type === "prsTerm"));
});

it("crimp on the cutting machine with a tube to put on first: the form warns instead of an impossible order", () => {
  // Одиночные провода, терминал с аппликатором автомата: станок обжимает при резке — трубку поверх разъёма раньше не надеть.
  const D: GenData = { ...data([{ id: "w", type: "Провод", name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 0.3 }, TERM, DS, TUT]),
    terRecords: [{ article: "T-DS1071-SC600", step: "2.54", strip: "3.5", lPlus: "5", lMinus: "", applicator: "A14", awgFrom: "", awgTo: "", mm2From: "", mm2To: "" }] };
  let S: GenState = { ...initState(D), crimpCut: true, crimpCutManual: true };
  expect(makeGen(D, S).getActiveOps().some((o) => o.type === "cutWireCrimp")).toBe(true);
  expect(makeGen(D, S).sleeveWarning_()).toBe("");
  S = updateEndpoint(D, S, S.endpoints[0].id, { shrinkOn: true });
  expect(makeGen(D, S).sleeveWarning_()).toBe("Трубку надо надеть до обжима — обжим на станке для сторон А и В отключите или наденьте трубку до резки.");
  expect(makeGen(D, { ...S, crimpCut: false }).sleeveWarning_()).toBe("");
});

it("solder D-Sub: housing parts and tubes on first, then strip, TUT, solder, shrink TUT, assemble the housing, shrink outer tubes", () => {
  const { D, S: s0 } = mixed();
  let S: GenState = { ...s0, jacketShrink: { B: { on: true, mat: "", len: "" } } };
  const b = S.endpoints.find((e) => e.side === "B")!;
  S = updateEndpoint(D, S, b.id, { shrinkOn: true });
  const g = makeGen(D, S);
  expect(g.housingOn_(S.endpoints.find((e) => e.side === "B")!)).toBe(true);
  const ops = g.getActiveOps();
  const i = (test: (o: OpSpec) => boolean) => at(ops, test);
  const on = i((o) => o.opKey === "insSleeves"), tut = i((o) => o.opKey === "insTut"), solder = i((o) => o.type === "solderConn");
  const heat = i((o) => o.opKey === "heatTut"), housing = i((o) => o.type === "insHousing");
  const outer = i((o) => o.opKey === "heatShrB1"), jacket = i((o) => o.opKey === "heatJsB");
  expect(ops[on].label).toContain("детали корпуса В1 (DB-9F)");
  expect(on).toBeLessThan(i((o) => o.type === "prsTerm"));
  expect([on < tut, tut < solder, solder < heat, heat < housing, housing < outer, housing < jacket]).toEqual([true, true, true, true, true, true]);
  expect(ops[housing]).toMatchObject({ side: "B", tmplHint: "INS_housing_DB/DH/СНП", units: 1, label: "Сборка корпуса после пайки В1 — DB-9F" });
  // Только обжим на А: надеть до обжима А.
  expect(on).toBeLessThan(i((o) => o.side === "A" && !!o.epId));
});

it("«Сборка корпуса после пайки»: on by default for D-Sub, off for a plain solder connector, the technologist's choice wins", () => {
  const { D, S } = mixed();
  const b = S.endpoints.find((e) => e.side === "B")!;
  const off = updateEndpoint(D, S, b.id, { housingOn: false });
  expect(makeGen(D, off).getActiveOps().some((o) => o.type === "insHousing")).toBe(false);
  expect(makeGen(D, off).getActiveOps().some((o) => o.opKey === "insSleeves")).toBe(false);
  const XT = { id: "x1", type: "Соед", name: "XT60 папа", art: "XT60-M", qty: 2 };
  const plain = data([{ id: "w", type: "Провод", name: "ПВАМ 0,5", art: "ПВАМ 0,5", qty: 0.3 }, XT]);
  const ps = initState(plain);
  expect(ps.endpoints.every((e) => e.method === "solder")).toBe(true);
  expect(makeGen(plain, ps).getActiveOps().some((o) => o.type === "insHousing")).toBe(false);
  // Включили руками — карта не подобрана: операция есть, норму уточнить.
  const forced = updateEndpoint(plain, ps, ps.endpoints[0].id, { housingOn: true });
  expect(makeGen(plain, forced).getActiveOps().find((o) => o.type === "insHousing")!.label).toBe("Сборка корпуса после пайки А1 — XT60-M (норма — уточнить)");
});

it("crimp only, both sides: tubes on before both crimps", () => {
  const D = ownerBom();
  const S: GenState = { ...initState(D), jacketShrink: { A: { on: true, mat: "", len: "" }, B: { on: true, mat: "", len: "" } } };
  const ops = makeGen(D, S).getActiveOps();
  const on = keysOf(ops).indexOf("insSleeves");
  for (const side of ["A", "B"] as const) expect(on).toBeLessThan(at(ops, (o) => o.type === "prsTerm" && o.side === side));
});
