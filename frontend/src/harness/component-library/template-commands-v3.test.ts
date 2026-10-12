// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  addBasicNodeV3,
  addArticleVariantsV3,
  addBundlePortV3,
  addContactPointV3,
  addContactTypeGroupV3,
  attachRepeatDomainV3,
  constantExpressionV3,
  constantExpressionValueV3,
  createRepeatPrototypeV3,
  deleteNodePointV3,
  deleteBundlePortV3,
  deleteContactPointV3,
  deleteContactTypeGroupV3,
  editBundlePortV3,
  editContactPointV3,
  editLogicalContactV3,
  linkLogicalContactPointV3,
  insertNodePointV3,
  groupRootNodesV3,
  moveNodePointV3,
  newTemplateContentV3,
  projectTemplateContentV3CoreToV2,
  removeArticleVariantContactGroupV3,
  removeArticleVariantV3,
  reorderRootNodeStepV3,
  renameContactTypeGroupV3,
  setRootNodeRotationAroundCenterV3,
  setArticleVariantContactGroupV3,
  setRepeatCountV3,
  TemplateCommandV3Error,
  ungroupRootNodeV3,
  upsertArticleVariantV3,
} from "./template-commands-v3";
import { validateTemplateContentV2 } from "./template-model-v2";
import { validateTemplateContentV3 } from "./template-model-v3";
import { expandTemplateRepeatsV2 } from "./template-repeat-v2";

describe("template v3 immutable commands", () => {
  it("adds an article batch atomically and rejects existing or intra-batch collisions", () => {
    const initial = newTemplateContentV3();
    const [withExisting] = upsertArticleVariantV3(initial, {
      sourceId: "БД.СОЕД", entityType: "connector", articleKey: "PHR-02",
    });
    const before = structuredClone(withExisting);
    const added = addArticleVariantsV3(withExisting, [
      { sourceId: "БД.СОЕД", entityType: "connector", articleKey: "PHR-01" },
      { sourceId: "БД.СОЕД", entityType: "connector", articleKey: "PHR-03" },
    ]);
    expect(added.articleVariants.map(item => item.articleKey)).toEqual(["PHR-02", "PHR-01", "PHR-03"]);
    expect(withExisting).toEqual(before);

    expect(() => addArticleVariantsV3(withExisting, [
      { sourceId: "БД.СОЕД", entityType: "connector", articleKey: "PHR-04" },
      { sourceId: "БД.СОЕД", entityType: "connector", articleKey: "PHR-04" },
    ])).toThrowError(new TemplateCommandV3Error("duplicate_article_variant", "Вариант с ключом «PHR-04» уже есть в серии или повторяется в списке."));
    expect(() => addArticleVariantsV3(withExisting, [
      { sourceId: "БД.СОЕД", entityType: "connector", articleKey: "PHR-02" },
      { sourceId: "БД.СОЕД", entityType: "connector", articleKey: "PHR-05" },
    ])).toThrowError(new TemplateCommandV3Error("duplicate_article_variant", "Вариант с ключом «PHR-02» уже есть в серии или повторяется в списке."));
    expect(withExisting).toEqual(before);
  });
  it("creates mandatory views and a strictly valid v3 document", () => {
    const content = newTemplateContentV3();

    expect(content.schemaVersion).toBe(3);
    expect(content.views.map(view => view.kind)).toEqual(["e4", "drawing"]);
    expect(content.contactTypeGroups).toEqual([]);
    expect(content.articleVariants).toEqual([]);
    expect(validateTemplateContentV3(content)).toEqual({ valid: true, diagnostics: [] });
  });

  it("creates, links, edits and deletes one logical contact without mutating its source", () => {
    const initial = newTemplateContentV3();
    const [grouped, groupId] = addContactTypeGroupV3(initial, "Сигнальные");
    const before = structuredClone(grouped);
    const e4 = grouped.views[0]!, drawing = grouped.views[1]!;
    const [withE4Point, e4PointId] = addContactPointV3(grouped, e4.id, {
      number: "A1", name: "CAN H", circuitText: "CAN_H", contactTypeGroupId: groupId,
      x: constantExpressionV3(10), y: constantExpressionV3(20), direction: "right",
    });
    const logicalId = withE4Point.logicalContacts[0]!.id;
    const [linked, drawingPointId] = linkLogicalContactPointV3(withE4Point, drawing.id, logicalId, {
      x: constantExpressionV3(30), y: constantExpressionV3(40), direction: "left",
    });
    const editedLogical = editLogicalContactV3(linked, logicalId, {
      number: "A2", name: "CAN Low", circuitText: null, contactTypeGroupId: null,
    });
    const editedPoint = editContactPointV3(editedLogical, drawing.id, drawingPointId, {
      x: constantExpressionV3(35), direction: "up",
    });
    const withoutDrawing = deleteContactPointV3(editedPoint, drawing.id, drawingPointId);
    const withoutE4 = deleteContactPointV3(withoutDrawing, e4.id, e4PointId);

    expect(grouped).toEqual(before);
    expect(editedPoint.logicalContacts[0]).toMatchObject({
      id: logicalId, number: "A2", name: "CAN Low", circuitText: null, contactTypeGroupId: null,
    });
    expect(editedPoint.views[1]!.contactPoints[0]).toMatchObject({ id: drawingPointId, direction: "up" });
    expect(withoutDrawing.logicalContacts).toHaveLength(1);
    expect(withoutE4.logicalContacts).toEqual([]);
    expect(validateTemplateContentV3(withoutE4).valid).toBe(true);
  });

  it("manages view-specific bundle ports", () => {
    const initial = newTemplateContentV3(), drawing = initial.views[1]!;
    const [added, portId] = addBundlePortV3(initial, drawing.id, { name: "Общий пучок", direction: "left" });
    const edited = editBundlePortV3(added, drawing.id, portId, {
      name: "Боковой пучок", x: constantExpressionV3(500), direction: "down",
    });
    const removed = deleteBundlePortV3(edited, drawing.id, portId);

    expect(initial.views[1]!.bundlePorts).toEqual([]);
    expect(edited.views[1]!.bundlePorts[0]).toMatchObject({ id: portId, name: "Боковой пучок", direction: "down" });
    expect(removed.views[1]!.bundlePorts).toEqual([]);
    expect(validateTemplateContentV3(removed).valid).toBe(true);
  });

  it("manages unique contact type groups and protects referenced groups", () => {
    const initial = newTemplateContentV3();
    const [withSignal, signalId] = addContactTypeGroupV3(initial, "Сигнальные");
    const [withPower, powerId] = addContactTypeGroupV3(withSignal, "Силовые");
    const renamed = renameContactTypeGroupV3(withPower, powerId, "Питание");
    const [referenced] = addContactPointV3(renamed, renamed.views[0]!.id, { contactTypeGroupId: signalId });

    expect(() => addContactTypeGroupV3(renamed, " питание ")).toThrowError(expect.objectContaining({
      code: "duplicate_contact_type_group_name",
    }));
    expect(deleteContactTypeGroupV3(referenced, signalId).contactTypeGroups.map(group => group.id)).toEqual([powerId]);
    expect(deleteContactTypeGroupV3(renamed, signalId).contactTypeGroups.map(group => group.id)).toEqual([powerId]);
  });

  it("upserts article variants and validates group counts and terminal keys atomically", () => {
    const initial = newTemplateContentV3();
    const [grouped, groupId] = addContactTypeGroupV3(initial, "Сигнальные");
    const [withContact] = addContactPointV3(grouped, grouped.views[0]!.id, { contactTypeGroupId: groupId });
    const source = structuredClone(withContact);
    const [withVariant, variantId] = upsertArticleVariantV3(withContact, {
      sourceId: "db", entityType: "connector", articleKey: "JST-XH-02", parameterValues: [], contactGroups: null,
    });
    const configured = setArticleVariantContactGroupV3(withVariant, variantId, groupId, 1, [
      { sourceId: "db", entityType: "terminal", articleKey: "SXH-001T-P0.6" },
    ]);
    const [withGeometry] = addBasicNodeV3(
      configured,
      configured.views[1]!.id,
      configured.views[1]!.layers[0]!.id,
      "rectangle",
    );
    const [renamed, sameId] = upsertArticleVariantV3(withGeometry, {
      id: variantId, sourceId: "db", entityType: "connector", articleKey: "JST-XH-02-A",
    });
    const unconfigured = removeArticleVariantContactGroupV3(renamed, variantId, groupId);
    const removed = removeArticleVariantV3(unconfigured, variantId);

    expect(withContact).toEqual(source);
    expect(sameId).toBe(variantId);
    expect(configured.articleVariants[0]!.contactGroups).toEqual([{
      contactTypeGroupId: groupId,
      contactCount: 1,
      allowedTerminalArticleKeys: [{ sourceId: "db", entityType: "terminal", articleKey: "SXH-001T-P0.6" }],
    }]);
    expect(renamed.articleVariants[0]).toMatchObject({ articleKey: "JST-XH-02-A" });
    expect(renamed.articleVariants[0]!.contactGroups).toEqual(configured.articleVariants[0]!.contactGroups);
    expect(unconfigured.articleVariants[0]!.contactGroups).toBeNull();
    expect(removed.articleVariants).toEqual([]);
    const temporarilyStale = setArticleVariantContactGroupV3(withVariant, variantId, groupId, 2, []);
    expect(validateTemplateContentV3(temporarilyStale)).toMatchObject({
      valid: false,
      diagnostics: expect.arrayContaining([expect.objectContaining({ code: "unproducible_contact_count" })]),
    });
    expect(validateTemplateContentV3(configured).valid).toBe(true);
  });

  it("permits an atomic editing sequence before the strict save boundary", () => {
    const initial = newTemplateContentV3();
    const [grouped, groupId] = addContactTypeGroupV3(initial, "Сигнальные");
    const [withFirst] = addContactPointV3(grouped, grouped.views[0]!.id, { number: "1", contactTypeGroupId: groupId });
    const [withVariant, variantId] = upsertArticleVariantV3(withFirst, {
      sourceId: "db", entityType: "connector", articleKey: "EDITABLE-2",
    });
    const configuredOne = setArticleVariantContactGroupV3(withVariant, variantId, groupId, 1, []);

    const [withSecond] = addContactPointV3(configuredOne, configuredOne.views[0]!.id, { number: "2", contactTypeGroupId: groupId });
    expect(validateTemplateContentV3(withSecond).valid).toBe(false);
    const configuredTwo = setArticleVariantContactGroupV3(withSecond, variantId, groupId, 2, []);

    expect(validateTemplateContentV3(configuredTwo)).toEqual({ valid: true, diagnostics: [] });
  });

  it("preserves all v3-only fields through V2 core geometry and repeat commands", () => {
    const initial = newTemplateContentV3(), e4 = initial.views[0]!, drawing = initial.views[1]!;
    const [grouped, groupId] = addContactTypeGroupV3(initial, "Сигнальные");
    const [withVariant, variantId] = upsertArticleVariantV3(grouped, {
      sourceId: "db", entityType: "connector", articleKey: "SERIES-X", contactGroups: null,
    });
    const [withNode, e4NodeId] = addBasicNodeV3(withVariant, e4.id, e4.layers[0]!.id, "line");
    const [withPoint, e4PointId] = addContactPointV3(withNode, e4.id, {
      number: "1", name: "Signal", circuitText: "NET-1", contactTypeGroupId: groupId,
    });
    const [repeated, repeatIds] = createRepeatPrototypeV3(withPoint, {
      viewId: e4.id, layerId: e4.layers[0]!.id, prototypeNodeId: e4NodeId, prototypePointId: e4PointId,
      count: 2, step: { x: constantExpressionV3(0), y: constantExpressionV3(10) },
    });
    const logicalId = repeated.logicalContacts[0]!.id;
    const [withDrawingNode, drawingNodeId] = addBasicNodeV3(repeated, drawing.id, drawing.layers[0]!.id, "rectangle");
    const [withDrawingPoint, drawingPointId] = linkLogicalContactPointV3(withDrawingNode, drawing.id, logicalId);
    const [attached] = attachRepeatDomainV3(withDrawingPoint, {
      viewId: drawing.id, layerId: drawing.layers[0]!.id, prototypeNodeId: drawingNodeId,
      repeatDomainId: repeatIds.repeatDomainId,
      step: { x: constantExpressionV3(20), y: constantExpressionV3(0) },
    });
    const counted = setRepeatCountV3(attached, repeatIds.repeatDomainId, 10);
    const core = projectTemplateContentV3CoreToV2(counted);

    expect(counted.contactTypeGroups).toEqual([{ id: groupId, name: "Сигнальные" }]);
    expect(counted.logicalContacts[0]).toMatchObject({ circuitText: "NET-1", contactTypeGroupId: groupId });
    expect(counted.articleVariants).toEqual([{
      id: variantId, sourceId: "db", entityType: "connector", articleKey: "SERIES-X",
      parameterValues: [], contactGroups: null,
    }]);
    expect(counted.views[1]!.repeatPlacements[0]!.contactPointIds).toEqual([drawingPointId]);
    expect(core.logicalContacts[0]!.contactType).toBe("Сигнальные");
    expect(core.articleParameterPresets[0]!.id).toBe(variantId);
    expect(validateTemplateContentV2(core).valid).toBe(true);
    expect(expandTemplateRepeatsV2(core).every(view => view.placements[0]!.occurrences.length === 10)).toBe(true);
    expect(validateTemplateContentV3(counted).valid).toBe(true);
  });

  it("rejects invalid input before applying a core command", () => {
    const invalid = newTemplateContentV3();
    invalid.schemaVersion = 2 as 3;

    expect(() => addBasicNodeV3(invalid, invalid.views[0]!.id, invalid.views[0]!.layers[0]!.id, "line"))
      .toThrowError(expect.objectContaining({ code: "invalid_input" } satisfies Partial<TemplateCommandV3Error>));
  });

  it("moves one constant line or Bezier point immutably", () => {
    const initial = newTemplateContentV3(), view = initial.views[1]!, layer = view.layers[0]!;
    const [withLine, lineId] = addBasicNodeV3(initial, view.id, layer.id, "line");
    const before = structuredClone(withLine);
    const moved = moveNodePointV3(withLine, view.id, layer.id, lineId, 1, 12, -8);
    const movedLine = moved.views[1]!.layers[0]!.nodes[0]!;

    expect(withLine).toEqual(before);
    expect(movedLine.kind).toBe("line");
    if (movedLine.kind === "line") expect(movedLine.geometry.points[1]).toEqual({
      x: constantExpressionV3(222), y: constantExpressionV3(92),
    });

    const bezier = structuredClone(withLine);
    const line = bezier.views[1]!.layers[0]!.nodes[0]!;
    bezier.views[1]!.layers[0]!.nodes[0] = {
      ...line,
      kind: "bezier",
      geometry: { points: [
        { x: constantExpressionV3(0), y: constantExpressionV3(0) },
        { x: constantExpressionV3(20), y: constantExpressionV3(0) },
        { x: constantExpressionV3(40), y: constantExpressionV3(40) },
        { x: constantExpressionV3(60), y: constantExpressionV3(40) },
      ], closed: false },
    };
    const movedBezier = moveNodePointV3(bezier, view.id, layer.id, lineId, 2, -5, 7);
    const result = movedBezier.views[1]!.layers[0]!.nodes[0]!;
    if (result.kind === "bezier") expect(result.geometry.points[2]).toEqual({
      x: constantExpressionV3(35), y: constantExpressionV3(47),
    });
    expect(() => insertNodePointV3(bezier, view.id, layer.id, lineId, 0, 10, 10))
      .toThrowError(expect.objectContaining({ code: "unsupported_point_insert" }));
    expect(() => deleteNodePointV3(bezier, view.id, layer.id, lineId, 1))
      .toThrowError(expect.objectContaining({ code: "unsupported_point_delete" }));
  });

  it("inserts and removes an internal line point while preserving endpoints", () => {
    const initial = newTemplateContentV3(), view = initial.views[1]!, layer = view.layers[0]!;
    const [withLine, lineId] = addBasicNodeV3(initial, view.id, layer.id, "line");
    const inserted = insertNodePointV3(withLine, view.id, layer.id, lineId, 0, 90, 45);
    const insertedNode = inserted.views[1]!.layers[0]!.nodes[0]!;
    expect(withLine.views[1]!.layers[0]!.nodes[0]).not.toBe(insertedNode);
    if (insertedNode.kind === "line") {
      expect(insertedNode.geometry.points).toHaveLength(3);
      expect(insertedNode.geometry.points[1]).toEqual({ x: constantExpressionV3(90), y: constantExpressionV3(45) });
    }

    const removed = deleteNodePointV3(inserted, view.id, layer.id, lineId, 1);
    expect(removed.views[1]!.layers[0]!.nodes[0]).toEqual(withLine.views[1]!.layers[0]!.nodes[0]);
    expect(() => deleteNodePointV3(withLine, view.id, layer.id, lineId, 0))
      .toThrowError(expect.objectContaining({ code: "endpoint_delete" }));
    expect(() => deleteNodePointV3(withLine, view.id, layer.id, lineId, 1))
      .toThrowError(expect.objectContaining({ code: "endpoint_delete" }));
  });

  it("edits a closed contour including its closing segment and keeps three vertices", () => {
    const initial = newTemplateContentV3(), view = initial.views[1]!, layer = view.layers[0]!;
    const [withLine, nodeId] = addBasicNodeV3(initial, view.id, layer.id, "line");
    const contour = structuredClone(withLine);
    const base = contour.views[1]!.layers[0]!.nodes[0]!;
    contour.views[1]!.layers[0]!.nodes[0] = {
      ...base,
      kind: "closedContour",
      geometry: { points: [
        { x: constantExpressionV3(0), y: constantExpressionV3(0) },
        { x: constantExpressionV3(100), y: constantExpressionV3(0) },
        { x: constantExpressionV3(50), y: constantExpressionV3(80) },
      ] },
    };
    const before = structuredClone(contour);
    const inserted = insertNodePointV3(contour, view.id, layer.id, nodeId, 2, 10, 40);
    const insertedNode = inserted.views[1]!.layers[0]!.nodes[0]!;
    expect(contour).toEqual(before);
    if (insertedNode.kind === "closedContour") {
      expect(insertedNode.geometry.points).toHaveLength(4);
      expect(insertedNode.geometry.points[3]).toEqual({ x: constantExpressionV3(10), y: constantExpressionV3(40) });
    }

    const moved = moveNodePointV3(inserted, view.id, layer.id, nodeId, 0, 5, 7);
    const movedNode = moved.views[1]!.layers[0]!.nodes[0]!;
    if (movedNode.kind === "closedContour") expect(movedNode.geometry.points[0]).toEqual({
      x: constantExpressionV3(5), y: constantExpressionV3(7),
    });
    const removed = deleteNodePointV3(moved, view.id, layer.id, nodeId, 0);
    const removedNode = removed.views[1]!.layers[0]!.nodes[0]!;
    if (removedNode.kind === "closedContour") expect(removedNode.geometry.points).toHaveLength(3);
    expect(() => deleteNodePointV3(removed, view.id, layer.id, nodeId, 0))
      .toThrowError(expect.objectContaining({ code: "minimum_points" }));
  });

  it("rejects invalid, parameterized and locked point edits without mutation", () => {
    const initial = newTemplateContentV3(), view = initial.views[1]!, layer = view.layers[0]!;
    const [withLine, lineId] = addBasicNodeV3(initial, view.id, layer.id, "line");
    const before = structuredClone(withLine);

    expect(() => moveNodePointV3(withLine, view.id, layer.id, lineId, 9, 1, 1))
      .toThrowError(expect.objectContaining({ code: "point_index" }));
    expect(() => moveNodePointV3(withLine, view.id, layer.id, lineId, 0, Number.NaN, 1))
      .toThrowError(expect.objectContaining({ code: "invalid_point_move" }));
    expect(() => moveNodePointV3(withLine, view.id, layer.id, lineId, 0, 1_000_000, 0))
      .toThrowError(expect.objectContaining({ code: "invalid_point_move" }));
    expect(() => insertNodePointV3(withLine, view.id, layer.id, lineId, 0, 1_000_001, 0))
      .toThrowError(expect.objectContaining({ code: "invalid_point_insert" }));
    expect(() => insertNodePointV3(withLine, view.id, layer.id, lineId, 2, 0, 0))
      .toThrowError(expect.objectContaining({ code: "segment_index" }));

    const locked = structuredClone(withLine);
    locked.views[1]!.layers[0]!.locked = true;
    expect(() => moveNodePointV3(locked, view.id, layer.id, lineId, 0, 1, 1))
      .toThrowError(expect.objectContaining({ code: "layer_locked" }));

    const nodeLocked = structuredClone(withLine);
    nodeLocked.views[1]!.layers[0]!.nodes[0]!.locked = true;
    expect(() => moveNodePointV3(nodeLocked, view.id, layer.id, lineId, 0, 1, 1))
      .toThrowError(expect.objectContaining({ code: "node_locked" }));
    expect(() => moveNodePointV3(withLine, view.id, layer.id, crypto.randomUUID(), 0, 1, 1))
      .toThrowError(expect.objectContaining({ code: "node_not_found" }));

    const parameterized = structuredClone(withLine);
    const parameterId = crypto.randomUUID();
    parameterized.parameters.push({
      id: parameterId, name: "X", type: "number", unit: null, defaultValue: 100,
      minimum: null, maximum: null, formula: null,
    });
    const parameterizedNode = parameterized.views[1]!.layers[0]!.nodes[0]!;
    if (parameterizedNode.kind === "line") parameterizedNode.geometry.points[0]!.x = {
      kind: "parameter", parameterId,
    };
    expect(() => moveNodePointV3(parameterized, view.id, layer.id, lineId, 0, 1, 1))
      .toThrowError(expect.objectContaining({ code: "non_constant_geometry" }));
    expect(withLine).toEqual(before);
  });

  it("groups distinct root nodes immutably and ungroups an identity group at the same paint position", () => {
    const initial = newTemplateContentV3(), view = initial.views[1]!, layer = view.layers[0]!;
    const [one, firstId] = addBasicNodeV3(initial, view.id, layer.id, "rectangle");
    const [two, middleId] = addBasicNodeV3(one, view.id, layer.id, "ellipse");
    const [three, lastId] = addBasicNodeV3(two, view.id, layer.id, "text");
    const before = structuredClone(three);
    const [grouped, groupId] = groupRootNodesV3(three, view.id, layer.id, [middleId, lastId]);
    const groupedNodes = grouped.views[1]!.layers[0]!.nodes;
    const group = groupedNodes.find(node => node.id === groupId)!;

    expect(three).toEqual(before);
    expect(group).toMatchObject({ kind: "group", geometry: { childIds: [middleId, lastId] } });
    expect(groupedNodes.filter(node => node.id === firstId || node.id === groupId).map(node => node.id))
      .toEqual([firstId, groupId]);

    const ungrouped = ungroupRootNodeV3(grouped, view.id, layer.id, groupId);
    expect(ungrouped.views[1]!.layers[0]!.nodes.map(node => node.id)).toEqual([firstId, middleId, lastId]);
    expect(validateTemplateContentV3(ungrouped).valid).toBe(true);
  });

  it("rejects a non-contiguous group selection to preserve paint order", () => {
    const initial = newTemplateContentV3(), view = initial.views[1]!, layer = view.layers[0]!;
    const [one, firstId] = addBasicNodeV3(initial, view.id, layer.id, "rectangle");
    const [two] = addBasicNodeV3(one, view.id, layer.id, "ellipse");
    const [three, lastId] = addBasicNodeV3(two, view.id, layer.id, "text");

    expect(() => groupRootNodesV3(three, view.id, layer.id, [firstId, lastId]))
      .toThrowError(expect.objectContaining({ code: "non_contiguous_group_selection" }));
  });

  it("bakes composable group TRS into children and rejects a composition that creates skew", () => {
    const initial = newTemplateContentV3(), view = initial.views[1]!, layer = view.layers[0]!;
    const [one, firstId] = addBasicNodeV3(initial, view.id, layer.id, "rectangle");
    const [two, secondId] = addBasicNodeV3(one, view.id, layer.id, "ellipse");
    const [grouped, groupId] = groupRootNodesV3(two, view.id, layer.id, [firstId, secondId]);
    const transformed = structuredClone(grouped);
    const nodes = transformed.views[1]!.layers[0]!.nodes;
    const group = nodes.find(node => node.id === groupId)!;
    group.transform = {
      translateX: constantExpressionV3(20), translateY: constantExpressionV3(30),
      rotationDegrees: constantExpressionV3(90), scaleX: constantExpressionV3(2), scaleY: constantExpressionV3(2),
    };
    const first = nodes.find(node => node.id === firstId)!;
    first.transform = {
      ...first.transform,
      translateX: constantExpressionV3(5), translateY: constantExpressionV3(7),
    };
    const ungrouped = ungroupRootNodeV3(transformed, view.id, layer.id, groupId);
    const result = ungrouped.views[1]!.layers[0]!.nodes.find(node => node.id === firstId)!;
    expect(result.transform.translateX).toEqual(constantExpressionV3(6));
    expect(result.transform.translateY).toEqual(constantExpressionV3(40));
    expect(result.transform.rotationDegrees).toMatchObject({ kind: "constant", value: 90 });
    expect(result.transform.scaleX).toEqual(constantExpressionV3(2));
    expect(result.transform.scaleY).toEqual(constantExpressionV3(2));

    const skewed = structuredClone(grouped);
    const skewNodes = skewed.views[1]!.layers[0]!.nodes;
    skewNodes.find(node => node.id === groupId)!.transform.scaleX = constantExpressionV3(2);
    skewNodes.find(node => node.id === firstId)!.transform.rotationDegrees = constantExpressionV3(45);
    expect(() => ungroupRootNodeV3(skewed, view.id, layer.id, groupId))
      .toThrowError(expect.objectContaining({ code: "non_decomposable_transform" }));

    const translucent = structuredClone(grouped);
    translucent.views[1]!.layers[0]!.nodes.find(node => node.id === groupId)!.opacity = 0.5;
    expect(() => ungroupRootNodeV3(translucent, view.id, layer.id, groupId))
      .toThrowError(expect.objectContaining({ code: "group_compositing" }));
  });

  it("uses stored paint order when imported group child ids have another order", () => {
    const initial = newTemplateContentV3(), view = initial.views[1]!, layer = view.layers[0]!;
    const [one, firstId] = addBasicNodeV3(initial, view.id, layer.id, "rectangle");
    const [two, secondId] = addBasicNodeV3(one, view.id, layer.id, "ellipse");
    const [grouped, groupId] = groupRootNodesV3(two, view.id, layer.id, [firstId, secondId]);
    const imported = structuredClone(grouped);
    const group = imported.views[1]!.layers[0]!.nodes.find(node => node.id === groupId)!;
    if (group.kind !== "group") throw new Error("Expected group.");
    group.geometry.childIds = [secondId, firstId];

    const ungrouped = ungroupRootNodeV3(imported, view.id, layer.id, groupId);
    expect(ungrouped.views[1]!.layers[0]!.nodes.map(node => node.id)).toEqual([firstId, secondId]);
  });

  it("moves a root by one paint-order step without treating group children as roots", () => {
    const initial = newTemplateContentV3(), view = initial.views[1]!, layer = view.layers[0]!;
    const [one, firstId] = addBasicNodeV3(initial, view.id, layer.id, "rectangle");
    const [two, secondId] = addBasicNodeV3(one, view.id, layer.id, "ellipse");
    const [three, thirdId] = addBasicNodeV3(two, view.id, layer.id, "text");
    const [grouped, groupId] = groupRootNodesV3(three, view.id, layer.id, [firstId, secondId]);
    const moved = reorderRootNodeStepV3(grouped, view.id, layer.id, groupId, "forward");
    const nodes = moved.views[1]!.layers[0]!.nodes;
    const owned = new Set([firstId, secondId]);

    expect(nodes.filter(node => !owned.has(node.id)).map(node => node.id)).toEqual([thirdId, groupId]);
    expect((nodes.find(node => node.id === groupId)! as { geometry: { childIds: string[] } }).geometry.childIds)
      .toEqual([firstId, secondId]);
    expect(reorderRootNodeStepV3(moved, view.id, layer.id, groupId, "forward")).toBe(moved);
    expect(() => reorderRootNodeStepV3(grouped, view.id, layer.id, firstId, "forward"))
      .toThrowError(expect.objectContaining({ code: "node_not_root" }));
  });

  it("rotates a root rectangle around its world-space geometric center", () => {
    const initial = newTemplateContentV3(), view = initial.views[1]!, layer = view.layers[0]!;
    const [withRectangle, nodeId] = addBasicNodeV3(initial, view.id, layer.id, "rectangle");
    const source = structuredClone(withRectangle);
    const node = source.views[1]!.layers[0]!.nodes[0]!;
    node.transform = {
      translateX: constantExpressionV3(17), translateY: constantExpressionV3(-9),
      rotationDegrees: constantExpressionV3(30), scaleX: constantExpressionV3(2), scaleY: constantExpressionV3(0.5),
    };
    const before = structuredClone(source);
    const rotated = setRootNodeRotationAroundCenterV3(source, view.id, layer.id, nodeId, 90);
    const result = rotated.views[1]!.layers[0]!.nodes[0]!;

    expect(source).toEqual(before);
    expect(result.transform.rotationDegrees).toEqual(constantExpressionV3(90));
    expect(constantExpressionValueV3(result.transform.translateX)).toBeCloseTo(345.1986372867092, 10);
    expect(constantExpressionValueV3(result.transform.translateY)).toBeCloseTo(-120.54328524455039, 10);
    expect(setRootNodeRotationAroundCenterV3(rotated, view.id, layer.id, nodeId, 90)).toBe(rotated);
  });

  it("rotates a root group around bounds that include child transforms", () => {
    const initial = newTemplateContentV3(), view = initial.views[1]!, layer = view.layers[0]!;
    const [one, firstId] = addBasicNodeV3(initial, view.id, layer.id, "rectangle");
    const [two, secondId] = addBasicNodeV3(one, view.id, layer.id, "rectangle");
    const positioned = structuredClone(two);
    positioned.views[1]!.layers[0]!.nodes.find(node => node.id === secondId)!.transform.translateX = constantExpressionV3(200);
    const [grouped, groupId] = groupRootNodesV3(positioned, view.id, layer.id, [firstId, secondId]);
    const rotated = setRootNodeRotationAroundCenterV3(grouped, view.id, layer.id, groupId, 180);
    const group = rotated.views[1]!.layers[0]!.nodes.find(node => node.id === groupId)!;

    expect(group.transform.rotationDegrees).toEqual(constantExpressionV3(180));
    expect(constantExpressionValueV3(group.transform.translateX)).toBeCloseTo(540, 10);
    expect(constantExpressionValueV3(group.transform.translateY)).toBeCloseTo(270, 10);
    expect(rotated.views[1]!.layers[0]!.nodes.find(node => node.id === firstId)!.transform)
      .toEqual(grouped.views[1]!.layers[0]!.nodes.find(node => node.id === firstId)!.transform);
  });

  it("rejects non-root, locked, parameterized and degenerate rotation inputs", () => {
    const initial = newTemplateContentV3(), view = initial.views[1]!, layer = view.layers[0]!;
    const [one, firstId] = addBasicNodeV3(initial, view.id, layer.id, "rectangle");
    const [two, secondId] = addBasicNodeV3(one, view.id, layer.id, "rectangle");
    const [grouped] = groupRootNodesV3(two, view.id, layer.id, [firstId, secondId]);
    expect(() => setRootNodeRotationAroundCenterV3(grouped, view.id, layer.id, firstId, 45))
      .toThrowError(expect.objectContaining({ code: "node_not_root" }));

    const locked = structuredClone(one);
    locked.views[1]!.layers[0]!.nodes[0]!.locked = true;
    expect(() => setRootNodeRotationAroundCenterV3(locked, view.id, layer.id, firstId, 45))
      .toThrowError(expect.objectContaining({ code: "node_locked" }));

    const parameterized = structuredClone(one);
    const parameterId = crypto.randomUUID();
    parameterized.parameters.push({ id: parameterId, name: "Угол", type: "number", unit: null,
      defaultValue: 0, minimum: null, maximum: null, formula: null });
    parameterized.views[1]!.layers[0]!.nodes[0]!.transform.rotationDegrees = { kind: "parameter", parameterId };
    expect(() => setRootNodeRotationAroundCenterV3(parameterized, view.id, layer.id, firstId, 45))
      .toThrowError(expect.objectContaining({ code: "non_constant_transform" }));

    const degenerate = structuredClone(one);
    degenerate.views[1]!.layers[0]!.nodes[0]!.transform.scaleX = constantExpressionV3(0);
    expect(() => setRootNodeRotationAroundCenterV3(degenerate, view.id, layer.id, firstId, 45))
      .toThrowError(expect.objectContaining({ code: "degenerate_transform" }));
    const degenerateChild = structuredClone(grouped);
    degenerateChild.views[1]!.layers[0]!.nodes.find(node => node.id === firstId)!.transform.scaleY = constantExpressionV3(0);
    const groupId = degenerateChild.views[1]!.layers[0]!.nodes.find(node => node.kind === "group")!.id;
    expect(() => setRootNodeRotationAroundCenterV3(degenerateChild, view.id, layer.id, groupId, 45))
      .toThrowError(expect.objectContaining({ code: "degenerate_transform" }));
    expect(() => setRootNodeRotationAroundCenterV3(one, view.id, layer.id, firstId, Number.NaN))
      .toThrowError(expect.objectContaining({ code: "invalid_rotation" }));

    const [withBezier, bezierId] = addBasicNodeV3(initial, view.id, layer.id, "bezier");
    expect(() => setRootNodeRotationAroundCenterV3(withBezier, view.id, layer.id, bezierId, 45))
      .not.toThrow();
  });

  it("rejects structural operations when an imported outer group contains a repeat prototype", () => {
    const initial = newTemplateContentV3(), view = initial.views[1]!, layer = view.layers[0]!;
    const [withType, typeId] = addContactTypeGroupV3(initial, "Сигнальные");
    const [withNode, nodeId] = addBasicNodeV3(withType, view.id, layer.id, "rectangle");
    const [withPoint, pointId] = addContactPointV3(withNode, view.id, { contactTypeGroupId: typeId });
    const [repeated, repeatIds] = createRepeatPrototypeV3(withPoint, {
      viewId: view.id, layerId: layer.id, prototypeNodeId: nodeId, prototypePointId: pointId,
      count: 2, step: { x: constantExpressionV3(0), y: constantExpressionV3(20) },
    });
    const [withSibling, siblingId] = addBasicNodeV3(repeated, view.id, layer.id, "ellipse");
    const imported = structuredClone(withSibling);
    const importedLayer = imported.views[1]!.layers[0]!;
    const prototype = importedLayer.nodes.find(node => node.id === repeatIds.groupId)!;
    if (prototype.kind !== "group") throw new Error("Expected repeat group.");
    const outerId = crypto.randomUUID();
    importedLayer.nodes.push({
      ...structuredClone(prototype), id: outerId,
      geometry: { childIds: [repeatIds.groupId] },
      transform: {
        translateX: constantExpressionV3(0), translateY: constantExpressionV3(0),
        rotationDegrees: constantExpressionV3(0), scaleX: constantExpressionV3(1), scaleY: constantExpressionV3(1),
      },
    });
    expect(() => groupRootNodesV3(imported, view.id, layer.id, [outerId, siblingId]))
      .toThrowError(expect.objectContaining({ code: "repeat_prototype_group" }));
    expect(() => ungroupRootNodeV3(imported, view.id, layer.id, outerId))
      .toThrowError(expect.objectContaining({ code: "repeat_prototype_group" }));
    expect(() => setRootNodeRotationAroundCenterV3(imported, view.id, layer.id, outerId, 90))
      .toThrowError(expect.objectContaining({ code: "repeat_prototype_group" }));
  });
});
