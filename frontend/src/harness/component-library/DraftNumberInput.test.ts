// @vitest-environment node
import { describe, expect, it } from "vitest";
import { parseDraftNumber } from "./DraftNumberInput";

describe("numeric editing boundaries", () => {
  it("does not convert an empty cell into zero", () => {
    expect(parseDraftNumber("", 0, 100)).toBeNull();
    expect(parseDraftNumber("-", -100, 100)).toBeNull();
    expect(parseDraftNumber("24", 1, 100)).toBe(24);
    expect(parseDraftNumber("-2.5", -100, 100)).toBe(-2.5);
    expect(parseDraftNumber("10001", 1, 10000)).toBeNull();
  });
});
