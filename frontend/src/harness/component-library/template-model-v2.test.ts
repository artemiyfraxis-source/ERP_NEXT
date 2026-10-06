// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  repeatOccurrenceKeyV2, upgradeTemplateContentV1ToV2, validateTemplateContentV2,
  type NumericExpressionV2, type TemplateContentV2, type TemplateNodeV2,
} from "./template-model-v2";

const ids = {
  e4: "00000000-0000-4000-8000-000000000001", drawing: "00000000-0000-4000-8000-000000000002",
  e4Layer: "00000000-0000-4000-8000-000000000003", drawingLayer: "00000000-0000-4000-8000-000000000004",
  contact: "00000000-0000-4000-8000-000000000005", e4Point: "00000000-0000-4000-8000-000000000006",
  drawingPoint: "00000000-0000-4000-8000-000000000007", primitive: "00000000-0000-4000-8000-000000000008",
  group: "00000000-0000-4000-8000-000000000009", count: "00000000-0000-4000-8000-00000000000a",
  size: "00000000-0000-4000-8000-00000000000b", repeater: "00000000-0000-4000-8000-00000000000c",
  asset: "00000000-0000-4000-8000-00000000000d", preset: "00000000-0000-4000-8000-00000000000e",
  image: "00000000-0000-4000-8000-00000000000f", bundle: "00000000-0000-4000-8000-000000000010",
  secondGroup: "00000000-0000-4000-8000-000000000011", drawingPrimitive: "00000000-0000-4000-8000-000000000012",
  drawingGroup: "00000000-0000-4000-8000-000000000013", secondRepeater: "00000000-0000-4000-8000-000000000014",
} as const;

const c = (value: number): NumericExpressionV2 => ({ kind: "constant", value });
const p = (parameterId: string): NumericExpressionV2 => ({ kind: "parameter", parameterId });
const transform = { translateX: c(0), translateY: c(0), rotationDegrees: c(0), scaleX: c(1), scaleY: c(1) };
function node(partial: Partial<TemplateNodeV2> & Pick<TemplateNodeV2, "id" | "kind" | "geometry">): TemplateNodeV2 {
  return { layerId: ids.e4Layer, visible: true, locked: false, opacity: 1, transform, stroke: { color: "#112233", width: c(2) }, fill: { color: null }, ...partial } as TemplateNodeV2;
}

function validDocument(): TemplateContentV2 {
  const primitive = node({ id: ids.primitive, kind: "rectangle", geometry: { x: c(10), y: c(20), width: p(ids.size), height: c(40), cornerRadii: [c(0), c(4), c(4), c(0)] } });
  const group = node({ id: ids.group, kind: "group", geometry: { childIds: [ids.primitive] } });
  const image = node({ id: ids.image, kind: "image", geometry: { assetId: ids.asset, x: c(0), y: c(0), width: c(100), height: c(80), cropX: 0, cropY: 0, cropWidth: 1, cropHeight: 1, underlay: true } });
  const drawingPrimitive = node({ id: ids.drawingPrimitive, layerId: ids.drawingLayer, kind: "ellipse", geometry: { centerX: c(10), centerY: c(20), radiusX: c(5), radiusY: c(8) } });
  const drawingGroup = node({ id: ids.drawingGroup, layerId: ids.drawingLayer, kind: "group", geometry: { childIds: [ids.drawingPrimitive] } });
  return {
    schemaVersion: 2,
    views: [
      { id: ids.e4, name: "Схема Э4", kind: "e4", layers: [{ id: ids.e4Layer, name: "Основной", visible: true, locked: false, nodes: [primitive, group, image] }], contactPoints: [{ id: ids.e4Point, logicalContactId: ids.contact, x: c(12), y: c(22), direction: "right" }], bundlePorts: [{ id: ids.bundle, name: "Общий выход", x: c(5), y: c(5), direction: "left" }], repeatPlacements: [{ repeatDomainId: ids.repeater, prototypeGroupId: ids.group, step: { x: c(10), y: c(0) }, contactPointIds: [ids.e4Point] }] },
      { id: ids.drawing, name: "Чертёж", kind: "drawing", layers: [{ id: ids.drawingLayer, name: "Основной", visible: true, locked: false, nodes: [drawingPrimitive, drawingGroup] }], contactPoints: [{ id: ids.drawingPoint, logicalContactId: ids.contact, x: c(15), y: c(25), direction: "right" }], bundlePorts: [], repeatPlacements: [{ repeatDomainId: ids.repeater, prototypeGroupId: ids.drawingGroup, step: { x: c(0), y: c(12) }, contactPointIds: [ids.drawingPoint] }] },
    ],
    logicalContacts: [{ id: ids.contact, number: "1", name: "Сигнал 1", contactType: "signal" }],
    parameters: [
      { id: ids.count, name: "Количество", type: "integer", unit: "шт", defaultValue: 2, minimum: 1, maximum: 100, formula: null },
      { id: ids.size, name: "Ширина", type: "number", unit: "px", defaultValue: 50, minimum: 1, maximum: 500, formula: { kind: "binary", operator: "multiply", left: p(ids.count), right: c(25) } },
    ],
    repeaters: [{ id: ids.repeater, countParameterId: ids.count, logicalContactIds: [ids.contact] }],
    assets: [{ assetId: ids.asset, fileName: "connector.png", mediaType: "image/png", sha256: "a".repeat(64), sizeBytes: 1024 }],
    articleParameterPresets: [{ id: ids.preset, sourceId: "technology", entityType: "connector", articleKey: "B2B-XH-A", values: [{ parameterId: ids.count, value: 2 }, { parameterId: ids.size, value: 50 }] }],
  };
}

const codes = (value: unknown) => validateTemplateContentV2(value).diagnostics.map(item => item.code);

describe("template content v2 validation", () => {
  it("accepts layers, group/repeater, linked contacts, typed parameters, preset and asset reference", () => {
    expect(validateTemplateContentV2(validDocument())).toEqual({ valid: true, diagnostics: [] });
  });

  it("accepts optional persisted dash styles and rejects unknown ones", () => {
    const legacy = validDocument();
    expect(validateTemplateContentV2(legacy).valid).toBe(true);
    legacy.views[0]!.layers[0]!.nodes[0]!.stroke.dash = "dash-dot";
    expect(validateTemplateContentV2(legacy).valid).toBe(true);
    (legacy.views[0]!.layers[0]!.nodes[0]!.stroke as { dash?: string }).dash = "zigzag";
    expect(codes(legacy)).toContain("invalid_stroke_dash");
  });

  it("enforces exact keys at every structural boundary", () => {
    const document = structuredClone(validDocument()) as TemplateContentV2 & { surprise?: boolean };
    document.surprise = true;
    expect(codes(document)).toContain("unexpected_key");
    const badNode = structuredClone(validDocument()) as unknown as { views: Array<{ layers: Array<{ nodes: Array<Record<string, unknown>> }> }> };
    badNode.views[0]!.layers[0]!.nodes[0]!.legacyColor = "red";
    expect(codes(badNode)).toContain("unexpected_key");
  });

  it("rejects non-finite constants, unknown numeric parameter refs and oversized AST", () => {
    const document = structuredClone(validDocument());
    const rectangle = document.views[0]!.layers[0]!.nodes[0] as { geometry: { x: NumericExpressionV2; y: NumericExpressionV2 } };
    rectangle.geometry.x = c(Number.NaN);
    rectangle.geometry.y = p(ids.asset);
    let deep: NumericExpressionV2 = c(1);
    for (let index = 0; index < 10; index++) deep = { kind: "negate", operand: deep };
    document.parameters[1]!.formula = deep;
    expect(codes(document)).toEqual(expect.arrayContaining(["invalid_number", "missing_numeric_parameter", "expression_limit"]));
  });

  it("detects formula dependency cycles without executing strings or eval", () => {
    const document = validDocument();
    document.parameters[0]!.formula = p(ids.size);
    document.parameters[1]!.formula = p(ids.count);
    expect(codes(document)).toContain("expression_cycle");
  });

  it("rejects missing refs, duplicate contact numbers and invalid typed preset values", () => {
    const document = validDocument();
    document.views[0]!.contactPoints[0]!.logicalContactId = ids.asset;
    document.logicalContacts.push({ ...document.logicalContacts[0]!, id: ids.secondGroup });
    document.articleParameterPresets[0]!.values[0]!.value = 2.5;
    expect(codes(document)).toEqual(expect.arrayContaining(["missing_logical_contact", "duplicate_contact_number", "parameter_value"]));
  });

  it("rejects group cycles, repeated ownership and nested repeaters", () => {
    const document = validDocument();
    const second = node({ id: ids.secondGroup, kind: "group", geometry: { childIds: [ids.group, ids.primitive] } });
    (second as { layerId: string }).layerId = ids.e4Layer;
    (document.views[0]!.layers[0]!.nodes as TemplateNodeV2[]).push(second);
    (document.views[0]!.layers[0]!.nodes[1] as { geometry: { childIds: string[] } }).geometry.childIds.push(ids.secondGroup);
    document.repeaters.push({ ...document.repeaters[0]!, id: ids.secondRepeater });
    document.views[0]!.repeatPlacements.push({ repeatDomainId: ids.secondRepeater, prototypeGroupId: ids.secondGroup, step: { x: c(0), y: c(10) }, contactPointIds: [ids.e4Point] });
    expect(codes(document)).toEqual(expect.arrayContaining(["group_cycle", "repeated_ownership", "nested_repeater"]));
  });

  it("rejects duplicate IDs, an absent drawing view and an unsupported image", () => {
    const document = validDocument();
    document.views[1]!.id = ids.e4;
    document.views[1]!.kind = "additional";
    document.assets[0]!.mediaType = "image/svg+xml";
    expect(codes(document)).toEqual(expect.arrayContaining(["duplicate_id", "required_views", "asset_media_type"]));
  });

  it("uses only PNG assets within the backend 10 MiB limit", () => {
    const document = validDocument();
    document.assets[0]!.mediaType = "image/jpeg";
    document.assets[0]!.sizeBytes = 10 * 1024 * 1024 + 1;
    expect(codes(document)).toEqual(expect.arrayContaining(["asset_media_type", "asset_size"]));
  });

  it("derives the same cross-view occurrence key without allocating IDs", () => {
    const e4Key = repeatOccurrenceKeyV2(ids.repeater, 7, ids.contact);
    const drawingKey = repeatOccurrenceKeyV2(ids.repeater, 7, ids.contact);
    expect(e4Key).toBe(drawingKey);
    expect(e4Key).toBe(`${ids.repeater}:7:${ids.contact}`);
  });

  it("accepts at most the backend limit of 64 assets", () => {
    const document = validDocument();
    for (let index = 1; index < 64; index++) {
      document.assets.push({
        ...document.assets[0]!,
        assetId: `10000000-0000-4000-8000-${index.toString(16).padStart(12, "0")}`,
        fileName: `asset-${index}.png`,
      });
    }
    expect(validateTemplateContentV2(document).valid).toBe(true);
    document.assets.push({
      ...document.assets[0]!,
      assetId: "10000000-0000-4000-8000-000000000040",
      fileName: "asset-64.png",
    });
    expect(codes(document)).toContain("limit");
  });
});

describe("deterministic template v1 to v2 upgrade", () => {
  const v1 = () => ({ schemaVersion: 1, views: [
    { id: ids.e4, kind: "e4", name: "Э4", primitives: [{ id: ids.primitive, kind: "line", x: 10, y: 20, width: 30, height: 40, color: "#123456", text: "" }], contactPoints: [{ id: ids.e4Point, name: "Контакт 1", contactNumber: "1", direction: "right", x: 50, y: 60 }] },
    { id: ids.drawing, kind: "drawing", name: "Чертёж", primitives: [], contactPoints: [{ id: ids.drawingPoint, name: "Контакт 1", contactNumber: "1", direction: "left", x: 70, y: 80 }] },
  ] });

  it("preserves stable IDs and coordinates and maps a unique number across views", () => {
    const result = upgradeTemplateContentV1ToV2(v1());
    const line = result.content.views[0]!.layers[0]!.nodes[0] as { id: string; geometry: { points: Array<{ x: { value: number }; y: { value: number } }> } };
    expect(result.content.views.map(view => view.id)).toEqual([ids.e4, ids.drawing]);
    expect(line.id).toBe(ids.primitive);
    expect(line.geometry.points).toEqual([{ x: c(10), y: c(20) }, { x: c(40), y: c(60) }]);
    expect(result.content.views[0]!.contactPoints[0]!.id).toBe(ids.e4Point);
    expect(result.content.views[0]!.contactPoints[0]!.logicalContactId).toBe(result.content.views[1]!.contactPoints[0]!.logicalContactId);
    expect(result.diagnostics).toEqual([{ code: "logical_contact_mapping_inferred", path: "$.views[1].contactPoints[0]", message: expect.any(String) }]);
    expect(validateTemplateContentV2(result.content).valid).toBe(true);
  });

  it("is byte-for-byte deterministic and diagnoses ambiguous contact mapping", () => {
    const legacy = structuredClone(v1()) as unknown as { views: Array<{ contactPoints: Array<{ id: string; name: string; contactNumber: string; direction: string; x: number; y: number }> }> };
    const duplicate = { ...legacy.views[0]!.contactPoints[0]!, id: "legacy-point", x: 55 };
    legacy.views[0]!.contactPoints.push(duplicate);
    const first = upgradeTemplateContentV1ToV2(legacy);
    const second = upgradeTemplateContentV1ToV2(structuredClone(legacy));
    expect(JSON.stringify(first)).toBe(JSON.stringify(second));
    expect(first.diagnostics.map(item => item.code)).toEqual(expect.arrayContaining(["logical_contact_mapping_ambiguous", "legacy_id_replaced"]));
    expect(first.content.views[0]!.contactPoints[0]!.logicalContactId).not.toBe(first.content.views[0]!.contactPoints[1]!.logicalContactId);
  });
});
