import { expect, it } from "vitest";
import { confirmPinout, pinoutEnds, pinoutRecord, restorePinout } from "./pinoutCheck";
import { initState, makeGen, updateEndpoint, updateWire, type GenData, type GenState } from "./tcGenLogic";

const USB_COMPONENTS = [
  { id: "w", type: "Провод", name: "НВ4 0,2 белый", art: "НВ4 0,2 белый", qty: 0.35 },
  { id: "g", type: "Провод", name: "НВ4 0,2 зеленый", art: "НВ4 0,2 зеленый", qty: 0.35 },
  { id: "r", type: "Провод", name: "НВ4 0,2 красный", art: "НВ4 0,2 красный", qty: 0.35 },
  { id: "k", type: "Провод", name: "НВ4 0,2 черный", art: "НВ4 0,2 черный", qty: 0.35 },
  { id: "ca", type: "Соед", name: "Колодка PH 8", art: "PHR-8", qty: 1 },
  { id: "cb", type: "Соед", name: "Колодка XH 4", art: "XHP-4", qty: 1 },
  { id: "ta", type: "Терминал", name: "Контакт PH", art: "SPH-002T-P0.5S", qty: 4 },
  { id: "tb", type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6", qty: 4 },
];

const D: GenData = {
  components: USB_COMPONENTS, assemblyInfo: { "Индекс": "КБ1", "Наименование": "Жгут интерфейсный 300мм" },
  templates: [], ops: [], terRecords: [], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [],
};

/** USB по примеру владельца: А1 красный 1, белый 3, зелёный 5, чёрный 7 — В1 1, 2, 3, 4. */
function usbPinned(S0: GenState = initState(D)): GenState {
  const idx = (compId: string) => S0.wires.find((w) => w.compId === compId)!.idx;
  const a = { [idx("r")]: "1", [idx("w")]: "3", [idx("g")]: "5", [idx("k")]: "7" };
  const b = { [idx("r")]: "1", [idx("w")]: "2", [idx("g")]: "3", [idx("k")]: "4" };
  return updateEndpoint(D, updateEndpoint(D, S0, "a1", { pins: a }), "b1", { pins: b });
}
const ends = (S: GenState) => pinoutEnds(makeGen(D, S), S);

it("asks to check every connector with wires, even an untouched or straight pinout", () => {
  const S = initState(D);
  expect(S.endpoints.map((ep) => ep.connId)).toEqual(["ca", "cb"]);
  expect(ends(S).map((end) => [end.key, end.conn, end.checked, end.summary])).toEqual([
    ["А1", "PHR-8", false, "контакты не заданы"], ["В1", "XHP-4", false, "контакты не заданы"],
  ]);
  const idx = S.wires.map((w) => w.idx);
  const straight = updateEndpoint(D, updateEndpoint(D, S, "a1", { pins: { [idx[0]]: "1", [idx[1]]: "2" } }), "b1", { pins: { [idx[0]]: "1", [idx[1]]: "2" } });
  expect(ends(straight).map((end) => [end.checked, end.summary])).toEqual([
    [false, "2 провода, прямой порядок, без контакта: 2"], [false, "2 провода, прямой порядок, без контакта: 2"],
  ]);
});

it("keeps the confirmation while the pinout is the same and drops it on any change of pins, wires or connector", () => {
  const S = confirmPinout(D, confirmPinout(D, usbPinned(), "a1"), "b1");
  const [a, b] = ends(S);
  expect(a.checked && b.checked).toBe(true);
  expect(a.summary).toBe("4 провода, перекрёст 3→2, 5→3, 7→4");
  // Тот же список другими словами — та же распиновка.
  const red = S.wires.find((w) => w.compId === "r")!.idx;
  expect(ends(updateEndpoint(D, S, "b1", { pins: { ...S.endpoints[1].pins, [red]: "1-1" } })).every((end) => end.checked)).toBe(true);
  // Контакт на В меняет пары и у А — снимаются оба.
  const moved = updateEndpoint(D, S, "b1", { pins: { ...S.endpoints[1].pins, [red]: "5" } });
  expect(ends(moved).map((end) => end.checked)).toEqual([false, false]);
  expect(ends(updateEndpoint(D, S, "a1", { connId: "cb" }))[0].checked).toBe(false);
  expect(ends(updateWire(S, red, { compId: "k" })).map((end) => end.checked)).toEqual([false, false]);
});

it("flags a contact taken by two wires unless the terminal is double crimped", () => {
  const S = usbPinned();
  const red = S.wires.find((w) => w.compId === "r")!.idx;
  const twice = updateEndpoint(D, S, "a1", { pins: { ...S.endpoints[0].pins, [red]: "3" } });
  expect(ends(twice)[0].dups).toEqual([3]);
  expect(ends(twice)[0].summary).toContain("контакт 3 дважды");
  expect(ends(updateEndpoint(D, twice, "a1", { double: true }))[0].dups).toEqual([]);
});

it("saves pins and checks into build_config.pinout and brings them back into a fresh form", () => {
  expect(pinoutRecord(makeGen(D, initState(D)), initState(D))).toBeNull();
  const S = confirmPinout(D, usbPinned(), "a1");
  const record = pinoutRecord(makeGen(D, S), S)!;
  expect(record.ends.map((end) => [end.end, end.conn, end.checked])).toEqual([["А1", "PHR-8", true], ["В1", "XHP-4", false]]);
  expect(record.ends[0].wires).toEqual([
    { pos: 0, art: "НВ4 0,2 белый", pins: "3" }, { pos: 1, art: "НВ4 0,2 зеленый", pins: "5" },
    { pos: 2, art: "НВ4 0,2 красный", pins: "1" }, { pos: 3, art: "НВ4 0,2 черный", pins: "7" },
  ]);

  const back = restorePinout(D, initState(D), { ops: [], pinout: record });
  expect(back.endpoints.map((ep) => ep.pins)).toEqual(S.endpoints.map((ep) => ep.pins));
  expect(ends(back).map((end) => end.checked)).toEqual([true, false]);
  // Другой разъём в форме — его распиновка не подставляется; старый конфиг без pinout — форма как есть.
  const other = restorePinout(D, updateEndpoint(D, initState(D), "a1", { connId: "cb" }), { pinout: record });
  expect(other.endpoints[0].pins).toBeUndefined();
  expect(restorePinout(D, initState(D), { ops: [] })).toEqual(initState(D));
});
