import type { TemplateContent as TemplateContentV1 } from "./template-model";
import {
  upgradeTemplateContentV1ToV2,
  validateTemplateContentV2,
  type TemplateContentV2,
  type TemplateAssetV2,
  type TemplateV2Diagnostic,
  type TemplateV2Upgrade,
} from "./template-model-v2";
import {
  validateTemplateContentV3,
  type TemplateContentV3,
  type TemplateV3Upgrade,
} from "./template-model-v3";
import { upgradeTemplateContentV1ToV3, upgradeTemplateContentV2ToV3 } from "./template-upgrade-v3";
import {
  isTemplateContentV4 as isStrictTemplateContentV4,
  upgradeTemplateContentV3ToV4,
  validateTemplateContentV4,
  type TemplateContentV4,
  type TemplateV4Upgrade,
} from "./template-model-v4";
import {
  isTemplateContentV5 as isStrictTemplateContentV5,
  upgradeTemplateContentV3ToV5,
  upgradeTemplateContentV4ToV5,
  validateTemplateContentV5,
  type TemplateContentV5,
  type TemplateV5Upgrade,
} from "./template-model-v5";

export type ComponentTemplateContent = TemplateContentV1 | TemplateContentV2 | TemplateContentV3 | TemplateContentV4 | TemplateContentV5;
export type TemplateEnvelopeAsset = Readonly<TemplateAssetV2>;
export interface TemplateAssetReconciliation {
  readonly assets: TemplateAssetV2[];
  readonly diagnostics: readonly TemplateV2Diagnostic[];
}

export class ComponentTemplateContentError extends Error {
  readonly diagnostics: readonly TemplateV2Diagnostic[];

  constructor(message: string, diagnostics: readonly TemplateV2Diagnostic[] = []) {
    super(message);
    this.name = "ComponentTemplateContentError";
    this.diagnostics = diagnostics;
  }
}

export function parseComponentTemplateContent(value: unknown): ComponentTemplateContent {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new ComponentTemplateContentError("Содержимое шаблона должно быть объектом.");
  const candidate = value as Record<string, unknown>;
  if (candidate.schemaVersion === 1) {
    if (!Array.isArray(candidate.views))
      throw new ComponentTemplateContentError("Шаблон v1 не содержит массив видов.");
    return value as TemplateContentV1;
  }
  if (candidate.schemaVersion === 2) {
    const validation = validateTemplateContentV2(value);
    if (!validation.valid)
      throw new ComponentTemplateContentError("Шаблон v2 не прошёл проверку содержимого.", validation.diagnostics);
    return value as TemplateContentV2;
  }
  if (candidate.schemaVersion === 3) {
    const validation = validateTemplateContentV3(value);
    if (!validation.valid)
      throw new ComponentTemplateContentError("Шаблон v3 не прошёл проверку содержимого.", validation.diagnostics);
    return value as TemplateContentV3;
  }
  if (candidate.schemaVersion === 4) {
    const validation = validateTemplateContentV4(value);
    if (!validation.valid)
      throw new ComponentTemplateContentError("Шаблон v4 не прошёл проверку содержимого.", validation.diagnostics);
    return value as TemplateContentV4;
  }
  if (candidate.schemaVersion === 5) {
    const validation = validateTemplateContentV5(value);
    if (!validation.valid)
      throw new ComponentTemplateContentError("Шаблон v5 не прошёл проверку содержимого.", validation.diagnostics);
    return value as TemplateContentV5;
  }
  throw new ComponentTemplateContentError("Шаблон имеет неподдерживаемую схему содержимого.");
}

export function isTemplateContentV1(content: ComponentTemplateContent): content is TemplateContentV1 {
  return content.schemaVersion === 1;
}

export function isTemplateContentV2(content: ComponentTemplateContent): content is TemplateContentV2 {
  return content.schemaVersion === 2;
}

export function isTemplateContentV3(content: ComponentTemplateContent): content is TemplateContentV3 {
  return content.schemaVersion === 3;
}

export function isTemplateContentV4(content: ComponentTemplateContent): content is TemplateContentV4 {
  return content.schemaVersion === 4 && isStrictTemplateContentV4(content);
}

export function isTemplateContentV5(content: ComponentTemplateContent): content is TemplateContentV5 {
  return content.schemaVersion === 5 && isStrictTemplateContentV5(content);
}

export function upgradeComponentTemplateContentV1(
  content: TemplateContentV1,
  envelopeAssets: readonly TemplateEnvelopeAsset[] = [],
): TemplateV2Upgrade {
  const upgraded = upgradeTemplateContentV1ToV2(content);
  const assets = normalizeTemplateEnvelopeAssets(envelopeAssets);
  const result = { ...upgraded, content: { ...upgraded.content, assets } };
  const validation = validateTemplateContentV2(result.content);
  return validation.valid ? result : { ...result, diagnostics: [...result.diagnostics, ...validation.diagnostics] };
}

export function upgradeComponentTemplateContentV2(content: TemplateContentV2): TemplateV3Upgrade {
  return upgradeTemplateContentV2ToV3(content);
}

export function upgradeComponentTemplateContentV3(content: TemplateContentV3): TemplateV4Upgrade {
  return upgradeTemplateContentV3ToV4(content);
}

export function upgradeComponentTemplateContentV3ToV5(content: TemplateContentV3): TemplateV5Upgrade {
  return upgradeTemplateContentV3ToV5(content);
}

export function upgradeComponentTemplateContentV4(content: TemplateContentV4): TemplateV5Upgrade {
  return upgradeTemplateContentV4ToV5(content);
}

export function upgradeComponentTemplateContentV1ToV3(
  content: TemplateContentV1,
  envelopeAssets: readonly TemplateEnvelopeAsset[] = [],
): TemplateV3Upgrade {
  const upgraded = upgradeTemplateContentV1ToV3(content);
  const result = { ...upgraded, content: { ...upgraded.content, assets: normalizeTemplateEnvelopeAssets(envelopeAssets) } };
  const validation = validateTemplateContentV3(result.content);
  return validation.valid ? result : {
    ...result,
    diagnostics: [...result.diagnostics, ...validation.diagnostics.map(item => ({ ...item, code: `upgrade_${item.code}` }))],
  };
}

export function normalizeTemplateEnvelopeAssets(assets: readonly TemplateEnvelopeAsset[]): TemplateAssetV2[] {
  return assets.map(asset => ({
    assetId: asset.assetId, fileName: asset.fileName, mediaType: asset.mediaType,
    sha256: asset.sha256, sizeBytes: asset.sizeBytes,
  })).sort((left, right) => left.assetId.localeCompare(right.assetId));
}

export function reconcileTemplateEnvelopeAssets(
  content: ComponentTemplateContent,
  envelopeAssets: readonly TemplateEnvelopeAsset[],
): TemplateAssetReconciliation {
  const assets = normalizeTemplateEnvelopeAssets(envelopeAssets);
  if (content.schemaVersion === 1) return { assets, diagnostics: [] };
  const contentAssets = normalizeTemplateEnvelopeAssets(content.assets);
  const matches = contentAssets.length === assets.length && contentAssets.every((asset, index) =>
    asset.assetId === assets[index]!.assetId && asset.fileName === assets[index]!.fileName &&
    asset.mediaType === assets[index]!.mediaType && asset.sha256 === assets[index]!.sha256 &&
    asset.sizeBytes === assets[index]!.sizeBytes);
  return matches ? { assets, diagnostics: [] } : { assets, diagnostics: [{
    code: "asset_envelope_mismatch", path: "$.assets",
    message: `Метаданные assets внутри шаблона v${content.schemaVersion} не совпадают с assets версии шаблона.`,
  }] };
}
