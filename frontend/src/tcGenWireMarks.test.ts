// @vitest-environment node
import { expect, it } from "vitest";
import { complexByHand, initState, makeGen, toggleLengthMode, updateEndpoint, type GenState } from "./tcGenLogic";
import { pickWireMarkTube, tubeBeforeShrink } from "./tcGenWireMarks";
import { assignPinJoined, joinSplice, renameSplice, setTail, spliceLabel } from "./tcGenSplices";
import { pinConns, pinRows, spliceLines } from "./tcGenPinTable";
import { pinoutEnds, pinUnits } from "./pinoutCheck";
import { DSA, end, fullHarness, id } from "./tcGenRealHarness.fixture";

/*
 * Маркировка проводов «откуда → куда» (владелец 26.10, 708К.5 п.8): по две метки на провод, «X1.1 to X2.2» и «X2.2 to X1.1».
 * Жгут из трёх проводов: разъём XS1 (сторона А) → разъём стороны В (без обозначения — X2), провод в сросток С1 и провод со
 * свободным концом.
 */
function threeWires(): GenState {
  let S = complexByHand(initState(DSA));
  const a = id(S, "A", 1), b = id(S, "B", 1);
  S = updateEndpoint(DSA, S, a, { connId: "csmr4" });
  S = updateEndpoint(DSA, S, a, { label: "XS1" });
  S = updateEndpoint(DSA, S, b, { connId: "c01" });
  S = toggleLengthMode({ ...S, wires: [["w2", 200], ["g2", 200], ["y2", 200]].map(([compId, length], idx) => ({ idx, compId: compId as string, length: length as number, qty: 1 })), nextWireIdx: 3 }, 0);
  S = assignPinJoined(DSA, S, a, 0, 0, 1); S = assignPinJoined(DSA, S, b, 0, 0, 2);
  S = assignPinJoined(DSA, S, a, 1, 0, 2); S = joinSplice(DSA, S, end(1, 0, "B"), "new");
  S = assignPinJoined(DSA, S, a, 2, 0, 3); S = setTail(DSA, S, end(2, 0, "B"));
  return S;
}
const on = (S: GenState, patch: Partial<NonNullable<GenState["wireMarks"]>> = {}): GenState => ({ ...S, wireMarks: { enabled: true, mat: "", len: "", dist: "", ...patch } });
const keysOf = (S: GenState) => makeGen(DSA, S).getActiveOps().map((o) => o.opKey);

it("texts go both ways: end A «from to to», end B the reverse; a splice end is named С1; a free end is skipped with the reason", () => {
  const list = makeGen(DSA, on(threeWires())).wireMarkList_();
  expect(list.rows.map((r) => [r.num, r.textA, r.textB])).toEqual([["1", "XS1.1 to X2.2", "X2.2 to XS1.1"], ["2", "XS1.2 to С1", "С1 to XS1.2"]]);
  expect(list.skipped.map((s) => `${s.num}: ${s.reason}`)).toEqual(["3: свободный конец В"]);
});

it("unmarked connectors take the generator names X1…, side A first then B; an edited text wins, an empty one returns the generated", () => {
  let S = threeWires();
  S = { ...S, endpoints: S.endpoints.map((e) => (e.side === "A" ? { ...e, label: "" } : e)) };
  expect(makeGen(DSA, on(S)).wireMarkList_().rows[0].textA).toBe("X1.1 to X2.2");
  const edited = on(S, { texts: { "0.0:A": "Л-1 начало", "0.0:B": "  " } });
  const row = makeGen(DSA, edited).wireMarkList_().rows[0];
  expect([row.textA, row.textB]).toEqual(["Л-1 начало", "X2.2 to X1.1"]);
});

it("operations: print (2 per wire), put on before any connector work, shrink after; the texts and the lengths travel in the config", () => {
  const S = on(threeWires(), { len: "12", dist: "25", mat: "ТТК 3/1мм, черная" });
  const ops = makeGen(DSA, S).getActiveOps();
  const keys = ops.map((o) => o.opKey);
  const cut = ops.find((o) => o.opKey === "cutWireMark")!;
  expect(cut.tutCount).toBe(4);
  expect(cut.markTexts).toEqual(["XS1.1 to X2.2", "X2.2 to XS1.1", "XS1.2 to С1", "С1 to XS1.2"]);
  expect(cut.tutParts).toEqual([expect.objectContaining({ count: 4, len: "12" })]);
  expect(cut.label).toMatch(/^Печать и резка маркировки проводов — 4 шт × 12 мм/);
  expect(ops.find((o) => o.opKey === "onWireMark")!.label).toBe("Надеть маркировку на провода — 4 шт, 25 мм от конца");
  const put = keys.indexOf("onWireMark"), heat = keys.indexOf("heatWireMark");
  expect(keys.indexOf("cutWireMark")).toBeLessThan(put);
  expect(heat).toBeGreaterThan(put);
  const terminal = ops.findIndex((o) => /^(insTerm|crimpTerm|solder|insHousing)/.test(o.type) || o.epId);
  if (terminal >= 0) expect(put).toBeLessThan(terminal);
  const cfg = makeGen(DSA, S).buildConfig() as { ops: Array<Record<string, unknown>> };
  const op = cfg.ops.find((o) => o.opKey === "cutWireMark")!;
  expect(op).toMatchObject({ wireMarks: true, markTube: true, markDist: "25", tutCount: 4, markTexts: cut.markTexts });
});

it("scheme: the marked wires pass «Провода с маркировкой» between the cut and their next block", () => {
  const g = makeGen(DSA, on(threeWires()));
  g.getActiveOps();
  const graph = g.pfGraph()!;
  expect(graph.blocks.find((b) => b.key === "pf-wiremark")?.name).toBe("Провода с маркировкой");
  expect(graph.blocks.find((b) => b.key === "pf-wmprint")?.kit).toBe(true);
  expect(graph.edges.some((e) => e.from === "pf-wmprint" && e.to === "pf-wiremark")).toBe(true);
  expect(graph.edges.some((e) => e.to === "pf-wiremark" && e.from.startsWith("pf-cutWire"))).toBe(true);
  expect(graph.edges.some((e) => e.from === "pf-wiremark")).toBe(true);
});

it("off by default and a saved form without the field open unchanged", () => {
  const S = fullHarness(DSA);
  expect(S.wireMarks).toBeUndefined();
  const same = (a: GenState, b: GenState) => JSON.stringify(makeGen(DSA, a).getActiveOps()) === JSON.stringify(makeGen(DSA, b).getActiveOps());
  expect(same(S, { ...S, wireMarks: { enabled: false, mat: "", len: "", dist: "" } })).toBe(true);
  expect(keysOf(S)).not.toContain("cutWireMark");
  expect(JSON.stringify(makeGen(DSA, S).buildConfig())).not.toMatch(/wireMarks|markTexts/);
});

it("the tube defaults by the wire Ø: the smallest one that fits with a margin", () => {
  expect(tubeBeforeShrink('Маркировка 4/2мм')).toBe(4);
  expect(pickWireMarkTube(["ТУТ 6/3мм", "ТУТ 3/1мм", "ТУТ 4/2мм"], 1.1)).toBe("ТУТ 3/1мм");
  expect(pickWireMarkTube(["ТУТ 6/3мм", "ТУТ 3/1мм", "ТУТ 4/2мм"], 2.6)).toBe("ТУТ 4/2мм");
  expect(pickWireMarkTube(["ТУТ 6/3мм", "ТУТ 3/1мм"], 9)).toBe("ТУТ 6/3мм");
  expect(pickWireMarkTube(["без размера"], 1)).toBe("без размера");
  expect(pickWireMarkTube([], 1)).toBe("");
});

it("kit marks with a ready inscription are not offered as the wire tube; БД.МАТ tubes are", () => {
  const data = { ...DSA, markMats: [{ name: "ТУТ 3/1мм белая", group: "ТУТ", unit: "м" }, { name: "Бирка", group: "Бирки", unit: "шт" }] };
  const g = makeGen(data, on(threeWires()));
  expect(g.wireMarkTubeOptions_()).toEqual(["ТУТ 3/1мм белая"]);
  const cut = g.getActiveOps().find((o) => o.opKey === "cutWireMark")!;
  expect([cut.tutArt, cut.tutParts?.[0].count]).toEqual(["ТУТ 3/1мм белая", 4]);
});

it("a splice name from the drawing (Spl1) flows into the marks, the pinout table, the splice operations and the PF blocks; an old draft keeps «С1»", () => {
  const old = threeWires();
  expect(old.splices![0].name).toBeUndefined();
  expect(makeGen(DSA, on(old)).wireMarkList_().rows[1].textA).toBe("XS1.2 to С1");
  const named = renameSplice(old, old.splices![0].id, "Spl1");
  expect(makeGen(DSA, on(named)).wireMarkList_().rows[1].textA).toBe("XS1.2 to Spl1");
  expect(spliceLabel(named, named.splices![0].id)).toBe("Spl1");
  expect(spliceLabel(renameSplice(named, named.splices![0].id, "  "), named.splices![0].id)).toBe("С1");
  const conns = pinConns(pinoutEnds(makeGen(DSA, named), named), named, {});
  const rows = pinRows(conns, pinUnits(makeGen(DSA, named), named), named, pinoutEnds(makeGen(DSA, named), named));
  expect(rows[1].cells.B.text).toBe("Spl1");
  expect(spliceLines(named, pinUnits(makeGen(DSA, named), named), conns)[0]).toMatchObject({ label: "Spl1", name: "Spl1" });

  const full = fullHarness(DSA);
  const ids = full.splices!.map((sp) => sp.id);
  const renamed = renameSplice(renameSplice(full, ids[0], "Spl1"), ids[1], "Spl2");
  const g = makeGen(DSA, renamed);
  const labels = g.getActiveOps().map((o) => o.label);
  expect(labels).toContain("Сростки Spl1, Spl2 — 2 шт по 4 провода");
  expect(labels.some((l) => /С1|С2/.test(l) && /Сростк|сростк/.test(l))).toBe(false);
  expect(g.pfGraph()!.blocks.map((b) => b.name)).toContain("Пайка Spl1, Spl2");
  expect(makeGen(DSA, full).getActiveOps().map((o) => o.label)).toContain("Сростки С1, С2 — 2 шт по 4 провода");
});
