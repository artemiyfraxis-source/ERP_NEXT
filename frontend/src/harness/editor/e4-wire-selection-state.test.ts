// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  clampScreenPosition,
  e4WireSelectionCapabilities,
  normalizeTwistPitchMm,
} from "./e4-wire-selection-state";

describe("e4 wire selection menu state", () => {
  it("enables differential pair only for exactly two unique selected wires", () => {
    expect(e4WireSelectionCapabilities(["w1"]).canCreateDifferentialPair).toBe(false);
    expect(e4WireSelectionCapabilities(["w1", "w2"]).canCreateDifferentialPair).toBe(true);
    expect(e4WireSelectionCapabilities(["w1", "w1"]).canCreateDifferentialPair).toBe(false);
    expect(e4WireSelectionCapabilities(["w1", "w2", "w3"]).canCreateDifferentialPair).toBe(false);
  });

  it("allows a screen for any non-empty selection and ignores empty ids", () => {
    expect(e4WireSelectionCapabilities([]).canCreateScreen).toBe(false);
    expect(e4WireSelectionCapabilities(["", "  "]).selectedCount).toBe(0);
    expect(e4WireSelectionCapabilities(["w1"]).canCreateScreen).toBe(true);
  });

  it("normalizes values emitted by numeric controls", () => {
    expect(clampScreenPosition(-10)).toBe(0);
    expect(clampScreenPosition(105)).toBe(100);
    expect(clampScreenPosition(Number.NaN)).toBe(50);
    expect(normalizeTwistPitchMm(12.34)).toBe(12.3);
    expect(normalizeTwistPitchMm(0)).toBe(0.1);
    expect(normalizeTwistPitchMm(Number.NaN)).toBe(25);
  });
});
