import { expect, it } from "vitest";
import type { Shipment } from "./api";
import { orderShipmentRows, shipmentGroupKey, shipmentSequence } from "./ShipmentGroupHeader";

const dated = (id: string, shipped_at: string) => ({ id, shipped_at, deadline: null, created_at: "2026-11-01T12:00:00" } as Shipment);

it("orders project groups by their newest shipment, then shipments newest first", () => {
  const rows = [{ project: "100К", shipment: dated("old", "2026-11-01") }, { project: "200К", shipment: dated("newest", "2026-11-15") },
    { project: "200К", shipment: dated("middle", "2026-11-10") }, { project: "001К", shipment: null }];
  expect(orderShipmentRows(rows).map((row) => row.shipment?.id ?? row.project)).toEqual(["newest", "middle", "old", "001К"]);
  expect(rows[0].shipment?.id).toBe("old");
});

it("numbers unique shipments oldest first independently within each project", () => {
  const old = { project: "100К", shipment: dated("old", "2026-11-01") };
  const newest = { project: "100К", shipment: dated("new", "2026-11-15") };
  const otherProject = { ...newest, project: "200К" };
  const unshipped = { project: "100К", shipment: null };
  const rows = [newest, old, newest, otherProject, unshipped];
  const sequence = shipmentSequence(orderShipmentRows(rows));
  expect(sequence.get(shipmentGroupKey(old))).toBe("1 из 2");
  expect(sequence.get(shipmentGroupKey(newest))).toBe("2 из 2");
  expect(sequence.get(shipmentGroupKey(otherProject))).toBe("1 из 1");
  expect(sequence.has(shipmentGroupKey(unshipped))).toBe(false);
  expect(sequence.size).toBe(3);
  expect(shipmentSequence([...rows].reverse())).toEqual(sequence);
});

it("keeps group identities distinct for project rows and shipment rows", () => {
  const shipment = dated("same", "2026-11-01");
  expect(shipmentGroupKey({ project: "100К" })).toBe(shipmentGroupKey({ project: "100К", shipment: null }));
  expect(shipmentGroupKey({ project: "100К", shipment })).not.toBe(shipmentGroupKey({ project: "200К", shipment }));
  expect(shipmentGroupKey({ project: "100К", shipment })).not.toBe(shipmentGroupKey({ project: "100К" }));
});
