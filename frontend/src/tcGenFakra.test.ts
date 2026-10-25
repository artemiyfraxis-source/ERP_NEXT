// @vitest-environment node
import { describe, expect, it } from "vitest";
import { autoConfig, generatorData, refsFromRecords, type BatchBom, type BatchRefs } from "./tcGenBatch";
import { fakraEndsFromBom_, initState, makeGen } from "./tcGenLogic";

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
const op = (opNumber: string) => ({ opNumber, opName: opNumber, tOp: "10", tPrep: "60", tMachine: "1" });
const coaxRow = (art: string, type: string, l1: string, l2: string, l3: string, pin: string) => ({
  "Артикул": art, "Тип/Серия": type, "Провод": "RTK 031", "L1": l1, "L2": l2, "L3": l3, "Тип пина": pin, "Тип экрана": "обжим",
});
const REFS: BatchRefs = {
  templates: CARDS, op: ["CUT_COAX_auto", "PRINT_MARK_TUBE", "STRIP_COAX_auto"].map(op), ter: [], wire_dia: [],
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
      "Надеть на кабель: крышку, уплотнитель и гильзу ×2",
      "Разделка коаксиала (А)", "Обжим центрального контакта (А)", "Вставка во вставку до щелчка, контроль заглубления контакта 0,18 мм (А)",
      "Надвинуть гильзу и обжать (А)", "Вставка в корпус до щелчка (А)", "Уплотнитель и крышка (А)",
      "Разделка коаксиала (B)", "Обжим центрального контакта (B)", "Вставка во вставку до щелчка, контроль заглубления контакта 0,18 мм (B)",
      "Надвинуть гильзу и обжать (B)", "Вставка в корпус до щелчка (B)", "Уплотнитель и крышка (B)",
      "Усадка ТУТ под маркировку ×3", "Контроль КСВН",
    ]);
  });
  it("детали комплекта идут компонентами операций, оснастки нет", () => {
    const { config, error } = run(KB15);
    expect(error).toBe("");
    const ops = (config!.ops as Array<Record<string, unknown>>);
    const by = (key: string) => ops.find((o) => o.opKey === key)!;
    expect(by("coaxInsSleeve").parts).toEqual([{ art: "59K14B-1M4/50", name: "59K14B-1M4/50", qty: 2 }, { art: "59Z163-000/51", name: "59Z163-000/51", qty: 2 }]);
    expect(by("coaxPinCrimpA").parts).toEqual([{ art: "59K13o-1M4A4", name: "59K13o-1M4A4", qty: 1 }]);
    expect(by("coaxBodyB").parts).toEqual([{ art: "59Z163-003-Z", name: "59Z163-003-Z", qty: 1 }]);
    expect(by("coaxPinCrimpA").noTooling).toBe(true);
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
    expect(labels(KB1).slice(4)).toEqual([
      "Разделка коаксиала (А)", "Обжим центрального контакта (А)", "Вставка во вставку до щелчка, контроль заглубления контакта 0,18 мм (А)",
      "Надвинуть гильзу и обжать (А)", "Вставка в корпус до щелчка (А)", "Уплотнитель и крышка (А)",
      "Разделка коаксиала (B)", "Обжим центрального контакта (B)", "Вставка в корпус, контроль пина в окне (B)",
      "Обжим гильзы (B)", "Запрессовать крышку (B)", "Корпус и вторичная фиксация (B)",
      "Усадка ТУТ под маркировку ×3", "Контроль КСВН",
    ]);
    expect(labels(KB1)[3]).toBe("Надеть на кабель: А — крышку, уплотнитель и гильзу; B — гильзу");
    const parts = (r.config!.ops as Array<{ opKey: string; parts?: unknown[] }>).find((o) => o.opKey === "coaxBodyB")!.parts;
    expect(parts).toEqual([{ art: "59Z006-T00-Z", name: "59Z006-T00-Z", qty: 1 }]);
  });
  it("КБ28: 2FA1 — ТТК, пайка жилы в пин, обжим гильзы, усадка ТТК; прямой FAKRA на другом конце", () => {
    const r = run(KB28);
    expect(r.S.coax.pinA).toBe("пайка");
    expect(r.S.coax.pinB).toBe("обжим");
    expect(labels(KB28)).toEqual([
      "Резка кабеля", "Печать меток и резка ТУТ под маркировку ×3", "Монтаж ТУТ под маркировку ×3",
      "Надеть на кабель: А — ТТК, гильзу и шайбу; B — крышку, уплотнитель и гильзу",
      "Разделка коаксиала (А)", "Пайка жилы в центральный контакт (А)", "Вставка контакта в корпус (А)", "Надвинуть гильзу и обжать (А)",
      "Разделка коаксиала (B)", "Обжим центрального контакта (B)", "Вставка во вставку до щелчка, контроль заглубления контакта 0,18 мм (B)",
      "Надвинуть гильзу и обжать (B)", "Вставка в корпус до щелчка (B)", "Уплотнитель и крышка (B)",
      "Усадка ТТК на гильзе (А)", "Усадка ТУТ под маркировку ×3", "Контроль КСВН",
    ]);
    const put = (r.config!.ops as Array<{ opKey: string; parts?: Array<{ art: string; qty: number; unit?: string }> }>).find((o) => o.opKey === "coaxInsSleeve")!;
    expect(put.parts!.find((p) => p.art.startsWith("ТТК"))).toMatchObject({ qty: 0.06, unit: "М." });
    expect(r.warnings.join("\n")).toMatch(/нет строки «Маркировка»/);
  });
  it("КБ3: HFM — четыре кабеля, контактные операции ×4, корпус один раз на конец", () => {
    const r = run(KB3);
    expect(r.S.coax.cables).toBe(4);
    expect(r.S.coax.len).toBe("1250");
    expect(labels(KB3)).toEqual([
      "Резка кабеля ×4", "Печать меток и резка ТУТ под маркировку ×3", "Монтаж ТУТ под маркировку ×3",
      "Надеть на кабель: опорные гильзы ×8",
      "Разделка коаксиала ×4 (А)", "Обжим центрального контакта ×4 (А)", "Вставка контактов в корпус до щелчка ×4 (А)",
      "Надвинуть гильзу и обжать ×4 (А)", "Вторичная фиксация и CPA (А)",
      "Разделка коаксиала ×4 (B)", "Обжим центрального контакта ×4 (B)", "Вставка контактов в корпус до щелчка ×4 (B)",
      "Надвинуть гильзу и обжать ×4 (B)", "Вторичная фиксация и CPA (B)",
      "Усадка ТУТ под маркировку ×3", "Контроль КСВН",
    ]);
    expect((r.config!.coax as Record<string, unknown>).cables).toBe(4);
    expect(r.warnings.join("\n")).toMatch(/Кабелей 4/);
  });
  it("операции без карты в библиотеке не уходят в «Создать», а предупреждение называет их", () => {
    const D = generatorData(refsFromRecords({ ...REFS, templates: CARDS.filter((t) => !t.id.startsWith("INS_CAP")) }), KB15);
    const r = autoConfig(D);
    expect((r.config!.ops as Array<{ opKey: string }>).some((o) => o.opKey.startsWith("coaxSealCap"))).toBe(false);
    expect(r.warnings.join("\n")).toMatch(/Нет карты операции «Уплотнитель и крышка \(А\)»/);
  });
});
