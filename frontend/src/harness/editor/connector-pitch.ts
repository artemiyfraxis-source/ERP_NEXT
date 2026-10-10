import type { ReferenceCatalogRecord } from "../reference-catalog";
import { materializePlacementRows } from "./component-template-placement";
import type { ComponentTemplateViewInstance } from "./component-template-view-renderer";
import { selectMaterializedContactRepresentation } from "./materialized-contact-representation";
import type { ConnectorInstance, HarnessDesignDocument, Point } from "./model";

/** Contact centres of a library connector on its Drawing view, in template units (as the field places them). */
export function drawingContactCentres(connector: ConnectorInstance, instance: Pick<ComponentTemplateViewInstance, "content" | "articleVariantId">): Point[] {
  const binding = connector.libraryBinding;
  if (binding?.mode !== "template") return [];
  let rows: ReturnType<typeof materializePlacementRows>;
  try { rows = materializePlacementRows(instance.content, instance.articleVariantId); } catch { return []; }
  const materialized: ConnectorInstance = { ...connector, libraryBinding: { ...binding, snapshot: { ...binding.snapshot,
    contacts: binding.snapshot.contacts.map(contact => ({ ...contact, representations: rows.find(row => row.key === contact.logicalContactId)?.representations ?? [] })) } } };
  return connector.contacts.flatMap(contact => {
    const representation = selectMaterializedContactRepresentation(materialized, contact.id, "drawing");
    return representation ? [{ x: representation.x, y: representation.y }] : [];
  });
}

/** Template units per mm of every library connector whose drawing can be measured: its contact centres against the
 * pitch of its ERP family. A connector left out is drawn at 1 unit = 1 mm and counted as «без размеров». */
export function templateUnitsPerMmByConnector(document: HarnessDesignDocument, instances: readonly ComponentTemplateViewInstance[],
  records: readonly ReferenceCatalogRecord[]): Map<string, number> {
  const units = new Map<string, number>();
  for (const instance of instances) {
    const connector = document.connectors.find(item => item.id === instance.objectId);
    if (connector?.libraryBinding?.mode !== "template") continue;
    const pitch = connectorPitchMm(connector.libraryBinding.article.articleKey, records) ?? connectorPitchMm(connector.partNumber, records);
    const factor = templateUnitsPerMm(drawingContactCentres(connector, instance), pitch);
    if (factor !== null) units.set(connector.id, factor);
  }
  return units;
}

/** Same normalisation as the ERP base of connectors (`connector_housings.normalize_article`). */
export function normalizeConnectorArticle(value: string): string {
  return value.trim().toUpperCase()
    .replace(/^(MOLEX|JST|HIROSE|JAE|TE(?:\s+CONNECTIVITY)?|CONNFLY|AMASS|AMP)\s+/, "")
    .replace(/\s+/g, "");
}

function regex(pattern: unknown): RegExp | null {
  if (typeof pattern !== "string" || !pattern) return null;
  try {
    return new RegExp(pattern, "i");
  } catch {
    return null;
  }
}

/** The ERP connector family («connector-family» of the harness catalog) whose pattern takes this part number, after
 * the analogues («connector-alias»), as `connector_housings.housing_of` resolves it. */
export function matchConnectorFamily(article: string, records: readonly ReferenceCatalogRecord[]): ReferenceCatalogRecord | null {
  let normalized = normalizeConnectorArticle(article);
  if (!normalized) return null;
  for (const record of records) {
    if (record.entityType !== "connector-alias") continue;
    const pattern = regex(record.payload.pattern);
    const replacement = record.payload.replacement;
    if (pattern && typeof replacement === "string" && pattern.test(normalized)) {
      normalized = normalizeConnectorArticle(normalized.replace(pattern, replacement));
    }
  }
  return records.find(record => record.entityType === "connector-family" && regex(record.payload.pattern)?.test(normalized)) ?? null;
}

/** Contact pitch in mm from the ERP base of connectors; null when the family is unknown or has no single pitch. */
export function connectorPitchMm(article: string, records: readonly ReferenceCatalogRecord[]): number | null {
  const pitch = matchConnectorFamily(article, records)?.payload.pitchMm;
  return typeof pitch === "number" && Number.isFinite(pitch) && pitch > 0 ? pitch : null;
}

/** Template units per millimetre of a library drawing: the median distance from each contact centre to its nearest
 * neighbour, in template units, over the connector's real pitch. Null with fewer than two distinct contacts or no
 * pitch — the caller then falls back to 1 unit = 1 mm. */
export function templateUnitsPerMm(contactCentres: readonly Point[], pitchMm: number | null): number | null {
  if (pitchMm === null || !(pitchMm > 0) || contactCentres.length < 2) return null;
  const nearest = contactCentres.map((point, index) => Math.min(...contactCentres
    .filter((_, other) => other !== index)
    .map(other => Math.hypot(other.x - point.x, other.y - point.y))
    .filter(distance => distance > 1e-6)))
    .filter(Number.isFinite)
    .sort((a, b) => a - b);
  if (!nearest.length) return null;
  const middle = nearest.length >> 1;
  const median = nearest.length % 2 ? nearest[middle]! : (nearest[middle - 1]! + nearest[middle]!) / 2;
  return median / pitchMm;
}
