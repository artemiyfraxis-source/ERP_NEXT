// @vitest-environment node
import { expect, it } from "vitest";
import { housingSeries, latinArt, pairsFromSeries, pickTerminals, pinCountFromArt } from "./terminalPick";
import { addEndpoint, initState, makeGen, updateEndpoint, type Comp, type GenData } from "./tcGenLogic";

it("reads the contact count from the housing article", () => {
  expect(["SMR-02V-B", "SMR-04V-B", "SMP-02V-BC", "I-DS1071-SCB01X3", "I-DS1071-SCB02X3", "XHP-3", "PHR-4", "43025-0400", "GHR-06V-S", "LP-12"].map(pinCountFromArt))
    .toEqual([2, 4, 2, 3, 6, 3, 4, 4, 6, null]);
  // Кириллица вместо латиницы в артикуле — та же серия.
  expect(pinCountFromArt("SМR-04V-В")).toBe(4);
  expect(latinArt("SМR-02V-B")).toBe("SMR-02V-B");
  expect(latinArt("НВ4 0,5 красный")).toBe("НВ4 0,5 красный");
  expect(["SMR-02V-B", "SMR-04V-B", "SMP-02V-BC", "I-DS1071-SCB02X3", "DF1B-18DS-2.5RC", "DF1B-5EP-2.5RC"].map(housingSeries))
    .toEqual(["SMR-V-B", "SMR-V-B", "SMP-V-BC", "I-DS1071-SCB", "DF1B-DS-2.5RC", "DF1B-EP-2.5RC"]);
});

const C = (id: string, type: string, art: string, qty: number): Comp => ({ id, type, name: art, art, qty });
const data = (components: Comp[]): GenData => ({ components, assemblyInfo: {}, templates: [], ops: [], terRecords: [], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [] });

it("never guesses: two terminals and nothing that tells them apart — no terminal is picked", () => {
  const D = data([C("x", "Соед", "XHP-4", 1), C("p", "Соед", "PHR-4", 1), C("cab", "Соед", "LP-12-J04", 1), C("t1", "Терминал", "AAA-1", 4), C("t2", "Терминал", "BBB-2", 4),
    C("w", "Провод", "МГТФ 0,12", 1)]);
  let S = addEndpoint(initState(D), "A");
  S = updateEndpoint(D, S, S.endpoints.at(-1)!.id, { connId: "x" });
  expect(S.tab).toBe("complex");
  expect(pickTerminals(makeGen(D, S), S)).toEqual({});
  expect(S.endpoints.at(-1)!.termId).toBe("");
});

it("the connector base's housing → terminal pairs decide first", () => {
  const D = data([C("x", "Соед", "XHP-4", 1), C("p", "Соед", "PHR-4", 1), C("cab", "Соед", "LP-12-J04", 1), C("t1", "Терминал", "SXH-001T-P0.6", 4), C("t2", "Терминал", "SPH-002T-P0.5S", 4)]);
  const S = initState(D);
  const ep = S.endpoints[0];
  const with_ = { ...S, endpoints: S.endpoints.map((e) => (e.id === ep.id ? { ...e, connId: "x" } : e)) };
  expect(pickTerminals(makeGen(D, with_), with_, { XHP4: ["SXH001TP06"] })[ep.id]).toBe("t1");
});

it("reads the pairs from a connector-series answer: the housing's own terminals, else its series' terminals and contacts", () => {
  const answer = { match: { series: "jst-sm", housing: {} }, series: [{ id: "jst-sm", terminals: [{ article: "SYM-001T-P0.6" }], contacts: [{ article: "SHF-001T-0.8BS", wire: "" }] }] };
  expect(pairsFromSeries("SMR-02V-B", answer)).toEqual(["SYM001TP06", "SHF001T08BS"]);
  expect(pairsFromSeries("SMR-02V-B", { match: { series: "jst-sm", terminals: ["SYM-001T-P0.6"] }, series: [] })).toEqual(["SYM001TP06"]);
  expect(pairsFromSeries("X", { match: null })).toEqual([]);
  expect(pairsFromSeries("X", "garbage")).toEqual([]);
});
