// @vitest-environment node
import { expect, it } from "vitest";
import { genSnapshot, restoreFromSnapshot } from "./genBuildState";
import {
  emptyState, initState, makeGen, setSplMethod, setTailMode, splMethodFromConfig, type Endpoint, type GenData, type GenState,
} from "./tcGenLogic";
import { DS, harness, R, tmplId } from "./tcGenRealHarness.fixture";

/*
 * «Жгут НВ4» (713К.4 КБ1) руками на «Сложной сборке» (владелец 16.10): хвосты зачищаются и лудятся при разъёмах стороны В,
 * трубка сростка — любая термоусадка состава (ТТК 3/1), у сростков есть способ — опрессовка гильзой (по умолчанию),
 * пайка, ленточный обжим — и гильзы НШВ из состава по сросткам.
 */
type Op = Record<string, unknown>;
const ops = (S: GenState, data: GenData = DS) => makeGen(data, S).buildConfig().ops as Op[];
const op = (S: GenState, key: string, data: GenData = DS) => ops(S, data).find((o) => o.opKey === key);
const withSleeves = (S: GenState) => {
  const [c1, c2, c3, c4] = S.splices!.map((s) => s.id);
  return { ...S, spl: { ...S.spl, sleeves: { [c1]: "nshv10", [c2]: "nshv10", [c3]: "nshv05", [c4]: "nshv05" } } };
};

it("tails get strip and tin ops while side В has connectors: 4 tail ends, 3 mm by default", () => {
  const S = setTailMode(harness(DS), "tin");
  const keys = makeGen(DS, S).getActiveOps().map((o) => o.opKey);
  expect(keys).toEqual(expect.arrayContaining(["stripTail", "tinTail"]));
  expect(keys.indexOf("stripTail")).toBeLessThan(keys.indexOf("tinTail"));
  // Схема ПФ «Сложной сборки» (владелец 16.10): резка → группы сростков → изделие (хвосты — первыми, до монтажа разъёмов).
  // Карты CUT_WIRE+2strip+tin в библиотеке нет — хвосты своими операциями, как раньше.
  const lastCut = keys.reduce((last, key, i) => (/^cutWire/.test(key) ? i : last), -1);
  expect(lastCut).toBeGreaterThanOrEqual(0);
  expect(keys.indexOf("stripTail")).toBe(keys.indexOf("splice2") + 1);
  expect(keys.indexOf("tinTail")).toBe(keys.indexOf("stripTail") + 1);
  expect(keys.indexOf("tinTail")).toBeLessThan(keys.findIndex((key) => /^insTerm/.test(key)));
  expect(op(S, "stripTail")).toMatchObject({ type: "stripCable", templateId: tmplId("STRIP_WIRE"), tutCount: 4, strip: { a: false, lenA: "", b: true, lenB: "3" } });
  const tin = op(S, "tinTail")!;
  expect(tin).toMatchObject({ type: "tin", templateId: tmplId("SDR_tin"), units: 4, wireIdxs: [R.r2t, R.k2t, R.w12t, R.b12t] });
  expect((tin.wires as Array<{ art: string; length: number }>).map((w) => `${w.art} ${w.length}`))
    .toEqual(["НВ4 0,2 красный 60", "НВ4 0,2 черный 60", "НВ4 0,12 белый 60", "НВ4 0,12 синий 60"]);
  expect(makeGen(DS, S).getActiveOps().find((o) => o.opKey === "stripTail")!.label).toBe("Зачистка хвостов (3 мм) — 4 конц.");
  // Своя длина зачистки; заделка трубкой при лужении не делается.
  const own = { ...S, tailTin: { enabled: true, strip: "2" } };
  expect(op(own, "stripTail")!.strip).toEqual({ a: false, lenA: "", b: true, lenB: "2" });
  expect(ops({ ...S, cap: { enabled: true, tut: "ttk3", len: "" } }).some((o) => o.opKey === "endCap")).toBe(false);
  // «Ничего» — ни заделки, ни лужения хвостов.
  const none = setTailMode(S, "none");
  expect(ops(none).filter((o) => ["stripTail", "tinTail", "cutCap", "endCap"].includes(String(o.opKey)))).toEqual([]);
});

const A1: Endpoint = { id: "a1", side: "A", connId: "csmr2", method: "crimp", methodAuto: true, sdrTmpl: null, termId: "tsym", wireIdxs: [], otp: false,
  double: false, label: "", solderStrip: "", tutLen: "" };
/** Без стороны В: два провода на разъёме А, лужение свободных концов только у второго, хвост первого нарисован на схеме. */
const noSideB = (tailIdx: number): GenState => ({
  ...emptyState(), tab: "complex", advancedLength: true, endpoints: [A1], nextEndpointId: 1,
  wires: [{ idx: 0, compId: "r2", length: 60, qty: 1 }, { idx: 1, compId: "k2", length: 60, qty: 1 }], nextWireIdx: 2,
  tin: { enabled: true, wires: [1], stripLen: "5" }, tails: [{ idx: tailIdx, unit: 0, side: "B" }],
});

it("without side В the old «Лужение» stays as it was and tail tinning joins it in one op", () => {
  const old = ops(noSideB(0));
  expect(old.filter((o) => o.type === "tin").map((o) => [o.opKey, o.units])).toEqual([["tin", undefined]]);
  expect(old.some((o) => o.opKey === "stripTail")).toBe(false);
  // Хвост провода, который «Лужение» не лудит: одна операция лужения на оба конца, зачистка только хвоста.
  const merged = ops(setTailMode(noSideB(0), "tin"));
  expect(merged.filter((o) => o.type === "tin").map((o) => [o.opKey, o.units, o.wireIdxs])).toEqual([["tin", 2, [0, 1]]]);
  expect(merged.find((o) => o.opKey === "stripTail")).toMatchObject({ tutCount: 1, strip: { b: true, lenB: "3" } });
  // Хвост провода, который «Лужение» уже лудит (и резка зачищает): всё как было.
  const covered = ops(setTailMode(noSideB(1), "tin"));
  expect(covered.filter((o) => o.type === "tin" || o.opKey === "stripTail").map((o) => [o.opKey, o.units])).toEqual([["tin", undefined]]);
});

it("with the CUT_WIRE+2strip+tin card the tails of the splices are cut, stripped and tinned by the machine — no tail ops", () => {
  const data: GenData = { ...DS, templates: [...DS.templates, { id: "t-tail", title: "CUT_WIRE+2strip+tin | Резка с зачисткой двух концов и лужением",
    category: "Автомат резка + опрессовка" }] };
  const S = setTailMode(harness(data), "tin");
  const list = ops(S, data);
  expect(list.filter((o) => ["stripTail", "tinTail", "stripSpl"].includes(String(o.opKey)))).toEqual([]);
  expect(list.find((o) => o.templateId === "t-tail")).toMatchObject({ type: "cutWireStrip", tin: true, strip: { a: true, lenA: "5", b: true, lenB: "3" },
    wires: [R.r2t, R.k2t, R.w12t, R.b12t].map(() => expect.objectContaining({ length: 60 })) });
  // Без лужения хвостов (заделка трубкой) хвост при резке не трогают — зачищается только конец под сросток.
  const cap = setTailMode(harness(data), "cap");
  const tailCut = ops(cap, data).find((o) => (o.wires as Array<{ length: number }> | undefined)?.some((w) => w.length === 60));
  expect(tailCut).toMatchObject({ type: "cutWireStrip", strip: { a: true, b: false } });
});

it("the splice tube and cap pickers list every heat-shrink tube of the BOM — ТТК 3/1 included; the cap seed stays ТУТ-only", () => {
  const S = harness(DS);
  expect(makeGen(DS, S).capTubes().map((c) => c.art)).toEqual(["ТТК 3/1мм, черная", "ТТК 8/2мм, черная", "ТТК 12/4мм, черная"]);
  expect(initState(DS).cap.tut).toBe("");
  const tubed = { ...S, spl: { ...S.spl, tut: "30", tutMat: "ttk3" } };
  // Трубка сростков режется вместе со всеми трубками — одной операцией.
  expect(op(tubed, "cutTubes")).toMatchObject({ tutArt: "ТТК 3/1мм, черная", tutCount: 4, tutParts: [{ art: "ТТК 3/1мм, черная", count: 4, len: "30" }] });
});

it("a new form splices by sleeve crimping: PRS_НШВИ with the BOM sleeves counted per splice", () => {
  const S0 = harness(DS);
  expect(S0.spl.method).toBe("sleeve");
  const gen0 = makeGen(DS, S0);
  expect(gen0.spliceSleeveComps_().map((c) => c.art)).toEqual(["НШВ 1,0-8", "НШВ 0,5-8"]);
  expect(gen0.spliceSlots_().map((s) => `${s.label}:${s.wires}`)).toEqual(["С1:4", "С2:4", "С3:3", "С4:3"]);
  const S = withSleeves(S0);
  // Группы сростков (по разъёмам А, с которых приходят провода): С1, С2 — с «5», С3, С4 — с «I3» и «C»; гильзы — своей группы.
  expect(op(S, "splice1")).toMatchObject({ type: "splice", units: 2, method: "sleeve", templateId: tmplId("PRS_НШВИ"),
    sleeves: [{ art: "НШВ 1,0-8", name: "НШВ 1,0-8", count: 2 }] });
  expect(op(S, "splice2")).toMatchObject({ units: 2, sleeves: [{ art: "НШВ 0,5-8", name: "НШВ 0,5-8", count: 2 }] });
  expect(makeGen(DS, S).getActiveOps().filter((o) => o.type === "splice").map((o) => o.label)).toEqual([
    "Сростки С1, С2 опрессовкой гильзой — 2 шт по 4 провода: НШВ 1,0-8 ×2",
    "Сростки С3, С4 опрессовкой гильзой — 2 шт по 3 провода: НШВ 0,5-8 ×2"]);
});

it("solder and band crimping: their template and method; band carries its optional material", () => {
  const S = withSleeves(harness(DS));
  const solder = setSplMethod(S, "solder");
  expect(op(solder, "splice1")).toMatchObject({ method: "solder", templateId: tmplId("SDR_W_W"), sleeves: [], units: 2 });
  const band = setSplMethod(S, "band");
  expect(op(band, "splice2")).toMatchObject({ method: "band", templateId: tmplId("PRS_COPPER"), sleeves: [] });
  const banded = { ...band, spl: { ...band.spl, band: "nshv05" } };
  expect([op(banded, "splice1")!.sleeves, op(banded, "splice2")!.sleeves]).toEqual([
    [{ art: "НШВ 0,5-8", name: "НШВ 0,5-8", count: 2 }], [{ art: "НШВ 0,5-8", name: "НШВ 0,5-8", count: 2 }]]);
});

it("changing the method drops the splice card picked for the old one — even a hand-picked card", () => {
  const S = { ...withSleeves(harness(DS)), selectedTemplates: { splice: "y", splice1: tmplId("SDR_W_W"), cutWire: "x" },
    manualTemplates: ["splice", "splice1", "cutWire"] };
  expect(op(S, "splice1")!.templateId).toBe(tmplId("SDR_W_W"));
  const next = setSplMethod(S, "band");
  expect(next.selectedTemplates).toEqual({ cutWire: "x" });
  expect(next.manualTemplates).toEqual(["cutWire"]);
  expect(op(next, "splice1")!.templateId).toBe(tmplId("PRS_COPPER"));
  expect(setSplMethod(next, "band")).toBe(next);
});

it("splices set only by count share one sleeve picker; a lone BOM sleeve is taken by itself", () => {
  const S = { ...emptyState(), tab: "complex" as const, spl: { ...emptyState().spl, enabled: true, count: "2", wires: "3" } };
  const one: GenData = { ...DS, components: DS.components.filter((c) => c.id !== "nshv10") };
  expect(makeGen(one, S).spliceSlots_().map((s) => [s.key, s.label, s.count])).toEqual([["*", "все сростки", 2]]);
  expect(op(S, "splice", one)!.sleeves).toEqual([{ art: "НШВ 0,5-8", name: "НШВ 0,5-8", count: 2 }]);
  expect(op(S, "splice")!.sleeves).toEqual([]);
  expect(op({ ...S, spl: { ...S.spl, sleeves: { "*": "nshv10" } } }, "splice")!.sleeves).toEqual([{ art: "НШВ 1,0-8", name: "НШВ 1,0-8", count: 2 }]);
});

it("the BOM check flags a НШВ sleeve until a splice uses it", () => {
  const S0 = harness(DS);
  const warn = (S: GenState) => makeGen(DS, S).bomWarnings_(makeGen(DS, S).buildConfig()).filter((w) => /гильз/i.test(w));
  expect(warn(S0)).toEqual([
    "Гильза «НШВ 1,0-8» из состава не выбрана ни в одном сростке.", "Гильза «НШВ 0,5-8» из состава не выбрана ни в одном сростке.",
    "Сростки (С1): не выбрана гильза из состава.", "Сростки (С2): не выбрана гильза из состава.", "Сростки (С3): не выбрана гильза из состава.",
    "Сростки (С4): не выбрана гильза из состава.",
  ]);
  expect(warn(withSleeves(S0))).toEqual([]);
  const half = { ...S0, spl: { ...S0.spl, sleeves: { [S0.splices![0].id]: "nshv10", [S0.splices![1].id]: "nshv10" } } };
  expect(warn(half)).toEqual(["Гильза «НШВ 0,5-8» из состава не выбрана ни в одном сростке.", "Сростки (С3): не выбрана гильза из состава.",
    "Сростки (С4): не выбрана гильза из состава."]);
});

it("a draft or card saved before 16.10 (no spl.method) opens as soldering and builds the same splice op", () => {
  const S = harness(DS);
  const { method: _m, ...oldSpl } = S.spl;
  const old = { ...S, spl: oldSpl };
  expect(makeGen(DS, old).splMethod_()).toBe("solder");
  const spec = makeGen(DS, old).getActiveOps().find((o) => o.opKey === "splice1")!;
  expect(spec).toMatchObject({ type: "splice", tmplHint: "SDR_W_W", cat: "Пайка", units: 2, label: "Сростки С1, С2 — 2 шт по 4 провода" });
  expect(op(old, "splice1")).toMatchObject({ type: "splice", templateId: tmplId("SDR_W_W"), units: 2, method: "solder", sleeves: [] });
  // Снимок формы в собранной карте без способа — пайка, хотя засев новой формы — гильза.
  const back = restoreFromSnapshot(DS.components, initState(DS), genSnapshot(DS.components, old, {}, {}));
  expect(back.S.spl.method).toBe("solder");
  expect(restoreFromSnapshot(DS.components, initState(DS), genSnapshot(DS.components, withSleeves(S), {}, {})).S.spl)
    .toMatchObject({ method: "sleeve", sleeves: withSleeves(S).spl.sleeves });
  // Карта без снимка: по операции «Сростки» её конфига.
  expect(splMethodFromConfig({ ops: [{ type: "splice", units: 4 }] })).toBe("solder");
  expect(splMethodFromConfig(makeGen(DS, S).buildConfig())).toBe("sleeve");
  expect(splMethodFromConfig({ ops: [{ type: "cutWire" }] })).toBeNull();
  expect(splMethodFromConfig(null)).toBeNull();
});
