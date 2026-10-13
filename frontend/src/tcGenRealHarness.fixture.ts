import { joinSplice, assignPinJoined, setTail } from "./tcGenSplices";
import { addEndpoint, complexByHand, initState, toggleLengthMode, updateEndpoint, type Comp, type GenData, type GenState, type TerRec, type WireEnd } from "./tcGenLogic";

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

