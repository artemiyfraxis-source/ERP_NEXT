// @vitest-environment node
import { expect, it } from "vitest";
import sample from "../../backend/tests/techcards/generator_713k4_config.json";
import { makeGen, type GenData, type GenState, type OpSpec } from "./tcGenLogic";
import { DSA, fullHarness, R } from "./tcGenRealHarness.fixture";

/*
 * 713К.4 КБ1 «Жгут НВ4» (владелец 16.10): «Обжим на автомате при резке» на стороне с разными терминалами — партиями по
 * терминалу. Сторона А: SYM («5», «I3», «A6») — одна партия, SHF («C») — другая; сторона В: T-DS1071; провода «A6» → В2 с
 * терминалами на обоих концах — партия обжима А и В (CUT_WIRE+2crimp). Концы в сростках и хвостах станок не обжимает.
 */
const cuts = (S: GenState, data: GenData = DSA) => makeGen(data, S).getActiveOps().filter((o) => /^cutWire/.test(o.type));
const brief = (o: OpSpec) => ({ label: o.label, tmplHint: o.tmplHint, wireIdxs: o.wireIdxs, wireUnits: o.wireUnits });

it("crimps a side with several terminal types at the cut, one batch per terminal, and both ends in one batch", () => {
  const S = fullHarness();
  const gen = makeGen(DSA, S);
  expect(gen.crimpCutSides_()).toEqual(["A", "B"]);
  expect(gen.crimpCutSideSelected_()).toBe("AB");
  expect(gen.crimpCutSideNote_("A")).toBe("");
  expect(cuts(S).map(brief)).toEqual([
    { label: "Резка с обжимом А (SYM-001T-P0.6) — 4 кабеля", tmplHint: "CUT_WIRE+1crimp", wireIdxs: [R.r5, R.k5, R.w12, R.b12],
      wireUnits: { [R.w12]: 1, [R.b12]: 1 } },
    { label: "Резка с обжимом В (T-DS1071-SC600) — 3 кабеля", tmplHint: "CUT_WIRE+1crimp", wireIdxs: [R.r2, R.k2a, R.k2b], wireUnits: undefined },
    { label: "Резка — 4 кабеля", tmplHint: "CUT_WIRE_auto", wireIdxs: [R.r2t, R.k2t, R.w12t, R.b12t], wireUnits: undefined },
    { label: "Резка с обжимом А (SHF-001T-0.8BS) — 2 кабеля", tmplHint: "CUT_WIRE+1crimp", wireIdxs: [R.w12, R.b12],
      wireUnits: { [R.w12]: 1, [R.b12]: 1 } },
    { label: "Резка с обжимом А и В (SYM-001T-P0.6 / T-DS1071-SC600) — 3 кабеля", tmplHint: "CUT_WIRE+2crimp", wireIdxs: [R.w2, R.g2, R.y2],
      wireUnits: undefined },
  ]);
  // Концы обжаты при резке — ручной опрессовки нет, монтаж всех шести разъёмов остаётся.
  const ops = gen.getActiveOps();
  expect(ops.filter((o) => o.type === "prsTerm")).toEqual([]);
  expect(ops.filter((o) => o.type === "insTerm").map((o) => o.opKey)).toEqual(["insTermA1", "insTermA2", "insTermA3", "insTermA4", "insTermB1", "insTermB2"]);
  // Партия обжимает концы на всех разъёмах своего терминала; зачистка — длина терминала из БД.ТЕР.
  const [sym, tds] = cuts(S);
  expect(sym).toMatchObject({ side: "A", crimpEpIds: [S.endpoints.find((e) => e.label === "5")!.id, S.endpoints.find((e) => e.label === "I3")!.id],
    strip: { a: true, lenA: "2,5", b: false, lenB: "" } });
  expect(tds).toMatchObject({ side: "B", strip: { a: false, b: true, lenB: "2,5" } });
  expect(gen.crimpCutBatchNote_()).toBe("Партии: А — SYM-001T-P0.6: 4 пр., SHF-001T-0.8BS: 2 пр.; В — T-DS1071-SC600: 4 пр.; "
    + "А и В — SYM-001T-P0.6 / T-DS1071-SC600: 3 пр.; концы в сростках и хвостах не обжимаются");
});

it("re-picks every cut's template: a key stuck to a plain cut does not follow it onto a crimp batch", () => {
  // 713К.4 КБ1, ревизия 3: cutWire1 был простой резкой, после партий обжима стал партией SYM, а сохранённый шаблон
  // «Резка» остался на ключе — карта вышла без опрессовки.
  const S = fullHarness();
  const gen = makeGen(DSA, S);
  const ops = cuts(S);
  const plain = gen.templateIdFor(ops.find((o) => o.tmplHint === "CUT_WIRE_auto")!);
  const want = ops.map((o) => gen.templateIdFor(o));
  expect(new Set(want).size).toBeGreaterThan(1);
  const stale = { ...S, selectedTemplates: Object.fromEntries(ops.map((o) => [o.opKey, plain])) };
  expect(cuts(stale).map((o) => makeGen(DSA, stale).templateIdFor(o))).toEqual(want);
  // Ручной выбор в карточке операции остаётся главнее.
  const manual = { ...stale, manualTemplates: [ops[0].opKey] };
  expect(makeGen(DSA, manual).templateIdFor(cuts(manual)[0])).toBe(plain);
});

it("the build config carries each batch's own wires and terminals — the server sample is this very config", () => {
  const cfg = makeGen(DSA, fullHarness()).buildConfig() as { ops: Array<Record<string, any>> };
  const batches = cfg.ops.filter((o) => o.type === "cutWireCrimp");
  expect(batches.map((o) => [o.ep.termArt, o.epB?.termArt ?? "", o.ep.label, o.crimpBatch])).toEqual([
    ["SYM-001T-P0.6", "", "5, I3", true], ["T-DS1071-SC600", "", "В1, В2", true], ["SHF-001T-0.8BS", "", "C", true],
    ["SYM-001T-P0.6", "T-DS1071-SC600", "A6", true],
  ]);
  // Провод ×2 «белый 0,12» — по одной жиле в партиях SYM и SHF; «красный 0,2» ×2 — обе к T-DS1071.
  expect(batches[0].wires.map((w: any) => `${w.art} ×${w.qty}`)).toEqual(["НВ4 0,5 красный ×1", "НВ4 0,5 черный ×1", "НВ4 0,12 белый ×1", "НВ4 0,12 синий ×1"]);
  expect(batches[1].wires.map((w: any) => `${w.art} ×${w.qty}`)).toEqual(["НВ4 0,2 красный ×2", "НВ4 0,2 черный ×1", "НВ4 0,2 черный ×1"]);
  // Терминалов в партиях — столько же, сколько в составе: SYM 7, T-DS1071 7, SHF 2.
  const count = (art: string) => batches.reduce((n, o) => n + [o.ep, o.epB].filter((e) => e?.termArt === art).length
    * o.wires.reduce((s: number, w: any) => s + w.qty, 0), 0);
  expect([count("SYM-001T-P0.6"), count("T-DS1071-SC600"), count("SHF-001T-0.8BS")]).toEqual([7, 7, 2]);
  expect(cfg).toEqual(sample);
});

it("one crimp side picked by hand: the other side's ends are crimped by hand, both-ends wires join their side's batch", () => {
  const S = { ...fullHarness(), crimpCutSide: "A" };
  expect(cuts(S).map((o) => o.label)).toEqual([
    "Резка с обжимом А (SYM-001T-P0.6) — 7 кабелей", "Резка — 7 кабелей", "Резка с обжимом А (SHF-001T-0.8BS) — 2 кабеля",
  ]);
  const ops = makeGen(DSA, S).getActiveOps();
  expect(ops.filter((o) => o.type === "prsTerm").map((o) => o.label)).toEqual(["Опрессовка терминалов В1", "Опрессовка терминалов В2"]);
  // Обжим при резке выключен — ручная опрессовка у каждого разъёма, как раньше.
  const off = makeGen(DSA, { ...fullHarness(), crimpCutManual: true, crimpCut: false }).getActiveOps();
  expect(off.filter((o) => /^cutWire/.test(o.type)).map((o) => o.type)).toEqual(["cutWire"]);
  expect(off.filter((o) => o.type === "prsTerm")).toHaveLength(6);
});

it("a terminal without the machine applicator stays a manual crimp; the rest of its side is still crimped at the cut", () => {
  const data: GenData = { ...DSA, terRecords: DSA.terRecords.map((r) => (r.article === "shf-001T-0.8BS" ? { ...r, applicator: "S8" } : r)) };
  const S = fullHarness(data);
  const gen = makeGen(data, S);
  expect(gen.crimpCutSides_()).toEqual(["A", "B"]);
  expect(cuts(S, data).map((o) => o.label)).toEqual([
    "Резка с обжимом А (SYM-001T-P0.6) — 4 кабеля", "Резка с обжимом В (T-DS1071-SC600) — 3 кабеля", "Резка — 6 кабелей",
    "Резка с обжимом А и В (SYM-001T-P0.6 / T-DS1071-SC600) — 3 кабеля",
  ]);
  expect(gen.getActiveOps().filter((o) => o.type === "prsTerm").map((o) => o.label)).toEqual(["Опрессовка терминалов А3"]);
  expect(gen.crimpCutBatchNote_()).toContain("вручную, нет аппликатора автомата: SHF-001T-0.8BS (А)");
  // Ни у одного терминала стороны нет аппликатора — сторона вручную, причина в форме (как раньше).
  const none: GenData = { ...DSA, terRecords: DSA.terRecords.map((r) => (r.article === "T-DS1071-SC600" ? r : { ...r, applicator: "S8" })) };
  const g2 = makeGen(none, fullHarness(none));
  expect(g2.crimpCutSides_()).toEqual(["B"]);
  expect(g2.crimpCutSideNote_("A")).toBe("сторона А: у SYM-001T-P0.6, SHF-001T-0.8BS нет аппликатора автомата (A…) — обжим вручную");
});

it("terminal fit checks only the ends that go into a terminal: the 0,5 wire spliced on side В is not checked against T-DS1071", () => {
  const S = fullHarness();
  const warnings = makeGen(DSA, S).validate().warnings.filter((w) => w.includes("вне диапазона"));
  expect(warnings.some((w) => w.includes("терминала B"))).toBe(false);
  // Конец А провода 0,5 стоит в SYM (0,08–0,35 мм²) — это предупреждение честное.
  expect(warnings).toEqual([
    "• «НВ4 0,5 красный» (0.5 мм²) вне диапазона терминала A «SYM-001T-P0.6» (0.08–0.35 мм²)",
    "• «НВ4 0,5 черный» (0.5 мм²) вне диапазона терминала A «SYM-001T-P0.6» (0.08–0.35 мм²)",
  ]);
  // Тот же провод на контакте В1 — проверяется по T-DS1071, как раньше.
  const fits = (state: GenState) => makeGen(DSA, state).validate().warnings.filter((w) => w.includes("терминала B"));
  const onPin = { ...S, wires: S.wires.map((w) => (w.idx === R.r2 ? { ...w, compId: "r5" } : w)) };
  expect(fits(onPin)).toEqual(["• «НВ4 0,5 красный» (0.5 мм²) вне диапазона терминала B «T-DS1071-SC600» (0.08–0.2 мм²)"]);
});
