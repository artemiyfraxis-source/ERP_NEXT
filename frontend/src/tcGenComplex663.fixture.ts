import { assignPinJoined, joinSplice, setTail } from "./tcGenSplices";
import {
  addEndpoint, complexByHand, initState, setSplMethod, switchGenTab, toggleLengthMode, updateEndpoint, type Comp, type GenData, type GenState, type TerRec, type WireEnd,
} from "./tcGenLogic";

/*
 * 663К.1 КБ2 «Жгут периферийного питания» БЦТС.002.01.03400.206.00 (партия 300) — сборочный чертёж изм. 1.
 * Сторона А — три ветви к приборам: XS1 JST SMP-02V-BC «5VTTL» (450 мм), XS2 CONNFLY I-DS1071-SCB01x3 «AUX.5V» (350),
 * XS3 JST GHR-06V-S «PWR2» (350); сторона В — XP1 JST SMR-02V-BC «PWR.AP» (25⁺⁵ мм от гильз). Два сростка в гильзах ГС-2,5
 * (опрессовка, неизолированные): X1 — красный ПВАМ ×3 + красный МС ×2, X2 — чёрный так же. МГТФ 0,12 — на контакты 3–4 XS3,
 * другие концы свободные (заделка). Состав — как в демо-комплектации (scripts/demo/seed_complex_kitset.py): id строк kit-N.
 */
const C = (n: number, type: string, art: string, qty: number, name = art): Comp => ({ id: `kit-${n}`, type, name, art, qty });
export const COMPONENTS_663: Comp[] = [
  C(0, "Соед", "SMR-02V-BC", 1), C(1, "Соед", "SMP-02V-BC", 1), C(2, "Соед", "I-DS1071-SCB01x3", 1), C(3, "Соед", "GHR-06V-S", 1),
  C(4, "Терминал", "SYM-001T-P0.6", 2), C(5, "Терминал", "SHF-001T-0.8BS", 2), C(6, "Терминал", "T-DS1071-SC600", 2), C(7, "Терминал", "SSHL-002T-P0.2", 6),
  C(8, "Провод", "ПВАМ 0,35 красный", 1.2), C(9, "Провод", "ПВАМ 0,35 черный", 1.2),
  C(10, "Провод", "МС 16-13 0,12 красный", 0.9), C(11, "Провод", "МС 16-13 0,12 черный", 0.9), C(12, "Провод", "МГТФ 0,12", 0.9),
  C(13, "Др", "ГС-2,5", 2, "Гильза ГС-2,5 неизолированная"),
  C(14, "ТТК", "ТСП-200 2,0", 0.9, "Трубка Радиант ТСП-200 2,0 мм"), C(15, "ТТК", "ТНТ-1/0,5", 0.05), C(16, "ТТК", "ТНТ-4/2 белый", 0.35),
];
const ter = (article: string, series: string): TerRec => ({
  article, step: "2,5", strip: "2,5", lPlus: "5", lMinus: "", applicator: "", awgFrom: "", awgTo: "", mm2From: "", mm2To: "",
  fields: { terArticle: article, terSeries: series },
});
export const D663: GenData = {
  assemblyInfo: { "Индекс": "КБ2", "Наименование": "Жгут периферийного питания", "Кол-во": "300" }, templates: [], ops: [], coaxRecords: [],
  wireDia: null, coaxCableDia: null, calcOps: [], components: COMPONENTS_663,
  terRecords: [ter("SYM-001T-P0.6", "SMR, SYP"), ter("SHF-001T-0.8BS", "SM"), ter("T-DS1071-SC600", "I-DS1071-SCB01"), ter("SSHL-002T-P0.2", "GH")],
};

/** Строки «Кабели»: [состав, длина мм, жил]. Каждый отрезок между узлами — своя строка. */
const ROWS: Array<[string, number, number]> = [
  ["kit-8", 30, 1], ["kit-9", 30, 1],      // 0, 1: ПВАМ красный / чёрный, XP1 → гильза
  ["kit-8", 450, 1], ["kit-9", 450, 1],    // 2, 3: гильза → XS1
  ["kit-8", 350, 1], ["kit-9", 350, 1],    // 4, 5: гильза → XS2
  ["kit-10", 350, 2], ["kit-11", 350, 2],  // 6, 7: МС красный ×2 / чёрный ×2, гильза → XS3
  ["kit-12", 350, 2],                      // 8: МГТФ ×2, XS3 → свободные концы
];
export const W = { xp1r: 0, xp1b: 1, xs1r: 2, xs1b: 3, xs2r: 4, xs2b: 5, xs3r: 6, xs3b: 7, mgtf: 8 };
export const end = (idx: number, unit: number, side: "A" | "B"): WireEnd => ({ idx, unit, side });

const epId = (S: GenState, side: "A" | "B", n: number) => S.endpoints.filter((e) => e.side === side)[n - 1].id;

/** Жгут так, как его вводит технолог: разъёмы, кабели с длинами, контакты, сростки X1/X2 в гильзах, хвосты МГТФ. */
export function harness663(data: GenData = D663): GenState {
  let S = complexByHand(initState(data));
  S = { ...S, wrap: { ...S.wrap, enabled: false, slots: S.wrap.slots.map((slot) => ({ ...slot, mat: "" })) }, hs: { ...S.hs, enabled: false } };
  S = addEndpoint(addEndpoint(S, "A"), "A"); // сторона А: XS1, XS2, XS3; сторона В: XP1 (уже есть)
  const conn: Array<["A" | "B", number, string, string, string]> = [
    ["A", 1, "kit-1", "XS1", "kit-5"], ["A", 2, "kit-2", "XS2", "kit-6"], ["A", 3, "kit-3", "XS3", "kit-7"], ["B", 1, "kit-0", "XP1", "kit-4"],
  ];
  conn.forEach(([side, n, connId, label, termId]) => {
    const id = epId(S, side, n);
    S = updateEndpoint(data, S, id, { connId });
    S = updateEndpoint(data, S, id, { label, termId, termAuto: false });
  });
  S = toggleLengthMode({ ...S, wires: ROWS.map(([compId, length, qty], idx) => ({ idx, compId, length, qty })), nextWireIdx: ROWS.length }, 0);
  const pin = (side: "A" | "B", n: number, idx: number, unit: number, p: number) => { S = assignPinJoined(data, S, epId(S, side, n), idx, unit, p); };
  // XP1 (сторона В): 1 красный, 2 чёрный.
  pin("B", 1, W.xp1r, 0, 1); pin("B", 1, W.xp1b, 0, 2);
  // XS1 (А): 1 красный, 2 чёрный. XS2 (А): 2 красный, 3 чёрный. XS3 (А): 1,2 МС красный; 3,4 МГТФ; 5,6 МС чёрный.
  pin("A", 1, W.xs1r, 0, 1); pin("A", 1, W.xs1b, 0, 2);
  pin("A", 2, W.xs2r, 0, 2); pin("A", 2, W.xs2b, 0, 3);
  pin("A", 3, W.xs3r, 0, 1); pin("A", 3, W.xs3r, 1, 2); pin("A", 3, W.mgtf, 0, 3); pin("A", 3, W.mgtf, 1, 4);
  pin("A", 3, W.xs3b, 0, 5); pin("A", 3, W.xs3b, 1, 6);
  // Сростки: красные в X1, чёрные в X2 — концы проводов у гильзы.
  const splice = (first: WireEnd, ...rest: WireEnd[]) => {
    S = joinSplice(data, S, first, "new");
    const id = S.splices!.at(-1)!.id;
    rest.forEach((e) => { S = joinSplice(data, S, e, id); });
  };
  splice(end(W.xp1r, 0, "A"), end(W.xs1r, 0, "B"), end(W.xs2r, 0, "B"), end(W.xs3r, 0, "B"), end(W.xs3r, 1, "B"));
  splice(end(W.xp1b, 0, "A"), end(W.xs1b, 0, "B"), end(W.xs2b, 0, "B"), end(W.xs3b, 0, "B"), end(W.xs3b, 1, "B"));
  // МГТФ: другие концы свободные (заделка).
  S = setTail(data, S, end(W.mgtf, 0, "B")); S = setTail(data, S, end(W.mgtf, 1, "B"));
  S = setSplMethod(S, "sleeve");
  S = { ...S, spl: { ...S.spl, sleeves: Object.fromEntries((S.splices || []).map((sp) => [sp.id, "kit-13"])) } };
  return S;
}

/** КБ2 с рукавом ТСП-200 на трёх ветвях стороны А (длины отрезков приняты: ветвь минус 10 мм — в КД не указаны). */
export function harness663Wrap(data: GenData = D663): GenState {
  const S = harness663(data);
  const braid: Record<string, string> = { XS1: "440", XS2: "340", XS3: "340" };
  return { ...S, wrap: { enabled: true, slots: S.wrap.slots.map((slot, i) => (i === 0 ? { mat: "kit-14", kind: "braid" as const, len: "" } : slot)) },
    endpoints: S.endpoints.map((e) => (e.side === "A" ? { ...e, braidLen: braid[e.label] } : e)) };
}

// ── 663К.1 КБ14 и 708К.5 КБ1: ленты (состав — scripts/demo/seed_complex_kitset.py) ─────────────────────────────────
const KB14: Comp[] = [
  C(0, "Соед", "SMR-02V-BC", 2), C(1, "Терминал", "SYM-001T-P0.6", 4), C(2, "Провод", "ПВАМ 0,35 красный", 1.5), C(3, "Провод", "ПВАМ 0,35 черный", 1.5),
  C(4, "Др", "Лента флисовая 19 мм", 0.8), C(5, "Др", "Изолента ПВХ 19 мм", 0.3), C(6, "ТТК", "ТСП-200 2,0", 0.5, "Трубка Радиант ТСП-200 2,0 мм"),
];
const KB708: Comp[] = [
  C(0, "Соед", "SMR-02V-BC", 2), C(1, "Терминал", "SYM-001T-P0.6", 4), C(2, "Провод", "ПВАМ 0,35 красный", 1.2), C(3, "Др", "Лента тканевая 19 мм", 0.5),
];
export const COMPONENTS_KB14 = KB14, COMPONENTS_708K5 = KB708;
const dataOf = (components: Comp[]): GenData => ({ ...D663, components });
type SlotSpec = Partial<GenState["wrap"]["slots"][number]> & { mat: string; kind: "tape" | "braid" | "under"; len: string };

/** Два разъёма SMR-02V-BC, провода по длинам; ленты в слоях `slots` (состав kit-N) — ствол жгута. */
function twoConnHarness(components: Comp[], wires: Array<[string, number]>, slots: SlotSpec[]): { D: GenData; S: GenState } {
  const data = dataOf(components);
  let S = complexByHand(switchGenTab(initState(data), "complex"));
  S = { ...S, hs: { ...S.hs, enabled: false } };
  S = addEndpoint(S, "B");
  S = { ...S, endpoints: S.endpoints.slice(0, 2) };
  const [a, b] = [epId(S, "A", 1), epId(S, "B", 1)];
  [[a, "XP1"], [b, "XS1"]].forEach(([id, label]) => { S = updateEndpoint(data, S, id, { connId: "kit-0" }); S = updateEndpoint(data, S, id, { label, termId: "kit-1", termAuto: false }); });
  S = toggleLengthMode({ ...S, wires: wires.map(([compId, length], idx) => ({ idx, compId, length, qty: 1 })), nextWireIdx: wires.length }, 0);
  wires.forEach((_, idx) => { S = assignPinJoined(data, S, a, idx, 0, idx + 1); S = assignPinJoined(data, S, b, idx, 0, idx + 1); });
  S = { ...S, wrap: { enabled: true, slots: S.wrap.slots.map((slot, i) => slots[i] ?? { ...slot, mat: "" }) } };
  return { D: data, S };
}
export const harnessKb14 = () => twoConnHarness(KB14, [["kit-2", 450], ["kit-3", 450]], [
  { mat: "kit-4", kind: "tape", len: "800" }, { mat: "kit-5", kind: "tape", len: "300" }, { mat: "kit-6", kind: "braid", len: "500" }]);
export const harness708k5 = () => twoConnHarness(KB708, [["kit-2", 480]], [{ mat: "kit-3", kind: "tape", len: "480" }]);
