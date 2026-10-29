// @vitest-environment node
import { expect, it } from "vitest";
import { genSnapshot, restoreFromSnapshot } from "./genBuildState";
import { emptyState, makeGen, setTailMode, tailModeOf, type GenData, type GenState } from "./tcGenLogic";
import { lugCrimpOf, lugRecordsOf, lugTypes } from "./tcGenLugs";
import { DS, harness, R, TEMPLATES } from "./tcGenRealHarness.fixture";

/*
 * «Свободные концы» → «Наконечник» (владелец 29.10: «нет возможности сделать на свободные концы НШВИ, НКИ и т.д.»): хвосты
 * «Жгута НВ4» (0,2 красный и чёрный, 0,12 белый и синий — сторона В) обжимаются наконечниками справочника. Втулочные и
 * кабельные — разные операции, у каждой своя зачистка (отдельно от резки, длина из справочника), припуски L+/L− — как у
 * контакта.
 */
type Op = Record<string, unknown>;
const lug = (id: string, fields: Record<string, string>, ter_kind = "lug") => ({ id, ter_kind, fields });
const TER = [
  lug("n025", { terComponent: "НШВИ", terArticle: "НШВИ 0.25-8", mm2From: "0,2", mm2To: "0,2", awgFrom: "24", awgTo: "24", terStrip: "10", terLPlus: "2", terLMinus: "6" }),
  lug("n05", { terComponent: "НШВИ", terArticle: "НШВИ 0.5-8", mm2From: "0,5", mm2To: "0,5", terStrip: "10" }),
  lug("n-wide", { terComponent: "НШВИ", terArticleBag: "НШВИ 0.2-0.5-8", mm2From: "0,12", mm2To: "0,5", terStrip: "9" }),
  lug("nki", { terComponent: "НКИ", terArticle: "НКИ 1.5-4", mm2From: "0,1", mm2To: "1,5", terApplicator: "M31" }),
  lug("pm", { terComponent: "ПМ", terArticle: "ПМ 1.5-4", mm2From: "0,1", mm2To: "1,5" }),
  lug("sym", { terComponent: "SYM", terArticle: "SYM-001T-P0.6", mm2From: "0,08", mm2To: "0,35" }, "contact"),
];
const NKI_CARD = { id: "t-nki", title: "PRS_HKИ/НВИ/РПИ | Опрессовка наконечников", category: "Опрессовка наконечников" };
const DL: GenData = { ...DS, templates: [...TEMPLATES, NKI_CARD], lugRecords: lugRecordsOf(TER) };
const ferruleCard = TEMPLATES.find((t) => t.title.startsWith("PRS_НШВИ |"))!.id;
const stripCard = TEMPLATES.find((t) => t.title.startsWith("STRIP_WIRE |"))!.id;

const lugs = (S: GenState, type = "НШВИ"): GenState => {
  const on = setTailMode(S, "lug");
  return { ...on, tailLug: { ...on.tailLug!, type } };
};
const ops = (S: GenState, data: GenData = DL) => makeGen(data, S).buildConfig().ops as Op[];
const op = (S: GenState, key: string) => ops(S).find((o) => o.opKey === key);

it("reads the lugs only from the lug list, takes the type from the record and keeps solder lugs out of the crimp types", () => {
  const recs = lugRecordsOf(TER);
  expect(recs.map((r) => [r.type, r.article || r.articleBag, r.strip, r.lPlus])).toEqual([
    ["НШВИ", "НШВИ 0.25-8", "10", "2"], ["НШВИ", "НШВИ 0.5-8", "10", ""], ["НШВИ", "НШВИ 0.2-0.5-8", "9", ""], ["НКИ", "НКИ 1.5-4", "", ""],
    ["ПМ", "ПМ 1.5-4", "", ""]]);
  expect(lugTypes(recs)).toEqual(["НШВИ", "НКИ"]);
  expect(["НШВИ", "НШВ", "НКИ", "НВИ", "РПИ-М", "ТМЛ", "ПМ"].map(lugCrimpOf)).toEqual(["ferrule", "ferrule", "lug", "lug", "lug", "tube", null]);
});

it("picks the lug per wire section from the reference: the narrowest range wins, a section without a lug is flagged", () => {
  const plan = makeGen(DL, lugs(harness(DL))).tailLugPlan_();
  expect(plan.map((r) => [r.idx, r.section, r.lug ? r.lug.article || r.lug.articleBag : "", r.problem])).toEqual([
    [R.r2t, "0,2 мм²", "НШВИ 0.25-8", ""], [R.k2t, "0,2 мм²", "НШВИ 0.25-8", ""],
    [R.w12t, "0,12 мм²", "НШВИ 0.2-0.5-8", ""], [R.b12t, "0,12 мм²", "НШВИ 0.2-0.5-8", ""]]);
  // Без широкой втулки 0,12 мм² не обжать ничем — красным в форме и предупреждением при сборке.
  const narrow: GenData = { ...DL, lugRecords: DL.lugRecords!.filter((r) => r.id !== "n-wide") };
  const S = lugs(harness(narrow));
  expect(makeGen(narrow, S).tailLugPlan_().filter((r) => !r.lug).map((r) => [r.idx, r.problem]))
    .toEqual([[R.w12t, "нет наконечника под сечение"], [R.b12t, "нет наконечника под сечение"]]);
  const { warnings } = makeGen(narrow, S).validate();
  expect(warnings).toEqual(expect.arrayContaining(["Свободные концы «НВ4 0,12 белый»: нет наконечника под сечение — опрессовки не будет."]));
  expect((makeGen(narrow, S).buildConfig().ops as Op[]).find((o) => o.opKey === "prsLugF2")).toBeUndefined();
});

it("ferrules: their own strip after the cut (length from the reference) and a crimp per article with the lug per tail end", () => {
  const S = lugs(harness(DL));
  const keys = ops(S).map((o) => String(o.opKey));
  const lastCut = keys.reduce((last, key, i) => (/^cutWire/.test(key) ? i : last), -1);
  expect(keys.slice(lastCut + 1).filter((k) => /Lug/.test(k))).toEqual(["stripLugF", "prsLugF1", "prsLugF2"]);
  expect(keys.indexOf("prsLugF2")).toBeLessThan(keys.findIndex((key) => /^insTerm/.test(key)));
  expect(keys.some((k) => ["stripTail", "tinTail", "cutCap", "endCap"].includes(k))).toBe(false);
  expect(op(S, "stripLugF")).toMatchObject({ type: "stripCable", templateId: stripCard, tutCount: 4, strip: { a: false, lenA: "", b: true, lenB: "10 / 9" } });
  const first = op(S, "prsLugF1")!;
  expect(first).toMatchObject({ type: "prsTerm", templateId: ferruleCard, side: "B",
    ep: expect.objectContaining({ termArt: "НШВИ 0.25-8", termName: "Наконечник НШВИ", label: "свободные концы" }) });
  expect((first.wires as Array<{ art: string; qty: number }>).map((w) => `${w.art} ×${w.qty}`)).toEqual(["НВ4 0,2 красный ×1", "НВ4 0,2 черный ×1"]);
  expect(makeGen(DL, S).getActiveOps().find((o) => o.opKey === "prsLugF1")!.label).toBe("Опрессовка втулочных наконечников НШВИ 0.25-8 — 2 шт");
  expect(op(S, "prsLugF2")).toMatchObject({ ep: expect.objectContaining({ termArt: "НШВИ 0.2-0.5-8" }) });
});

it("a ferrule and a ring lug on one harness are two crimps, each after its own strip; no strip length in the reference — the form's", () => {
  const S0 = lugs(harness(DL));
  const S: GenState = { ...S0, tailTin: { enabled: false, strip: "4" }, tailLug: { ...S0.tailLug!, arts: { [R.w12t]: "НКИ 1.5-4", [R.b12t]: "нки 1.5-4" } } };
  expect(ops(S).filter((o) => /Lug/.test(String(o.opKey))).map((o) => o.opKey)).toEqual(["stripLugF", "prsLugF1", "stripLugL", "prsLugL1"]);
  expect(op(S, "stripLugL")).toMatchObject({ tutCount: 2, strip: { b: true, lenB: "4" } });
  expect(op(S, "prsLugL1")).toMatchObject({ templateId: "t-nki", ep: expect.objectContaining({ termArt: "НКИ 1.5-4" }) });
  expect((op(S, "prsLugL1")!.wires as unknown[]).length).toBe(2);
  const { warnings } = makeGen(DL, S).validate();
  expect(warnings).toEqual(expect.arrayContaining([
    "Наконечник «НКИ 1.5-4»: длины зачистки нет в БД — взята «Зачистка хвоста» 4 мм.",
    "Наконечника «НКИ 1.5-4» нет в составе — добавьте его в состав КБ."]));
  // Тип «НКИ» по сечению: 0,2 и 0,12 входят в 0,1–1,5.
  expect(makeGen(DL, lugs(harness(DL), "НКИ")).tailLugPlan_().every((r) => r.lug?.article === "НКИ 1.5-4")).toBe(true);
});

it("adds the lug's L+ / L− to the tail wire like a contact's, and «Длина реза по КД» cuts without them", () => {
  const len = (S: GenState, idx: number) => (makeGen(DL, S).buildConfig().wires as Array<{ length: number }>)[idx].length;
  const S = lugs(harness(DL));
  expect(len({ ...S, measureMode: "inner" }, R.r2t)).toBe(62);
  expect(len({ ...S, measureMode: "outer" }, R.r2t)).toBe(54);
  expect(len({ ...S, measureMode: "cut" }, R.r2t)).toBe(60);
  expect(len({ ...S, measureMode: "inner" }, R.w12t)).toBe(60);  // у широкой втулки припусков нет
  expect(len({ ...setTailMode(S, "tin"), measureMode: "inner" }, R.r2t)).toBe(60);
});

it("old forms open as before; the lug choice is saved with the form and survives the snapshot", () => {
  const old = harness(DL);
  expect(old.tailLug).toBeUndefined();
  expect(tailModeOf(old)).not.toBe("lug");
  expect(tailModeOf(setTailMode(old, "tin"))).toBe("tin");
  expect(setTailMode(old, "tin").tailLug).toBeUndefined();
  const S0 = lugs(old);
  const S: GenState = { ...S0, tailLug: { ...S0.tailLug!, arts: { [R.w12t]: "НКИ 1.5-4" } } };
  expect(tailModeOf(S)).toBe("lug");
  expect(setTailMode(S, "cap")).toMatchObject({ cap: { enabled: true }, tailTin: { enabled: false }, tailLug: { enabled: false, type: "НШВИ" } });
  const restored = restoreFromSnapshot(DL.components, emptyState(), genSnapshot(DL.components, S, {}, {})).S;
  expect(restored.tailLug).toEqual({ enabled: true, type: "НШВИ", arts: { [R.w12t]: "НКИ 1.5-4" } });
  expect(ops(restored).map((o) => o.opKey)).toEqual(ops(S).map((o) => o.opKey));
});
