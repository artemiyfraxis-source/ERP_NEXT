import { describe, expect, it } from "vitest";
import { etkDrawPinout, etkFigure, type EtkHousing } from "./etkDraw";
import { pinCoreColor, withCoreColors } from "./etkCoreColors";

// Карта выданной ТК (КБ1): кабель UL2527 28AWG на 4 жилы, цвета заданы в генераторе, разъём 5 позиций, 4 использованы.
// Номер проекта и артикулы обезличены, как в прежних фикстурах.
const housing: EtkHousing = { title: "Connfly DS1071", art: "I-DS1071-SCB01X5", rows: 1, cols: 5, n: 5, kind: "grid", color: "#efe6cf", latch: "ramp",
  cavity: "square", labels: null, grid: [[1, 2, 3, 4, 5]], round: null, conf: "high", note: "", pitch: 2.54 };
const wire = { art: "UL2527 28AWG", name: "UL2527 28AWG", qty: 1, length: 420, pins: [1, 2, 3, 4] };
const links = [1, 2, 3, 4].map((pin) => ({ pin, wire: "UL2527 28AWG", dir: "to", peer: { end: "В1", pin, conn: "I-DS1071-SCB01X5", label: "" } }));
const document = (config: Record<string, unknown> = {}) => ({
  build_config: {
    wires: [{ art: "UL2527 28AWG", name: "UL2527 28AWG", qty: 1, cores: 4, length: 420, coreColors: ["красный", "белый", "зелёный", "чёрный"] }],
    ...config,
  },
  operations: [{ type: "insTerm", housing, fields: { wires: [wire], links, pinout: true } }],
});
const fills = (svg: string) => [...svg.matchAll(/fill="(#[0-9a-f]{6})"/gi)].map((m) => m[1].toLowerCase());

describe("цвета жил многожильного кабеля в монтаже (545К.9)", () => {
  it("без цветов в карточке эскиз серый — как было", () => {
    const svg = etkFigure(document().operations[0] as never);
    expect(svg).toContain("конт. 1 — UL2527 28AWG");
    expect(fills(svg)).toContain("#6b7c86");
  });

  it("жилы красят гнёзда на торце и квадраты легенды, в легенде есть название цвета", () => {
    const [op] = withCoreColors(document().operations, document());
    const svg = etkFigure(op as never);
    ["#d93a32", "#ffffff", "#2e9e4f", "#222222"].forEach((hex) => expect(fills(svg)).toContain(hex));
    expect(svg).toContain("конт. 1 — UL2527 28AWG, красный");
    expect(svg).toContain("конт. 4 — UL2527 28AWG, чёрный");
    expect(svg).not.toContain("#6b7c86");
    // Квадрат легенды — 12×12, по одному на контакт, каждый в цвете своей жилы.
    const legend = [...svg.matchAll(/<rect x="\d+" y="[\d.]+" width="12" height="12" fill="(#[0-9a-f]{6})"/gi)].map((m) => m[1].toLowerCase());
    expect(legend).toEqual(["#d93a32", "#ffffff", "#2e9e4f", "#222222"]);
  });

  it("свои цвета (colorHex) красят по hex", () => {
    const doc = document({ wires: [{ art: "UL2527 28AWG", name: "UL2527 28AWG", qty: 1, cores: 4, coreColors: ["болотный", "белый", "", "чёрный"] }], colorHex: { болотный: "#6b7a3a" } });
    const [op] = withCoreColors(doc.operations, doc);
    const wires = (op.fields as { wires: Array<Record<string, unknown>> }).wires;
    expect(pinCoreColor(wires[0], 1)).toBe("#6b7a3a");
    expect(pinCoreColor(wires[0], 3)).toBe("");
    expect(fills(etkDrawPinout(op as never))).toContain("#6b7a3a");
  });

  it("цвета по распиновке (pinout.cores), если у кабеля нет coreColors", () => {
    const doc = document({ wires: [{ art: "UL2527 28AWG", name: "UL2527 28AWG", qty: 1, cores: 4 }],
      pinout: { cores: [{ pos: 0, unit: 0, color: "красный" }, { pos: 0, unit: 1, color: "белый" }] } });
    const [op] = withCoreColors(doc.operations, doc);
    const wires = (op.fields as { wires: Array<Record<string, unknown>> }).wires;
    expect([1, 2, 3, 4].map((pin) => pinCoreColor(wires[0], pin))).toEqual(["#d93a32", "#ffffff", "", ""]);
  });

  it("одиночные провода без цветов жил остаются как были, операция — тем же объектом", () => {
    const doc = { build_config: { wires: [{ art: "красный", qty: 1, cores: 1 }] }, operations: [{ type: "insTerm", housing, fields: { wires: [{ art: "красный", pins: [1] }] } }] };
    expect(withCoreColors(doc.operations, doc)[0]).toBe(doc.operations[0]);
    expect(etkDrawPinout(doc.operations[0] as never)).toContain("конт. 1 — красный");
  });
});
