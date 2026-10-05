import type { HarnessDesignDocument } from "./model";

/** Volume lighting is a drawing presentation setting. A single physical pipe
 * is a valid target too; grouping or a four-wire threshold is not required. */
export function volumeShadingEligible(document: HarnessDesignDocument): boolean {
  return (document.physicalTopology?.segments.length ?? 0) > 0;
}
