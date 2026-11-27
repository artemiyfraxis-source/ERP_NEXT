import { describe, expect, it } from "vitest";
import { coaxSerialPatch, initState, makeGen, withCoaxVswrFromConfig, type GenData, type GenState } from "./tcGenLogic";
import { coaxCoverLengths, coaxMarkDefaults, coaxSerialKb, coaxSerialProductFromName, coaxSerialText } from "./tcGenMarks";
import fixture from "./__fixtures__/coaxSerialConfigs.json";

// 831К. КБ1 (27.11): SMA/RG58 without FAKRA parts, black end TUT in the kit; the clear cover TUT is added for the labels.
const components = [
  { id: "k", type: "Кабель", name: "RG58C/U", art: "RG58C/U", qty: 0.51 },
  { id: "a", type: "Соед", name: "SMA-C58P", art: "SMA-C58P", qty: 1 },
  { id: "b", type: "Соед", name: "SMA-C58P", art: "SMA-C58P-B", qty: 1 },
  { id: "t", type: "ТУТ", name: "ТУТ 6/3 чёрная", art: "ТУТ 6/3 чёрная", qty: 0.04 },
  { id: "c", type: "ТУТ", name: "ТУТ 6/3 прозрачная", art: "ТУТ 6/3 прозрачная", qty: 0.05 },
];
const D: GenData = {
  components, assemblyInfo: { "Индекс": "КБ1", "Наименование": "Кабельная сборка SMA-Male to SMA-Male RG58C/U 500mm" },
  projectNo: "831К.", itemIndex: "КБ1", ops: [],
  templates: ["CUT_COAX+2strip", "PRINT_MARK_LABEL", "PRINT_MARK_TUBE", "CUT_TUT_short", "SDR_COAX_pin", "INS_TUT", "INS_SLEEVE_coax",
    "INS_housing_COAX", "PRS_SLEEVE_coax", "HEAT_TYT_short", "INS_MARK_LABEL", "CTRL_VSWR_coax"].map((code) => ({ id: code, title: `${code} | ${code}`, category: "Прочее" })), terRecords: [], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [],
};
const profile = { id: "p40", name: "40×60", lengthMm: 40 };
const withCoax = (S: GenState, patch: Partial<GenState["coax"]>): GenState => ({ ...S, coax: { ...S.coax, ...patch } });
const serialOn = (patch: Partial<GenState["coax"]> = {}) => {
  const S = initState(D);
  return withCoax(withCoax(S, coaxSerialPatch(D, S.coax, true)), patch);
};
const keys = (S: GenState) => makeGen(D, S).getActiveOps().map((op) => op.opKey);
const cfg = (S: GenState) => makeGen(D, S).buildConfig() as Record<string, any>;

describe("заводской номер на изделии: шаблон FRX.<проект>.<изделие>.{№} (владелец 27.11)", () => {
  it("КБ по умолчанию, своё обозначение или PN заказчика; номер без нулей заполняется при печати", () => {
    expect(coaxSerialText("831К.", "КБ1")).toBe("FRX.831К.КБ1.{№}");
    expect(coaxSerialText("831К.", "КБ-2")).toBe("FRX.831К.КБ2.{№}");
    expect(coaxSerialText("591K.3", "591K.3.КБ4")).toBe("FRX.591K.3.КБ4.{№}");
    expect(coaxSerialText("831К.", "КБ1", " 34.226 ")).toBe("FRX.831К.34.226.{№}");
    expect(coaxSerialKb("708К.", "01")).toBe("01");
    expect(coaxSerialProductFromName("Кабель FAKRA 120907/AA → 120908")).toBe("120907/AA");
    expect(coaxSerialProductFromName("Кабельная сборка SMA-Male RG58C/U 500mm")).toBe("");
  });

  it("включение кладёт одну метку в середину на изделие с QR, ленту и прозрачную ТУТ; выключение её убирает", () => {
    const S = serialOn();
    expect(S.coax).toMatchObject({ serialMark: true, serialProduct: "", markMaterial: "tape", markProtect: true });
    expect(S.coax.marks).toEqual([{ text: "FRX.831К.КБ1.{№}", place: "middle", minMm: 100, scope: "assembly", serial: true, qr: true }]);
    const custom = withCoax(S, coaxSerialPatch(D, { ...S.coax, serialProduct: "34.226" }, true));
    expect(custom.coax.marks?.map((mark) => mark.text)).toEqual(["FRX.831К.34.226.{№}"]);
    expect(coaxSerialPatch(D, { ...S.coax, marks: [...S.coax.marks!, { text: "А", place: "A", minMm: 100 }] }, false))
      .toEqual({ serialMark: false, marks: [{ text: "А", place: "A", minMm: 100 }] });
  });

  it("метка с номером из PN (708К.) заменяется, а не дублируется; PN заказчика — обозначение по умолчанию", () => {
    const named: GenData = { ...D, assemblyInfo: { ...D.assemblyInfo, "Наименование": "Кабель 120907/AA" } };
    const S = withCoax(initState(named), { marks: [{ text: "FRX.831К.120907/AA.{№}", place: "middle", minMm: 100 }] });
    const patch = coaxSerialPatch(named, S.coax, true);
    expect(patch.serialProduct).toBe("120907/AA");
    expect(patch.marks?.map((mark) => mark.text)).toEqual(["FRX.831К.120907/AA.{№}"]);
    expect(patch.marks?.[0].serial).toBe(true);
  });
});

describe("операции ЭТК обычного коакса с заводским номером", () => {
  it("печать → резка прозрачной ТУТ → … → наклейка → усадка → КСВН; ТУТ на концах из состава остаётся", () => {
    const S = serialOn({ labelProfile: profile });
    const ops = makeGen(D, S).getActiveOps();
    expect(ops.map((op) => [op.opKey, op.tmplHint, op.units ?? 1])).toEqual([
      ["coaxCutStrip", "CUT_COAX+2strip", 1],
      ["coaxPrintLabel", "PRINT_MARK_LABEL", 1],
      ["coaxProtectCut", "CUT_TUT_short", 1],
      ["coaxCutTut", "CUT_TUT_short", 2],
      ["coaxPinA", "SDR_COAX_pin", 1],
      ["coaxPinB", "SDR_COAX_pin", 1],
      ["coaxProtectIns", "INS_TUT", 1],
      ["coaxInsTut", "INS_TUT", 2],
      ["coaxInsSleeve", "INS_SLEEVE_coax", 2],
      ["coaxHousingA", "INS_housing_COAX", 1],
      ["coaxHousingB", "INS_housing_COAX", 1],
      ["coaxShield", "PRS_SLEEVE_coax", 2],
      ["coaxHeatTut", "HEAT_TYT_short", 2],
      ["coaxInsLabel", "INS_MARK_LABEL", 1],
      ["coaxHeat", "HEAT_TYT_short", 1],
      ["coaxVswr", "CTRL_VSWR_coax", 1],
    ]);
    expect(ops.find((op) => op.opKey === "coaxPrintLabel")).toMatchObject({ label: "Печать этикетки заводского номера", markMaterial: "tape", markPhase: "print" });
    expect(ops.filter((op) => op.protective).map((op) => op.opKey)).toEqual(["coaxProtectCut", "coaxProtectIns", "coaxHeat"]);
    expect(ops.filter((op) => op.endTut).map((op) => op.opKey)).toEqual(["coaxCutTut", "coaxInsTut", "coaxHeatTut"]);
    expect(ops.find((op) => op.opKey === "coaxCutTut")?.shrinkMaterials).toEqual([{ art: "ТУТ 6/3 чёрная", name: "ТУТ 6/3 чёрная" }]);
    expect(ops.find((op) => op.opKey === "coaxHeat")?.shrinkMaterials).toEqual([{ art: "ТУТ 6/3 прозрачная", name: "ТУТ 6/3 прозрачная" }]);
  });

  it("цветовая маркировка разъёмов — последней, после КСВН", () => {
    const S = serialOn({ connColor: { method: "paint", color: "белый", code: "WH", art: "" } });
    expect(keys(S).slice(-4)).toEqual(["coaxInsLabel", "coaxHeat", "coaxVswr", "coaxConnColor"]);
  });

  it("прозрачная ТУТ = этикетка по профилю + 10 мм; конфиг несёт шаблон, изделие, профиль и QR", () => {
    const config = cfg(serialOn({ labelProfile: profile }));
    expect(coaxCoverLengths(40)).toEqual({ A: 50, B: 50, middle: 50 });
    expect(config.coax).toMatchObject({
      markMaterial: "tape", markProtect: true, tutLengths: { A: 50, B: 50, middle: 50 }, coverPrintedMm: 40, tutLength: 20,
      tutArt: "ТУТ 6/3 прозрачная", labelProfile: profile,
      serial: { template: "FRX.831К.КБ1.{№}", designation: "КБ1", productKind: "kb", carrier: "tape", qr: true, profile },
    });
    expect(config.ops.find((op: any) => op.opKey === "coaxCutTut")).toMatchObject({ endTut: true, tutArt: "ТУТ 6/3 чёрная", units: 2 });
    expect(config.ops.find((op: any) => op.opKey === "coaxPrintLabel")).toMatchObject({
      label: "Печать этикетки заводского номера", marks: [{ text: "FRX.831К.КБ1.{№}", serial: true, qr: true }],
    });
    // Без профиля — прежние 35/55 мм и предупреждение.
    const plain = cfg(serialOn());
    expect(plain.coax.tutLengths).toEqual({ A: 35, B: 35, middle: 55 });
    expect(plain.coax).not.toHaveProperty("coverPrintedMm");
    expect(makeGen(D, serialOn()).validate().warnings.join("\n")).toMatch(/Профиль этикетки не выбран/);
  });

  it("своё обозначение изделия уходит в конфиг", () => {
    const S = serialOn();
    const custom = withCoax(S, coaxSerialPatch(D, { ...S.coax, serialProduct: "34.226" }, true));
    expect(cfg(custom).coax.serial).toMatchObject({ template: "FRX.831К.34.226.{№}", designation: "34.226", productKind: "custom" });
  });

  it("носитель — трубка: печать PRINT_MARK_TUBE, трубка надевается до корпусов, прозрачная ТУТ = трубка + 10 мм", () => {
    const S = serialOn({ markMaterial: "tube", markTubeLen: "25" });
    const ops = makeGen(D, S).getActiveOps();
    const order = ops.map((op) => op.opKey);
    expect(ops.find((op) => op.opKey === "coaxPrintLabel")).toMatchObject({ tmplHint: "PRINT_MARK_TUBE", markMaterial: "tube", printedTube: true,
      label: "Печать маркировочной трубки заводского номера" });
    // Трубку с номером надеть и усадить, затем надвинуть на неё прозрачную ТУТ — всё до корпусов; прозрачная усаживается
    // своей операцией в конце.
    expect(order).toEqual(["coaxCutStrip", "coaxPrintLabel", "coaxProtectCut", "coaxCutTut", "coaxPinA", "coaxPinB", "coaxInsMarkTube",
      "coaxHeatMarkTube", "coaxProtectIns", "coaxInsTut", "coaxInsSleeve", "coaxHousingA", "coaxHousingB", "coaxShield", "coaxHeatTut",
      "coaxHeat", "coaxVswr"]);
    expect(coaxCoverLengths(25)).toEqual({ A: 35, B: 35, middle: 35 });
    const at = (key: string) => order.indexOf(key);
    expect(at("coaxInsMarkTube")).toBeGreaterThan(-1);
    expect(at("coaxInsMarkTube")).toBeLessThan(at("coaxHeatMarkTube"));
    expect(at("coaxHeatMarkTube")).toBeLessThan(at("coaxProtectIns"));
    expect(at("coaxProtectIns")).toBeLessThan(at("coaxHousingA"));
    expect(at("coaxHeat")).toBeGreaterThan(at("coaxShield"));
    expect(order).not.toContain("coaxInsLabel");
    expect(ops.find((op) => op.opKey === "coaxProtectIns")).toMatchObject({ protective: true, label: "Надвинуть прозрачную ТУТ на маркировочную трубку" });
    expect(ops.find((op) => op.opKey === "coaxHeatMarkTube")).toMatchObject({ printedTube: true, label: "Усадка маркировочной трубки" });
    expect(ops.find((op) => op.opKey === "coaxHeat")).toMatchObject({ protective: true, label: "Усадка прозрачной ТУТ на маркировочной трубке" });
    const config = cfg(S);
    expect(config.coax).toMatchObject({ tutLengths: { A: 35, B: 35, middle: 35 }, coverPrintedMm: 25,
      serial: { carrier: "tube", tubeLengthMm: 25 } });
    expect(config.coax).not.toHaveProperty("labelProfile");
    // Без прозрачной ТУТ трубка усаживается своей операцией.
    const bare = makeGen(D, withCoax(S, { markProtect: false })).getActiveOps();
    expect(bare.find((op) => op.opKey === "coaxHeatMarkTube")).toMatchObject({ tmplHint: "HEAT_TYT_short", printedTube: true });
    expect(bare.map((op) => op.opKey)).not.toContain("coaxProtectCut");
  });

  it("без прозрачной ТУТ в составе сборка остановлена с подсказкой", () => {
    const bare: GenData = { ...D, components: components.filter((item) => item.id !== "c") };
    const S0 = initState(bare);
    const S = withCoax(S0, coaxSerialPatch(bare, S0.coax, true));
    expect(makeGen(bare, S).validate().error).toMatch(/нет прозрачной ТУТ для защиты этикеток/);
    const tube = withCoax(S, { markMaterial: "tube" });
    expect(makeGen(bare, tube).validate().error).toBe(
      "В составе нет прозрачной ТУТ для защиты маркировочной трубки — добавьте её в состав или выключите «Прозрачная ТУТ поверх».");
  });

  it("шаблон строится по текущему обозначению: КБ1 → КБ2 после перенумерации, своё обозначение — как набрано", () => {
    const S = serialOn();
    const renumbered: GenData = { ...D, itemIndex: "КБ2", assemblyInfo: { ...D.assemblyInfo, "Индекс": "КБ2" } };
    const config = makeGen(renumbered, S).buildConfig() as Record<string, any>;
    expect(config.coax.serial).toMatchObject({ template: "FRX.831К.КБ2.{№}", designation: "КБ2", productKind: "kb" });
    expect(config.ops.find((op: any) => op.opKey === "coaxPrintLabel").marks[0].text).toBe("FRX.831К.КБ2.{№}");
    for (const own of ["34.226", "120907/AA"]) {
      const custom = withCoax(S, { serialProduct: own });
      const cfgOwn = makeGen(renumbered, custom).buildConfig() as Record<string, any>;
      expect(cfgOwn.coax.serial).toMatchObject({ template: `FRX.831К.${own}.{№}`, designation: own, productKind: "custom" });
      expect(cfgOwn.ops.find((op: any) => op.opKey === "coaxPrintLabel").marks[0].text).toBe(`FRX.831К.${own}.{№}`);
    }
  });

  it("с номером прежние операции карты сохраняют имя карты, своё название — только у добавленных", () => {
    const labelled = Object.fromEntries((cfg(serialOn({ labelProfile: profile })).ops as any[]).map((op) => [op.opKey, op.label]));
    for (const key of ["coaxCutStrip", "coaxCutTut", "coaxPinA", "coaxInsTut", "coaxInsSleeve", "coaxHousingA", "coaxShield", "coaxHeatTut", "coaxVswr"])
      expect(labelled[key], key).toBeUndefined();
    expect(labelled).toMatchObject({ coaxPrintLabel: "Печать этикетки заводского номера", coaxProtectCut: "Резка прозрачной ТУТ",
      coaxInsLabel: "Наклейка этикеток маркировки", coaxHeat: "Усадка прозрачной ТУТ на этикетках" });
  });

  it("конфиг генератора совпадает с фикстурой, по которой собирает ЭТК тест бэкенда", () => {
    expect(cfg(serialOn({ labelProfile: profile, vswrPassport: true }))).toEqual(fixture.tape);
    expect(cfg(serialOn({ markMaterial: "tube", markTubeLen: "25" }))).toEqual(fixture.tube);
  });

  it("прозрачная ТУТ в составе: «прозрачная», «бесцветная», «clear», «transparent» в любом регистре", () => {
    for (const name of ["ТУТ 6/3 ПРОЗРАЧНАЯ", "ТУТ 6/3мм, бесцветная", "Heat shrink 6/3 Clear", "Heat shrink 6/3 TRANSPARENT"])
      expect(coaxMarkDefaults([{ type: "ТУТ", name, art: name }]).protect?.name, name).toBe(name);
    expect(coaxMarkDefaults([{ type: "ТУТ", name: "ТУТ 6/3 чёрная", art: "x" }]).protect).toBeUndefined();
    const colourless: GenData = { ...D, components: components.map((item) => item.id === "c" ? { ...item, name: "ТУТ 6/3 бесцветная", art: "ТУТ 6/3 бесцветная" } : item) };
    const S0 = initState(colourless);
    const ops = makeGen(colourless, withCoax(S0, coaxSerialPatch(colourless, S0.coax, true))).getActiveOps();
    expect(ops.find((op) => op.opKey === "coaxCutTut")?.shrinkMaterials).toEqual([{ art: "ТУТ 6/3 чёрная", name: "ТУТ 6/3 чёрная" }]);
  });

  it("без номера обычный коакс собирается как раньше", () => {
    expect(keys(initState(D))).toEqual(["coaxCutStrip", "coaxCutTut", "coaxPinA", "coaxPinB", "coaxInsTut", "coaxInsSleeve",
      "coaxHousingA", "coaxHousingB", "coaxShield", "coaxHeat", "coaxVswr"]);
  });
});

describe("паспорт по ВАЦ на каждое изделие (владелец 27.11)", () => {
  it("флажок даёт сплошной контроль с паспортом в конфиге и в операции, и возвращается из собранной карты", () => {
    const S = withCoax(initState(D), { vswrMode: "sample", vswrPassport: true });
    const vswr = makeGen(D, S).getActiveOps().find((op) => op.type === "coaxVswr");
    expect(vswr).toMatchObject({ label: "Контроль КСВН", vswrControl: { mode: "all", every: null, passport: true } });
    const config = cfg(S);
    expect(config.coax.vswrControl).toEqual({ mode: "all", every: null, passport: true });
    expect(config.ops.at(-1)).toMatchObject({ type: "coaxVswr", vswrControl: { passport: true } });
    const back = withCoaxVswrFromConfig(initState(D), config);
    expect(back.coax).toMatchObject({ vswrMode: "all", vswrPassport: true });
    expect(withCoaxVswrFromConfig(initState(D), { coax: { vswrControl: { mode: "all", every: null } } }).coax.vswrPassport).toBe(false);
  });
});
