import { expect, it } from "vitest";
import {
  addBranch, assignPin, autoFillPinout, pinoutEnds, pinoutRecord, pinUnits, removeBranch, restorePinout, routingWarnings, setBranchMode, unassignPin,
} from "./pinoutCheck";
import { addEndpoint, initState, makeGen, switchGenTab, type GenData, type GenState } from "./tcGenLogic";

// Три конца: А1 — В1 и В2 (владелец 12.10, «трёхконцовый жгут»).
const PV = [
  { id: "r", type: "Провод", name: "ПВАМ 0,35 красный", art: "ПВАМ 0,35 красный", qty: 0.3 },
  { id: "k", type: "Провод", name: "ПВАМ 0,35 черный", art: "ПВАМ 0,35 черный", qty: 0.3 },
  { id: "y", type: "Провод", name: "ПВАМ 0,35 желтый", art: "ПВАМ 0,35 желтый", qty: 0.3 },
  { id: "w", type: "Провод", name: "ПВАМ 0,35 белый", art: "ПВАМ 0,35 белый", qty: 0.3 },
  { id: "c1", type: "Соед", name: "XH 4", art: "XHP-4", qty: 2 },
  { id: "c2", type: "Соед", name: "LP", art: "LP-12-J04SX-02-401", qty: 1 },
  { id: "t", type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6", qty: 12 },
];
const D3: GenData = {
  components: PV, assemblyInfo: { "Индекс": "КБ1", "Наименование": "Жгут трёхконцовый" },
  templates: [], ops: [], terRecords: [], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [],
};
function threeEnds(): GenState {
  const S1 = addEndpoint(switchGenTab(initState(D3), "complex"), "B");
  return { ...S1, endpoints: S1.endpoints.map((e) => ({ ...e, connId: e.side === "A" ? "c1" : e.id === "b1" ? "c2" : "c1" })) };
}
const g3 = (S: GenState) => makeGen(D3, S);
const idxOf = (S: GenState, compId: string) => S.wires.find((w) => w.compId === compId)!.idx;
const ep = (S: GenState, id: string) => S.endpoints.find((e) => e.id === id)!;
const pairs = (S: GenState, wire?: string) => g3(S).connections_().list.filter((l) => !wire || l.wire === wire)
  .map((l) => `${l.from.end}${l.from.pin}>${l.to.end}${l.to.pin}`);

it("three ends, nothing assigned yet: wires are on no B end, the generator says so instead of guessing", () => {
  const S = threeEnds();
  expect(S.endpoints.map((e) => e.id)).toEqual(["a1", "b1", "e2"]);
  expect(pinoutEnds(g3(S), S).map((e) => [e.key, e.multi, e.wires.length])).toEqual([["А1", false, 4], ["В1", true, 0], ["В2", true, 0]]);
  expect(pinUnits(g3(S), S).every((u) => !("b1" in u.at) && !("e2" in u.at))).toBe(true);
  expect(routingWarnings(D3, S)[0]).toContain("Сторона В: провода не розданы по разъёмам");
});

it("auto-fill splits the wires over the B ends by their contact count: 2 to B1, 2 to B2, pin to pin, no stubs", () => {
  const { state, notes, warnings } = autoFillPinout(D3, threeEnds(), { a1: 4, b1: 2, e2: 2 });
  expect(warnings).toEqual([]);
  expect(notes).toContain("Сторона В: провода разведены по разъёмам — В1: 1, 2; В2: 3, 4.");
  expect(ep(state, "a1").pins).toEqual({ 0: "1", 1: "2", 2: "3", 3: "4" });
  expect(ep(state, "b1").pins).toEqual({ 0: "1", 1: "2" });
  expect(ep(state, "e2").pins).toEqual({ 2: "1", 3: "2" });
  // Раздача генератора совпадает с контактами: ни пустых, ни лишних проводов на разъёме.
  expect(state.endpoints.map((e) => [e.id, g3(state).routedWireIdxs_(e)])).toEqual([["a1", [0, 1, 2, 3]], ["b1", [0, 1]], ["e2", [2, 3]]]);
  expect(g3(state).connections_().warnings).toEqual([]);
  expect(pairs(state)).toEqual(["А11>В11", "А12>В12", "А13>В21", "А14>В22"]);
  expect(routingWarnings(D3, state)).toEqual([]);
  expect(pinUnits(g3(state), state).map((u) => Object.keys(u.at).length)).toEqual([2, 2, 2, 2]);
});

it("auto-fill does not guess: unknown contact counts or too few contacts leave wires «не назначен»", () => {
  const unknown = autoFillPinout(D3, threeEnds(), { a1: 4 });
  expect(unknown.warnings).toContain("Сторона В: число контактов разъёмов неизвестно — раздайте провода по разъёмам на схеме.");
  expect(pinUnits(g3(unknown.state), unknown.state).every((u) => Object.keys(u.at).join() === "a1")).toBe(true);
  const few = autoFillPinout(D3, threeEnds(), { a1: 4, b1: 2, e2: 1 });
  expect(few.warnings.some((w) => w.startsWith("Сторона В: контактов разъёмов не хватает — не назначены: 4 · ПВАМ 0,35 белый"))).toBe(true);
  expect(routingWarnings(D3, few.state).some((w) => w.startsWith("Сторона В: не назначены на разъём — 4 · ПВАМ 0,35 белый"))).toBe(true);
});

it("a legacy state with routing but no pins keeps its routing and auto-fill pins the wires in order", () => {
  const S = threeEnds();
  const legacy = { ...S, endpoints: S.endpoints.map((e) => (e.id === "b1" ? { ...e, wireIdxs: [0, 1] } : e.id === "e2" ? { ...e, wireIdxs: [2, 3] } : e)) };
  expect(pinoutEnds(g3(legacy), legacy).map((e) => [e.key, e.wires.length])).toEqual([["А1", 4], ["В1", 2], ["В2", 2]]);
  const { state } = autoFillPinout(D3, legacy, { a1: 4, b1: 4, e2: 4 });
  expect(ep(state, "b1").pins).toEqual({ 0: "1", 1: "2" });
  expect(ep(state, "e2").pins).toEqual({ 2: "1", 3: "2" });
});

it("assigning a pin on a multi-end side routes the wire there and nowhere else; clearing it takes the wire off", () => {
  const S = threeEnds();
  const white = idxOf(S, "w");
  const a = assignPin(D3, S, "e2", white, 0, 2);
  expect(ep(a, "e2").pins).toEqual({ [white]: "2" });
  expect(g3(a).routedWireIdxs_(ep(a, "e2"))).toEqual([white]);
  expect(pinUnits(g3(a), a).find((u) => u.idx === white)!.at).toEqual({ a1: null, e2: 2 });
  expect(ep(unassignPin(D3, a, "e2", white, 0), "e2").pins).toEqual({});
});

/** Все четыре провода пин-в-пин А1 → В1 (у В1 четыре контакта), В2 свободен. */
const whiteOnB1 = () => autoFillPinout(D3, threeEnds(), { a1: 4, b1: 4, e2: 4 }).state;

it("autofill with room for all wires on the first B end leaves the second B end empty, not half-filled", () => {
  const S = whiteOnB1();
  expect(ep(S, "b1").pins).toEqual({ 0: "1", 1: "2", 2: "3", 3: "4" });
  expect(ep(S, "e2").pins).toEqual({});
  expect(pinoutEnds(g3(S), S).find((e) => e.key === "В2")!.wires).toEqual([]);
});

it("one pin to two outputs, splice: the extra wire runs only to the far end, the splice operations appear", () => {
  const S0 = whiteOnB1();
  const white = idxOf(S0, "w");
  const S = addBranch(D3, S0, { epId: "a1", idx: white, pin: 4 }, "e2", 2, "splice");
  const extra = S.branches![0].extra;
  expect(S.branches).toEqual([{ main: white, extra, mode: "splice", from: "a1", pin: 4, to: "e2", toPin: 2 }]);
  expect(S.spl).toMatchObject({ enabled: true, count: "1", wires: "2" });
  expect(S.wires.filter((w) => w.compId === "w")).toHaveLength(2);
  expect(S.wires.find((w) => w.idx === extra)).toMatchObject({ compId: "w", qty: 1 });
  expect(ep(S, "e2").pins).toEqual({ [extra]: "2" });
  expect(ep(S, "a1").pins![extra]).toBeUndefined();
  expect(g3(S).routedWireIdxs_(ep(S, "a1"))).not.toContain(extra);
  expect(g3(S).getActiveOps().map((o) => o.type)).toContain("splice");
  expect(pinoutEnds(g3(S), S).find((e) => e.key === "В2")!.wires.map((w) => [w.idx, w.text])).toEqual([[extra, "2"]]);
});

it("one pin to two outputs, double crimp: both wires share the source pin, the source end crimps double", () => {
  const S0 = whiteOnB1();
  const white = idxOf(S0, "w");
  const S = addBranch(D3, S0, { epId: "a1", idx: white, pin: 4 }, "e2", 2, "double");
  const extra = S.branches![0].extra;
  expect(ep(S, "a1").double).toBe(true);
  expect(ep(S, "a1").pins![white]).toBe("4");
  expect(ep(S, "a1").pins![extra]).toBe("4");
  expect(S.spl.enabled).toBe(false);
  expect(g3(S).connections_().warnings).toEqual([]);
  expect(pairs(S, "ПВАМ 0,35 белый")).toEqual(["А14>В14", "А14>В22"]);
  // Двойной обжим — контакт держит два провода, не «занят дважды».
  expect(pinoutEnds(g3(S), S).find((e) => e.key === "А1")!.dups).toEqual([]);
  // Переключение режима и снятие.
  const sp = setBranchMode(D3, S, extra, "splice");
  expect(sp.branches![0].mode).toBe("splice");
  expect(ep(sp, "a1").double).toBe(false);
  expect(sp.spl.enabled).toBe(true);
  const gone = removeBranch(D3, sp, sp.branches![0].extra);
  expect(gone.branches).toEqual([]);
  expect(gone.wires).toHaveLength(4);
  expect(gone.spl.enabled).toBe(false);
});

it("a wire from one source pin to two B ends is a branch of the connections, not a broken pairing", () => {
  const S0 = whiteOnB1();
  const S = assignPin(D3, S0, "e2", idxOf(S0, "w"), 0, 2);
  expect(g3(S).connections_().warnings).toEqual([]);
  expect(pairs(S, "ПВАМ 0,35 белый")).toEqual(["А14>В14", "А14>В22"]);
  expect(routingWarnings(D3, S).some((w) => w.startsWith("Разветвление: ПВАМ 0,35 белый — нужен сросток или перемычка"))).toBe(true);
});

it("branches survive build_config: the extra wire, its mode and pins come back after a reload", () => {
  const S0 = whiteOnB1();
  const S = addBranch(D3, S0, { epId: "a1", idx: idxOf(S0, "w"), pin: 4 }, "e2", 2, "double");
  const record = pinoutRecord(g3(S), S)!;
  expect(record.branches).toEqual([{ pos: 3, mode: "double", from: "А1", pin: 4, to: "В2", toPin: 2 }]);
  const back = restorePinout(D3, threeEnds(), JSON.parse(JSON.stringify({ pinout: record })));
  expect(back.branches).toHaveLength(1);
  expect(back.wires.filter((w) => w.compId === "w")).toHaveLength(2);
  expect(ep(back, "a1").double).toBe(true);
  expect(pairs(back).filter((p) => p.endsWith("В22"))).toEqual(["А14>В22"]);
});
