import { expect, it } from "vitest";
import { findWireMark_, initState, makeGen, WIRE_DIA_MISSING_, WIRE_DIA_OLD_, wireDiaFromRecords, type GenData } from "./tcGenLogic";

// Строки GET /api/references/wire-dia (backend references.wire_dia): AWG · ГОСТ · Ø жилы · 12 серий базы монтажных.
const SERIES = ["UL1061", "UL1571", "UL1007", "UL3239", "НВ-4", "ПВАМ", "МГТФ", "МГШВ", "МС 16-13", "МС 15-11", "МК 26-13", "ПуГВ"];
function row(awg: string, gost: string, core: string, values: Record<string, string>, old = "") {
  return { "По старой таблице": old, AWG: awg, "ГОСТ": gost, "Ø жилы": core, ...Object.fromEntries(SERIES.map((s) => [s, values[s] || "—"])) };
}
const ROWS = [
  row("30", "0,05", "0,26", { UL1061: "0,81" }),
  row("", "0,07", "—", { "МГТФ": "0,75" }),
  row("28", "0,08", "0,32", { UL1061: "0,89", "МС 16-13": "0,72" }),
  row("26", "0,12", "0,41", { "МГТФ": "0,9", "НВ-4": "1,3" }),
  row("20", "0,5", "0,81", { "ПВАМ": "1,6", "НВ-4": "1,8", "МГТФ": "1,35", "МС 15-11": "1,5" }, "МГТФ, МС 15-11"),
  row("18", "0,75", "1,02", { "ПуГВ": "2,3" }),
  row("17", "1", "1,15", { "ПуГВ": "2,5" }),
];

function data(wire: { name: string; art: string }, extra: Partial<GenData> = {}): GenData {
  return {
    components: [
      { id: "w1", type: "Провод", name: wire.name, art: wire.art, qty: 0.3 },
      { id: "w2", type: "Провод", name: wire.name, art: wire.art, qty: 0.3 },
      { id: "t", type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6", qty: 4 },
      { id: "c", type: "Соед", name: "Колодка XH 2", art: "XHP-2", qty: 2 },
    ],
    assemblyInfo: { "Индекс": "КБ1", "Наименование": "Жгут XHP 150мм" },
    templates: [{ id: "cut", title: "CUT_WIRE_auto | Резка", category: "Резка" }],
    ops: [{ opNumber: "CUT_WIRE_auto", opName: "Резка", tOp: "12", tPrep: "60", tMachine: "3" }],
    terRecords: [{ article: "SXH-001T-P0.6", step: "2.50", strip: "2", lPlus: "5", lMinus: "3", applicator: "S12", awgFrom: "", awgTo: "", mm2From: "", mm2To: "" }],
    coaxRecords: [], wireDia: wireDiaFromRecords(ROWS), coaxCableDia: null, calcOps: [],
    ...extra,
  };
}

function twisted(D: GenData, dia = "") {
  const S = initState(D);
  return makeGen(D, { ...S, twist: { ...S.twist, enabled: true, mode: "all", wires: null, pitch: "20", dia, diaManual: !!dia } });
}

it("reads the hook-up wire base table: 12 series columns, the core Ø and a ГОСТ-only size by its section", () => {
  const wd = wireDiaFromRecords(ROWS)!;
  expect(wd.marks.map((m) => m.name)).toEqual(SERIES);
  expect(wd.marks.find((m) => m.name === "МС 16-13")?.norm).toBe("мс1613");
  expect(wd.byAwg["28"]).toEqual({ __core: 0.32, ul1061: 0.89, мс1613: 0.72 });
  expect(wd.byGost["0.07"]).toEqual({ мгтф: 0.75 });
  expect(Object.keys(wd.byAwg)).not.toContain("");
  // ГОСТ → AWG только по строкам с целым AWG (0,07 — без AWG)
  expect(wd.gostAwg).toEqual({ "0.05": 30, "0.08": 28, "0.12": 26, "0.5": 20, "0.75": 18, "1": 17 });
});

it("finds a series only as a whole mark: МНВ-4, ПуГВнг(А)-LS, МГТФЭ and UL10610 are other wires", () => {
  const marks = wireDiaFromRecords(ROWS)!.marks;
  const name = (art: string) => findWireMark_(art.toLowerCase().replace(/,/g, "."), marks)?.mk.name ?? null;
  expect(name("МС16-13 0,08")).toBe("МС 16-13");
  expect(name("Провод МС 16-13 0,08")).toBe("МС 16-13");
  expect(name("НВ-4 600 0,12")).toBe("НВ-4");
  expect(name("UL1061 28AWG черный")).toBe("UL1061");
  expect(name("MГТФ 0,07")).toBe("МГТФ"); // латинская M вместо кириллической
  expect(name("МНВ-4 0,12")).toBeNull();
  expect(name("ПуГВнг(А)-LS 1х1,0")).toBeNull();
  expect(name("МГТФЭ 0,12")).toBeNull();
  expect(name("UL10610 28AWG")).toBeNull();
  expect(name("UL1015 26AWG")).toBeNull();
});

it("takes the twist wire Ø from the hook-up wire base by AWG, by section and for a ГОСТ-only size", () => {
  expect(twisted(data({ name: "UL1061 28AWG", art: "UL1061 28AWG" })).twistDiaValue()).toBe("0,89");
  expect(twisted(data({ name: "МС16-13 0,08", art: "МС16-13 0,08" })).twistDiaValue()).toBe("0,72");
  expect(twisted(data({ name: "МГТФ 0,07", art: "МГТФ 0,07" })).twistDiaValue()).toBe("0,75");
  // «1х» — число жил, сечение 0,75
  expect(twisted(data({ name: "ПуГВ 1х0,75", art: "ПуГВ 1х0,75" })).twistDiaValue()).toBe("2,3");
  expect(twisted(data({ name: "ПуГВ 1,0", art: "ПуГВ 1,0" })).twistDiaValue()).toBe("2,5");
  const gen = twisted(data({ name: "МГТФ 0,12", art: "МГТФ 0,12" }));
  expect(gen.twistDiaMissing_()).toEqual([]);
  // Z₀ по геометрии: Ø жилы из «Сечения AWG ↔ мм²», ε_r МГТФ
  expect(gen.twistGeom_()).toMatchObject({ d: 0.41, D: 0.9, eps: 2.1, epsExplicit: true });
});

it("ε_r is known for the new series of the base (МС 16-13 like МГТФ)", () => {
  const gen = twisted(data({ name: "МС16-13 0,08", art: "МС16-13 0,08" }));
  expect(gen.twistGeom_()).toMatchObject({ d: 0.32, D: 0.72, eps: 2.1, epsExplicit: true, epsMark: "МС 16-13" });
  expect(gen.twistNote().text).toBe("ε_r = 2.1 (МС 16-13)");
});

it("a wire outside the base gets the visible note instead of a silent 0, a manual Ø lifts it", () => {
  for (const art of ["UL1015 26AWG", "Силиконовый 26AWG черный", "ПуГВнг(А)-LS 1х1,0", "НВ-4 1,5"]) {
    const gen = twisted(data({ name: art, art }));
    expect(gen.twistDiaValue()).toBe("");
    expect(gen.twistDiaMissing_()).toEqual([art]);
    expect(gen.validate().warnings).toContain(`• Свивка: «${art}» — ${WIRE_DIA_MISSING_} (поле «Ø провода»)`);
  }
  const manual = twisted(data({ name: "UL1015 26AWG", art: "UL1015 26AWG" }), "1,5");
  expect(manual.twistDiaMissing_()).toEqual([]);
  expect(manual.validate().warnings.join("\n")).not.toContain(WIRE_DIA_MISSING_);
  // без свивки подсказки нет
  const D = data({ name: "UL1015 26AWG", art: "UL1015 26AWG" });
  expect(makeGen(D, initState(D)).twistDiaMissing_()).toEqual([]);
});

it("terminal fit still sees the section of a mark outside the base, like the crimp norms on the server", () => {
  const D = data({ name: "ПГВА 0,50", art: "ПГВА 0,50" }, {
    terRecords: [{ article: "SXH-001T-P0.6", step: "2.50", strip: "2", lPlus: "5", lMinus: "3", applicator: "S12", awgFrom: "", awgTo: "", mm2From: "0,08", mm2To: "0,2" }],
  });
  const warnings = makeGen(D, initState(D)).validate().warnings.join("\n");
  expect(warnings).toContain("«ПГВА 0,50» (0.5 мм²) вне диапазона терминала A «Контакт XH» (0.08–0.2 мм²)");
  // «4x0,35» — не сечение (многожильный кабель), как у technology.wire_section
  const multi = data({ name: "КСПВГ 4x0,35", art: "КСПВГ 4x0,35" }, { terRecords: D.terRecords });
  expect(makeGen(multi, initState(multi)).validate().warnings.join("\n")).not.toContain("вне диапазона");
});

it("a Ø kept from the old СПР.КАБ table is used but flagged «по старой таблице, не подтверждено»", () => {
  const wd = wireDiaFromRecords(ROWS)!;
  expect(wd.byAwg["20"]).toMatchObject({ мгтф: 1.35, "old:мгтф": 1, "old:мс1511": 1, пвам: 1.6 });
  expect(wd.byAwg["20"]["old:пвам"]).toBeUndefined();
  const old = twisted(data({ name: "МГТФ 0,5", art: "МГТФ 0,5" }));
  expect(old.twistDiaValue()).toBe("1,35");
  expect(old.twistDiaOld_()).toEqual(["МГТФ 0,5"]);
  expect(old.twistDiaMissing_()).toEqual([]);
  expect(old.validate().warnings).toContain(`• Свивка: «МГТФ 0,5» — ${WIRE_DIA_OLD_} (поле «Ø провода»)`);
  const sourced = twisted(data({ name: "ПВАМ 0,5", art: "ПВАМ 0,5" }));
  expect(sourced.twistDiaValue()).toBe("1,6");
  expect(sourced.twistDiaOld_()).toEqual([]);
  expect(sourced.validate().warnings.join("\n")).not.toContain(WIRE_DIA_OLD_);
  // ручной Ø снимает подсказку
  expect(twisted(data({ name: "МГТФ 0,5", art: "МГТФ 0,5" }), "1,4").twistDiaOld_()).toEqual([]);
});
