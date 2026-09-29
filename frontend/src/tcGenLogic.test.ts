import { expect, it } from "vitest";
import { initState, makeGen, updateEndpoint, type GenData } from "./tcGenLogic";

const templates = [
  { id: "cut", title: "CUT_WIRE_auto | Резка", category: "Резка" },
  { id: "prsA", title: "PRS_TERMINAL_2.50 | Опрессовка терминалов (А)", category: "Опрессовка терминалов" },
  { id: "prsB", title: "PRS_TERMINAL_2.50 | Опрессовка терминалов (В)", category: "Опрессовка терминалов" },
  { id: "insA", title: "INS_terminal_2.50 | Монтаж терминалов (А)", category: "Монтаж терминалов" },
  { id: "insB", title: "INS_terminal_2.50 | Монтаж терминалов (В)", category: "Монтаж терминалов" },
  { id: "sdrXt", title: "SDR_XT60 | Пайка XT60", category: "Пайка" },
  { id: "cutTut", title: "CUT_TUT_short | Резка ТУТ", category: "Резка" },
  { id: "insTut", title: "INS_TUT | Надеть ТУТ", category: "Монтаж" },
  { id: "heatTut", title: "HEAT_TYT_short | Усадка ТУТ", category: "Усадка" },
  { id: "coaxCut", title: "CUT_WIRE_auto | Резка коакс", category: "Резка" },
];

function data(components: GenData["components"], name = "Жгут XHP 150мм"): GenData {
  return {
    components, assemblyInfo: { "Индекс": "КБ1", "Наименование": name }, templates,
    ops: [{ opNumber: "CUT_WIRE_auto", opName: "Резка", tOp: "12", tPrep: "60", tMachine: "3" }],
    terRecords: [{ article: "SXH-001T-P0.6", step: "2.50", strip: "2", lPlus: "5", lMinus: "3", applicator: "S12", awgFrom: "", awgTo: "", mm2From: "", mm2To: "" }],
    coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [],
  };
}

const interboard = [
  { id: "w", type: "Провод", name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 0.3 },
  { id: "t", type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6", qty: 4 },
  { id: "c", type: "Соед", name: "Колодка XH 2", art: "XHP-2", qty: 2 },
];

it("builds the interboard config the /build endpoint reads (TcGenInline buildConfig)", () => {
  const D = data(interboard);
  const S = initState(D);
  expect(S.tab).toBe("inter");
  expect(S.endpoints.map((ep) => [ep.side, ep.connId, ep.termId, ep.method])).toEqual([["A", "c", "t", "crimp"], ["B", "c", "t", "crimp"]]);
  const cfg = makeGen(D, S).buildConfig() as Record<string, any>;
  expect(cfg.assemblyIndex).toBe("КБ1");
  // 150 по чертежу + L+ обеих сторон из БД.ТЕР = 160; проводов по терминалам: 4 контакта / 2 разъёма
  expect(cfg.wires).toEqual([{ name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 2, length: 160 }]);
  expect(cfg.ops.map((op: any) => [op.type, op.side ?? "", op.templateId])).toEqual([
    ["cutWire", "", "cut"], ["prsTerm", "A", "prsA"], ["insTerm", "A", "insA"], ["prsTerm", "B", "prsB"], ["insTerm", "B", "insB"],
  ]);
  expect(cfg.ops[0]).toMatchObject({ opNum: "CUT_WIRE_auto", tOp: "12", tPrep: "60", tMachine: "3" });
  expect(cfg.ops[1]).toMatchObject({ firstTerm: true, ep: { connArt: "XHP-2", termArt: "SXH-001T-P0.6" }, wireSources: [""] });
  expect(cfg.ops[3].wireSources).toEqual(["Колодка XH 2"]);
});

it("routes a solder connector through TUT cut, fit and shrink", () => {
  const D = data([
    { id: "w", type: "Провод", name: "ПВАМ 0,5", art: "ПВАМ 0,5", qty: 0.3 },
    { id: "x", type: "Соед", name: "XT60 папа", art: "XT60-M", qty: 2 },
    { id: "tut", type: "ТУТ", name: "ТУТ 4/2", art: "ТУТ 4/2", qty: 0.04 },
  ]);
  const S = initState(D);
  const ops = makeGen(D, S).getActiveOps();
  expect(ops.map((op) => op.type)).toEqual(["cutWireStrip", "cutTut", "insTut", "solderConn", "solderConn", "heatTut"]);
  expect(ops[0].strip).toEqual({ a: true, lenA: "5", b: true, lenB: "5" });
  expect(ops[1].tutSegs).toEqual([{ len: "10", count: 4 }]);
  expect(ops[3].tmplHint).toBe("SDR_XT60");
});

it("switching a connector back to crimp drops the solder template", () => {
  const D = data(interboard);
  const S = updateEndpoint(D, initState(D), "a1", { method: "solder" });
  expect(S.endpoints[0]).toMatchObject({ method: "solder", methodAuto: false });
  expect(makeGen(D, S).getActiveOps().map((op) => op.type)).toContain("solderConn");
});

it("coax kit opens the КОАКС tab and orders angled side B before the second pin", () => {
  const D = data([
    { id: "k", type: "Кабель", name: "RG316", art: "RG316", qty: 0.2 },
    { id: "a", type: "Соед", name: "SMA", art: "SMA-J", qty: 1 },
    { id: "b", type: "Соед", name: "MCX R/A", art: "MCX-JW R/A", qty: 1 },
  ], "Сборка RG316 150mm");
  const S = initState(D);
  expect(S.tab).toBe("coax");
  expect(S.coax.pinB).toBe("внутри корпуса");
  expect(S.coax.len).toBe("160");
  const cfg = makeGen(D, S).buildConfig() as Record<string, any>;
  expect(cfg.mode).toBe("coax");
  expect(cfg.ops.map((op: any) => op.type)).toEqual([
    "coaxCut", "coaxStrip", "coaxStrip", "coaxPin", "coaxInsSleeve", "coaxPin", "coaxHousing", "coaxShield",
  ]);
  expect(cfg.coax.sideB).toMatchObject({ article: "MCX-JW R/A", pinType: "внутри корпуса", type: "" });
});

it("takes coax diameters from СПР.КАБ by the BOM cable, lengths from the connector record", () => {
  const rec = { article: "SMA-J", connArticle: "SMA-J", type: "SMA", wire: "RG316", mfr: "", program: "",
    d1: "9", d2: "9", d3: "9", l1: "4", l2: "3", l3: "2", lPlus: "1", lMinus: "0,5", pinType: "", shieldType: "" };
  const D = { ...data([
    { id: "k", type: "Кабель", name: "RG316", art: "RG316", qty: 0.2 },
    { id: "a", type: "Соед", name: "SMA", art: "SMA-J", qty: 1 },
  ], "Сборка RG316 150mm"), coaxRecords: [rec], coaxCableDia: { byCable: { rg316: { d1: "0,51", d2: "1,52", d3: "2,5" } } } };
  const S = initState(D);
  const cfg = makeGen(D, S).buildConfig() as Record<string, any>;
  expect(cfg.coax.sideA).toMatchObject({ d1: "0,51", d2: "1,52", d3: "2,5", l1: "4", l2: "3", l3: "2", lPlus: "1", lMinus: "0,5" });
  expect(makeGen(D, S).coaxInfo("A").text).toContain("D1:0,51");
  // Кабеля нет в СПР.КАБ — запасной вариант: D1–D3 записи разъёма.
  const noCable = { ...D, coaxCableDia: { byCable: {} } };
  expect((makeGen(noCable, initState(noCable)).buildConfig() as Record<string, any>).coax.sideA).toMatchObject({ d1: "9", d2: "9", d3: "9" });
});

it("takes cut lengths from the calc sheet rows when the name has none", () => {
  const D = data(interboard, "Жгут XHP");
  const calcOps = [
    { code: "CUT_WIRE", comp: "МГТФ 0,12", n: "2", l: "0,16", req: "L=150мм" },
    { code: "SDR_tin", comp: "", n: "", l: "", req: "L=3" },
  ];
  const S = initState({ ...D, calcOps });
  expect(S.drawingLength).toBe(150);
  expect(S.lenFromCalc).toBe(true);
  expect((makeGen({ ...D, calcOps }, S).buildConfig() as any).wires[0].length).toBe(160);
});

it("keeps the auto-picked template after the first show (renderOpsCard selectedTemplates)", () => {
  const D = data(interboard, "Жгут XHP 150мм");
  const S = initState(D);
  const g = makeGen(D, S);
  const prs = g.getActiveOps().find((op) => op.type === "prsTerm")!;
  const first = g.templateIdFor(prs);
  const stuck = g.stickTemplates();
  expect(stuck?.[prs.opKey]).toBe(first);
  const S2 = { ...S, selectedTemplates: stuck! };
  const D2 = { ...D, templates: [{ id: "other", title: "PRS_TERMINAL_2.50 | Другой (А)", category: "Опрессовка терминалов" }, ...D.templates] };
  expect(makeGen(D2, S2).templateIdFor(prs)).toBe(first);
  expect(makeGen(D2, S2).stickTemplates()).toBeNull();
});
