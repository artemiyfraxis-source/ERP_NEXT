import { describe, expect, it } from "vitest";
import { drawingArea, frameRect, placeOnSheet, sheetFormat, smallestSheetFor, titleBlockRect, TITLE_BLOCK_HEIGHT_MM } from "./sheet";

describe("лист чертежа", () => {
  it("форматы альбомные, поле чертежа лежит над основной надписью внутри рамки", () => {
    const a3 = sheetFormat("A3");
    expect([a3.widthMm, a3.heightMm]).toEqual([420, 297]);
    const frame = frameRect(a3), area = drawingArea(a3), block = titleBlockRect(a3);
    expect(block.height).toBe(TITLE_BLOCK_HEIGHT_MM);
    expect(block.y + block.height).toBe(frame.y + frame.height);
    expect(area.y + area.height).toBeLessThan(block.y);
    expect(area.x).toBeGreaterThan(frame.x);
  });

  it("«по листу» вписывает чертёж в поле по центру и считает масштаб изделия", () => {
    const placement = placeOnSheet({ width: 800, height: 200 }, "A3", { kind: "fit" }, { denominator: 2 });
    if (!placement.ok) throw new Error("должно помещаться");
    const area = drawingArea(placement.sheet);
    expect(placement.box.width).toBeCloseTo(area.width, 6);
    expect(placement.box.height).toBeLessThanOrEqual(area.height);
    expect(placement.mmPerUnit).toBeCloseTo(area.width / 800, 9);
    expect(placement.box.x + placement.box.width / 2).toBeCloseTo(area.x + area.width / 2, 6);
    // 1 единица = N мм изделия, на бумаге s мм: масштаб М 1:(N / s).
    expect(placement.denominator).toBeCloseTo(2 / placement.mmPerUnit, 9);
    expect(placement.scaleLabel).toMatch(/^по листу, около М 1:/);
  });

  it("«по листу» без реальных размеров не выдумывает масштаб", () => {
    const placement = placeOnSheet({ width: 100, height: 100 }, "A4", { kind: "fit" }, null);
    expect(placement).toMatchObject({ ok: true, denominator: null, scaleLabel: "по листу" });
  });

  it("в масштабе: 1 мм бумаги на единицу чертежа, подпись М 1:N", () => {
    const placement = placeOnSheet({ width: 300, height: 150 }, "A3", { kind: "true" }, { denominator: 5 });
    expect(placement).toMatchObject({ ok: true, mmPerUnit: 1, denominator: 5, scaleLabel: "М 1:5" });
    if (placement.ok) expect(placement.box).toMatchObject({ width: 300, height: 150 });
  });

  it("в масштабе не помещается: отказ с подсказкой и наименьшим подходящим листом", () => {
    const placement = placeOnSheet({ width: 380, height: 200 }, "A4", { kind: "true" }, { denominator: 4 });
    expect(placement.ok).toBe(false);
    if (placement.ok) return;
    expect(placement.suggestion).toBe("A3");
    expect(placement.hint).toContain("не помещается на А4");
    expect(placement.hint).toContain("Подойдёт А3");
    expect(placement.hint).toContain("М 1:4");
  });

  it("не помещается даже на А1: предлагает «По листу» и другой знаменатель", () => {
    const placement = placeOnSheet({ width: 2000, height: 100 }, "A1", { kind: "true" }, { denominator: 1 });
    expect(placement).toMatchObject({ ok: false, suggestion: null });
    if (!placement.ok) expect(placement.hint).toMatch(/даже на А1.*По листу/);
  });

  it("в масштабе без реальных размеров: отказ с подсказкой, где их включить", () => {
    const placement = placeOnSheet({ width: 10, height: 10 }, "A4", { kind: "true" }, null);
    expect(placement.ok).toBe(false);
    if (!placement.ok) expect(placement.hint).toContain("Реальных размерах");
  });

  it("наименьший лист ищется от А4 к А1", () => {
    expect(smallestSheetFor(200, 100)?.id).toBe("A4");
    expect(smallestSheetFor(380, 200)?.id).toBe("A3");
    expect(smallestSheetFor(5000, 5000)).toBeNull();
  });
});
