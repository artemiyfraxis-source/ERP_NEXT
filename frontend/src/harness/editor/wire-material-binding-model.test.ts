// @vitest-environment node
import { describe, expect, it } from "vitest";
import { createConnector, createWire } from "./commands";
import { createEmptyHarnessDesign, parseHarnessDesignDocument } from "./model";

const snapshotId = "38d9aa91-b8d4-45d8-8e39-da14ee4effad";
const snapshotSha256 = "a".repeat(64);
const recordId = "b".repeat(64);

function documentWithWire() {
  const first = createConnector("x1", "X1", 1, { x: 0, y: 0 });
  const second = createConnector("x2", "X2", 1, { x: 800, y: 0 });
  const { e4Route: _route, ...wireWithoutPersistedRoute } = createWire(
    "w1",
    { connectorId: first.id, contactId: first.contacts[0]!.id },
    { connectorId: second.id, contactId: second.contacts[0]!.id },
  );
  return {
    ...createEmptyHarnessDesign(),
    connectors: [first, second],
    wires: [wireWithoutPersistedRoute],
  };
}

function binding(overrides: Readonly<Record<string, unknown>> = {}) {
  return {
    sourceId: "technology-wires",
    snapshotId,
    snapshotSha256,
    recordId,
    entityType: "wire",
    sourceKey: "UL1061-28-BK",
    displayName: "UL1061 28AWG, чёрный",
    ...overrides,
  };
}

describe("wire material binding document model", () => {
  it("keeps schemaVersion 1 documents without a material binding backward compatible", () => {
    const parsed = parseHarnessDesignDocument(JSON.parse(JSON.stringify(documentWithWire())));

    expect(parsed.schemaVersion).toBe(1);
    expect(parsed.wires[0]!.materialBinding).toBeUndefined();
  });

  it("parses and freezes an exact catalog snapshot binding", () => {
    const source = documentWithWire();
    const parsed = parseHarnessDesignDocument({
      ...source,
      wires: [{ ...source.wires[0]!, materialBinding: binding() }],
    });

    expect(parsed.wires[0]!.materialBinding).toEqual(binding());
    expect(Object.isFrozen(parsed.wires[0]!.materialBinding)).toBe(true);
  });

  it.each([
    ["empty source", { sourceId: "" }],
    ["long source", { sourceId: "s".repeat(129) }],
    ["invalid snapshot ID", { snapshotId: "not-a-guid" }],
    ["empty snapshot ID", { snapshotId: "00000000-0000-0000-0000-000000000000" }],
    ["invalid snapshot hash", { snapshotSha256: "a".repeat(63) }],
    ["invalid record ID", { recordId: "record-1" }],
    ["unsupported entity", { entityType: "terminal" }],
    ["long source key", { sourceKey: "k".repeat(513) }],
    ["long display name", { displayName: "n".repeat(257) }],
  ])("rejects %s", (_name, overrides) => {
    const source = documentWithWire();
    expect(() => parseHarnessDesignDocument({
      ...source,
      wires: [{ ...source.wires[0]!, materialBinding: binding(overrides) }],
    })).toThrow();
  });

  it("accepts a cable record and normalizes a mixed-case snapshot ID", () => {
    const source = documentWithWire();
    const parsed = parseHarnessDesignDocument({
      ...source,
      wires: [{ ...source.wires[0]!, materialBinding: binding({
        entityType: "cable",
        snapshotId: snapshotId.toUpperCase(),
      }) }],
    });

    expect(parsed.wires[0]!.materialBinding).toMatchObject({ entityType: "cable", snapshotId });
  });
});
