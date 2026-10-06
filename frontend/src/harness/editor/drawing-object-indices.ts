export interface DrawingIndexedObject {
  readonly id: string;
  readonly kind?: string;
  readonly label?: string;
  readonly metadata?: Readonly<Record<string, string>>;
}

const wireIndex = /^(?:W\d+|П\d+)$/i;

const coveringPrefix = (kind: string | undefined): string => ({
  "heat-shrink": "ТУ",
  nylon: "Н",
  braid: "ОПЛ",
  "metal-braid": "МО",
  tape: "Л",
  band: "Б",
} as Record<string, string>)[kind ?? ""] ?? "ОБ";

/** Resolves an object's explicit, operator-readable designator when one exists. */
export function getDrawingObjectIndex(object: DrawingIndexedObject): string | undefined {
  const explicit = object.metadata?.index?.trim();
  if (explicit) return explicit;
  if (object.kind === "wire") {
    const sequence = object.metadata?.index?.trim();
    if (sequence && wireIndex.test(sequence)) return sequence;
  }
  return undefined;
}

/** Assigns deterministic readable indices to wires and protective coverings. */
export function buildDrawingObjectIndices(objects: readonly DrawingIndexedObject[]): Map<string, string> {
  const result = new Map<string, string>();
  const wireSequence = { value: 0 };
  const cableSequence = { value: 0 };
  const coveringSequences = new Map<string, number>();
  for (const object of objects) {
    const explicit = getDrawingObjectIndex(object);
    if (explicit) {
      result.set(object.id, explicit);
      if (object.kind === "wire") wireSequence.value = Math.max(wireSequence.value, Number(explicit.match(/\d+$/)?.[0] ?? 0));
      continue;
    }
    if (object.kind === "wire") {
      result.set(object.id, `W${++wireSequence.value}`);
    } else if (object.kind === "cable") {
      result.set(object.id, `K${++cableSequence.value}`);
    } else if (object.kind === "physical-covering") {
      const prefix = coveringPrefix(object.metadata?.coveringKind);
      const sequence = (coveringSequences.get(prefix) ?? 0) + 1;
      coveringSequences.set(prefix, sequence);
      result.set(object.id, `${prefix}${sequence}`);
    }
  }
  return result;
}
