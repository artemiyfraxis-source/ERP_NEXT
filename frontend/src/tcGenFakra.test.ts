// @vitest-environment node
import { describe, expect, it } from "vitest";
import { autoConfig as seededConfig, generatorData, refsFromRecords, runBatch, type BatchBom, type BatchRefs } from "./tcGenBatch";
import { fakraEndsFromBom_, initState as seededState, makeGen, coaxCutLength, coaxFromFields, restoreCoaxState, type GenData, type GenState } from "./tcGenLogic";
import batchAConfigs from "./__fixtures__/fakraBatchAConfigs.json";
import freshConfigs from "./__fixtures__/fakra1116Configs.json";
import productionConfigs from "./__fixtures__/fakra708Configs.json";
import erpLibrary from "../../backend/data/techcard-library-erp.json";

import { COAX_COLORS, coaxMarkDefaults, coaxMarksFromName, sumCoaxMaterial } from "./tcGenMarks";
import { expandMarkSerial, hasMarkSerial, markSerialList } from "./markSerial";
import { coaxReseedPatch } from "./tcGenLogic";
import { deriveRoute, type RouteOp } from "./routeLogic";
import { genCards } from "./routeCards";

// Historical route/consumption fixtures predate the additive per-operation material association.
const withoutShrinkMaterials = (config: unknown) => JSON.parse(JSON.stringify(config, (key, value) => key === "shrinkMaterials" ? undefined : value));

// Historical assertions exercise a saved state with no new fields, not the new seed recipe.
function initState(D: GenData): GenState {
  const state = seededState(D);
  const { fakraCutStrip: _f, lengthBasis: _b, finishedLength: _l, finishedLengthFrom: _from, lPlusA: _a, lPlusB: _c,
    markMaterial: _m, markProtect: _p, marks: _s, connColor: _cc, ...coax } = state.coax;
  return { ...state, coax };
}
function autoConfig(D: GenData) {
  let state = initState(D);
  for (let i = 0; i < 10; i++) {
    const next = makeGen(D, state).stickTemplates();
    if (!next) break;
    state = { ...state, selectedTemplates: next };
  }
  const { cfg, error, warnings } = makeGen(D, state).validate();
  return { config: error ? null : cfg, error, warnings };
}

// 708К. (Яндекс): Rosenberger FAKRA / HFM и Amphenol 2FA1 на кабеле RTK 031 (владелец 25.10). Составы — как отдаёт
// GET /api/techcards/kitsets/708К./bom/КБn; строки БД.КОАКС — как их внесёт справочник (размеры разделки из документов
// изготовителя: 59K13O X 9,7 / Y 4,6 / Z 3,0; 59K25H 11,6 / 6,4 / 1,7; 2FA1 2,7 / 1,0 / 8,5).
const T = (code: string, category: string, side = "") => ({ id: `${code}${side}`, title: `${code} | ${category}${side ? ` (${side})` : ""}`, category });
const CARDS = [
  T("CUT_COAX_auto", "Резка"), T("CUT_TUT_short", "Резка"), T("PRINT_MARK_TUBE", "Печать маркировок"), T("INS_TUT", "Монтаж"),
  T("INS_SLEEVE_coax", "Монтаж"), T("STRIP_COAX_auto", "Разделка", "А"), T("STRIP_COAX_auto", "Разделка", "В"),
  T("PRS_SLEEVE_coax", "Опрессовка"), T("SDR_COAX_pin", "Пайка", "А"), T("SDR_COAX_pin", "Пайка", "В"),
  T("INS_housing_COAX", "Монтаж корпуса", "А"), T("INS_housing_COAX", "Монтаж корпуса", "В"),
  T("PRS_CAP", "Опрессовка крышки коакс"), T("INS_CAP", "Монтаж"), T("HEAT_TYT_short", "Усадка"), T("HEAT_TTK_short", "Усадка"),
  T("CTRL_VSWR_coax", "Контроль"),
];
const DEDICATED_CODES = {
  straight: ["INS_FAKRA_ST_PREP", "STRIP_FAKRA_ST", "PRS_FAKRA_ST_PIN", "INS_FAKRA_ST_CONTACT", "PRS_FAKRA_ST_SHIELD", "INS_FAKRA_ST_HOUSING", "INS_FAKRA_ST_SEAL"],
  angled: ["INS_FAKRA_RA_PREP", "STRIP_FAKRA_RA", "PRS_FAKRA_RA_PIN", "INS_FAKRA_RA_CONTACT", "PRS_FAKRA_RA_SHIELD", "PRS_FAKRA_RA_CAP", "INS_FAKRA_RA_HOUSING"],
  hfm: ["INS_HFM_PREP", "STRIP_HFM", "PRS_HFM_PIN", "INS_HFM_CONTACT", "PRS_HFM_SHIELD", "INS_HFM_HOUSING"],
  bulkhead: ["INS_FAKRA_BH_PREP", "STRIP_FAKRA_BH", "SDR_FAKRA_BH_PIN", "INS_FAKRA_BH_CONTACT", "PRS_FAKRA_BH_SHIELD", "HEAT_FAKRA_BH_TTK"],
};
const DEDICATED_CARDS = Object.values(DEDICATED_CODES).flat().map((code) => T(code, "Специализированная операция"));
const DEDICATED_OPS = DEDICATED_CARDS.map((card) => ({ opNumber: card.id, opName: card.title, tPrep: "", tOp: "", tMachine: "" }));
const op = (opNumber: string) => ({ opNumber, opName: opNumber, tOp: "10", tPrep: "60", tMachine: "1" });
const coaxRow = (art: string, type: string, l1: string, l2: string, l3: string, pin: string) => ({
  "Артикул": art, "Тип/Серия": type, "Провод": "RTK 031", "L1": l1, "L2": l2, "L3": l3, "Тип пина": pin, "Тип экрана": "обжим",
});
const REFS: BatchRefs = {
  templates: [...CARDS, ...DEDICATED_CARDS], op: [...["CUT_COAX_auto", "PRINT_MARK_TUBE", "STRIP_COAX_auto"].map(op), ...DEDICATED_OPS], ter: [], wire_dia: [],
  coax: [
    coaxRow("59K13O-1M4A4", "FAKRA-HF прямой", "9,7", "4,6", "3,0", "обжим"),
    coaxRow("59K25H-1M4A4", "FAKRA-HF угловой", "11,6", "6,4", "1,7", "обжим"),
    coaxRow("2FA1-NZSP-C30E2", "FAKRA Gen2 панельный", "2,7", "1,0", "8,5", "пайка"),
    { ...coaxRow("AMK11D-1M4Z5-Z", "HFM Quad", "13", "5,55", "2,1", "обжим"), "Наименование": "Корпус на 4 канала с контактами" },
  ],
  kab_coax: [{ "Кабель": "RTK 031", D1: "0,81", D2: "2,1", D3: "3,2" }],
};
const c = (type: string, art: string, qty: number | string) => ({ id: `${type}-${art}`, type, name: art, art, qty });
const bom = (index: string, name: string, components: BatchBom["components"]): BatchBom => ({ index, name, run: "15", part_qty: "15", components, calc_ops: [] });
const straight = (n: number) => [c("Разъем", "59Z163-003-Z", n), c("Разъем", "59K13o-1M4A4", n), c("Разъем", "59K14B-1M4/50", n), c("Разъем", "59Z163-000/51", n)];
const tutMarks = [c("ТУТ", "ТУТ 4/2мм, прозрачная", "0.18"), c("Маркировка", "Маркировка", 3)];

const KB15 = bom("КБ15", "Кабельная сборка 122662/AA  121709/AA Camera 3", [...straight(2), ...tutMarks, c("Кабель", "RTK 031", "1.55")]);
const KB1 = bom("КБ1", "Кабельная сборка 122664/AA  120907/AA Camera 14 → Splitter B", [
  ...straight(1), c("Разъем", "59Z006-T00-Z", 1), c("Разъем", "59K25H-1M4A4", 1), ...tutMarks, c("Кабель", "RTK 031", "4.8"), c("Др", "Маркер розовый Оранжевый", 1)]);
const KB28 = bom("КБ28", "Кабельная сборка 122663/AA  120898/AA Camera 3 → Splitter A", [
  c("Разъем", "2FA1-NZSP-C30E2", 1), ...straight(1), c("ТТК", "ТТК 6/2мм, черная", "0.06"), c("ТУТ", "ТУТ 4/2мм, прозрачная", "0.18"),
  c("Кабель", "RTK 031", "10.05"), c("Др", "Маркер белый", 1)]);
const KB3 = bom("КБ3", "Кабельная сборка 122666/AA  120961/AA Splitter A V1-4 → HPC", [c("Разъем", "AMK11D-1M4Z5-Z", 2), ...tutMarks, c("Кабель", "RTK 031", "5")]);
const KB4 = bom("КБ4", "Кабельная сборка 122666/AA  120965/AA Splitter A 2V1-4 → HPC", [
  c("Разъем", "AMK11D-1M4Z5-Z", 2), ...tutMarks, c("Разъем", "AMK11D-1M4Z5-Z", 2), ...tutMarks, c("Кабель", "RTK 031", "5")]);
const KB41 = bom("КБ41", "Кабельная сборка 121501_AA-Жгут салонных камер", [
  c("Разъем", "818018737", 1), ...straight(1), c("Разъем", "59Z006-T00-Z", 1), c("Разъем", "59K25H-1M4A4", 1),
  c("Разъем", "Тканевая лента Tesa, черная", 1), c("Др", "Оплетка XS-8", "1.05"), ...tutMarks, c("Кабель", "RTK 031", "8.9")]);
const KB11 = bom("КБ11", "Кабельная сборка 121537/AA  121538AA TBOX 5G1-1 (LTE_1)", [
  c("Разъем", "73403-6270", 1), c("Разъем", "901-9610-3", 1), c("ТТК", "ТТК 6/2мм, черная", "0.06"), ...tutMarks, c("Кабель", "LMR-100", "2.25")]);

function run(b: BatchBom) {
  const D = generatorData(refsFromRecords(REFS), b);
  const S = initState(D);
  const g = makeGen(D, S);
  return { D, S, g, ops: g.getActiveOps(), ...autoConfig(D) };
}
const labels = (b: BatchBom) => run(b).ops.map((o) => o.label);

const conn = (b: BatchBom) => b.components.filter((x) => x.type === "Разъем");

type SavedPart = { art: string; name: string; qty: number; role?: string; unit?: string };
type SavedOp = { opKey: string; type: string; side: string; units: number; templateId: string; opNum: string;
  tPrep: string; tOp: string; tMachine: string; label?: string; parts?: SavedPart[]; noTooling?: boolean };
type SavedSide = { article: string; connName: string; kind?: keyof typeof DEDICATED_CODES; parts?: SavedPart[];
  type: string; wire: string; mfr: string; l1: string; l2: string; l3: string; pinType: string; shield: string; d1: string; d2: string; d3?: string };
type ProductionFixture = { project: string; kb: string; config: { assemblyName: string; partQty: number; ops: SavedOp[];
  coax: { sideA: SavedSide; sideB: SavedSide; cableLength: number; cables?: number; cableArt: string; cableName: string;
    tutLength: number; tutArt: string; tutName: string } } };
const fixtures = productionConfigs as ProductionFixture[];

// Exported build_config has no BOM/gen_state. Reconstruct only its saved material quantities and reference fields;
// keep the original operations as independent evidence for order, labels, quantities and component placement.
function productionData(fixture: ProductionFixture, dedicated = true) {
  const { config } = fixture, { coax } = config;
  const sides = [coax.sideA, coax.sideB];
  const marks = config.ops.find((o) => o.opKey === "coaxCutTut")?.units || 0;
  const components: BatchBom["components"] = sides.flatMap((end) => (end.parts || [{ art: end.article, name: end.connName, qty: 1 }])
    .map((part) => ({ ...c("Разъем", part.art, part.qty), name: part.name })));
  for (const operation of config.ops) {
    for (const part of operation.parts || []) {
      if (part.unit === "М.") components.push({ ...c("ТТК", part.art, part.qty), name: part.name });
    }
  }
  components.push({ ...c("Кабель", coax.cableArt, coax.cableLength * (coax.cables || 1) / 1000), name: coax.cableName });
  if (marks) {
    components.push({ ...c("ТУТ", coax.tutArt, coax.tutLength * marks / 1000), name: coax.tutName });
    components.push(c("Маркировка", "Маркировка", marks));
  }
  const genericCode = (operation: SavedOp) => operation.opNum || (operation.type === "coaxCapCrimp" ? "PRS_CAP" : "CTRL_VSWR_coax");
  const templates = Array.from(new Map(config.ops.map((operation) => [operation.templateId, {
    id: operation.templateId, title: `${genericCode(operation)} | ${operation.label || operation.type}`,
    category: operation.type === "coaxCapCrimp" ? "Опрессовка крышки коакс" : "",
  }])).values());
  const refs: BatchRefs = {
    ...REFS, templates: [...templates, ...(dedicated ? DEDICATED_CARDS : [])],
    op: [...config.ops.filter((o) => o.opNum).map((o) => ({ opNumber: o.opNum, opName: o.opNum, tPrep: o.tPrep, tOp: o.tOp, tMachine: o.tMachine })),
      ...(dedicated ? DEDICATED_OPS : [])],
    coax: sides.map((end) => ({ ...coaxRow(end.article, end.type, end.l1, end.l2, end.l3, end.pinType),
      "Провод": end.wire, "Производитель": end.mfr, "Тип экрана": end.shield,
      ...(end.kind === "hfm" ? { "Количество каналов": String(coax.cables) } : {}) })),
    kab_coax: [{ "Кабель": coax.cableArt, D1: coax.sideA.d1, D2: coax.sideA.d2, D3: coax.sideA.d3 || "" }],
  };
  return generatorData(refsFromRecords(refs), { ...bom(fixture.kb, config.assemblyName, components), run: String(config.partQty), part_qty: String(config.partQty) });
}

describe("production 708К. fixture preservation", () => {
  it.each(fixtures.filter((fixture) => fixture.kb !== "КБ11"))("$kb preserves every operation and uses the exact dedicated stage cards", (fixture) => {
    const data = productionData(fixture);
    const result = autoConfig(data);
    expect(result.error).toBe("");
    const ops = result.config!.ops as SavedOp[];
    const preserved = (operation: SavedOp) => ({ opKey: operation.opKey, type: operation.type, side: operation.side,
      units: operation.units, label: operation.label, parts: operation.parts });
    // Short connector cards 18.11: no «0,18 мм» depth, no click on the metal contact, no CPA without a defined order.
    const relabel: Array<[string, string]> = [
      ["Вставка во вставку до щелчка, контроль заглубления контакта 0,18 мм", "Вставка контакта в металлический корпус, утопание 0,18…0,68 мм"],
      ["Вставка контактов в корпус до щелчка", "Вставка контактов в металлические корпуса"],
      ["Вторичная фиксация и CPA", "Установка в корпус QUAD и вторичная фиксация"],
      ["Установка в корпус до щелчка", "Установка в пластмассовый корпус"]];
    const expected = fixture.config.ops.map((saved) => {
      const operation = { ...saved, label: relabel.reduce((label, [from, to]) => label?.replace(from, to), saved.label) };
      const end = fixture.config.coax[operation.side === "B" ? "sideB" : "sideA"];
      if (!/^coaxInsSleeve[AB]$/.test(operation.opKey)) return operation;
      // PREP must not assert unavailable seal/cap fitting order, or HFM sleeve placement before partial stripping.
      if (end.kind === "straight") return { ...operation, label: operation.label!
        .replace("Надеть крышку, уплотнитель и гильзу", "Подготовить гильзу, уплотнитель и крышку") };
      return end.kind === "hfm" ? { ...operation, label: operation.label!.replace("Надеть", "Подготовить") } : operation;
    });
    const marking = new Set(["coaxCutTut", "coaxInsTut", "coaxHeat"]);
    const corrected = (operation: SavedOp) => endCount > 1 && marking.has(operation.opKey)
      ? { ...operation, units: operation.units * endCount, label: operation.label?.replace(`×${operation.units}`, `×${operation.units * endCount}`) } : operation;
    const endCount = fixture.config.coax.cables || 1;
    expect(ops.map(preserved)).toEqual(expected.map(corrected).map(preserved));
    for (const side of ["A", "B"] as const) {
      const end = fixture.config.coax[side === "A" ? "sideA" : "sideB"];
      const stages = ops.filter((operation) => operation.side === side);
      const codes = stages.map((operation) => data.templates.find((template) => template.id === operation.templateId)!.title.split("|")[0].trim());
      expect(codes).toEqual(DEDICATED_CODES[end.kind!]);
      expect(stages.map((operation) => operation.opNum)).toEqual(codes);
      expect(stages.map(({ tPrep, tOp, tMachine }) => [tPrep, tOp, tMachine]))
        .toEqual(stages.map(() => ["", "", ""]));
      expect(stages.filter((operation) => operation.noTooling).map((operation) => operation.opKey))
        .toEqual(end.kind === "straight" ? [`coaxSealCap${side}`] : []);
    }
    const unchanged = (operation: SavedOp) => ({ ...preserved(operation), templateId: operation.templateId, opNum: operation.opNum,
      tPrep: operation.tPrep, tOp: operation.tOp, tMachine: operation.tMachine });
    expect(ops.filter((operation) => !operation.side).map(unchanged))
      .toEqual(fixture.config.ops.filter((operation) => !operation.side).map(corrected).map(unchanged));
  });

  it("ERP-owned cards resolve every production stage without generic fallback", () => {
    const codes = new Set(Object.values(DEDICATED_CODES).flat());
    const dedicated = erpLibrary.templates.filter((template) => codes.has(template.title.split("|")[0].trim()));
    expect(new Set(dedicated.map((template) => template.title.split("|")[0].trim()))).toEqual(codes);
    for (const fixture of fixtures.filter((item) => item.kb !== "КБ11")) {
      const data = productionData(fixture);
      data.templates = [...data.templates.filter((template) => !codes.has(template.id)), ...dedicated];
      const result = autoConfig(data);
      expect(result.error).toBe("");
      expect((result.config!.ops as SavedOp[]).filter((operation) => operation.side)
        .every((operation) => dedicated.some((template) => template.id === operation.templateId))).toBe(true);
    }
  });

  it("КБ11: the crimped centre contact moves to its own PRS_PIN_coax card; every other byte stays (свод 4/5)", async () => {
    const fixture = fixtures.find((item) => item.kb === "КБ11")!;
    const withPin = (dedicated: boolean) => {
      const data = productionData(fixture, dedicated);
      data.templates = [...data.templates, T("PRS_PIN_coax", "Опрессовка")];
      data.ops = [...(data.ops || []), { opNumber: "PRS_PIN_coax", opName: "Обжим центрального контакта", tPrep: "", tOp: "", tMachine: "" }];
      return data;
    };
    const config = autoConfig(withPin(true)).config as typeof fixture.config;
    const pin = config.ops.filter((operation) => operation.type === "coaxPinCrimp");
    // The saved-route correction (docs/data/etk-pin-coax-1119) writes exactly this: the card, its code and its own
    // (not yet set) times; the shield crimp stays a separate PRS_SLEEVE_coax operation.
    expect(pin.map(({ side, templateId, opNum, tPrep, tOp, tMachine }) => [side, templateId, opNum, tPrep, tOp, tMachine]))
      .toEqual([["A", "PRS_PIN_coax", "PRS_PIN_coax", "", "", ""]]);
    expect(config.ops.filter((operation) => operation.type === "coaxShield").map((operation) => operation.opNum)).toEqual(["PRS_SLEEVE_coax"]);
    // Put the former shield-card binding back: the bytes are the 18.11 output exactly — nothing else moved.
    const sleeve = fixture.config.ops.find((operation) => operation.type === "coaxPinCrimp")!;
    const before = { ...config, ops: config.ops.map((operation) => operation.type !== "coaxPinCrimp" ? operation
      : { ...operation, templateId: sleeve.templateId, opNum: sleeve.opNum, tPrep: sleeve.tPrep, tOp: sleeve.tOp, tMachine: sleeve.tMachine }) };
    const bytes = new TextEncoder().encode(JSON.stringify(withoutShrinkMaterials(before)));
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    expect(Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join(""))
      .toBe("508266ccd3e42a51b7f7dbaba7df3c21621f02e9e7447db80cf3994d3d3328fc");
    expect(config).toEqual(autoConfig(withPin(false)).config);
    // Without the card the generator stops instead of giving the pin the shield card.
    expect(autoConfig(productionData(fixture)).error).toContain("PRS_PIN_coax");
  });
});

describe("концы FAKRA из состава", () => {
  it("КБ15: qty 2 — оба конца прямые, детали по одной на конец", () => {
    const fk = fakraEndsFromBom_(conn(KB15))!;
    expect(fk.ends.map((e) => [e.art, e.kind])).toEqual([["59K13o-1M4A4", "straight"], ["59K13o-1M4A4", "straight"]]);
    expect(fk.ends[0].parts!.map((p) => [p.role, p.art, p.qty])).toEqual([
      ["main", "59K13o-1M4A4", 1], ["housing", "59Z163-003-Z", 1], ["seal", "59K14B-1M4/50", 1], ["cap", "59Z163-000/51", 1]]);
  });
  it("КБ1: прямой конец А и угловой В со своим корпусом 59Z006", () => {
    const fk = fakraEndsFromBom_(conn(KB1))!;
    expect(fk.ends.map((e) => e.kind)).toEqual(["straight", "angled"]);
    expect(fk.ends[1].parts!.map((p) => p.art)).toEqual(["59K25H-1M4A4", "59Z006-T00-Z"]);
  });
  it("КБ28: панельный 2FA1 и прямой, КБ3: два четверных HFM", () => {
    expect(fakraEndsFromBom_(conn(KB28))!.ends.map((e) => e.kind)).toEqual(["bulkhead", "straight"]);
    expect(fakraEndsFromBom_(conn(KB3))!.ends.map((e) => e.kind)).toEqual(["hfm", "hfm"]);
  });
  it("КБ4: дубль блока AMK11D даёт 4 конца — в карте два, лишние в предупреждении", () => {
    const fk = fakraEndsFromBom_(conn(KB4))!;
    expect(fk.ends).toHaveLength(2);
    expect(fk.extra).toBe(2);
    expect(run(KB4).warnings.join("\n")).toMatch(/4 конца FAKRA\/HFM/);
  });
  it("КБ41 с чужим разъёмом TZTEK и КБ11 (Molex + Amphenol) — не FAKRA", () => {
    expect(fakraEndsFromBom_(conn(KB41))).toBeNull();
    expect(fakraEndsFromBom_(conn(KB11))).toBeNull();
    expect(run(KB11).S.coax.sideA?.kind).toBeUndefined();
  });
});

describe("операции FAKRA по документам изготовителя", () => {
  it("КБ15: вкладка «КОАКС», порядок MA_59V059 на каждом конце", () => {
    const r = run(KB15);
    expect(r.S.tab).toBe("coax");
    expect(r.S.coax.len).toBe("1550");
    expect(labels(KB15)).toEqual([
      "Резка кабеля", "Печать меток и резка ТУТ под маркировку ×3", "Монтаж ТУТ под маркировку ×3",
      "Подготовить гильзу, уплотнитель и крышку (А)",
      "Разделка коаксиала (А)", "Обжим центрального контакта (А)", "Вставка контакта в металлический корпус, утопание 0,18…0,68 мм (А)",
      "Обжим гильзы экрана (А)", "Установка в пластмассовый корпус (А)", "Уплотнитель и крышка (А)",
      "Подготовить гильзу, уплотнитель и крышку (В)",
      "Разделка коаксиала (В)", "Обжим центрального контакта (В)", "Вставка контакта в металлический корпус, утопание 0,18…0,68 мм (В)",
      "Обжим гильзы экрана (В)", "Установка в пластмассовый корпус (В)", "Уплотнитель и крышка (В)",
      "Усадка ТУТ под маркировку ×3", "Контроль КСВН",
    ]);
  });
  it("детали комплекта сохранены, оснастка включена кроме герметизации без MA_59V111", () => {
    const { config, error } = run(KB15);
    expect(error).toBe("");
    const ops = (config!.ops as Array<Record<string, unknown>>);
    const by = (key: string) => ops.find((o) => o.opKey === key)!;
    expect(by("coaxInsSleeveA").parts).toEqual([{ art: "59K14B-1M4/50", name: "59K14B-1M4/50", qty: 1 }, { art: "59Z163-000/51", name: "59Z163-000/51", qty: 1 }]);
    expect(by("coaxInsSleeveB")).toMatchObject({ side: "B", label: "Подготовить гильзу, уплотнитель и крышку (В)" });
    expect(by("coaxPinCrimpA").parts).toEqual([{ art: "59K13o-1M4A4", name: "59K13o-1M4A4", qty: 1 }]);
    expect(by("coaxBodyB").parts).toEqual([{ art: "59Z163-003-Z", name: "59Z163-003-Z", qty: 1 }]);
    expect(by("coaxPinCrimpA").noTooling).toBeUndefined();
    expect(by("coaxSealCapA").noTooling).toBe(true);
    expect(by("coaxCut").noTooling).toBeUndefined();
    const coax = config!.coax as Record<string, any>;
    expect(coax.sideA.kind).toBe("straight");
    expect([coax.sideA.l1, coax.sideA.l2, coax.sideA.l3]).toEqual(["9,7", "4,6", "3,0"]);
    expect(coax.sideA.pinType).toBe("обжим");
    expect(coax.tutLength).toBe(60);
    expect(coax.cables).toBeUndefined();
  });
  it("КБ1: прямой конец А и угловой В (MA_59V065: пин, окно, гильза, крышка, корпус 59Z006)", () => {
    const r = run(KB1);
    expect(r.S.coax.len).toBe("4800");
    expect(labels(KB1).slice(3)).toEqual([
      "Подготовить гильзу, уплотнитель и крышку (А)",
      "Разделка коаксиала (А)", "Обжим центрального контакта (А)", "Вставка контакта в металлический корпус, утопание 0,18…0,68 мм (А)",
      "Обжим гильзы экрана (А)", "Установка в пластмассовый корпус (А)", "Уплотнитель и крышка (А)",
      "Надеть гильзу (В)",
      "Разделка коаксиала (В)", "Обжим центрального контакта (В)", "Вставка в корпус, контроль пина в окне (В)",
      "Обжим гильзы экрана (В)", "Запрессовать крышку (В)", "Корпус и вторичная фиксация (В)",
      "Усадка ТУТ под маркировку ×3", "Контроль КСВН",
    ]);
    const parts = (r.config!.ops as Array<{ opKey: string; parts?: unknown[] }>).find((o) => o.opKey === "coaxBodyB")!.parts;
    expect(parts).toEqual([{ art: "59Z006-T00-Z", name: "59Z006-T00-Z", qty: 1 }]);
  });
  it("КБ28: 2FA1 — ТТК, пайка жилы в пин, обжим гильзы, усадка ТТК; прямой FAKRA на другом конце", () => {
    const r = run(KB28);
    expect(r.S.coax.pinA).toBe("пайка");
    expect(r.S.coax.pinB).toBe("обжим");
    expect(labels(KB28)).toEqual([
      "Резка кабеля", "Печать меток и резка ТУТ под маркировку ×3", "Монтаж ТУТ под маркировку ×3",
      "Надеть ТТК, гильзу и шайбу (А)",
      "Разделка коаксиала (А)", "Пайка жилы в центральный контакт (А)", "Вставка контакта в корпус (А)", "Обжим гильзы экрана (А)", "Усадка ТТК на гильзе (А)",
      "Подготовить гильзу, уплотнитель и крышку (В)",
      "Разделка коаксиала (В)", "Обжим центрального контакта (В)", "Вставка контакта в металлический корпус, утопание 0,18…0,68 мм (В)",
      "Обжим гильзы экрана (В)", "Установка в пластмассовый корпус (В)", "Уплотнитель и крышка (В)",
      "Усадка ТУТ под маркировку ×3", "Контроль КСВН",
    ]);
    const put = (r.config!.ops as Array<{ opKey: string; parts?: Array<{ art: string; qty: number; unit?: string }> }>).find((o) => o.opKey === "coaxInsSleeveA")!;
    expect(put.parts!.find((p) => p.art.startsWith("ТТК"))).toMatchObject({ qty: 0.06, unit: "М." });
    expect(r.warnings.join("\n")).toMatch(/нет строки «Маркировка»/);
  });
  it("КБ3: HFM — четыре кабеля, контактные операции ×4, корпус один раз на конец", () => {
    const r = run(KB3);
    expect(r.S.coax.cables).toBe(4);
    expect(r.S.coax.len).toBe("1250");
    expect(labels(KB3)).toEqual([
      "Резка кабеля ×4", "Печать меток и резка ТУТ под маркировку ×12", "Монтаж ТУТ под маркировку ×12",
      "Подготовить опорные гильзы ×4 (А)",
      "Разделка коаксиала ×4 (А)", "Обжим центрального контакта ×4 (А)", "Вставка контактов в металлические корпуса ×4 (А)",
      "Обжим гильзы экрана ×4 (А)", "Установка в корпус QUAD и вторичная фиксация (А)",
      "Подготовить опорные гильзы ×4 (В)",
      "Разделка коаксиала ×4 (В)", "Обжим центрального контакта ×4 (В)", "Вставка контактов в металлические корпуса ×4 (В)",
      "Обжим гильзы экрана ×4 (В)", "Установка в корпус QUAD и вторичная фиксация (В)",
      "Усадка ТУТ под маркировку ×12", "Контроль КСВН",
    ]);
    expect((r.config!.coax as Record<string, unknown>).cables).toBe(4);
    expect(r.warnings.join("\n")).toMatch(/Кабелей 4/);
  });
  it.each(Object.values(DEDICATED_CODES).flat())("отсутствие %s блокирует создание целой сборки", (code) => {
    const fixture = fixtures.find((item) => [item.config.coax.sideA.kind, item.config.coax.sideB.kind]
      .some((kind) => kind && DEDICATED_CODES[kind].includes(code)))!;
    const data = productionData(fixture);
    data.templates = data.templates.filter((template) => template.id !== code);
    // A similarly named card and the generic coax cards must not satisfy the exact stage contract.
    data.templates.push(T(`${code}_OTHER`, "Другая операция"));
    const result = autoConfig(data);
    expect(result.config).toBeNull();
    expect(result.error).toContain(code);
    expect(result.error).toMatch(/Нет специализированной карты/);
    const gen = makeGen(data, initState(data));
    const preview = gen.buildConfig().ops as SavedOp[];
    expect(preview.map((operation) => operation.opKey)).toEqual(fixture.config.ops.map((operation) => operation.opKey));
    const missing = gen.getActiveOps().filter((operation) => operation.tmplHint === code).map((operation) => operation.opKey);
    expect(missing.length).toBeGreaterThan(0);
    expect(preview.filter((operation) => missing.includes(operation.opKey)).every((operation) => operation.templateId === "")).toBe(true);
    expect(gen.validate().error).toContain(code);
  });

  it("старый ручной выбор общей карты не подменяет специализированную операцию", () => {
    const { D, S } = run(KB1);
    const state = { ...S, selectedTemplates: { ...S.selectedTemplates, coaxPinCrimpB: "PRS_SLEEVE_coax" }, manualTemplates: ["coaxPinCrimpB"] };
    const gen = makeGen(D, state);
    const spec = gen.getActiveOps().find((operation) => operation.opKey === "coaxPinCrimpB")!;
    expect(gen.templateIdFor(spec)).toBe("PRS_FAKRA_RA_PIN");
    const without = { ...D, templates: D.templates.filter((template) => template.id !== "PRS_FAKRA_RA_PIN") };
    expect(makeGen(without, state).validate().error).toContain("PRS_FAKRA_RA_PIN");
  });
});

const NEW_CODES = ["CUT_COAX+2strip", "PRINT_MARK_LABEL", "INS_MARK_LABEL", "MARK_CONN_PAINT", "MARK_CONN_LASER", "MARK_CONN_TAPE"];
function freshData(kb = "КБ1") {
  const color = kb === "КБ2" ? "151986" : "151437";
  const name = kb === "КБ2" ? "Кабельная сборка 122664/AA 120905/AA Camera 11 → Splitter B C3"
    : "Кабельная сборка 122664/AA 120907/AA Camera 14 → Splitter B C6";
  const refs = { ...REFS, templates: [...REFS.templates, ...NEW_CODES.map((code) => T(code, code))],
    coax: REFS.coax.map((row) => ({ ...row, "L+": row["Артикул"].startsWith("59K13") ? "30" : "25" })) };
  const items = KB1.components.filter((item) => item.type !== "Др");
  return generatorData(refsFromRecords(refs), bom(kb, name, [...items,
    c("Материал", "Лента Vell VL-S641", 3), { ...c("Материал", color, 1), name: "Маркер-краска Brauberg" }]));
}

describe("FAKRA flow 16.11", () => {
  it.each([
    [true, true, "all"], [true, true, "none"], [true, false, "all"],
    [false, true, "all"], [false, true, "none"], [false, false, "all"],
  ] as const)("finishes covered labels after all connector work (FAKRA %s, two ends %s, VSWR %s)", (fakra, both, vswrMode) => {
    const data = freshData(), state = seededState(data);
    if (!fakra) {
      state.coax.sideA = { ...state.coax.sideA!, kind: undefined, art: "SMA-A", name: "SMA" };
      state.coax.sideB = { ...state.coax.sideB!, kind: undefined, art: "SMA-B", name: "SMA" };
    }
    if (!both) state.coax.sideB = null;
    state.coax.vswrMode = vswrMode;
    // Simulate an older saved custom order with sticking before connector assembly and colour before VSWR.
    state.opOrder = freshConfigs[0].config.ops.map((op) => op.opKey);
    const ops = makeGen(data, state).getActiveOps(), keys = ops.map((op) => op.opKey);
    const install = keys.indexOf("coaxInsLabel"), heat = keys.indexOf("coaxHeat"), protect = keys.indexOf("coaxProtectIns");
    expect(install).toBeGreaterThan(0);
    expect(heat).toBe(install + 1);
    expect(keys.at(-1)).toBe("coaxConnColor");
    const connectorWork = ops.filter((op) => ["coaxHousing", "coaxShield", "coaxCapCrimp", "coaxCapScrew"].includes(op.type));
    expect(connectorWork.length).toBeGreaterThan(0);
    connectorWork.forEach((op) => {
      expect(keys.indexOf(op.opKey)).toBeGreaterThan(protect);
      expect(keys.indexOf(op.opKey)).toBeLessThan(install);
    });
    const routeOps: RouteOp[] = ops.map((op, i) => ({ position: i + 1, key: op.opKey, type: op.type,
      code: op.tmplHint || "", side: op.side, label: op.label || "", minutes: 0 }));
    const { cards, links } = genCards(deriveRoute(routeOps), routeOps);
    expect(cards.find((card) => card.final)?.ops.some((op) => op.key === "coaxInsLabel")).toBe(true);
    expect(links.some((link) => link.from === "labels" && link.to === "final")).toBe(true);
    expect(links.some((link) => link.from === "labels" && link.to === "marked")).toBe(false);
  });

  it.each([1, 4])("protects labels by place on %i cables regardless of the saved single length", (cables) => {
    const data = freshData(), state = seededState(data);
    state.coax.cables = cables;
    for (const tutLen of ["30", "60"]) {
      state.coax.tutLen = tutLen;
      const cfg = makeGen(data, state).buildConfig() as any;
      expect(cfg.coax.tutLengths).toEqual({ A: 35, B: 35, middle: 55 });
      expect(cfg.coax).not.toHaveProperty("tutLength");
      for (const key of ["coaxProtectCut", "coaxProtectIns", "coaxHeat"])
        expect(cfg.ops.find((op: any) => op.opKey === key).units).toBe(3 * cables);
      state.coax.marks![0].scope = "assembly";
      expect((makeGen(data, state).buildConfig() as any).ops.find((op: any) => op.opKey === "coaxProtectCut").units).toBe(2 * cables + 1);
      delete state.coax.marks![0].scope;
    }
    state.coax.markMaterial = "tube";
    const tubes = makeGen(data, state).buildConfig() as any;
    expect(tubes.coax.tutLength).toBe(60);
    expect(tubes.coax).not.toHaveProperty("tutLengths");
  });
  it.each(["КБ1", "КБ2"])("%s seeds finished length, three explicit labels and the BOM colour", (kb) => {
    const data = freshData(kb), state = seededState(data), gen = makeGen(data, state);
    expect(state.coax).toMatchObject({ lengthBasis: "finished", finishedLength: "4800", lPlusA: "30", lPlusB: "25", markMaterial: "tape", markProtect: true });
    expect(coaxCutLength(state.coax)).toBe(4855);
    expect(seededConfig(data).error).toContain("подтвердите длину готового изделия");
    const cfg = gen.buildConfig() as any;
    expect(cfg.coax.cableLength).toBe(4855);
    const expected = structuredClone(freshConfigs.find((item) => item.kb === kb)!.config) as any;
    expected.coax.finishedLengthFrom = "bom";
    delete expected.ops.find((op: any) => op.opKey === "coaxPrintLabel").parts;
    // The frozen 16.11 recipe remains an old-config regression fixture; finishing changed on 23.11.
    const finishing = ["coaxInsLabel", "coaxHeat", "coaxVswr", "coaxConnColor"];
    expected.ops = [...expected.ops.filter((op: any) => !finishing.includes(op.opKey)),
      ...finishing.map((key) => expected.ops.find((op: any) => op.opKey === key))];
    expect(withoutShrinkMaterials(cfg)).toEqual(expected);
    for (const key of ["coaxProtectCut", "coaxProtectIns", "coaxHeat"]) {
      expect(cfg.ops.find((op: any) => op.opKey === key).shrinkMaterials).toEqual([
        { art: cfg.coax.tutArt, name: cfg.coax.tutName },
      ]);
    }
    expect(cfg.coax.marks).toEqual(coaxMarksFromName(kb === "КБ2" ? "120905/AA Camera 11 → Splitter B C3" : "120907/AA Camera 14 → Splitter B C6"));
    expect(cfg.coax.connColor).toMatchObject(kb === "КБ2" ? { color: "розовый", code: "PK" } : { color: "оранжевый", code: "OG" });
    expect(cfg.ops.map((op: any) => op.opKey)).toEqual([
      "coaxFakraCutStrip", "coaxPrintLabel", "coaxProtectCut", "coaxProtectIns", "coaxInsSleeveA", "coaxInsSleeveB",
      "coaxPinCrimpA", "coaxInsertA", "coaxShieldA", "coaxBodyA", "coaxSealCapA",
      "coaxPinCrimpB", "coaxInsertB", "coaxShieldB", "coaxCapCrimpB", "coaxBodyB", "coaxInsLabel", "coaxHeat", "coaxVswr", "coaxConnColor",
    ]);
    expect(cfg.ops[0]).toMatchObject({ stripSides: ["A", "B"], templateId: "CUT_COAX+2strip", units: 1 });
    expect(cfg.ops[0].machine.model).toBe("BZW-886+Q2");
    for (const key of ["coaxPrintLabel", "coaxProtectCut", "coaxProtectIns", "coaxInsLabel", "coaxHeat"])
      expect(cfg.ops.find((op: any) => op.opKey === key).units).toBe(3);
    expect(cfg.ops.find((op: any) => op.opKey === "coaxConnColor").units).toBe(2);
    expect(cfg.ops.find((op: any) => op.opKey === "coaxInsLabel").marks).toEqual(cfg.coax.marks);
    const restored = { ...state, coax: { ...state.coax, len: "4855" } };
    expect((makeGen(data, restored).buildConfig().coax as any).cableLength).toBe(4855);
    const ops: RouteOp[] = cfg.ops.map((op: any, i: number) => ({ position: i + 1, key: op.opKey, type: op.type, code: op.templateId, label: op.label, side: op.side || undefined, minutes: 0 }));
    const route = deriveRoute(ops), cards = genCards(route, ops).cards;
    expect(cards.map((card) => card.title)).toEqual(["Нарезанный и разделанный кабель", "Этикетки", "Нарезанные трубки",
      "Кабель с трубками и деталями", "Сторона А собрана", "Сторона В собрана", "Готовый кабель"]);
    expect(cards.slice(0, 3).map((card) => card.ops.map((op) => op.position))).toEqual([[1], [2], [3]]);
    expect(cards.flatMap((card) => card.ops).map((op) => op.position)).toEqual(ops.map((op) => op.position));
    expect(route.edges.filter((edge) => ["b1", "b2", "b3"].includes(edge.to))).toEqual([]);
    expect(route.edges).toContainEqual({ from: "b2", to: "b17", per_unit: 1 });
    expect(route.edges).toContainEqual({ from: "b3", to: "b4", per_unit: 1 });
  });
  it("blocks a missing allowance and accepts an explicit zero", () => {
    const data = freshData(), state = seededState(data);
    const coax = { ...state.coax, lPlusB: "" };
    expect(makeGen(data, { ...state, coax }).validate().error).toMatch(/припуски L\+/);
    expect(coaxCutLength({ ...coax, lPlusB: "0" })).toBe(4830);
    expect(coaxCutLength({ ...coax, lengthBasis: "cut", len: "4800" })).toBe(4800);
  });
  it("tape protection is optional and tube retains print/cut/mount/shrink", () => {
    const data = freshData(), state = seededState(data);
    const ops = (patch: Partial<GenState["coax"]>) => makeGen(data, { ...state, coax: { ...state.coax, ...patch } }).getActiveOps();
    expect(ops({ markProtect: false }).map((op) => op.opKey)).not.toContain("coaxProtectCut");
    expect(ops({ markProtect: false }).map((op) => op.opKey)).not.toContain("coaxHeat");
    expect(ops({ markProtect: false }).map((op) => op.opKey)).toContain("coaxInsLabel");
    const tube = ops({ markMaterial: "tube" });
    expect(tube.find((op) => op.opKey === "coaxCutTut")?.tmplHint).toBe("PRINT_MARK_TUBE");
    expect(tube.map((op) => op.opKey)).toContain("coaxHeat");
    expect(tube.map((op) => op.opKey)).not.toContain("coaxInsLabel");
  });
  it.each(["paint", "laser", "tape", "none"] as const)("colour method %s controls the exact operation", (method) => {
    const data = freshData(), state = seededState(data);
    const ops = makeGen(data, { ...state, coax: { ...state.coax, connColor: { ...state.coax.connColor!, method } } }).getActiveOps();
    const color = ops.find((op) => op.type === "coaxConnColor");
    if (method === "none") expect(color).toBeUndefined();
    else expect(color?.tmplHint).toBe(`MARK_CONN_${method.toUpperCase()}`);
  });
  it("conflicting colour words or articles require a choice", () => {
    expect(coaxMarkDefaults([{ art: "151437", name: "Маркер-краска розовый" }]).conflict).toBe(true);
    const data = freshData();
    data.components.push({ ...c("Материал", "151986", 1), name: "Маркер-краска Brauberg" });
    const state = seededState(data);
    expect(state.coax.connColor?.color).toBe("");
    state.coax.finishedLengthFrom = "user";
    expect(makeGen(data, state).validate().error).toMatch(/несколько цветов/);
    expect(coaxMarkDefaults([{ name: "Маркер-краска синий" }]).connColor.code).toBe("BU");
  });

});


describe("FAKRA restoration and material validation", () => {
  it.each(["name", "calc", "bom"] as const)("keeps %s finished-length provenance and adds allowances once", (source) => {
    const data = freshData();
    if (source === "name") data.assemblyInfo["Наименование"] += " 4800 мм";
    if (source !== "bom") data.calcOps = [{ code: "CUT_COAX_auto", comp: "RTK 031", n: "1", l: "4,855", req: "" }];
    const state = seededState(data), length = source === "calc" ? 4855 : 4800;
    expect(state.coax).toMatchObject({ finishedLength: String(length), finishedLengthFrom: source });
    for (let i = 0; i < 2; i++) {
      const result = makeGen(data, state).validate();
      if (source === "name") {
        expect(result.error).toBe("");
        expect(result.cfg.coax).toMatchObject({ cableLength: length + 55, finishedLengthFrom: source });
      } else {
        expect(result.error).toBe(`L взята из ${source === "calc" ? "расчёта" : "состава"} — подтвердите длину готового изделия по КД (без запаса на разъёмы)`);
        expect(result.cfg).toEqual({});
      }
      state.coax.len = String(length + 55);
    }
    state.coax.finishedLengthFrom = "user";
    for (let i = 0; i < 2; i++) {
      const result = makeGen(data, state).validate();
      expect(result.error).toBe("");
      expect(result.cfg.coax).toMatchObject({ cableLength: length + 55, finishedLength: length, finishedLengthFrom: "user" });
    }
    expect(makeGen(data, state).validate().warnings.join("\n")).not.toContain("L взята");
  });
  it.each(["М.", "м", "рулон"])("ignores BOM tape with unit %s and counts only printed labels", (unit) => {
    const data = freshData(), tape = data.components.find((c) => c.art.includes("Vell"))!;
    tape.qty = "0,18"; tape.unit = unit;
    const state = seededState(data);
    state.coax.finishedLengthFrom = "user";
    const result = makeGen(data, state).validate();
    expect(result.error).toBe("");
    expect((result.cfg.ops as any[]).find((op) => op.opKey === "coaxPrintLabel")).toMatchObject({
      units: 3,
    });
    expect((result.cfg.ops as any[]).find((op) => op.opKey === "coaxPrintLabel")).not.toHaveProperty("parts");
    expect(result.warnings.join("\n")).toContain("Лента маркировки в составе не расходуется");
  });
  it.each(["fakra", "ordinary"])("blocks every enabled marking card on %s with cut-strip disabled", (family) => {
    for (const code of ["PRINT_MARK_LABEL", "INS_MARK_LABEL", "CUT_TUT_short", "INS_TUT", "HEAT_TYT_short", "MARK_CONN_PAINT", "MARK_CONN_LASER", "MARK_CONN_TAPE"]) {
      const data = freshData(), state = seededState(data);
      state.coax.fakraCutStrip = false;
      state.coax.finishedLengthFrom = "user";
      if (family === "ordinary") {
        state.coax.sideA = { art: "SMA", name: "SMA" }; state.coax.sideB = { art: "SMA", name: "SMA" };
      }
      if (code.startsWith("MARK_CONN_")) state.coax.connColor!.method = code.slice(10).toLowerCase() as "paint" | "laser" | "tape";
      const active = makeGen(data, state).getActiveOps();
      expect(active.some((op) => op.tmplHint === code)).toBe(true);
      // A stale manual selection must not substitute a generic tube card for label printing.
      const key = active.find((op) => op.tmplHint === code)!.opKey;
      state.selectedTemplates[key] = "PRINT_MARK_TUBE"; state.manualTemplates.push(key);
      data.templates = data.templates.filter((t) => !t.title.startsWith(`${code} |`));
      expect(makeGen(data, state).validate().error).toContain(code);
    }
  });
  it("prints tape labels without any BOM tape or consumption norm", () => {
    const data = freshData(), state = seededState(data);
    state.coax.finishedLengthFrom = "user";
    data.components = data.components.filter((row) => !row.art.includes("Vell"));
    const result = makeGen(data, state).validate();
    expect(result.error).toBe("");
    expect((result.cfg.ops as any[]).find((op) => op.opKey === "coaxPrintLabel")).not.toHaveProperty("parts");
  });
  it("restoration helper prevents a fresh seed from enabling new settings on a legacy snapshot", () => {
    const data = freshData(), seed = seededState(data), saved = initState(data);
    const restored = { ...saved, coax: restoreCoaxState(seed.coax, saved.coax) };
    expect(makeGen(data, restored).buildConfig()).toEqual(makeGen(data, saved).buildConfig());
    expect(restoreCoaxState(seed.coax, seed.coax)).toEqual(seed.coax);
  });
  it("requires protective tube but not printer tape in the BOM", () => {
    const data = freshData(), seed = seededState(data);
    seed.coax.finishedLengthFrom = "user";
    const withoutTape = { ...data, components: data.components.filter((c) => !c.art.includes("Vell")) };
    expect(makeGen(withoutTape, seed).validate().error).toBe("");
    const withoutClear = { ...data, components: data.components.filter((c) => !c.name.includes("прозрач")) };
    expect(makeGen(withoutClear, seed).validate().error).toMatch(/нет прозрачной ТУТ/);
    const cfg = makeGen(withoutClear, seed).buildConfig() as any;
    expect(cfg.coax.tutArt).toBe("");
  });
  it("supports connector colour on ordinary coax and requires its exact card", () => {
    const data = freshData();
    const state = seededState(data);
    const coax = { ...state.coax, sideA: { art: "SMA", name: "SMA" }, sideB: { art: "SMA", name: "SMA" },
      lengthBasis: "cut" as const, markMaterial: undefined, marks: undefined };
    const color = makeGen(data, { ...state, coax }).getActiveOps().find((op) => op.type === "coaxConnColor");
    expect(color?.units).toBe(2);
    const missing = { ...data, templates: data.templates.filter((t) => t.id !== "MARK_CONN_PAINT") };
    expect(makeGen(missing, { ...state, coax }).validate().error).toContain("MARK_CONN_PAINT");
  });
});

function batchAData(b: BatchBom): GenData {
  const refs = { ...REFS, templates: [...REFS.templates, ...NEW_CODES.map((code) => T(code, code)),
    ...erpLibrary.templates.filter((card) => card.title.startsWith("PRS_HFM_SUPPORT |"))],
    coax: REFS.coax.map((row) => ({ ...row, "L+": "25" })) };
  return { ...generatorData(refsFromRecords(refs), b), projectNo: "708К." };
}

describe("multi-channel coax quantities", () => {
  it.each([
    [{ "Количество каналов": "6" }, 6],
    [{ "Наименование": "Корпус на 4 канала с контактами" }, 4],
    [{ "Примечание": "Комплект: 2 канала; на каждый кабель свой контакт" }, 2],
    [{ "Тип/Серия": "HFM" }, undefined],
    [{ "Количество каналов": "2.5" }, undefined],
  ])("reads explicit channel data %j", (fields, expected) => {
    expect(coaxFromFields(fields).channels).toBe(expected);
  });

  it.each([2, 4, 6])("counts %i cables from references, preserving housing and connector kit counts", (channels) => {
    const data = batchAData(KB3);
    data.coaxRecords = data.coaxRecords.map((record) => ({ ...record, channels }));
    const state = seededState(data);
    const gen = makeGen(data, state);
    const ops = gen.getActiveOps();
    expect(ops[0].units! * 15).toBe(channels * 15);
    for (const side of ["A", "B"]) {
      for (const type of ["coaxSupportCrimp", "coaxPinCrimp", "coaxShield"]) {
        expect(ops.find((op) => op.type === type && op.side === side)?.units! * 15).toBe(channels * 15);
      }
      expect(ops.find((op) => op.opKey === `coaxInsert${side}`)?.units).toBe(channels);
      expect(ops.find((op) => op.opKey === `coaxBody${side}`)?.units ?? 1).toBe(1);
      expect(ops.find((op) => op.opKey === `coaxPinCrimp${side}`)?.parts?.[0].qty).toBe(1);
    }
    for (const type of ["coaxCutTut", "coaxInsTut", "coaxHeat"]) {
      expect(ops.find((op) => op.type === type)?.units).toBe(3 * channels);
    }
    expect(ops.find((op) => op.type === "coaxVswr")?.units ?? 1).toBe(1);
    const config = gen.buildConfig();
    expect((config.coax as any).sideA.channels).toBe(channels);
  });

  it("counts cable labels separately from a label on the complete assembly", () => {
    const data = batchAData(KB3), state = seededState(data);
    state.coax.marks![0].scope = "assembly";
    expect(makeGen(data, state).getActiveOps().find((op) => op.type === "coaxCutTut")?.units).toBe(9);
  });

  it("applies the same cable counts to a generic multi-channel connector", () => {
    const data = batchAData(KB3), state = seededState(data);
    state.coax.sideA = { art: "GEN-2", name: "Двухканальный разъём" };
    state.coax.sideB = { art: "GEN-2", name: "Двухканальный разъём" };
    state.coax.cables = 2;
    state.coax.stripMode = "separate";
    const ops = makeGen(data, state).getActiveOps();
    for (const op of ops.filter((op) => ["coaxCut", "coaxStrip", "coaxPinCrimp"].includes(op.type))) expect(op.units).toBe(2);
    expect(ops.find((op) => op.type === "coaxInsSleeve")?.units).toBe(4);
    expect(ops.find((op) => op.type === "coaxInsSleeve")?.label).toContain("×4");
    expect(ops.find((op) => op.type === "coaxShield")?.units).toBe(4);
    expect(ops.find((op) => op.type === "coaxShield")?.label).toContain("×4");
    expect(ops.find((op) => op.type === "coaxCutTut")?.units).toBe(4);
    expect(ops.find((op) => op.type === "coaxHousing")?.units ?? 1).toBe(1);
  });

  it("divides generic connector cable metres by its channels when no length is named", () => {
    const data = batchAData(KB3);
    data.components = data.components.map((comp) => comp.type === "Разъем" ? { ...comp, art: "GEN-4", name: "GEN-4" } : comp);
    data.coaxRecords = [{ ...data.coaxRecords[3], article: "GEN-4", connArticle: "GEN-4", channels: 4 }];
    const state = seededState(data);
    expect(state.coax.cables).toBe(4);
    expect(state.coax.len).toBe("1250");
    expect(makeGen(data, state).getActiveOps()[0].units).toBe(4);
  });
});

describe("coax batch A contract", () => {
  it("passes each batch BOM's project to the same FRX seed as the form", () => {
    const refs = { ...REFS, templates: [...REFS.templates, ...NEW_CODES.map((code) => T(code, code))],
      coax: REFS.coax.map((row) => ({ ...row, "L+": "25" })) };
    const items = ["708К.", "709К."].map((project) => ({ key: project, bom: {
      ...KB1, project, name: `${KB1.name} 4800мм`, components: KB1.components.filter((c) => c.type !== "Др"),
    } }));
    const results = runBatch({ refs, items }).items;
    results.forEach((result, i) => {
      expect(result.error).toBe("");
      const formData = { ...generatorData(refsFromRecords(refs), items[i].bom), projectNo: items[i].bom.project };
      expect(result.config).toEqual(seededConfig(formData).config);
      expect((result.config?.coax as any).marks[0].text).toBe(`FRX.${i ? "709К" : "708К"}.120907/AA.{№}`);
    });
  });
  it.each([KB3, KB28])("$index strips both ends at cut and uses exact end recipes", (bom) => {
    const data = batchAData(bom), state = seededState(data), gen = makeGen(data, state);
    const ops = gen.getActiveOps();
    expect(ops[0]).toMatchObject({ opKey: "coaxFakraCutStrip", stripSides: ["A", "B"], units: bom === KB3 ? 4 : 1 });
    expect(ops.filter((op) => op.type === "coaxStrip")).toEqual([]);
    const recipe = (side: "A" | "B") => ops.filter((op) => op.side === side).map((op) => [op.tmplHint, op.units ?? 1]);
    if (bom === KB3) {
      for (const side of ["A", "B"] as const) expect(recipe(side)).toEqual([
        ["PRS_HFM_SUPPORT", 4], ["PRS_HFM_PIN", 4], ["INS_HFM_CONTACT", 4], ["PRS_HFM_SHIELD", 4], ["INS_HFM_HOUSING", 1],
      ]);
      expect(ops.map((op) => op.opKey)).toEqual(["coaxFakraCutStrip", "coaxCutTut", "coaxInsTut",
        "coaxSupportCrimpA", "coaxPinCrimpA", "coaxInsertA", "coaxShieldA", "coaxBodyA",
        "coaxSupportCrimpB", "coaxPinCrimpB", "coaxInsertB", "coaxShieldB", "coaxBodyB", "coaxHeat", "coaxVswr"]);
    } else {
      expect(recipe("A")).toEqual([["INS_FAKRA_BH_PREP", 1], ["SDR_FAKRA_BH_PIN", 1], ["INS_FAKRA_BH_CONTACT", 1], ["PRS_FAKRA_BH_SHIELD", 1], ["HEAT_FAKRA_BH_TTK", 1]]);
      expect(recipe("B")).toEqual(DEDICATED_CODES.straight.filter((code) => code !== "STRIP_FAKRA_ST").map((code) => [code, 1]));
      expect(ops.find((op) => op.opKey === "coaxInsSleeveA")?.parts).toContainEqual({ art: "ТТК 6/2мм, черная", name: "ТТК 6/2мм, черная", qty: 0.06, unit: "М." });
    }
    expect(gen.validate().error).toContain("подтвердите длину");
    state.coax.finishedLengthFrom = "user";
    expect(makeGen(data, state).validate().error).toBe("");
    const cfg = makeGen(data, state).buildConfig() as any;
    const routeOps: RouteOp[] = cfg.ops.map((op: any, index: number) => ({ position: index + 1, key: op.opKey, type: op.type, code: op.templateId, side: op.side, label: op.label, minutes: 0 }));
    const blocks = genCards(deriveRoute(routeOps), routeOps).cards;
    expect(blocks.find((block) => block.title === "Сторона А собрана")!.ops[0].key).toBe(bom === KB3 ? "coaxSupportCrimpA" : "coaxPinA");
    expect(blocks.find((block) => block.title === "Сторона В собрана")!.ops[0].key).toBe(bom === KB3 ? "coaxSupportCrimpB" : "coaxPinCrimpB");
  });

  it("requires the dedicated support card instead of a generic sleeve card", () => {
    const data = batchAData(KB3), state = seededState(data);
    data.templates = data.templates.filter((card) => !card.title.startsWith("PRS_HFM_SUPPORT |"));
    state.coax.finishedLengthFrom = "user";
    expect(makeGen(data, state).validate().error).toContain("PRS_HFM_SUPPORT");
  });

  it("sums КБ4's two 0.09 m tube rows and two 1.5 marking rows", () => {
    const b = { ...KB3, index: "КБ4", components: [c("Разъем", "AMK11D-1M4Z5-Z", 1), c("Разъем", "AMK11D-1M4Z5-Z", 1),
      c("ТУТ", "ТУТ 4/2мм, прозрачная", "0,09"), c("ТУТ", "ТУТ 4/2мм, прозрачная", "0.09"),
      c("Маркировка", "Маркировка", 1.5), c("Маркировка", "Маркировка", 1.5), c("Кабель", "RTK 031", 5)] };
    const data = batchAData(b), state = seededState(data), before = JSON.stringify(data.components);
    expect(state.coax.tutLen).toBe("60");
    expect(makeGen(data, state).getActiveOps().find((op) => op.opKey === "coaxCutTut")?.units).toBe(12);
    data.components.push(c("Материал", "Лента Vell VL-S641", 0.18));
    expect(coaxReseedPatch(data, state).tutLen).toBe("60");
    expect(JSON.stringify(data.components.slice(0, -1))).toBe(before);
  });

  it("sums identical TTK rows only in new recipes and keeps incompatible units separate", () => {
    const data = batchAData({ ...KB28, components: [...KB28.components, c("ТТК", "ТТК 6/2мм, черная", "0,06")] });
    const fresh = seededState(data);
    const qty = (state: GenState) => makeGen(data, state).getActiveOps().find((op) => op.opKey === "coaxInsSleeveA")!.parts![0].qty;
    expect(qty(fresh)).toBe(0.12);
    expect(qty(initState(data))).toBe(0.06);
    const rows = [{ art: "tube", qty: 0.09, unit: "М." }, { art: "tube", qty: 0.09, unit: "м" }, { art: "tube", qty: 3, unit: "шт" }, { art: "other", qty: 2, unit: "м" }];
    expect(sumCoaxMaterial(rows, rows[0])!.qty).toBe(0.18);
    expect(sumCoaxMaterial(rows, rows[2])!.qty).toBe(3);
  });

  it("seeds FRX with the customer PN, strips the project's trailing dot and expands existing item serials", () => {
    const text = coaxMarksFromName("122664/AA 120907/AA Camera 14 → Splitter B", "708К.")[0].text;
    expect(text).toBe("FRX.708К.120907/AA.{№}");
    expect(hasMarkSerial(text)).toBe(true);
    expect(expandMarkSerial(text, 7)).toBe("FRX.708К.120907/AA.7");
    expect(markSerialList(text, { first: 1, last: 15 })).toHaveLength(15);
    expect(seededState(batchAData(KB3)).coax.marks![0].text).toBe("FRX.708К.120961/AA.{№}");
    expect(coaxMarksFromName(KB11.name, "708К.")[0].text).toBe("FRX.708К.121538AA.{№}");
  });

  it.each(COAX_COLORS)("recognises marker $code from ordinary and paint marker names", ({ code, color }) => {
    for (const prefix of ["Маркер", "Маркер-краска"]) expect(coaxMarkDefaults([{ name: `${prefix} ${color}` }]).connColor).toMatchObject({ code, color, method: "paint" });
  });
  it("recognises blue aliases and both Brauberg articles and does not choose a conflicting colour", () => {
    for (const name of ["Маркер голубой", "Маркер синий"]) expect(coaxMarkDefaults([{ name }]).connColor.code).toBe("BU");
    expect(coaxMarkDefaults([{ art: "151437" }]).connColor.code).toBe("OG");
    expect(coaxMarkDefaults([{ art: "151986" }]).connColor.code).toBe("PK");
    expect(coaxMarkDefaults([{ name: "Маркер розовый Оранжевый" }])).toMatchObject({ conflict: true, connColor: { color: "", code: "", art: "" } });
  });

  it("reseed is an explicit patch and leaves unrelated form settings intact", () => {
    const data = batchAData(KB3), state = seededState(data);
    state.coax = { ...state.coax, vswrMode: "sample", vswrEvery: "13", machine: "big", pinA: "manual", lPlusA: "99" };
    state.note = "Ручная правка"; state.times = { coaxPinCrimpA: { tmpl: "manual", tPrep: "9", tOp: "8", tMachine: "7" } };
    const before = structuredClone(state), patch = coaxReseedPatch(data, state);
    expect(state).toEqual(before);
    expect(patch.lPlusA).toBe("25");
    expect(patch).not.toHaveProperty("vswrMode"); expect(patch).not.toHaveProperty("machine"); expect(patch).not.toHaveProperty("sideA");
    const result = { ...state, coax: { ...state.coax, ...patch } };
    expect(result.times).toBe(state.times); expect(result.note).toBe(state.note);
    expect(result.coax).toMatchObject({ machine: "big", vswrMode: "sample", vswrEvery: "13", pinA: "manual" });
  });
});

it.each([KB3, KB28])("$index matches the shared batch A ETK fixture", (bom) => {
  const data = batchAData(bom), state = seededState(data); state.coax.finishedLengthFrom = "user";
  const cfg = makeGen(data, state).buildConfig() as any;
  const expected = structuredClone(batchAConfigs.find((item) => item.kb === bom.index)!.config) as any;
  expected.ops = [...expected.ops.filter((op: any) => op.type !== "coaxConnColor"), ...expected.ops.filter((op: any) => op.type === "coaxConnColor")];
  if (bom === KB3) {
    expected.coax.sideA.channels = expected.coax.sideB.channels = 4;
    for (const op of expected.ops) if (["coaxCutTut", "coaxInsTut", "coaxHeat"].includes(op.opKey)) {
      op.label = op.label.replace("×3", "×12");
      op.units = 12;
    }
  }
  expect(withoutShrinkMaterials(cfg)).toEqual(expected);
  if (bom.index === "КБ28") expect(cfg.ops.find((op: any) => op.opKey === "coaxHeatTtkA").shrinkMaterials)
    .toEqual([{ art: "ТТК 6/2мм, черная", name: "ТТК 6/2мм, черная" }]);
  expect(cfg.ops.find((op: any) => op.opKey === "coaxHeat").shrinkMaterials)
    .toEqual([{ art: cfg.coax.tutArt, name: cfg.coax.tutName }]);
});

it("re-seeding HFM preserves B-before-A without placing support crimp after B assembly", () => {
  const data = batchAData(KB3), saved = initState(data);
  const old = makeGen(data, saved).getActiveOps();
  saved.opOrder = [...old.filter((op) => !op.side && op.type !== "coaxHeat" && op.type !== "coaxVswr"),
    ...old.filter((op) => op.side === "B"), ...old.filter((op) => op.side === "A"),
    ...old.filter((op) => !op.side && (op.type === "coaxHeat" || op.type === "coaxVswr"))].map((op) => op.opKey);
  const order = [...saved.opOrder];
  const state = { ...saved, coax: { ...saved.coax, ...coaxReseedPatch(data, saved) } };
  const ops = makeGen(data, state).getActiveOps();
  expect(state.opOrder).toEqual(order);
  expect(ops.filter((op) => op.side).map((op) => op.opKey)).toEqual([
    "coaxSupportCrimpB", "coaxPinCrimpB", "coaxInsertB", "coaxShieldB", "coaxBodyB",
    "coaxSupportCrimpA", "coaxPinCrimpA", "coaxInsertA", "coaxShieldA", "coaxBodyA",
  ]);
});

it.each([KB3, KB28])("$index: a fresh seed and a later reseed agree on the marking-TUT length", (bom) => {
  const data = batchAData(bom), seeded = initState(data);
  const patch = coaxReseedPatch(data, seeded);
  if (patch.tutLen) expect(seeded.coax.tutLen).toBe(patch.tutLen);
});
