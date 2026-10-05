import type { HarnessCutList, HarnessCutListItem } from "../harness-cut-list-api";
import { calculateWireCutLength, type HarnessDesignDocument } from "./model";
import { coveringMeasuredLength } from "./physical-coverings";

/** Same physical blanks as the server cut list, projected from the current editor revision. */
export function buildLiveCutList(document: HarnessDesignDocument, projectId: string, harnessId: string, quantity: number): HarnessCutList {
  const members = new Set(document.cables.flatMap(cable => cable.memberWireIds));
  const blanks = [...document.wires.filter(wire => !members.has(wire.id)), ...document.cables];
  const items: HarnessCutListItem[] = blanks.map(blank => {
    const cut = calculateWireCutLength(blank);
    const warnings: HarnessCutListItem["warnings"] = [
      ...(!blank.materialBinding ? ["material-missing" as const] : []),
      ...(cut.cutLengthMm === null ? ["length-missing" as const] : []),
    ];
    return {
      wireId: blank.id, circuit: "circuit" in blank ? blank.circuit : "Кабель",
      sourceKind: "circuit" in blank ? "wire" : "cable",
      material: blank.materialBinding?.displayName ?? "not-pinned",
      materialSourceKey: blank.materialBinding?.sourceKey ?? null,
      materialDisplayName: blank.materialBinding?.displayName ?? null,
      sourceLengthMm: cut.sourceLengthMm, endCorrectionFromMm: cut.endCorrectionFromMm,
      endCorrectionToMm: cut.endCorrectionToMm, roundingStepMm: cut.cutRoundingStepMm,
      cutLengthMm: cut.cutLengthMm, pieces: quantity,
      totalMetres: cut.cutLengthMm === null ? null : Number(BigInt(Math.round(cut.cutLengthMm * 1000)) * BigInt(quantity)) / 1_000_000,
      status: cut.isComplete ? "ready" : "incomplete", warnings,
    };
  });
  for (const covering of document.physicalTopology?.coverings ?? []) {
    const length = coveringMeasuredLength(document, covering);
    items.push({ wireId: covering.id, sourceKind: "covering", circuit: covering.name,
      material: covering.material?.displayName ?? "not-pinned", materialSourceKey: covering.material?.sourceKey ?? null,
      materialDisplayName: covering.material?.displayName ?? null, sourceLengthMm: length,
      endCorrectionFromMm: 0, endCorrectionToMm: 0, roundingStepMm: .001, cutLengthMm: length, pieces: quantity,
      totalMetres: length === null ? null : Number(BigInt(Math.round(length * 1000)) * BigInt(quantity)) / 1_000_000,
      status: length === null ? "incomplete" : "ready", warnings: [...(!covering.material ? ["material-missing" as const] : []), ...(length === null ? ["length-missing" as const] : [])],
    });
  }
  return { projectId, harnessId, harnessQuantity: quantity, items,
    status: items.some(item => item.warnings.length > 0) ? "incomplete" : "ready", warning: "" };
}
