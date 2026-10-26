// @vitest-environment node
import { describe, expect, it } from "vitest";
import { cableColorStem, cableMatchKey, matchCableRec, type CableMatchRec } from "./cableMatch";
import { cableRecFromFields } from "./tcGenLogic";

// Строки БД.ПРОВ как их видит генератор: монтажные — строка на цвет (Марка + Цвет), многожильные — одна строка.
const rec = (mark: string, color = "", supplierCodes: string[] = []): CableMatchRec & { id: string } => ({ id: `${mark}|${color}`, mark, color, supplierCodes });
const BASE = [
  rec("НВ-4 0,20", "красный"), rec("НВ-4 0,20", "чёрный"), rec("НВ-4 0,50", "красный"),
  rec("ПВАМ 0,50", "красный"), rec("ПВАМ 0,50", "чёрный"),
  rec("Силиконовый провод 26AWG", "синий"), rec("Силиконовый провод 26AWG", "красный"), rec("Силиконовый провод 28AWG", "белый"),
  rec("UL3135 22AWG", "красный"), rec("UL3135 22AWG", "жёлтый"),
  rec("LiFY 0,14"), rec("ПВВС 4×0,35"), rec("CCCU-4G"), rec("U/UTP 2x2x0,48"),
];
const find = (...texts: string[]) => matchCableRec(BASE, texts)?.id ?? null;

describe("ключ марки", () => {
  it("дефис после букв серии, добивка нулями, «мм2/мм²», ё/е, цвет и слово «провод» не мешают", () => {
    expect(cableMatchKey("НВ4 0,2 красный")).toBe(cableMatchKey("НВ-4 0,20"));
    expect(cableMatchKey("НВ4-0,2мм2, черный")).toBe(cableMatchKey("НВ-4 0,20"));
    expect(cableMatchKey("НВ4 0,2 мм²")).toBe(cableMatchKey("НВ-4 0,20"));
    expect(cableMatchKey("ПВАМ 0,5 черный")).toBe(cableMatchKey("ПВАМ 0,50"));
    expect(cableMatchKey("Силикон 28AWG белый")).toBe(cableMatchKey("Силиконовый провод 28AWG"));
    expect(cableMatchKey("ПВВС 4x0,35")).toBe(cableMatchKey("ПВВС 4×0,35"));
    expect(cableMatchKey("ПВВС 4х0,35")).toBe(cableMatchKey("ПВВС 4×0,35")); // кириллическая «х»
  });
  it("разные сечения и марки остаются разными", () => {
    expect(cableMatchKey("НВ-4 0,5")).not.toBe(cableMatchKey("НВ-4 0,35"));
    expect(cableMatchKey("ПВАМ 0,5")).not.toBe(cableMatchKey("ПВАМ 0,75"));
  });
  it("«коммуникационный», «серия» и прочие слова с «мм»/«сер» не портятся", () => {
    expect(cableMatchKey("коммуникационный")).toBe("коммуникационный");
    expect(cableMatchKey("серия 5")).toBe("серия5");
  });
  it("цвет — основа слова", () => {
    expect(cableColorStem("НВ4-0,2мм2, черный")).toBe("черн");
    expect(cableColorStem("чёрный")).toBe("черн");
    expect(cableColorStem("жёлтый")).toBe("желт");
    expect(cableColorStem("НВ-4 0,20")).toBe("");
  });
});

describe("запись БД.ПРОВ по позиции состава", () => {
  it("НВ4 0,2 красный / НВ4-0,2мм2, черный → «НВ-4 0,20» нужного цвета", () => {
    expect(find("НВ4 0,2 красный")).toBe("НВ-4 0,20|красный");
    expect(find("НВ4-0,2мм2, черный")).toBe("НВ-4 0,20|чёрный");
    expect(find("НВ4 0,5 красный")).toBe("НВ-4 0,50|красный");
  });
  it("ПВАМ 0,5 черный → «ПВАМ 0,50» чёрный", () => {
    expect(find("ПВАМ 0,5 черный")).toBe("ПВАМ 0,50|чёрный");
  });
  it("«Силиконовый 26AWG синий», «Силикон 28AWG белый» → «Силиконовый провод …» с цветом", () => {
    expect(find("Силиконовый 26AWG синий")).toBe("Силиконовый провод 26AWG|синий");
    expect(find("Силикон 28AWG белый")).toBe("Силиконовый провод 28AWG|белый");
  });
  it("«Силиконовый 22AWG красный» — алиас на UL3135 22AWG (того же цвета)", () => {
    expect(find("Силиконовый 22AWG красный")).toBe("UL3135 22AWG|красный");
    expect(find("Силикон 22AWG жёлтый")).toBe("UL3135 22AWG|жёлтый");
  });
  it("«Helukabel 15201» — алиас на LiFY 0,14", () => {
    expect(find("Helukabel 15201")).toBe("LiFY 0,14|");
  });
  it("артикул поставщика — запасное имя: находит строку, у которой он записан", () => {
    const base = [...BASE, rec("Кабель Х 3x0,5", "", ["778899"])];
    expect(matchCableRec(base, ["778899"])?.mark).toBe("Кабель Х 3x0,5");
    expect(matchCableRec(base, ["778"])).toBeNull(); // короче 4 знаков и без совпадения
  });
  it("марка из состава с «×» и «x»; марка точно как в базе — как раньше", () => {
    expect(find("ПВВС 4x0,35")).toBe("ПВВС 4×0,35|");
    expect(find("CCCU-4G")).toBe("CCCU-4G|");
    expect(find("U/UTP 2x2x0,48", "Кабель ParLan Patch U/UTP Cat5e 2x2x0,48")).toBe("U/UTP 2x2x0,48|");
  });
  it("марка внутри длинного наименования (самая длинная из подходящих)", () => {
    expect(find("Кабель CCCU-4G серый 28AWG")).toBe("CCCU-4G|");
  });
  it("нет записи — null, пустая база — null", () => {
    expect(find("НВ-9 9,9 красный")).toBeNull();
    expect(matchCableRec([], ["CCCU-4G"])).toBeNull();
  });
  it("цвета у позиции нет — первая запись марки; цвета в базе нет — она же", () => {
    expect(find("НВ4 0,2")).toBe("НВ-4 0,20|красный");
    expect(find("Силиконовый 22AWG зелёный")).toBe("UL3135 22AWG|красный");
  });
});

describe("cableRecFromFields читает Цвет и Артикул поставщика", () => {
  it("цвет строки и коды поставщика (префикс «сайт: » отбрасывается)", () => {
    const r = cableRecFromFields({ "Марка": "НВ-4 0,20", "Цвет": "красный", "Артикул поставщика": "gsm-repiteri.ru: 3449; 5521" }, "wire");
    expect(r).toMatchObject({ kind: "wire", color: "красный", supplierCodes: ["3449", "5521"] });
  });
});
