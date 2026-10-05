import { upgradeTemplateContentV1ToV2, type TemplateContentV2, type TemplateV2Diagnostic } from "./template-model-v2";
import { validateTemplateContentV3, type ContactTypeGroupV3, type TemplateContentV3, type TemplateV3Upgrade } from "./template-model-v3";

function derivedUuid(seed: string): string {
  let a = 0x811c9dc5, b = 0x9e3779b9, c = 0x85ebca6b, d = 0xc2b2ae35;
  for (let index = 0; index < seed.length; index += 1) {
    const code = seed.charCodeAt(index);
    a = Math.imul(a ^ code, 0x01000193);
    b = Math.imul(b ^ code, 0x27d4eb2d);
    c = Math.imul(c ^ code, 0x165667b1);
    d = Math.imul(d ^ code, 0x85ebca77);
  }
  const hex = [a, b, c, d].map(value => (value >>> 0).toString(16).padStart(8, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-5${hex.slice(13, 16)}-${((parseInt(hex.slice(16, 18), 16) & 0x3f) | 0x80).toString(16).padStart(2, "0")}${hex.slice(18, 20)}-${hex.slice(20, 32)}`;
}

function occupiedIds(content: TemplateContentV2): Set<string> {
  return new Set([
    ...content.views.flatMap(view => [view.id, ...view.layers.flatMap(layer => [layer.id, ...layer.nodes.map(node => node.id)]), ...view.contactPoints.map(point => point.id), ...view.bundlePorts.map(port => port.id)]),
    ...content.logicalContacts.map(contact => contact.id),
    ...content.parameters.map(parameter => parameter.id),
    ...content.repeaters.map(repeater => repeater.id),
    ...content.assets.map(asset => asset.assetId),
    ...content.articleParameterPresets.map(preset => preset.id),
  ]);
}

function uniqueDerivedUuid(seed: string, used: Set<string>): string {
  let attempt = 0, candidate = derivedUuid(seed);
  while (used.has(candidate)) candidate = derivedUuid(`${seed}:${++attempt}`);
  used.add(candidate);
  return candidate;
}

function contactTypeName(value: string): string | null {
  const normalized = value.trim();
  return normalized && normalized.length <= 128 && !/[\u0000-\u001f\u007f-\u009f]/.test(normalized) ? normalized : null;
}

/** Deterministic and non-mutating v2 to v3 upgrade. */
export function upgradeTemplateContentV2ToV3(content: TemplateContentV2): TemplateV3Upgrade {
  const diagnostics: TemplateV2Diagnostic[] = [];
  const used = occupiedIds(content), groups: ContactTypeGroupV3[] = [], groupByName = new Map<string, string>();
  for (const contact of content.logicalContacts) {
    const name = contactTypeName(contact.contactType);
    if (!name) {
      if (contact.contactType) diagnostics.push({ code: "legacy_contact_type_dropped", path: `$.logicalContacts[id=${contact.id}].contactType`, message: "Некорректный устаревший тип контакта не перенесён в группу." });
      continue;
    }
    const key = name.toLowerCase();
    if (groupByName.has(key)) continue;
    const id = uniqueDerivedUuid(`template-v3:contact-type-group:${key}`, used);
    groupByName.set(key, id);
    groups.push({ id, name });
  }

  const cloned = structuredClone(content);
  const { articleParameterPresets, ...core } = cloned;
  const upgraded: TemplateContentV3 = {
    ...core,
    schemaVersion: 3,
    logicalContacts: content.logicalContacts.map(contact => ({
      id: contact.id,
      number: contact.number,
      name: contact.name,
      circuitText: null,
      contactTypeGroupId: contactTypeName(contact.contactType)
        ? groupByName.get(contactTypeName(contact.contactType)!.toLowerCase()) ?? null
        : null,
    })),
    contactTypeGroups: groups,
    articleVariants: articleParameterPresets.map(preset => ({
      id: preset.id,
      sourceId: preset.sourceId,
      entityType: preset.entityType,
      articleKey: preset.articleKey,
      parameterValues: preset.values.map(value => ({ ...value })),
      contactGroups: null,
    })),
  };
  const validation = validateTemplateContentV3(upgraded);
  if (!validation.valid) diagnostics.push(...validation.diagnostics.map(item => ({ ...item, code: `upgrade_${item.code}` })));
  return { content: upgraded, diagnostics };
}

/** Uses the established deterministic v1 to v2 conversion as the only legacy path. */
export function upgradeTemplateContentV1ToV3(value: unknown): TemplateV3Upgrade {
  const v2 = upgradeTemplateContentV1ToV2(value);
  const v3 = upgradeTemplateContentV2ToV3(v2.content);
  return { content: v3.content, diagnostics: [...v2.diagnostics, ...v3.diagnostics] };
}
