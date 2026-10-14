// @vitest-environment node
import { expect, it } from "vitest";
import { assignPin, autoFillPinout, pinFitWarnings, pinoutEnds, pinUnits, routingWarnings, SHIELD_COLOR, unassignPin } from "./pinoutCheck";
import {
  addEndpoint, cableRecFromFields, formatCorePins_, initState, makeGen, parseCorePins_, parsePinList_, switchGenTab, updateEndpoint, updateWire,
  type CableRec, type GenData, type GenState,
} from "./tcGenLogic";
import { buildHarnessDocument, planFromGenerator, type ConnectorSource } from "./generatorToHarness";

// Случай владельца (12.10): А1 DF1B-18DS-2.5RC (терминал DF1B-2428SCFA) ↔ В1 DF1B-5EP-2.5RC (терминал DF1-PD2428SCFB)
// одним многожильным «Helukabel 15201 ×19». Жилы — единицы распиновки, кабель — одна оболочка.
const COLORS = ["белый", "коричневый", "зелёный", "жёлтый", "серый", "розовый", "синий", "красный", "чёрный", "фиолетовый",
  "серо-розовый", "красно-синий", "бело-зелёный", "коричнево-зелёный", "бело-жёлтый", "жёлто-коричневый", "бело-серый", "серо-коричневый", "бело-розовый"];
const HELU: CableRec = { mark: "Helukabel 15201", cores: 19, colors: COLORS, screen: "" };

function data(rec: CableRec = HELU): GenData {
  return {
    components: [
      { id: "cab", type: "Кабель", name: "Helukabel 15201", art: "Helukabel 15201", qty: 0.3 },
      { id: "ca", type: "Соед", name: "DF1B-18DS-2.5RC", art: "DF1B-18DS-2.5RC", qty: 1 },
      { id: "cb", type: "Соед", name: "DF1B-5EP-2.5RC", art: "DF1B-5EP-2.5RC", qty: 2 },
      { id: "ta", type: "Терминал", name: "DF1B-2428SCFA", art: "DF1B-2428SCFA", qty: 19 },
      { id: "tb", type: "Терминал", name: "DF1-PD2428SCFB", art: "DF1-PD2428SCFB", qty: 19 },
    ],
    assemblyInfo: { "Индекс": "КБ1", "Наименование": "Кабель Helukabel 300мм" },
    templates: [], ops: [], terRecords: [], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [], cableRecords: [rec],
  };
}
const D = data();
const g = (S: GenState, d: GenData = D) => makeGen(d, S);
type Op = { type: string; side?: string; tutCount?: number; wires?: Array<{ qty: number; cores?: number }>; pins?: string[]; ep?: { termArt: string } };
const opsOf = (S: GenState, d: GenData = D) => (g(S, d).buildConfig() as { ops: Op[] }).ops;
/** Терминалов у опрессовки — как их считает etk_builder._term_count: кабелей × жил по проводам операции. */
const terms = (op: Op) => (op.wires || []).reduce((s, w) => s + (Number(w.qty) || 1) * (Number(w.cores) || 1), 0);

/** Пин-в-пин: число жил — из записи БД.ПРОВ (в наименовании его нет), контакты 1…19 на обоих концах. */
function pinToPin(d: GenData = D): GenState {
  const S = { ...initState(d), rotor: false };
  return autoFillPinout(d, S, { a1: 19, b1: 19 }).state;
}

/** Жилы 1–10 → В1, 11–19 → В2 (второй DF1B-5EP-2.5RC). */
function split(): GenState {
  let S: GenState = switchGenTab({ ...initState(D), rotor: false }, "complex");
  S = addEndpoint(S, "B");
  S = updateEndpoint(D, S, "e2", { connId: "cb", termId: "tb" });
  return autoFillPinout(D, S, { a1: 19, b1: 10, e2: 9 }).state;
}

it("19 cores pin to pin: 19 terminals on each end, the cable cut once, core colours from the БД.ПРОВ record", () => {
  const S = pinToPin();
  expect(g(S).wireCores_(S.wires[0])).toBe(19);
  expect(g(S).connections_().list).toHaveLength(19);
  const prs = opsOf(S).filter((op) => op.type === "prsTerm");
  expect(prs.map((op) => [op.side, op.ep?.termArt, terms(op)])).toEqual([["A", "DF1B-2428SCFA", 19], ["B", "DF1-PD2428SCFB", 19]]);
  const cfg = g(S).buildConfig() as { wires: Array<{ qty: number; cores: number; coreColors: string[] }> };
  expect(cfg.wires).toEqual([expect.objectContaining({ qty: 1, cores: 19 })]);
  expect(cfg.wires[0].coreColors).toEqual(COLORS);
  const units = pinUnits(g(S), S);
  expect(units.slice(0, 3).map((u) => [u.num, u.colorName])).toEqual([["1.1", "белый"], ["1.2", "коричневый"], ["1.3", "зелёный"]]);
  // Без ротора кабель разделывают у каждого конца — у обжима тоже, по разу на сторону.
  expect(g(S).getActiveOps().filter((op) => op.opKey.startsWith("stripCable")).map((op) => [op.label, op.tmplHint])).toEqual([
    ["Разделка кабеля под обжим А1 — 1 конц.", "STRIP_CABLE"], ["Разделка кабеля под обжим В1 — 1 конц.", "STRIP_CABLE"],
  ]);
  expect(routingWarnings(D, S)).toEqual([]);
});

it("splits the cores of one cable 10/9 across В1 and В2: each end counts its own terminals, one jacket strip on side В", () => {
  const S = split();
  const b2 = S.endpoints.find((e) => e.id === "e2")!;
  const idx = S.wires[0].idx;
  expect(S.endpoints.find((e) => e.id === "b1")!.pins).toEqual({ [idx]: "1, 2, 3, 4, 5, 6, 7, 8, 9, 10" });
  expect(b2.pins).toEqual({ [idx]: "11:1, 2, 3, 4, 5, 6, 7, 8, 9" });
  expect(parseCorePins_(b2.pins![idx]).slice(9, 12)).toEqual([null, 1, 2]);
  // Жила 11 идёт А1·11 → В2·1, жила 10 — А1·10 → В1·10.
  const links = g(S).connections_();
  expect(links.warnings).toEqual([]);
  expect(links.list).toHaveLength(19);
  expect(links.list.find((l) => l.from.pin === 11)).toMatchObject({ to: { end: "В2", pin: 1 } });
  expect(links.list.find((l) => l.from.pin === 10)).toMatchObject({ to: { end: "В1", pin: 10 } });
  const prs = opsOf(S).filter((op) => op.type === "prsTerm");
  expect(prs.map(terms)).toEqual([19, 10, 9]);
  expect(prs[2].pins).toEqual(["1, 2, 3, 4, 5, 6, 7, 8, 9"]);
  // Палитра: жила 1.11 — на В2, не на В1.
  const u11 = pinUnits(g(S), S).find((u) => u.num === "1.11")!;
  expect(u11.at).toEqual({ a1: 11, e2: 1 });
  expect(pinoutEnds(g(S), S).map((end) => [end.key, end.wires[0].qty])).toEqual([["А1", 19], ["В1", 10], ["В2", 9]]);
  expect(pinoutEnds(g(S), S).map((end) => end.summary)).toEqual([
    "19 жил, по разъёмам: В1 — 10, В2 — 9", "10 жил, прямой порядок", "9 жил, перекрёст 11→1, 12→2, 13→3, 14→4, 15→5, 16→6, 17→7, 18→8 … ещё 1",
  ]);
  expect(routingWarnings(D, S)).toEqual([]);
  // Оболочку на стороне В снимают один раз — у В1, для жил В1 и В2.
  expect(g(S).getActiveOps().filter((op) => op.opKey.startsWith("stripCable")).map((op) => op.label)).toEqual([
    "Разделка кабеля под обжим А1 — 1 конц.", "Разделка кабеля под обжим В1, В2 — 1 конц.",
  ]);
  // Перенос одной жилы с В2 на В1 не сдвигает остальные жилы кабеля.
  const moved = assignPin(D, unassignPin(D, S, "e2", idx, 18), "b1", idx, 18, 5);
  expect(parseCorePins_(moved.endpoints.find((e) => e.id === "e2")!.pins![idx])).toEqual([...Array(10).fill(null), 1, 2, 3, 4, 5, 6, 7, 8]);
});

it("«Заполнить автоматически» after adding В2 deals side В again: the cable splits instead of staying whole on В1", () => {
  let S: GenState = switchGenTab({ ...initState(D), rotor: false }, "complex");
  S = autoFillPinout(D, S, { a1: 19, b1: 19 }).state;
  S = addEndpoint(S, "B");
  S = updateEndpoint(D, S, "e2", { connId: "cb", termId: "tb" });
  // Без «заново» сторона В уже разведена — В2 остаётся пустым (автозаполнение при открытии формы её не трогает).
  expect(autoFillPinout(D, S, { a1: 19, b1: 10, e2: 9 }).state.endpoints.find((e) => e.id === "e2")!.pins ?? {}).toEqual({});
  const again = autoFillPinout(D, S, { a1: 19, b1: 10, e2: 9 }, true).state;
  expect(opsOf(again).filter((op) => op.type === "prsTerm").map(terms)).toEqual([19, 10, 9]);
});

it("2 unused cores: 17 terminals per end, an op to cut and insulate them on each end and a visible warning", () => {
  const full = pinToPin();
  const idx = full.wires[0].idx;
  let S = full;
  [17, 18].forEach((k) => { S = unassignPin(D, unassignPin(D, S, "a1", idx, k), "b1", idx, k); });
  expect(opsOf(S).filter((op) => op.type === "prsTerm").map(terms)).toEqual([17, 17]);
  const idle = g(S).getActiveOps().filter((op) => op.type === "endCap");
  expect(idle.map((op) => [op.label, op.tmplHint, op.tutCount])).toEqual([
    ["Обрезка и изоляция незадействованных жил А1 — 2 шт", "INS_END_CAP", 2],
    ["Обрезка и изоляция незадействованных жил В1 — 2 шт", "INS_END_CAP", 2],
  ]);
  expect(g(S).cableEnds_("B")[0].unused).toEqual([17, 18]);
  expect(routingWarnings(D, S)).toEqual(["Не задействованы жилы: 1.18 · Helukabel 15201, 1.19 · Helukabel 15201 — назначьте контакт или подрежьте и заизолируйте."]);
  // Время опрессовки и состав — тоже по 17 жилам: так их считает сервер (op.wires).
  expect(opsOf(S).find((op) => op.type === "insTerm" && op.side === "B")!.wires).toEqual([expect.objectContaining({ qty: 1, cores: 17 })]);
});

it("a screened cable (БД.ПРОВ «Экран») adds shield twisting on each end and the screened strip card", () => {
  const screened = data({ ...HELU, screen: "оплётка луж. медь" });
  const S = pinToPin(screened);
  const ops = g(S, screened).getActiveOps();
  // Карты «Скрутка экрана» в библиотеке нет — разделка экранированного кабеля, в схеме видно «вместо TWST_SHILED».
  expect(ops.filter((op) => op.tmplWant === "TWST_SHILED").map((op) => [op.label, op.tmplHint])).toEqual([
    ["Скрутка и изоляция экрана А1 — 1 шт", "STRIP_CABLE_sheld"], ["Скрутка и изоляция экрана В1 — 1 шт", "STRIP_CABLE_sheld"],
  ]);
  const withCard = { ...screened, templates: [{ id: "tw", title: "TWST_SHILED | Скрутка экрана", category: "Разделка" }] };
  expect(g(S, withCard).getActiveOps().filter((op) => op.tmplWant === "TWST_SHILED").every((op) => op.tmplHint === "TWST_SHILED")).toBe(true);
  expect(ops.filter((op) => op.opKey.startsWith("stripCable")).every((op) => op.tmplHint === "STRIP_CABLE_sheld")).toBe(true);
  // Экран — до опрессовки своего конца.
  const keys = ops.map((op) => op.opKey);
  expect(keys.indexOf("shieldA1")).toBeLessThan(keys.indexOf("prsTermA1"));
  // На роторе разделка при резке, а экран всё равно скручивают у разъёма.
  const rotor = g({ ...S, rotor: true }, screened).getActiveOps();
  expect(rotor.some((op) => op.opKey.startsWith("stripCable"))).toBe(false);
  expect(rotor.filter((op) => op.tmplWant === "TWST_SHILED")).toHaveLength(2);
});

it("an old saved state loads unchanged: pin strings without core gaps read as before", () => {
  expect(parseCorePins_("1-3, 7")).toEqual([1, 2, 3, 7]);
  expect(parsePinList_("1-3, 7")).toEqual([1, 2, 3, 7]);
  expect(parseCorePins_("11:1-3")).toEqual([...Array(10).fill(null), 1, 2, 3]);
  expect(parseCorePins_("_, 2, 5:9")).toEqual([null, 2, null, null, 9]);
  expect(parsePinList_("11:1-3")).toEqual([1, 2, 3]);
  expect(formatCorePins_([1, 2, 3, 7])).toBe("1, 2, 3, 7");
  expect(formatCorePins_([null, 2, null, null, 9, null])).toBe("2:2, 5:9");
  expect(parseCorePins_(formatCorePins_([...Array(10).fill(null), 1, 2]))).toEqual([...Array(10).fill(null), 1, 2]);
  // Сохранение до жил по разъёмам: «1-19» на обоих концах, число жил задано в строке «Кабели», записи БД.ПРОВ нет.
  const old = { ...data(), cableRecords: [] };
  let S = initState(old);
  S = updateWire(S, S.wires[0].idx, { cores: 19, qty: 1 });
  const idx = S.wires[0].idx;
  S = updateEndpoint(old, updateEndpoint(old, S, "a1", { pins: { [idx]: "1-19" } }), "b1", { pins: { [idx]: "19-1" } });
  expect(g(S, old).connections_().list.map((l) => [l.from.pin, l.to.pin]).slice(0, 2)).toEqual([[1, 19], [2, 18]]);
  expect(pinUnits(g(S, old), S)[0].at).toEqual({ a1: 1, b1: 19 });
  expect(opsOf(S, old).filter((op) => op.type === "prsTerm").map(terms)).toEqual([19, 19]);
  // Правка одной жилы пишет строку в прежнем виде, пока пропусков нет.
  expect(assignPin(old, S, "a1", idx, 0, 1).endpoints[0].pins).toEqual({ [idx]: "1-19" });
  expect(assignPin(old, S, "a1", idx, 0, 20).endpoints[0].pins![idx]).toBe([20, ...Array.from({ length: 18 }, (_, i) => i + 2)].join(", "));
});

it("reads a БД.ПРОВ row: mark, cores, colours, screen", () => {
  expect(cableRecFromFields({ "Марка": "PAAR-TRONIC-CY 4x2x0,5", "Число жил": "8 (4 пары)", "Цвета": "серый", "Экран": "оплётка (луженая медь)" }))
    .toEqual({ mark: "PAAR-TRONIC-CY 4x2x0,5", cores: 8, colors: ["серый"], screen: "оплётка (луженая медь)" });
  expect(cableRecFromFields({ "Число жил": "4" })).toBeNull();
  // Один цвет на 8 жил — цвет оболочки, не жил: жилы не красятся.
  const paar = data({ mark: "Helukabel 15201", cores: 8, colors: ["серый"], screen: "" });
  const S = initState(paar);
  expect(g(S, paar).coreColors_(S.wires[0]).every((c) => c === "")).toBe(true);
});

it("«Построить чертёж» draws the multi-core cable as one jacketed cable whose cores fan out to В1 and В2", () => {
  const S = { ...split(), drawingLength: 300, sharedLengthText: "300" };
  const plan = planFromGenerator(D, S);
  expect(plan.wires).toHaveLength(19);
  expect(plan.cables).toEqual([expect.objectContaining({ mark: "Helukabel 15201", lengthMm: 300, stripMm: 20 })]);
  expect(plan.cables[0].wireKeys).toHaveLength(19);
  expect(plan.wires.find((w) => w.unit === 0)!.color).toBe("#FFFFFF");
  const free: ConnectorSource = { kind: "free", contactCount: null, libraryCode: null };
  const { document, wires } = buildHarnessDocument(plan, Object.fromEntries(plan.connectors.map((c) => [c.key, free])));
  expect(wires).toBe(19);
  expect(document.cables).toHaveLength(1);
  expect(document.cables[0]).toMatchObject({ lengthMm: 300, sheathStrip: { fromMm: 20, toMm: 20 } });
  expect(document.cables[0].memberWireIds).toHaveLength(19);
  // Ствол А → узел — кабель в оболочке; ветки к В1 (10 жил) и В2 (9 жил) — жилы россыпью.
  const segments = document.physicalTopology!.segments;
  expect(segments).toHaveLength(3);
  expect(segments.map((s) => s.specificationItemId ?? "")).toEqual([document.cables[0].id, "", ""]);
  const through = (id: string) => document.physicalTopology!.routes.filter((r) => r.steps.some((s) => s.segmentId === id)).length;
  expect(segments.map((s) => through(s.id))).toEqual([19, 10, 9]);
});

// Экран на контакт (владелец 13.10): экран (дренаж) экранированного кабеля заводится на контакт разъёма, как жила.
const SCREENED = data({ ...HELU, screen: "оплётка луж. медь" });
const shieldUnit = (S: GenState, d: GenData = SCREENED) => pinUnits(g(S, d), S).find((u) => u.shield)!;
/** Пин-в-пин 19 жил, экран — на контакт 20 разъёма `ep` (и `ep2`, если задан). */
function shieldOn(ep: string, ep2?: string): GenState {
  let S = pinToPin(SCREENED);
  const u = shieldUnit(S);
  S = assignPin(SCREENED, S, ep, u.idx, u.unit, 20);
  return ep2 ? assignPin(SCREENED, S, ep2, u.idx, u.unit, 20) : S;
}

it("a screened cable has one shield in the palette after its cores; it is not a core: no warning while it is not on a contact", () => {
  const S = pinToPin(SCREENED);
  const units = pinUnits(g(S, SCREENED), S);
  expect(units).toHaveLength(20);
  const u = shieldUnit(S);
  expect([u.num, u.unit, u.name, u.color, u.at]).toEqual(["1.Э", 19, "экран Helukabel 15201", SHIELD_COLOR, {}]);
  expect(routingWarnings(SCREENED, S)).toEqual([]);
  // Неэкранированный кабель — без экрана.
  expect(pinUnits(g(pinToPin()), pinToPin()).some((x) => x.shield)).toBe(false);
});

it("shield on contact 20 at both ends: a connection, a 20th terminal per end, its own ETK row and «twist into a lead»", () => {
  const S = shieldOn("a1", "b1");
  const idx = S.wires[0].idx;
  expect(S.endpoints.find((e) => e.id === "a1")!.pins![idx]).toBe(Array.from({ length: 20 }, (_, i) => i + 1).join(", "));
  expect(shieldUnit(S).at).toEqual({ a1: 20, b1: 20 });
  const links = g(S, SCREENED).connections_();
  expect(links.warnings).toEqual([]);
  expect(links.list).toHaveLength(20);
  expect(links.list[19]).toMatchObject({ wire: "Helukabel 15201 · экран", shield: true, from: { end: "А1", pin: 20 }, to: { end: "В1", pin: 20 } });
  const prs = opsOf(S, SCREENED).filter((op) => op.type === "prsTerm");
  expect(prs.map(terms)).toEqual([20, 20]);
  expect(prs[0].wires).toEqual([expect.objectContaining({ qty: 1, cores: 19 }), expect.objectContaining({ name: "Экран Helukabel 15201", qty: 1 })]);
  expect(prs[0].pins).toEqual([Array.from({ length: 19 }, (_, i) => i + 1).join(", "), "20"]);
  expect(g(S, SCREENED).getActiveOps().filter((op) => op.tmplWant === "TWST_SHILED").map((op) => op.label)).toEqual([
    "Скрутка экрана в вывод на контакт А1·20 — 1 шт", "Скрутка экрана в вывод на контакт В1·20 — 1 шт",
  ]);
  expect(pinoutEnds(g(S, SCREENED), S).map((end) => end.summary)).toEqual([
    "19 жил + экран (конт. 20), прямой порядок", "19 жил + экран (конт. 20), прямой порядок",
  ]);
  expect(routingWarnings(SCREENED, S)).toEqual([]);
  // Контакт 20 у 19-контактного разъёма — проверка «контакта нет» видит и экран.
  expect(pinFitWarnings(pinoutEnds(g(S, SCREENED), S), { a1: 19, b1: 20 })).toEqual([
    "А1 (DF1B-18DS-2.5RC): 19 контактов, а 20 проводов — всем контакта не хватит.", "А1 (DF1B-18DS-2.5RC): контакта 20 нет — у разъёма 19 контактов.",
  ]);
});

it("shield on a contact at А only (grounded at one end): no pairing warning, В insulates its shield", () => {
  const S = shieldOn("a1");
  const links = g(S, SCREENED).connections_();
  expect(links.warnings).toEqual([]);
  expect(links.list).toHaveLength(19);
  expect(opsOf(S, SCREENED).filter((op) => op.type === "prsTerm").map(terms)).toEqual([20, 19]);
  expect(g(S, SCREENED).getActiveOps().filter((op) => op.tmplWant === "TWST_SHILED").map((op) => op.label)).toEqual([
    "Скрутка экрана в вывод на контакт А1·20 — 1 шт", "Скрутка и изоляция экрана В1 — 1 шт",
  ]);
  expect(g(S, SCREENED).cableEnds_("B")[0].unused).toEqual([]);
  // Автозаполнение раздаёт жилы заново, а экран на контакте 20 оставляет.
  const again = autoFillPinout(SCREENED, S, { a1: 20, b1: 19 }).state;
  expect(shieldUnit(again).at).toEqual({ a1: 20 });
  expect(pinUnits(g(again, SCREENED), again).filter((u) => !u.shield).every((u) => u.at.a1 === u.unit + 1)).toBe(true);
  // Снять экран с контакта — снова «скрутить и изолировать».
  const off = unassignPin(SCREENED, S, "a1", S.wires[0].idx, 19);
  expect(shieldUnit(off).at).toEqual({});
  expect(opsOf(off, SCREENED).filter((op) => op.type === "prsTerm").map(terms)).toEqual([19, 19]);
});

it("autofill steps around a shield already on a contact; the shield on a soldered connector gets its own heat-shrink", () => {
  let S = pinToPin(SCREENED);
  const u = shieldUnit(S);
  S = assignPin(SCREENED, S, "a1", u.idx, u.unit, 1);
  const filled = autoFillPinout(SCREENED, S, { a1: 20, b1: 19 });
  const a1 = pinUnits(g(filled.state, SCREENED), filled.state);
  expect(a1.find((x) => x.shield)!.at).toEqual({ a1: 1 });
  expect(a1.filter((x) => !x.shield).map((x) => x.at.a1)).toEqual(Array.from({ length: 19 }, (_, i) => i + 2));
  expect(filled.warnings).toEqual([]);
  // Пайка на А: ТУТ на 19 жил и экран — 20.
  const solder = { ...filled.state, endpoints: filled.state.endpoints.map((e) => (e.id === "a1" ? { ...e, method: "solder" as const, methodAuto: false, tutLen: "10" } : e)) };
  expect(g(solder, SCREENED).getActiveOps().filter((op) => op.type === "cutTut").reduce((s, op) => s + (op.tutCount || 0), 0)).toBe(20);
});

it("«Построить чертёж»: a shield on contacts at both ends is a wire «Экран …» inside the same jacketed cable", () => {
  const S = { ...shieldOn("a1", "b1"), drawingLength: 300, sharedLengthText: "300" };
  const plan = planFromGenerator(SCREENED, S);
  expect(plan.wires).toHaveLength(20);
  expect(plan.wires[19]).toMatchObject({ mark: "Экран Helukabel 15201", shield: true, section: "", color: "#B0A089", from: { pin: 20 }, to: { pin: 20 } });
  expect(plan.cables[0].wireKeys).toHaveLength(20);
  const free: ConnectorSource = { kind: "free", contactCount: null, libraryCode: null };
  const { document } = buildHarnessDocument(plan, Object.fromEntries(plan.connectors.map((c) => [c.key, free])));
  expect(document.cables[0].memberWireIds).toHaveLength(20);
  expect(document.connectors[0].contacts.find((c) => c.number === 20)).toMatchObject({ wire: "Экран Helukabel 15201" });
});