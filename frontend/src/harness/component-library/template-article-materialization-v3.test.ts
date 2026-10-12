// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  addBasicNodeV2,
  addContactPointV2,
  createRepeatPrototypeV2,
  newTemplateContentV2,
} from "./template-commands-v2";
import { expandTemplateRepeatsV2 } from "./template-repeat-v2";
import {
  ArticleVariantMaterializationV3Error,
  materializeArticleVariantV3,
} from "./template-article-materialization-v3";
import type { ArticleVariantV3, TemplateContentV3 } from "./template-model-v3";
import { upgradeTemplateContentV2ToV3 } from "./template-upgrade-v3";

interface Fixture {
  readonly content: TemplateContentV3;
  readonly groupId: string;
  readonly countParameterId: string;
  readonly domainId: string;
  readonly variant: ArticleVariantV3;
}

function fixture(target = 10): Fixture {
  const initial = newTemplateContentV2(), view = initial.views[0]!, layer = view.layers[0]!;
  const [withFixed] = addContactPointV2(initial, view.id, { number: "100", name: "Фиксированный", contactType: "signal" });
  const [withNode, nodeId] = addBasicNodeV2(withFixed, view.id, layer.id, "line");
  const [withPrototype, pointId] = addContactPointV2(withNode, view.id, { number: "1", name: "Повтор", contactType: "signal" });
  const [repeated, ids] = createRepeatPrototypeV2(withPrototype, {
    viewId: view.id,
    layerId: layer.id,
    prototypeNodeId: nodeId,
    prototypePointId: pointId,
    count: 2,
    step: { x: { kind: "constant", value: 0 }, y: { kind: "constant", value: 12 } },
  });
  const content = upgradeTemplateContentV2ToV3(repeated).content;
  const groupId = content.contactTypeGroups[0]!.id;
  const variant: ArticleVariantV3 = {
    id: crypto.randomUUID(), sourceId: "db", entityType: "connector", articleKey: `X-${target}`,
    parameterValues: [],
    contactGroups: [{ contactTypeGroupId: groupId, contactCount: target, allowedTerminalArticleKeys: [] }],
  };
  content.articleVariants.push(variant);
  return { content, groupId, countParameterId: ids.countParameterId, domainId: ids.repeatDomainId, variant };
}

function errorCode(action: () => unknown): string | undefined {
  try { action(); return undefined; }
  catch (error) { return error instanceof ArticleVariantMaterializationV3Error ? error.code : undefined; }
}

describe("article variant v3 materialization", () => {
  it("derives a repeat override from requested total, fixed contacts and stride", () => {
    const { content, variant, groupId, countParameterId, domainId } = fixture(10);
    const before = structuredClone(content);

    const result = materializeArticleVariantV3(content, variant.id);

    expect(result.overrides).toEqual({ [countParameterId]: 9 });
    expect(result.repeatCounts).toEqual([{
      contactTypeGroupId: groupId,
      repeatDomainId: domainId,
      countParameterId,
      fixedContactCount: 1,
      contactsPerOccurrence: 1,
      requestedContactCount: 10,
      repeatCount: 9,
    }]);
    expect(content).toEqual(before);
  });

  it("returns an input that expands through the existing v2 repeat engine", () => {
    const { content, variant } = fixture(10);
    const result = materializeArticleVariantV3(content, variant);

    const expanded = expandTemplateRepeatsV2(result.repeatContent, result.repeatOptions);
    const e4 = expanded.find(view => view.viewId === content.views[0]!.id)!;

    expect(e4.placements[0]!.occurrences).toHaveLength(9);
    expect(e4.placements[0]!.occurrences.at(-1)!.contactPoints[0]!.number).toBe("9");
  });

  it("combines ordinary parameter values with the derived repeat count", () => {
    const { content, variant, countParameterId } = fixture(10);
    const widthId = crypto.randomUUID();
    content.parameters.push({ id: widthId, name: "Ширина", type: "number", unit: "мм", defaultValue: 20, minimum: 1, maximum: 100, formula: null });
    variant.parameterValues.push({ parameterId: widthId, value: 35 });

    expect(materializeArticleVariantV3(content, variant.id).overrides).toEqual({
      [widthId]: 35,
      [countParameterId]: 9,
    });
  });

  it("leaves repeat counts at defaults when contactGroups is null", () => {
    const { content, variant } = fixture(10);
    const widthId = crypto.randomUUID();
    content.parameters.push({ id: widthId, name: "Ширина", type: "number", unit: "мм", defaultValue: 20, minimum: 1, maximum: 100, formula: null });
    variant.parameterValues.push({ parameterId: widthId, value: 40 });
    variant.contactGroups = null;

    const result = materializeArticleVariantV3(content, variant.id);
    expect(result.overrides).toEqual({ [widthId]: 40 });
    expect(result.repeatCounts).toEqual([]);
    expect(expandTemplateRepeatsV2(result.repeatContent, result.repeatOptions)[0]!.placements[0]!.occurrences).toHaveLength(2);
  });

  it("rejects a missing article variant with an addressable error", () => {
    const { content } = fixture();
    try {
      materializeArticleVariantV3(content, crypto.randomUUID());
      throw new Error("Expected materialization failure.");
    } catch (error) {
      expect(error).toBeInstanceOf(ArticleVariantMaterializationV3Error);
      expect(error).toMatchObject({ code: "article_variant_missing", path: "$.articleVariants" });
    }
  });

  it("rejects totals below fixed contacts, zero repeats and non-divisible strides", () => {
    const below = fixture(0);
    expect(errorCode(() => materializeArticleVariantV3(below.content, below.variant.id))).toBe("contact_count_below_fixed");

    const zero = fixture(1);
    expect(errorCode(() => materializeArticleVariantV3(zero.content, zero.variant.id))).toBe("repeat_count_out_of_range");

    const stride = fixture(10);
    const prototype = stride.content.repeaters[0]!.logicalContactIds[0]!;
    stride.content.repeaters[0]!.logicalContactIds.push(prototype);
    expect(errorCode(() => materializeArticleVariantV3(stride.content, stride.variant.id))).toBe("contact_count_not_divisible");
  });

  it("rejects more than one repeat domain for the same contact group", () => {
    const { content, variant, groupId } = fixture();
    const secondCount = crypto.randomUUID();
    content.parameters.push({ id: secondCount, name: "Второй повтор", type: "integer", unit: "шт", defaultValue: 2, minimum: 1, maximum: 1_000, formula: null });
    content.repeaters.push({
      id: crypto.randomUUID(), countParameterId: secondCount,
      logicalContactIds: [content.logicalContacts.find(contact => contact.contactTypeGroupId === groupId)!.id],
    });

    expect(errorCode(() => materializeArticleVariantV3(content, variant.id))).toBe("ambiguous_group_repeat");
  });

  it("rejects a repeat domain that mixes contact type groups", () => {
    const { content, variant } = fixture();
    const otherGroupId = crypto.randomUUID(), otherContactId = crypto.randomUUID();
    content.contactTypeGroups.push({ id: otherGroupId, name: "power" });
    content.logicalContacts.push({ id: otherContactId, number: "200", name: "Power", circuitText: null, contactTypeGroupId: otherGroupId });
    content.repeaters[0]!.logicalContactIds.push(otherContactId);
    variant.contactGroups!.push({ contactTypeGroupId: otherGroupId, contactCount: 1, allowedTerminalArticleKeys: [] });

    expect(errorCode(() => materializeArticleVariantV3(content, variant.id))).toBe("mixed_repeat_contact_groups");
  });

  it("rejects an explicit repeat count in parameterValues", () => {
    const { content, variant, countParameterId } = fixture();
    variant.parameterValues.push({ parameterId: countParameterId, value: 3 });

    expect(errorCode(() => materializeArticleVariantV3(content, variant.id))).toBe("repeat_parameter_conflict");
  });

  it("turns invalid ordinary parameter values into a clear materialization error", () => {
    const { content, variant } = fixture();
    const widthId = crypto.randomUUID();
    content.parameters.push({ id: widthId, name: "Ширина", type: "number", unit: "мм", defaultValue: 20, minimum: 1, maximum: 100, formula: null });
    variant.parameterValues.push({ parameterId: widthId, value: 101 });

    try {
      materializeArticleVariantV3(content, variant.id);
      throw new Error("Expected materialization failure.");
    } catch (error) {
      expect(error).toMatchObject({
        code: "parameter_materialization_failed",
        path: "$.articleVariants[0].parameterValues",
      });
      expect((error as Error).message).toContain("больше максимума");
    }
  });
});
