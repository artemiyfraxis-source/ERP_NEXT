import { joinSplice, assignPinJoined, setTail } from "./tcGenSplices";
import {
  addEndpoint, complexByHand, initState, setSplMethod, setTailMode, toggleLengthMode, updateEndpoint, type Comp, type GenData, type GenState,
  type OpRec, type SpliceMethod, type TerRec, type Tmpl, type WireEnd,
} from "./tcGenLogic";
import { newMarkRow } from "./tcGenMarks";

/*
 * Чертёж заказчика «Кабельная сборка Жгут НВ4» (КБ1, 500 шт, владелец 13.10): сторона А — «5» SMR-02V-B, «I3» SMR-02V-B,
 * «C» SMP-02V-BC, «A6» SMR-04V-B; сторона В — I-DS1071-SCB01X3 и I-DS1071-SCB02X3. Красный 0,5 с «5»-1 в гильзе
 * расходится на два красных 0,2 к SCB01X3-1/-2 и хвост 60 мм; чёрный 0,5 с «5»-2 — к SCB01X3-3, SCB02X3-2 (180 мм) и
 * хвост; белые 0,12 с I3-1 и C-1 сходятся в хвост 60 мм, синие так же; A6 — три провода 0,2 по 330 мм на SCB02X3.
 * Форма собирается так, как технолог её набирает руками: разъём → его терминал сам, провода, контакты, сростки, хвосты.
 */
const C = (id: string, type: string, art: string, qty: number): Comp => ({ id, type, name: art, art, qty });
const COMPONENTS: Comp[] = [
  C("c01", "Соед", "I-DS1071-SCB01X3", 1), C("c02", "Соед", "I-DS1071-SCB02X3", 1), C("tds", "Терминал", "T-DS1071-SC600", 7),
  C("csmp", "Соед", "SMP-02V-BC", 1), C("tshf", "Терминал", "SHF-001T-0.8BS", 2), C("csmr2", "Соед", "SMR-02V-B", 2),
  C("csmr4", "Соед", "SMR-04V-B", 1), C("tsym", "Терминал", "SYM-001T-P0.6", 7),
  C("r5", "Провод", "НВ4 0,5 красный", 0.16), C("k5", "Провод", "НВ4 0,5 черный", 0.16), C("r2", "Провод", "НВ4 0,2 красный", 0.43),
  C("k2", "Провод", "НВ4 0,2 черный", 0.43), C("b12", "Провод", "НВ4 0,12 синий", 0.38), C("w12", "Провод", "НВ4 0,12 белый", 0.38),
  C("w2", "Провод", "НВ4 0,2 белый", 0.32), C("y2", "Провод", "НВ4 0,2 желтый", 0.32), C("g2", "Провод", "НВ4 0,2 зеленый", 0.32),
  C("ttk3", "ТТК", "ТТК 3/1мм, черная", 0.06), C("mk5", "Маркировка", "Маркировка \"5\" 4/2мм", 1),
];
const ter = (article: string, series: string, lPlus = "5"): TerRec => ({
  article, step: "2,5", strip: "2,5", lPlus, lMinus: "", applicator: "", awgFrom: "", awgTo: "", mm2From: "", mm2To: "",
  fields: { terArticle: article, terSeries: series },
});
const base = { assemblyInfo: { "Индекс": "КБ1", "Наименование": "Кабельная сборка Жгут НВ4", "Кол-во": "500" }, templates: [], ops: [], coaxRecords: [],
  wireDia: null, coaxCableDia: null, calcOps: [] };
// БД.ТЕР как в рабочей базе (T4, 13.10): «Series» — начала артикулов корпусов; у SHF — только «SM», артикул строчными.
export const D: GenData = { ...base, components: COMPONENTS, terRecords: [ter("SYM-001T-P0.6", "SMR, SYP"), ter("T-DS1071-SC600", "I-DS1071-SCB01, I-DS1071-SCB02"), ter("shf-001T-0.8BS", "SM")] };
// Тот же состав без записей БД.ТЕР: терминал — по семейству артикулов и по количеству в составе.
export const BARE: GenData = { ...base, components: COMPONENTS, terRecords: [] };
// Состав КД целиком (владелец 16.10): гильзы сростков НШВ (тип «Др»), термоусадка ТТК трёх размеров, оплётка XP-3 — и
// карты операций сростка, лужения и зачистки из библиотеки.
const SLEEVES: Comp[] = [
  C("nshv10", "Др", "НШВ 1,0-8", 2), C("nshv05", "Др", "НШВ 0,5-8", 2),
  C("ttk8", "ТТК", "ТТК 8/2мм, черная", 0.1), C("ttk12", "ТТК", "ТТК 12/4мм, черная", 0.05), C("xp3", "Др", "XP-3", 0.3),
];
export const TEMPLATES: Tmpl[] = ["SDR_W_W | Пайка", "PRS_НШВИ | Опрессовка наконечников", "PRS_COPPER | Ленточный обжим", "SDR_tin | Лужение",
  "STRIP_WIRE | Разделка"].map((title, i) => ({ id: `t${i}`, title, category: title.split("|")[1].trim() }));
export const tmplId = (code: string) => TEMPLATES.find((t) => t.title.startsWith(`${code} |`))!.id;
export const DS: GenData = { ...D, components: [...COMPONENTS, ...SLEEVES], templates: TEMPLATES };

const ROWS: Array<[string, number, number]> = [
  ["r5", 150, 1], ["k5", 150, 1], ["r2", 160, 2], ["r2", 60, 1], ["k2", 160, 1], ["k2", 180, 1], ["k2", 60, 1],
  ["w12", 150, 2], ["w12", 60, 1], ["b12", 150, 2], ["b12", 60, 1], ["w2", 330, 1], ["g2", 330, 1], ["y2", 330, 1],
];
export const R = { r5: 0, k5: 1, r2: 2, r2t: 3, k2a: 4, k2b: 5, k2t: 6, w12: 7, w12t: 8, b12: 9, b12t: 10, w2: 11, g2: 12, y2: 13 };

export const id = (S: GenState, side: "A" | "B", n: number) => S.endpoints.filter((e) => e.side === side)[n - 1].id;
export const end = (idx: number, unit: number, side: "A" | "B"): WireEnd => ({ idx, unit, side });

/** Технолог набирает жгут: разъёмы (терминал подбирается сам), кабели с длинами, контакты, сростки и хвосты. */
export function harness(data: GenData): GenState {
  let S = complexByHand(initState(data));
  // Сторона А: «5», I3, C, A6; сторона В: SCB01X3, SCB02X3.
  S = addEndpoint(addEndpoint(addEndpoint(addEndpoint(S, "A"), "A"), "A"), "B");
  const conn: Array<[string, string, string]> = [
    [id(S, "A", 1), "csmr2", "5"], [id(S, "A", 2), "csmr2", "I3"], [id(S, "A", 3), "csmp", "C"], [id(S, "A", 4), "csmr4", "A6"],
    [id(S, "B", 1), "c01", ""], [id(S, "B", 2), "c02", ""],
  ];
  conn.forEach(([ep, connId, label]) => { S = updateEndpoint(data, S, ep, { connId }); S = updateEndpoint(data, S, ep, { label }); });
  // Кабели: каждый отрезок — своя строка с длиной по чертежу.
  S = toggleLengthMode({ ...S, wires: ROWS.map(([compId, length, qty], idx) => ({ idx, compId, length, qty })), nextWireIdx: ROWS.length }, 0);
  const pin = (ep: string, idx: number, unit: number, p: number) => { S = assignPinJoined(data, S, ep, idx, unit, p); };
  const [a1, a2, a3, a4] = [1, 2, 3, 4].map((n) => id(S, "A", n)), [b1, b2] = [1, 2].map((n) => id(S, "B", n));
  pin(a1, R.r5, 0, 1); pin(a1, R.k5, 0, 2);
  pin(a2, R.w12, 0, 1); pin(a2, R.b12, 0, 2); pin(a3, R.w12, 1, 1); pin(a3, R.b12, 1, 2);
  pin(a4, R.w2, 0, 2); pin(a4, R.g2, 0, 3); pin(a4, R.y2, 0, 4);
  pin(b1, R.r2, 0, 1); pin(b1, R.r2, 1, 2); pin(b1, R.k2a, 0, 3);
  pin(b2, R.k2b, 0, 2); pin(b2, R.y2, 0, 3); pin(b2, R.g2, 0, 4); pin(b2, R.w2, 0, 5);
  // Сростки: красный, чёрный, белый, синий; хвосты 60 мм — свободным концом на стороне В.
  const splice = (first: WireEnd, ...rest: WireEnd[]) => {
    S = joinSplice(data, S, first, "new");
    const sid = S.splices!.at(-1)!.id;
    rest.forEach((e) => { S = joinSplice(data, S, e, sid); });
  };
  splice(end(R.r5, 0, "B"), end(R.r2, 0, "A"), end(R.r2, 1, "A"), end(R.r2t, 0, "A"));
  splice(end(R.k5, 0, "B"), end(R.k2a, 0, "A"), end(R.k2b, 0, "A"), end(R.k2t, 0, "A"));
  splice(end(R.w12, 0, "B"), end(R.w12, 1, "B"), end(R.w12t, 0, "A"));
  splice(end(R.b12, 0, "B"), end(R.b12, 1, "B"), end(R.b12t, 0, "A"));
  [R.r2t, R.k2t, R.w12t, R.b12t].forEach((idx) => { S = setTail(data, S, end(idx, 0, "B")); });
  return S;
}

// ── 713К.4 КБ1 целиком (владелец 16.10) ─────────────────────────────────────────────────────────────────────────────
// Маркировка трубками «Маркировка "<надпись>" 4/2мм» у разъёмов стороны А (длина по КД 10–15 мм), аппликаторы автомата
// и сечения терминалов как в рабочей БД.ТЕР (SYM — A7, 0,08–0,35 мм²; SHF — A5; T-DS1071 — A14, 0,08–0,2 мм²),
// Ø проводов НВ-4, карты операций библиотеки.
const MARK_COMPS: Comp[] = ["I3", "C", "A6"].map((text) => C(`mk${text}`, "Маркировка", `Маркировка "${text}" 4/2мм`, 1));
const terFull = (article: string, series: string, applicator: string, mm2From: string, mm2To: string): TerRec =>
  ({ ...ter(article, series), applicator, mm2From, mm2To });
// Карты библиотеки с их id (backend/data/techcard-library.json): конфиг сборки годится серверу как есть
// (backend/tests/techcards/generator_713k4_config.json).
export const FULL_TEMPLATES: Tmpl[] = ([
  ["cut-wire-auto-резка", "CUT_WIRE_auto | Резка", "Резка"],
  ["cut-wire-1crimp-автомат-резка-опрессовка", "CUT_WIRE+1crimp | Автомат резка + опрессовка", "Автомат резка + опрессовка"],
  ["cut-wire-2crimp-автомат-резка-опрессовка", "CUT_WIRE+2crimp | Автомат резка + опрессовка", "Автомат резка + опрессовка"],
  ["cut-tut-short-резка", "CUT_TUT_short | Резка", "Резка"], ["ins-tut-монтаж", "INS_TUT | Монтаж", "Монтаж"],
  ["heat-tyt-short-усадка", "HEAT_TYT_short | Усадка", "Усадка"], ["heat-ttk-short-усадка", "HEAT_TTK_short | Усадка", "Усадка"],
  ["ins-braid-монтаж-оплётки", "INS_BRAID | Монтаж оплётки", "Монтаж оплётки"], ["twist-свивка", "TWIST | Свивка", "Свивка"],
  ["sdr-w-w-пайка", "SDR_W_W | Пайка", "Пайка"], ["prs-ншви-опрессовка-наконечников", "PRS_НШВИ | Опрессовка наконечников", "Опрессовка наконечников"],
  ["prs-copper-ленточный-обжим", "PRS_COPPER | Ленточный обжим", "Ленточный обжим"], ["us-weld-уз-сварка", "US_WELD | УЗ сварка", "УЗ сварка"],
  ["strip-wire-разделка", "STRIP_WIRE | Разделка", "Разделка"], ["sdr-tin-лужение", "SDR_tin | Лужение", "Лужение"],
  ["cut-wire-1strip-резка-с-разделкой", "CUT_WIRE+1strip | Резка с разделкой", "Резка с разделкой"],
  ["cut-wire-2strip-резка-с-разделкой", "CUT_WIRE+2strip | Резка с разделкой", "Резка с разделкой"],
  ["cut-wire-2strip-tin-резка-с-зачисткой-двух-концов-и-лужением", "CUT_WIRE+2strip+tin | Резка с зачисткой двух концов и лужением", "Автомат резка + опрессовка"],
  ["ins-mark-tube-auto-печать-и-резка-маркировочной-трубки", "INS_MARK_TUBE_AUTO | Печать и резка маркировочной трубки", "Монтаж маркировки"],
  ["cut-neylon-резка-линейных-материалов", "CUT_NEYLON | Резка линейных материалов", "Резка линейных материалов"],
  ["prs-terminal-auto-опрессовка-терминалов-a", "PRS_TERMINAL_auto | Опрессовка терминалов (A)", "Опрессовка терминалов"],
  ["prs-terminal-auto-опрессовка-терминалов-b", "PRS_TERMINAL_auto | Опрессовка терминалов (B)", "Опрессовка терминалов"],
  ["ins-terminal-2-54-6-3-монтаж-терминалов-a", "INS_terminal_2,54-6,3 | Монтаж терминалов (A)", "Монтаж терминалов"],
  ["ins-terminal-2-54-6-3-монтаж-терминалов-b", "INS_terminal_2,54-6,3 | Монтаж терминалов (B)", "Монтаж терминалов"],
] as const).map(([id, title, category]) => ({ id, title, category }));
// Строки БД.ОП новых карт (прод, 16.10): маркиратор 4 с / 600 с, резка с зачисткой и лужением 6,5 с / 600 с.
export const FULL_OPS: OpRec[] = [
  { opNumber: "INS_MARK_TUBE_AUTO", opName: "Печать и резка маркировочной трубки", tOp: "4", tPrep: "600", tMachine: "" },
  { opNumber: "CUT_WIRE+2strip+tin", opName: "Резка с зачисткой двух концов и лужением", tOp: "6,5", tPrep: "600", tMachine: "" },
];
export const DSA: GenData = {
  ...DS, components: [...DS.components, ...MARK_COMPS], templates: FULL_TEMPLATES, ops: FULL_OPS,
  terRecords: [terFull("SYM-001T-P0.6", "SMR, SYP", "S11/M11/A7", "0,08", "0,35"),
    terFull("T-DS1071-SC600", "I-DS1071-SCB01, I-DS1071-SCB02", "M21/A14", "0,08", "0,2"), terFull("shf-001T-0.8BS", "SM", "S8/A5", "0,08", "0,35")],
  wireDia: { marks: [{ name: "НВ-4", norm: "нв4" }], byAwg: {}, gostAwg: {},
    byGost: { "0.5": { нв4: 1.4, __core: 0.9 }, "0.2": { нв4: 1.1, __core: 0.6 }, "0.12": { нв4: 0.95, __core: 0.45 } } },
};

/** Жгут НВ4 так, как его собрал владелец: пары белый/синий «I3» и «C» свиты, сростки паяные с трубкой ТТК 3/1, хвосты
 *  зачищены и облужены, оплётка XP-3 на ветвях А (отрезки по КД: X1 120, X2 110, X3 110, X4 120 мм), ТТК 8/2 на
 *  хвостовики разъёмов X1–X3, ТТК 12/4 ×2 на ствол, маркировка трубками у разъёмов А. */
export function fullHarness(data: GenData = DSA, method: SpliceMethod = "solder"): GenState {
  let S = setTailMode(harness(data), "tin");
  S = setSplMethod(S, method);
  S = { ...S, spl: { ...S.spl, tut: "20", tutMat: "ttk3" } };
  S = { ...S, twist: { ...S.twist, enabled: true, mode: "pairs", pairs: [{ a: String(R.w12), b: String(R.b12) }], pitch: "20" } };
  S = { ...S, wrap: { enabled: true, slots: S.wrap.slots.map((slot, i) => (i === 0 ? { mat: "xp3", kind: "braid" as const, len: "460" } : { ...slot, mat: "" })) } };
  const braid: Record<string, string> = { "5": "120", I3: "110", C: "110", A6: "120" };
  S = { ...S, endpoints: S.endpoints.map((e) => (e.side !== "A" ? e : { ...e, braidLen: braid[e.label],
    ...(e.label === "A6" ? {} : { shrinkOn: true, shrinkMat: "ttk8", shrinkLen: "15" }) })) };
  S = { ...S, hs: { enabled: true, manual: true, rows: [{ compId: "ttk12", len: "25", cnt: "2" }] } };
  const conn: Record<string, string> = { csmr2: "SMR-02V-B", csmp: "SMP-02V-BC", csmr4: "SMR-04V-B" };
  S = { ...S, mark: true, markRows: S.endpoints.filter((e) => e.side === "A").map((e) => newMarkRow({
    target: `Разъём ${e.label} (${conn[e.connId]})`, text: e.label, material: `Маркировка "${e.label}" 4/2мм`,
    place: "conn", qty: "1", len: "10–15" })) };
  return S;
}

