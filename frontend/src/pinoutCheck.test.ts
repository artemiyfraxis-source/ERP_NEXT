import { expect, it } from "vitest";
import {
  assignPin, autoFillPinout, confirmPinout, freeSide, pinFitWarnings, pinoutEnds, pinoutRecord, pinUnits, recolorWire, restorePinout, swapPins, unassignPin,
} from "./pinoutCheck";
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

// ── Редактор распиновки (владелец 12.10) ──
/** Контакты разъёма по кабелям формы: [id провода из состава, строка контактов]. */
const pinsOf = (S: GenState, epId: string) => {
  const ep = S.endpoints.find((e) => e.id === epId)!;
  return S.wires.filter((w) => w.compId).map((w) => [w.compId, ep.pins?.[w.idx] ?? ""]);
};
const OPEN_END: GenData = { ...D, components: [
  { id: "k", type: "Провод", name: "Силиконовый 30AWG черный", art: "Силиконовый 30AWG черный", qty: 0.2 },
  { id: "r", type: "Провод", name: "Силиконовый 30AWG красный", art: "Силиконовый 30AWG красный", qty: 0.2 },
  { id: "c", type: "Соед", name: "Колодка SH 2", art: "SHR-02V-S-B", qty: 1 },
  { id: "t", type: "Терминал", name: "Контакт SH", art: "SSH-003T-P0.2-H", qty: 2 },
] };

it("auto-fill: one connector — wires on pins 1…N in wire order, the other side stays free ends", () => {
  const S0 = initState(OPEN_END);
  expect(S0.endpoints.map((ep) => ep.side)).toEqual(["A"]);
  const { state, notes, warnings } = autoFillPinout(OPEN_END, S0, { a1: 2 });
  expect(state.endpoints[0].pins).toEqual({ [S0.wires[0].idx]: "1", [S0.wires[1].idx]: "2" });
  expect(notes).toEqual([]);
  expect(warnings).toEqual([]);
  // Контактов больше — свободные названы; число неизвестно — по порядку, с пометкой.
  expect(autoFillPinout(OPEN_END, S0, { a1: 4 }).notes).toEqual(["А1 (SHR-02V-S-B): 2 провода на 4 контакта, свободны 3–4."]);
  expect(autoFillPinout(OPEN_END, S0, {}).notes).toEqual(["А1 (SHR-02V-S-B): число контактов разъёма неизвестно — 2 провода по порядку на 1–2."]);
});

it("auto-fill: two connectors — pin to pin, wire k on А k and В k; a wire ×N takes N pins in a row", () => {
  const S0 = initState(D);
  const { state } = autoFillPinout(D, S0, { a1: 8, b1: 4 });
  expect(pinsOf(state, "a1")).toEqual([["w", "1"], ["g", "2"], ["r", "3"], ["k", "4"]]);
  expect(pinsOf(state, "b1")).toEqual([["w", "1"], ["g", "2"], ["r", "3"], ["k", "4"]]);
  expect(makeGen(D, state).connections_().list.every((l) => l.from.pin === l.to.pin)).toBe(true);
  const twice = updateWire(S0, S0.wires[0].idx, { qty: 2 });
  expect(pinsOf(autoFillPinout(D, twice, { a1: 8, b1: 8 }).state, "a1")).toEqual([["w", "1, 2"], ["g", "3"], ["r", "4"], ["k", "5"]]);
  // Двойной обжим: по два провода в контакт.
  const dbl = updateEndpoint(D, S0, "a1", { double: true });
  expect(pinsOf(autoFillPinout(D, dbl, { a1: 8, b1: 4 }).state, "a1")).toEqual([["w", "1"], ["g", "1"], ["r", "2"], ["k", "2"]]);
});

it("auto-fill: more wires than pins — the extra wires get no pin and are named in the warning", () => {
  const S0 = initState(D);
  const { state, warnings } = autoFillPinout(D, S0, { a1: 8, b1: 3 });
  expect(pinsOf(state, "b1")).toEqual([["w", "1"], ["g", "2"], ["r", "3"], ["k", ""]]);
  expect(warnings).toEqual(["В1 (XHP-4): 3 контакта, а 4 провода — без контакта: 4 · НВ4 0,2 черный."]);
  expect(pinFitWarnings(ends(S0), { a1: 8, b1: 3 })).toEqual(["В1 (XHP-4): 3 контакта, а 4 провода — всем контакта не хватит."]);
  expect(pinFitWarnings(ends(usbPinned()), { a1: 4 })).toEqual(["А1 (PHR-8): контактов 5, 7 нет — у разъёма 4 контакта."]);
});

it("assign: a wire into a pin; an occupied pin swaps the wires (or frees the other one when the wire had no pin)", () => {
  const S0 = initState(D);
  const [w, g, r] = S0.wires.map((x) => x.idx);
  let S = assignPin(D, S0, "a1", w, 0, 3);
  expect(pinsOf(S, "a1")[0]).toEqual(["w", "3"]);
  S = assignPin(D, S, "a1", g, 0, 5);
  // Белый — на 5: зелёный уходит на прежний контакт белого (3).
  S = assignPin(D, S, "a1", w, 0, 5);
  expect(pinsOf(S, "a1").slice(0, 2)).toEqual([["w", "5"], ["g", "3"]]);
  // Красный без контакта — на 5: белый остаётся без контакта.
  S = assignPin(D, S, "a1", r, 0, 5);
  expect(pinsOf(S, "a1").slice(0, 3)).toEqual([["w", ""], ["g", "3"], ["r", "5"]]);
  // Двойной обжим — второй провод в тот же контакт без вытеснения.
  const dbl = assignPin(D, updateEndpoint(D, S, "a1", { double: true }), "a1", w, 0, 5);
  expect(pinsOf(dbl, "a1").slice(0, 3)).toEqual([["w", "5"], ["g", "3"], ["r", "5"]]);
  expect(unassignPin(D, S, "a1", r, 0).endpoints[0].pins).toEqual({ [g]: "3" });
});

it("swap: two pins trade their wires, an empty pin is a move; the result stays a plain pin string for the ETK", () => {
  const S = usbPinned();
  const swapped = swapPins(D, S, "a1", 1, 3);
  expect(pinsOf(swapped, "a1")).toEqual([["w", "1"], ["g", "5"], ["r", "3"], ["k", "7"]]);
  expect(pinsOf(swapPins(D, S, "a1", 7, 8), "a1")[3]).toEqual(["k", "8"]);
  expect(swapPins(D, S, "a1", 2, 4)).toBe(S);
  expect(makeGen(D, swapped).connections_().list.find((l) => l.wire === "НВ4 0,2 красный")).toMatchObject({ from: { pin: 3 }, to: { pin: 1 } });
});

it("recolor: another wire for the cable; a colour already used by another cable trades places with it", () => {
  const S = usbPinned();
  const [w, g] = S.wires.map((x) => x.idx);
  const traded = recolorWire(S, w, "g");
  expect(traded.wires.slice(0, 2).map((x) => x.compId)).toEqual(["g", "w"]);
  expect(traded.endpoints).toEqual(S.endpoints);
  const units = pinUnits(makeGen(D, traded), traded);
  expect(units.find((u) => u.idx === w)).toMatchObject({ name: "НВ4 0,2 зеленый", color: "#2e9e4f", at: { a1: 3, b1: 2 } });
  expect(units.find((u) => u.idx === g)).toMatchObject({ name: "НВ4 0,2 белый", num: "2" });
  expect(recolorWire(S, w, "w")).toBe(S);
});

it("free ends of a one-sided cable follow the generator: tinned, stripped only or untouched", () => {
  const S0 = initState(OPEN_END);
  const free = (S: GenState) => freeSide(makeGen(OPEN_END, S), S);
  const off = { ...S0, tin: { enabled: false, wires: null, stripLen: "" } };
  expect(free(off)).toEqual({ side: "B", ends: S0.wires.map((w) => ({ idx: w.idx, kind: "none", text: "без обработки" })) });
  const strip = { ...off, freeStrip: { enabled: true, len: "4" } };
  expect(free(strip)!.ends.map((e) => [e.kind, e.text])).toEqual([["strip", "зачистка L=4 мм"], ["strip", "зачистка L=4 мм"]]);
  expect(free({ ...off, freeStrip: { enabled: true, len: "" } })!.ends[0].text).toBe("зачистка (длина не задана)");
  // Лудят только выбранные провода; длина — зачистка под лужение, иначе свободная зачистка.
  const tin = { ...strip, tin: { enabled: true, wires: [1], stripLen: "5" } };
  expect(free(tin)!.ends.map((e) => [e.kind, e.text])).toEqual([["strip", "зачистка L=4 мм"], ["tin", "лужение L=5 мм"]]);
  expect(free({ ...tin, tin: { enabled: true, wires: null, stripLen: "" } })!.ends.map((e) => e.text)).toEqual(["лужение L=4 мм", "лужение L=4 мм"]);
  // Разъёмы с обеих сторон — свободных концов нет.
  expect(free(initState(D))).toBeNull();
});
