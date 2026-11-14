// @vitest-environment node
import { describe, expect, it } from "vitest";
import { autoConfig, generatorData, refsFromRecords, type BatchBom, type BatchRefs } from "./tcGenBatch";
import { fakraEndsFromBom_, initState, makeGen } from "./tcGenLogic";
import productionConfigs from "./__fixtures__/fakra708Configs.json";
import erpLibrary from "../../backend/data/techcard-library-erp.json";

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
    coaxRow("AMK11D-1M4Z5-Z", "HFM Quad", "13", "5,55", "2,1", "обжим"),
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
    id: operation.templateId, title: `${genericCode(operation)} | ${operation.label || operation.type} (${operation.side})`,
    category: operation.type === "coaxCapCrimp" ? "Опрессовка крышки коакс" : "",
  }])).values());
  const refs: BatchRefs = {
    ...REFS, templates: [...templates, ...(dedicated ? DEDICATED_CARDS : [])],
    op: [...config.ops.filter((o) => o.opNum).map((o) => ({ opNumber: o.opNum, opName: o.opNum, tPrep: o.tPrep, tOp: o.tOp, tMachine: o.tMachine })),
      ...(dedicated ? DEDICATED_OPS : [])],
    coax: sides.map((end) => ({ ...coaxRow(end.article, end.type, end.l1, end.l2, end.l3, end.pinType),
      "Провод": end.wire, "Производитель": end.mfr, "Тип экрана": end.shield })),
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
    const expected = fixture.config.ops.map((operation) => {
      const end = fixture.config.coax[operation.side === "B" ? "sideB" : "sideA"];
      if (!/^coaxInsSleeve[AB]$/.test(operation.opKey)) return operation;
      // PREP must not assert unavailable seal/cap fitting order, or HFM sleeve placement before partial stripping.
      if (end.kind === "straight") return { ...operation, label: operation.label!
        .replace("Надеть крышку, уплотнитель и гильзу", "Подготовить гильзу, уплотнитель и крышку") };
      return end.kind === "hfm" ? { ...operation, label: operation.label!.replace("Надеть", "Подготовить") } : operation;
    });
    expect(ops.map(preserved)).toEqual(expected.map(preserved));
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
      .toEqual(fixture.config.ops.filter((operation) => !operation.side).map(unchanged));
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

  it("КБ11 generator bytes stay unchanged with dedicated cards in the library", async () => {
    const fixture = fixtures.find((item) => item.kb === "КБ11")!;
    const config = autoConfig(productionData(fixture)).config;
    const bytes = new TextEncoder().encode(JSON.stringify(config));
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    expect(Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join(""))
      .toBe("73b3bcf31283d39f53241504c7c25bd1bdc870f20cac337ab2fda58894694ded");
    expect(config).toEqual(autoConfig(productionData(fixture, false)).config);
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
      "Разделка коаксиала (А)", "Обжим центрального контакта (А)", "Вставка во вставку до щелчка, контроль заглубления контакта 0,18 мм (А)",
      "Обжим гильзы экрана (А)", "Установка в корпус до щелчка (А)", "Уплотнитель и крышка (А)",
      "Подготовить гильзу, уплотнитель и крышку (В)",
      "Разделка коаксиала (В)", "Обжим центрального контакта (В)", "Вставка во вставку до щелчка, контроль заглубления контакта 0,18 мм (В)",
      "Обжим гильзы экрана (В)", "Установка в корпус до щелчка (В)", "Уплотнитель и крышка (В)",
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
      "Разделка коаксиала (А)", "Обжим центрального контакта (А)", "Вставка во вставку до щелчка, контроль заглубления контакта 0,18 мм (А)",
      "Обжим гильзы экрана (А)", "Установка в корпус до щелчка (А)", "Уплотнитель и крышка (А)",
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
      "Разделка коаксиала (В)", "Обжим центрального контакта (В)", "Вставка во вставку до щелчка, контроль заглубления контакта 0,18 мм (В)",
      "Обжим гильзы экрана (В)", "Установка в корпус до щелчка (В)", "Уплотнитель и крышка (В)",
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
      "Резка кабеля ×4", "Печать меток и резка ТУТ под маркировку ×3", "Монтаж ТУТ под маркировку ×3",
      "Подготовить опорные гильзы ×4 (А)",
      "Разделка коаксиала ×4 (А)", "Обжим центрального контакта ×4 (А)", "Вставка контактов в корпус до щелчка ×4 (А)",
      "Обжим гильзы экрана ×4 (А)", "Вторичная фиксация и CPA (А)",
      "Подготовить опорные гильзы ×4 (В)",
      "Разделка коаксиала ×4 (В)", "Обжим центрального контакта ×4 (В)", "Вставка контактов в корпус до щелчка ×4 (В)",
      "Обжим гильзы экрана ×4 (В)", "Вторичная фиксация и CPA (В)",
      "Усадка ТУТ под маркировку ×3", "Контроль КСВН",
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
