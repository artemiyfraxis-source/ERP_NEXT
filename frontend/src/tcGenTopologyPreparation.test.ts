// @vitest-environment node
import { describe, expect, it } from "vitest";
import { D663, end, harness663 } from "./tcGenComplex663.fixture";
import { makeGen, type GenState } from "./tcGenLogic";

function spliceCable(cores: number): GenState {
  const base = harness663();
  return { ...base, endpoints: [], tails: [], rotor: false, measureMode: "cut",
    wires: [{ idx: 0, compId: "kit-8", qty: cores === 1 ? 2 : 1, cores, length: 100 }],
    splices: [{ id: "s1", ends: [end(0, 0, "A"), end(0, 1, "A")] }],
    spl: { ...base.spl, enabled: true, count: "1", wires: "2" }, topology: { version: 1 } };
}

describe("physical end preparation", () => {
  it("strips splice ends even when there are no connector cards", () => {
    const S = spliceCable(1);
    const ops = makeGen(D663, S).getActiveOps();
    const cuts = ops.filter((o) => /^cutWire/.test(o.type));
    expect(cuts.length).toBeGreaterThan(0);
    expect(cuts.every((o) => o.strip?.a && !!o.strip.lenA)).toBe(true);
    expect(cuts.every((o) => !o.strip?.b)).toBe(true);
  });

  it("strips splice-bound cable cores after jacket removal and before joining", () => {
    const S = spliceCable(2);
    const ops = makeGen(D663, S).getActiveOps();
    const jacket = ops.find((o) => o.opKey === "cable.0.A.jacket")!;
    const strip = ops.find((o) => o.tmplHint === "STRIP_WIRE" && o.physicalEnds?.some((e) => e.side === "A"))!;
    const join = ops.find((o) => o.type === "splice")!;
    expect(jacket).toBeDefined();
    expect(strip).toBeDefined();
    expect(strip).toMatchObject({ tutCount: 2, physicalEnds: [end(0, 0, "A"), end(0, 1, "A")] });
    expect(ops.indexOf(jacket)).toBeLessThan(ops.indexOf(strip));
    expect(ops.indexOf(strip)).toBeLessThan(ops.indexOf(join));
    const built = makeGen(D663, S).buildConfig().ops as Array<{ opKey: string; wires?: Array<{ qty: number; cores?: number }> }>;
    const row = built.find((o) => o.opKey === strip.opKey)!;
    expect(row.wires?.reduce((n, w) => n + w.qty * (w.cores || 1), 0)).toBe(2);
  });

  it("exposes cable cores before preloading a tube despite a requested earlier preload", () => {
    const S = spliceCable(2);
    S.topology = { version: 1, tubes: [{ id: "core", material: { art: "ТУТ", name: "Трубка" },
      lengthMm: 20, count: 1, target: { kind: "branch", end: end(0, 0, "A") }, phase: "preload" }] };
    S.opOrder = makeGen(D663, S).getActiveOps().map((o) => o.opKey).reverse();
    const ops = makeGen(D663, S).getActiveOps();
    const keys = ops.map((o) => o.opKey);
    expect(keys.indexOf("cable.0.A.jacket")).toBeLessThan(keys.indexOf("tube.core.preload"));
  });
});
