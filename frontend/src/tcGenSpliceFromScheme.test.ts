// @vitest-environment node
import { expect, it } from "vitest";
import { makeGen, type GenData, type GenState } from "./tcGenLogic";
import { DS, harness } from "./tcGenRealHarness.fixture";

/* Сростки «Сложной сборки» берутся со схемы (владелец 18.10: «нет нормальной настройки сростков»): набранные число и провода
 * их не переопределяют, а работают только когда на схеме сростков нет. */
const sizes = (S: GenState, data: GenData = DS) => makeGen(data, S).spliceSizes_();
const material = (S: GenState) => JSON.stringify(makeGen(DS, S).buildConfig().ops);

it("drawn splices win over the typed count and wires; materials are the same as with the matching typed numbers", () => {
  const S = harness(DS);
  expect(sizes(S)).toEqual([4, 4, 3, 3]);
  const wrong = { ...S, spl: { ...S.spl, count: "9", wires: "2,2" } };
  expect(sizes(wrong)).toEqual([4, 4, 3, 3]);
  expect(makeGen(DS, wrong).spliceSlots_().map((s) => `${s.label}:${s.wires}:${s.count}`)).toEqual(["С1:4:1", "С2:4:1", "С3:3:1", "С4:3:1"]);
  expect(material(wrong)).toBe(material(S));
});

it("typed numbers apply when nothing is drawn; a disabled toggle splices nothing", () => {
  const S = harness(DS);
  const typed = { ...S, splices: [], spl: { ...S.spl, count: "2", wires: "3" } };
  expect(sizes(typed)).toEqual([3, 3]);
  expect(sizes({ ...typed, spl: { ...typed.spl, wires: "4,2,2" } })).toEqual([4, 2, 2]);
  expect(sizes({ ...S, spl: { ...S.spl, enabled: false } })).toEqual([]);
});

it("a drawn splice with a single end does not count; branch splices follow the drawn ones as two-wire splices", () => {
  const S = harness(DS);
  const lone = { ...S, splices: [...S.splices!, { id: "s9", ends: [S.splices![0].ends[0]] }] };
  expect(sizes(lone)).toEqual([4, 4, 3, 3]);
  const branch = { ...S, branches: [{ main: 0, extra: 99, mode: "splice" as const, from: "a1", pin: 1, to: "a1", toPin: 2 }] };
  expect(sizes(branch)).toEqual([4, 4, 3, 3, 2]);
});

it("spliceRows_ lists each drawn splice with its wires and where they come from", () => {
  const rows = makeGen(DS, harness(DS)).spliceRows_();
  expect(rows.map((r) => `${r.label}:${r.size}:${r.wires.length}`)).toEqual(["С1:4:4", "С2:4:4", "С3:3:3", "С4:3:3"]);
  expect(rows[0].wires.every((w) => w.art !== "")).toBe(true);
  expect(rows.flatMap((r) => r.wires).some((w) => /конт\. \d/.test(w.from))).toBe(true);
});

it("the fold of the core counts only for the sleeve crimping method", () => {
  const S = harness(DS);
  const fold = (method: "sleeve" | "solder") => makeGen(DS, { ...S, spl: { ...S.spl, method, fold: true } }).getActiveOps().some((o) => o.opKey === "foldCore");
  expect(fold("sleeve")).toBe(true);
  expect(fold("solder")).toBe(false);
});
