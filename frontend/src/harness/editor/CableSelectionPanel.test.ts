// @vitest-environment node
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { CableSelectionPanel } from "./CableSelectionPanel";

describe("multicore cable selection panel", () => {
  it("offers one physical cable for multiple selected conductors", () => {
    const markup = renderToStaticMarkup(createElement(CableSelectionPanel, {
      selectedWireIds: ["W1", "W2"], cable: null, disabled: false,
      onCreate: vi.fn(), onUpdate: vi.fn(), onMaterialClear: vi.fn(), onRemove: vi.fn(),
    }));
    expect(markup).toContain("2 жилы");
    expect(markup).toContain("Создать кабель");
    expect(markup).toContain("материал и длина будут учтены один раз");
  });

  it("shows cable members, pinned material and physical cut inputs", () => {
    const markup = renderToStaticMarkup(createElement(CableSelectionPanel, {
      selectedWireIds: ["W1", "W2"], disabled: false,
      cable: { id: "C1", memberWireIds: ["W1", "W2"], lengthMm: null, endCorrectionFromMm: 12, endCorrectionToMm: 8, cutRoundingStepMm: 1,
        materialBinding: { sourceId: "technology-database", snapshotId: "00000000-0000-4000-8000-000000000001", snapshotSha256: "a".repeat(64), recordId: "b".repeat(64), entityType: "cable", sourceKey: "CABLE-2X", displayName: "Кабель 2×0,35" } },
      onCreate: vi.fn(), onUpdate: vi.fn(), onMaterialClear: vi.fn(), onRemove: vi.fn(),
    }));
    expect(markup).toContain("W1"); expect(markup).toContain("W2"); expect(markup).toContain("Кабель 2×0,35");
    expect(markup).toContain('aria-label="Конечная длина кабеля, мм"');
    expect(markup).toContain('aria-label="Снятие оболочки начала, мм"');
    expect(markup).toContain('aria-label="Снятие оболочки конца, мм"');
    expect(markup).toContain('role="tooltip"');
    expect(markup).toContain("Длина кабеля не задана"); expect(markup).toContain("Расформировать кабель");
  });
});
