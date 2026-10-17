import { describe, expect, it } from "vitest";
import { etkPfArt } from "./etkPfArt";
import type { EtkPfGroup } from "./etkRoute";

function group(extra: Partial<EtkPfGroup> = {}): EtkPfGroup {
  return { key: "b", number: 1, label: "2", name: "ПФ", final: false, ops: [1], components: [], inputs: [], output: [], minutes: 0, ...extra };
}

const cut = { position: 1, type: "cutWire", code: "CUT_WIRE_auto", fields: { wires: [{ name: "ПВАМ 0,5 черный", art: "ПВАМ 0,5 черный", qty: 2, length: 300 }] } };
const semis = [{ name: "Провода нарезаны: ПВАМ 0,5 черный — 1 шт по 1000 мм; ПВАМ 0,5 красный — 1 шт по 980 мм", norm: "10", unit: "шт" }];

describe("etkPfArt — эскиз полуфабриката в схеме ПФ", () => {
  it("draws the cut wires of a cutting ПФ with the drawing of the operation", () => {
    const art = etkPfArt({ group: group(), operations: [cut], document: {} })!;
    expect(art.main && "svg" in art.main ? art.main.svg : "").toContain("ПВАМ 0,5 черный × 2");
    expect(art.layers).toBe("");
  });

  it("draws a splice group as wires joined under the tube of its input, with the count from the harness scheme", () => {
    const ops = [
      { position: 3, type: "insTut", code: "INS_TUT", semis },
      { position: 4, type: "splice", code: "US_WELD", semis },
      { position: 5, type: "heatTut", code: "HEAT_TTK_short", semis },
    ];
    const art = etkPfArt({ group: group({ ops: [3, 4, 5] }), operations: ops, inputs: ["ПВАМ 0,5 черный ×3", "ТТК 4,8/1,6 ×3"],
      document: { topology: { nodes: [{ id: "s", conn: "Сростки ×3", kind: "splice" }] } } })!;
    const svg = art.main && "svg" in art.main ? art.main.svg : "";
    expect(svg).toContain("сросток ×3");
    expect(svg).toContain("под ТТК 4,8/1,6");
    expect(svg).toContain("#d7322d"); // красный провод входа
    // Трубки сростка уже на рисунке — слоями на пучке не повторяются.
    expect(art.layers).toBe("");
  });

  it("puts braid, tape and marking on a bundle strip with the material and its length per piece", () => {
    const ops = [
      { position: 9, type: "insWrap", code: "INS_WRAP", components: [{ art: "Изолента Temflex 1300, черная", norm: "4,45", unit: "М." }] },
      { position: 10, type: "insWrap", code: "INS_BRAID", components: [{ art: "Оплетка XS-3", norm: "10", unit: "М." }] },
      { position: 12, type: "insMark", code: "INS_MARK_HARNESS", fields: { marks: [{ target: "Разъём X1", text: "X1", qty: "1" }] } },
    ];
    const art = etkPfArt({ group: group({ ops: [9, 10, 12] }), operations: ops, document: {}, batch: 10 })!;
    expect(art.main).toBeNull();
    expect(art.layers).toContain("Изолента Temflex 1300");
    expect(art.layers).toContain("445 мм");
    expect(art.layers).toContain("Оплетка XS-3");
    expect(art.layers).toContain(">1 м<");
    expect(art.layers).toContain("url(#pfBraid)");
    // Метка — белой трубкой с надписью по КД.
    expect(art.layers).toContain(">X1<");
  });

  it("draws the product ПФ as the harness scheme and falls back to the technologist's picture when nothing is drawn", () => {
    const topology = { nodes: [{ id: "A", conn: "X1", side: "A", depth: 0 }, { id: "B", conn: "X2", side: "B", depth: 1 }],
      edges: [{ from: "A", to: "B", wires: ["W1"], count: 1, length: 300 }] };
    const final = etkPfArt({ group: group({ final: true }), operations: [{ position: 1, type: "insWrap", code: "INS_WRAP" }], document: { topology } })!;
    expect(final.main && "svg" in final.main ? final.main.svg : "").toContain("X2");
    const plain = { position: 1, type: "other", code: "OTHER" };
    expect(etkPfArt({ group: group(), operations: [plain], document: {}, image: () => "/sketch.png" })?.main).toEqual({ image: "/sketch.png" });
    expect(etkPfArt({ group: group(), operations: [plain], document: {} })).toBeNull();
  });
});
