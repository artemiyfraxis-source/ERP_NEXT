// @vitest-environment node
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { physicalFixture } from "./physical-topology-fixture";
import { drawingPhysicalScale, drawingPipeWidth, drawingRealSizeScale, drawingWireWidth, segmentWireProjection, wiresWithoutDiameter } from "./drawing-thickness";
import { applyInRealSizeView, connectorsWithoutRealSize, drawingScale, DRAWING_VIEW_PLACEMENT_ID, realSizeView } from "./drawing-scale";
import { validateDrawingDocuments, type DrawingDocuments, type DrawingRealSize } from "./drawing-documents";
import { RealSizeControl, RealSizeScaleBar, scaleBarLength } from "./DrawingScaleControl";
import { applyEditorCommand } from "./commands";
import { parseHarnessDesignDocument, type ConnectorInstance, type HarnessDesignDocument, type WireMaterialBinding } from "./model";

const binding = (outerDiameterMm?: number): WireMaterialBinding => ({ sourceId: "technology", snapshotId: "11111111-2222-3333-8444-555555555555", snapshotSha256: "a".repeat(64),
  recordId: "a".repeat(64), entityType: "wire", sourceKey: "МГТФ", displayName: "МГТФ", ...(outerDiameterMm === undefined ? {} : { outerDiameterMm }) });
const docs = (extra: Partial<DrawingDocuments> = {}): DrawingDocuments => ({ tables: [], leaders: [], bomOrder: [], ...extra });
/** W1 Ø1,1 мм, W2 Ø2,3 мм, W3 without a catalogue diameter. */
function drawing(realSize?: DrawingRealSize): HarnessDesignDocument {
  const base = physicalFixture();
  const diameters = [1.1, 2.3, undefined];
  return { ...base, wires: base.wires.map((wire, i) => ({ ...wire, materialBinding: binding(diameters[i]) })),
    ...(realSize ? { drawingDocuments: docs({ realSize }) } : {}) };
}
const widths = (document: HarnessDesignDocument) => document.wires.map(wire => drawingWireWidth(document, wire));
const pipes = (document: HarnessDesignDocument) => document.physicalTopology!.segments.map(segment => drawingPipeWidth(document, segment));

describe("«Реальные размеры, мм» — widths", () => {
  it("a drawing without the field keeps today's relative widths, and the switch off changes nothing", () => {
    const today = drawing();
    expect(drawingRealSizeScale(today)).toBeNull();
    // Relative look: 2,5 drawing units for the thinnest catalogue wire, the rest in proportion; no diameter → as the thinnest.
    expect(widths(today)).toEqual([2.5, 2.5 * 2.3 / 1.1, 2.5]);
    expect(pipes(today)).toEqual([2.5 + 2.5 * 2.3 / 1.1 + .25 + .5, 2.5 + 2.5 + .25 + .5, 2.5 * 2.3 / 1.1 + 2.5 + .25 + .5].map(v => expect.closeTo(v, 9)));
    const off = drawing({ enabled: false, scaleDenominator: 5 });
    expect(widths(off)).toEqual(widths(today));
    expect(pipes(off)).toEqual(pipes(today));
    expect(segmentWireProjection(off, "S0")).toEqual(segmentWireProjection(today, "S0"));
    expect(drawingPhysicalScale(off)).toBe(1);
  });

  it("in real size a wire is its catalogue diameter / N; one without a diameter is drawn as the thinnest and counted", () => {
    const half = drawing({ enabled: true, scaleDenominator: 2 });
    expect(widths(half)).toEqual([1.1 / 2, 2.3 / 2, 1.1 / 2]);
    expect(wiresWithoutDiameter(half)).toBe(1);
    const enlarged = drawing({ enabled: true, scaleDenominator: .5 });
    expect(widths(enlarged)).toEqual([2.2, 4.6, 2.2]);
    // The «Толщина» factor does not apply: 1 drawing unit is N mm.
    const thick = { ...half, drawingDocuments: docs({ realSize: { enabled: true, scaleDenominator: 2 }, physicalScale: 4 }) };
    expect(widths(thick)).toEqual(widths(half));
    expect(drawingPhysicalScale(thick)).toBe(.5);
  });

  it("a pipe in real size is the touching bundle, a cable segment its catalogue diameter / N", () => {
    const real = drawing({ enabled: true, scaleDenominator: 2 });
    expect(pipes(real)[0]).toBeCloseTo((1.1 + 2.3) / 2, 9);
    const cable = { id: "K1", designation: "К1", memberWireIds: ["W1"], lengthMm: 300, materialBinding: { ...binding(6), entityType: "cable" as const } };
    const withCable = { ...real, cables: [cable] as unknown as HarnessDesignDocument["cables"],
      physicalTopology: { ...real.physicalTopology!, segments: real.physicalTopology!.segments.map(s => s.id === "S0" ? { ...s, specificationItemId: "K1" } : s) } };
    expect(drawingPipeWidth(withCable, withCable.physicalTopology.segments[0]!)).toBe(3);
  });
});

describe("«Реальные размеры, мм» — the stored field", () => {
  const document = drawing();
  it("validates enabled + М 1:N and keeps the field through storage", () => {
    for (const realSize of [{ enabled: true, scaleDenominator: 1 }, { enabled: false, scaleDenominator: 20 }, { enabled: true, scaleDenominator: .1 }])
      expect(validateDrawingDocuments(docs({ realSize }), document)?.realSize).toEqual(realSize);
    for (const realSize of [{ enabled: "да", scaleDenominator: 1 }, { enabled: true, scaleDenominator: 0 }, { enabled: true, scaleDenominator: 25 },
      { enabled: true, scaleDenominator: Number.NaN }, { enabled: true }, { enabled: true, scaleDenominator: 1, extra: 1 }, null, [1]])
      expect(() => validateDrawingDocuments({ ...docs(), realSize }, document)).toThrow();
    const stored = { ...document, drawingDocuments: docs({ realSize: { enabled: true, scaleDenominator: 2.5 } }) };
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(stored))).drawingDocuments?.realSize).toEqual({ enabled: true, scaleDenominator: 2.5 });
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify({ ...document, drawingDocuments: docs() }))).drawingDocuments).not.toHaveProperty("realSize");
  });
});

describe("«Реальные размеры, мм» — connectors at template size", () => {
  const template = (connector: ConnectorInstance, scale?: number): ConnectorInstance => ({ ...connector,
    libraryBinding: { mode: "template" } as unknown as ConnectorInstance["libraryBinding"],
    ...(scale === undefined ? {} : { drawingPlacements: [{ drawingId: DRAWING_VIEW_PLACEMENT_ID, visible: true, offset: { x: 0, y: 0 }, scale }] }) });
  const withTemplates = (realSize?: DrawingRealSize): HarnessDesignDocument => {
    const base = drawing(realSize);
    return { ...base, connectors: base.connectors.map((c, i) => i === 0 ? template(c) : i === 1 ? template(c, 1.5) : c) };
  };

  it("is a view: off — the same document; on — template connectors at 1/N, the others as drawn", () => {
    const off = withTemplates({ enabled: false, scaleDenominator: 4 });
    expect(realSizeView(off)).toBe(off);
    const on = withTemplates({ enabled: true, scaleDenominator: 4 }), view = realSizeView(on);
    // No measured template units → 1 unit = 1 mm, and both library connectors count as «без размеров».
    expect(view.connectors.map(c => drawingScale(c.drawingPlacements))).toEqual([.25, .25, 1]);
    expect(on.connectors.map(c => drawingScale(c.drawingPlacements))).toEqual([1, 1.5, 1]);
    expect(connectorsWithoutRealSize(on)).toBe(3);
    // A drawing measured at 10 units per mm is shown at 1/(N × 10).
    const measured = new Map([["A", 10]]);
    expect(realSizeView(on, measured).connectors.map(c => drawingScale(c.drawingPlacements))).toEqual([.025, .25, 1]);
    expect(connectorsWithoutRealSize(on, measured)).toBe(2);
  });

  it("an edit made in real size stores the drawing's own scales back", () => {
    const stored = withTemplates({ enabled: true, scaleDenominator: 4 });
    const untouched = applyInRealSizeView(stored, document => ({ ...document, wires: document.wires.slice(0, 2) }));
    expect(untouched.connectors).toEqual(stored.connectors);
    expect(untouched.connectors[0]).toBe(stored.connectors[0]);
    expect(untouched.wires).toHaveLength(2);
    const rotated = applyInRealSizeView(stored, document => applyEditorCommand(document, { type: "set-drawing-placement", connectorId: "A", drawingId: DRAWING_VIEW_PLACEMENT_ID, rotationDegrees: 90 }));
    expect(rotated.connectors[0]!.drawingPlacements).toEqual([{ drawingId: DRAWING_VIEW_PLACEMENT_ID, visible: true, offset: { x: 0, y: 0 }, rotationDegrees: 90 }]);
    expect(rotated.connectors[1]).toBe(stored.connectors[1]);
    const moved = applyInRealSizeView(stored, document => ({ ...document, connectors: document.connectors.map(c => ({ ...c, positions: { ...c.positions, drawing: { x: 1, y: 2 } } })) }));
    expect(moved.connectors[0]).not.toHaveProperty("drawingPlacements");
    expect(drawingScale(moved.connectors[1]!.drawingPlacements)).toBe(1.5);
    expect(moved.connectors.every(c => c.positions.drawing.x === 1)).toBe(true);
    expect(applyInRealSizeView(stored, document => document)).toBe(stored);
    const measured = applyInRealSizeView(stored, document => ({ ...document, connectors: document.connectors.map(c => ({ ...c, designation: `${c.designation}*` })) }), new Map([["A", 10], ["B", 13.6]]));
    expect(measured.connectors.map(c => drawingScale(c.drawingPlacements))).toEqual([1, 1.5, 1]);
    expect(measured.connectors[0]).not.toHaveProperty("drawingPlacements");
  });
});

it("the «Вид» switch is off by default and the bar names М 1:N, the cursor in mm and what has no size", () => {
  const control = (value?: DrawingRealSize) => renderToStaticMarkup(createElement(RealSizeControl, { value, disabled: false, onChange: vi.fn() }));
  expect(control()).toContain("Реальные размеры, мм");
  expect(control()).not.toContain("checked");
  expect(control()).toMatch(/aria-label="Масштаб реальных размеров, М 1:N"[^>]*disabled/);
  expect(control({ enabled: true, scaleDenominator: 2.5 })).toContain('value="2,5"');
  const bar = renderToStaticMarkup(createElement(RealSizeScaleBar, { status: { denominator: 2, wiresWithoutDiameter: 1, connectorsWithoutSize: 0 }, zoom: 1, cursor: { x: 10, y: 4.25 } }));
  expect(bar).toContain("М 1:2 · длины не в масштабе");
  expect(bar).toContain("X 20 · Y 8,5 мм");
  expect(bar).toContain("200 мм");
  expect(bar).toContain("проводов без диаметра: 1");
  expect(bar).not.toContain("соединителей");
});

it("the scale bar is the shortest 1-2-5 length of at least 64 px", () => {
  expect(scaleBarLength(1, 1)).toEqual({ mm: 100, px: 100 });
  expect(scaleBarLength(1, 10)).toEqual({ mm: 10, px: 100 });
  expect(scaleBarLength(5, 2)).toEqual({ mm: 200, px: 80 });
  expect(scaleBarLength(.5, 1).mm).toBe(50);
});
