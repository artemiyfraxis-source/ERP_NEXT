// @vitest-environment node
import { describe, expect, it } from "vitest";
import { addJumper, assignPhysicalPin, pinoutEnds, pinUnits } from "./pinoutCheck";
import { applyEnd, firstFreePin, occupiedPins, pinConns, pinRows, spliceLines } from "./tcGenPinTable";
import { joinSplice, leaveJoin, linkWires, setTail } from "./tcGenSplices";
import { makeGen, type GenState } from "./tcGenLogic";
import { D663, W, end, harness663 } from "./tcGenComplex663.fixture";

const blank = (): GenState => ({ ...harness663(), wires: [], nextWireIdx: 0, splices: [], tails: [],
  endpoints: harness663().endpoints.map((ep) => ({ ...ep, pins: {} })) });
const jumper = () => addJumper(D663, blank(), { epId: "a1", pin: 1 }, { epId: "a1", pin: 2 }, { compId: "kit-8", length: 80 });
function view(S: GenState) {
  const gen = makeGen(D663, S), ends = pinoutEnds(gen, S), units = pinUnits(gen, S);
  const conns = pinConns(ends, S, { a1: 3, b1: 2, e2: 3, e3: 6 });
  return { gen, ends, units, conns, rows: pinRows(conns, units, S, ends) };
}

describe("physical pinout editing", () => {
  it("exports two separate terminals after moving one wire out of a double-crimped pin", () => {
    const base = harness663();
    const S: GenState = { ...base, wires: base.wires.filter((w) => w.idx === W.xs1r || w.idx === W.xs1b),
      endpoints: base.endpoints.filter((ep) => ep.id === "a1").map((ep) => ({ ...ep,
        pins: { [W.xs1r]: "1", [W.xs1b]: "1" }, doublePins: [1] })), splices: [], tails: [], topology: { version: 1 } };
    expect(makeGen(D663, S).epDoublePins_(S.endpoints[0])).toEqual([1]);
    const moved = assignPhysicalPin(D663, S, end(W.xs1b, 0, "A"), "a1", 2);
    expect(moved.endpoints[0].pins).toEqual(S.endpoints[0].pins);
    const gen = makeGen(D663, moved);
    expect(gen.epDoublePins_(moved.endpoints[0])).toEqual([]);
    const ops = gen.buildConfig().ops as Array<{ type: string; epId?: string; ep?: { doublePins?: number[] }; physicalEnds?: unknown[] }>;
    const crimp = ops.find((o) => o.type === "prsTerm" && o.epId === "a1")!;
    // The server uses one terminal per physical end when no double pins are exported.
    expect(crimp.physicalEnds).toEqual([end(W.xs1r, 0, "A"), end(W.xs1b, 0, "A")]);
    expect(crimp.physicalEnds).toHaveLength(2);
    expect(crimp.ep).not.toHaveProperty("doublePins");
  });

  it("recognizes an allowed double crimp from both physical jumper ends without legacy pins", () => {
    const moved = assignPhysicalPin(D663, jumper(), end(0, 0, "B"), "a1", 1);
    expect(makeGen(D663, moved).epDoublePins_(moved.endpoints[0])).toEqual([]);
    const S = { ...moved, endpoints: moved.endpoints.map((ep) => ep.id === "a1" ? { ...ep, doublePins: [1, 1, 2] } : ep) };
    expect(S.endpoints[0].pins).toEqual({});
    expect(makeGen(D663, S).epDoublePins_(S.endpoints[0])).toEqual([1]);
  });

  it("prints swapped physical pin numbers in both directions", () => {
    let S = assignPhysicalPin(D663, harness663(), end(W.xs1r, 0, "A"), "a1", 2);
    S = assignPhysicalPin(D663, S, end(W.xs1b, 0, "A"), "a1", 1);
    S = { ...S, wireMarks: { enabled: true, mat: "ТУТ 3/1мм белая", len: "12", dist: "25" } };
    const gen = makeGen(D663, S);
    const rows = gen.wireMarkList_().rows.filter((r) => r.idx === W.xs1r || r.idx === W.xs1b);
    const texts = ["XS1.2 to С1", "С1 to XS1.2", "XS1.1 to С2", "С2 to XS1.1"];
    expect(rows.flatMap((r) => [r.textA, r.textB])).toEqual(texts);
    const ops = gen.buildConfig().ops as Array<{ opKey: string; markTexts?: string[] }>;
    expect(ops.find((o) => o.opKey === "cutWireMark")?.markTexts).toEqual(expect.arrayContaining(texts));
  });

  it("prints both ends of a same-connector jumper", () => {
    const S = { ...jumper(), wireMarks: { enabled: true, mat: "ТУТ 3/1мм белая", len: "12", dist: "25" } };
    const gen = makeGen(D663, S);
    const texts = ["XS1.1 to XS1.2", "XS1.2 to XS1.1"];
    expect(gen.wireMarkList_().rows.flatMap((r) => [r.textA, r.textB])).toEqual(texts);
    expect(gen.wireMarkList_().skipped).toEqual([]);
    const ops = gen.buildConfig().ops as Array<{ opKey: string; markTexts?: string[] }>;
    expect(ops.find((o) => o.opKey === "cutWireMark")?.markTexts).toEqual(texts);
  });

  it.each(["cut", "inner", "outer"] as const)("keeps a jumper's own length and existing cut lengths in shared %s mode", (measureMode) => {
    const before: GenState = { ...harness663(), advancedLength: false, drawingLength: 300, sharedLengthText: "300", measureMode };
    const oldWires = makeGen(D663, before).buildConfig().wires as Array<{ length: number }>;
    const S = addJumper(D663, before, { epId: "a1", pin: 3 }, { epId: "a1", pin: 4 }, { compId: "kit-8", length: 80 });
    const wires = makeGen(D663, S).buildConfig().wires as Array<{ length: number }>;
    expect(wires.slice(0, -1).map((w) => w.length)).toEqual(oldWires.map((w) => w.length));
    expect(wires.at(-1)?.length).toBe(measureMode === "inner" ? 90 : 80);
  });

  it("retains both same-connector contacts in rows, occupancy, and link anchors", () => {
    const S = jumper(), v = view(S), conn = v.conns.find((c) => c.id === "a1")!;
    expect(S.wires).toEqual([{ idx: 0, compId: "kit-8", length: 80, qty: 1, cores: 1 }]);
    expect(v.ends.find((e) => e.id === "a1")!.wires[0]).toMatchObject({ qty: 2, pins: [1, 2] });
    expect(v.units[0].endPins).toEqual([
      { end: end(0, 0, "A"), epId: "a1", pin: 1 }, { end: end(0, 0, "B"), epId: "a1", pin: 2 },
    ]);
    expect(v.rows[0].cells.A.value).toBe("pin:a1:1");
    expect(v.rows[0].cells.B.value).toBe("pin:a1:2");
    expect([...occupiedPins(conn, v.units)]).toEqual([[1, ["1"]], [2, ["1"]]]);
    expect(firstFreePin(conn, v.units)).toBe(3);
    expect(linkWires(v.ends, v.units, S)[0]).toMatchObject({
      a: [{ kind: "pin", epId: "a1", pin: 1 }], b: [{ kind: "pin", epId: "a1", pin: 2 }],
    });
  });

  it("preserves inherited legacy contacts when assigning one physical end", () => {
    const S = assignPhysicalPin(D663, harness663(), end(W.xs1r, 0, "B"), "a1", 2);
    const gen = makeGen(D663, S);
    expect(gen.physicalAt_(end(W.xs1r, 0, "A"))).toMatchObject({ kind: "pin", epId: "a1", pin: 1 });
    expect(gen.physicalAt_(end(W.xs1r, 0, "B"))).toMatchObject({ kind: "pin", epId: "a1", pin: 2 });
    expect(S.splices!.some((sp) => sp.ends.some((e) => e.idx === W.xs1r && e.side === "B"))).toBe(false);
    expect(S.spl.wires).toBe("4,5");
  });

  it("moves only the addressed jumper end to a splice, then to a free end", () => {
    let S = joinSplice(D663, jumper(), end(0, 0, "B"), "new");
    let v = view(S);
    expect(v.gen.physicalAt_(end(0, 0, "A"))).toMatchObject({ kind: "pin", pin: 1 });
    expect(v.gen.physicalAt_(end(0, 0, "B"))).toMatchObject({ kind: "splice", spliceId: "s1" });
    expect(spliceLines(S, v.units, v.conns)[0].ends[0].where).toBe("→ XS1·1");
    S = setTail(D663, S, end(0, 0, "B"));
    v = view(S);
    expect(v.rows[0].cells.B.value).toBe("tail");
    expect(v.gen.physicalAt_(end(0, 0, "A"))).toMatchObject({ kind: "pin", pin: 1 });
    expect(v.gen.physicalAt_(end(0, 0, "B"))).toMatchObject({ kind: "free" });
    expect(S.splices).toEqual([]);
  });

  it("does not resurrect a legacy pin when leaving a splice or clearing an assignment", () => {
    const legacy = harness663();
    const opted: GenState = { ...legacy, topology: { version: 1 } };
    let S = joinSplice(D663, opted, end(W.xs1r, 0, "A"), "s1");
    S = leaveJoin(D663, S, end(W.xs1r, 0, "A"));
    expect(makeGen(D663, S).physicalAt_(end(W.xs1r, 0, "A"))).toMatchObject({ kind: "free" });
    let J = jumper();
    const v = view(J);
    J = applyEnd(D663, J, v.conns, v.units, { idx: 0, unit: 0 }, "B", "");
    expect(view(J).rows[0].cells.B.value).toBe("");
    expect(makeGen(D663, J).physicalAt_(end(0, 0, "A"))).toMatchObject({ kind: "pin", pin: 1 });
  });

  it("reassigns a physical B end to a connector on side A through the table", () => {
    let S = setTail(D663, jumper(), end(0, 0, "B"));
    const v = view(S);
    S = applyEnd(D663, S, v.conns, v.units, { idx: 0, unit: 0 }, "B", "pin:a1:auto");
    expect(view(S).rows[0].cells.B.value).toBe("pin:a1:2");
    expect(S.tails).toEqual([]);
    expect(makeGen(D663, S).physicalAt_(end(0, 0, "A"))).toMatchObject({ kind: "pin", pin: 1 });
  });

  it("reports duplicate contacts without dropping either physical end", () => {
    const S = assignPhysicalPin(D663, jumper(), end(0, 0, "B"), "a1", 1), v = view(S);
    expect(v.ends.find((e) => e.id === "a1")!.dups).toEqual([1]);
    expect(occupiedPins(v.conns.find((c) => c.id === "a1")!, v.units).get(1)).toEqual(["1", "1"]);
  });

  it("returns the original state for invalid jumper and physical-end inputs", () => {
    const S = blank();
    for (const wire of [{ compId: "missing", length: 80 }, { compId: "kit-8", length: 0 }, { compId: "kit-0", length: 80 }]) {
      expect(addJumper(D663, S, { epId: "a1", pin: 1 }, { epId: "a1", pin: 2 }, wire)).toBe(S);
    }
    expect(addJumper(D663, S, { epId: "missing", pin: 1 }, { epId: "a1", pin: 2 }, { compId: "kit-8", length: 80 })).toBe(S);
    const J = jumper();
    expect(assignPhysicalPin(D663, J, end(0, 1, "B"), "a1", 2)).toBe(J);
    expect(assignPhysicalPin(D663, J, end(0, 0, "B"), "a1", 1.5)).toBe(J);
  });
});
