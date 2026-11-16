import { describe, expect, it } from "vitest";
import { compareShipments, shipmentCreatedDay, shipmentDateKind, shipmentDay } from "./shipmentOrder";

const shipment = (id: string, created_at: string, shipped_at: string | null = null, deadline: string | null = null) => ({ id, created_at, shipped_at, deadline });

describe("shipment display order", () => {
  it("uses actual, then planned, then created day and newest first", () => {
    const backdated = shipment("a", "2026-11-15T12:00:00", "2026-11-01", "2026-12-01");
    const planned = shipment("b", "2026-10-01T12:00:00", null, "2026-11-20");
    const actual = shipment("c", "2026-10-01T12:00:00", "2026-11-10");
    const created = shipment("d", "2026-11-12T12:00:00");
    expect([backdated, planned, actual, created].sort(compareShipments).map((row) => row.id)).toEqual(["b", "d", "c", "a"]);
    expect(shipmentDay(backdated)).toBe("2026-11-01");
    expect([backdated, planned, created].map(shipmentDateKind)).toEqual(["факт", "план", "создана"]);
  });

  it("breaks day ties by creation instant then id, independent of timestamp spelling", () => {
    const rows = [shipment("a", "2026-11-01T10:00:00+03:00", "2026-11-15"),
      shipment("b", "2026-11-01T07:00:00Z", "2026-11-15"), shipment("c", "2026-11-01T11:00:00", "2026-11-15")];
    expect(rows.sort(compareShipments).map((row) => row.id)).toEqual(["c", "b", "a"]);
  });

  it("uses Moscow day near midnight, including offsetless SQLite timestamps", () => {
    expect(shipmentCreatedDay("2026-11-01T21:15:00Z")).toBe("2026-11-02");
    expect(shipmentCreatedDay("2026-11-01T23:15:00-04:00")).toBe("2026-11-02");
    expect(shipmentCreatedDay("2026-11-01T00:15:00")).toBe("2026-11-01");
  });

  it("preserves the server's microsecond creation order before the id tie-breaker", () => {
    const rows = [shipment("z", "2026-11-01T10:00:00.000100+03:00", "2026-11-15"),
      shipment("a", "2026-11-01T07:00:00.000900Z", "2026-11-15")];
    expect(rows.sort(compareShipments).map((row) => row.id)).toEqual(["a", "z"]);
  });
});
