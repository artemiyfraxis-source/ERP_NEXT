// @vitest-environment node
import { expect, it } from "vitest";
import { pinUnits, setCableCoreColors } from "./pinoutCheck";
import { cableCoresFromName_, initState, makeGen, type CableRec, type Comp, type GenData } from "./tcGenLogic";

// Случай владельца (14.10): «Межплатная» КБ1 «Кабель TC-BLS-5-BLS-5-400-RLB» — два I-DS1071-SCB01X5, 8 терминалов
// T-DS1071-SC600 и «UL2575 26AWG» 0,42 м на изделие. Записи БД.ПРОВ нет — это один кабель на 4 жилы, а не 4 провода.
function data(wire: string, norma = 0.42, recs: CableRec[] = [], product = "Кабель TC-BLS-5-BLS-5-400-RLB"): GenData {
  const components: Comp[] = [
    { id: "c1", type: "Соед", name: "I-DS1071-SCB01X5", art: "I-DS1071-SCB01X5", qty: 2 },
    { id: "t1", type: "Терминал", name: "T-DS1071-SC600", art: "T-DS1071-SC600", qty: 8 },
    { id: "w1", type: "Провод", name: wire, art: wire, qty: norma },
    { id: "h1", type: "ТУТ", name: "ТУТ 6/3мм", art: "ТУТ 6/3мм", qty: 0.06 },
  ];
  return {
    components, assemblyInfo: { "Индекс": "КБ1", "Наименование": product },
    templates: [], ops: [], terRecords: [], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [], cableRecords: recs,
  };
}
const seeded = (D: GenData) => { const S = initState(D); const g = makeGen(D, S); return { S, g, w: S.wires[0] }; };

it("«UL2575 26AWG» 0,42 м, 4 терминала на сторону: 1 кабель × 4 жилы, кабель в оболочке", () => {
  const { g, w } = seeded(data("UL2575 26AWG"));
  expect([w.qty, g.wireCores_(w), g.unitCount_(w)]).toEqual([1, 4, 4]);
  expect(g.isJacketedCable_(w)).toBe(true);
  // Длина в наименовании: 0,42 м — один кабель 400 мм.
  const named = seeded(data("UL2575 26AWG", 0.42, [], "Кабель TC-BLS-5 400мм"));
  expect([named.w.length, named.w.qty, named.w.cores]).toEqual([400, 1, 4]);
});

it("other UL multi-conductor styles too (UL2464, UL20276); a norm of 4 cables keeps 4 cables", () => {
  for (const name of ["UL2464 26AWG", "UL20276 28AWG", "UL-2547 24AWG"]) {
    const { w } = seeded(data(name));
    expect([name, w.qty, w.cores]).toEqual([name, 1, 4]);
  }
  const four = seeded(data("UL2575 26AWG", 1.68, [], "Кабель 400мм")).w;
  expect([four.qty, four.cores ?? 1]).toEqual([4, 1]);
  const two = seeded(data("UL2575 26AWG", 0.84, [], "Кабель 400мм")).w;
  expect([two.qty, two.cores]).toEqual([2, 2]);
});

it("single wires stay single: НВ4 0,2, UL1007, UL1061, UL3239, UL10368", () => {
  for (const name of ["НВ4 0,2", "UL1007 26AWG", "UL1061 24AWG", "UL3239 22AWG", "UL10368 26AWG"]) {
    const { g, w } = seeded(data(name));
    expect([name, w.qty, g.wireCores_(w), g.isJacketedCable_(w)]).toEqual([name, 4, 1, false]);
  }
});

it("a БД.ПРОВ record with «Число жил» decides itself: its cores and colours, the name style is not consulted", () => {
  const rec: CableRec = { mark: "UL2575 26AWG", cores: 4, colors: ["белый", "коричневый", "зелёный", "жёлтый"], screen: "" };
  const { S, g, w } = seeded(data("UL2575 26AWG", 0.42, [rec]));
  expect([w.qty, g.wireCores_(w)]).toEqual([1, 4]);
  expect(g.coreColors_(w)).toEqual(rec.colors);
  // Запись с одной жилой (UL2725 28AWG в БД.ПРОВ) — не кабель по стилю.
  const single = seeded(data("UL2725 28AWG", 0.42, [{ mark: "UL2725 28AWG", cores: 1, colors: [], screen: "" }]));
  expect([single.w.qty, single.g.isJacketedCable_(single.w)]).toEqual([4, false]);
  expect(pinUnits(g, S)).toHaveLength(4);
});

it("cores from the name: «24AWG 4C», «4x26AWG»", () => {
  expect(cableCoresFromName_("UL2464 24AWG 4C")).toBe(4);
  expect(cableCoresFromName_("4xAWG26")).toBe(4);
  expect(cableCoresFromName_("4x26AWG")).toBe(4);
  expect(cableCoresFromName_("UL1007 105C")).toBe(1);
  expect(cableCoresFromName_("НВ4 0,2")).toBe(1);
});

it("«Цвета жил»: core 3 of every cable in the line, null returns the БД.ПРОВ colour, the colours go to build_config", () => {
  const D = data("UL2575 26AWG");
  let { S } = seeded(D);
  S = { ...S, wires: [{ ...S.wires[0], qty: 2 }] };
  S = setCableCoreColors(D, S, S.wires[0].idx, { 2: "белый", 0: "красный" });
  const g = makeGen(D, S);
  expect(g.coreColors_(S.wires[0])).toEqual(["красный", "", "белый", "", "красный", "", "белый", ""]);
  S = setCableCoreColors(D, S, S.wires[0].idx, { 0: null, 9: "синий" });
  expect(makeGen(D, S).coreColors_(S.wires[0])).toEqual(["", "", "белый", "", "", "", "белый", ""]);
  const cfg = makeGen(D, S).buildConfig() as { wires: Array<{ coreColors?: string[] }> };
  expect(cfg.wires[0].coreColors).toEqual(["", "", "белый", "", "", "", "белый", ""]);
});
