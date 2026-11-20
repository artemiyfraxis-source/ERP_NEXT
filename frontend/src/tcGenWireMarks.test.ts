// @vitest-environment node
import { expect, it } from "vitest";
import { complexByHand, initState, makeGen, toggleLengthMode, updateEndpoint, type GenState } from "./tcGenLogic";
import {
  laserMarkOf, laserPrintedKey, laserTextWarnings, markMediaOf, markTubeForWire, pickCoverTube, pickWireMarkTube, savedWireMarks, tubeBeforeShrink, WIRE_MARK_TAPE_WIDTHS_MM,
  WIRE_MARK_TUBE_SIZES_MM, wireMarkCoverLen, wireMarkCoverOn, wireMarkKindOf, wireMarksFromDefaults, wireMarkTubeBySize, withTubeMm, type WireMarkCfg,
} from "./tcGenWireMarks";
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

it("трубка метки: размер без «мм» из справочника материалов, печать — на светлой (708К.5, 26.10)", () => {
  expect(tubeBeforeShrink("ТУТ 2/1 белая")).toBe(2);
  expect(tubeBeforeShrink("ТТК 4,8/1,6 прозрачная")).toBe(4.8);
  expect(tubeBeforeShrink("Лента 19мм x 15мм")).toBeNull();
  const mat = ["ТТК 19/6,5мм, черная", "ТУТ 2/1 черная", "ТУТ 2/1 белая", "ТУТ 3/1,5 белая", "ТУТ 8/4 белая"];
  expect(pickWireMarkTube(mat, 1.4)).toBe("ТУТ 2/1 белая");
  expect(pickWireMarkTube(["ТУТ 2/1 черная", "ТУТ 3/1,5 черная"], 1.4)).toBe("ТУТ 2/1 черная");
});


/*
 * Метка на проводе (владелец 20.11): нет / трубка (Ø маркиратора) / лента (ширина) / лазер ОБ.019 при резке. Концы пайкой —
 * провода режет ОБ.019 (резка с разделкой); резку с обжимом режет BZW, лазера у неё нет.
 */
function soldered(): GenState {
  let S = threeWires();
  S = updateEndpoint(DSA, S, id(S, "A", 1), { method: "solder" });
  return updateEndpoint(DSA, S, id(S, "B", 1), { method: "solder" });
}
type Raw = { ops: Array<Record<string, unknown>> };
const rawOps = (S: GenState) => (makeGen(DSA, S).buildConfig() as Raw).ops;

it("лазер «по краям»: у каждой резки ОБ.019 своя пара надписей, жилы с разными надписями — разные партии; печатной маркировки нет", () => {
  const S = on(soldered(), { kind: "laser" });
  const cuts = rawOps(S).filter((o) => /^cutWire/.test(String(o.opKey)));
  expect(makeGen(DSA, S).getActiveOps().filter((o) => /^cutWire\d*$/.test(o.opKey)).map((o) => [o.type, o.wireIdxs])).toEqual([["cutWireStrip", [0]], ["cutWireStrip", [1]], ["cutWireStrip", [2]]]);
  expect(cuts[0].laserMark).toEqual({ mode: "ends", front: "XS1.1 to X2.2", back: "X2.2 to XS1.1", middle: "", frontMm: 20, backMm: 20, stepMm: 100 });
  expect(cuts[1].laserMark).toMatchObject({ front: "XS1.2 to С1", back: "С1 to XS1.2" });
  expect(cuts[2]).not.toHaveProperty("laserMark");  // № 3 со свободным концом — без метки
  expect(keysOf(S)).not.toContain("cutWireMark");
  // Без лазера те же провода 2 и 3 — одной партией, как раньше.
  expect(makeGen(DSA, on(soldered())).getActiveOps().filter((o) => /^cutWire\d*$/.test(o.opKey)).map((o) => o.wireIdxs)).toEqual([[0], [1, 2]]);
});

it("лазер «по всей длине»: одна надпись с шагом (пусто — надпись конца А), отступы и шаг из формы, надписи до 30 знаков", () => {
  const S = on(soldered(), { kind: "laser", laser: { mode: "full", stepMm: "150", frontMm: "30", backMm: "30" } });
  const cut = rawOps(S).find((o) => o.opKey === "cutWire")!;
  expect(cut.laserMark).toEqual({ mode: "full", front: "XS1.1 to X2.2", back: "X2.2 to XS1.1", middle: "XS1.1 to X2.2", frontMm: 30, backMm: 30, stepMm: 150 });
  const own = laserMarkOf({ enabled: true, mat: "", len: "", dist: "", kind: "laser", laser: { mode: "full", middle: "  ЖГУТ-7  ", stepMm: "0" } }, "A".repeat(40), "B");
  expect(own).toMatchObject({ middle: "ЖГУТ-7", stepMm: 100, frontMm: 20 });
  expect(own.front).toHaveLength(30);
});

it("лазер не попадает в резку с обжимом (BZW): партии не делятся, предупреждение называет провода", () => {
  const S = on(threeWires(), { kind: "laser" });
  const g = makeGen(DSA, S);
  const ops = rawOps(S);
  expect(g.getActiveOps().filter((o) => o.type === "cutWireCrimp").map((o) => o.wireIdxs)).toEqual([[0], [1, 2]]);
  expect(JSON.stringify(ops)).not.toMatch(/laserMark/);
  expect(g.wireMarkCrimpNote_(g.wireMarkList_().rows)).toMatch(/лазер ОБ\.019 их не маркирует/);
});

it("переключатель: трубка, лента и лазер взаимоисключающие; «нет» — без операций", () => {
  expect(wireMarkKindOf(undefined)).toBe("none");
  expect(wireMarkKindOf({ enabled: false, mat: "", len: "", dist: "", kind: "laser" })).toBe("none");
  expect(wireMarkKindOf({ enabled: true, mat: "", len: "", dist: "" })).toBe("tube");
  const json = (S: GenState) => JSON.stringify(rawOps(S));
  expect(json(on(soldered(), { kind: "laser" }))).not.toMatch(/markMedia|cutWireMark/);
  expect(json(on(soldered(), { kind: "tape" }))).not.toMatch(/laserMark/);
  expect(json(on(soldered(), { kind: "tube", tubeMm: "3.2" }))).not.toMatch(/laserMark/);
  expect(json({ ...soldered(), wireMarks: { enabled: false, mat: "", len: "", dist: "", kind: "laser" } })).not.toMatch(/laserMark|markMedia|wireMarks/);
});

it("трубка маркиратора: выбранный Ø — в markMedia всех трёх операций и трубка этого Ø; без выбора — Ø TP80E по Ø провода", () => {
  expect(WIRE_MARK_TUBE_SIZES_MM).toEqual([2.4, 3.2, 4.8, 6, 8]);
  const data = { ...DSA, markMats: [{ name: "ТУТ 3,2/1,6 белая", group: "ТУТ", unit: "м" }, { name: "ТУТ 2,4/1,2 белая", group: "ТУТ", unit: "м" }] };
  const S = on(threeWires(), { kind: "tube", tubeMm: "3.2" });
  const ops = (makeGen(data, S).buildConfig() as Raw).ops.filter((o) => o.wireMarks);
  expect(ops.map((o) => o.opKey)).toEqual(["cutWireMark", "onWireMark", "heatWireMark"]);
  ops.forEach((o) => expect(o.markMedia).toEqual({ kind: "tube", sizeMm: 3.2 }));
  expect(ops[0].tutArt).toBe("ТУТ 3,2/1,6 белая");
  expect(wireMarkTubeBySize(["ТУТ 3,2/1,6 черная", "ТУТ 3,2/1,6 белая"], 3.2)).toBe("ТУТ 3,2/1,6 белая");
  expect(wireMarkTubeBySize(["ТУТ 4/2мм"], 3.2)).toBe("");
  expect(markMediaOf({ enabled: true, mat: "", len: "", dist: "", kind: "tube" })).toEqual({ kind: "tube", sizeMm: 2.4 });
});

it("лента маркиратора: печать этикеток и наклейка, без усадки; ширина по умолчанию — первая из списка", () => {
  expect(WIRE_MARK_TAPE_WIDTHS_MM).toEqual([6, 9, 12]);
  const ops = rawOps(on(threeWires(), { kind: "tape", tapeMm: "9" })).filter((o) => o.wireMarks);
  expect(ops.map((o) => o.opKey)).toEqual(["cutWireMark", "onWireMark"]);
  ops.forEach((o) => expect(o.markMedia).toEqual({ kind: "tape", sizeMm: 9 }));
  expect(ops[0]).toMatchObject({ markTexts: ["XS1.1 to X2.2", "X2.2 to XS1.1", "XS1.2 to С1", "С1 to XS1.2"], tutCount: 4 });
  expect(markMediaOf({ enabled: true, mat: "", len: "", dist: "", kind: "tape" })).toEqual({ kind: "tape", sizeMm: 6 });
});

it("старый черновик (трубка без поля kind) — как трубка «по Ø провода»: Ø TP80E в markMedia, без лазера; форма без метки — байт в байт", () => {
  const before = on(soldered(), { len: "12", dist: "25" });
  const same = (a: GenState, b: GenState) => expect(JSON.stringify(makeGen(DSA, a).buildConfig())).toBe(JSON.stringify(makeGen(DSA, b).buildConfig()));
  same(before, on(soldered(), { len: "12", dist: "25", kind: "tube", tubeMm: "", laser: { mode: "full" } }));
  expect(JSON.stringify(makeGen(DSA, before).buildConfig())).not.toMatch(/laserMark/);
  expect(rawOps(before).find((o) => o.opKey === "cutWireMark")!.markMedia).toEqual({ kind: "tube", sizeMm: 2.4 });
  const full = fullHarness(DSA);
  same(full, { ...full, wireMarks: { enabled: false, mat: "", len: "", dist: "", kind: "tape", tapeMm: "9" } });
});

const wmRow = (key: string, num: string, textA: string, textB: string) => ({ key, idx: 0, unit: 0, num, from: "", to: "", textA, textB, autoA: textA, autoB: textB });
const FULL_CFG = (middle: string) => ({ enabled: true, mat: "", len: "", dist: "", kind: "laser" as const, laser: { mode: "full" as const, middle } });
const ENDS_CFG = { enabled: true, mat: "", len: "", dist: "", kind: "laser" as const, laser: { mode: "ends" as const } };

it("партии резки лазером: по всей длине делит печатаемая надпись, а не пара надписей концов; по краям — пара", () => {
  const a = wmRow("0.0", "1", "XS1.1 to X2.2", "X2.2 to XS1.1"), b = wmRow("0.1", "2", "XS1.2 to X2.3", "X2.3 to XS1.2");
  expect(laserPrintedKey(FULL_CFG("ЖГУТ-7"), a)).toBe(laserPrintedKey(FULL_CFG("ЖГУТ-7"), b));
  expect(laserPrintedKey(FULL_CFG(""), a)).not.toBe(laserPrintedKey(FULL_CFG(""), b));
  expect(laserPrintedKey(ENDS_CFG, a)).not.toBe(laserPrintedKey(ENDS_CFG, b));
  expect(laserPrintedKey(ENDS_CFG, a)).toBe(laserPrintedKey(ENDS_CFG, { ...a }));
  // Обрезка до 30 знаков тоже считается: станок напечатает одно и то же.
  const l1 = wmRow("0.2", "3", "A".repeat(30) + "1", "x"), l2 = wmRow("0.3", "4", "A".repeat(30) + "2", "x");
  expect(laserPrintedKey(FULL_CFG(""), l1)).toBe(laserPrintedKey(FULL_CFG(""), l2));
});

it("лазер: надпись длиннее 30 знаков — предупреждение у провода, одинаковые после обрезки — отдельное", () => {
  const long1 = "A".repeat(30) + "1", long2 = "A".repeat(30) + "2";
  const rows = [wmRow("0.0", "1", long1, "B1"), wmRow("0.1", "2", long2, "B2"), wmRow("0.2", "3", "short", "B3")];
  const w = laserTextWarnings(ENDS_CFG, rows);
  expect(w["0.0"].join(" ")).toContain(`надпись длиннее 30 знаков — на станке обрежется до «${"A".repeat(30)}»`);
  expect(w["0.0"].join(" ")).toMatch(/совпадает с проводом № 2 — напечатается одинаковая метка/);
  expect(w["0.1"].join(" ")).toMatch(/совпадает с проводом № 1/);
  expect(w["0.2"]).toBeUndefined();
  // По всей длине со своей надписью концы не печатаются — предупреждений нет.
  expect(laserTextWarnings(FULL_CFG("ЖГУТ-7"), rows)).toEqual({});
  expect(Object.keys(laserTextWarnings(FULL_CFG(""), rows))).toEqual(["0.0", "0.1"]);
});

/*
 * Печать на Supvan TP80E ОБ.057 (владелец 20.11): карта PRINT_MARK для трубки и ленты, «по Ø провода» — Ø TP80E, прозрачная
 * трубка поверх метки и запомненный выбор проекта.
 */
const cfg = (patch: Partial<WireMarkCfg> = {}): WireMarkCfg => ({ enabled: true, mat: "", len: "", dist: "", ...patch });
const TP80E = { ...DSA, templates: [...DSA.templates, { id: "print-mark-печать-маркировки-на-трубке-и-ленте", title: "PRINT_MARK | Печать маркировки на трубке и ленте", category: "Монтаж маркировки" }] };

it("TP80E: трубка и лента печатаются картой PRINT_MARK, если она есть в библиотеке; нет её — карты Godex, как раньше", () => {
  const tube = makeGen(TP80E, on(threeWires(), { kind: "tube" })).getActiveOps().find((o) => o.opKey === "cutWireMark")!;
  expect([tube.tmplHint, tube.tmplWant]).toEqual(["PRINT_MARK", "PRINT_MARK"]);
  expect(tube.label).toMatch(/^Печать и резка маркировки проводов/);
  const tape = makeGen(TP80E, on(threeWires(), { kind: "tape" })).getActiveOps().find((o) => o.opKey === "cutWireMark")!;
  expect([tape.tmplHint, tape.tmplWant]).toEqual(["PRINT_MARK", "PRINT_MARK"]);
  expect((makeGen(TP80E, on(threeWires(), { kind: "tape" })).buildConfig() as Raw).ops.find((o) => o.opKey === "cutWireMark")!.templateId)
    .toBe("print-mark-печать-маркировки-на-трубке-и-ленте");
  expect(makeGen(DSA, on(threeWires(), { kind: "tube" })).getActiveOps().find((o) => o.opKey === "cutWireMark")!.tmplHint).toBe("PRINT_MARK_TUBE");
  // Метки-трубки состава (cutMark) остаются на Godex.
  expect(makeGen(TP80E, fullHarness(TP80E)).getActiveOps().find((o) => o.opKey === "cutMark")!.tmplHint).toBe("PRINT_MARK_TUBE");
});

it("«по Ø провода» — ближайший Ø TP80E не меньше Ø провода; запомненный для этого Ø провода — раньше; выбранный — всегда", () => {
  expect([0, 1.1, 2.4, 2.5, 4.9, 7.9, 9].map(markTubeForWire)).toEqual([2.4, 2.4, 2.4, 3.2, 6, 8, 8]);
  expect(markMediaOf(cfg({ kind: "tube" }), 3)).toEqual({ kind: "tube", sizeMm: 3.2 });
  expect(markMediaOf(cfg({ kind: "tube", tubeByDia: { "1.1": "4.8" } }), 1.1)).toEqual({ kind: "tube", sizeMm: 4.8 });
  expect(markMediaOf(cfg({ kind: "tube", tubeByDia: { "1.4": "4.8" } }), 1.1)).toEqual({ kind: "tube", sizeMm: 2.4 });
  expect(markMediaOf(cfg({ kind: "tube", tubeMm: "6", tubeByDia: { "1.1": "4.8" } }), 1.1)).toEqual({ kind: "tube", sizeMm: 6 });
  // Жгут: самый толстый маркируемый провод НВ4 0,2 — Ø 1,1 → трубка 2,4; запомненный Ø для 1,1 — в операции.
  const g = makeGen(DSA, on(threeWires()));
  expect(g.wireMarkDia_(g.wireMarkList_().rows)).toBe(1.1);
  const media = (S: GenState) => makeGen(DSA, S).getActiveOps().find((o) => o.opKey === "cutWireMark")!.markMedia;
  expect(media(on(threeWires(), { kind: "tube" }))).toEqual({ kind: "tube", sizeMm: 2.4 });
  expect(media(on(threeWires(), { kind: "tube", tubeByDia: { "1.1": "3.2" } }))).toEqual({ kind: "tube", sizeMm: 3.2 });
});

it("выбор Ø трубки запоминается за Ø провода; «по Ø провода» запомненное убирает; Ø провода неизвестен — только выбор", () => {
  const picked = withTubeMm(cfg({ kind: "tube", tubeByDia: { "2": "4.8" } }), "3.2", 1.1);
  expect(picked).toMatchObject({ tubeMm: "3.2", tubeByDia: { "2": "4.8", "1.1": "3.2" } });
  expect(withTubeMm(picked, "", 1.1)).toMatchObject({ tubeMm: "", tubeByDia: { "2": "4.8" } });
  expect(withTubeMm(cfg({ kind: "tube" }), "6", 0)).toEqual(cfg({ kind: "tube", tubeMm: "6" }));
});

it("выбор уходит в рецепт (build_config.wireMarks): чем метить, размер, запомненные Ø, покрытие; выключено — ничего", () => {
  const raw = (S: GenState) => (makeGen(DSA, S).buildConfig() as Record<string, unknown>).wireMarks;
  expect(raw(on(threeWires(), { kind: "tube", tubeMm: "3.2", tubeByDia: { "1.1": "3.2" } })))
    .toEqual({ enabled: true, kind: "tube", tubeMm: "3.2", tubeByDia: { "1.1": "3.2" }, cover: false });
  expect(raw(on(threeWires(), { kind: "tape", tapeMm: "9", tubeMm: "3.2" }))).toEqual({ enabled: true, kind: "tape", tapeMm: "9", cover: true });
  expect(raw(on(threeWires(), { kind: "tape" }))).toMatchObject({ tapeMm: "6" });
  expect(raw({ ...threeWires(), wireMarks: cfg({ enabled: false, kind: "tape" }) })).toBeUndefined();
  expect(savedWireMarks(cfg({ kind: "laser" }))).toEqual({ enabled: true, kind: "laser" });
});

it("выбор проекта для новой КБ: трубка или лента, ширина ленты, Ø трубки по Ø провода; ничего не печатали — форму не трогаем", () => {
  expect(wireMarksFromDefaults({ kind: null, tapeMm: null, tubeByDia: {} })).toBeUndefined();
  expect(wireMarksFromDefaults(null)).toBeUndefined();
  expect(wireMarksFromDefaults({ kind: "tape", tapeMm: 9, tubeByDia: {} })).toEqual(cfg({ kind: "tape", tapeMm: "9" }));
  const tube = wireMarksFromDefaults({ kind: "tube", tapeMm: null, tubeByDia: { "1.1": 3.2, "1.60": 4.8, x: 6 } })!;
  expect(tube).toEqual(cfg({ kind: "tube", tubeByDia: { "1.1": "3.2", "1.6": "4.8" } }));
  // Новая КБ того же проекта: провод Ø 1,1 — трубка 3,2, как выбрали в прошлый раз.
  const ops = makeGen(DSA, { ...threeWires(), wireMarks: tube }).getActiveOps();
  expect(ops.find((o) => o.opKey === "cutWireMark")!.markMedia).toEqual({ kind: "tube", sizeMm: 3.2 });
});

it("прозрачная трубка поверх метки: у ленты по умолчанию да, у трубки нет; переключатель решает; лазер — никогда", () => {
  expect(wireMarkCoverOn(cfg({ kind: "tape" }))).toBe(true);
  expect(wireMarkCoverOn(cfg({ kind: "tube" }))).toBe(false);
  expect(wireMarkCoverOn(cfg())).toBe(false);
  expect(wireMarkCoverOn(cfg({ kind: "tape", cover: false }))).toBe(false);
  expect(wireMarkCoverOn(cfg({ kind: "tube", cover: true }))).toBe(true);
  expect(wireMarkCoverOn(cfg({ kind: "laser", cover: true }))).toBe(false);
  expect(wireMarkCoverOn(cfg({ enabled: false, kind: "tape" }))).toBe(false);
  expect([wireMarkCoverLen(cfg()), wireMarkCoverLen(cfg({ len: "12" })), wireMarkCoverLen(cfg({ len: "7,5" }))]).toEqual(["25", "22", "17.5"]);
  expect(keysOf(on(threeWires(), { kind: "tube" }))).not.toContain("cutWireMarkCover");
  expect(keysOf(on(soldered(), { kind: "laser", cover: true }))).not.toContain("cutWireMarkCover");
});

it("прозрачная трубка: Ø над проводом (лента) или над трубкой маркиратора; меньшая прозрачная ТУТ состава / БД.МАТ, иначе КВТ ТУТ(HF)", () => {
  expect(pickCoverTube([], 1.1)).toEqual({ art: "83266", name: "ТУТ(HF) 4/2 прозрачная", mm: 4 });
  expect(pickCoverTube([], 4.8)).toEqual({ art: "ТУТ 6/3 прозрачная", name: "ТУТ 6/3 прозрачная", mm: 6 });
  expect(pickCoverTube([], 8)).toEqual({ art: "83269", name: "ТУТ(HF) 10/5 прозрачная", mm: 10 });
  const mats = [{ name: "ТУТ 3/1,5 белая" }, { name: "ТТК 6/2 прозрачная" }, { name: "ТУТ 3/1,5 прозрачная" }, { name: "ТУТ 6/3 прозрачная", art: "ПР-6" }];
  expect(pickCoverTube(mats, 1.1)).toEqual({ art: "ТУТ 3/1,5 прозрачная", name: "ТУТ 3/1,5 прозрачная", mm: 3 });
  expect(pickCoverTube(mats, 3.2)).toEqual({ art: "ПР-6", name: "ТУТ 6/3 прозрачная", mm: 6 });
  expect(pickCoverTube(mats, 6)).toEqual({ art: "ТУТ 8/4 прозрачная", name: "ТУТ 8/4 прозрачная", mm: 8 });
});

it("покрытие ленты: три операции (резка ОБ.018, надеть сразу после метки, усадка после неё), отрезок = метка + 10 мм, без флагов метки", () => {
  const S = on(threeWires(), { kind: "tape", len: "12" });
  const g = makeGen(DSA, S);
  const ops = g.getActiveOps();
  const keys = ops.map((o) => o.opKey);
  const cover = ops.filter((o) => /WireMarkCover$/.test(o.opKey));
  expect(cover.map((o) => [o.opKey, o.type, o.tmplHint])).toEqual([
    ["cutWireMarkCover", "cutTut", "CUT_TUT_short"], ["onWireMarkCover", "insTut", "INS_TUT"], ["heatWireMarkCover", "heatTut", "HEAT_TYT_short"]]);
  expect(keys.indexOf("onWireMarkCover")).toBe(keys.indexOf("onWireMark") + 1);
  expect(keys.indexOf("heatWireMarkCover")).toBe(keys.indexOf("onWireMarkCover") + 1);
  const terminal = ops.findIndex((o) => !!o.epId);
  if (terminal >= 0) expect(keys.indexOf("heatWireMarkCover")).toBeLessThan(terminal);
  expect(cover[0]).toMatchObject({ tutArt: "83266", tutName: "ТУТ(HF) 4/2 прозрачная", tutCount: 4, coverMm: 4,
    tutParts: [{ art: "83266", name: "ТУТ(HF) 4/2 прозрачная", count: 4, len: "22" }], tutSegs: [{ len: "22", count: 4 }] });
  cover.forEach((o) => { expect(o.markTube).toBeFalsy(); expect(o.wireMarks).toBeFalsy(); expect(o.shrinkMaterials).toEqual([{ art: "83266", name: "ТУТ(HF) 4/2 прозрачная" }]); });
  const raw = (g.buildConfig() as Raw).ops.filter((o) => /WireMarkCover$/.test(String(o.opKey)));
  expect(raw.map((o) => o.templateId)).toEqual(["cut-tut-short-резка", "ins-tut-монтаж", "heat-tyt-short-усадка"]);
  expect(raw[0]).toMatchObject({ coverMm: 4, tutParts: [{ len: "22", count: 4 }] });
  raw.forEach((o) => { expect(o).not.toHaveProperty("markTube"); expect(o).not.toHaveProperty("wireMarks"); });
  // Схема ПФ: резка — с прочими трубками («Резка трубок»), надеть и усадка — в «Провода с маркировкой».
  const graph = g.pfGraph()!;
  const pf = new Map(ops.map((o) => [o.opKey, o.pf]));
  expect([pf.get("cutWireMarkCover"), pf.get("onWireMarkCover"), pf.get("heatWireMarkCover")]).toEqual(["pf-tubes", "pf-wiremark", "pf-wiremark"]);
  expect(graph.blocks.find((b) => b.key === "pf-tubes")).toMatchObject({ name: "Резка трубок", kit: true });
  expect(graph.edges.find((e) => e.from === "pf-tubes" && e.to === "pf-wiremark")?.items).toEqual(["ТУТ(HF) 4/2 прозрачная 22 мм — 4 шт"]);
});

it("покрытие трубки маркиратора: Ø над трубкой TP80E, надеть сразу после метки, усадка последней", () => {
  const data = { ...DSA, markMats: [{ name: "ТУТ 4,8/2,4 белая", group: "ТУТ", unit: "м" }] };
  const ops = makeGen(data, on(threeWires(), { kind: "tube", tubeMm: "4.8", cover: true })).getActiveOps();
  const keys = ops.map((o) => o.opKey).filter((k) => /WireMark/.test(k));
  expect(keys).toEqual(["cutWireMarkCover", "cutWireMark", "onWireMark", "onWireMarkCover", "heatWireMark", "heatWireMarkCover"]);
  expect(ops.find((o) => o.opKey === "cutWireMarkCover")).toMatchObject({ tutName: "ТУТ 6/3 прозрачная", coverMm: 6, tutParts: [{ len: "25", count: 4 }] });
});
