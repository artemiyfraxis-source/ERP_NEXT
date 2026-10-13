// @vitest-environment node
import { expect, it } from "vitest";
import { buildHarnessDocument, planFromGenerator, type ConnectorSource, type GeneratorDrawingPlan } from "./generatorToHarness";
import { parseHarnessDesignDocument } from "./harness/editor/model";
import { pinoutEnds, pinoutRecord, pinUnits, restorePinout, routingWarnings } from "./pinoutCheck";
import { linkLayout } from "./PinoutLinks";
import { assignPinJoined, leaveJoin, linkWires, spliceLabel } from "./tcGenSplices";
import { complexByHand, initState, makeGen, updateEndpoint, type GenData, type GenState } from "./tcGenLogic";
import { BARE, D, end, harness, id, R } from "./tcGenRealHarness.fixture";

// Чертёж заказчика «Жгут НВ4» (tcGenRealHarness.fixture.ts) — набран руками через генератор.
const termOf = (data: GenData, S: GenState) => S.endpoints.map((e) => `${e.label || e.id}:${makeGen(data, S).getCompById(e.termId)?.art ?? "—"}`);

it("a new «Сложная сборка» card starts with no wire configuration: no cables, lengths or twist picked by the generator", () => {
  expect(initState(D).wires.length).toBeGreaterThan(1);
  const S = complexByHand(initState(D));
  expect(S.tab).toBe("complex");
  expect(S.wires).toEqual([{ idx: 0, compId: "", length: 0, qty: 1 }]);
  expect(S.twist.enabled).toBe(false);
  expect(S.drawingLength).toBe(0);
});

it("picks each connector's terminal from the sheet by housing compatibility (БД.ТЕР «Series» or, without it, the BOM)", () => {
  for (const data of [D, BARE]) {
    const S = harness(data);
    expect(termOf(data, S)).toEqual(["5:SYM-001T-P0.6", "b1:T-DS1071-SC600", "I3:SYM-001T-P0.6", "C:SHF-001T-0.8BS", "A6:SYM-001T-P0.6", "e5:T-DS1071-SC600"]);
    expect(S.endpoints.every((e) => e.termAuto)).toBe(true);
  }
  // Терминалов на разъём — столько, сколько контактов занято: SYM 2+2+3 = 7, SHF 2, T-DS 3+4 = 7 — как в листе.
  const S = harness(D), gen = makeGen(D, S);
  const used = (art: string) => S.endpoints.filter((e) => gen.getCompById(e.termId)?.art === art)
    .reduce((n, e) => n + gen.activeWires_().filter((w) => gen.routedWireIdxs_(e).includes(w.idx)).reduce((s, w) => s + gen.endUnits_(e, w), 0), 0);
  expect([used("SYM-001T-P0.6"), used("SHF-001T-0.8BS"), used("T-DS1071-SC600")]).toEqual([7, 2, 7]);
});

it("a terminal chosen by hand stays; a new connector gets its own terminal", () => {
  let S = harness(D);
  const a3 = id(S, "A", 3);
  S = updateEndpoint(D, S, a3, { termId: "tsym" });
  S = updateEndpoint(D, S, id(S, "A", 1), { connId: "csmp" });
  expect(termOf(D, S)[0]).toBe("5:SHF-001T-0.8BS");
  expect(termOf(D, S)[3]).toBe("C:SYM-001T-P0.6");
});

it("rebuilds the drawing: every contact, four splices, four tails — nothing left unassigned", () => {
  const S = harness(D), gen = makeGen(D, S);
  expect(routingWarnings(D, S)).toEqual([]);
  expect(pinoutEnds(gen, S).map((e) => `${e.key}:${e.wires.flatMap((w) => w.pins).join(",")}`))
    .toEqual(["А1:1,2", "А2:1,2", "А3:1,2", "А4:2,3,4", "В1:1,2,3", "В2:2,5,4,3"]);
  expect(S.splices!.map((s) => `${spliceLabel(S, s.id)}:${s.ends.length}`)).toEqual(["С1:4", "С2:4", "С3:3", "С4:3"]);
  expect(S.tails).toHaveLength(4);
  // Операция «Сростки» — по сросткам формы; свободных концов — четыре хвоста.
  expect(S.spl).toMatchObject({ enabled: true, count: "4", wires: "4,4,3,3" });
  expect(gen.freeEnds_()).toBe(4);
  const labels = gen.getActiveOps().map((o) => o.label);
  expect(labels).toContain("Сростки — 4 шт по 4, 4, 3, 3 провода");
  expect(labels).toContain("Зачистка концов под сростки — 14 конц.");
});

it("cuts each segment by its own drawing length plus L+ of the terminal at its crimped ends (a splice or tail end adds nothing)", () => {
  const S = harness(D);
  const cfg = makeGen(D, S).buildConfig() as { wires: Array<{ art: string; qty: number; length: number }> };
  expect(cfg.wires.map((w) => `${w.art} ×${w.qty} ${w.length}`)).toEqual([
    "НВ4 0,5 красный ×1 155", "НВ4 0,5 черный ×1 155", "НВ4 0,2 красный ×2 165", "НВ4 0,2 красный ×1 60",
    "НВ4 0,2 черный ×1 165", "НВ4 0,2 черный ×1 185", "НВ4 0,2 черный ×1 60", "НВ4 0,12 белый ×2 155", "НВ4 0,12 белый ×1 60",
    "НВ4 0,12 синий ×2 155", "НВ4 0,12 синий ×1 60", "НВ4 0,2 белый ×1 340", "НВ4 0,2 зеленый ×1 340", "НВ4 0,2 желтый ×1 340",
  ]);
});

it("draws the links: lines from contact to contact, through the splice dots, tails as free ends", () => {
  const S = harness(D), gen = makeGen(D, S);
  const ends = pinoutEnds(gen, S), units = pinUnits(gen, S);
  const wires = linkWires(ends, units, S);
  expect(wires).toHaveLength(17);
  const red = wires.find((w) => w.unit.idx === R.r5)!;
  expect([red.a, red.b]).toEqual([[{ kind: "pin", epId: id(S, "A", 1), pin: 1 }], [{ kind: "splice", id: S.splices![0].id }]]);
  const L = linkLayout(ends, units, {}, S);
  expect(L.boxes.map((b) => `${b.end.key}:${b.rows}`)).toEqual(["А1:2", "А2:2", "А3:2", "А4:4", "В1:3", "В2:6"]);
  expect(L.nodes).toHaveLength(4);
  expect(L.lines.filter((l) => l.free === "tail")).toHaveLength(4);
  expect(L.lines.filter((l) => !l.free)).toHaveLength(13);
});

it("a contact taken by a spliced end frees it from the splice; leaving a splice brings the end back to the contacts", () => {
  let S = harness(D);
  S = leaveJoin(D, S, end(R.r2t, 0, "A"));
  expect(S.splices![0].ends).toHaveLength(3);
  expect(S.spl.wires).toBe("3,4,3,3");
  expect(routingWarnings(D, S).some((w) => w.includes("Сторона А: не назначены на разъём — 4 · НВ4 0,2 красный"))).toBe(true);
  S = assignPinJoined(D, S, id(S, "A", 4), R.r2t, 0, 1);
  expect(S.tails!.some((e) => e.idx === R.r2t && e.side === "A")).toBe(false);
});

it("keeps splices and tails in build_config.pinout and restores them on reopening", () => {
  const S = harness(D);
  const record = pinoutRecord(makeGen(D, S), S)!;
  expect(record.splices).toHaveLength(4);
  expect(record.tails).toHaveLength(4);
  const fresh = { ...S, splices: [], tails: [] };
  const back = restorePinout(D, fresh, { pinout: record });
  expect(back.splices!.map((s) => s.ends.length)).toEqual([4, 4, 3, 3]);
  expect(back.tails).toEqual(S.tails);
});

it("«Построить чертёж»: splices become junctions of the drawing, wires go contact → junction with their own lengths", () => {
  const S = harness(D);
  const plan = planFromGenerator(D, S);
  expect(plan.wires.map((w) => `${w.mark} ${w.from.pin}→${w.to.pin}`)).toEqual(["НВ4 0,2 белый 2→5", "НВ4 0,2 зеленый 3→4", "НВ4 0,2 желтый 4→3"]);
  expect(plan.splices!.map((sp) => `${sp.label}:${sp.wires.map((w) => `${w.side}${w.end.pin}`).join(",")}`))
    .toEqual(["С1:A1,B1,B2", "С2:A2,B3,B2", "С3:A1,A1", "С4:A2,A2"]);
  expect(plan.warnings.filter((w) => w.includes("хвост"))).toHaveLength(4);
  const sources = Object.fromEntries(plan.connectors.map((c) => [c.key, { kind: "free", contactCount: null, libraryCode: null } as ConnectorSource]));
  const drawing = buildHarnessDocument(plan as GeneratorDrawingPlan, sources);
  expect(drawing.warnings.filter((w) => w.includes("узел"))).toEqual([]);
  // С1 и С2 (провод 0,5 и провода 0,2 к контактам) — узлы; С3 и С4 сводят по два контакта — их контакты соединены проводом.
  expect(drawing.document.junctions.map((j) => j.wireIds.length)).toEqual([3, 3]);
  expect(drawing.warnings.filter((w) => w.includes("два провода"))).toHaveLength(2);
  expect(drawing.wires).toBe(11);
  expect(drawing.connectors).toBe(6);
  expect(() => parseHarnessDesignDocument(drawing.document)).not.toThrow();
  const toJunction = drawing.document.wires.filter((w) => "junctionId" in w.from || "junctionId" in w.to);
  expect(toJunction.map((w) => w.lengthMm).sort()).toEqual([150, 150, 160, 160, 160, 180]);
  expect(toJunction.find((w) => w.lengthMm === 180)).toBeTruthy();
});
