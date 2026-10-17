// @vitest-environment node
import { expect, it } from "vitest";
import { genSnapshot, restoreFromSnapshot } from "./genBuildState";
import { freeSide, pinoutEnds, pinUnits } from "./pinoutCheck";
import { genArtHeight, schemeArt, type ArtCable } from "./routeCards";
import { initState, makeGen, setCableJacketShrink, updateEndpoint, type Comp, type GenData, type GenState } from "./tcGenLogic";

// Владелец 15.10, 545К.9 КБ1 «Кабель TC-BLS-5-BLS-5-400-RLB» ×50: «визуал полуфабриката неправильный; так же нет резки
// ТУТ, установки и термоусаживания». Схема рисовала четыре провода А1:1→В1:1 … вместо одного кабеля на 4 жилы.
const DS = { id: "c1", type: "Соед", name: "I-DS1071-SCB01X5", art: "I-DS1071-SCB01X5", qty: 2 };
const TERM = { id: "t1", type: "Терминал", name: "T-DS1071-SC600", art: "T-DS1071-SC600", qty: 8 };
const UL = { id: "w1", type: "Провод", name: "UL2527 28AWG", art: "UL2527 28AWG", kd: "UL2575 26AWG", qty: 0.42 };
const TUT = { id: "h1", type: "ТУТ", name: "ТУТ 6/3мм черная", art: "ТУТ 6/3мм черная", qty: 0.06 };

function data(components: Comp[]): GenData {
  return {
    components, assemblyInfo: { "Индекс": "КБ1", "Наименование": "Кабель TC-BLS-5-BLS-5-400-RLB", "Кол-во": "50" },
    templates: [], ops: [], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [], cableRecords: [],
    terRecords: [{ article: "T-DS1071-SC600", step: "2.54", strip: "3.2", lPlus: "5", lMinus: "", applicator: "M21", awgFrom: "", awgTo: "", mm2From: "", mm2To: "" }],
  };
}
const ownerBom = () => data([DS, TERM, UL, TUT]);
/** Форма владельца: «Без зачистки (OTP)» на стороне А. */
const ownerForm = (D: GenData) => { const S = initState(D); return updateEndpoint(D, S, S.endpoints[0].id, { otp: true }); };

function art(D: GenData, S: GenState) {
  const gen = makeGen(D, S);
  const cables: ArtCable[] = gen.activeWires_().filter((w) => gen.isJacketedCable_(w) && gen.wireCores_(w) > 1)
    .map((w) => ({ idx: w.idx, mark: gen.getCompById(w.compId)?.art || "", cores: gen.wireCores_(w) }));
  const args = [gen.getActiveOps(), pinoutEnds(gen, S), pinUnits(gen, S).filter((u) => !u.shield), {}, freeSide(gen, S)] as const;
  return { withCables: schemeArt(...args, cables), plain: schemeArt(...args), cables };
}

it("owner's BOM: the semi-finished picture is ONE cable with 4 cores, stripped on both ends after the cut, crimped per side", () => {
  const D = ownerBom();
  const S = ownerForm(D);
  const { withCables: a, cables } = art(D, S);
  expect(cables).toEqual([{ idx: 0, mark: "UL2527 28AWG", cores: 4 }]);
  expect(a.wires).toHaveLength(1);
  const cable = a.wires[0];
  expect(cable.label).toBe("UL2527 28AWG");
  expect(cable.cable?.cores).toHaveLength(4);
  expect(cable.cable?.sub).toBe("4 жилы · А1 → В1");
  // Ротор снимает оболочку и зачищает жилы с обеих сторон.
  expect([cable.left, cable.right]).toEqual(["copper", "copper"]);
  // «Обжатые провода, сторона А» — наконечники на жилах А, В ещё зачищена; общая — с обеих сторон.
  expect(a.crimpedBy?.A.map((w) => [w.cable?.cores.length, w.left, w.right])).toEqual([[4, "terminal", "copper"]]);
  expect(a.crimpedBy?.B.map((w) => [w.cable?.cores.length, w.left, w.right])).toEqual([[4, "copper", "terminal"]]);
  expect(a.crimped.map((w) => [w.left, w.right])).toEqual([["terminal", "terminal"]]);
  // Строка кабеля выше строки провода.
  expect(genArtHeight("wires", a, false)).toBeGreaterThan(22 + 8);
});

it("core colours ride on the cable's cores in order", () => {
  const D = ownerBom();
  let S = ownerForm(D);
  S = { ...S, wires: S.wires.map((w) => ({ ...w, coreColors: ["красный", "белый", "зелёный", "чёрный"] })) };
  const cores = art(D, S).withCables.wires[0].cable!.cores;
  expect(cores.map((c) => c.label.split(" · ")[0])).toEqual(["1.1 красный", "1.2 белый", "1.3 зелёный", "1.4 чёрный"]);
  expect(new Set(cores.map((c) => c.color)).size).toBe(4);
});

it("single wires keep today's drawing — one row per wire, no cable", () => {
  const D = data([{ id: "w", type: "Провод", name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 0.3 }, { ...TERM, qty: 4 }, DS]);
  const S = initState(D);
  const { withCables, plain, cables } = art(D, S);
  expect(cables).toEqual([]);
  expect(withCables).toEqual(plain);
  expect(withCables.wires.every((w) => !w.cable)).toBe(true);
});

it("default ON: a free ТУТ in the BOM and a jacketed multicore cable — both sides, on the cable, not on the form", () => {
  const D = ownerBom();
  const S = initState(D);
  expect(S.wires[0].jacketShrink).toEqual({ A: true, B: true, mat: "", len: "" });
  expect(S.jacketShrink).toBeUndefined();
  // Нет ТУТ — нет и галочки.
  expect(initState(data([DS, TERM, UL])).wires[0].jacketShrink).toBeUndefined();
  // Одиночные провода — не кабель в оболочке.
  expect(initState(data([{ id: "w", type: "Провод", name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 0.3 }, TERM, DS, TUT])).wires[0].jacketShrink).toBeUndefined();
  // ТУТ занята чашками пайки — по умолчанию не ставится.
  const DB9 = { id: "d1", type: "Соед", name: "DB-9F", art: "DB-9F", qty: 2 };
  expect(initState(data([DB9, UL, TUT])).wires[0].jacketShrink).toBeUndefined();
});

it("owner's BOM: ops order and labels — cut, tube cut with its norm, put on both sides, crimp A and B, mount A, shrink A, mount B, shrink B", () => {
  const D = ownerBom();
  const ops = makeGen(D, ownerForm(D)).getActiveOps();
  // Кабель в оболочке — обжим обеих сторон до монтажа, ротор снимает только оболочку, жилы В — своя зачистка, на А —
  // «Без зачистки (ОТП)» (владелец 15.10, tcGenMulticoreCrimpOrder.test.ts).
  expect(ops.map((o) => o.label)).toEqual([
    "Резка с разделкой на роторе (2 ст.: оболочка 20 мм) — UL2527 28AWG",
    "Резка ТУТ на место разделки А, В — 2×25 мм",
    "Надеть трубки (стороны А и В): ТУТ 6/3мм черная на место разделки А, В — 2 шт",
    "Опрессовка терминалов А1",
    "Зачистка жил под обжим В1 (3,2 мм) — 4 шт",
    "Опрессовка терминалов В1",
    "Монтаж в разъём А1 — I-DS1071-SCB01X5",
    "Усадка ТУТ на месте разделки А — 1 шт",
    "Монтаж в разъём В1 — I-DS1071-SCB01X5",
    "Усадка ТУТ на месте разделки В — 1 шт",
  ]);
  expect(ops[1]).toMatchObject({ type: "cutTut", tutArt: "ТУТ 6/3мм черная", tutCount: 2, tutSegs: [{ len: "25", count: 2 }] });
  expect(ops.filter((o) => o.type === "heatTut").map((o) => o.side)).toEqual(["A", "B"]);
});

it("a form saved before the cable field: the per-side value is read as a fallback and moved onto the cable on the first edit", () => {
  const D = ownerBom();
  const seeded = initState(D);
  const old: GenState = { ...seeded, wires: seeded.wires.map(({ jacketShrink: _drop, ...w }) => w), jacketShrink: { B: { on: true, mat: "", len: "30" } } };
  const g = makeGen(D, old);
  expect(g.cableJacketShrink_(old.wires[0])).toEqual({ A: false, B: true, mat: "", len: "30" });
  expect(g.getActiveOps().find((o) => o.opKey === "cutJs1")!.label).toBe("Резка ТУТ на место разделки В — 1×30 мм");
  const moved = setCableJacketShrink(D, old, 0, { A: true });
  expect(moved.jacketShrink).toBeUndefined();
  expect(moved.wires[0].jacketShrink).toEqual({ A: true, B: true, mat: "", len: "30" });
});

it("a built card's saved form is not flipped by the new default", () => {
  const D = ownerBom();
  const seeded = initState(D);
  const saved: GenState = { ...seeded, wires: seeded.wires.map(({ jacketShrink: _drop, ...w }) => w) };
  const back = restoreFromSnapshot(D.components, seeded, genSnapshot(D.components, saved, {}, {}));
  expect(back.S.wires[0].jacketShrink).toBeUndefined();
  expect(makeGen(D, back.S).getActiveOps().some((o) => /Js/.test(o.opKey))).toBe(false);
  // Свой выбор трубки переживает сдвиг строк состава.
  const own = setCableJacketShrink(D, seeded, 0, { mat: "h1" });
  expect(restoreFromSnapshot(D.components, seeded, genSnapshot(D.components, own, {}, {})).S.wires[0].jacketShrink?.mat).toBe("h1");
});
