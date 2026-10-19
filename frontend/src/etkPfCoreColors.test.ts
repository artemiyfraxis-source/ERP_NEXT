import { describe, expect, it } from "vitest";
import { etkPfArt } from "./etkPfArt";
import type { EtkPfGroup } from "./etkRoute";

// Карта выданной ТК (КБ1), как в etkCoreColors.test.ts: кабель UL2527 28AWG на 4 жилы, цвета заданы в генераторе, разъём 5
// позиций. Номер проекта и артикулы обезличены; обозначения разъёмов X1/X2 поставлены, чтобы корпус нашёлся по метке.
const group = (extra: Partial<EtkPfGroup> = {}): EtkPfGroup =>
  ({ key: "b", number: 1, label: "2", name: "ПФ", final: false, ops: [1], components: [], inputs: [], output: [], minutes: 0, ...extra });
const housing = { title: "Connfly DS1071", art: "I-DS1071-SCB01X5", rows: 1, cols: 5, n: 5, kind: "grid", color: "#efe6cf", latch: "ramp",
  cavity: "square", labels: null, grid: [[1, 2, 3, 4, 5]], round: null, conf: "high", note: "", pitch: 2.54 };
const NAMES = ["красный", "белый", "зелёный", "чёрный"];
const HEX = ["#d93a32", "#ffffff", "#2e9e4f", "#222222"];
const GREY = "#6b7c86";
const cable = { art: "UL2527 28AWG", name: "UL2527 28AWG", qty: 1, cores: 4, length: 420, coreColors: NAMES };
const mount = (side: "A" | "B", label: string) => ({ ep: { label, connArt: "I-DS1071-SCB01X5" }, side, type: "insTerm", epKey: `${side === "A" ? "А" : "В"}1`, pins: ["1, 2, 3, 4"], wires: [cable] });
const real = (side: "A" | "B", position: number) => ({ position, type: "insTerm", code: "INS_terminal_2,54-6,3", side, connector_article: "I-DS1071-SCB01X5", housing,
  fields: { wires: [{ art: "UL2527 28AWG", name: "UL2527 28AWG", qty: 1, length: 420, pins: [1, 2, 3, 4] }], pinout: true } });
const svgOf = (art: ReturnType<typeof etkPfArt>) => (art?.main && "svg" in art.main ? art.main.svg : "");
const count = (svg: string, hex: string) => svg.split(hex).length - 1;

describe("ПФ: цвета жил многожильного кабеля (545К.9)", () => {
  it("ветвь: жилы веером и контакты вида сверху — в цветах жил", () => {
    const operations = [real("A", 1), { position: 2, type: "insTut", code: "INS_TUT", components: [] }];
    const config = { wires: [cable], ops: [{ ...mount("A", "X1"), branch: "X1" }, { type: "insTut", tutArt: "ТТК 12/4", label: "Надеть ТТК X1 — ТТК 12/4" }] };
    const svg = svgOf(etkPfArt({ group: group({ ops: [1, 2] }), operations, document: { operations, build_config: config } }));
    expect(svg).toContain('data-part="connector"');
    HEX.forEach((hex) => expect(count(svg, hex)).toBeGreaterThanOrEqual(2));
    expect(svg).not.toContain(GREY);
  });

  it("ПФ «ТУТ на месте Б»: провода под трубкой и контакты разъёмов — по жилам", () => {
    const ops = [mount("A", "X1"), mount("B", "X2"), { type: "insTut", harness: true, tutArt: "ТТК 12/4", label: "Надеть ТТК" }];
    const operations = [real("A", 1), real("B", 2), { position: 3, type: "insTut", code: "INS_TUT", components: [] }];
    const svg = svgOf(etkPfArt({ group: group({ ops: [3] }), operations: [operations[2]], document: { operations, build_config: { wires: [cable], ops } } }));
    expect(svg).toContain('data-part="harness-tube"');
    HEX.forEach((hex) => expect(count(svg, hex)).toBeGreaterThanOrEqual(4));
    expect(svg).not.toContain(GREY);
  });

  it("обзор изделия: пучок между разъёмами — четыре цвета жил, контакты — по жилам", () => {
    const operations = [real("A", 1), real("B", 2)];
    const endpoint = (side: "A" | "B", label: string) => ({ id: side === "A" ? "a1" : "b1", side, label, connId: "kit-0", pins: { 0: "1, 2, 3, 4" }, wireIdxs: [0] });
    const config = {
      wires: [cable], ops: [mount("A", "X1"), mount("B", "X2")],
      gen_state: { kit: [{ id: "kit-0", key: "c|I-DS1071-SCB01X5" }, { id: "kit-2", key: "w|UL2527 28AWG" }],
        S: { wires: [{ idx: 0, qty: 1, cores: 4, compId: "kit-2", length: 420, coreColors: NAMES }], endpoints: [endpoint("A", "X1"), endpoint("B", "X2")] } },
    };
    const svg = svgOf(etkPfArt({ group: group({ final: true, ops: [1, 2] }), operations, document: { operations, build_config: config } }));
    HEX.forEach((hex) => expect(count(svg, hex)).toBeGreaterThanOrEqual(3));
    expect(svg).not.toContain(GREY);
  });

  it("одиночные провода и кабель без цветов красятся как раньше", () => {
    const plain = { ...cable, coreColors: undefined };
    const operations = [real("A", 1)];
    const svg = svgOf(etkPfArt({ group: group(), operations, document: { operations, build_config: { wires: [plain], ops: [{ ...mount("A", "X1"), wires: [plain] }] } } }));
    expect(svg).toContain(GREY);
    HEX.slice(0, 3).forEach((hex) => expect(svg).not.toContain(`stroke="${hex}"`));
  });
});
