// @vitest-environment node
import { expect, it } from "vitest";
import { buildHarnessDocument, planFromGenerator, type ConnectorSource, type GeneratorDrawingPlan } from "./generatorToHarness";
import { createFreeEndpoint, isFreeEndpoint, isJunctionEndpoint, parseHarnessDesignDocument } from "./harness/editor/model";
import { openWireEndMetadata } from "./harness/editor/open-wire-ends";
import tailsSample from "../../backend/tests/techcards/harness_generated_tails_sample.json";
import { pinoutEnds, pinoutRecord, pinUnits, restorePinout, routingWarnings } from "./pinoutCheck";
import { linkLayout } from "./PinoutLinks";
import { assignPinJoined, leaveJoin, linkWires, setTail, spliceLabel } from "./tcGenSplices";
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
  // Новая форма — опрессовка гильзой (владелец 16.10: «основа это обжим»); гильз в этом составе нет. Сростки — группами по
  // разъёмам А, с которых приходят провода; концы под сростки зачищает станок резки (отдельной зачистки нет).
  expect(labels).toEqual(expect.arrayContaining(["Сростки С1, С2 опрессовкой гильзой — 2 шт по 4 провода",
    "Сростки С3, С4 опрессовкой гильзой — 2 шт по 3 провода"]));
  expect(labels.some((label) => label.startsWith("Зачистка концов под сростки"))).toBe(false);
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

const freeSources = (plan: GeneratorDrawingPlan) =>
  Object.fromEntries(plan.connectors.map((c) => [c.key, { kind: "free", contactCount: null, libraryCode: null } as ConnectorSource]));

it("«Построить чертёж»: splices become junctions, tails — open ends of their own length, no warnings about either", () => {
  const S = harness(D);
  const plan = planFromGenerator(D, S);
  expect(plan.wires.map((w) => `${w.mark} ${w.from.pin}→${w.to.pin}`)).toEqual(["НВ4 0,2 белый 2→5", "НВ4 0,2 зеленый 3→4", "НВ4 0,2 желтый 4→3"]);
  expect(plan.splices!.map((sp) => `${sp.label}:${sp.wires.map((w) => `${w.side}${w.end.pin}`).join(",")}+${sp.tails.map((t) => t.lengthMm).join(",")}`))
    .toEqual(["С1:A1,B1,B2+60", "С2:A2,B3,B2+60", "С3:A1,A1+60", "С4:A2,A2+60"]);
  const drawing = buildHarnessDocument(plan, freeSources(plan));
  expect(drawing.warnings.filter((w) => /хвост|сросток|свободный конец|два провода|узел|без трассы/i.test(w))).toEqual([]);
  // Четыре узла: С1 и С2 — провод 0,5, провода 0,2 к контактам и хвост; С3 и С4 — два контакта и хвост.
  expect(drawing.document.junctions.map((j) => j.wireIds.length)).toEqual([4, 4, 3, 3]);
  expect(drawing.wires).toBe(17);
  expect(drawing.connectors).toBe(6);
  expect(() => parseHarnessDesignDocument(drawing.document)).not.toThrow();
  const tails = drawing.document.wires.filter((w) => isFreeEndpoint(w.to));
  expect(tails.map((w) => [w.lengthMm, isJunctionEndpoint(w.from)])).toEqual([[60, true], [60, true], [60, true], [60, true]]);
  const toJunction = drawing.document.wires.filter((w) => !isFreeEndpoint(w.to) && (isJunctionEndpoint(w.from) || isJunctionEndpoint(w.to)));
  expect(toJunction.map((w) => w.lengthMm).sort()).toEqual([150, 150, 150, 150, 150, 150, 160, 160, 160, 180]);
  // Чертёж: у каждого провода трасса; хвост — участок от узла сростка до свободного конца, подпись — его длина.
  const topology = drawing.document.physicalTopology!;
  expect(drawing.document.wires.every((w) => topology.routes.some((r) => r.wireId === w.id))).toBe(true);
  for (const tail of tails) {
    const route = topology.routes.find((r) => r.wireId === tail.id)!;
    const segment = topology.segments.find((s) => s.id === route.steps[0].segmentId)!;
    expect(topology.nodes.find((n) => n.id === segment.to)!.connectorId).toBeUndefined();
    expect(openWireEndMetadata(tail)).toEqual({ spliceFrom: "true", openTo: "60 мм" });
  }
  expect(drawing.document).toEqual(tailsSample);
});

it("a tail straight from a contact (no splice) is drawn as a wire into an open end", () => {
  let S = harness(D);
  S = setTail(D, S, end(R.w2, 0, "B"));
  const plan = planFromGenerator(D, S);
  expect(plan.tails!.map((t) => `${t.mark} ${t.side}${t.end.pin} ${t.lengthMm}`)).toEqual(["НВ4 0,2 белый A2 330"]);
  expect(plan.wires).toHaveLength(2);
  const drawing = buildHarnessDocument(plan, freeSources(plan));
  const wire = drawing.document.wires.find((w) => isFreeEndpoint(w.to) && !isJunctionEndpoint(w.from))!;
  expect(wire.lengthMm).toBe(330);
  expect(drawing.document.physicalTopology!.routes.some((r) => r.wireId === wire.id)).toBe(true);
  expect(drawing.warnings.filter((w) => /хвост|без трассы|распиновка/.test(w))).toEqual([]);
});

it("the editor model keeps an open end: one per wire, parsed back with its point", () => {
  const drawing = buildHarnessDocument(planFromGenerator(D, harness(D)), freeSources(planFromGenerator(D, harness(D))));
  const tail = drawing.document.wires.find((w) => isFreeEndpoint(w.to))!;
  const parsed = parseHarnessDesignDocument(JSON.parse(JSON.stringify(drawing.document)));
  expect(parsed.wires.find((w) => w.id === tail.id)!.to).toEqual(tail.to);
  const both = { ...drawing.document, wires: drawing.document.wires.map((w) => (w.id === tail.id ? { ...w, from: createFreeEndpoint({ x: 0, y: 0 }) } : w)) };
  expect(() => parseHarnessDesignDocument(JSON.parse(JSON.stringify(both)))).toThrow(/только один конец/);
});
