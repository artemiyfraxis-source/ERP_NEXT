import { expect, it } from "vitest";
import { autoFillPinout, coreCount, pinoutEnds, pinUnits, routingWarnings, assignPin, unassignPin } from "./pinoutCheck";
import { initState, makeGen, updateWire, type GenData, type GenState } from "./tcGenLogic";

// Многожильный кабель в оболочке: жила — единица распиновки (владелец 12.10, Helukabel 15201 на 19 жил).
const D: GenData = {
  components: [
    { id: "cab", type: "Кабель", name: "Helukabel 15201", art: "Helukabel 15201", qty: 3 },
    { id: "ca", type: "Соед", name: "DF1B-18DS-2.5RC", art: "DF1B-18DS-2.5RC", qty: 1 },
    { id: "cb", type: "Соед", name: "DF1B-5EP-2.5RC", art: "DF1B-5EP-2.5RC", qty: 1 },
    { id: "ta", type: "Терминал", name: "DF1B-2428SCFA", art: "DF1B-2428SCFA", qty: 19 },
    { id: "tb", type: "Терминал", name: "DF1-PD2428SCFB", art: "DF1-PD2428SCFB", qty: 19 },
  ],
  assemblyInfo: { "Индекс": "КБ1", "Наименование": "Кабель Helukabel" },
  templates: [], ops: [], terRecords: [], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [],
};
function cable19(): GenState {
  const S = initState(D);
  return updateWire(S, S.wires[0].idx, { cores: 19, qty: 1 });
}
const g = (S: GenState) => makeGen(D, S);

it("a 19-core cable is 19 cores in the pinout, each pinned and coloured on its own; the cable stays one piece for the cut", () => {
  const S = cable19();
  expect(S.wires).toHaveLength(1);
  expect(coreCount(g(S), S.wires[0])).toBe(19);
  const units = pinUnits(g(S), S);
  expect(units.map((u) => u.num)).toEqual(Array.from({ length: 19 }, (_, i) => `1.${i + 1}`));
  expect(pinoutEnds(g(S), S).map((end) => [end.key, end.wires[0].qty])).toEqual([["А1", 19], ["В1", 19]]);
  // Кабель режется один раз; терминалов — по жилам на каждом конце.
  expect(g(S).buildConfig().wires).toEqual([expect.objectContaining({ art: "Helukabel 15201", qty: 1, cores: 19 })]);
});

it("pin to pin: 19 cores go A 1…19 → B 1…19, and the generator pairs them one to one", () => {
  const { state, warnings } = autoFillPinout(D, cable19(), { a1: 19, b1: 19 });
  expect(warnings).toEqual([]);
  const idx = state.wires[0].idx;
  expect(state.endpoints[0].pins).toEqual({ [idx]: Array.from({ length: 19 }, (_, i) => i + 1).join(", ") });
  const links = g(state).connections_();
  expect(links.warnings).toEqual([]);
  expect(links.list).toHaveLength(19);
  expect(links.list.every((l) => l.from.pin === l.to.pin)).toBe(true);
  expect(routingWarnings(D, state)).toEqual([]);
});

it("an unused core is flagged: contacts for 18 of 19 cores — the free one is named, assigning it clears the warning", () => {
  const filled = autoFillPinout(D, cable19(), { a1: 19, b1: 19 }).state;
  const idx = filled.wires[0].idx;
  // Жила 1.19 снята с обоих разъёмов.
  const open = unassignPin(D, unassignPin(D, filled, "a1", idx, 18), "b1", idx, 18);
  expect(routingWarnings(D, open)).toEqual(["Не задействованы жилы: 1.19 · Helukabel 15201 — назначьте контакт или подрежьте и заизолируйте."]);
  const back = assignPin(D, assignPin(D, open, "a1", idx, 18, 19), "b1", idx, 18, 19);
  expect(routingWarnings(D, back)).toEqual([]);
});

it("fewer contacts than cores: the cores without a contact are named in the auto-fill warning", () => {
  const { warnings } = autoFillPinout(D, cable19(), { a1: 18, b1: 5 });
  expect(warnings).toHaveLength(2);
  expect(warnings[0]).toContain("А1 (DF1B-18DS-2.5RC): 18 контактов, а 19 проводов — без контакта: 1.19");
  expect(warnings[1]).toContain("В1 (DF1B-5EP-2.5RC): 5 контактов, а 19 проводов");
});
