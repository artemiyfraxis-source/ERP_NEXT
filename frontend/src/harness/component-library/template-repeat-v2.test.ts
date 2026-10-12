// @vitest-environment node
import { describe, expect, it } from "vitest";
import type {
  GroupNodeV2,
  NumericExpressionV2,
  ParameterValueV2,
  TemplateContentV2,
} from "./template-model-v2";
import { repeatOccurrenceKeyV2 } from "./template-model-v2";
import {
  expandRepeatPlacementV2,
  expandTemplateRepeatsV2,
  expandTemplateViewRepeatsV2,
  resolveTemplateParameterValuesV2,
  TemplateRepeatV2Error,
} from "./template-repeat-v2";

const ids = {
  view: "00000000-0000-4000-8000-000000000001",
  drawing: "00000000-0000-4000-8000-000000000002",
  layer: "00000000-0000-4000-8000-000000000003",
  drawingLayer: "00000000-0000-4000-8000-000000000004",
  group: "00000000-0000-4000-8000-000000000005",
  segment: "00000000-0000-4000-8000-000000000012",
  count: "00000000-0000-4000-8000-000000000006",
  pitch: "00000000-0000-4000-8000-000000000007",
  doublePitch: "00000000-0000-4000-8000-000000000008",
  enabled: "00000000-0000-4000-8000-000000000009",
  label: "00000000-0000-4000-8000-00000000000a",
  domain: "00000000-0000-4000-8000-00000000000b",
  contact1: "00000000-0000-4000-8000-00000000000c",
  contact2: "00000000-0000-4000-8000-00000000000d",
  point1: "00000000-0000-4000-8000-00000000000e",
  point2: "00000000-0000-4000-8000-00000000000f",
  preset2: "00000000-0000-4000-8000-000000000010",
  preset10: "00000000-0000-4000-8000-000000000011",
} as const;

const c = (value: number): NumericExpressionV2 => ({ kind: "constant", value });
const p = (parameterId: string): NumericExpressionV2 => ({ kind: "parameter", parameterId });

function fixture(): TemplateContentV2 {
  const segment = {
    id: ids.segment, kind: "line" as const, layerId: ids.layer, visible: true, locked: false, opacity: 1,
    transform: { translateX: c(0), translateY: c(0), rotationDegrees: c(0), scaleX: c(1), scaleY: c(1) },
    stroke: { color: "#112233", width: c(2) }, fill: { color: null },
    geometry: { points: [{ x: c(0), y: c(0) }, { x: c(10), y: c(0) }], bendRadius: c(0) },
  };
  const group = {
    id: ids.group, kind: "group" as const, layerId: ids.layer, visible: true, locked: false, opacity: 1,
    transform: { translateX: c(0), translateY: c(0), rotationDegrees: c(0), scaleX: c(1), scaleY: c(1) },
    stroke: { color: "#112233", width: c(0) }, fill: { color: null }, geometry: { childIds: [ids.segment] },
  };
  return {
    schemaVersion: 2,
    parameters: [
      { id: ids.count, name: "Контакты", type: "integer", unit: "шт", defaultValue: 2, minimum: 1, maximum: 1_000, formula: null },
      { id: ids.pitch, name: "Шаг", type: "number", unit: "мм", defaultValue: 2.5, minimum: 0.1, maximum: 100, formula: null },
      { id: ids.doublePitch, name: "Двойной шаг", type: "number", unit: "мм", defaultValue: 0, minimum: 0.1, maximum: 200, formula: { kind: "binary", operator: "multiply", left: p(ids.pitch), right: c(2) } },
      { id: ids.enabled, name: "Флаг", type: "boolean", unit: null, defaultValue: true, minimum: null, maximum: null, formula: null },
      { id: ids.label, name: "Метка", type: "string", unit: null, defaultValue: "XH", minimum: null, maximum: null, formula: null },
    ],
    logicalContacts: [
      { id: ids.contact1, number: "1", name: "Сигнал", contactType: "signal" },
      { id: ids.contact2, number: "A", name: "Сервис", contactType: "service" },
    ],
    repeaters: [{ id: ids.domain, countParameterId: ids.count, logicalContactIds: [ids.contact1, ids.contact2] }],
    articleParameterPresets: [
      { id: ids.preset2, sourceId: "test", entityType: "connector", articleKey: "2P", values: [{ parameterId: ids.count, value: 2 }] },
      { id: ids.preset10, sourceId: "test", entityType: "connector", articleKey: "10P", values: [{ parameterId: ids.count, value: 10 }, { parameterId: ids.pitch, value: 3 }] },
    ],
    assets: [],
    views: [
      {
        id: ids.view,
        name: "Э4",
        kind: "e4",
        layers: [{ id: ids.layer, name: "Основной", visible: true, locked: false, nodes: [segment, group] }],
        bundlePorts: [],
        contactPoints: [
          { id: ids.point2, logicalContactId: ids.contact2, x: c(1), y: c(4), direction: "right" },
          { id: ids.point1, logicalContactId: ids.contact1, x: c(1), y: c(2), direction: "right" },
        ],
        repeatPlacements: [],
      },
      { id: ids.drawing, name: "Чертёж", kind: "drawing", layers: [{ id: ids.drawingLayer, name: "Основной", visible: true, locked: false, nodes: [] }], contactPoints: [], bundlePorts: [], repeatPlacements: [] },
    ],
  };
}

const placement = {
  repeatDomainId: ids.domain,
  prototypeGroupId: ids.group,
  step: { x: p(ids.doublePitch), y: c(1) },
  // Deliberately reversed: logicalContactIds defines the canonical contact order.
  contactPointIds: [ids.point2, ids.point1],
};

function withPlacement(content: TemplateContentV2): TemplateContentV2 {
  content.views[0]!.repeatPlacements.push(placement);
  return content;
}

describe("template v2 parameter resolution", () => {
  it("applies defaults, an article preset, formulas and explicit overrides in order", () => {
    const content = withPlacement(fixture());
    const original = structuredClone(content);
    const values = resolveTemplateParameterValuesV2(content, {
      articlePreset: ids.preset10,
      overrides: new Map<string, ParameterValueV2>([[ids.pitch, 4]]),
    });

    expect(Object.fromEntries(values)).toEqual({
      [ids.count]: 10,
      [ids.pitch]: 4,
      [ids.doublePitch]: 8,
      [ids.enabled]: true,
      [ids.label]: "XH",
    });
    expect(content).toEqual(original);
  });

  it("rejects a wrong type, an out-of-range number and a fractional integer", () => {
    const content = withPlacement(fixture());
    expect(() => resolveTemplateParameterValuesV2(content, { overrides: { [ids.enabled]: "yes" } })).toThrowError(TemplateRepeatV2Error);
    expect(() => resolveTemplateParameterValuesV2(content, { overrides: { [ids.pitch]: 101 } })).toThrowError(expect.objectContaining({ code: "parameter_range" }));
    expect(() => resolveTemplateParameterValuesV2(content, { overrides: { [ids.count]: 2.5 } })).toThrowError(expect.objectContaining({ code: "parameter_value" }));
  });

  it("materializes every parameter even when no view contains repeat placements", () => {
    const content = fixture();
    content.repeaters = [];
    content.parameters[1] = { ...content.parameters[1]!, defaultValue: 101 };
    expect(() => expandTemplateRepeatsV2(content)).toThrowError(expect.objectContaining({ code: "parameter_range" }));

    const division = fixture();
    division.repeaters = [];
    division.parameters[2] = {
      ...division.parameters[2]!,
      formula: { kind: "binary", operator: "divide", left: c(1), right: c(0) },
    };
    expect(() => expandTemplateRepeatsV2(division)).toThrowError(expect.objectContaining({ code: "division_by_zero" }));

    const boundary = fixture();
    boundary.repeaters = [];
    boundary.parameters[1] = { ...boundary.parameters[1]!, defaultValue: 100 };
    expect(expandTemplateRepeatsV2(JSON.parse(JSON.stringify(boundary)) as TemplateContentV2)).toHaveLength(2);
  });
});

describe("template v2 repeat expansion", () => {
  it.each([[2], [10]])("expands %i groups and contact point sets deterministically", count => {
    const content = withPlacement(fixture());
    const domain = content.repeaters[0]!;
    const first = expandRepeatPlacementV2(content, domain, placement, { overrides: { [ids.count]: count } });
    const second = expandRepeatPlacementV2(content, domain, placement, { overrides: { [ids.count]: count } });

    expect(first).toEqual(second);
    expect(first).toHaveLength(count);
    expect(first.flatMap(item => item.contactPoints)).toHaveLength(count * 2);
    expect(first.flatMap(item => item.nodes)).toHaveLength(count);
    expect(first[0]!.key).toBe(repeatOccurrenceKeyV2(ids.domain, 0, ids.group));
    expect(first[count - 1]!.contactPoints[0]!.key).toBe(repeatOccurrenceKeyV2(ids.domain, count - 1, ids.contact1));
    expect(first[0]!.contactPoints.map(point => point.number)).toEqual(["1", "A-1"]);
    expect(first[0]!.nodes[0]).toMatchObject({
      key: repeatOccurrenceKeyV2(ids.domain, 0, ids.segment), prototypeNodeId: ids.segment,
    });
    expect(first[1]!.contactPoints.map(point => point.number)).toEqual(["3", "A-2"]);
    expect(first[count - 1]!.offset).toEqual({ x: (count - 1) * 5, y: count - 1 });
    expect(first[count - 1]!.contactPoints[0]).toMatchObject({ x: 1 + (count - 1) * 5, y: 2 + count - 1 });
  });

  it("rebuilds offsets from preset/formula values and an explicit step override", () => {
    const content = withPlacement(fixture());
    const domain = content.repeaters[0]!;
    const fromPreset = expandRepeatPlacementV2(content, domain, placement, { articlePreset: ids.preset10 });
    const overridden = expandRepeatPlacementV2(content, domain, placement, {
      articlePreset: ids.preset2,
      overrides: { [ids.pitch]: 7 },
    });

    expect(fromPreset).toHaveLength(10);
    expect(fromPreset[9]!.offset).toEqual({ x: 54, y: 9 });
    expect(overridden[1]!.offset).toEqual({ x: 14, y: 1 });
  });

  it.each([0, -1, 2.5, 1_001])("rejects invalid count %s atomically with the same error", count => {
    const content = withPlacement(fixture());
    const original = structuredClone(content);
    let thrown: unknown;
    try {
      expandRepeatPlacementV2(content, content.repeaters[0]!, placement, { overrides: { [ids.count]: count } });
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toBeInstanceOf(TemplateRepeatV2Error);
    expect(thrown).toMatchObject({ code: "repeater_count" });
    expect(content).toEqual(original);
  });

  it("rejects a missing prototype group before returning any occurrences", () => {
    const content = fixture();
    const missing = {
      ...placement,
      prototypeGroupId: "00000000-0000-4000-8000-000000000099",
    };
    content.views[0]!.repeatPlacements.push(missing);
    expect(() => expandRepeatPlacementV2(content, content.repeaters[0]!, missing))
      .toThrowError(expect.objectContaining({ code: "prototype_group_missing" }));
  });

  it("expands all placements of a view through the aggregate entry point", () => {
    const content = withPlacement(fixture());
    const expanded = expandTemplateViewRepeatsV2(content, ids.view, { overrides: { [ids.count]: 10 } });

    expect(expanded).toHaveLength(1);
    expect(expanded[0]!.occurrences).toHaveLength(10);
  });

  it("assigns stable occurrence descriptors to every nested prototype node", () => {
    const content = withPlacement(fixture());
    const layer = content.views[0]!.layers[0]!;
    const root = layer.nodes.find(node => node.id === ids.group)!;
    if (root.kind !== "group") throw new Error("Expected root prototype group.");
    const nestedId = "00000000-0000-4000-8000-000000000013";
    layer.nodes.push({ ...root, id: nestedId, geometry: { childIds: [ids.segment] } });
    root.geometry.childIds = [nestedId];

    const occurrence = expandRepeatPlacementV2(content, content.repeaters[0]!, placement)[0]!;

    expect(occurrence.nodes.map(node => node.prototypeNodeId)).toEqual([nestedId, ids.segment]);
    expect(occurrence.nodes.map(node => node.key)).toEqual([
      repeatOccurrenceKeyV2(ids.domain, 0, nestedId),
      repeatOccurrenceKeyV2(ids.domain, 0, ids.segment),
    ]);
  });

  it("does not count prototype points as additional ordinary contacts", () => {
    const content = withPlacement(fixture());
    const expanded = expandTemplateViewRepeatsV2(content, ids.view);
    expect(expanded[0]!.occurrences.flatMap(occurrence => occurrence.contactPoints).map(point => point.number))
      .toEqual(["1", "A-1", "3", "A-2"]);
  });

  it("atomically rejects a displayed-number collision with an ordinary contact", () => {
    const content = withPlacement(fixture());
    const logicalId = "00000000-0000-4000-8000-000000000020";
    const pointId = "00000000-0000-4000-8000-000000000021";
    content.logicalContacts.push({ id: logicalId, number: "3", name: "Обычный", contactType: "signal" });
    content.views[0]!.contactPoints.push({ id: pointId, logicalContactId: logicalId, x: c(20), y: c(20), direction: "left" });
    const original = structuredClone(content);

    expect(() => expandTemplateRepeatsV2(content))
      .toThrowError(expect.objectContaining({ code: "duplicate_expanded_contact_number" }));
    expect(content).toEqual(original);
  });

  it("atomically rejects displayed-number collisions between repeat domains", () => {
    const content = withPlacement(fixture());
    const countId = "00000000-0000-4000-8000-000000000022";
    const domainId = "00000000-0000-4000-8000-000000000023";
    const logicalId = "00000000-0000-4000-8000-000000000024";
    const pointId = "00000000-0000-4000-8000-000000000025";
    const segmentId = "00000000-0000-4000-8000-000000000026";
    const groupId = "00000000-0000-4000-8000-000000000027";
    const firstSegment = content.views[0]!.layers[0]!.nodes.find(node => node.id === ids.segment)!;
    const firstGroup = content.views[0]!.layers[0]!.nodes.find(node => node.id === ids.group)!;
    content.parameters.push({ id: countId, name: "Второй повтор", type: "integer", unit: "шт", defaultValue: 2, minimum: 1, maximum: 1_000, formula: null });
    content.logicalContacts.push({ id: logicalId, number: "3", name: "Другой домен", contactType: "signal" });
    content.repeaters.push({ id: domainId, countParameterId: countId, logicalContactIds: [logicalId] });
    content.views[0]!.layers[0]!.nodes.push(
      { ...firstSegment, id: segmentId },
      { ...(firstGroup as GroupNodeV2), id: groupId, geometry: { childIds: [segmentId] } },
    );
    content.views[0]!.contactPoints.push({ id: pointId, logicalContactId: logicalId, x: c(30), y: c(30), direction: "left" });
    content.views[0]!.repeatPlacements.push({ repeatDomainId: domainId, prototypeGroupId: groupId, step: { x: c(6), y: c(0) }, contactPointIds: [pointId] });
    const original = structuredClone(content);

    expect(() => expandTemplateRepeatsV2(content))
      .toThrowError(expect.objectContaining({ code: "duplicate_expanded_contact_number" }));
    expect(content).toEqual(original);
  });
});
