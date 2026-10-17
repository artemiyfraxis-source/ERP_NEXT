import { expect, it } from "vitest";
import { addBranch, autoFillPinout, coreColor, pinoutRecord, pinUnits, rememberColor, restorePinout, setCoreColor } from "./pinoutCheck";
import { setCustomColors } from "./wireColors";
import { initState, makeGen, type GenData } from "./tcGenLogic";

const D: GenData = {
  components: [
    { id: "cab", type: "Кабель", name: "Helukabel 15201", art: "Helukabel 15201", qty: 3 },
    { id: "ca", type: "Соед", name: "XH 4", art: "XHP-4", qty: 1 },
    { id: "cb", type: "Соед", name: "XH 4", art: "XHP-4", qty: 1 },
    { id: "t", type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6", qty: 8 },
  ],
  assemblyInfo: { "Индекс": "КБ1", "Наименование": "Кабель" },
  templates: [], ops: [], terRecords: [], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [],
};
/** Многожильный кабель ×4: жилы 1.1…1.4. */
function cable() {
  const S0 = initState(D);
  return { ...S0, wires: S0.wires.map((w) => ({ ...w, qty: 4 })) };
}
const units = (S: ReturnType<typeof cable>) => pinUnits(makeGen(D, S), S);

it("a core colour: one colour and two colours get canonical names, hex and stripe; clearing returns the colour of the name", () => {
  const S = cable();
  const idx = S.wires[0].idx;
  expect(units(S).map((u) => [u.colorName, u.color])).toEqual([["", null], ["", null], ["", null], ["", null]]);
  const one = setCoreColor(D, S, idx, 1, "красный");
  expect(units(one).map((u) => u.colorName)).toEqual(["", "красный", "", ""]);
  expect(units(one)[1]).toMatchObject({ color: "#d93a32", stripe: null, baseName: "красный" });
  // Двухцветная: основной белый, полоса синяя; написание алиасом приводится к названию палитры.
  const two = setCoreColor(D, one, idx, 2, "белый-синий");
  expect(units(two)[2]).toMatchObject({ colorName: "бело-синий", color: "#ffffff", stripe: "#2b5fd9", baseName: "белый" });
  expect(two.wires[0].coreColors).toEqual(["", "красный", "бело-синий", ""]);
  expect(coreColor("ж/з")).toMatchObject({ name: "жёлто-зелёный", baseName: "жёлтый", stripe: "#2e9e4f" });
  // Не из палитры — игнорируется; сброс.
  expect(setCoreColor(D, two, idx, 0, "лазурный")).toBe(two);
  const cleared = setCoreColor(D, setCoreColor(D, two, idx, 1, null), idx, 2, null);
  expect(cleared.wires[0].coreColors).toBeUndefined();
  expect(units(cleared).every((u) => u.colorName === "")).toBe(true);
});

it("core colours go to build_config wires and survive a reload with the pinout record", () => {
  const S = setCoreColor(D, setCoreColor(D, cable(), cable().wires[0].idx, 0, "синий"), cable().wires[0].idx, 3, "бело-синий");
  const filled = autoFillPinout(D, S, { a1: 8, b1: 8 }).state;
  const config = makeGen(D, filled).buildConfig() as { wires: Array<{ coreColors?: string[] }> };
  expect(config.wires[0].coreColors).toEqual(["синий", "", "", "бело-синий"]);
  const record = pinoutRecord(makeGen(D, filled), filled)!;
  expect(record.cores).toEqual([{ pos: 0, unit: 0, color: "синий" }, { pos: 0, unit: 3, color: "бело-синий" }]);
  const back = restorePinout(D, cable(), JSON.parse(JSON.stringify({ pinout: record })));
  expect(back.wires[0].coreColors).toEqual(["синий", "", "", "бело-синий"]);
});

it("a custom colour (15.10) goes to build_config with its hex, survives a reload and is painted on the unit", () => {
  const idx = cable().wires[0].idx;
  const S = setCoreColor(D, rememberColor(cable(), "Болотный", "#6B7A3A"), idx, 1, "болотный");
  expect(S.colorHex).toEqual({ болотный: "#6b7a3a" });
  expect(units(S)[1]).toMatchObject({ colorName: "болотный", color: "#6b7a3a", stripe: null });
  expect(rememberColor(S, "красный", "#000000")).toBe(S); // palette names are not redefined
  expect(rememberColor(S, "бурый", "коричневый")).toBe(S); // not a hex
  const filled = autoFillPinout(D, S, { a1: 8, b1: 8 }).state;
  const config = JSON.parse(JSON.stringify(makeGen(D, filled).buildConfig())) as { wires: Array<{ coreColors?: string[] }>; colorHex?: Record<string, string> };
  expect(config.wires[0].coreColors).toEqual(["", "болотный", "", ""]);
  expect(config.colorHex).toEqual({ болотный: "#6b7a3a" });
  const record = pinoutRecord(makeGen(D, filled), filled)!;
  setCustomColors(null);
  const back = restorePinout(D, cable(), { pinout: record, colorHex: config.colorHex });
  expect(back.wires[0].coreColors).toEqual(["", "болотный", "", ""]);
  expect(back.colorHex).toEqual({ болотный: "#6b7a3a" });
  // Не использован ни на одной жиле — в карту не уходит.
  const unused = makeGen(D, rememberColor(cable(), "лазурный", "#007ba7")).buildConfig() as { colorHex?: unknown };
  expect(unused.colorHex).toBeUndefined();
});

it("a branch keeps the colour of the core it was taken from", () => {
  const S0 = cable();
  const idx = S0.wires[0].idx;
  const colored = setCoreColor(D, setCoreColor(D, S0, idx, 1, "жёлтый"), idx, 2, "красно-чёрный");
  const switched = { ...colored, tab: "complex" as const };
  const filled = autoFillPinout(D, switched, { a1: 8, b1: 8 }).state;
  const branched = addBranch(D, filled, { epId: "a1", idx, pin: 3 }, "b1", 7, "double");
  const extra = branched.branches![0].extra;
  expect(branched.wires.find((w) => w.idx === extra)!.coreColors).toEqual(["красно-чёрный"]);
});
