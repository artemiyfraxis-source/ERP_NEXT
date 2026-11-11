import { expect, it } from "vitest";
import { GEN_STATE_KEY, genSnapshot, restoreFromSnapshot, snapshotFromConfig } from "./genBuildState";
import { emptyRework, emptyState } from "./tcGenLogic";

it("reopens a JSON snapshot in rework mode and remaps the incoming product after kit rows move", () => {
  const product = { id: "kit-2", type: "Изделие", art: "ДИ-1", name: "Изделие", qty: 2.5 };
  const S = { ...emptyState(), tab: "rework" as const, rework: { ...emptyRework(), compId: product.id, length: "250", quantity: "10", ends: "AB" as const, treatment: "tin" as const, stripLen: "5" } };
  const config = JSON.parse(JSON.stringify({ [GEN_STATE_KEY]: genSnapshot([product], S, {}, {}) }));
  const snap = snapshotFromConfig(config)!;
  const restored = restoreFromSnapshot([{ ...product, id: "kit-0" }], emptyState(), snap);
  expect(restored.S.tab).toBe("rework");
  expect(restored.S.rework).toEqual({ ...S.rework, compId: "kit-0" });
  const removed = restoreFromSnapshot([], emptyState(), snap);
  expect(removed.S.rework?.compId).toBe("");
  expect(removed.note).toContain("убрано 1");
});

it("does not add rework parameters to old snapshots", () => {
  const S = emptyState();
  const restored = restoreFromSnapshot([], S, genSnapshot([], S, {}, {}));
  expect(restored.S).not.toHaveProperty("rework");
});
