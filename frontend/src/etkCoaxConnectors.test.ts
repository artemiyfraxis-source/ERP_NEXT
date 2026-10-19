// @vitest-environment node
import { describe, expect, it } from "vitest";
import fixture from "./fixtures/coaxSmaRg316Rev1.json";
import { coaxEndNames, coaxEndTitle, etkFigure, etkParams } from "./etkDraw";
import { etkPfArt } from "./etkPfArt";
import { etkPfGroups } from "./etkRoute";

/*
 * КБ1 «SMA-Male to SMA-Female RG316 275mm» (прод, ревизия 1, 19.10.2026): какая зачистка к какому разъёму и картинки ПФ
 * на размер карточки схемы (владелец 19.10).
 */
const document = fixture.document as Record<string, unknown>;
const operations = document.operations as Array<Record<string, unknown>>;
const groups = etkPfGroups(document.route as never, operations as never);
const art = (name: string) => {
  const group = groups.find((item) => item.name.startsWith(name))!;
  const result = etkPfArt({ group, operations: group.ops.map((position) => operations[position - 1]), inputs: [], document, batch: 2000 });
  return result?.main && "svg" in result.main ? result.main.svg : "";
};
const sizes = (svg: string) => [...svg.matchAll(/font-size="([\d.]+)"/g)].map((match) => Number(match[1]));
const texts = (svg: string) => [...svg.matchAll(/<text[^>]*>([^<]*)<\/text>/g)].map((match) => match[1]);

describe("разделка коакса: каждая сторона названа разъёмом", () => {
  const sketch = etkFigure(operations[0]);

  it("заголовок над рисунком каждой стороны: сторона, разъём (вилка/розетка) и артикул", () => {
    expect(sketch).toContain("Сторона А — SMA-Male (вилка), SMA-C174P");
    expect(sketch).toContain("Сторона В — SMA-Female bulkhead (розетка), SMA-C316F");
    const titles = [...sketch.matchAll(/data-coax-title/g)];
    expect(titles).toHaveLength(2);
    // Заголовок идёт раньше своего рисунка и крупный (не подпись сбоку).
    expect(sketch.indexOf("Сторона А —")).toBeLessThan(sketch.indexOf('data-band="jacket"'));
    expect(sketch.indexOf("Сторона В —")).toBeLessThan(sketch.lastIndexOf('data-band="jacket"'));
    expect(sketch).toContain('data-coax-title x="40" y="22" font-size="20"');
  });

  it("в шапке операции у каждой стороны — разъём и ступени L1–L3", () => {
    const { sides } = etkParams(operations[0]);
    const find = (end: string, label: string) => sides.find((side) => side.end === end)?.items.find((item) => item[0] === label)?.[1];
    expect(find("A", "Разъём")).toBe("SMA-Male (вилка), SMA-C174P");
    expect(find("B", "Разъём")).toBe("SMA-Female bulkhead (розетка), SMA-C316F");
    expect(find("A", "Разделка")).toBe("L1 2,5 · L2 3,5 · L3 7,5 мм");
    expect(find("B", "Разделка")).toBe("L1 2,5 · L2 4,8 · L3 10 мм");
  });

  it("выданный ЭТК без готовых имён: половина считается по типу из БД.КОАКС", () => {
    expect(coaxEndNames({ type: "SMA-male" })).toEqual({ kind: "SMA-Male", word: "вилка" });
    expect(coaxEndNames({ type: "MCX-female R/A" })).toEqual({ kind: "MCX-Female R/A", word: "розетка" });
    expect(coaxEndNames({ type: "MHF1-plug" })).toEqual({ kind: "MHF1-plug", word: "" });
    expect(coaxEndTitle("Сторона А", { connector: "SMA-C174P" })).toBe("Сторона А — SMA-C174P");
  });

  it("эскиз пайки контакта подписан разъёмом конца", () => {
    expect(etkFigure(operations[2])).toContain("Сторона А — SMA-Male (вилка), SMA-C174P");
  });
});

describe("ПФ коаксиальной сборки: картинка на размер карточки", () => {
  it("ПФ «Кабель с центральным контактом»: только конец кабеля и подпись «сторона … · под …»", () => {
    const a = art("Кабель с центральным контактом, сторона А"), b = art("Кабель с центральным контактом, сторона В");
    expect(texts(a)).toContain("сторона А · под SMA-Male");
    expect(texts(b)).toContain("сторона В · под SMA-Female bulkhead");
    ["оплётка", "диэлектрик", "контакт, припой", "RG316"].forEach((name) => expect(texts(a)).toContain(name));
    expect(a).toContain('fill="url(#pfBraid)"');
    expect(Math.min(...sizes(a))).toBeGreaterThanOrEqual(10);
    expect(a).not.toContain("L1 =");
  });

  it("ПФ «Сторона … собрана»: тот же конец с ТУТ, втулкой экрана и корпусом — гайка у вилки, резьба у розетки", () => {
    const a = art("Сторона А собрана"), b = art("Сторона В собрана");
    expect(texts(a)).toEqual(expect.arrayContaining(["сторона А · под SMA-Male", "накидная гайка", "втулка экрана", "ТУТ 4/2"]));
    expect(texts(b)).toEqual(expect.arrayContaining(["сторона В · под SMA-Female bulkhead", "корпус с резьбой", "втулка экрана", "ТУТ 4/2"]));
    expect(Math.min(...sizes(a), ...sizes(b))).toBeGreaterThanOrEqual(10);
  });

  it("изделие: оба разъёма, ТУТ на обоих концах и размер 275 мм; без заглушки «Раз…»", () => {
    const svg = art("Кабельная сборка");
    expect(texts(svg)).toEqual(expect.arrayContaining(["SMA-Male (вилка)", "SMA-Female bulkhead (розетка)", "L = 275 мм"]));
    expect(texts(svg).filter((text) => text === "ТУТ")).toHaveLength(2);
    expect(svg).not.toMatch(/Раз|Разъ/);
    expect(svg).not.toContain('data-part="connector"');
    expect(Math.min(...sizes(svg))).toBeGreaterThanOrEqual(10);
  });

  it("резка кабеля и трубок рисуются как раньше", () => {
    expect(art("Нарезанные трубки")).toContain("ТУТ 4/2мм, черная");
    expect(art("Нарезанный и разделанный кабель")).toContain("Сторона А — SMA-Male (вилка), SMA-C174P");
  });
});

describe("эскизы операций «Надеть» и «Монтаж корпуса» (владелец 19.10)", () => {
  const tube = operations.find((operation) => operation.type === "coaxInsTut")!;
  const sleeve = operations.find((operation) => operation.type === "coaxInsSleeve")!;
  const housings = operations.filter((operation) => operation.type === "coaxHousing");
  const texts = (svg: string) => [...svg.matchAll(/<text[^>]*>([^<]*)<\/text>/g)].map((match) => match[1]);
  const sizes = (svg: string) => [...svg.matchAll(/font-size="([\d.]+)"/g)].map((match) => Number(match[1]));

  it.each([["ТУТ", tube, "tube", "Надеть: ТУТ 4/2мм, черная × 2"], ["втулка", sleeve, "sleeve", "Надеть: втулка экрана × 2"]])(
    "%s: оба конца со своими разъёмами, знак разрыва, стрелка со свободного конца", (_name, operation, part, title) => {
      const svg = etkFigure(operation);
      expect(texts(svg)).toEqual(expect.arrayContaining([title, "Сторона А — SMA-Male (вилка)", "Сторона В — SMA-Female bulkhead (розетка)",
        "контакт, припой", "оплётка", "диэлектрик", "надвинуть со свободного конца"]));
      expect(svg.match(/data-part="break"/g)).toHaveLength(2);
      expect(svg.match(new RegExp(`data-part="${part}"`, "g"))).toHaveLength(2);
      expect(texts(svg).filter((text) => text === "отодвинуть от торца оболочки")).toHaveLength(2);
      expect(Math.min(...sizes(svg))).toBeGreaterThanOrEqual(12);
    });

  it("расстояние от торца оболочки — числом, когда оно есть в данных", () => {
    const withOffset = { ...tube, fields: { ...(tube.fields as object), coaxParts: { ...(tube.fields as { coaxParts: object }).coaxParts, offset: "15" } } };
    expect(texts(etkFigure(withOffset))).toContain("отодвинуть на 15 мм от торца оболочки");
  });

  it("без данных о концах (выданный до 19.10 ЭТК без чтения) — прежний эскиз, а не пустота", () => {
    const old = { ...tube, fields: { coaxParts: { ...(tube.fields as { coaxParts: object }).coaxParts, ends: [] } } };
    expect(etkFigure(old)).toContain("до монтажа корпусов, отодвинуть от концов");
  });

  it("монтаж корпуса: у вилки накидная гайка, у розетки резьбовой корпус; контакт в изоляторе, оплётка по хвостовику", () => {
    const [a, b] = housings.map((operation) => etkFigure(operation));
    expect(a).toContain('data-part="male-body"');
    expect(b).toContain('data-part="female-body"');
    expect(a).not.toContain("female-body");
    expect(texts(a)).toEqual(expect.arrayContaining(["Сторона А — SMA-Male (вилка), SMA-C174P", "накидная гайка", "изолятор", "контакт входит в изолятор",
      "Корпус надвинуть до упора: контакт зафиксирован в изоляторе", "оплётка расправлена по хвостовику,", "внутрь не завёрнута"]));
    expect(texts(b)).toEqual(expect.arrayContaining(["Сторона В — SMA-Female bulkhead (розетка), SMA-C316F", "резьбовой корпус", "изолятор"]));
    expect(Math.min(...sizes(a), ...sizes(b))).toBeGreaterThanOrEqual(12);
  });

  it("заглубление контакта: размер со стороны корпуса и увеличенный вид А; нет значения — ничего не рисуется (угловые без значения)", () => {
    const withDepth = (operation: Record<string, unknown>, depth: string) => {
      const fields = operation.fields as { coaxEnd: object };
      return etkFigure({ ...operation, fields: { ...fields, coaxEnd: { ...fields.coaxEnd, contactDepth: depth } } });
    };
    const [a, b] = housings;
    expect(texts(withDepth(a, "0,38…1,14"))).toEqual(expect.arrayContaining(["заглубление контакта 0,38…1,14 мм", "А (10:1)", "вид А увеличен, не в масштабе"]));
    expect(texts(withDepth(b, "1,88…2,41"))).toContain("заглубление контакта 1,88…2,41 мм");
    expect(withDepth(a, "0,38-1,14")).toContain("заглубление контакта 0,38…1,14 мм");
    for (const svg of [etkFigure(a), etkFigure(b), withDepth(a, ""), withDepth(a, "по КД")]) expect(svg).not.toContain("заглубление");
    expect(Math.min(...sizes(withDepth(a, "0,38…1,14")))).toBeGreaterThanOrEqual(12);
    expect(withDepth(a, "0,38…1,14")).not.toMatch(/undefined|NaN/);
  });
});
