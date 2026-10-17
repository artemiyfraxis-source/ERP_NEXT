// @vitest-environment node
import { describe, expect, it } from "vitest";
import { applyEditorCommand, createConnector, createWire } from "./commands";
import {
  calculateWireStripSteps,
  createEmptyHarnessDesign,
  parseHarnessDesignDocument,
  type HarnessDesignDocument,
  type WireStripProfileBinding,
} from "./model";

const snapshotId = "38d9aa91-b8d4-45d8-8e39-da14ee4effad";

function profile(overrides: Partial<WireStripProfileBinding> = {}): WireStripProfileBinding {
  return {
    sourceId: "technology-coax-terminations",
    snapshotId,
    snapshotSha256: "a".repeat(64),
    recordId: "b".repeat(64),
    entityType: "coax-termination",
    sourceKey: "BNC|RG58|5|8|12",
    displayName: "BNC / RG58",
    layers: [
      { index: 1, diameterMm: 0.9, stripLengthMm: 2.5 },
      { index: 2, diameterMm: 2.95, stripLengthMm: 3.5 },
      { index: 3, diameterMm: 4.95, stripLengthMm: 7.5 },
    ],
    ...overrides,
  };
}

function documentWithWire(): HarnessDesignDocument {
  const first = createConnector("x1", "X1", 1, { x: 0, y: 0 });
  const second = createConnector("x2", "X2", 1, { x: 800, y: 0 });
  let document = createEmptyHarnessDesign();
  document = applyEditorCommand(document, { type: "add-connector", connector: first });
  document = applyEditorCommand(document, { type: "add-connector", connector: second });
  return applyEditorCommand(document, {
    type: "add-wire",
    wire: createWire(
      "w1",
      { connectorId: first.id, contactId: first.contacts[0]!.id },
      { connectorId: second.id, contactId: second.contacts[0]!.id },
    ),
  });
}

describe("wire strip profile document model", () => {
  it("keeps legacy schemaVersion 1 wires without strip profiles compatible", () => {
    const parsed = parseHarnessDesignDocument(JSON.parse(JSON.stringify(documentWithWire())));

    expect(parsed.wires[0]!.stripProfiles).toBeUndefined();
  });

  it("parses immutable profiles independently for both ends", () => {
    const source = documentWithWire();
    const parsed = parseHarnessDesignDocument({
      ...source,
      wires: [{ ...source.wires[0]!, stripProfiles: {
        from: profile(),
        to: profile({ sourceKey: "BNC|RG58|6|9|13", displayName: "BNC / RG58, вариант 2" }),
      } }],
    });

    expect(parsed.wires[0]!.stripProfiles?.from?.layers).toEqual(profile().layers);
    expect(parsed.wires[0]!.stripProfiles?.to?.sourceKey).toBe("BNC|RG58|6|9|13");
    expect(Object.isFrozen(parsed.wires[0]!.stripProfiles)).toBe(true);
    expect(Object.isFrozen(parsed.wires[0]!.stripProfiles!.from)).toBe(true);
    expect(Object.isFrozen(parsed.wires[0]!.stripProfiles!.from!.layers)).toBe(true);
    expect(Object.isFrozen(parsed.wires[0]!.stripProfiles!.from!.layers[0])).toBe(true);
  });

  it("normalizes an empty legacy strip profile container away", () => {
    const source = documentWithWire();
    const parsed = parseHarnessDesignDocument({
      ...source,
      wires: [{ ...source.wires[0]!, stripProfiles: {} }],
    });

    expect(parsed.wires[0]!.stripProfiles).toBeUndefined();
  });

  it.each([
    ["unsupported entity", profile({ entityType: "terminal" as "coax-termination" })],
    ["invalid snapshot", profile({ snapshotId: "not-a-guid" })],
    ["invalid record ID", profile({ recordId: "record-1" })],
    ["no layers", profile({ layers: [] })],
    ["more than 64 layers", profile({ layers: Array.from({ length: 65 }, (_, index) => ({
      index: index + 1, diameterMm: index + 1, stripLengthMm: index + 1,
    })) })],
    ["zero index", profile({ layers: [{ index: 0, diameterMm: 1, stripLengthMm: 1 }] })],
    ["duplicate index", profile({ layers: [
      { index: 1, diameterMm: 1, stripLengthMm: 1 },
      { index: 1, diameterMm: 2, stripLengthMm: 2 },
    ] })],
    ["unsorted indices", profile({ layers: [
      { index: 2, diameterMm: 1, stripLengthMm: 1 },
      { index: 1, diameterMm: 2, stripLengthMm: 2 },
    ] })],
    ["zero diameter", profile({ layers: [{ index: 1, diameterMm: 0, stripLengthMm: 1 }] })],
    ["zero length", profile({ layers: [{ index: 1, diameterMm: 1, stripLengthMm: 0 }] })],
    ["decreasing diameter", profile({ layers: [
      { index: 1, diameterMm: 2, stripLengthMm: 1 },
      { index: 2, diameterMm: 1, stripLengthMm: 2 },
    ] })],
    ["equal cumulative length", profile({ layers: [
      { index: 1, diameterMm: 1, stripLengthMm: 2 },
      { index: 2, diameterMm: 2, stripLengthMm: 2 },
    ] })],
    ["sub-micrometre diameter", profile({ layers: [{ index: 1, diameterMm: 1.0001, stripLengthMm: 1 }] })],
    ["measurement above domain limit", profile({ layers: [{ index: 1, diameterMm: 1, stripLengthMm: 1_000_000_001 }] })],
  ])("rejects %s", (_name, invalidProfile) => {
    const source = documentWithWire();
    expect(() => parseHarnessDesignDocument({
      ...source,
      wires: [{ ...source.wires[0]!, stripProfiles: { from: invalidProfile } }],
    })).toThrow();
  });

  it("calculates exact independent steps from cumulative lengths", () => {
    const layers = profile().layers;
    const steps = calculateWireStripSteps(layers);
    expect(steps).toEqual([
      { index: 1, diameterMm: 0.9, cumulativeLengthMm: 2.5, stepLengthMm: 2.5 },
      { index: 2, diameterMm: 2.95, cumulativeLengthMm: 3.5, stepLengthMm: 1 },
      { index: 3, diameterMm: 4.95, cumulativeLengthMm: 7.5, stepLengthMm: 4 },
    ]);
    expect(Object.isFrozen(steps)).toBe(true);
    expect(layers).toEqual(profile().layers);
  });
});

describe("set-wire-strip-profile command", () => {
  it("assigns, replaces and clears each end without changing wire geometry", () => {
    let document = documentWithWire();
    const initialRoute = document.wires[0]!.e4Route;
    const from = profile();
    const to = profile({ sourceKey: "BNC|RG58|to", displayName: "Конец B" });

    document = applyEditorCommand(document, { type: "set-wire-strip-profile", wireId: "w1", end: "from", profile: from });
    document = applyEditorCommand(document, { type: "set-wire-strip-profile", wireId: "w1", end: "to", profile: to });
    expect(document.wires[0]!.stripProfiles).toMatchObject({ from, to });
    expect(document.wires[0]!.e4Route).toEqual(initialRoute);

    const replacement = profile({ sourceKey: "BNC|RG58|replacement", displayName: "Новая разделка" });
    document = applyEditorCommand(document, { type: "set-wire-strip-profile", wireId: "w1", end: "from", profile: replacement });
    expect(document.wires[0]!.stripProfiles?.from?.sourceKey).toBe("BNC|RG58|replacement");
    expect(document.wires[0]!.stripProfiles?.to?.sourceKey).toBe("BNC|RG58|to");

    document = applyEditorCommand(document, { type: "set-wire-strip-profile", wireId: "w1", end: "from", profile: null });
    expect(document.wires[0]!.stripProfiles?.from).toBeUndefined();
    expect(document.wires[0]!.stripProfiles?.to).toEqual(to);
    document = applyEditorCommand(document, { type: "set-wire-strip-profile", wireId: "w1", end: "to", profile: null });
    expect(document.wires[0]!.stripProfiles).toBeUndefined();
  });

  it("validates a command profile before storing its immutable snapshot", () => {
    const document = documentWithWire();
    const invalid = profile({ layers: [{ index: 1, diameterMm: 1, stripLengthMm: 1.0001 }] });

    expect(() => applyEditorCommand(document, {
      type: "set-wire-strip-profile", wireId: "w1", end: "from", profile: invalid,
    })).toThrow(/0,001 мм/);
    expect(document.wires[0]!.stripProfiles).toBeUndefined();
  });
});

describe("free drawing end commands", () => {
  it("updates a free endpoint and rejects non-finite coordinates", () => {
    const document = { ...createEmptyHarnessDesign(), wires: [{ ...createWire("free", { connectorId: "isolated:free:from", contactId: "free" }, { connectorId: "isolated:free:to", contactId: "free" }), drawingEndpoints: { from: { x: 1, y: 2 }, to: { x: 10, y: 2 } } }] };
    const changed = applyEditorCommand(document, { type: "set-wire-drawing-endpoint", wireId: "free", end: "from", position: { x: 5, y: 6 } });
    expect(changed.wires[0]!.drawingEndpoints?.from).toEqual({ x: 5, y: 6 });
    expect(() => applyEditorCommand(document, { type: "set-wire-drawing-endpoint", wireId: "free", end: "from", position: { x: Number.NaN, y: 6 } })).toThrow(/конечными/);
  });
});
