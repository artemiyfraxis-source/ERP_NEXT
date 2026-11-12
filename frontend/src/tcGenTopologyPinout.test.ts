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
