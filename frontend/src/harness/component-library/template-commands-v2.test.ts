// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  addBasicNodeV2, addBundlePortV2, addContactPointV2, addLayerV2, addNodeV2, attachRepeatDomainV2, constantExpressionV2,
  createRepeatPrototypeV2, deleteContactPointV2, deleteLayerV2, deleteNodeV2,
  deleteBundlePortV2, deleteRepeatPrototypeV2, editBundlePortV2, editContactPointV2, editLogicalContactV2,
  editNodeV2, evaluateNumericExpressionV2, moveNodeV2, resizeNodeV2, newTemplateContentV2, renameLayerV2,
  linkLogicalContactPointV2, parameterizeNodeDimensionV2,
  reorderLayerV2, reorderNodeV2, setLayerLockedV2, setLayerVisibleV2, setNodeLockedV2,
  setRepeatCountV2, setRepeatStepV2, setTemplateParameterDefaultV2,
  TemplateCommandV2Error,
} from "./template-commands-v2";
import { TEMPLATE_V2_LIMITS, validateTemplateContentV2, type GroupNodeV2, type RectangleNodeV2, type TemplateContentV2, type TemplateNodeV2 } from "./template-model-v2";
import { expandTemplateRepeatsV2 } from "./template-repeat-v2";

describe("template v2 immutable commands", () => {
  it("creates stable UUIDs, mandatory views and one Main layer in each", () => {
    const content = newTemplateContentV2();
    const ids = content.views.flatMap(view => [view.id, ...view.layers.map(layer => layer.id)]);

    expect(content.views.map(view => view.kind)).toEqual(["e4", "drawing"]);
    expect(content.views.map(view => view.layers.map(layer => layer.name))).toEqual([["Основной"], ["Основной"]]);
    expect(new Set(ids).size).toBe(4);
    expect(ids.every(id => /^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(id))).toBe(true);
    expect(validateTemplateContentV2(content).valid).toBe(true);
  });

  it("adds, renames, reorders, toggles and deletes layers without mutating input", () => {
    const initial = newTemplateContentV2(), view = initial.views[0]!, original = structuredClone(initial);
    const [added, secondId] = addLayerV2(initial, view.id, "Служебный");
    const renamed = renameLayerV2(added, view.id, secondId, "Контакты");
    const reordered = reorderLayerV2(renamed, view.id, secondId, 0);
    const hidden = setLayerVisibleV2(reordered, view.id, secondId, false);
    const locked = setLayerLockedV2(hidden, view.id, secondId, true);

    expect(initial).toEqual(original);
    expect(locked.views[0]!.layers[0]).toMatchObject({ id: secondId, name: "Контакты", visible: false, locked: true });
    expect(() => renameLayerV2(locked, view.id, secondId, "X")).toThrowError(TemplateCommandV2Error);
    const unlocked = setLayerLockedV2(locked, view.id, secondId, false);
    expect(deleteLayerV2(unlocked, view.id, secondId).views[0]!.layers).toHaveLength(1);
    expect(() => deleteLayerV2(initial, view.id, view.layers[0]!.id)).toThrowError(expect.objectContaining({ code: "last_layer" }));
  });

  it("adds, moves, edits, reorders and deletes basic nodes while respecting locks", () => {
    const initial = newTemplateContentV2(), view = initial.views[0]!, layer = view.layers[0]!;
    const [withRectangle, rectangleId] = addBasicNodeV2(initial, view.id, layer.id, "rectangle");
    const [withText, textId] = addBasicNodeV2(withRectangle, view.id, layer.id, "text");
    const moved = moveNodeV2(withText, view.id, layer.id, rectangleId, 25, -5);
    const edited = editNodeV2(moved, view.id, layer.id, rectangleId, { opacity: 0.4 });
    const reordered = reorderNodeV2(edited, view.id, layer.id, textId, 0);
    const rectangle = reordered.views[0]!.layers[0]!.nodes[1] as RectangleNodeV2;

    expect((rectangle.geometry.x as { value: number }).value).toBe(100);
    expect((rectangle.geometry.y as { value: number }).value).toBe(100);
    expect((rectangle.transform.translateX as { value: number }).value).toBe(25);
    expect((rectangle.transform.translateY as { value: number }).value).toBe(-5);
    expect(rectangle.opacity).toBe(0.4);
    expect(initial.views[0]!.layers[0]!.nodes).toEqual([]);

    const nodeLocked = setNodeLockedV2(reordered, view.id, layer.id, rectangleId, true);
    expect(() => moveNodeV2(nodeLocked, view.id, layer.id, rectangleId, 1, 1)).toThrowError(expect.objectContaining({ code: "node_locked" }));
    const nodeUnlocked = setNodeLockedV2(nodeLocked, view.id, layer.id, rectangleId, false);
    const layerLocked = setLayerLockedV2(nodeUnlocked, view.id, layer.id, true);
    expect(() => deleteNodeV2(layerLocked, view.id, layer.id, rectangleId)).toThrowError(expect.objectContaining({ code: "layer_locked" }));
    const final = deleteNodeV2(setLayerLockedV2(layerLocked, view.id, layer.id, false), view.id, layer.id, rectangleId);
    expect(final.views[0]!.layers[0]!.nodes.map(node => node.id)).toEqual([textId]);
  });

  it("resizes rectangles, ellipses and line endpoints from selection handles", () => {
    const initial = newTemplateContentV2(), view = initial.views[0]!, layer = view.layers[0]!;
    const [withRectangle, rectangleId] = addBasicNodeV2(initial, view.id, layer.id, "rectangle");
    const [withEllipse, ellipseId] = addBasicNodeV2(withRectangle, view.id, layer.id, "ellipse");
    const [withLine, lineId] = addBasicNodeV2(withEllipse, view.id, layer.id, "line");
    const rectangle = resizeNodeV2(withLine, view.id, layer.id, rectangleId, "nw", 10, 5);
    const ellipse = resizeNodeV2(rectangle, view.id, layer.id, ellipseId, "e", 20, 0);
    const resized = resizeNodeV2(ellipse, view.id, layer.id, lineId, "end", 15, -4);
    const nodes = resized.views[0]!.layers[0]!.nodes;
    const rectangleNode = nodes.find(node => node.id === rectangleId)! as RectangleNodeV2;
    const ellipseNode = nodes.find(node => node.id === ellipseId)!;
    const lineNode = nodes.find(node => node.id === lineId)!;

    expect(rectangleNode.geometry.x).toEqual(constantExpressionV2(110));
    expect(rectangleNode.geometry.y).toEqual(constantExpressionV2(105));
    expect(rectangleNode.geometry.width).toEqual(constantExpressionV2(130));
    expect(rectangleNode.geometry.height).toEqual(constantExpressionV2(65));
    expect(ellipseNode.kind === "ellipse" && ellipseNode.geometry.radiusX).toEqual(constantExpressionV2(80));
    expect(lineNode.kind === "line" && lineNode.geometry.points[1]).toEqual({ x: constantExpressionV2(225), y: constantExpressionV2(96) });
    expect(withLine.views[0]!.layers[0]!.nodes.find(node => node.id === rectangleId)).toEqual(withRectangle.views[0]!.layers[0]!.nodes[0]);
  });

  it("rejects invalid, locked and parameterized resize atomically", () => {
    const initial = newTemplateContentV2(), view = initial.views[0]!, layer = view.layers[0]!;
    const [withRectangle, rectangleId] = addBasicNodeV2(initial, view.id, layer.id, "rectangle");
    const before = structuredClone(withRectangle);
    expect(() => resizeNodeV2(withRectangle, view.id, layer.id, rectangleId, "w", 500, 0))
      .toThrowError(expect.objectContaining({ code: "invalid_resize" }));
    expect(withRectangle).toEqual(before);
    const locked = setNodeLockedV2(withRectangle, view.id, layer.id, rectangleId, true);
    expect(() => resizeNodeV2(locked, view.id, layer.id, rectangleId, "e", 10, 0))
      .toThrowError(expect.objectContaining({ code: "node_locked" }));
    const parameterized = parameterizeNodeDimensionV2(withRectangle, view.id, layer.id, rectangleId, "width", {
      name: "Ширина", defaultValue: 140, minimum: 1, maximum: 500,
    })[0];
    expect(() => resizeNodeV2(parameterized, view.id, layer.id, rectangleId, "e", 10, 0))
      .toThrowError(expect.objectContaining({ code: "non_constant_geometry" }));
  });

  it("preserves parameterized geometry and moves the node through its constant transform", () => {
    const initial = newTemplateContentV2(), view = initial.views[0]!, layer = view.layers[0]!;
    const parameterId = crypto.randomUUID();
    const content = { ...initial, parameters: [{
      id: parameterId, name: "X", type: "number" as const, unit: "px", defaultValue: 20,
      minimum: 0, maximum: 100, formula: null,
    }] };
    const advanced: RectangleNodeV2 = {
      id: crypto.randomUUID(), kind: "rectangle", layerId: layer.id, visible: true, locked: false, opacity: 1,
      transform: { translateX: constantExpressionV2(0), translateY: constantExpressionV2(0), rotationDegrees: constantExpressionV2(0), scaleX: constantExpressionV2(1), scaleY: constantExpressionV2(1) },
      stroke: { color: "#123456", width: constantExpressionV2(2) }, fill: { color: null },
      geometry: {
        x: { kind: "parameter", parameterId }, y: constantExpressionV2(10), width: constantExpressionV2(30), height: constantExpressionV2(40),
        cornerRadii: [constantExpressionV2(1), constantExpressionV2(2), constantExpressionV2(3), constantExpressionV2(4)],
      },
    };
    const withAdvanced = addNodeV2(content, view.id, layer.id, advanced);
    const before = JSON.stringify(withAdvanced.views[0]!.layers[0]!.nodes[0]);

    const moved = moveNodeV2(withAdvanced, view.id, layer.id, advanced.id, 5, 5);
    expect((moved.views[0]!.layers[0]!.nodes[0]!.transform.translateX as { value: number }).value).toBe(5);
    expect((moved.views[0]!.layers[0]!.nodes[0]!.transform.translateY as { value: number }).value).toBe(5);
    expect((moved.views[0]!.layers[0]!.nodes[0] as RectangleNodeV2).geometry.x).toEqual({ kind: "parameter", parameterId });
    const edited = editNodeV2(withAdvanced, view.id, layer.id, advanced.id, { visible: false });
    expect(JSON.stringify({ ...edited.views[0]!.layers[0]!.nodes[0], visible: true })).toBe(before);
  });

  it("binds a rectangle width to a canvas-compatible named number parameter and edits its default immutably", () => {
    const initial = newTemplateContentV2(), view = initial.views[0]!, layer = view.layers[0]!;
    const [withRectangle, nodeId] = addBasicNodeV2(initial, view.id, layer.id, "rectangle");
    const original = structuredClone(withRectangle);
    const [parameterized, parameterId] = parameterizeNodeDimensionV2(
      withRectangle, view.id, layer.id, nodeId, "width", { name: " Ширина корпуса ", unit: " мм ", defaultValue: 70, minimum: 10, maximum: 100 },
    );
    const rectangle = parameterized.views[0]!.layers[0]!.nodes[0] as RectangleNodeV2;

    expect(withRectangle).toEqual(original);
    expect(rectangle.geometry.width).toEqual({ kind: "parameter", parameterId });
    expect(parameterized.parameters.at(-1)).toEqual({
      id: parameterId, name: "Ширина корпуса", type: "number", unit: "мм", defaultValue: 70,
      minimum: 10, maximum: 100, formula: null,
    });
    expect(evaluateNumericExpressionV2(rectangle.geometry.width, { [parameterId]: 175 })).toBe(175);

    const edited = setTemplateParameterDefaultV2(parameterized, parameterId, 85);
    expect(edited.parameters.find(parameter => parameter.id === parameterId)?.defaultValue).toBe(85);
    expect(parameterized.parameters.find(parameter => parameter.id === parameterId)?.defaultValue).toBe(70);
    expect((edited.views[0]!.layers[0]!.nodes[0] as RectangleNodeV2).geometry.width).toEqual({ kind: "parameter", parameterId });
    expect(validateTemplateContentV2(edited).valid).toBe(true);
  });

  it("rejects parameter values outside the dimension range and locked nodes atomically", () => {
    const initial = newTemplateContentV2(), view = initial.views[0]!, layer = view.layers[0]!;
    const [withRectangle, nodeId] = addBasicNodeV2(initial, view.id, layer.id, "rectangle");
    const [parameterized, parameterId] = parameterizeNodeDimensionV2(
      withRectangle, view.id, layer.id, nodeId, "height", { name: "Высота", defaultValue: 80, minimum: 0.001, maximum: TEMPLATE_V2_LIMITS.coordinate },
    );
    const original = structuredClone(parameterized);
    expect(() => setTemplateParameterDefaultV2(parameterized, parameterId, 0))
      .toThrowError(expect.objectContaining({ code: "parameter_range" }));
    expect(parameterized).toEqual(original);

    const locked = setNodeLockedV2(withRectangle, view.id, layer.id, nodeId, true);
    const lockedOriginal = structuredClone(locked);
    expect(() => parameterizeNodeDimensionV2(locked, view.id, layer.id, nodeId, "width", { name: "Ширина", defaultValue: 140, minimum: 0.001, maximum: TEMPLATE_V2_LIMITS.coordinate }))
      .toThrowError(expect.objectContaining({ code: "node_locked" }));
    expect(locked).toEqual(lockedOriginal);
  });

  it("validates custom dimension bounds atomically before creating a parameter", () => {
    const initial = newTemplateContentV2(), view = initial.views[0]!, layer = view.layers[0]!;
    const [withRectangle, nodeId] = addBasicNodeV2(initial, view.id, layer.id, "rectangle");
    for (const range of [
      { defaultValue: 9, minimum: 10, maximum: 100 },
      { defaultValue: 50, minimum: 100, maximum: 10 },
      { defaultValue: 50, minimum: 0, maximum: 100 },
      { defaultValue: 50, minimum: 10, maximum: TEMPLATE_V2_LIMITS.coordinate + 1 },
    ]) {
      const before = structuredClone(withRectangle);
      expect(() => parameterizeNodeDimensionV2(withRectangle, view.id, layer.id, nodeId, "width", { name: "Размер", ...range }))
        .toThrowError(expect.objectContaining({ code: "parameter_range" }));
      expect(withRectangle).toEqual(before);
    }
  });

  it("moves a rotated and scaled primitive in canvas coordinates without changing its local geometry", () => {
    const initial = newTemplateContentV2(), view = initial.views[0]!, layer = view.layers[0]!;
    const [created, nodeId] = addBasicNodeV2(initial, view.id, layer.id, "rectangle");
    const rectangle = created.views[0]!.layers[0]!.nodes[0] as RectangleNodeV2;
    const transformed = editNodeV2(created, view.id, layer.id, nodeId, { transform: {
      ...rectangle.transform,
      translateX: constantExpressionV2(7), translateY: constantExpressionV2(9),
      rotationDegrees: constantExpressionV2(30), scaleX: constantExpressionV2(2), scaleY: constantExpressionV2(3),
    } });
    const moved = moveNodeV2(transformed, view.id, layer.id, nodeId, 11, -4);
    const result = moved.views[0]!.layers[0]!.nodes[0] as RectangleNodeV2;

    expect(result.geometry).toEqual(rectangle.geometry);
    expect(result.transform).toMatchObject({
      translateX: { kind: "constant", value: 18 }, translateY: { kind: "constant", value: 5 },
      rotationDegrees: { kind: "constant", value: 30 }, scaleX: { kind: "constant", value: 2 }, scaleY: { kind: "constant", value: 3 },
    });
  });

  it("creates a logical contact and its point in the selected view with the first free number", () => {
    const initial = newTemplateContentV2(), e4 = initial.views[0]!, drawing = initial.views[1]!;
    const [first, firstPointId] = addContactPointV2(initial, e4.id);
    const [second, secondPointId] = addContactPointV2(first, drawing.id, {
      number: "3", name: "Силовой", contactType: "power",
      x: constantExpressionV2(40), y: constantExpressionV2(70), direction: "left",
    });
    const [third] = addContactPointV2(second, e4.id);

    expect(initial.logicalContacts).toEqual([]);
    expect(initial.views.every(view => view.contactPoints.length === 0)).toBe(true);
    expect(second.logicalContacts[1]).toMatchObject({ number: "3", name: "Силовой", contactType: "power" });
    expect(second.views[1]!.contactPoints[0]).toMatchObject({ id: secondPointId, direction: "left" });
    expect((second.views[1]!.contactPoints[0]!.x as { value: number }).value).toBe(40);
    expect(first.views[0]!.contactPoints[0]!.id).toBe(firstPointId);
    expect(third.logicalContacts.map(contact => contact.number)).toEqual(["1", "3", "2"]);
    expect(validateTemplateContentV2(third).valid).toBe(true);
  });

  it("edits shared logical properties separately from view point geometry", () => {
    const initial = newTemplateContentV2(), view = initial.views[0]!;
    const [created, pointId] = addContactPointV2(initial, view.id);
    const logicalId = created.views[0]!.contactPoints[0]!.logicalContactId;
    const logicalEdited = editLogicalContactV2(created, logicalId, { number: "A1", name: "Экран", contactType: "shield" });
    const pointEdited = editContactPointV2(logicalEdited, view.id, pointId, {
      x: constantExpressionV2(310), y: constantExpressionV2(90), direction: "up",
    });

    expect(created.logicalContacts[0]).toMatchObject({ number: "1", name: "Контакт 1", contactType: "" });
    expect(pointEdited.logicalContacts[0]).toMatchObject({ number: "A1", name: "Экран", contactType: "shield" });
    expect(pointEdited.views[0]!.contactPoints[0]).toMatchObject({ id: pointId, direction: "up" });
    expect((pointEdited.views[0]!.contactPoints[0]!.x as { value: number }).value).toBe(310);
    expect(validateTemplateContentV2(pointEdited).valid).toBe(true);
  });

  it("rejects duplicate contact numbers and removes an unreferenced logical contact with its point", () => {
    const initial = newTemplateContentV2(), view = initial.views[0]!;
    const [withFirst, firstPointId] = addContactPointV2(initial, view.id, { number: "X1" });
    const [withSecond] = addContactPointV2(withFirst, view.id, { number: "X2" });
    const secondLogicalId = withSecond.logicalContacts[1]!.id;

    expect(() => addContactPointV2(withSecond, view.id, { number: " X1 " }))
      .toThrowError(expect.objectContaining({ code: "duplicate_contact_number" }));
    expect(() => editLogicalContactV2(withSecond, secondLogicalId, { number: "X1" }))
      .toThrowError(expect.objectContaining({ code: "duplicate_contact_number" }));

    const removed = deleteContactPointV2(withSecond, view.id, firstPointId);
    expect(removed.views[0]!.contactPoints).toHaveLength(1);
    expect(removed.logicalContacts.map(contact => contact.number)).toEqual(["X2"]);
    expect(withSecond.views[0]!.contactPoints).toHaveLength(2);
    expect(validateTemplateContentV2(removed).valid).toBe(true);
  });

  it("keeps a logical contact while another view still contains its point", () => {
    const initial = newTemplateContentV2(), e4 = initial.views[0]!, drawing = initial.views[1]!;
    const [created, e4PointId] = addContactPointV2(initial, e4.id);
    const logicalId = created.logicalContacts[0]!.id;
    const [linked, drawingPointId] = linkLogicalContactPointV2(created, drawing.id, logicalId, {
      x: constantExpressionV2(100), y: constantExpressionV2(120), direction: "left",
    });

    const removed = deleteContactPointV2(linked, e4.id, e4PointId);
    expect(removed.logicalContacts).toHaveLength(1);
    expect(removed.views[1]!.contactPoints[0]).toMatchObject({ id: drawingPointId, logicalContactId: logicalId });
    expect(validateTemplateContentV2(removed).valid).toBe(true);
  });

  it("links one stable view point to an existing logical contact without duplicating the contact", () => {
    const initial = newTemplateContentV2(), e4 = initial.views[0]!, drawing = initial.views[1]!;
    const [created] = addContactPointV2(initial, e4.id, { number: "A1", name: "Экран", contactType: "shield" });
    const logical = created.logicalContacts[0]!;
    const original = structuredClone(created);
    const [linked, pointId] = linkLogicalContactPointV2(created, drawing.id, logical.id, {
      x: constantExpressionV2(45), y: constantExpressionV2(70), direction: "up",
    });

    expect(created).toEqual(original);
    expect(linked.logicalContacts).toEqual(created.logicalContacts);
    expect(linked.views[1]!.contactPoints).toEqual([{
      id: pointId, logicalContactId: logical.id,
      x: constantExpressionV2(45), y: constantExpressionV2(70), direction: "up",
    }]);
    expect(editContactPointV2(linked, drawing.id, pointId, { direction: "down" }).views[1]!.contactPoints[0]!.id).toBe(pointId);
    expect(() => linkLogicalContactPointV2(linked, drawing.id, logical.id))
      .toThrowError(expect.objectContaining({ code: "duplicate_contact_point" }));
    expect(() => linkLogicalContactPointV2(linked, drawing.id, crypto.randomUUID()))
      .toThrowError(expect.objectContaining({ code: "logical_contact_not_found" }));
    expect(linked.logicalContacts).toHaveLength(1);
    expect(validateTemplateContentV2(linked).valid).toBe(true);
  });

  it("adds, edits and deletes an independent bundle port immutably", () => {
    const initial = newTemplateContentV2(), view = initial.views[1]!, original = structuredClone(initial);
    const [created, portId] = addBundlePortV2(initial, view.id, {
      name: "Общий выход", x: constantExpressionV2(12), y: constantExpressionV2(34), direction: "left",
    });
    const edited = editBundlePortV2(created, view.id, portId, {
      name: "Выход пучка", y: constantExpressionV2(56), direction: "down",
    });
    const removed = deleteBundlePortV2(edited, view.id, portId);

    expect(initial).toEqual(original);
    expect(created.logicalContacts).toEqual([]);
    expect(edited.views[1]!.bundlePorts[0]).toEqual({
      id: portId, name: "Выход пучка", x: constantExpressionV2(12), y: constantExpressionV2(56), direction: "down",
    });
    expect(created.views[1]!.bundlePorts[0]).toMatchObject({ id: portId, name: "Общий выход", direction: "left" });
    expect(removed.views[1]!.bundlePorts).toEqual([]);
    expect(validateTemplateContentV2(edited).valid).toBe(true);
    expect(validateTemplateContentV2(removed).valid).toBe(true);
  });

  it("atomically wraps a top-level node and point into a valid repeat prototype", () => {
    const initial = newTemplateContentV2(), view = initial.views[0]!, layer = view.layers[0]!;
    const [withNode, nodeId] = addBasicNodeV2(initial, view.id, layer.id, "rectangle");
    const [withPoint, pointId] = addContactPointV2(withNode, view.id, { number: "1" });
    const original = structuredClone(withPoint);
    const [created, ids] = createRepeatPrototypeV2(withPoint, {
      viewId: view.id, layerId: layer.id, prototypeNodeId: nodeId, prototypePointId: pointId,
      count: 10, step: { x: constantExpressionV2(12), y: constantExpressionV2(0) },
    });

    expect(withPoint).toEqual(original);
    expect(created.parameters.at(-1)).toMatchObject({ id: ids.countParameterId, type: "integer", defaultValue: 10, minimum: 1, maximum: 1_000 });
    expect(created.repeaters.at(-1)).toEqual({ id: ids.repeatDomainId, countParameterId: ids.countParameterId, logicalContactIds: [created.logicalContacts[0]!.id] });
    expect(created.views[0]!.repeatPlacements.at(-1)).toMatchObject({ repeatDomainId: ids.repeatDomainId, prototypeGroupId: ids.groupId, contactPointIds: [pointId] });
    expect(created.views[0]!.layers[0]!.nodes.at(-1)).toMatchObject({ id: ids.groupId, kind: "group", geometry: { childIds: [nodeId] } });
    expect(validateTemplateContentV2(created)).toEqual({ valid: true, diagnostics: [] });

    for (const count of [0, -1, 1.5, 1_001]) {
      expect(() => createRepeatPrototypeV2(withPoint, {
        viewId: view.id, layerId: layer.id, prototypeNodeId: nodeId, prototypePointId: pointId,
        count, step: { x: constantExpressionV2(12), y: constantExpressionV2(0) },
      })).toThrowError(expect.objectContaining({ code: "repeater_count" }));
      expect(withPoint).toEqual(original);
    }
  });

  it("deletes a repeat prototype and leaves the original node, point and contact", () => {
    const initial = newTemplateContentV2(), view = initial.views[0]!, layer = view.layers[0]!;
    const [withNode, nodeId] = addBasicNodeV2(initial, view.id, layer.id, "line");
    const [withPoint, pointId] = addContactPointV2(withNode, view.id);
    const [created, ids] = createRepeatPrototypeV2(withPoint, {
      viewId: view.id, layerId: layer.id, prototypeNodeId: nodeId, prototypePointId: pointId,
      step: { x: constantExpressionV2(0), y: constantExpressionV2(8) },
    });
    const removed = deleteRepeatPrototypeV2(created, ids.repeatDomainId);

    expect(removed.views[0]!.layers[0]!.nodes.map(node => node.id)).toEqual([nodeId]);
    expect(removed.views[0]!.contactPoints.map(point => point.id)).toEqual([pointId]);
    expect(removed.logicalContacts).toEqual(withPoint.logicalContacts);
    expect(removed.parameters).toEqual([]);
    expect(removed.repeaters).toEqual([]);
    expect(removed.views[0]!.repeatPlacements).toEqual([]);
    expect(validateTemplateContentV2(removed)).toEqual({ valid: true, diagnostics: [] });
  });

  it("deletes one repeat domain and its prototype groups from every view", () => {
    const initial = newTemplateContentV2(), e4 = initial.views[0]!, drawing = initial.views[1]!;
    const [withNode, nodeId] = addBasicNodeV2(initial, e4.id, e4.layers[0]!.id, "line");
    const [withPoint, pointId] = addContactPointV2(withNode, e4.id, { number: "1" });
    const [created, ids] = createRepeatPrototypeV2(withPoint, {
      viewId: e4.id, layerId: e4.layers[0]!.id, prototypeNodeId: nodeId, prototypePointId: pointId,
      step: { x: constantExpressionV2(10), y: constantExpressionV2(0) },
    });
    const logicalId = created.views[0]!.contactPoints[0]!.logicalContactId;
    const drawingNodeId = crypto.randomUUID(), drawingGroupId = crypto.randomUUID(), drawingPointId = crypto.randomUUID();
    const drawingNode: TemplateNodeV2 = { ...created.views[0]!.layers[0]!.nodes.find(node => node.id === nodeId)!, id: drawingNodeId, layerId: drawing.layers[0]!.id };
    const drawingGroup: GroupNodeV2 = { ...(created.views[0]!.layers[0]!.nodes.find(node => node.id === ids.groupId) as GroupNodeV2), id: drawingGroupId, layerId: drawing.layers[0]!.id, geometry: { childIds: [drawingNodeId] } };
    const crossView: TemplateContentV2 = {
      ...created,
      views: created.views.map(view => view.id === drawing.id ? {
        ...view,
        layers: [{ ...view.layers[0]!, nodes: [drawingNode, drawingGroup] }],
        contactPoints: [{ id: drawingPointId, logicalContactId: logicalId, x: constantExpressionV2(5), y: constantExpressionV2(6), direction: "left" as const }],
        repeatPlacements: [{ repeatDomainId: ids.repeatDomainId, prototypeGroupId: drawingGroupId, step: { x: constantExpressionV2(0), y: constantExpressionV2(8) }, contactPointIds: [drawingPointId] }],
      } : view),
    };
    const original = structuredClone(crossView);

    expect(validateTemplateContentV2(crossView).valid).toBe(true);
    const removed = deleteRepeatPrototypeV2(crossView, ids.repeatDomainId);
    expect(crossView).toEqual(original);
    expect(removed.repeaters).toEqual([]);
    expect(removed.parameters).toEqual([]);
    expect(removed.views.every(view => view.repeatPlacements.length === 0)).toBe(true);
    expect(removed.views[0]!.layers[0]!.nodes.map(node => node.id)).toEqual([nodeId]);
    expect(removed.views[1]!.layers[0]!.nodes.map(node => node.id)).toEqual([drawingNodeId]);
    expect(removed.views[0]!.contactPoints.map(point => point.id)).toEqual([pointId]);
    expect(removed.views[1]!.contactPoints.map(point => point.id)).toEqual([drawingPointId]);
    expect(validateTemplateContentV2(removed).valid).toBe(true);
  });

  it("changes repeat count and step through validated atomic commands", () => {
    const initial = newTemplateContentV2(), view = initial.views[0]!, layer = view.layers[0]!;
    const [withNode, nodeId] = addBasicNodeV2(initial, view.id, layer.id, "line");
    const [withPoint, pointId] = addContactPointV2(withNode, view.id);
    const [created, ids] = createRepeatPrototypeV2(withPoint, {
      viewId: view.id, layerId: layer.id, prototypeNodeId: nodeId, prototypePointId: pointId,
      count: 2, step: { x: constantExpressionV2(0), y: constantExpressionV2(8) },
    });
    const counted = setRepeatCountV2(created, ids.repeatDomainId, 10);
    const stepped = setRepeatStepV2(counted, view.id, ids.repeatDomainId, {
      x: constantExpressionV2(4), y: constantExpressionV2(12),
    });

    expect(counted.parameters.find(item => item.id === ids.countParameterId)?.defaultValue).toBe(10);
    expect(stepped.views[0]!.repeatPlacements[0]!.step).toEqual({
      x: constantExpressionV2(4), y: constantExpressionV2(12),
    });
    expect(() => setRepeatCountV2(created, ids.repeatDomainId, 0)).toThrowError(expect.objectContaining({ code: "repeater_count" }));
    expect(validateTemplateContentV2(stepped).valid).toBe(true);
  });

  it("attaches one repeat domain across views with shared 2/10 contact occurrence keys", () => {
    const initial = newTemplateContentV2(), e4 = initial.views[0]!, drawing = initial.views[1]!;
    const [withE4Node, e4NodeId] = addBasicNodeV2(initial, e4.id, e4.layers[0]!.id, "line");
    const [withE4Point, e4PointId] = addContactPointV2(withE4Node, e4.id, { number: "1", name: "Сигнал" });
    const [repeated, ids] = createRepeatPrototypeV2(withE4Point, {
      viewId: e4.id, layerId: e4.layers[0]!.id, prototypeNodeId: e4NodeId, prototypePointId: e4PointId,
      count: 2, step: { x: constantExpressionV2(12), y: constantExpressionV2(0) },
    });
    const logicalId = repeated.logicalContacts[0]!.id;
    const [withDrawingNode, drawingNodeId] = addBasicNodeV2(repeated, drawing.id, drawing.layers[0]!.id, "rectangle");
    const [withDrawingPoint, drawingPointId] = linkLogicalContactPointV2(withDrawingNode, drawing.id, logicalId, {
      x: constantExpressionV2(40), y: constantExpressionV2(50), direction: "left",
    });
    const beforeAttach = structuredClone(withDrawingPoint);
    const [attached, drawingGroupId] = attachRepeatDomainV2(withDrawingPoint, {
      viewId: drawing.id, layerId: drawing.layers[0]!.id, prototypeNodeId: drawingNodeId,
      repeatDomainId: ids.repeatDomainId, step: { x: constantExpressionV2(0), y: constantExpressionV2(20) },
    });

    expect(withDrawingPoint).toEqual(beforeAttach);
    expect(attached.logicalContacts).toEqual(repeated.logicalContacts);
    expect(attached.repeaters).toEqual(repeated.repeaters);
    expect(attached.views[1]!.repeatPlacements[0]).toEqual({
      repeatDomainId: ids.repeatDomainId, prototypeGroupId: drawingGroupId,
      step: { x: constantExpressionV2(0), y: constantExpressionV2(20) }, contactPointIds: [drawingPointId],
    });
    expect(attached.views[1]!.layers[0]!.nodes.find(node => node.id === drawingGroupId)).toMatchObject({
      kind: "group", geometry: { childIds: [drawingNodeId] },
    });

    for (const count of [2, 10]) {
      const counted = setRepeatCountV2(attached, ids.repeatDomainId, count);
      const expansion = expandTemplateRepeatsV2(counted);
      const e4Occurrences = expansion.find(view => view.viewId === e4.id)!.placements[0]!.occurrences;
      const drawingOccurrences = expansion.find(view => view.viewId === drawing.id)!.placements[0]!.occurrences;
      expect(e4Occurrences).toHaveLength(count);
      expect(drawingOccurrences).toHaveLength(count);
      expect(e4Occurrences.map(item => item.contactPoints[0]!.key))
        .toEqual(drawingOccurrences.map(item => item.contactPoints[0]!.key));
      expect(e4Occurrences.map(item => item.contactPoints[0]!.number))
        .toEqual(Array.from({ length: count }, (_, index) => String(index + 1)));
      expect(drawingOccurrences.at(-1)!.offset).toEqual({ x: 0, y: (count - 1) * 20 });
    }

    const removed = deleteRepeatPrototypeV2(attached, ids.repeatDomainId);
    expect(removed.repeaters).toEqual([]);
    expect(removed.logicalContacts).toEqual(repeated.logicalContacts);
    expect(removed.views[0]!.contactPoints.map(point => point.id)).toEqual([e4PointId]);
    expect(removed.views[1]!.contactPoints.map(point => point.id)).toEqual([drawingPointId]);
    expect(removed.views[0]!.layers[0]!.nodes.some(node => node.id === e4NodeId)).toBe(true);
    expect(removed.views[1]!.layers[0]!.nodes.some(node => node.id === drawingNodeId)).toBe(true);
    expect(validateTemplateContentV2(removed).valid).toBe(true);
  });

  it("reuses an editable top-level group and rejects unsafe repeat attachments atomically", () => {
    const initial = newTemplateContentV2(), e4 = initial.views[0]!, drawing = initial.views[1]!;
    const [withE4Node, e4NodeId] = addBasicNodeV2(initial, e4.id, e4.layers[0]!.id, "line");
    const [withE4Point, e4PointId] = addContactPointV2(withE4Node, e4.id);
    const [repeated, ids] = createRepeatPrototypeV2(withE4Point, {
      viewId: e4.id, layerId: e4.layers[0]!.id, prototypeNodeId: e4NodeId, prototypePointId: e4PointId,
      step: { x: constantExpressionV2(8), y: constantExpressionV2(0) },
    });
    const [withChild, childId] = addBasicNodeV2(repeated, drawing.id, drawing.layers[0]!.id, "line");
    const groupId = crypto.randomUUID();
    const group: GroupNodeV2 = {
      id: groupId, kind: "group", layerId: drawing.layers[0]!.id, visible: true, locked: false, opacity: 1,
      transform: { translateX: constantExpressionV2(0), translateY: constantExpressionV2(0), rotationDegrees: constantExpressionV2(0), scaleX: constantExpressionV2(1), scaleY: constantExpressionV2(1) },
      stroke: { color: "#27445a", width: constantExpressionV2(2) }, fill: { color: null }, geometry: { childIds: [childId] },
    };
    const withGroup = addNodeV2(withChild, drawing.id, drawing.layers[0]!.id, group);
    const logicalId = withGroup.logicalContacts[0]!.id;
    const [linked, drawingPointId] = linkLogicalContactPointV2(withGroup, drawing.id, logicalId);
    const original = structuredClone(linked);
    const [attached, attachedGroupId] = attachRepeatDomainV2(linked, {
      viewId: drawing.id, layerId: drawing.layers[0]!.id, prototypeNodeId: groupId,
      repeatDomainId: ids.repeatDomainId, contactPointIds: [drawingPointId],
      step: { x: constantExpressionV2(0), y: constantExpressionV2(9) },
    });

    expect(attachedGroupId).toBe(groupId);
    expect(attached.views[1]!.layers[0]!.nodes).toHaveLength(linked.views[1]!.layers[0]!.nodes.length);
    expect(() => attachRepeatDomainV2(attached, {
      viewId: drawing.id, layerId: drawing.layers[0]!.id, prototypeNodeId: groupId,
      repeatDomainId: ids.repeatDomainId, contactPointIds: [drawingPointId],
      step: { x: constantExpressionV2(0), y: constantExpressionV2(9) },
    })).toThrowError(expect.objectContaining({ code: "duplicate_repeat_placement" }));
    expect(linked).toEqual(original);

    const withoutLinkedPoint = deleteContactPointV2(linked, drawing.id, drawingPointId);
    const beforeMissing = structuredClone(withoutLinkedPoint);
    expect(() => attachRepeatDomainV2(withoutLinkedPoint, {
      viewId: drawing.id, layerId: drawing.layers[0]!.id, prototypeNodeId: groupId,
      repeatDomainId: ids.repeatDomainId, step: { x: constantExpressionV2(0), y: constantExpressionV2(9) },
    })).toThrowError(expect.objectContaining({ code: "repeat_contact_missing" }));
    expect(withoutLinkedPoint).toEqual(beforeMissing);

    const locked = setNodeLockedV2(linked, drawing.id, drawing.layers[0]!.id, groupId, true);
    const beforeLocked = structuredClone(locked);
    expect(() => attachRepeatDomainV2(locked, {
      viewId: drawing.id, layerId: drawing.layers[0]!.id, prototypeNodeId: groupId,
      repeatDomainId: ids.repeatDomainId, step: { x: constantExpressionV2(0), y: constantExpressionV2(9) },
    })).toThrowError(expect.objectContaining({ code: "node_locked" }));
    expect(locked).toEqual(beforeLocked);
  });

  it("rejects a cross-view repeat attachment that would collide with an ordinary contact number", () => {
    const initial = newTemplateContentV2(), e4 = initial.views[0]!, drawing = initial.views[1]!;
    const [withE4Node, e4NodeId] = addBasicNodeV2(initial, e4.id, e4.layers[0]!.id, "line");
    const [withPrototype, e4PointId] = addContactPointV2(withE4Node, e4.id, { number: "1" });
    const [repeated, ids] = createRepeatPrototypeV2(withPrototype, {
      viewId: e4.id, layerId: e4.layers[0]!.id, prototypeNodeId: e4NodeId, prototypePointId: e4PointId,
      count: 2, step: { x: constantExpressionV2(10), y: constantExpressionV2(0) },
    });
    const logicalId = repeated.logicalContacts[0]!.id;
    const [withDrawingNode, drawingNodeId] = addBasicNodeV2(repeated, drawing.id, drawing.layers[0]!.id, "line");
    const [withLinkedPoint] = linkLogicalContactPointV2(withDrawingNode, drawing.id, logicalId);
    const [withCollision] = addContactPointV2(withLinkedPoint, drawing.id, { number: "2" });
    const before = structuredClone(withCollision);

    expect(() => attachRepeatDomainV2(withCollision, {
      viewId: drawing.id, layerId: drawing.layers[0]!.id, prototypeNodeId: drawingNodeId,
      repeatDomainId: ids.repeatDomainId, step: { x: constantExpressionV2(0), y: constantExpressionV2(10) },
    })).toThrowError(expect.objectContaining({ code: "duplicate_expanded_contact_number" }));
    expect(withCollision).toEqual(before);
  });

  it("enforces the aggregate materialized budget when attaching a repeat to another view", () => {
    const initial = newTemplateContentV2(), e4 = initial.views[0]!, drawing = initial.views[1]!;
    const [withE4Node, e4NodeId] = addBasicNodeV2(initial, e4.id, e4.layers[0]!.id, "line");
    const [withE4Point, e4PointId] = addContactPointV2(withE4Node, e4.id);
    const [repeated, ids] = createRepeatPrototypeV2(withE4Point, {
      viewId: e4.id, layerId: e4.layers[0]!.id, prototypeNodeId: e4NodeId, prototypePointId: e4PointId,
      count: 1_000, step: { x: constantExpressionV2(1), y: constantExpressionV2(0) },
    });
    let withDrawingNodes = repeated;
    const childIds: string[] = [];
    for (let index = 0; index < 3; index++) {
      const result = addBasicNodeV2(withDrawingNodes, drawing.id, drawing.layers[0]!.id, "line");
      withDrawingNodes = result[0];
      childIds.push(result[1]);
    }
    const groupId = crypto.randomUUID();
    const group: GroupNodeV2 = {
      id: groupId, kind: "group", layerId: drawing.layers[0]!.id, visible: true, locked: false, opacity: 1,
      transform: { translateX: constantExpressionV2(0), translateY: constantExpressionV2(0), rotationDegrees: constantExpressionV2(0), scaleX: constantExpressionV2(1), scaleY: constantExpressionV2(1) },
      stroke: { color: "#27445a", width: constantExpressionV2(2) }, fill: { color: null }, geometry: { childIds },
    };
    const withGroup = addNodeV2(withDrawingNodes, drawing.id, drawing.layers[0]!.id, group);
    const [linked] = linkLogicalContactPointV2(withGroup, drawing.id, withGroup.logicalContacts[0]!.id);
    const before = structuredClone(linked);

    expect(() => attachRepeatDomainV2(linked, {
      viewId: drawing.id, layerId: drawing.layers[0]!.id, prototypeNodeId: groupId,
      repeatDomainId: ids.repeatDomainId, step: { x: constantExpressionV2(0), y: constantExpressionV2(1) },
    })).toThrowError(expect.objectContaining({ code: "expanded_budget" }));
    expect(linked).toEqual(before);
  });

  it("checks aggregate expansion before accepting create and count changes", () => {
    const initial = newTemplateContentV2(), view = initial.views[0]!, layer = view.layers[0]!;
    const [withFirstNode, firstNodeId] = addBasicNodeV2(initial, view.id, layer.id, "line");
    const [withFirstPoint, firstPointId] = addContactPointV2(withFirstNode, view.id, { number: "1" });
    const [firstRepeat] = createRepeatPrototypeV2(withFirstPoint, {
      viewId: view.id, layerId: layer.id, prototypeNodeId: firstNodeId, prototypePointId: firstPointId,
      count: 1_000, step: { x: constantExpressionV2(1), y: constantExpressionV2(0) },
    });
    const [withSecondNode, secondNodeId] = addBasicNodeV2(firstRepeat, view.id, layer.id, "line");
    const [withSecondPoint, secondPointId] = addContactPointV2(withSecondNode, view.id, { number: "1001" });
    const [withOrdinaryPoint] = addContactPointV2(withSecondPoint, view.id, { number: "3000" });
    const beforeCreate = structuredClone(withOrdinaryPoint);

    expect(() => createRepeatPrototypeV2(withOrdinaryPoint, {
      viewId: view.id, layerId: layer.id, prototypeNodeId: secondNodeId, prototypePointId: secondPointId,
      count: 1_000, step: { x: constantExpressionV2(1), y: constantExpressionV2(0) },
    })).toThrowError(expect.objectContaining({ code: "expanded_budget" }));
    expect(withOrdinaryPoint).toEqual(beforeCreate);

    const [secondRepeat, secondIds] = createRepeatPrototypeV2(withOrdinaryPoint, {
      viewId: view.id, layerId: layer.id, prototypeNodeId: secondNodeId, prototypePointId: secondPointId,
      count: 999, step: { x: constantExpressionV2(1), y: constantExpressionV2(0) },
    });
    const beforeCount = structuredClone(secondRepeat);
    expect(() => setRepeatCountV2(secondRepeat, secondIds.repeatDomainId, 1_000))
      .toThrowError(expect.objectContaining({ code: "expanded_budget" }));
    expect(secondRepeat).toEqual(beforeCount);
  });

  it("rejects ordinary contact numbers already produced by a repeat", () => {
    const initial = newTemplateContentV2(), view = initial.views[0]!, layer = view.layers[0]!;
    const [withNode, nodeId] = addBasicNodeV2(initial, view.id, layer.id, "line");
    const [withPoint, pointId] = addContactPointV2(withNode, view.id, { number: "1" });
    const [repeated] = createRepeatPrototypeV2(withPoint, {
      viewId: view.id, layerId: layer.id, prototypeNodeId: nodeId, prototypePointId: pointId,
      count: 2, step: { x: constantExpressionV2(0), y: constantExpressionV2(8) },
    });

    expect(() => addContactPointV2(repeated, view.id))
      .toThrowError(expect.objectContaining({ code: "duplicate_expanded_contact_number" }));

    const logicalId = crypto.randomUUID(), newPointId = crypto.randomUUID();
    const withOrdinary: TemplateContentV2 = {
      ...repeated,
      logicalContacts: [...repeated.logicalContacts, { id: logicalId, number: "9", name: "Обычный", contactType: "signal" }],
      views: repeated.views.map(candidate => candidate.id === view.id ? {
        ...candidate,
        contactPoints: [...candidate.contactPoints, {
          id: newPointId, logicalContactId: logicalId,
          x: constantExpressionV2(10), y: constantExpressionV2(10), direction: "left" as const,
        }],
      } : candidate),
    };
    const before = structuredClone(withOrdinary);

    expect(() => editLogicalContactV2(withOrdinary, logicalId, { number: "2" }))
      .toThrowError(expect.objectContaining({ code: "duplicate_expanded_contact_number" }));
    expect(withOrdinary).toEqual(before);
  });
});

describe("template v2 numeric expressions", () => {
  it("evaluates constants, parameters and bounded arithmetic", () => {
    const parameterId = crypto.randomUUID();
    expect(evaluateNumericExpressionV2({
      kind: "binary", operator: "multiply", left: { kind: "parameter", parameterId },
      right: { kind: "negate", operand: constantExpressionV2(-2) },
    }, { [parameterId]: 12 })).toBe(24);
    expect(() => evaluateNumericExpressionV2({ kind: "binary", operator: "divide", left: constantExpressionV2(1), right: constantExpressionV2(0) }))
      .toThrowError(expect.objectContaining({ code: "division_by_zero" }));
  });
});
