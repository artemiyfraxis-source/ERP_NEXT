// @vitest-environment node
import { describe, expect, it } from "vitest";
import { genSnapshot, restoreFromSnapshot } from "./genBuildState";
import { addJumper } from "./pinoutCheck";
import { emptyState, makeGen, setTailMode, type GenState, type PhysicalConnection, type PhysicalTopology } from "./tcGenLogic";
import { lugRecordsOf } from "./tcGenLugs";
import { D663, W, end, harness663 } from "./tcGenComplex663.fixture";

const withTopology = (topology: PhysicalTopology): GenState => ({ ...harness663(), topology });
const connections = (S: GenState) => makeGen(D663, S).buildConfig().physicalConnections as PhysicalConnection[];

describe("physical topology", () => {
  it("exports every physical segment without inventing a direct wire through a splice", () => {
    const S = withTopology({ version: 1 });
    const gen = makeGen(D663, S), rows = connections(S);
    expect(rows).toHaveLength(gen.activeWires_().reduce((n, w) => n + gen.unitCount_(w) + gen.shieldCount_(w), 0));
    expect(new Set(rows.map((r) => `${r.idx}.${r.unit}`)).size).toBe(rows.length);
    for (const sp of S.splices!) for (const end of sp.ends) {
      const row = rows.find((r) => r.idx === end.idx && r.unit === end.unit)!;
      expect(end.side === "A" ? row.from : row.to).toMatchObject({ kind: "splice", spliceId: sp.id, end });
    }
    expect(gen.connections_().list).toEqual([]);
  });

  it("prepares jacket and both cores on each physical cable end before connector crimping", () => {
    const base = harness663();
    const S: GenState = { ...base, endpoints: base.endpoints.filter((e) => e.id === "a1"),
      wires: [{ idx: 0, compId: "kit-8", qty: 1, cores: 2, length: 80 }], splices: [], tails: [], rotor: false, measureMode: "cut",
      topology: { version: 1, ends: (["A", "B"] as const).flatMap((side, s) => [0, 1].map((unit) => ({
        end: end(0, unit, side), at: { kind: "pin" as const, epId: "a1", pin: s * 2 + unit + 1 },
      }))) } };
    const ops = makeGen(D663, S).getActiveOps();
    const keys = ops.map((o) => o.opKey);
    const crimp = ops.find((o) => o.epId === "a1" && o.type === "prsTerm")!;
    expect(crimp).toBeDefined();
    for (const side of ["A", "B"] as const) {
      const jacket = ops.find((o) => o.opKey === `cable.0.${side}.jacket`)!;
      const cores = ops.find((o) => o.opKey === `cable.0.${side}.cores.a1`)!;
      expect(jacket).toMatchObject({ tutCount: 1, strip: { a: side === "A", b: side === "B" } });
      expect(cores).toMatchObject({ tutCount: 2, physicalEnds: [end(0, 0, side), end(0, 1, side)], strip: { a: side === "A", b: side === "B" } });
      expect(keys.indexOf(jacket.opKey)).toBeLessThan(keys.indexOf(cores.opKey));
      expect(keys.indexOf(cores.opKey)).toBeLessThan(keys.indexOf(crimp.opKey));
    }
  });

  it("counts explicit multicore pin assignments when finding unused cores", () => {
    const base = harness663();
    const xs3 = base.endpoints.find((e) => e.label === "XS3")!;
    const S: GenState = { ...base, measureMode: "cut", wires: base.wires.map((w) => w.idx === W.mgtf ? { ...w, cores: 2, qty: 1 } : w),
      endpoints: base.endpoints.map((e) => e.id === xs3.id ? { ...e, pins: { ...e.pins, [W.mgtf]: "3" } } : e),
      topology: { version: 1, ends: [{ end: end(W.mgtf, 1, "A"), at: { kind: "pin", epId: xs3.id, pin: 4 } }] } };
    const gen = makeGen(D663, S);
    expect(gen.cableEnds_("A").find((entry) => entry.w.idx === W.mgtf)?.unused).toEqual([]);
    expect(gen.getActiveOps().some((o) => o.opKey === "idleA3" && o.wireIdxs?.includes(W.mgtf))).toBe(false);
  });

  it("preserves inherited tail lugs, their allowances and operations when topology is enabled", () => {
    const data = { ...D663, lugRecords: lugRecordsOf([{ id: "f", ter_kind: "lug", fields: {
      terComponent: "НШВИ", terArticle: "F-012", mm2From: "0,1", mm2To: "0,2", terStrip: "5", terLPlus: "2",
    } }]) };
    const on = setTailMode(harness663(data), "lug");
    const S = { ...on, tailLug: { ...on.tailLug!, type: "НШВИ" } };
    const legacy = makeGen(data, S);
    const physical = makeGen(data, { ...S, topology: { version: 1 } });
    const oldWires = legacy.buildConfig().wires as Array<{ length: number }>;
    const newConfig = physical.buildConfig();
    expect((newConfig.wires as Array<{ length: number }>)[W.mgtf].length).toBe(oldWires[W.mgtf].length);
    expect((newConfig.physicalConnections as PhysicalConnection[]).filter((c) => c.idx === W.mgtf).map((c) => c.to.kind)).toEqual(["lug", "lug"]);
    expect(physical.getActiveOps().filter((o) => /Lug/.test(o.opKey)).map((o) => o.opKey)).toEqual(legacy.getActiveOps().filter((o) => /Lug/.test(o.opKey)).map((o) => o.opKey));
    expect(legacy.getActiveOps().some((o) => /prsLug/.test(o.opKey))).toBe(true);
    const withTube = makeGen(data, { ...S, topology: { version: 1, tubes: [{ id: "tail", material: { compId: "kit-15" }, lengthMm: 20, count: 1,
      target: { kind: "branch", end: end(W.mgtf, 0, "B") }, phase: "preload" }] } }).getActiveOps();
    const keys = withTube.map((o) => o.opKey);
    expect(keys).toContain("prsLugF1");
    expect(keys.indexOf("tube.tail.preload")).toBeLessThan(keys.indexOf("prsLugF1"));
    expect(keys.indexOf("prsLugF1")).toBeLessThan(keys.indexOf("tube.tail.position"));
  });
  it("exports a segment between two distinct splices without collapsing it", () => {
    const base = harness663();
    const S: GenState = { ...base, topology: { version: 1 }, splices: base.splices!.map((s, i) => i === 1
      ? { ...s, ends: [...s.ends, end(W.xp1r, 0, "B")] } : s) };
    const row = connections(S).find((c) => c.idx === W.xp1r && c.unit === 0)!;
    expect(row.from).toMatchObject({ kind: "splice", spliceId: "s1", end: end(W.xp1r, 0, "A") });
    expect(row.to).toMatchObject({ kind: "splice", spliceId: "s2", end: end(W.xp1r, 0, "B") });
  });

  it("overrides only the assigned end and keeps the legacy splice at the other end", () => {
    const S = withTopology({ version: 1, ends: [{ end: end(W.xs1r, 0, "A"), at: { kind: "free" } }] });
    const row = connections(S).find((c) => c.idx === W.xs1r && c.unit === 0)!;
    expect(row.from).toMatchObject({ kind: "free", end: end(W.xs1r, 0, "A") });
    expect(row.to).toMatchObject({ kind: "splice", spliceId: "s1", end: end(W.xs1r, 0, "B") });
  });

  it("keeps different lug materials and operations on opposite ends", () => {
    const S = { ...withTopology({ version: 1, ends: [
      { end: end(W.mgtf, 0, "A"), at: { kind: "lug", material: { art: "L-A", name: "Lug A" }, crimp: "lug", strip: "4" } },
      { end: end(W.mgtf, 0, "B"), at: { kind: "lug", material: { art: "L-B", name: "Lug B" }, crimp: "ferrule", strip: "7" } },
    ] }), measureMode: "cut" as const };
    const row = connections(S).find((c) => c.idx === W.mgtf && c.unit === 0)!;
    expect(row.from).toMatchObject({ kind: "lug", material: { art: "L-A", name: "Lug A" } });
    expect(row.to).toMatchObject({ kind: "lug", material: { art: "L-B", name: "Lug B" } });
    const keys = makeGen(D663, S).getActiveOps().map((o) => o.opKey);
    for (const side of ["A", "B"]) {
      expect(keys).toContain(`end.${W.mgtf}.0.${side}.strip`);
      expect(keys).toContain(`end.${W.mgtf}.0.${side}.crimp`);
      expect(keys.indexOf(`end.${W.mgtf}.0.${side}.strip`)).toBeLessThan(keys.indexOf(`end.${W.mgtf}.0.${side}.crimp`));
    }
    const built = makeGen(D663, S).buildConfig().ops as Array<{ opKey: string; ep?: { termArt?: string } }>;
    expect(built.find((o) => o.opKey === `end.${W.mgtf}.0.A.crimp`)?.ep?.termArt).toBe("L-A");
    expect(built.find((o) => o.opKey === `end.${W.mgtf}.0.B.crimp`)?.ep?.termArt).toBe("L-B");
  });

  it("rejects an unknown material reference before generating operations", () => {
    const S = withTopology({ version: 1, ends: [{ end: end(W.mgtf, 0, "B"),
      at: { kind: "lug", material: { compId: "missing" }, crimp: "lug", strip: "5" } }] });
    expect(makeGen(D663, S).validate().error).toMatch(/материал|состав/i);
    expect(() => makeGen(D663, S).buildConfig()).toThrow(/материал|состав/i);
  });

  it("rejects duplicate tube ids before generating operations", () => {
    const placement = { id: "duplicate", material: { compId: "kit-15" }, lengthMm: 20, count: 1,
      target: { kind: "branch" as const, end: end(W.mgtf, 0, "A") }, phase: "preload" as const };
    const S = withTopology({ version: 1, tubes: [placement, { ...placement, target: { kind: "branch", end: end(W.mgtf, 1, "A") } }] });
    expect(makeGen(D663, S).validate().error).toMatch(/труб|дубли|повтор/i);
    expect(() => makeGen(D663, S).getActiveOps()).toThrow(/труб|дубли|повтор/i);
  });

  it("resolves a BOM material separately from an explicit material", () => {
    const S = { ...withTopology({ version: 1, ends: [
      { end: end(W.mgtf, 0, "A"), at: { kind: "lug", material: { compId: "kit-13" }, crimp: "tube", strip: "5" } },
      { end: end(W.mgtf, 0, "B"), at: { kind: "lug", material: { art: "CUSTOM", name: "Custom" }, crimp: "lug", strip: "5" } },
    ] }), measureMode: "cut" as const };
    const row = connections(S).find((c) => c.idx === W.mgtf && c.unit === 0)!;
    expect(row.from).toMatchObject({ kind: "lug", material: { art: D663.components[13].art, name: D663.components[13].name } });
    expect(row.to).toMatchObject({ kind: "lug", material: { art: "CUSTOM", name: "Custom" } });
  });

  it("adds each physical lug's allowance to a single-unit segment", () => {
    const initial = withTopology({ version: 1, ends: [
      { end: end(W.xp1r, 0, "A"), at: { kind: "lug", material: { art: "L-A", name: "A" }, crimp: "lug", strip: "4", lPlus: "2", lMinus: "4" } },
      { end: end(W.xp1r, 0, "B"), at: { kind: "lug", material: { art: "L-B", name: "B" }, crimp: "lug", strip: "4", lPlus: "3", lMinus: "6" } },
    ] });
    const S = { ...initial, splices: initial.splices!.map((sp) => ({ ...sp, ends: sp.ends.filter((e) => e.idx !== W.xp1r) })) };
    const length = (mode: GenState["measureMode"]) => (makeGen(D663, { ...S, measureMode: mode }).buildConfig().wires as Array<{ length: number }>)[W.xp1r].length;
    expect(length("inner")).toBe(35);
    expect(length("outer")).toBe(20);
    expect(length("cut")).toBe(30);
    const shared = makeGen(D663, { ...S, advancedLength: false, drawingLength: 100, sharedLengthText: "100" }).buildConfig();
    expect((shared.wires as Array<{ length: number }>)[W.xp1r].length).toBe(105);
  });

  it("rejects different end allowances within one multi-unit wire row", () => {
    const S = withTopology({ version: 1, ends: [{ end: end(W.mgtf, 0, "B"), at: {
      kind: "lug", material: { art: "L-A", name: "A" }, crimp: "lug", strip: "4", lPlus: "2",
    } }] });
    expect(() => makeGen(D663, S).buildConfig()).toThrow(/разн.*припуск.*раздел/i);
  });

  it("does not apply splice cutting strip to an explicit lug or free end", () => {
    const S: GenState = { ...withTopology({ version: 1, ends: [
      { end: end(W.mgtf, 0, "B"), at: { kind: "lug", material: { art: "L", name: "Lug" }, crimp: "lug", strip: "7" } },
      { end: end(W.mgtf, 1, "B"), at: { kind: "free" } },
    ] }), measureMode: "cut" };
    const ops = makeGen(D663, S).getActiveOps();
    const cuts = ops.filter((o) => /^cutWire/.test(o.type) && o.wireIdxs?.includes(W.mgtf));
    expect(cuts.length).toBeGreaterThan(0);
    expect(cuts.every((o) => !o.strip?.b)).toBe(true);
    expect(ops.find((o) => o.opKey === `end.${W.mgtf}.0.B.strip`)?.strip).toMatchObject({ b: true, lenB: "7" });
    expect(ops.some((o) => o.type === "splice")).toBe(true);
  });

  it("limits global tinning and free stripping to implicit free ends when side B has no endpoint", () => {
    const base = harness663();
    const indices = [W.xp1r, W.xp1b, W.xs1r, W.xs1b];
    const S: GenState = { ...base, endpoints: base.endpoints.filter((e) => e.side === "A"),
      wires: base.wires.filter((w) => indices.includes(w.idx)), splices: [], tails: [], measureMode: "cut",
      tin: { enabled: true, wires: null, stripLen: "6" }, freeStrip: { enabled: true, len: "9" },
      topology: { version: 1, ends: [
        { end: end(W.xp1r, 0, "B"), at: { kind: "lug", material: { art: "L", name: "Lug" }, crimp: "lug", strip: "4" } },
        { end: end(W.xp1b, 0, "B"), at: { kind: "free" } },
        { end: end(W.xs1r, 0, "B"), at: { kind: "pin", epId: "a1", pin: 3 } },
      ] } };
    const gen = makeGen(D663, S);
    expect(gen.tinWireIndices_()).toEqual([3]);
    const cuts = gen.getActiveOps().filter((o) => /^cutWire/.test(o.type));
    for (const idx of indices.slice(0, 3)) expect(cuts.find((o) => o.wireIdxs?.includes(idx))?.strip?.lenB || "").not.toBe("6");
    expect(cuts.find((o) => o.wireIdxs?.includes(W.xs1b))?.strip).toMatchObject({ b: true, lenB: "6" });
    const freeCuts = makeGen(D663, { ...S, tin: { ...S.tin, enabled: false } }).getActiveOps().filter((o) => /^cutWire/.test(o.type));
    for (const idx of indices.slice(0, 3)) expect(freeCuts.find((o) => o.wireIdxs?.includes(idx))?.strip?.lenB || "").not.toBe("9");
    expect(freeCuts.find((o) => o.wireIdxs?.includes(W.xs1b))?.strip).toMatchObject({ b: true, lenB: "9" });
  });

  it("retains old and new snapshots", () => {
    const old = harness663();
    const restoredOld = restoreFromSnapshot(D663.components, emptyState(), genSnapshot(D663.components, old, {}, {})).S;
    expect(restoredOld.topology).toBeUndefined();
    expect(restoredOld.splices).toEqual(old.splices);
    expect(JSON.stringify(makeGen(D663, restoredOld).buildConfig())).toBe(JSON.stringify(makeGen(D663, old).buildConfig()));
    const seeded = restoreFromSnapshot(D663.components, { ...emptyState(), topology: { version: 1 } }, genSnapshot(D663.components, old, {}, {})).S;
    expect(seeded).not.toHaveProperty("topology");
    const S = withTopology({ version: 1, ends: [{ end: end(W.mgtf, 0, "B"), at: { kind: "free" } }], tubes: [
      { id: "b", material: { compId: "kit-15" }, lengthMm: 20, count: 2, target: { kind: "branch", end: end(W.mgtf, 0, "A") }, phase: "preload" },
    ] });
    const restored = restoreFromSnapshot(D663.components, emptyState(), genSnapshot(D663.components, S, {}, {})).S;
    expect(restored.topology).toEqual(S.topology);
    expect(connections(restored)).toEqual(connections(S));
  });

  it("remaps BOM references and removes attachments to a removed wire on restore", () => {
    const S = withTopology({ version: 1, ends: [
      { end: end(W.mgtf, 0, "B"), at: { kind: "lug", material: { compId: "kit-13" }, crimp: "lug", strip: "5" } },
      { end: end(W.xs1r, 0, "A"), at: { kind: "free" } },
    ], tubes: [{ id: "b", material: { compId: "kit-15" }, lengthMm: 20, count: 1,
      target: { kind: "branch", end: end(W.mgtf, 0, "A") }, phase: "preload" }] });
    const components = D663.components.filter((c) => c.id !== "kit-8").map((c, i) => ({ ...c, id: `new-${i}` }));
    const restored = restoreFromSnapshot(components, emptyState(), genSnapshot(D663.components, S, {}, {})).S;
    expect(restored.topology?.ends).toEqual([{ end: end(W.mgtf, 0, "B"), at: { kind: "lug", material: { compId: "new-12" }, crimp: "lug", strip: "5" } }]);
    expect(restored.topology?.tubes?.[0].material).toEqual({ compId: "new-14" });
  });

  it("rejects a restored pin when its connector was removed from the BOM", () => {
    const S = withTopology({ version: 1, ends: [{ end: end(W.mgtf, 0, "A"), at: { kind: "pin", epId: "a1", pin: 1 } }] });
    const components = D663.components.filter((c) => c.id !== "kit-1").map((c, i) => ({ ...c, id: `new-${i}` }));
    const restored = restoreFromSnapshot(components, emptyState(), genSnapshot(D663.components, S, {}, {})).S;
    const data = { ...D663, components };
    expect(makeGen(data, restored).validate().error).toMatch(/разъём|контакт/i);
    expect(() => makeGen(data, restored).buildConfig()).toThrow(/разъём|контакт/i);
  });
});

describe("tube placement", () => {
  it("preloads a branch before closing the end even when the saved order is reversed", () => {
    const S = withTopology({ version: 1, tubes: [{ id: "branch", material: { compId: "kit-15" }, lengthMm: 25, count: 2,
      target: { kind: "branch", end: end(W.xs1r, 0, "A") }, phase: "preload" }] });
    const initial = makeGen(D663, S).getActiveOps();
    const closure = initial.find((o) => o.epId === "a1" && (o.type === "prsTerm" || o.type === "insTerm"))!;
    expect(closure).toBeDefined();
    const ops = makeGen(D663, { ...S, opOrder: [closure.opKey, "tube.branch.shrink", "tube.branch.position", "tube.branch.preload", "tube.branch.cut"] }).getActiveOps();
    const keys = ops.map((o) => o.opKey);
    expect(keys.indexOf("tube.branch.cut")).toBeLessThan(keys.indexOf("tube.branch.preload"));
    expect(keys.indexOf("tube.branch.preload")).toBeLessThan(keys.indexOf(closure.opKey));
    expect(keys.indexOf("tube.branch.position")).toBeLessThan(keys.indexOf("tube.branch.shrink"));
    expect(ops.find((o) => o.opKey === closure.opKey)?.dependsOn).toContain("tube.branch.preload");
  });

  it("preloads a splice tube before joining and positions and shrinks after joining", () => {
    const S = withTopology({ version: 1, tubes: [{ id: "splice", material: { compId: "kit-15" }, lengthMm: 30, count: 1,
      target: { kind: "splice", spliceId: "s1" }, phase: "preload", preloadEnd: end(W.xs1r, 0, "B") }] });
    const ops = makeGen(D663, S).getActiveOps();
    const keys = ops.map((o) => o.opKey);
    const join = ops.find((o) => o.type === "splice")!;
    expect(join).toBeDefined();
    expect(keys.indexOf("tube.splice.preload")).toBeLessThan(keys.indexOf(join.opKey));
    expect(keys.indexOf(join.opKey)).toBeLessThan(keys.indexOf("tube.splice.position"));
    expect(keys.indexOf("tube.splice.position")).toBeLessThan(keys.indexOf("tube.splice.shrink"));
    expect(ops.find((o) => o.opKey === "tube.splice.position")?.dependsOn).toContain(join.opKey);
  });

  it("charges each tube's material, length and count only to its cut", () => {
    const S = withTopology({ version: 1, tubes: [
      { id: "one", material: { compId: "kit-15" }, lengthMm: 25, count: 2, target: { kind: "branch", end: end(W.xs1r, 0, "A") }, phase: "preload" },
      { id: "two", material: { art: "CUSTOM-T", name: "Tube" }, lengthMm: 40, count: 3, target: { kind: "splice", spliceId: "s1" }, phase: "afterJoin" },
    ] });
    const ops = makeGen(D663, S).getActiveOps();
    expect(ops.find((o) => o.opKey === "tube.one.cut")?.tutParts).toEqual([{ art: D663.components[15].art, name: D663.components[15].name, len: "25", count: 2 }]);
    expect(ops.find((o) => o.opKey === "tube.two.cut")?.tutParts).toEqual([{ art: "CUSTOM-T", name: "Tube", len: "40", count: 3 }]);
    expect(ops.filter((o) => /^tube\./.test(o.opKey) && o.tutParts?.length).map((o) => o.opKey).sort()).toEqual(["tube.one.cut", "tube.two.cut"]);
    expect(ops.filter((o) => /^tube\./.test(o.opKey)).flatMap((o) => o.tutParts || []).reduce((sum, p) => sum + Number(p.len) * p.count, 0)).toBe(170);
  });

  it("replaces the legacy connector cup sleeve instead of charging it twice", () => {
    const base = harness663();
    const solder: GenState = { ...base, endpoints: base.endpoints.map((e) => e.id === "a1" ? { ...e, method: "solder", tutOff: false, tutLen: "25", shrinkOn: true, shrinkMat: "kit-15", shrinkLen: "30" } : e) };
    const S: GenState = { ...solder,
      topology: { version: 1, tubes: [{ id: "cup", material: { compId: "kit-15" }, lengthMm: 25, count: 1,
        target: { kind: "cup", end: end(W.xs1r, 0, "A") }, phase: "preload" }] } };
    const legacy = makeGen(D663, { ...solder, topology: { version: 1 } }).getActiveOps();
    const ops = makeGen(D663, S).getActiveOps();
    const count25 = (list: typeof ops) => list.filter((o) => o.opKey === "cutTubes").flatMap((o) => o.tutParts || []).filter((p) => p.len === "25").reduce((n, p) => n + p.count, 0);
    expect(count25(legacy)).toBeGreaterThan(0);
    expect(count25(ops)).toBe(count25(legacy) - 1);
    expect(ops.find((o) => o.opKey === "tube.cup.cut")?.tutParts).toEqual([{ art: D663.components[15].art, name: D663.components[15].name, len: "25", count: 1 }]);
    expect(ops.some((o) => o.opKey === "heatShrA1")).toBe(true);
  });

  it("deducts the explicit cup length before calculating the remaining automatic cup length", () => {
    const data = { ...D663, components: D663.components.filter((c) => c.id !== "kit-14" && c.id !== "kit-16") };
    const base = harness663(data);
    const S: GenState = { ...base, endpoints: base.endpoints.map((e) => e.id === "a1"
      ? { ...e, method: "solder", tutOff: false, tutLen: "", shrinkOn: false } : e),
      cap: { ...base.cap, enabled: false }, hs: { ...base.hs, enabled: false }, topology: { version: 1 } };
    const legacy = makeGen(data, S).getActiveOps();
    const explicit = makeGen(data, { ...S, topology: { version: 1, tubes: [{ id: "cup", material: { compId: "kit-15" }, lengthMm: 25, count: 1,
      target: { kind: "cup", end: end(W.xs1r, 0, "A") }, phase: "preload" }] } }).getActiveOps();
    const parts = (ops: typeof legacy, key: string) => ops.find((o) => o.opKey === key)?.tutParts;
    const material = data.components.find((c) => c.id === "kit-15")!;
    expect(parts(legacy, "cutTubes")).toEqual([{ art: material.art, name: material.name, len: "25", count: 2 }]);
    expect(parts(explicit, "cutTubes")).toEqual([{ art: material.art, name: material.name, len: "25", count: 1 }]);
    expect(parts(explicit, "tube.cup.cut")).toEqual([{ art: material.art, name: material.name, len: "25", count: 1 }]);
  });
});

describe("same-connector jumper", () => {
  it("rejects a jumper from a pin to itself without changing the state", () => {
    const S = harness663();
    expect(addJumper(D663, S, { epId: "a1", pin: 1 }, { epId: "a1", pin: 1 }, { compId: "kit-8", length: 80 })).toBe(S);
  });

  it("creates one segment with two contacts on the same connector", () => {
    const before = harness663();
    const S = addJumper(D663, before, { epId: "a1", pin: 1 }, { epId: "a1", pin: 2 }, { compId: "kit-8", length: 80 });
    expect(S.wires).toHaveLength(before.wires.length + 1);
    const wire = S.wires.at(-1)!;
    expect(wire).toMatchObject({ compId: "kit-8", length: 80, qty: 1, cores: 1 });
    const row = connections(S).find((c) => c.idx === wire.idx && c.unit === 0)!;
    expect(row.from).toMatchObject({ kind: "pin", epId: "a1", pin: 1, end: end(wire.idx, 0, "A") });
    expect(row.to).toMatchObject({ kind: "pin", epId: "a1", pin: 2, end: end(wire.idx, 0, "B") });
    const gen = makeGen(D663, S);
    const ops = gen.getActiveOps();
    expect(ops.some((o) => /^cutWire/.test(o.type) && o.wireIdxs?.includes(wire.idx))).toBe(true);
    expect(gen.endUnits_(S.endpoints.find((e) => e.id === "a1")!, wire)).toBe(2);
    const rows = gen.buildConfig().ops as Array<{ epKey?: string; wires?: Array<{ art: string; qty: number }>; pins?: string[] }>;
    expect(rows.some((o) => o.epKey === "А1" && o.wires?.some((w) => w.art === D663.components[8].art && w.qty === 2) && o.pins?.some((p) => p === "1, 2"))).toBe(true);
  });
});
