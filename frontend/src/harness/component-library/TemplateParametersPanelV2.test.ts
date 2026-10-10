import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  TemplateParametersPanelV2,
  availableNodeDimensionsV2,
  availableRepeatContactPointsV2,
  canUseNodeForRepeatPlacementV2,
  canCreateRepeatV2,
  calculatedRepeatContactCountV2,
  isTopLevelNodeV2,
  missingRepeatLogicalContactIdsV2,
  parseRepeatCountV2,
  parseRepeatStepV2,
  parseTemplateDimensionRangeV2,
  parseTemplateParameterDefaultV2,
  parameterizeDimensionFormKeyV2,
  repeatPlacementViewNamesV2,
  repeatPreviewValuesV2,
  unplacedRepeatDomainsV2,
  type TemplateParametersPanelV2Props,
} from "./TemplateParametersPanelV2";
import type { NumericExpressionV2, TemplateContentV2, TemplateNodeV2, TransformV2 } from "./template-model-v2";

const ids = {
  e4: "00000000-0000-4000-8000-000000000001", drawing: "00000000-0000-4000-8000-000000000002",
  layer: "00000000-0000-4000-8000-000000000003", drawingLayer: "00000000-0000-4000-8000-000000000004",
  node: "00000000-0000-4000-8000-000000000005", child: "00000000-0000-4000-8000-000000000006",
  group: "00000000-0000-4000-8000-000000000007", logical: "00000000-0000-4000-8000-000000000008",
  point: "00000000-0000-4000-8000-000000000009", parameter: "00000000-0000-4000-8000-00000000000a",
  domain: "00000000-0000-4000-8000-00000000000b",
  logical2: "00000000-0000-4000-8000-00000000000c", point2: "00000000-0000-4000-8000-00000000000d",
  size: "00000000-0000-4000-8000-00000000000e", ellipse: "00000000-0000-4000-8000-00000000000f",
  drawingNode: "00000000-0000-4000-8000-000000000010", drawingPoint: "00000000-0000-4000-8000-000000000011",
} as const;

const c = (value: number): NumericExpressionV2 => ({ kind: "constant", value });
const transform: TransformV2 = { translateX: c(0), translateY: c(0), rotationDegrees: c(0), scaleX: c(1), scaleY: c(1) };
const line = (id: string): TemplateNodeV2 => ({
  id, kind: "line", layerId: ids.layer, visible: true, locked: false, opacity: 1, transform,
  stroke: { color: "#123456", width: c(1) }, fill: { color: null },
  geometry: { points: [{ x: c(0), y: c(0) }, { x: c(20), y: c(0) }], bendRadius: c(0) },
});

function fixture(withRepeat = false): TemplateContentV2 {
  const child = line(ids.child);
  const group: TemplateNodeV2 = { ...line(ids.group), kind: "group", geometry: { childIds: [ids.child] } };
  return {
    schemaVersion: 2,
    views: [
      { id: ids.e4, name: "Схема Э4", kind: "e4", layers: [{ id: ids.layer, name: "Основной", visible: true, locked: false, nodes: [line(ids.node), child, group] }], contactPoints: [{ id: ids.point, logicalContactId: ids.logical, x: c(20), y: c(0), direction: "right" }, { id: ids.point2, logicalContactId: ids.logical2, x: c(20), y: c(20), direction: "right" }], bundlePorts: [], repeatPlacements: withRepeat ? [{ repeatDomainId: ids.domain, prototypeGroupId: ids.group, step: { x: c(0), y: c(12) }, contactPointIds: [ids.point] }] : [] },
      { id: ids.drawing, name: "Чертёж", kind: "drawing", layers: [{ id: ids.drawingLayer, name: "Основной", visible: true, locked: false, nodes: [] }], contactPoints: [], bundlePorts: [], repeatPlacements: [] },
    ],
    logicalContacts: [{ id: ids.logical, number: "1", name: "Сигнал", contactType: "signal" }, { id: ids.logical2, number: "2", name: "Питание", contactType: "power" }],
    parameters: withRepeat ? [{ id: ids.parameter, name: "Контакты", type: "integer", unit: "шт", defaultValue: 10, minimum: 1, maximum: 1000, formula: null }] : [],
    repeaters: withRepeat ? [{ id: ids.domain, countParameterId: ids.parameter, logicalContactIds: [ids.logical] }] : [],
    assets: [], articleParameterPresets: [],
  };
}

const callbacks = (): Omit<TemplateParametersPanelV2Props, "content" | "activeViewId" | "activeLayerId" | "selectedNodeId"> => ({
  onCreateRepeat: vi.fn(), onSetCount: vi.fn(), onSetStep: vi.fn(), onDeleteRepeat: vi.fn(), onPreviewValues: vi.fn(),
  onParameterizeNodeDimension: vi.fn(), onSetParameterDefault: vi.fn(),
});

function render(content: TemplateContentV2, selectedNodeId: string | null): string {
  return renderToStaticMarkup(createElement(TemplateParametersPanelV2, {
    content, activeViewId: ids.e4, activeLayerId: ids.layer, selectedNodeId, ...callbacks(),
  }));
}

describe("TemplateParametersPanelV2", () => {
  it("shows an empty state until a top-level object is selected", () => {
    const document = fixture();
    expect(render(document, null)).toContain("Выберите объект верхнего уровня");
    expect(render(document, ids.child)).toContain("Выберите объект верхнего уровня");
    expect(isTopLevelNodeV2(document, ids.e4, ids.layer, ids.child)).toBe(false);
  });

  it("renders a ready creation form with active-view contacts and default values", () => {
    const markup = render(fixture(), ids.node);
    expect(markup).toContain("Новый повторяемый сегмент");
    expect(markup).toContain("1 · Сигнал");
    expect(markup).toContain('value="2"');
    expect(markup).toContain('value="20"');
    expect(markup).toContain("Сделать повторяемым");
    expect(markup).toContain("Вычислено контактов: 2");
    expect(markup).not.toContain("disabled=\"\"");
  });

  it("renders existing count and active-view placement with deterministic contact total", () => {
    const markup = render(fixture(true), ids.node);
    expect(markup).toContain("Контакты");
    expect(markup).toContain("Размещён в активном виде");
    expect(markup).toContain('value="10"');
    expect(markup).toContain('value="12"');
    expect(markup).toContain("Вычислено контактов: 10");
    expect(markup).toContain("Быстрый выбор количества");
    expect(markup).toContain("Охват: 1 · Схема Э4");
    expect(markup).toContain("Новый повторяемый сегмент");
    expect(markup).toContain("2 · Питание");
    expect(markup).not.toContain("1 · Сигнал");
    expect(markup).toContain("из всех видов");
  });

  it("offers an existing repeat domain in another view when its logical point and a prototype are ready", () => {
    const document = fixture(true);
    document.views[1]!.layers[0]!.nodes.push({ ...line(ids.drawingNode), layerId: ids.drawingLayer });
    document.views[1]!.contactPoints.push({ id: ids.drawingPoint, logicalContactId: ids.logical, x: c(20), y: c(0), direction: "left" });
    const markup = renderToStaticMarkup(createElement(TemplateParametersPanelV2, {
      content: document, activeViewId: ids.drawing, activeLayerId: ids.drawingLayer, selectedNodeId: ids.drawingNode,
      ...callbacks(), onPlaceRepeatInActiveView: vi.fn(),
    }));

    expect(markup).toContain('aria-label="Разместить повтор в этом виде"');
    expect(markup).toContain("Контакты · 1 конт.");
    expect(markup).toContain('value="0"');
    expect(markup).toContain('value="20"');
    expect(markup).toContain('<button type="submit">Разместить повтор в этом виде</button>');
    expect(markup).not.toContain("Сначала разместите в этом виде точки логических контактов домена");
  });

  it("explains missing logical points and disables placement until they exist in the active view", () => {
    const document = fixture(true);
    document.views[1]!.layers[0]!.nodes.push({ ...line(ids.drawingNode), layerId: ids.drawingLayer });
    const markup = renderToStaticMarkup(createElement(TemplateParametersPanelV2, {
      content: document, activeViewId: ids.drawing, activeLayerId: ids.drawingLayer, selectedNodeId: ids.drawingNode,
      ...callbacks(), onPlaceRepeatInActiveView: vi.fn(),
    }));

    expect(markup).toContain("Сначала разместите в этом виде точки логических контактов домена: 1 · Сигнал.");
    expect(markup).toContain('<button type="submit" disabled="">Разместить повтор в этом виде</button>');
  });

  it("renders the resolved formula count as read-only", () => {
    const document = fixture(true);
    document.parameters[0] = { ...document.parameters[0]!, defaultValue: 0, formula: c(2) };
    const setCount = vi.fn();
    const markup = renderToStaticMarkup(createElement(TemplateParametersPanelV2, {
      content: document, activeViewId: ids.e4, activeLayerId: ids.layer, selectedNodeId: null,
      ...callbacks(), onSetCount: setCount,
    }));
    expect(markup).toContain('value="2"');
    expect(markup).toContain("Вычислено контактов: 2");
    expect(markup).toContain("Задано формулой; редактор формул пока только для чтения");
    expect(markup.match(/disabled=""/g)).toHaveLength(3);
    expect(setCount).not.toHaveBeenCalled();
  });

  it("lists named dimensions separately from repeat counts and resolves formula values", () => {
    const document = fixture(true);
    document.parameters.push({ id: ids.size, name: "Ширина корпуса", type: "number", unit: "мм", defaultValue: 0, minimum: 0, maximum: 100, formula: c(42) });
    const markup = render(document, null);
    expect(markup).toContain("Именованные размеры");
    expect(markup).toContain("Ширина корпуса");
    expect(markup).toContain('value="42"');
    expect(markup).toContain("по формуле");
    expect(markup.match(/template-parameter-v2-row/g)).toHaveLength(1);
  });

  it("offers only constant dimensions of the selected rectangle", () => {
    const document = fixture();
    const rectangle: TemplateNodeV2 = {
      ...line(ids.ellipse), kind: "rectangle",
      geometry: { x: c(0), y: c(0), width: c(80), height: { kind: "parameter", parameterId: ids.size }, cornerRadii: [c(0), c(0), c(0), c(0)] },
    };
    document.views[0]!.layers[0]!.nodes.push(rectangle);
    const markup = render(document, rectangle.id);
    expect(markup).toContain("Параметризовать ширину");
    expect(markup).not.toContain("Параметризовать высоту");
    expect(markup).toContain('value="80"');
    expect(markup).toContain('value="0.001"');
    expect(markup).toContain('value="1000000"');
    expect(markup).toContain("Создать параметр");
  });

  it("resets the dimension form when selection moves from width 80 to width 140", () => {
    const first = { ...line(crypto.randomUUID()), kind: "rectangle", geometry: { x: c(0), y: c(0), width: c(80), height: c(50), cornerRadii: [c(0), c(0), c(0), c(0)] } } as TemplateNodeV2;
    const second = { ...first, id: crypto.randomUUID(), geometry: { ...first.geometry, width: c(140) } } as TemplateNodeV2;
    const firstDimensions = availableNodeDimensionsV2(first), secondDimensions = availableNodeDimensionsV2(second);
    expect(parameterizeDimensionFormKeyV2(first.id, firstDimensions)).not.toBe(parameterizeDimensionFormKeyV2(second.id, secondDimensions));

    const firstDocument = fixture(), secondDocument = fixture();
    firstDocument.views[0]!.layers[0]!.nodes.push(first);
    secondDocument.views[0]!.layers[0]!.nodes.push(second);
    expect(render(firstDocument, first.id)).toContain('value="80"');
    expect(render(secondDocument, second.id)).toContain('value="140"');
  });

  it("resets the form to the height value after width becomes parameterized", () => {
    const nodeId = crypto.randomUUID();
    const before = { ...line(nodeId), kind: "rectangle", geometry: { x: c(0), y: c(0), width: c(80), height: c(50), cornerRadii: [c(0), c(0), c(0), c(0)] } } as TemplateNodeV2;
    const after = { ...before, geometry: { ...before.geometry, width: { kind: "parameter", parameterId: ids.size } } } as TemplateNodeV2;
    const beforeDimensions = availableNodeDimensionsV2(before), afterDimensions = availableNodeDimensionsV2(after);
    expect(parameterizeDimensionFormKeyV2(nodeId, beforeDimensions)).not.toBe(parameterizeDimensionFormKeyV2(nodeId, afterDimensions));
    expect(afterDimensions).toEqual([{ dimension: "height", label: "Параметризовать высоту", defaultValue: 50 }]);

    const document = fixture();
    document.views[0]!.layers[0]!.nodes.push(after);
    const markup = render(document, nodeId);
    expect(markup).toContain("Параметризовать высоту");
    expect(markup).toContain('value="50"');
    expect(markup).not.toContain('value="80"');
  });

  it("uses the selected node actual layer for dimension controls", () => {
    const document = fixture();
    const secondLayerId = crypto.randomUUID();
    const rectangle: TemplateNodeV2 = {
      ...line(ids.ellipse), layerId: secondLayerId, kind: "rectangle",
      geometry: { x: c(0), y: c(0), width: c(80), height: c(50), cornerRadii: [c(0), c(0), c(0), c(0)] },
    };
    document.views[0]!.layers.push({ id: secondLayerId, name: "Видимый второй", visible: true, locked: false, nodes: [rectangle] });
    const markup = renderToStaticMarkup(createElement(TemplateParametersPanelV2, {
      content: document, activeViewId: ids.e4, activeLayerId: secondLayerId, selectedNodeId: rectangle.id, ...callbacks(),
    }));
    expect(markup).toContain("Размер выбранного объекта");
    expect(markup).toContain("Параметризовать ширину");
  });
});

describe("TemplateParametersPanelV2 helpers", () => {
  it("validates repeat counts without coercing fractions or out-of-range values", () => {
    expect(parseRepeatCountV2("2")).toBe(2);
    expect(parseRepeatCountV2(" 1000 ")).toBe(1000);
    for (const value of ["", "0", "-1", "1.5", "1001", "text", Number.NaN]) expect(parseRepeatCountV2(value)).toBeNull();
  });

  it("validates finite bounded steps and calculated contact totals", () => {
    expect(parseRepeatStepV2("-12.5")).toBe(-12.5);
    expect(parseRepeatStepV2(1_000_000)).toBe(1_000_000);
    expect(parseRepeatStepV2(" ")).toBeNull();
    expect(parseRepeatStepV2(1_000_001)).toBeNull();
    expect(parseRepeatStepV2(Number.POSITIVE_INFINITY)).toBeNull();
    expect(calculatedRepeatContactCountV2(10, 3)).toBe(30);
    expect(calculatedRepeatContactCountV2(0, 3)).toBeNull();
    expect(calculatedRepeatContactCountV2(2, -1)).toBeNull();
  });

  it("builds a complete immutable preview for repeat count parameters", () => {
    const document = fixture(true);
    const before = structuredClone(document);
    expect(repeatPreviewValuesV2(document, ids.parameter, 2)).toEqual({ [ids.parameter]: 2 });
    expect(document).toEqual(before);
  });

  it("excludes used prototype trees and contact points while allowing another domain", () => {
    const document = fixture(true);
    expect(canCreateRepeatV2(document, ids.e4, ids.layer, ids.node)).toBe(true);
    expect(canCreateRepeatV2(document, ids.e4, ids.layer, ids.group)).toBe(false);
    expect(canCreateRepeatV2(document, ids.e4, ids.layer, ids.child)).toBe(false);
    expect(availableRepeatContactPointsV2(document, ids.e4).map(point => point.id)).toEqual([ids.point2]);
  });

  it("reports every placement affected by domain deletion, including other views", () => {
    const document = fixture(true);
    document.views[1]!.repeatPlacements.push({ repeatDomainId: ids.domain, prototypeGroupId: ids.group, step: { x: c(10), y: c(0) }, contactPointIds: [] });
    expect(repeatPlacementViewNamesV2(document, ids.domain)).toEqual(["Схема Э4", "Чертёж"]);
    expect(repeatPlacementViewNamesV2(document, crypto.randomUUID())).toEqual([]);
  });

  it("keeps domain-wide deletion available when the active view has no placement", () => {
    const document = fixture(true);
    document.views[0]!.repeatPlacements = [];
    document.views[1]!.repeatPlacements.push({ repeatDomainId: ids.domain, prototypeGroupId: ids.group, step: { x: c(10), y: c(0) }, contactPointIds: [] });
    const markup = render(document, null);
    expect(markup).toContain("Нет размещения в активном виде");
    expect(markup).toContain("Охват: 1 · Чертёж");
    expect(markup).toContain(`aria-label="Удалить повтор Контакты из всех видов"`);
    expect(markup).not.toContain('aria-label="Разместить повтор в этом виде"');
  });

  it("finds domains absent from a view and checks their logical point coverage", () => {
    const document = fixture(true);
    expect(unplacedRepeatDomainsV2(document, ids.e4)).toEqual([]);
    expect(unplacedRepeatDomainsV2(document, ids.drawing).map(domain => domain.id)).toEqual([ids.domain]);
    expect(missingRepeatLogicalContactIdsV2(document, ids.drawing, ids.domain)).toEqual([ids.logical]);
    document.views[1]!.contactPoints.push({ id: ids.drawingPoint, logicalContactId: ids.logical, x: c(0), y: c(0), direction: "left" });
    expect(missingRepeatLogicalContactIdsV2(document, ids.drawing, ids.domain)).toEqual([]);
  });

  it("accepts only an editable free top-level node as a cross-view repeat prototype", () => {
    const document = fixture(true);
    document.views[1]!.layers[0]!.nodes.push({ ...line(ids.drawingNode), layerId: ids.drawingLayer });
    expect(canUseNodeForRepeatPlacementV2(document, ids.drawing, ids.drawingLayer, ids.drawingNode)).toBe(true);
    document.views[1]!.layers[0]!.locked = true;
    expect(canUseNodeForRepeatPlacementV2(document, ids.drawing, ids.drawingLayer, ids.drawingNode)).toBe(false);
  });

  it("detects parameterizable dimensions and validates editable defaults", () => {
    const ellipse: TemplateNodeV2 = { ...line(ids.ellipse), kind: "ellipse", geometry: { centerX: c(0), centerY: c(0), radiusX: c(10), radiusY: { kind: "parameter", parameterId: ids.size } } };
    expect(availableNodeDimensionsV2(ellipse)).toEqual([{ dimension: "radiusX", label: "Параметризовать радиус X", defaultValue: 10 }]);
    const numberParameter = { id: ids.size, name: "Размер", type: "number", unit: "мм", defaultValue: 10, minimum: 0, maximum: 20, formula: null } as const;
    const integerParameter = { ...numberParameter, type: "integer" as const };
    expect(parseTemplateParameterDefaultV2("12.5", numberParameter)).toBe(12.5);
    expect(parseTemplateParameterDefaultV2("12.5", integerParameter)).toBeNull();
    expect(parseTemplateParameterDefaultV2("21", numberParameter)).toBeNull();
  });

  it("validates a complete custom dimension range", () => {
    expect(parseTemplateDimensionRangeV2("50", "10", "100")).toEqual({ defaultValue: 50, minimum: 10, maximum: 100 });
    expect(parseTemplateDimensionRangeV2("9", "10", "100")).toBeNull();
    expect(parseTemplateDimensionRangeV2("50", "100", "10")).toBeNull();
    expect(parseTemplateDimensionRangeV2("50", "0", "100")).toBeNull();
    expect(parseTemplateDimensionRangeV2("50", "10", "1000001")).toBeNull();
    expect(parseTemplateDimensionRangeV2("", "10", "100")).toBeNull();
  });
});
