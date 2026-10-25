import { describe, expect, it } from "vitest";
import { COAX_SHIELD_OPTIONS, coaxFromFields, coaxVswrCustProblem, coaxVswrCustStricter, coaxVswrCustomer_, componentsVswrMax, withCoaxVswrFromConfig, coaxShieldKind_, coaxRecordFields, savedComplex, applyGrapherGroups, applyOpOrder, grapherLengthText, emptyState, initState, makeGen, opOrderFromConfig, parsePinList_, pluralRu, terFromFields, updateEndpoint, type CoaxVswrCustForm, type GenData, type GenState, type OpSpec } from "./tcGenLogic";
import { genCards } from "./routeCards";
import { deriveRoute, type RouteOp } from "./routeLogic";

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
  // Вкладка и ключи операций — по ним сервер режет ЭТК вкладки генератора на ПФ (pf_split.py), «Сложную сборку» — тоже (16.10).
  expect(cfg.tab).toBe("inter");
  expect(cfg.ops.map((op: any) => op.opKey)).toEqual(expect.arrayContaining(["cutWire"]));
  expect(cfg.ops.every((op: any) => typeof op.opKey === "string" && op.opKey)).toBe(true);
});

it("a drawing with end allowances is cut by the drawing's cut length — our L+ is not added twice (owner 06.10)", () => {
  const D = data(interboard);
  const S = initState(D);
  // TECHMAP-GRAPHER: 300 по чертежу + поправки +8/+4 → резка 312 (вверх до шага один раз) — как cut_mm экспорта.
  const byCut = applyGrapherGroups(S, [{ compId: "w", qty: 2, length: 300, cut: 312, correctionFrom: 8, correctionTo: 4 }]);
  expect(byCut.measureMode).toBe("cut");
  expect((makeGen(D, byCut).buildConfig() as Record<string, any>).wires[0].length).toBe(312);
  // Без поправок в чертеже — как раньше: длина по чертежу + наш L+ обеих сторон из БД.ТЕР.
  const plain = applyGrapherGroups(S, [{ compId: "w", qty: 2, length: 300, cut: 300 }]);
  expect(plain.measureMode).toBe("inner");
  expect((makeGen(D, plain).buildConfig() as Record<string, any>).wires[0].length).toBe(310);
  expect(grapherLengthText({ length_mm: 300, cut_mm: 312, correction_from_mm: 8, correction_to_mm: 4 }))
    .toBe("по чертежу 300 мм · резка 312 мм (+8/+4)");
  expect(grapherLengthText({ length_mm: "150.2", cut_mm: 149, correction_from_mm: "0.4", correction_to_mm: -2 }))
    .toBe("по чертежу 150,2 мм · резка 149 мм (+0,4/−2)");
  expect(grapherLengthText({ length_mm: 90, cut_mm: 90, correction_from_mm: 0, correction_to_mm: 0 })).toBe("по чертежу 90 мм");
});

it("pairs the wire ends by the order of both pin lists: А «1-2», В «2, 1» is a crossover", () => {
  const D = data(interboard);
  const plain = initState(D);
  const w = plain.wires[0].idx;
  // Без распиновки на обоих концах — конфиг как раньше: ни соединений, ни ключей концов.
  const before = makeGen(D, updateEndpoint(D, plain, "a1", { pins: { [w]: "1-2" } })).buildConfig() as Record<string, any>;
  expect(before.connections).toBeUndefined();
  expect(before.ops.some((op: any) => "epKey" in op)).toBe(false);

  const S = updateEndpoint(D, updateEndpoint(D, plain, "a1", { pins: { [w]: "1-2" } }), "b1", { pins: { [w]: "2, 1" } });
  const gen = makeGen(D, S);
  const cfg = gen.buildConfig() as Record<string, any>;
  expect(cfg.connections).toEqual([
    { wire: "МГТФ 0,12", from: { end: "А1", conn: "XHP-2", label: "", pin: 1 }, to: { end: "В1", conn: "XHP-2", label: "", pin: 2 } },
    { wire: "МГТФ 0,12", from: { end: "А1", conn: "XHP-2", label: "", pin: 2 }, to: { end: "В1", conn: "XHP-2", label: "", pin: 1 } },
  ]);
  expect(cfg.ops.map((op: any) => [op.type, op.epKey ?? ""])).toEqual([
    ["cutWire", ""], ["prsTerm", "А1"], ["insTerm", "А1"], ["prsTerm", "В1"], ["insTerm", "В1"],
  ]);
  expect(cfg.ops[4].pins).toEqual(["2, 1"]);
  expect(gen.connections_().warnings).toEqual([]);

  // Разное число контактов — пары до короткого списка и предупреждение перед сборкой.
  const short = makeGen(D, updateEndpoint(D, S, "b1", { pins: { [w]: "2" } }));
  expect(short.connections_().list).toHaveLength(1);
  expect(short.validate().warnings).toContain("МГТФ 0,12: на А1 2 конт., на В1 1 — соединения по порядку только для 1.");
});

it("reads a pin list in the written order, ranges both ways, repeats kept for double crimp", () => {
  expect(parsePinList_("3, 1, 2, 4-6")).toEqual([3, 1, 2, 4, 5, 6]);
  expect(parsePinList_("6-4; 1")).toEqual([6, 5, 4, 1]);
  expect(parsePinList_("1 1 2 2 x 0")).toEqual([1, 1, 2, 2]);
  expect(parsePinList_("1-1000")).toHaveLength(200);
  expect(parsePinList_(undefined)).toEqual([]);
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
    "coaxCut", "coaxPin", "coaxInsSleeve", "coaxPin", "coaxHousing", "coaxShield", "coaxVswr",
  ]);
  expect(cfg.coax.sideB).toMatchObject({ article: "MCX-JW R/A", pinType: "внутри корпуса", type: "" });
  const separate = { ...S, coax: { ...S.coax, stripMode: "separate" as const } };
  expect((makeGen(D, separate).buildConfig() as Record<string, any>).ops.map((op: any) => op.type)).toEqual([
    "coaxCut", "coaxStrip", "coaxStrip", "coaxPin", "coaxInsSleeve", "coaxPin", "coaxHousing", "coaxShield", "coaxVswr",
  ]);
});

describe("коакс: контроль КСВН — каждое изделие, выборочно 1 из N или не проверять (владелец 22.10)", () => {
  const D = data([
    { id: "k", type: "Кабель", name: "RG316", art: "RG316", qty: 0.2 },
    { id: "a", type: "Соед", name: "SMA", art: "SMA-J", qty: 1 },
    { id: "b", type: "Соед", name: "SMA", art: "SMA-K", qty: 1 },
  ], "Сборка RG316 150mm");
  const cfgOf = (S: GenState) => makeGen(D, S).buildConfig() as Record<string, any>;
  const vswrOp = (S: GenState) => makeGen(D, S).getActiveOps().find((op) => op.type === "coaxVswr");
  const withMode = (patch: Partial<GenState["coax"]>) => { const S = initState(D); return { ...S, coax: { ...S.coax, ...patch } }; };

  it("a new assembly checks every unit and says so in the config", () => {
    const S = initState(D);
    expect(S.coax).toMatchObject({ vswrMode: "all", vswrEvery: "50" });
    expect(vswrOp(S)).toMatchObject({ label: "Контроль КСВН", vswrControl: { mode: "all", every: null } });
    expect(cfgOf(S).coax.vswrControl).toEqual({ mode: "all", every: null });
    expect(cfgOf(S).ops.at(-1)).toMatchObject({ type: "coaxVswr", vswrControl: { mode: "all", every: null } });
  });

  it("sample carries N into the op and the config; N below 2 falls back to 1 of 50", () => {
    const S = withMode({ vswrMode: "sample", vswrEvery: "20" });
    expect(vswrOp(S)).toMatchObject({ label: "Контроль КСВН — выборочно 1 из 20", vswrControl: { mode: "sample", every: 20 } });
    expect(cfgOf(S).coax.vswrControl).toEqual({ mode: "sample", every: 20 });
    expect(vswrOp(withMode({ vswrMode: "sample", vswrEvery: "1" }))?.vswrControl).toEqual({ mode: "sample", every: 50 });
  });

  it("«Не проверять» leaves no VSWR op", () => {
    const S = withMode({ vswrMode: "none" });
    expect(vswrOp(S)).toBeUndefined();
    expect(cfgOf(S).ops.map((op: any) => op.type)).not.toContain("coaxVswr");
    expect(cfgOf(S).coax.vswrControl).toEqual({ mode: "none", every: null });
  });

  it("a card built before the setting keeps the op as it was: no mode, no control in the config", () => {
    const S = withCoaxVswrFromConfig(initState(D), { mode: "coax", coax: { sideA: {}, sideB: {} } });
    expect(S.coax.vswrMode).toBeUndefined();
    expect(vswrOp(S)).toEqual({ type: "coaxVswr", opKey: "coaxVswr", tmplHint: "CTRL_VSWR_coax", label: "Контроль КСВН" });
    expect(cfgOf(S).coax).not.toHaveProperty("vswrControl");
    expect(cfgOf(S).ops.at(-1)).not.toHaveProperty("vswrControl");
  });

  it("a saved card reopens with its mode; a non-coax config leaves the form alone", () => {
    const S = withCoaxVswrFromConfig(initState(D), { coax: { vswrControl: { mode: "sample", every: 30 } } });
    expect(S.coax).toMatchObject({ vswrMode: "sample", vswrEvery: "30" });
    expect(withCoaxVswrFromConfig(initState(D), { coax: { vswrControl: { mode: "none", every: null } } }).coax.vswrMode).toBe("none");
    const plain = initState(D);
    expect(withCoaxVswrFromConfig(plain, { mode: "inter", ops: [] })).toBe(plain);
  });
});

describe("коакс: резка с разделкой на роторном станке или резка, затем разделка А и В (владелец 14.10)", () => {
  const coaxTemplates = [...templates,
    { id: "cc1", title: "CUT_COAX+1strip | Резка с разделкой", category: "Резка с разделкой" },
    { id: "cc2", title: "CUT_COAX+2strip | Резка с разделкой", category: "Резка с разделкой" },
    { id: "strip", title: "STRIP_COAX_auto | Разделка коаксиала", category: "Разделка" },
  ];
  const kit = (withB = true) => [
    { id: "k", type: "Кабель", name: "RG316", art: "RG316", qty: 1.02 },
    { id: "a", type: "Соед", name: "MMCX-Male RG316", art: "MMCX-Male RG316", qty: 1 },
    ...(withB ? [{ id: "b", type: "Соед", name: "N-Male RG316", art: "N-Male RG316", qty: 1 }] : []),
    { id: "t", type: "ТУТ", name: "ТУТ 3/1,5", art: "ТУТ 3/1,5", qty: 0.04 },
  ];
  const coaxData = (withB = true, d3 = "2,5"): GenData => ({
    ...data(kit(withB), "Кабельная сборка MMCX-Male to N-Male RG316 1m"), templates: coaxTemplates,
    coaxCableDia: d3 ? { byCable: { rg316: { d1: "0,51", d2: "1,52", d3 } } } : null,
  });
  const opsOf = (D: GenData, S: GenState) => makeGen(D, S).getActiveOps();

  it("by default cuts and strips both ends in one operation first, ТУТ cut after it", () => {
    const D = coaxData();
    const S = initState(D);
    expect(S.coax).toMatchObject({ stripMode: "machine", machine: "" });
    const ops = opsOf(D, S);
    expect(ops.slice(0, 2).map((op) => [op.type, op.label, op.tmplHint])).toEqual([
      ["coaxCut", "Резка с разделкой (А и В)", "CUT_COAX+2strip"], ["coaxCutTut", "Резка ТУТ ×2", "CUT_TUT_short"],
    ]);
    expect(ops.some((op) => op.type === "coaxStrip")).toBe(false);
    const cfg = makeGen(D, S).buildConfig() as Record<string, any>;
    expect(cfg.ops[0]).toMatchObject({ type: "coaxCut", opKey: "coaxCutStrip", templateId: "cc2", stripSides: ["A", "B"],
      machine: { model: "BZW-886+Q2", inventory: "ОБ.094" } });
  });

  it("picks the small machine for a cable up to Ø 5,5 mm, the big one for a thicker or unknown cable, the technologist's choice wins", () => {
    const thin = coaxData();
    expect(makeGen(thin, initState(thin)).coaxMachine_()).toBe("small");
    const thick = coaxData(true, "7,2");
    expect(makeGen(thick, initState(thick)).coaxMachine_()).toBe("big");
    expect(opsOf(thick, initState(thick))[0].machine).toBe("BZW-50X");
    const unknown = coaxData(true, "");
    expect(makeGen(unknown, initState(unknown)).coaxMachine_()).toBe("big");
    const S = initState(thin);
    const chosen = { ...S, coax: { ...S.coax, machine: "big" as const } };
    expect(makeGen(thin, chosen).coaxMachine_()).toBe("big");
    expect((makeGen(thin, chosen).buildConfig() as Record<string, any>).ops[0].machine).toMatchObject({ model: "BZW-50X", inventory: "ОБ.095" });
  });

  it("one end only takes CUT_COAX+1strip; a stripped bare end B — +2strip", () => {
    const D = coaxData(false);
    const S = initState(D);
    expect(opsOf(D, S)[0]).toMatchObject({ label: "Резка с разделкой (А)", tmplHint: "CUT_COAX+1strip", stripSides: ["A"] });
    const bare = { ...S, coax: { ...S.coax, stripB: true } };
    expect(opsOf(D, bare)[0]).toMatchObject({ label: "Резка с разделкой (А) и зачисткой (В)", tmplHint: "CUT_COAX+2strip", stripSides: ["A", "B"] });
  });

  describe("тип экрана: обжим / пайка / прижимной (владелец 19.10), по разъёму каждой стороны", () => {
    const rec = (article: string, shield: string, extra: Record<string, string> = {}) =>
      coaxFromFields({ coaxArticle: article, coaxWire: "RG316", coaxType: "N-male", "Тип экрана": shield, L1: "6", L2: "10", L3: "10", ...extra });
    const clampKit = (a: string, b: string) => [
      { id: "k", type: "Кабель", name: "RG316", art: "RG316", qty: 1.02 },
      { id: "a", type: "Соед", name: a, art: a, qty: 1 },
      ...(b ? [{ id: "b", type: "Соед", name: b, art: b, qty: 1 }] : []),
      { id: "t", type: "ТУТ", name: "ТУТ 3/1,5", art: "ТУТ 3/1,5", qty: 0.04 },
    ];
    const clampData = (shieldA: string, shieldB: string, withB = true): GenData => ({
      ...data(clampKit("N-112B", withB ? "S-111" : ""), "Кабельная сборка N-112B S-111 RG316 1m"), templates: coaxTemplates,
      coaxRecords: [rec("N-112B", shieldA), rec("S-111", shieldB, { coaxType: "SMA-male" })],
    });
    const types = (D: GenData, S: GenState) => opsOf(D, S).map((op) => `${op.type}${op.side || ""}`);

    it("normalizes the spellings of «Тип экрана»", () => {
      expect(["прижим", "Прижимной", "зажимной", "clamp", "обжимной", "Обжим втулки", "пайка", "паяный", "RG-178: пайка", "накручивание", "", undefined]
        .map((value) => coaxShieldKind_(value))).toEqual(["прижим", "прижим", "прижим", "прижим", "обжим", "обжим", "пайка", "пайка", "пайка", "накрутка", "", ""]);
    });

    it("knows compression, twist-on and Push-On (19.10): own kinds, «самообжим» is not a crimp", () => {
      expect(["компрессия", "компрессионный", "накрутка", "накручивающийся", "резьбовой", "самообжим", "Push-On (самообжим)", "обжим"]
        .map((value) => coaxShieldKind_(value))).toEqual(["компрессия", "компрессия", "накрутка", "накрутка", "накрутка", "самообжим", "самообжим", "обжим"]);
    });

    it("knows the body shield (MHF, U.FL, 19.10): «корпус» / «корпусом» / «обжим корпуса», no sleeve, no ferrule press, own housing note", () => {
      expect(["корпус", "корпусом", "обжим корпуса", "Корпусом (без гильзы)", "обжим"].map((value) => coaxShieldKind_(value)))
        .toEqual(["корпус", "корпус", "корпус", "корпус", "обжим"]);
      const body = clampData("корпус", "корпусом");
      const ops = opsOf(body, initState(body));
      expect(ops.some((op) => op.type === "coaxShield" || op.type === "coaxInsSleeve")).toBe(false);
      expect(ops.find((op) => op.type === "coaxHousing" && op.side === "A")?.label).toBe("Монтаж корпуса (А) — экран зажимается корпусом разъёма, без гильзы");
      const mixed = clampData("корпус", "обжим");
      expect(opsOf(mixed, initState(mixed)).find((op) => op.type === "coaxShield")).toMatchObject({ units: 1 });
      expect(COAX_SHIELD_OPTIONS).toContainEqual(["корпус", "Корпусом (без гильзы)"]);
    });

    it("twist-on and Push-On sides have no shield op and no sleeve; compression is a crimp op with the tool in the label and no HEX", () => {
      const twist = clampData("накрутка", "обжим");
      const ops = opsOf(twist, initState(twist));
      expect(ops.find((op) => op.type === "coaxShield")).toMatchObject({ units: 1, label: "Опрессовка втулки экрана" });
      expect(ops.find((op) => op.type === "coaxInsSleeve")).toMatchObject({ units: 1, label: "Монтаж втулки экрана" });
      expect(ops.find((op) => op.type === "coaxHousing" && op.side === "A")?.label).toBe("Монтаж корпуса (А) — накручивается на кабель, экран зажимает разъём");
      const bare = clampData("самообжим", "накрутка");
      const bareOps = opsOf(bare, initState(bare));
      expect(bareOps.some((op) => op.type === "coaxShield" || op.type === "coaxInsSleeve")).toBe(false);
      expect(bareOps.find((op) => op.type === "coaxHousing" && op.side === "A")?.label).toBe("Монтаж корпуса (А) — Push-On, без инструмента");
      const compress = clampData("компрессия", "компрессия");
      const compressOps = opsOf(compress, initState(compress));
      expect(compressOps.find((op) => op.type === "coaxShield")).toMatchObject({
        units: 2, label: "Опрессовка втулки экрана ×2 — компрессия: А, B (компрессионный инструмент, без HEX)", tmplHint: "PRS_SLEEVE_coax" });
      const cfg = makeGen(compress, initState(compress)).buildConfig() as Record<string, any>;
      expect([cfg.coax.sideA.shield, cfg.coax.sideB.shield]).toEqual(["компрессия", "компрессия"]);
    });

    it("takes the shield of each side from its БД.КОАКС connector, a manual choice overrides", () => {
      const D = clampData("прижимной", "обжим");
      const S = initState(D);
      expect([S.coax.shield, S.coax.shieldB]).toEqual(["прижим", "обжим"]);
      const cfg = makeGen(D, S).buildConfig() as Record<string, any>;
      expect([cfg.coax.sideA.shield, cfg.coax.sideB.shield]).toEqual(["прижим", "обжим"]);
      const manual = { ...S, coax: { ...S.coax, shield: "обжим" } };
      expect(opsOf(D, manual).find((op) => op.type === "coaxHousing" && op.side === "A")?.label).toBe("Монтаж корпуса (А)");
      const same = { ...S, coax: { ...S.coax, shieldB: "прижим" } };
      expect(opsOf(D, same).find((op) => op.type === "coaxHousing" && op.side === "B")?.label).toBe("Монтаж корпуса с прижимом (B)");
    });

    it("a clamp side: one «Надеть» with nut, washer, gasket and ring, the contact, one housing op — no sleeve crimp, no separate nut or braid ops", () => {
      const both = clampData("прижим", "прижим");
      const ops = opsOf(both, initState(both));
      expect(ops.filter((op) => op.type !== "coaxCut").map((op) => `${op.type}${op.side || ""}`)).toEqual([
        "coaxCutTut", "coaxPinA", "coaxPinB", "coaxInsTut", "coaxInsSleeve", "coaxHousingA", "coaxHousingB", "coaxHeat", "coaxVswr"]);
      expect(ops.find((op) => op.type === "coaxInsSleeve")).toMatchObject({
        label: "Надеть гайку, шайбу, уплотнитель и зажимное кольцо ×2", units: 2, clampSides: ["A", "B"], tmplHint: "INS_SLEEVE_coax" });
      expect(ops.filter((op) => op.type === "coaxHousing").map((op) => op.label)).toEqual([
        "Монтаж корпуса с прижимом (А)", "Монтаж корпуса с прижимом (B)"]);
      expect(ops.map((op) => op.type)).not.toEqual(expect.arrayContaining(["coaxShield"]));
      expect(ops.some((op) => /ClampNut|ClampBraid/.test(op.type))).toBe(false);
      const cfg = makeGen(both, initState(both)).buildConfig() as Record<string, any>;
      expect(cfg.ops.find((op: any) => op.type === "coaxInsSleeve")).toMatchObject({ units: 2, clampSides: ["A", "B"] });
    });

    it("mixed A clamp / B crimp: «Надеть» for both, ONE sleeve pressing for B only, solder and crimp per side", () => {
      const mixed = clampData("прижим", "обжим");
      const ops = opsOf(mixed, initState(mixed));
      expect(ops.map((op) => `${op.type}${op.side || ""}`).filter((t) => t !== "coaxCut")).toEqual([
        "coaxCutTut", "coaxPinA", "coaxPinB", "coaxInsTut", "coaxInsSleeve", "coaxHousingA", "coaxHousingB", "coaxShield", "coaxHeat", "coaxVswr"]);
      expect(ops.find((op) => op.type === "coaxInsSleeve")).toMatchObject({
        label: "Надеть втулку экрана (B) и гайку, шайбу, уплотнитель, зажимное кольцо (А)", units: 2, clampSides: ["A"] });
      expect(ops.find((op) => op.type === "coaxShield")).toMatchObject({ units: 1 });
      expect(ops.find((op) => op.type === "coaxHousing" && op.side === "B")?.label).toBe("Монтаж корпуса (B)");
      expect(ops.find((op) => op.type === "coaxHousing" && op.side === "A")?.label).toBe("Монтаж корпуса с прижимом (А)");
      // Пайка у одной стороны и обжим у другой — тоже по сторонам.
      const soldered = clampData("пайка", "обжим");
      expect(types(soldered, initState(soldered))).toEqual(expect.arrayContaining(["coaxSolderShieldA", "coaxInsSleeve", "coaxShield"]));
    });

    it("the shield type and the centre-contact type are independent: clamp + soldered pin, clamp + crimped pin (coaxPinCrimp)", () => {
      const D = clampData("прижим", "прижим");
      const S = initState(D);
      const order = (pin: string) => types(D, { ...S, coax: { ...S.coax, pinA: pin, pinB: pin } }).filter((t) => /Pin|InsSleeve|Housing|Shield/.test(t));
      expect(order("пайка")).toEqual(["coaxPinA", "coaxPinB", "coaxInsSleeve", "coaxHousingA", "coaxHousingB"]);
      expect(order("обжим")).toEqual(["coaxPinCrimpA", "coaxPinCrimpB", "coaxInsSleeve", "coaxHousingA", "coaxHousingB"]);
    });

    it("the clamp housing card is the ERP template once the library has it, the nearest library card before", () => {
      const D = clampData("прижим", "прижим");
      const clampHousings = (G: GenData) => opsOf(G, initState(G)).filter((op) => op.type === "coaxHousing");
      expect(clampHousings(D).map((op) => op.tmplHint)).toEqual(["INS_housing_COAX", "INS_housing_COAX"]);
      const withCards: GenData = { ...D, templates: [...coaxTemplates,
        { id: "ha", title: "INS_housing_COAX_clamp | Монтаж корпуса с прижимом (A)", category: "Монтаж корпуса" },
        { id: "hb", title: "INS_housing_COAX_clamp | Монтаж корпуса с прижимом (B)", category: "Монтаж корпуса" }] };
      expect(clampHousings(withCards).map((op) => [op.tmplHint, op.tmplWant])).toEqual([
        ["INS_housing_COAX_clamp", "INS_housing_COAX_clamp"], ["INS_housing_COAX_clamp", "INS_housing_COAX_clamp"]]);
    });
  });

  it("separately: cut, strip A, strip B, then the ТУТ cut and everything else", () => {
    const D = coaxData();
    const S = initState(D);
    const ops = opsOf(D, { ...S, coax: { ...S.coax, stripMode: "separate" } });
    expect(ops.slice(0, 4).map((op) => op.label)).toEqual(["Резка кабеля", "Разделка коаксиала (А)", "Разделка коаксиала (B)", "Резка ТУТ ×2"]);
    expect(ops.slice(0, 3).map((op) => op.tmplHint)).toEqual(["CUT_WIRE_auto", "STRIP_COAX_auto", "STRIP_COAX_auto"]);
    expect(ops.some((op) => op.machine)).toBe(false);
  });

  it("a crimped centre contact («Тип пина» = обжим in БД.КОАКС) is pressed by its own operation, not soldered (19.10)", () => {
    const rec = coaxFromFields({ coaxArticle: "SMA-JW3", coaxWire: "RG-316", coaxType: "SMA-male R/A", "Тип пина": "обжим", "Тип экрана": "обжим",
      L1: "2,6", L2: "5,5", L3: "12" });
    const kit = [
      { id: "k", type: "Кабель", name: "RG-316", art: "RG-316", qty: 0.3 },
      { id: "a", type: "Соед", name: "SMA-JW3", art: "SMA-JW3", qty: 1 },
      { id: "b", type: "Соед", name: "N-Male RG316", art: "N-Male RG316", qty: 1 },
    ];
    const D: GenData = { ...data(kit, "Кабельная сборка SMA-JW3 RG-316 0,3m"), templates: coaxTemplates, coaxRecords: [rec] };
    const S = initState(D);
    expect(S.coax.pinA).toBe("обжим");
    const ops = opsOf(D, S);
    const crimp = ops.filter((op) => op.type === "coaxPinCrimp");
    expect(crimp.map((op) => [op.side, op.label])).toEqual([["A", "Опрессовка центрального контакта (А)"]]);
    // Своей карты PRS_PIN_coax ещё нет — карта опрессовки коакса, с пометкой, какой карты не хватает.
    expect(crimp[0]).toMatchObject({ tmplWant: "PRS_PIN_coax" });
    expect(ops.filter((op) => op.type === "coaxPin").map((op) => op.side)).toEqual(["B"]);
    expect((makeGen(D, S).buildConfig() as Record<string, any>).coax.sideA.pinType).toBe("обжим");
  });

  it("the 2.92 mm and SMP stock articles are matched by article, dots and hyphens ignored (19.10); «Тип пина» цанга → no pin solder (20.10)", () => {
    // Владелец 20.10: у 2,92 / 3,5 мм пин не паяется — «цанга»; у углового пин только паяется внутри корпуса.
    const rec = coaxFromFields({ coaxArticle: "2.92-JB2G-F", coaxWire: "RG-405", coaxType: "2.92mm-male", "Тип пина": "цанга", "Тип экрана": "обжим" });
    const smp = coaxFromFields({ coaxArticle: "SMP-KW2", coaxWire: "RG-178", coaxType: "SMP-female R/A", "Тип пина": "пайка внутри корпуса", "Тип экрана": "обжим" });
    const kit = [
      { id: "k", type: "Кабель", name: "RG-405", art: "RG-405", qty: 0.3 },
      { id: "a", type: "Соед", name: "2.92 JB2G F", art: "2.92 JB2G F", qty: 1 },
      { id: "b", type: "Соед", name: "SMP-KW2", art: "SMP-KW2", qty: 1 },
    ];
    const D: GenData = { ...data(kit, "Кабельная сборка 2.92-JB2G-F RG-405 0,3m"), templates: coaxTemplates, coaxRecords: [rec, smp] };
    const S = initState(D);
    expect([S.coax.pinA, S.coax.pinB]).toEqual(["цанга", "внутри корпуса"]);
    expect(opsOf(D, S).filter((op) => op.type === "coaxPin" || op.type === "coaxPinCrimp").map((op) => op.side)).toEqual(["B"]);
  });

  it("a collet centre contact (N-111C/8D × 8D-FB) is not soldered: no pin solder, the housing says why", () => {
    // gsm-repiteri.ru 3300: «центральный контакт - цанговый и не требует пайки»; «Тип пина» в БД.КОАКС пуст.
    const rec = coaxFromFields({ coaxArticle: "N-111C/8D", coaxWire: "8D-FB", coaxType: "N-male", "Тип экрана": "обжим",
      "Центральный контакт": "цанговый, не требует пайки", L1: "9", L2: "3", L3: "6" });
    expect(rec.centerContact).toBe("цанговый, не требует пайки");
    const collet = [
      { id: "k", type: "Кабель", name: "8D-FB", art: "8D-FB", qty: 1.02 },
      { id: "a", type: "Соед", name: "N-111C/8D", art: "N-111C/8D", qty: 1 },
      { id: "b", type: "Соед", name: "N-Male RG316", art: "N-Male RG316", qty: 1 },
      { id: "t", type: "ТУТ", name: "ТУТ 3/1,5", art: "ТУТ 3/1,5", qty: 0.04 },
    ];
    const D: GenData = { ...data(collet, "Кабельная сборка N-111C/8D 8D-FB 1m"), templates: coaxTemplates, coaxRecords: [rec] };
    const S = initState(D);
    expect([S.coax.pinA, S.coax.pinB]).toEqual(["цанга", "пайка"]);
    const ops = opsOf(D, S);
    expect(ops.filter((op) => op.type === "coaxPin").map((op) => op.side)).toEqual(["B"]);
    expect(ops.filter((op) => op.type === "coaxHousing").map((op) => op.label)).toEqual([
      "Монтаж корпуса (А) — цанговый контакт, без пайки", "Монтаж корпуса (B)"]);
    expect((makeGen(D, S).buildConfig() as Record<string, any>).coax.sideA.pinType).toBe("цанга");
  });

  it("separately: the cut takes the coax card CUT_COAX_auto once both its card and its БД.ОП row exist", () => {
    const card = { id: "ccut", title: "CUT_COAX_auto | Резка", category: "Резка" };
    const row = { opNumber: "CUT_COAX_auto", opName: "Резка", tOp: "3,5", tPrep: "180", tMachine: "3,5" };
    const S0 = initState(coaxData());
    const S = { ...S0, coax: { ...S0.coax, stripMode: "separate" as const } };
    const full: GenData = { ...coaxData(), templates: [...coaxTemplates, card], ops: [...coaxData().ops, row] };
    expect(opsOf(full, S)[0]).toMatchObject({ tmplHint: "CUT_COAX_auto", tmplWant: "CUT_COAX_auto" });
    expect((makeGen(full, S).buildConfig() as Record<string, any>).ops[0]).toMatchObject({ templateId: "ccut", opNum: "CUT_COAX_auto", tOp: "3.5" });
    // Карта без строки БД.ОП (или строка без карты) — прежняя карта провода, чтобы у резки остались нормы.
    const cardOnly: GenData = { ...coaxData(), templates: [...coaxTemplates, card] };
    expect(opsOf(cardOnly, S)[0]).toMatchObject({ tmplHint: "CUT_WIRE_auto", tmplWant: "CUT_COAX_auto" });
    const rowOnly: GenData = { ...coaxData(), ops: [...coaxData().ops, row] };
    expect(opsOf(rowOnly, S)[0].tmplHint).toBe("CUT_WIRE_auto");
  });

  it("796К.2 КБ1: the ПФ scheme follows the order of work, the tubes go where «Монтаж ТУТ» takes them", () => {
    // MMCX-Male RG316 → N-Male, ТУТ, втулка экрана (обжим), пайка А и В, корпуса А и В, опрессовка, усадка.
    const D = coaxData();
    const S = initState(D);
    const specs = opsOf(D, { ...S, coax: { ...S.coax, stripMode: "separate" } });
    const ops: RouteOp[] = specs.map((spec, index) => ({ position: index + 1, key: spec.opKey, type: spec.type, code: spec.tmplHint || "",
      label: spec.label, minutes: null, ...(spec.side ? { side: spec.side } : {}) }));
    expect(ops.map((op) => op.key)).toEqual(["coaxCut", "coaxStripA", "coaxStripB", "coaxCutTut", "coaxPinA", "coaxPinB", "coaxInsTut",
      "coaxInsSleeve", "coaxHousingA", "coaxHousingB", "coaxShield", "coaxHeat", "coaxVswr"]);
    const { cards, links } = genCards(deriveRoute(ops), ops);
    expect(cards.map((card) => [card.title, card.ops.map((op) => op.position)])).toEqual([
      ["Нарезанный и разделанный кабель", [1, 2, 3]], ["Нарезанные трубки", [4]],
      ["Кабель с центральным контактом, сторона А", [5]], ["Кабель с центральным контактом, сторона В", [6]],
      ["Сторона А собрана", [7, 8, 9]], ["Сторона В собрана", [10]], ["Готовый кабель", [11, 12, 13]],
    ]);
    // Каждый ПФ идёт после предыдущего по номерам операций — номера ПФ совпадают с порядком работы.
    const firsts = cards.map((card) => card.ops[0].position);
    expect(firsts).toEqual([...firsts].sort((a, b) => a - b));
    expect(links.map((link) => `${link.from}>${link.to}`)).toEqual(["wires>crimpedA", "tubes>A", "crimpedA>crimpedB", "crimpedB>A", "A>B", "B>final"]);
  });

  it("«Контроль КСВН» closes an assembly with connectors on both ends, a bare end B has none (владелец 22.10)", () => {
    const both = coaxData();
    const ops = opsOf(both, initState(both));
    expect(ops[ops.length - 1]).toMatchObject({ type: "coaxVswr", opKey: "coaxVswr", label: "Контроль КСВН", tmplHint: "CTRL_VSWR_coax" });
    expect(ops.filter((op) => op.type === "coaxVswr")).toHaveLength(1);
    const cfg = makeGen(both, initState(both)).buildConfig() as Record<string, any>;
    expect(cfg.ops[cfg.ops.length - 1]).toMatchObject({ type: "coaxVswr", opKey: "coaxVswr" });
    const bare = coaxData(false);
    const S = initState(bare);
    expect(opsOf(bare, S).some((op) => op.type === "coaxVswr")).toBe(false);
    expect(opsOf(bare, { ...S, coax: { ...S.coax, stripB: true } }).some((op) => op.type === "coaxVswr")).toBe(false);
  });
});

it("takes coax diameters from СПР.КАБ by the BOM cable, lengths from the connector record", () => {
  const rec = { article: "SMA-J", connArticle: "SMA-J", type: "SMA", wire: "RG316", mfr: "", program: "",
    l1: "4", l2: "3", l3: "2", lPlus: "1", lMinus: "0,5", pinType: "", shieldType: "" };
  const D = { ...data([
    { id: "k", type: "Кабель", name: "RG316", art: "RG316", qty: 0.2 },
    { id: "a", type: "Соед", name: "SMA", art: "SMA-J", qty: 1 },
  ], "Сборка RG316 150mm"), coaxRecords: [rec], coaxCableDia: { byCable: { rg316: { d1: "0,51", d2: "1,52", d3: "2,5" } } } };
  const S = initState(D);
  const cfg = makeGen(D, S).buildConfig() as Record<string, any>;
  expect(cfg.coax.sideA).toMatchObject({ d1: "0,51", d2: "1,52", d3: "2,5", l1: "4", l2: "3", l3: "2", lPlus: "1", lMinus: "0,5" });
  expect(makeGen(D, S).coaxInfo("A").text).toContain("D1:0,51");
  // Кабеля нет в СПР.КАБ — D нет (владелец 16.10: D у разъёма не берём, даже если в строке листа остались D1–D3).
  const legacyRow = coaxFromFields({ Артикул: "SMA-J", Провод: "RG316", D1: "9", D2: "9", D3: "9", L1: "4", L2: "3", L3: "2" });
  expect(legacyRow).not.toHaveProperty("d1");
  const noCable = { ...D, coaxRecords: [{ ...rec, ...legacyRow, connArticle: "SMA-J" }], coaxCableDia: { byCable: {} } };
  const g = makeGen(noCable, initState(noCable));
  expect((g.buildConfig() as Record<string, any>).coax.sideA).toMatchObject({ d1: "", d2: "", d3: "", l1: "4", l2: "3", l3: "2" });
  expect(g.coaxInfo("A").text).toContain("D1:— D2:— D3:— L1:4");
});

it("writes no D1–D3 into a new БД.КОАКС record: the diameters are the cable's", () => {
  const fields = coaxRecordFields({ connArticle: "SMA-J", type: "SMA", wire: "RG316", mfr: "", program: "",
    l1: "4", l2: "3", l3: "2", lPlus: "", lMinus: "", pinType: "пайка", shieldType: "обжим" });
  expect(Object.keys(fields).filter((key) => /^d[123]$/i.test(key))).toEqual([]);
  expect(fields).toMatchObject({ coaxArticle: "SMA-J", coaxWire: "RG316", l1: "4" });
});

it("finds the БД.КОАКС record whatever the hyphens and case: «MMCX-Male RG316» = «MMCX-male RG-316» (owner 14.10)", () => {
  const rec = { article: "MMCX-male RG-316", connArticle: "", type: "MMCX-male", wire: "RG-316", mfr: "", program: "",
    l1: "1,8", l2: "4,5", l3: "10", lPlus: "", lMinus: "", pinType: "пайка", shieldType: "обжим" };
  const D = { ...data([
    { id: "k", type: "Кабель", name: "RG316", art: "RG316", qty: 0.2 },
    { id: "a", type: "Соед", name: "MMCX-Male RG316", art: "MMCX-Male RG316", qty: 1 },
  ], "Сборка RG316 150mm"), coaxRecords: [rec] };
  const g = makeGen(D, initState(D));
  const info = g.coaxInfo("A");
  expect(info.missing).toBe(false);
  expect(info.text).toContain("L1:1,8");
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

// КБ1 «SHR-02V-S-B to Open-End»: 2 провода на разъём стороны А, стороны В нет, свободные концы лудятся.
const openEnd = [
  { id: "w", type: "Провод", name: "Силиконовый 28AWG черный", art: "Сил 28AWG", qty: 0.2 },
  { id: "t", type: "Терминал", name: "Контакт SH", art: "SSH-003T-P0.2-H", qty: 2 },
  { id: "c", type: "Соед", name: "Колодка SH 2", art: "SHR-02V-S-B", qty: 1 },
];
const tinTemplates = [
  { id: "cwc", title: "CUT_WIRE+1crimp | Автомат резка + опрессовка", category: "Автомат резка + опрессовка" },
  { id: "cwct", title: "CUT_WIRE+1crimp+tin | Автомат резка + опрессовка + лужение", category: "Автомат резка + опрессовка" },
  { id: "tin", title: "SDR_tin | Лужение", category: "Лужение" },
];

function openEndGen(opts: { applicator?: string; qty?: string; withTinTemplate?: boolean } = {}) {
  const base = data(openEnd, "Кабельная сборка SHR-02V-S-B to Open-End 28AWG 100mm BUZZ");
  const D: GenData = {
    ...base,
    assemblyInfo: { ...base.assemblyInfo, "Кол-во": opts.qty ?? "50" },
    templates: [...templates, ...tinTemplates.filter((t) => opts.withTinTemplate !== false || t.id !== "cwct")],
    ops: [...base.ops, { opNumber: "CUT_WIRE+1crimp+tin", opName: "Автомат резка + опрессовка + лужение", tOp: "4", tPrep: "3600", tMachine: "4" }],
    terRecords: [{ article: "SSH-003T-P0.2-H", step: "1.00", strip: "1,2", lPlus: "4", lMinus: "", applicator: opts.applicator ?? "S9/M1/A19", awgFrom: "32", awgTo: "28", mm2From: "", mm2To: "" }],
  };
  const S0 = initState(D);
  const S = { ...S0, tin: { enabled: true, wires: null, stripLen: "3" } };
  return { D, S, g: makeGen(D, S) };
}

it("Open-End with tinned free ends: one cut + crimp + tin machine operation, no separate crimp or tinning", () => {
  const { g } = openEndGen();
  expect(g.endpointsOnSide_("B")).toEqual([]);
  expect(g.batchWires_()).toBe(100);
  expect(g.crimpCutMinWires_()).toBe(90);
  const ops = g.getActiveOps();
  expect(ops.map((op) => op.type)).toEqual(["cutWireCrimp", "insTerm"]);
  expect(ops[0]).toMatchObject({ side: "A", tin: true, tmplHint: "CUT_WIRE+1crimp+tin", strip: { a: true, lenA: "1,2", b: true, lenB: "3" } });
  expect(ops[0].label).toBe("Резка с обжимом А (SSH-003T-P0.2-H) и лужением — Силиконовый 28AWG черный");
  expect(g.templateIdFor(ops[0])).toBe("cwct");
  const cfg = g.buildConfig() as Record<string, any>;
  expect(cfg.ops[0]).toMatchObject({ type: "cutWireCrimp", opNum: "CUT_WIRE+1crimp+tin", tPrep: "3600", tOp: "4", templateId: "cwct", tin: true });
  expect(cfg.tin).toMatchObject({ enabled: true, stripLen: "3" });
  expect(g.crimpTinNoApplicator_()).toBe("");
});

it("Open-End: the tinned end keeps the tin strip length when «Зачистка свободных концов» is on without a length (719К.2 КБ1)", () => {
  const { D, S } = openEndGen();
  const strip = (state: typeof S) => makeGen(D, state).getActiveOps()[0].strip;
  expect(strip({ ...S, freeStrip: { enabled: true, len: "" } })).toEqual({ a: true, lenA: "1,2", b: true, lenB: "3" });
  // Длины под лужение нет — берётся длина свободной зачистки; нет обеих — «?» на ЭТК.
  const noTinLen = { ...S, tin: { ...S.tin, stripLen: "" } };
  expect(strip({ ...noTinLen, freeStrip: { enabled: true, len: "4" } })).toMatchObject({ b: true, lenB: "4" });
  expect(strip(noTinLen)).toMatchObject({ b: true, lenB: "" });
});

it("keeps crimp and tinning separate when the machine cannot take them", () => {
  // У терминала в БД.ТЕР нет аппликатора автомата — генератор называет его.
  const manual = openEndGen({ applicator: "S9/M1" });
  expect(manual.g.getActiveOps().map((op) => op.type)).toEqual(["cutWireStrip", "prsTerm", "insTerm", "tin"]);
  expect(manual.g.crimpTinNoApplicator_()).toBe("SSH-003T-P0.2-H");
  // 80 проводов в партии — не больше 90: переналадка станка не окупается.
  expect(openEndGen({ qty: "40" }).g.getActiveOps().map((op) => op.type)).toEqual(["cutWireStrip", "prsTerm", "insTerm", "tin"]);
  // Нет карты CUT_WIRE+1crimp+tin в библиотеке — прежний порог 300 и прежние операции.
  const noCard = openEndGen({ withTinTemplate: false });
  expect(noCard.g.crimpCutMinWires_()).toBe(300);
  expect(noCard.g.getActiveOps().map((op) => op.type)).toEqual(["cutWireStrip", "prsTerm", "insTerm", "tin"]);
  // Обжим при резке включён вручную, но карты с лужением нет — лужение остаётся отдельной операцией.
  const forced = makeGen(noCard.D, { ...noCard.S, crimpCutManual: true, crimpCut: true }).getActiveOps();
  expect(forced.map((op) => [op.type, op.tmplHint ?? ""])).toEqual([["cutWireCrimp", "CUT_WIRE+1crimp"], ["insTerm", expect.any(String)], ["tin", expect.any(String)]]);
});

// Кабель USB «Сибирь» ТЦСМ.685621.047: НВ4 0,2, терминал SXH-001T-P0.6N с аппликатором автомата A2 на обеих сторонах,
// 3 провода (6 контактов на 2 разъёма) × 200 изделий = 600 в партии — обжим при резке включён по умолчанию (> 300).
const usbComps = [
  { id: "w", type: "Провод", name: "Провод НВ4 0,2", art: "НВ4 0,2", qty: 0.3 },
  { id: "t", type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6N", qty: 4 },
  { id: "t2", type: "Терминал", name: "Контакт PH", art: "SPH-002T-P0.5S", qty: 2 },
  { id: "c", type: "Соед", name: "Розетка XH", art: "I-DS1071-SCB02X5", qty: 2 },
];
const crimpTemplates = [
  { id: "cwc", title: "CUT_WIRE+1crimp | Автомат резка + опрессовка", category: "Автомат резка + опрессовка" },
  { id: "cw2c", title: "CUT_WIRE+2crimp | Автомат резка + опрессовка", category: "Автомат резка + опрессовка" },
];
const ter = (article: string, applicator: string, strip: string) =>
  ({ article, step: "2.50", strip, lPlus: "5", lMinus: "", applicator, awgFrom: "", awgTo: "", mm2From: "", mm2To: "" });

function usbGen(opts: { both?: boolean; side?: string; twoCard?: boolean; patch?: (S: GenState, D: GenData) => GenState } = {}) {
  const base = data(usbComps, "Кабель USB «Сибирь» ТЦСМ.685621.047 150мм");
  const D: GenData = {
    ...base,
    assemblyInfo: { ...base.assemblyInfo, "Кол-во": "200" },
    templates: [...templates, ...crimpTemplates.filter((t) => opts.twoCard !== false || t.id !== "cw2c")],
    ops: [...base.ops,
      { opNumber: "CUT_WIRE+1crimp", opName: "Автомат резка + опрессовка", tOp: "4,00", tPrep: "3600", tMachine: "4,00" },
      { opNumber: "CUT_WIRE+2crimp", opName: "Автомат резка + опрессовка", tOp: "4,80", tPrep: "3600", tMachine: "4,80" }],
    terRecords: [ter("SXH-001T-P0.6N", "S5/M5/A2", "2"), ter("SPH-002T-P0.5S", "S7/A7", "1,8")],
  };
  let S: GenState = initState(D);
  S = updateEndpoint(D, updateEndpoint(D, S, "a1", { termId: "t" }), "b1", { termId: "t" });
  if (opts.patch) S = opts.patch(S, D);
  S = { ...S, crimpCutSide: opts.both ? "AB" : opts.side ?? "" };
  return { D, S, g: makeGen(D, S) };
}
const kinds = (ops: Array<{ type: string; side?: string }>) => ops.map((op) => `${op.type}${op.side ? ":" + op.side : ""}`);

it("«Обе стороны»: one machine op crimps А and В, no manual crimping, the mountings follow the cut", () => {
  const { g } = usbGen({ both: true });
  expect(g.batchWires_()).toBe(600);
  expect(g.crimpCutSides_()).toEqual(["A", "B"]);
  expect(g.crimpCutSideSelected_()).toBe("AB");
  const ops = g.getActiveOps();
  expect(kinds(ops)).toEqual(["cutWireCrimp:A", "insTerm:A", "insTerm:B"]);
  expect(ops[0].label).toBe("Резка с обжимом А и В (SXH-001T-P0.6N) — Провод НВ4 0,2");
  expect(ops[0]).toMatchObject({ tmplHint: "CUT_WIRE+2crimp", strip: { a: true, lenA: "2", b: true, lenB: "2" } });
  const cfg = g.buildConfig() as Record<string, any>;
  expect(cfg.ops[0]).toMatchObject({
    type: "cutWireCrimp", side: "A", templateId: "cw2c", opNum: "CUT_WIRE+2crimp", tPrep: "3600", tOp: "4.80",
    ep: { termArt: "SXH-001T-P0.6N", connArt: "I-DS1071-SCB02X5" }, epB: { termArt: "SXH-001T-P0.6N", connArt: "I-DS1071-SCB02X5" },
  });
  // Терминалы — только в резке (обе стороны), ручной опрессовки в конфигурации нет.
  expect(cfg.ops.filter((op: any) => op.type === "prsTerm")).toEqual([]);
});

it("«Обе стороны» with different terminals on А and В names both; without a CUT_WIRE+2crimp card — the one-side card", () => {
  const patch = (S: GenState, D: GenData) => updateEndpoint(D, S, "b1", { termId: "t2" });
  const { g } = usbGen({ both: true, patch });
  const [cut] = g.getActiveOps();
  expect(cut.label).toBe("Резка с обжимом А и В (SXH-001T-P0.6N / SPH-002T-P0.5S) — Провод НВ4 0,2");
  expect(cut.strip).toEqual({ a: true, lenA: "2", b: true, lenB: "1,8" });
  expect((g.buildConfig() as Record<string, any>).ops[0].epB).toMatchObject({ termArt: "SPH-002T-P0.5S" });
  const noCard = usbGen({ both: true, twoCard: false });
  expect(noCard.g.getActiveOps()[0].tmplHint).toBe("CUT_WIRE+1crimp");
  expect((noCard.g.buildConfig() as Record<string, any>).ops[0]).toMatchObject({ templateId: "cwc", opNum: "CUT_WIRE+1crimp" });
});

it("no choice yet: both sides crimpable on the machine → «Обе стороны» by default", () => {
  const { g } = usbGen({ side: "" });
  expect(g.crimpCutSideSelected_()).toBe("AB");
  expect(kinds(g.getActiveOps())).toEqual(["cutWireCrimp:A", "insTerm:A", "insTerm:B"]);
});

it("«Сторона А» / «Сторона В» keep the one-side machine crimp and crimp the other side by hand", () => {
  for (const side of ["A"]) {
    const { g } = usbGen({ side });
    expect(g.crimpCutSideSelected_()).toBe("A");
    const ops = g.getActiveOps();
    expect(kinds(ops)).toEqual(["cutWireCrimp:A", "insTerm:A", "prsTerm:B", "insTerm:B"]);
    // Без ОТП вторую сторону зачищает пресс ручной опрессовки — резка её не трогает.
    expect(ops[0]).toMatchObject({ label: "Резка с обжимом А (SXH-001T-P0.6N) — Провод НВ4 0,2", tmplHint: "CUT_WIRE+1crimp" });
    expect(ops[0].strip).toMatchObject({ a: true, b: false });
    const cfg = g.buildConfig() as Record<string, any>;
    expect(cfg.ops[0]).toMatchObject({ side: "A", templateId: "cwc", opNum: "CUT_WIRE+1crimp" });
    expect(cfg.ops.some((op: any) => "epB" in op)).toBe(false);
  }
  const b = usbGen({ side: "B" }).g;
  expect(kinds(b.getActiveOps())).toEqual(["cutWireCrimp:B", "prsTerm:A", "insTerm:A", "insTerm:B"]);
  expect(b.getActiveOps()[0].label).toBe("Резка с обжимом В (SXH-001T-P0.6N) — Провод НВ4 0,2");
});

it("обжим на станке с одной стороны и «Без зачистки (ОТП)» с другой — резка с обжимом и зачисткой (владелец 14.10)", () => {
  const { g } = usbGen({ side: "A", patch: (S, D) => updateEndpoint(D, S, "b1", { otp: true }) });
  const ops = g.getActiveOps();
  expect(kinds(ops)).toEqual(["cutWireCrimp:A", "insTerm:A", "prsTerm:B", "insTerm:B"]);
  expect(ops[0].label).toBe("Резка с обжимом А (SXH-001T-P0.6N) и зачисткой В — Провод НВ4 0,2");
  expect(ops[0].strip).toMatchObject({ a: true, b: true, lenB: "2" });
  // ОТП без станка: резка с разделкой той стороны.
  const plain = usbGen({ side: "", patch: (S, D) => updateEndpoint(D, S, "b1", { otp: true }) });
  const manual = makeGen(plain.D, { ...plain.S, crimpCutManual: true, crimpCut: false });
  expect(manual.getActiveOps()[0]).toMatchObject({ type: "cutWireStrip", strip: { b: true, lenB: "2" } });
});

it("a side the machine cannot crimp stays manual and says why; «Обе стороны» then falls back to the other side", () => {
  // Сторона В без терминала: «Обе стороны» недоступна, сохранённый выбор "AB" — обжим стороны А, как раньше.
  const bare = usbGen({ both: true, patch: (S, D) => updateEndpoint(D, S, "b1", { termId: "" }) }).g;
  expect(bare.crimpCutSides_()).toEqual(["A"]);
  expect(bare.crimpCutSideSelected_()).toBe("A");
  expect(bare.crimpCutSideNote_("B")).toBe("сторона В: у 3 проводов не выбран терминал");
  expect(kinds(bare.getActiveOps())).toEqual(["cutWireCrimp:A", "insTerm:A", "insTerm:B"]);
  // У терминала стороны В нет аппликатора автомата — опрессовка В вручную.
  const noApplicator = usbGen({ both: true, patch: (S, D) => updateEndpoint(D, { ...S }, "b1", { termId: "t2" }) });
  const D2 = { ...noApplicator.D, terRecords: [ter("SXH-001T-P0.6N", "S5/M5/A2", "2"), ter("SPH-002T-P0.5S", "S7", "1,8")] };
  const g2 = makeGen(D2, noApplicator.S);
  expect(g2.crimpCutSideNote_("B")).toBe("сторона В: у SPH-002T-P0.5S нет аппликатора автомата (A…) — обжим вручную");
  expect(kinds(g2.getActiveOps())).toEqual(["cutWireCrimp:A", "insTerm:A", "prsTerm:B", "insTerm:B"]);
  expect(g2.crimpCutSideNote_("A")).toBe("");
});

it("names the cut of several cables with the right Russian plural: «4 кабеля», «5 кабелей»", () => {
  const four = (S: GenState) => ({ ...S, wires: [0, 1, 2, 3].map((idx) => ({ idx, compId: "w", length: 0, qty: 1 })), nextWireIdx: 4 });
  const [cut] = usbGen({ both: true, patch: four }).g.getActiveOps();
  expect(cut.label).toBe("Резка с обжимом А и В (SXH-001T-P0.6N) — 4 кабеля");
  expect([1, 2, 4, 5, 11, 21, 22, 25].map((n) => `${n} ${pluralRu(n, "кабель", "кабеля", "кабелей")}`)).toEqual([
    "1 кабель", "2 кабеля", "4 кабеля", "5 кабелей", "11 кабелей", "21 кабель", "22 кабеля", "25 кабелей",
  ]);
});

it("the both-sides cut asks for CUT_WIRE+2crimp even when it falls back to the one-side card", () => {
  expect(usbGen({ both: true, twoCard: false }).g.getActiveOps()[0]).toMatchObject({ tmplHint: "CUT_WIRE+1crimp", tmplWant: "CUT_WIRE+2crimp" });
});

it("a technologist's operation order (opOrder) reorders the ops and the config, keeps templates and goes into build_config", () => {
  const plain = usbGen({ both: true });
  expect(kinds(plain.g.getActiveOps())).toEqual(["cutWireCrimp:A", "insTerm:A", "insTerm:B"]);
  const order = ["cutWire", "insTermB1", "insTermA1"];
  const g = makeGen(plain.D, { ...plain.S, opOrder: order });
  expect(kinds(g.getActiveOps())).toEqual(["cutWireCrimp:A", "insTerm:B", "insTerm:A"]);
  const cfg = g.buildConfig() as Record<string, any>;
  expect(cfg.ops.map((op: any) => [op.type, op.side, op.templateId])).toEqual([["cutWireCrimp", "A", "cw2c"], ["insTerm", "B", "insB"], ["insTerm", "A", "insA"]]);
  expect(cfg.opOrder).toEqual(order);
  expect(plain.g.buildConfig()).not.toHaveProperty("opOrder");
  expect(opOrderFromConfig(cfg)).toEqual(order);
  expect(opOrderFromConfig({ opOrder: "x" })).toEqual([]);
  expect(opOrderFromConfig(null)).toEqual([]);
});

it("applyOpOrder keeps new operations where the generator put them and moves «first crimp» to the first one", () => {
  const op = (opKey: string, extra: Partial<OpSpec> = {}): OpSpec => ({ type: "x", opKey, label: opKey, ...extra });
  const ops = [op("cut"), op("prsA", { firstTerm: true }), op("new"), op("prsB", { firstTerm: false })];
  const sorted = applyOpOrder(ops, ["cut", "prsB", "prsA", "gone"]);
  expect(sorted.map((item) => item.opKey)).toEqual(["cut", "prsB", "new", "prsA"]);
  expect(sorted.map((item) => item.firstTerm)).toEqual([undefined, true, undefined, false]);
  expect(applyOpOrder(ops, [])).toBe(ops);
});

it("finds a terminal by its BAG article too, spelled any way (protocol v5.2, owner 12.10)", () => {
  const card = terFromFields("t1", { terArticle: "39000038", terArticleBag: "39000039", terStep: "4.20", terApplicator: "M5" });
  const legacy = terFromFields("t2", { terArticle: "5556-T", "Артикул контакта (BAG)": "5556-TL ", terStep: "4.20" });
  const gen = makeGen({ ...data(interboard), terRecords: [card, legacy] }, initState(data(interboard)));
  expect(gen.findTerRecord_("39000039")?.id).toBe("t1");
  expect(gen.findTerRecord_("Molex 39000039")?.id).toBe("t1");
  expect(gen.findTerRecord_("39000038")?.id).toBe("t1");
  expect(gen.findTerRecord_("5556 tl")?.id).toBe("t2"); // a row stored before terArticleBag: the sheet column
  expect(legacy.articleBag).toBe("5556-TL"); // the stored spelling is kept
  expect(gen.findTerRecord_("39000040")).toBeNull();
});

it("зачистка под лужение по умолчанию — 5 мм (владелец 12.10.2026)", () => {
  expect(emptyState().tin.stripLen).toBe("5");
});

const TTK = { id: "ttk", type: "ТУТ", name: "ТТК 6/2мм, чёрная", art: "ТТК 6/2", qty: 0.05 };
const shrinkTemplates = [...templates, { id: "heatTtk", title: "HEAT_TTK_short | Усадка ТТК", category: "Усадка" }];

it("heat-shrink over a connector: cut with other tubes, put on before this end's crimp, shrink after its mount", () => {
  const D = { ...data([...interboard, TTK]), templates: shrinkTemplates };
  const S = updateEndpoint(D, initState(D), "a1", { shrinkOn: true, shrinkLen: "25" });
  const ops = makeGen(D, S).getActiveOps();
  const keys = ops.map((o) => o.opKey);
  expect(ops.find((o) => o.opKey === "cutShr1")).toMatchObject({ type: "cutTut", tmplHint: "CUT_TUT_short", label: "Резка ТТК поверх разъёма А1 — 1×25 мм",
    tutArt: "ТТК 6/2", tutSegs: [{ len: "25", count: 1 }] });
  // Надевается вместе с прочими трубками одной операцией на обе стороны — до обжима (владелец 15.10).
  const put = keys.indexOf("insSleeves"), shrink = keys.indexOf("heatShrA1");
  expect(ops[put]).toMatchObject({ type: "insTut", harness: true, tutCount: 1, label: "Надеть трубки (стороны А и В): ТТК 6/2 поверх разъёма А1 — 1 шт" });
  expect(ops[shrink]).toMatchObject({ label: "Усадка ТТК поверх разъёма А1", tmplHint: "HEAT_TTK_short" });
  const ownOps = ops.filter((o) => o.side === "A" && o.epId).map((o) => keys.indexOf(o.opKey));
  expect(put).toBeLessThan(Math.min(...ownOps));
  expect(shrink).toBe(Math.max(...ownOps) + 1);
  expect(keys.indexOf("cutShr1")).toBeLessThan(put);
  expect(keys.some((k) => /Shr.*B/.test(k))).toBe(false);
});

it("heat-shrink over a solder connector shrinks after its mount; toggle off gives no ops", () => {
  const D = { ...data([{ id: "w", type: "Провод", name: "ПВАМ 0,5", art: "ПВАМ 0,5", qty: 0.3 },
    { id: "x", type: "Соед", name: "XT60 папа", art: "XT60-M", qty: 2 }, TTK]), templates: shrinkTemplates };
  // Клеевая ТТК — единственная трубка состава, разъём паяный: засев сам надевает её на корпус (776К. КБ2–КБ4).
  const seeded = initState(D);
  expect(seeded.endpoints.map((e) => e.shrinkOn)).toEqual([true, true]);
  // Выключили руками — операций термоусадки нет, и повторный updateEndpoint её не включает.
  const offState = updateEndpoint(D, updateEndpoint(D, seeded, "a1", { shrinkOn: false }), "b1", { shrinkOn: false });
  const off = makeGen(D, offState).getActiveOps();
  expect(off.some((o) => /Shr/.test(o.opKey))).toBe(false);
  expect(updateEndpoint(D, offState, "a1", { label: "X1" }).endpoints.find((e) => e.id === "a1")!.shrinkOn).toBe(false);
  const S = updateEndpoint(D, offState, "a1", { shrinkOn: true });
  const ops = makeGen(D, S).getActiveOps();
  const keys = ops.map((o) => o.opKey);
  expect(ops.find((o) => o.opKey === "cutShr1")?.tutSegs).toEqual([{ len: "20", count: 1 }]);
  const own = ops.filter((o) => o.epId === "a1").map((o) => keys.indexOf(o.opKey));
  expect(keys.indexOf("insSleeves")).toBeLessThan(Math.min(...own));
  expect(keys.indexOf("heatShrA1")).toBeGreaterThan(Math.max(...own));
});

it("connector sleeve is not counted twice as harness sleeve; old endpoints load without the fields", () => {
  const D = data([...interboard, TTK]);
  const base = initState(D);
  expect(base.endpoints[0].shrinkOn).toBeUndefined();
  expect(makeGen(D, base).getActiveOps().some((o) => /Shr/.test(o.opKey))).toBe(false);
  expect(makeGen(D, base).harnessSleeveComps_().map((c) => c.id)).toEqual(["ttk"]);
  const S = updateEndpoint(D, base, "a1", { shrinkOn: true });
  expect(makeGen(D, S).harnessSleeveComps_()).toEqual([]);
});

describe("savedComplex", () => {
  it("reads the saved tab, and an older config by what only «Сложная сборка» writes", () => {
    expect(savedComplex({ tab: "complex" })).toBe(true);
    expect(savedComplex({ tab: "inter", bigAssembly: true })).toBe(false);
    expect(savedComplex({ bigAssembly: true })).toBe(true);
    expect(savedComplex({ ops: [{ type: "seal" }] })).toBe(true);
    expect(savedComplex({ ops: [{ ep: { label: "NODE" } }] })).toBe(true);
    expect(savedComplex({ mode: "manual", bigAssembly: true })).toBe(false);
    expect(savedComplex({ ops: [{ type: "cutWire" }] })).toBe(false);
    expect(savedComplex(null)).toBe(false);
  });
});

// Владелец 14.10: «под каждый разъём длину зачистки; если записана — из БД».
describe("длина зачистки у разъёма", () => {
  it("from БД.ТЕР by default; the typed value overrides it in the cut strip and the build config; empty falls back", () => {
    const base = usbGen({ both: true });
    expect(base.g.epStripLen_(base.S.endpoints[0])).toBe("2");
    expect(base.g.getActiveOps()[0].strip).toEqual({ a: true, lenA: "2", b: true, lenB: "2" });
    const over = usbGen({ both: true, patch: (S, D) => updateEndpoint(D, S, "b1", { stripLen: " 3,5 " }) });
    expect(over.g.epStripLen_(over.S.endpoints[1])).toBe("3,5");
    expect(over.g.getActiveOps()[0].strip).toEqual({ a: true, lenA: "2", b: true, lenB: "3,5" });
    expect((over.g.buildConfig() as Record<string, any>).ops[0].strip).toMatchObject({ lenA: "2", lenB: "3,5" });
    const empty = usbGen({ both: true, patch: (S, D) => updateEndpoint(D, S, "b1", { stripLen: "  " }) });
    expect(empty.g.getActiveOps()[0].strip).toEqual({ a: true, lenA: "2", b: true, lenB: "2" });
  });

  it("one-side machine crimp + ОТП: the override of the hand-crimped side goes into the cut's strip of that side", () => {
    const { g } = usbGen({ side: "A", patch: (S, D) => updateEndpoint(D, S, "b1", { stripLen: "4", otp: true }) });
    expect(g.getActiveOps()[0].strip).toMatchObject({ a: true, lenA: "2", b: true, lenB: "4" });
  });

  it("no БД value and no override: «—»; an end without a terminal gives «—» too", () => {
    const { g, S } = usbGen({ both: true, patch: (st, D) => updateEndpoint(D, st, "a1", { termId: "" }) });
    expect(g.epStripLen_(S.endpoints[0])).toBe("—");
    expect(g.epStripLen_(null)).toBe("—");
  });

  it("solder ends keep solderStrip, not the crimp override", () => {
    const D = data([
      { id: "w", type: "Провод", name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 0.3 },
      { id: "c", type: "Соед", name: "Колодка XH 2", art: "XHP-2", qty: 2 },
    ]);
    const S = updateEndpoint(D, initState(D), "a1", { method: "solder", solderStrip: "7", stripLen: "9" });
    const strips = makeGen(D, S).getActiveOps().map((op) => op.strip).filter(Boolean);
    expect(JSON.stringify(strips)).toContain('"7"');
    expect(JSON.stringify(strips)).not.toContain('"9"');
  });
});

describe("коакс: норма заказчика (КД) задаётся на этапе ТК (владелец 23.10)", () => {
  const D = data([
    { id: "k", type: "Кабель", name: "RG316", art: "RG316", qty: 0.2 },
    { id: "a", type: "Соед", name: "SMA", art: "SMA-J", qty: 1 },
    { id: "b", type: "Соед", name: "SMA", art: "SMA-K", qty: 1 },
  ], "Сборка RG316 150mm");
  const cust = (patch: Partial<CoaxVswrCustForm> = {}): CoaxVswrCustForm => ({ max: "1,5", from: "700", to: "2700", basis: "ТУ 16-505.123, п. 3.4", ...patch });
  const withCust = (form: CoaxVswrCustForm | undefined, mode: GenState["coax"]["vswrMode"] = "all") => {
    const S = initState(D);
    return { ...S, coax: { ...S.coax, vswrMode: mode, vswrCust: form } };
  };
  const cfgOf = (S: GenState) => makeGen(D, S).buildConfig() as Record<string, any>;

  it("empty fields mean the norm by components: nothing in the config, no problem", () => {
    expect(coaxVswrCustProblem(undefined)).toBe("");
    expect(coaxVswrCustProblem({ max: "", from: "", to: "", basis: "" })).toBe("");
    expect(cfgOf(withCust(undefined)).coax).not.toHaveProperty("vswrCustomer");
  });

  it("a set norm goes to build_config.coax.vswrCustomer with numbers", () => {
    expect(cfgOf(withCust(cust())).coax.vswrCustomer).toEqual({ vswrMax: 1.5, fromMhz: 700, toMhz: 2700, basis: "ТУ 16-505.123, п. 3.4" });
    // «Не проверять» — нормы нет.
    expect(cfgOf(withCust(cust(), "none")).coax).not.toHaveProperty("vswrCustomer");
  });

  it("validates КСВН ≥ 1, from < to and a basis; the error stops the build", () => {
    expect(coaxVswrCustProblem(cust({ max: "0,9" }))).toBe("Норма заказчика: КСВН не может быть меньше 1");
    expect(coaxVswrCustProblem(cust({ from: "2700", to: "700" }))).toBe("Норма заказчика: начало полосы должно быть больше 0 и меньше конца");
    expect(coaxVswrCustProblem(cust({ to: "" }))).toBe("Норма заказчика: укажите полосу — от и до, МГц");
    expect(coaxVswrCustProblem(cust({ basis: " " }))).toBe("Норма заказчика: укажите основание — ТУ, КД или заказ");
    expect(coaxVswrCustProblem(cust({ max: "" }))).toBe("Норма заказчика: укажите КСВН или очистите поля");
    const S = withCust(cust({ basis: "" }));
    expect(cfgOf(S).coax).not.toHaveProperty("vswrCustomer");
    expect(makeGen(D, S).validate().error).toBe("Норма заказчика: укажите основание — ТУ, КД или заказ");
  });

  it("a saved card reopens with its customer norm", () => {
    const S = withCoaxVswrFromConfig(initState(D), { coax: { vswrControl: { mode: "all", every: null },
      vswrCustomer: { vswrMax: 1.5, fromMhz: 700, toMhz: 2700, basis: "заказ 77" } } });
    expect(S.coax.vswrCust).toEqual({ max: "1,5", from: "700", to: "2700", basis: "заказ 77" });
    expect(cfgOf(S).coax.vswrCustomer).toEqual({ vswrMax: 1.5, fromMhz: 700, toMhz: 2700, basis: "заказ 77" });
  });

  it("the components norm comes from the ETK norm and a stricter customer norm warns", () => {
    expect(componentsVswrMax({ coaxVswrNorm: { status: "ok", vswrMax: 2.06 } })).toBe(2.06);
    expect(componentsVswrMax({ coaxVswrNorm: { status: "ok", vswrMax: 1.5, source: "customer", components: { status: "ok", vswrMax: 2.06 } } })).toBe(2.06);
    expect(componentsVswrMax({ coaxVswrNorm: { status: "undefined", vswrMax: null } })).toBeNull();
    expect(componentsVswrMax(null)).toBeNull();
    const customer = coaxVswrCustomer_(cust());
    expect(coaxVswrCustStricter(customer, 2.06)).toBe(true);
    expect(coaxVswrCustStricter(customer, 1.4)).toBe(false);
    expect(coaxVswrCustStricter(customer, null)).toBe(false);
  });
});
