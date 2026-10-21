// @vitest-environment node
import { expect, it } from "vitest";
import { opStages } from "./routeCards";
import type { RouteOp } from "./routeLogic";
import { initState, makeGen, updateEndpoint, type Comp, type GenData, type GenState, type OpSpec } from "./tcGenLogic";

// Владелец 15.10, 316К.16 КБ1 «Межплатная»: «свивка после опресовки». Кабельная сборка GHR-04V-S to GHR-04V-S 30AWG
// 90mm — 4 провода UL1061 30AWG, обжим А на станке резки, «Без зачистки (OTP)» на В, свивка всех проводов. Свивка
// стояла до опрессовки В: резка, монтаж А1, свивка, опрессовка В1, монтаж В1.
const COLORS = ["красный", "черный", "синий", "белый"];
const WIRES: Comp[] = COLORS.map((color, i) => ({ id: `w${i}`, type: "Провод", name: `UL1061 30AWG ${color}`, art: `UL1061 30AWG ${color}`, qty: 0.1 }));
const TERM: Comp = { id: "t", type: "Терминал", name: "SSHL-002T-P0.2", art: "SSHL-002T-P0.2", qty: 8 };
const GHR: Comp = { id: "c", type: "Соед", name: "GHR-04V-S", art: "GHR-04V-S", qty: 2 };
const ter = (article: string, applicator: string) =>
  ({ article, step: "1.25", strip: "1,5", lPlus: "3", lMinus: "", applicator, awgFrom: "", awgTo: "", mm2From: "", mm2To: "" });

function data(components: Comp[], applicator = "A3"): GenData {
  return {
    components, assemblyInfo: { "Индекс": "КБ1", "Наименование": "Кабельная сборка GHR-04V-S to GHR-04V-S 30AWG 90mm", "Кол-во": "200" },
    templates: [{ id: "cwc", title: "CUT_WIRE+1crimp | Автомат резка + опрессовка", category: "Автомат резка + опрессовка" },
      { id: "cw2c", title: "CUT_WIRE+2crimp | Автомат резка + опрессовка", category: "Автомат резка + опрессовка" },
      { id: "tw", title: "TWIST | Свивка", category: "Свивка" }],
    ops: [], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [], cableRecords: [],
    terRecords: [ter("SSHL-002T-P0.2", applicator)],
  };
}

/** Форма владельца: терминалы на обеих сторонах, обжим А при резке, ОТП на В, свивка «все вместе». */
function ownerForm(D: GenData, patch: (S: GenState) => GenState = (S) => S): GenState {
  let S = initState(D);
  for (const ep of S.endpoints) S = updateEndpoint(D, S, ep.id, { termId: "t", connId: "c" });
  const b1 = S.endpoints.find((ep) => ep.side === "B")!;
  S = updateEndpoint(D, S, b1.id, { otp: true });
  S = { ...S, crimpCutManual: true, crimpCut: true, crimpCutSide: "A", twist: { ...S.twist, enabled: true, mode: "all", wires: null, pitch: "15" } };
  return patch(S);
}

const kinds = (ops: OpSpec[]) => ops.map((op) => `${op.type}${op.side ? ":" + op.side : ""}`);

it("свивка после опрессовки В1 и до монтажа В1 (владелец 15.10, 316К.16 КБ1)", () => {
  const D = data([...WIRES, TERM, GHR]);
  const ops = makeGen(D, ownerForm(D)).getActiveOps();
  expect(ops.map((op) => op.label)).toEqual([
    "Резка с обжимом А (SSHL-002T-P0.2) и зачисткой В — 4 кабеля",
    "Монтаж в разъём А1 — GHR-04V-S",
    "Опрессовка терминалов В1",
    "Свивка",
    "Монтаж в разъём В1 — GHR-04V-S",
  ]);
});

it("ПФ: свивка — в «Сторона В собрана», опрессовка В — свой полуфабрикат перед ней", () => {
  const D = data([...WIRES, TERM, GHR]);
  const ops = makeGen(D, ownerForm(D)).getActiveOps();
  const route: RouteOp[] = ops.map((op, i) => ({ position: i + 1, key: op.opKey, type: op.type, code: op.tmplHint || "", label: op.label, minutes: null, side: op.side }));
  expect([...opStages(route).entries()].sort(([a], [b]) => a - b).map(([, stage]) => stage)).toEqual(["wires", "A", "crimpedB", "B", "B"]);
});

it("обжим обеих сторон на станке — свивка, как и была, между монтажом А и монтажом В", () => {
  const D = data([...WIRES, TERM, GHR]);
  const ops = makeGen(D, ownerForm(D, (S) => ({ ...S, crimpCutSide: "AB" }))).getActiveOps();
  expect(kinds(ops)).toEqual(["cutWireCrimp:A", "insTerm:A", "twist", "insTerm:B"]);
});

it("обжим А руками — опрессовка А, монтаж А, опрессовка В, свивка, монтаж В", () => {
  const D = data([...WIRES, TERM, GHR]);
  const ops = makeGen(D, ownerForm(D, (S) => ({ ...S, crimpCut: false }))).getActiveOps();
  expect(kinds(ops)).toEqual(["cutWireStrip", "prsTerm:A", "insTerm:A", "prsTerm:B", "twist", "insTerm:B"]);
});

it("пайка на стороне В — свивка, как и была, прямо перед пайкой", () => {
  const D = data([...WIRES, TERM, GHR]);
  const S = ownerForm(D, (cur) => updateEndpoint(D, cur, cur.endpoints.find((ep) => ep.side === "B")!.id, { method: "solder", otp: false }));
  const types = kinds(makeGen(D, S).getActiveOps());
  expect(types.indexOf("twist")).toBe(types.indexOf("solderConn:B") - 1);
  expect(types.indexOf("twist")).toBeGreaterThan(types.indexOf("insTerm:A"));
});

it("два разъёма на В — обе опрессовки В до свивки, монтажи В после", () => {
  const D = data([...WIRES, TERM, { ...GHR, qty: 3 }]);
  const S0 = ownerForm(D);
  const b1 = S0.endpoints.find((ep) => ep.side === "B")!;
  const b2 = { ...b1, id: "b2" };
  const S: GenState = { ...S0, endpoints: [...S0.endpoints, b2] };
  const ops = makeGen(D, S).getActiveOps();
  const keys = ops.map((op) => `${op.type}${op.side ? ":" + op.side + (op.ord ?? "") : ""}`);
  expect(keys).toEqual(["cutWireCrimp:A", "insTerm:A1", "prsTerm:B1", "prsTerm:B2", "twist", "insTerm:B1", "insTerm:B2"]);
});

it("терминалы на В без колодки — свивка после их опрессовки", () => {
  const D = data([...WIRES, TERM, GHR]);
  const S = ownerForm(D, (cur) => updateEndpoint(D, cur, cur.endpoints.find((ep) => ep.side === "B")!.id, { connId: "" }));
  expect(kinds(makeGen(D, S).getActiveOps())).toEqual(["cutWireCrimp:A", "insTerm:A", "prsTerm:B", "twist"]);
});
