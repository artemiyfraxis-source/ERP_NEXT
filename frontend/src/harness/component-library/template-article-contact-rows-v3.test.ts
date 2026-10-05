// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  addAdditionalViewV2,
  addBasicNodeV2,
  addBundlePortV2,
  addContactPointV2,
  attachRepeatDomainV2,
  constantExpressionV2,
  createRepeatPrototypeV2,
  linkLogicalContactPointV2,
  newTemplateContentV2,
} from "./template-commands-v2";
import { materializeArticleContactRowsV3 } from "./template-article-contact-rows-v3";
import { materializeArticleVariantV3 } from "./template-article-materialization-v3";
import type { ArticleVariantV3, TemplateContentV3 } from "./template-model-v3";
import { expandTemplateRepeatsV2 } from "./template-repeat-v2";
import { upgradeTemplateContentV2ToV3 } from "./template-upgrade-v3";

interface Fixture {
  readonly content: TemplateContentV3;
  readonly variant: ArticleVariantV3;
  readonly fixedContactId: string;
  readonly repeatedContactId: string;
  readonly repeatDomainId: string;
  readonly bundlePortId: string;
}

function fixture(target = 10): Fixture {
  let content = newTemplateContentV2();
  const e4 = content.views[0]!, drawing = content.views[1]!;
  let fixedPointId: string;
  [content, fixedPointId] = addContactPointV2(content, e4.id, {
    number: "100", name: "Корпус", contactType: "signal",
    x: constantExpressionV2(12), y: constantExpressionV2(18), direction: "left",
  });
  const fixedContactId = content.views[0]!.contactPoints.find(point => point.id === fixedPointId)!.logicalContactId;

  let e4NodeId: string;
  [content, e4NodeId] = addBasicNodeV2(content, e4.id, e4.layers[0]!.id, "line");
  let repeatedPointId: string;
  [content, repeatedPointId] = addContactPointV2(content, e4.id, {
    number: "1", name: "Сигнал", contactType: "signal",
    x: constantExpressionV2(30), y: constantExpressionV2(40), direction: "right",
  });
  let repeatIds: ReturnType<typeof createRepeatPrototypeV2>[1];
  [content, repeatIds] = createRepeatPrototypeV2(content, {
    viewId: e4.id,
    layerId: e4.layers[0]!.id,
    prototypeNodeId: e4NodeId,
    prototypePointId: repeatedPointId,
    count: 2,
    step: { x: constantExpressionV2(0), y: constantExpressionV2(10) },
  });
  const repeatedContactId = content.repeaters[0]!.logicalContactIds[0]!;

  [content] = linkLogicalContactPointV2(content, drawing.id, fixedContactId, {
    x: constantExpressionV2(50), y: constantExpressionV2(60), direction: "right",
  });
  let drawingNodeId: string;
  [content, drawingNodeId] = addBasicNodeV2(content, drawing.id, drawing.layers[0]!.id, "rectangle");
  let drawingRepeatPointId: string;
  [content, drawingRepeatPointId] = linkLogicalContactPointV2(content, drawing.id, repeatedContactId, {
    x: constantExpressionV2(70), y: constantExpressionV2(80), direction: "left",
  });
  [content] = attachRepeatDomainV2(content, {
    viewId: drawing.id,
    layerId: drawing.layers[0]!.id,
    prototypeNodeId: drawingNodeId,
    repeatDomainId: repeatIds.repeatDomainId,
    contactPointIds: [drawingRepeatPointId],
    step: { x: constantExpressionV2(20), y: constantExpressionV2(0) },
  });

  let additionalViewId: string;
  [content, additionalViewId] = addAdditionalViewV2(content, "Монтажный вид");
  const additional = content.views.find(view => view.id === additionalViewId)!;
  [content] = linkLogicalContactPointV2(content, additional.id, fixedContactId, {
    x: constantExpressionV2(90), y: constantExpressionV2(100), direction: "up",
  });
  let additionalNodeId: string;
  [content, additionalNodeId] = addBasicNodeV2(content, additional.id, additional.layers[0]!.id, "ellipse");
  let additionalRepeatPointId: string;
  [content, additionalRepeatPointId] = linkLogicalContactPointV2(content, additional.id, repeatedContactId, {
    x: constantExpressionV2(110), y: constantExpressionV2(120), direction: "down",
  });
  [content] = attachRepeatDomainV2(content, {
    viewId: additional.id,
    layerId: additional.layers[0]!.id,
    prototypeNodeId: additionalNodeId,
    repeatDomainId: repeatIds.repeatDomainId,
    contactPointIds: [additionalRepeatPointId],
    step: { x: constantExpressionV2(0), y: constantExpressionV2(15) },
  });
  let bundlePortId: string;
  [content, bundlePortId] = addBundlePortV2(content, additional.id, {
    name: "Выход пучка", x: constantExpressionV2(130), y: constantExpressionV2(140), direction: "right",
  });

  const upgraded = upgradeTemplateContentV2ToV3(content).content;
  const groupId = upgraded.contactTypeGroups[0]!.id;
  upgraded.logicalContacts.find(contact => contact.id === repeatedContactId)!.circuitText = "DATA";
  const variant: ArticleVariantV3 = {
    id: crypto.randomUUID(),
    sourceId: "БД.СОЕД",
    entityType: "connector",
    articleKey: `XH-${target}`,
    parameterValues: [],
    contactGroups: [{
      contactTypeGroupId: groupId,
      contactCount: target,
      allowedTerminalArticleKeys: [
        { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "SXH-001T-P0.6" },
        { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "SXH-002T-P0.6" },
      ],
    }],
  };
  upgraded.articleVariants.push(variant);
  return {
    content: upgraded,
    variant,
    fixedContactId,
    repeatedContactId,
    repeatDomainId: repeatIds.repeatDomainId,
    bundlePortId,
  };
}

describe("article contact rows v3", () => {
  it.each([2, 10])("materializes fixed and repeated rows for a %i-contact article", target => {
    const { content, variant, fixedContactId } = fixture(target);

    const rows = materializeArticleContactRowsV3(content, variant);

    expect(rows).toHaveLength(target);
    expect(rows.filter(candidate => candidate.prototypeLogicalContactId === fixedContactId)).toHaveLength(1);
    expect(rows.map(candidate => candidate.number)).toEqual([
      "100",
      ...Array.from({ length: target - 1 }, (_, index) => String(index + 1)),
    ]);
  });

  it("groups E4, drawing and additional points into one row and excludes bundle ports", () => {
    const { content, variant, fixedContactId, repeatedContactId, bundlePortId } = fixture(2);

    const rows = materializeArticleContactRowsV3(content, variant.id);
    const fixed = rows.find(candidate => candidate.prototypeLogicalContactId === fixedContactId)!;
    const repeated = rows.find(candidate => candidate.prototypeLogicalContactId === repeatedContactId)!;

    expect(fixed.representations.map(representation => representation.viewKind)).toEqual(["e4", "drawing", "additional"]);
    expect(repeated.representations.map(representation => representation.viewKind)).toEqual(["e4", "drawing", "additional"]);
    expect(fixed.representations.map(representation => [representation.x, representation.y])).toEqual([
      [12, 18], [50, 60], [90, 100],
    ]);
    expect(repeated.representations.every(representation => representation.occurrenceKey === repeated.key)).toBe(true);
    expect(rows.flatMap(candidate => candidate.representations).map(representation => representation.pointId))
      .not.toContain(bundlePortId);
    expect(repeated.circuitText).toBe("DATA");
  });

  it("copies every allowed terminal onto rows of its contact group", () => {
    const { content, variant } = fixture(2);
    const configuredKeys = variant.contactGroups![0]!.allowedTerminalArticleKeys;

    const rows = materializeArticleContactRowsV3(content, variant);

    expect(rows.every(candidate => candidate.allowedTerminalArticleKeys.map(key => key.articleKey).join(",") ===
      "SXH-001T-P0.6,SXH-002T-P0.6")).toBe(true);
    expect(rows[0]!.allowedTerminalArticleKeys).not.toBe(configuredKeys);
    expect(rows[0]!.allowedTerminalArticleKeys[0]).not.toBe(configuredKeys[0]);
  });

  it("resolves fixed representation coordinates with article parameter values", () => {
    const { content, variant, fixedContactId } = fixture(2);
    const parameterId = crypto.randomUUID();
    content.parameters.push({
      id: parameterId,
      name: "Положение корпуса",
      type: "number",
      unit: "мм",
      defaultValue: 20,
      minimum: 0,
      maximum: 100,
      formula: null,
    });
    variant.parameterValues.push({ parameterId, value: 35 });
    const fixedPoint = content.views[0]!.contactPoints.find(point => point.logicalContactId === fixedContactId)!;
    fixedPoint.x = { kind: "parameter", parameterId };

    const fixed = materializeArticleContactRowsV3(content, variant)
      .find(candidate => candidate.prototypeLogicalContactId === fixedContactId)!;

    expect(fixed.representations[0]!.x).toBe(35);
  });

  it("uses the repeat engine numbering rule for numeric and textual stride-two prototypes", () => {
    const { content, variant, repeatedContactId } = fixture(5);
    const domain = content.repeaters[0]!;
    content.logicalContacts.find(contact => contact.id === repeatedContactId)!.number = "A";
    const secondId = crypto.randomUUID();
    content.logicalContacts.push({
      id: secondId,
      number: "2",
      name: "Силовой",
      circuitText: null,
      contactTypeGroupId: content.logicalContacts.find(contact => contact.id === repeatedContactId)!.contactTypeGroupId,
    });
    domain.logicalContactIds.push(secondId);
    for (const view of content.views) {
      const pointId = crypto.randomUUID();
      view.contactPoints.push({
        id: pointId,
        logicalContactId: secondId,
        x: constantExpressionV2(150),
        y: constantExpressionV2(160),
        direction: "right",
      });
      view.repeatPlacements[0]!.contactPointIds.push(pointId);
    }

    const materialized = materializeArticleVariantV3(content, variant);
    const engineNumbers = expandTemplateRepeatsV2(materialized.repeatContent, materialized.repeatOptions)
      .find(view => view.viewId === content.views[0]!.id)!.placements[0]!.occurrences
      .flatMap(occurrence => occurrence.contactPoints.map(point => point.number));
    const rowNumbers = materializeArticleContactRowsV3(content, variant).slice(1).map(candidate => candidate.number);

    expect(rowNumbers).toEqual(["A-1", "2", "A-2", "4"]);
    expect(rowNumbers).toEqual(engineNumbers);
  });

  it("keeps keys and order stable, freezes nested output and does not mutate inputs", () => {
    const { content, variant, repeatDomainId, repeatedContactId } = fixture(10);
    const beforeContent = structuredClone(content), beforeVariant = structuredClone(variant);

    const first = materializeArticleContactRowsV3(content, variant);
    const second = materializeArticleContactRowsV3(structuredClone(content), variant.id);

    expect(second.map(candidate => candidate.key)).toEqual(first.map(candidate => candidate.key));
    expect(first[1]!.key).toBe(`${repeatDomainId}:0:${repeatedContactId}`);
    expect(content).toEqual(beforeContent);
    expect(variant).toEqual(beforeVariant);
    expect(Object.isFrozen(first)).toBe(true);
    expect(Object.isFrozen(first[0])).toBe(true);
    expect(Object.isFrozen(first[0]!.representations)).toBe(true);
    expect(Object.isFrozen(first[0]!.representations[0])).toBe(true);
    expect(Object.isFrozen(first[0]!.allowedTerminalArticleKeys)).toBe(true);
    expect(Object.isFrozen(first[0]!.allowedTerminalArticleKeys[0])).toBe(true);
  });

  it("uses default repeats and no terminals for legacy null contact groups", () => {
    const { content, variant } = fixture(10);
    variant.contactGroups = null;

    const rows = materializeArticleContactRowsV3(content, variant);

    expect(rows.map(candidate => candidate.number)).toEqual(["100", "1", "2"]);
    expect(rows.every(candidate => candidate.allowedTerminalArticleKeys.length === 0)).toBe(true);
  });

  it("rejects a legacy variant that would exceed the article-wide row budget", () => {
    const { content, variant, repeatedContactId } = fixture(10);
    variant.contactGroups = null;
    const countParameterId = content.repeaters[0]!.countParameterId;
    const parameter = content.parameters.find(candidate => candidate.id === countParameterId)!;
    parameter.maximum = 1_000;
    parameter.defaultValue = 1_000;
    const repeated = content.logicalContacts.find(candidate => candidate.id === repeatedContactId)!;
    const secondRepeatedId = crypto.randomUUID();
    content.logicalContacts.push({
      ...repeated,
      id: secondRepeatedId,
      number: "2",
    });
    content.repeaters[0]!.logicalContactIds.push(secondRepeatedId);
    for (const view of content.views.slice(1)) view.repeatPlacements = [];

    expect(() => materializeArticleContactRowsV3(content, variant)).toThrowError(expect.objectContaining({
      code: "article_contact_row_budget",
    }));
  });
});
