// @vitest-environment node
import { expect, it } from "vitest";
import { genCards, opStages } from "./routeCards";
import { deriveRoute, type RouteOp } from "./routeLogic";
import { addEndpoint, initState, makeGen, updateEndpoint, type Comp, type GenData, type GenState, type OpSpec } from "./tcGenLogic";

// Владелец 15.10, 545К.9 КБ1 «Кабель TC-BLS-5-BLS-5-400-RLB»: «на многожильном проводе обжим сначала с двух сторон а
// потом монтаж»; «жилы у многожильного не могут разделываться на станке, они либо разделываются отдельно перед обжимом,
// либо на ОТП». Кабель UL2527 28AWG на 4 жилы, оба конца — I-DS1071-SCB01X5 с терминалами, оболочка снимается на
// роторе, «Термоусадка на срез оболочки» с обеих сторон, «Без зачистки (OTP)» на А. Было: ротор «оболочка 20 мм, жилы
// 3,2 мм», обжим А, монтаж А, усадка А, обжим В, монтаж В, усадка В — и ПФ «Обжатые провода, сторона В» с усадкой
// стороны А внутри.
const DS: Comp = { id: "c1", type: "Соед", name: "I-DS1071-SCB01X5", art: "I-DS1071-SCB01X5", qty: 2 };
const TERM: Comp = { id: "t1", type: "Терминал", name: "T-DS1071-SC600", art: "T-DS1071-SC600", qty: 8 };
const UL: Comp = { id: "w1", type: "Провод", name: "UL2527 28AWG", art: "UL2527 28AWG", kd: "UL2575 26AWG", qty: 0.42 };
const TUT: Comp = { id: "h1", type: "ТУТ", name: "ТУТ 6/3мм черная", art: "ТУТ 6/3мм черная", qty: 0.06 };
const DB9: Comp = { id: "d1", type: "Соед", name: "DB-9F", art: "DB-9F", qty: 1 };

function data(components: Comp[]): GenData {
  return {
    components, assemblyInfo: { "Индекс": "КБ1", "Наименование": "Кабель TC-BLS-5-BLS-5-400-RLB", "Кол-во": "50" },
    templates: [], ops: [], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [], cableRecords: [],
    terRecords: [{ article: "T-DS1071-SC600", step: "2.54", strip: "3.2", lPlus: "5", lMinus: "", applicator: "M21", awgFrom: "", awgTo: "", mm2From: "", mm2To: "" }],
  };
}
const ownerBom = () => data([DS, TERM, UL, TUT]);
/** Форма владельца: «Без зачистки (OTP)» на стороне А. */
const ownerForm = (D: GenData) => { const S = initState(D); return updateEndpoint(D, S, S.endpoints[0].id, { otp: true }); };
const sideB = (S: GenState) => S.endpoints.find((ep) => ep.side === "B")!;

const keys = (ops: OpSpec[]) => ops.map((op) => `${op.type === "stripCable" ? op.opKey.replace(/\d+$/, "") : op.type}${op.side && op.type !== "stripCable" ? ":" + op.side + (op.ord ?? "") : ""}`);
const routeOf = (ops: OpSpec[]): RouteOp[] =>
  ops.map((op, i) => ({ position: i + 1, key: op.opKey, type: op.type, code: op.tmplHint || "", label: op.label, minutes: null, ...(op.side ? { side: op.side } : {}) }));
const stagesOf = (ops: OpSpec[]) => [...opStages(routeOf(ops)).entries()].sort(([a], [b]) => a - b).map(([, stage]) => stage);

it("кабель в оболочке: ротор снимает оболочку; обжим А (ОТП), зачистка жил В, обжим В; потом монтаж А, усадка А, монтаж В, усадка В", () => {
  const D = ownerBom();
  const ops = makeGen(D, ownerForm(D)).getActiveOps();
  expect(ops.map((op) => op.label)).toEqual([
    "Резка с разделкой на роторе (2 ст.: оболочка 20 мм) — UL2527 28AWG",
    "Резка ТУТ на место разделки А, В — 2×25 мм",
    "Надеть трубки (стороны А и В): ТУТ 6/3мм черная на место разделки А, В — 2 шт",
    "Опрессовка терминалов А1",
    "Зачистка жил под обжим В1 (3,2 мм) — 4 шт",
    "Опрессовка терминалов В1",
    "Монтаж в разъём А1 — I-DS1071-SCB01X5",
    "Усадка ТУТ на месте разделки А — 1 шт",
    "Монтаж в разъём В1 — I-DS1071-SCB01X5",
    "Усадка ТУТ на месте разделки В — 1 шт",
  ]);
  expect(ops[0].strip).toEqual({ a: true, lenA: "20", b: true, lenB: "20" });
  // Первым по-прежнему отмечен обжим А (по нему подбирается терминал в шаблон).
  expect(ops.filter((op) => op.type === "prsTerm").map((op) => [op.side, op.firstTerm])).toEqual([["A", true], ["B", false]]);
});

it("без ОТП — зачистка жил у каждой стороны прямо перед её обжимом; ОТП на обеих — зачисток нет", () => {
  const D = ownerBom();
  const S = initState(D);
  expect(keys(makeGen(D, S).getActiveOps())).toEqual([
    "cutWireStrip", "cutTut", "insTut", "stripCoresA", "prsTerm:A1", "stripCoresB", "prsTerm:B1", "insTerm:A1", "heatTut:A", "insTerm:B1", "heatTut:B",
  ]);
  const both = updateEndpoint(D, ownerForm(D), sideB(S).id, { otp: true });
  expect(makeGen(D, both).getActiveOps().some((op) => op.opKey.startsWith("stripCores"))).toBe(false);
});

it("ПФ кабеля в оболочке: обжатые провода А, обжатые провода В, сторона А собрана, готовый кабель — усадка А не в обжиме В", () => {
  const D = ownerBom();
  const ops = makeGen(D, ownerForm(D)).getActiveOps();
  expect(stagesOf(ops)).toEqual(["wires", "tubes", "crimpedA", "crimpedA", "crimpedB", "crimpedB", "A", "A", "B", "B"]);
  const route = routeOf(ops);
  const { cards, links } = genCards(deriveRoute(route), route);
  expect(cards.map((card) => [card.title, card.ops.map((op) => op.label.split(" — ")[0])])).toEqual([
    ["Нарезанные провода", ["Резка с разделкой на роторе (2 ст.: оболочка 20 мм)"]],
    ["Нарезанные трубки", ["Резка ТУТ на место разделки А, В"]],
    ["Обжатые провода, сторона А", ["Надеть трубки (стороны А и В): ТУТ 6/3мм черная на место разделки А, В", "Опрессовка терминалов А1"]],
    ["Обжатые провода, сторона В", ["Зачистка жил под обжим В1 (3,2 мм)", "Опрессовка терминалов В1"]],
    ["Сторона А собрана", ["Монтаж в разъём А1", "Усадка ТУТ на месте разделки А"]],
    ["Готовый кабель", ["Монтаж в разъём В1", "Усадка ТУТ на месте разделки В"]],
  ]);
  expect(links.map((link) => link.id).sort()).toEqual(["A>B", "crimpedA>crimpedB", "crimpedB>A", "tubes>crimpedA", "wires>crimpedA"]);
});

it("два разъёма на стороне В (жилы 1–2 → В1, 3–4 → В2): все зачистки и опрессовки обеих сторон до первого монтажа", () => {
  const D = data([{ ...DS, qty: 3 }, TERM, UL, TUT]);
  let S = initState(D);
  S = addEndpoint(S, "B");
  const b1 = sideB(S), b2 = S.endpoints.filter((ep) => ep.side === "B")[1];
  const idx = S.wires[0].idx;
  S = updateEndpoint(D, S, b2.id, { connId: "c1", termId: "t1", pins: { [idx]: "3:1, 2" } });
  S = updateEndpoint(D, S, b1.id, { pins: { [idx]: "1, 2" } });
  const ops = makeGen(D, S).getActiveOps();
  expect(ops.filter((op) => /^(stripCores|prsTerm|insTerm)/.test(op.opKey)).map((op) => op.opKey)).toEqual([
    "stripCoresA1", "prsTermA1", "stripCoresB1", "prsTermB1", "stripCoresB2", "prsTermB2", "insTermA1", "insTermB1", "insTermB2",
  ]);
  expect(ops.find((op) => op.opKey === "stripCoresB2")!.label).toBe("Зачистка жил под обжим В2 (3,2 мм) — 2 шт");
  expect(new Set(stagesOf(ops))).toEqual(new Set(["wires", "tubes", "crimpedA", "crimpedB", "A", "B"]));
});

it("пайка на стороне В — не обжим: порядок как был (обжим А, монтаж А, зачистка жил под пайку, пайка В)", () => {
  const D = data([DS, { ...TERM, qty: 4 }, UL, DB9]);
  let S = ownerForm(D);
  S = updateEndpoint(D, S, sideB(S).id, { connId: "d1", method: "solder", methodAuto: false, termId: "" });
  const ops = makeGen(D, S).getActiveOps();
  expect(ops.filter((op) => /^(stripCores|prsTerm|insTerm|solderConn)/.test(op.opKey)).map((op) => op.opKey))
    .toEqual(["prsTermA1", "insTermA1", "stripCoresB1", "solderConnB1"]);
});

it("сохранённый технологом порядок (opOrder) главнее", () => {
  const D = ownerBom();
  const S = ownerForm(D);
  const own = makeGen(D, S).getActiveOps().map((op) => op.opKey).filter((key) => key !== "prsTermB1" && key !== "stripCoresB1");
  own.splice(own.indexOf("heatJsA") + 1, 0, "stripCoresB1", "prsTermB1");
  expect(makeGen(D, { ...S, opOrder: own }).getActiveOps().map((op) => op.opKey)).toEqual(own);
});

it("одиночные провода — как были (владелец 14.10): резка, обжим А, монтаж А, обжим В, монтаж В", () => {
  const COLORS = ["красный", "черный", "синий", "белый"];
  const wires: Comp[] = COLORS.map((color, i) => ({ id: `w${i}`, type: "Провод", name: `UL1061 30AWG ${color}`, art: `UL1061 30AWG ${color}`, qty: 0.1 }));
  const D = data([...wires, { ...TERM, art: "SSHL-002T-P0.2", name: "SSHL-002T-P0.2" }, { ...DS, name: "GHR-04V-S", art: "GHR-04V-S" }]);
  let S: GenState = initState(D);
  for (const ep of S.endpoints) S = updateEndpoint(D, S, ep.id, { termId: "t1", connId: "c1" });
  S = { ...S, crimpCutManual: true, crimpCut: false };
  const ops = makeGen(D, S).getActiveOps();
  expect(keys(ops)).toEqual(["cutWire", "prsTerm:A1", "insTerm:A1", "prsTerm:B1", "insTerm:B1"]);
  expect(stagesOf(ops)).toEqual(["wires", "crimpedA", "A", "crimpedB", "B"]);
});
