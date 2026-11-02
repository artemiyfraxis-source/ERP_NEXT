import { describe, expect, it } from "vitest";
import { needsTrack, OWN_COURIER, termsFor } from "./carriers";

describe("shipment tracking rule", () => {
  it("keeps our courier a carrier delivery without requiring a track", () => {
    expect(termsFor(OWN_COURIER)).toBe("ДОСТАВКА ТК");
    expect(needsTrack({ terms: "ДОСТАВКА ТК", carrier: ` ${OWN_COURIER} ` })).toBe(false);
    for (const carrier of ["СДЭК", "Яндекс Доставка", "Курьер внешний", "курьер (наш)", "old carrier"]) {
      expect(needsTrack({ terms: "ДОСТАВКА ТК", carrier })).toBe(true);
    }
    expect(needsTrack({ terms: "САМОВЫВОЗ", carrier: "" })).toBe(false);
  });
  it("uses the server decision when provided", () => {
    expect(needsTrack({ terms: "ДОСТАВКА ТК", carrier: OWN_COURIER, track_required: true })).toBe(true);
    expect(needsTrack({ terms: "ДОСТАВКА ТК", carrier: "СДЭК", track_required: false })).toBe(false);
  });
});
