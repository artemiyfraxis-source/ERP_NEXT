// @vitest-environment node
import { describe, expect, it } from "vitest";
import { addContactPointV2, newTemplateContentV2 } from "./template-commands-v2";
import { newTemplateContent } from "./template-model";
import { validateTemplateContentV3 } from "./template-model-v3";
import { upgradeTemplateContentV1ToV3, upgradeTemplateContentV2ToV3 } from "./template-upgrade-v3";

describe("component template v3 upgrades", () => {
  it("maps v2 contact types to deterministic shared groups and presets to variants", () => {
    const v2 = newTemplateContentV2();
    const [one] = addContactPointV2(v2, v2.views[0]!.id, { number: "1", name: "A", contactType: " Signal " });
    const [two] = addContactPointV2(one, one.views[0]!.id, { number: "2", name: "B", contactType: "signal" });
    const [three] = addContactPointV2(two, two.views[0]!.id, { number: "3", name: "C", contactType: "" });
    const parameterId = crypto.randomUUID(), presetId = crypto.randomUUID();
    three.parameters.push({ id: parameterId, name: "Шаг", type: "number", unit: "мм", defaultValue: 2.5, minimum: 1, maximum: 5, formula: null });
    three.articleParameterPresets.push({ id: presetId, sourceId: "db", entityType: "connector", articleKey: "XH", values: [{ parameterId, value: 3 }] });
    const before = structuredClone(three);

    const first = upgradeTemplateContentV2ToV3(three);
    const second = upgradeTemplateContentV2ToV3(structuredClone(three));

    expect(three).toEqual(before);
    expect(first.content).toEqual(second.content);
    expect(first.content.contactTypeGroups).toHaveLength(1);
    expect(first.content.contactTypeGroups[0]!.id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(first.content.logicalContacts.map(contact => contact.contactTypeGroupId)).toEqual([
      first.content.contactTypeGroups[0]!.id,
      first.content.contactTypeGroups[0]!.id,
      null,
    ]);
    expect(first.content.logicalContacts.every(contact => contact.circuitText === null)).toBe(true);
    expect(first.content.articleVariants).toEqual([{
      id: presetId, sourceId: "db", entityType: "connector", articleKey: "XH",
      parameterValues: [{ parameterId, value: 3 }], contactGroups: null,
    }]);
    expect(validateTemplateContentV3(first.content)).toEqual({ valid: true, diagnostics: [] });
  });

  it("preserves views, points, ports, parameters, repeaters and assets", () => {
    const v2 = newTemplateContentV2();
    const port = { id: crypto.randomUUID(), name: "Пучок", x: { kind: "constant" as const, value: 1 }, y: { kind: "constant" as const, value: 2 }, direction: "left" as const };
    v2.views[1]!.bundlePorts.push(port);
    const asset = { assetId: crypto.randomUUID(), fileName: "view.png", mediaType: "image/png", sha256: "a".repeat(64), sizeBytes: 100 };
    v2.assets.push(asset);

    const v3 = upgradeTemplateContentV2ToV3(v2).content;

    expect(v3.views).toEqual(v2.views);
    expect(v3.parameters).toEqual(v2.parameters);
    expect(v3.repeaters).toEqual(v2.repeaters);
    expect(v3.assets).toEqual(v2.assets);
    expect(v3.views).not.toBe(v2.views);
  });

  it("upgrades v1 through the established v2 conversion deterministically", () => {
    const v1 = newTemplateContent();
    const before = structuredClone(v1);

    const first = upgradeTemplateContentV1ToV3(v1);
    const second = upgradeTemplateContentV1ToV3(structuredClone(v1));

    expect(v1).toEqual(before);
    expect(first).toEqual(second);
    expect(first.content.schemaVersion).toBe(3);
    expect(validateTemplateContentV3(first.content).valid).toBe(true);
  });
});
