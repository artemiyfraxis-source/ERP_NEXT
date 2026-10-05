// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  ComponentTemplateContentError,
  isTemplateContentV1,
  isTemplateContentV2,
  isTemplateContentV3,
  isTemplateContentV4,
  parseComponentTemplateContent,
  reconcileTemplateEnvelopeAssets,
  upgradeComponentTemplateContentV1,
  upgradeComponentTemplateContentV1ToV3,
  upgradeComponentTemplateContentV2,
  upgradeComponentTemplateContentV3,
} from "./template-content";
import { newTemplateContent, type TemplateContent } from "./template-model";

describe("component template content boundary", () => {
  it("keeps schema v1 unchanged until an explicit upgrade is requested", () => {
    const v1 = newTemplateContent();
    const before = JSON.stringify(v1);

    const parsed = parseComponentTemplateContent(v1);

    expect(isTemplateContentV1(parsed)).toBe(true);
    expect(parsed.schemaVersion).toBe(1);
    expect(JSON.stringify(parsed)).toBe(before);
  });

  it("parses an explicitly upgraded schema v2 without rewriting it", () => {
    const upgraded = upgradeComponentTemplateContentV1(newTemplateContent());
    expect(upgraded.content.schemaVersion).toBe(2);

    const parsed = parseComponentTemplateContent(upgraded.content);

    expect(isTemplateContentV2(parsed)).toBe(true);
    expect(parsed).toBe(upgraded.content);
  });

  it("recognizes strict v3 while preserving readable v2 content", () => {
    const v2 = upgradeComponentTemplateContentV1(newTemplateContent()).content;
    const v3 = upgradeComponentTemplateContentV2(v2).content;

    expect(isTemplateContentV2(parseComponentTemplateContent(v2))).toBe(true);
    const parsed = parseComponentTemplateContent(v3);
    expect(isTemplateContentV3(parsed)).toBe(true);
    expect(parsed).toBe(v3);
  });

  it("upgrades v1 through v2 to v3 and installs envelope assets", () => {
    const asset = {
      assetId: crypto.randomUUID(), fileName: "drawing.png", mediaType: "image/png",
      sha256: "c".repeat(64), sizeBytes: 512,
    };

    const result = upgradeComponentTemplateContentV1ToV3(newTemplateContent(), [asset]);

    expect(result.content.schemaVersion).toBe(3);
    expect(result.content.assets).toEqual([asset]);
    expect(isTemplateContentV3(parseComponentTemplateContent(result.content))).toBe(true);
  });

  it("returns inferred cross-view contact diagnostics from the explicit upgrade", () => {
    const contactIds = [crypto.randomUUID(), crypto.randomUUID()];
    const content: TemplateContent = {
      schemaVersion: 1,
      views: [
        {
          id: crypto.randomUUID(), kind: "e4", name: "E4", primitives: [],
          contactPoints: [{ id: contactIds[0]!, name: "Signal", contactNumber: "1", direction: "right", x: 10, y: 20 }],
        },
        {
          id: crypto.randomUUID(), kind: "drawing", name: "Drawing", primitives: [],
          contactPoints: [{ id: contactIds[1]!, name: "Signal", contactNumber: "1", direction: "left", x: 30, y: 40 }],
        },
      ],
    };

    const result = upgradeComponentTemplateContentV1(content);

    expect(result.diagnostics.map(item => item.code)).toEqual(["logical_contact_mapping_inferred"]);
    expect(result.content.views[0]!.contactPoints[0]!.logicalContactId)
      .toBe(result.content.views[1]!.contactPoints[0]!.logicalContactId);
  });

  it("rejects invalid v2 content with addressable diagnostics", () => {
    const invalid = { schemaVersion: 2, views: [] };

    expect(() => parseComponentTemplateContent(invalid)).toThrow(ComponentTemplateContentError);
    try {
      parseComponentTemplateContent(invalid);
    } catch (error) {
      expect(error).toBeInstanceOf(ComponentTemplateContentError);
      expect((error as ComponentTemplateContentError).diagnostics.length).toBeGreaterThan(0);
    }
  });

  it("rejects invalid v3 content with addressable diagnostics", () => {
    const invalid = { schemaVersion: 3, views: [] };

    expect(() => parseComponentTemplateContent(invalid)).toThrow(ComponentTemplateContentError);
    try {
      parseComponentTemplateContent(invalid);
    } catch (error) {
      expect(error).toBeInstanceOf(ComponentTemplateContentError);
      expect((error as ComponentTemplateContentError).diagnostics)
        .toEqual(expect.arrayContaining([expect.objectContaining({ path: "$.logicalContacts" })]));
    }
  });

  it("requires exact v2 asset metadata equality with the version envelope", () => {
    const asset = {
      assetId: crypto.randomUUID(), fileName: "connector.png", mediaType: "image/png",
      sha256: "a".repeat(64), sizeBytes: 128,
    };
    const content = upgradeComponentTemplateContentV1(newTemplateContent(), [asset]).content;

    expect(reconcileTemplateEnvelopeAssets(content, [asset]).diagnostics).toEqual([]);
    expect(reconcileTemplateEnvelopeAssets(content, [{ ...asset, sizeBytes: 129 }]).diagnostics)
      .toEqual([expect.objectContaining({ code: "asset_envelope_mismatch", path: "$.assets" })]);
    expect(reconcileTemplateEnvelopeAssets({ ...content, assets: [] }, [asset]).diagnostics)
      .toEqual([expect.objectContaining({ code: "asset_envelope_mismatch" })]);
  });

  it("copies normalized envelope asset metadata only during an explicit v1 upgrade", () => {
    const assets = [{
      assetId: crypto.randomUUID(), fileName: "connector.png", mediaType: "image/png",
      sha256: "b".repeat(64), sizeBytes: 256,
    }];
    const v1 = newTemplateContent();

    expect(parseComponentTemplateContent(v1).schemaVersion).toBe(1);
    expect(upgradeComponentTemplateContentV1(v1, assets).content.assets).toEqual(assets);
  });

  it("uses the same immutable asset envelope rule for v3", () => {
    const asset = {
      assetId: crypto.randomUUID(), fileName: "connector.png", mediaType: "image/png",
      sha256: "d".repeat(64), sizeBytes: 256,
    };
    const content = upgradeComponentTemplateContentV1ToV3(newTemplateContent(), [asset]).content;

    expect(reconcileTemplateEnvelopeAssets(content, [asset]).diagnostics).toEqual([]);
    expect(reconcileTemplateEnvelopeAssets(content, [{ ...asset, sizeBytes: 257 }]).diagnostics)
      .toEqual([expect.objectContaining({ code: "asset_envelope_mismatch", message: expect.stringContaining("v3") })]);
  });

  it("parses v4 with its E4 table and upgrades v3 explicitly", () => {
    const v3 = upgradeComponentTemplateContentV2(upgradeComponentTemplateContentV1(newTemplateContent()).content).content;
    const upgraded = upgradeComponentTemplateContentV3(v3).content;
    expect(upgraded.schemaVersion).toBe(4);
    const parsed = parseComponentTemplateContent(upgraded);
    expect(isTemplateContentV4(parsed)).toBe(true);
    expect(parsed).toBe(upgraded);
  });

  it("rejects v4 when its table is not synchronized with the v3 article core", () => {
    const v3 = upgradeComponentTemplateContentV2(upgradeComponentTemplateContentV1(newTemplateContent()).content).content;
    const upgraded = upgradeComponentTemplateContentV3(v3).content;
    const broken = {
      ...structuredClone(upgraded),
      e4ConnectorTable: {
        ...structuredClone(upgraded.e4ConnectorTable),
        contactTypeGroups: [...upgraded.e4ConnectorTable.contactTypeGroups, { id: crypto.randomUUID(), name: "Новый тип" }],
      },
    };
    expect(() => parseComponentTemplateContent(broken)).toThrow(ComponentTemplateContentError);
    try { parseComponentTemplateContent(broken); } catch (error) {
      expect((error as ComponentTemplateContentError).diagnostics)
        .toEqual(expect.arrayContaining([expect.objectContaining({ code: "e4_group_set_mismatch" })]));
    }
  });
});
