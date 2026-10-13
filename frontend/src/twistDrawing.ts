/*
 * Свитая группа из трёх проводов и больше на чертеже жгута. Свитая пара редактора (diffPairs) — только из двух проводов,
 * поэтому группа ложится надписью: на чертеже — под жгутом, на схеме Э4 — под таблицами разъёмов: «Свить вместе
 * 3 провода: XS1:1, XS1:2, XS1:3 · шаг 25 мм». Модуль грузится вместе с построением чертежа (лениво).
 */
import type { GeneratorDrawingPlan } from "./generatorToHarness";
import { applyEditorCommand } from "./harness/editor/commands";
import type { DrawingGraphic } from "./harness/editor/drawing-documents";
import { connectorContactPosition, connectorE4TableGeometry, type HarnessDesignDocument } from "./harness/editor/model";

const NOTE_GAP = 50, NOTE_STEP = 22, NOTE_SIZE = 14, NOTE_COLOR = "#253b4a";

const wiresWord = (n: number) => {
  const d = n % 10, dd = n % 100;
  return d >= 2 && d <= 4 && (dd < 12 || dd > 14) ? "провода" : "проводов";
};

/** Текст надписи группы: концы проводов на первом разъёме («XS1:1»), шаг свивки. */
export function twistNoteText(plan: Pick<GeneratorDrawingPlan, "connectors" | "wires" | "twistPitchMm">, keys: readonly string[]): string {
  const name = (key: string) => plan.connectors.find((c) => c.key === key)?.designation ?? key;
  const ends = keys.map((key) => plan.wires.find((wire) => wire.key === key)).filter((wire) => !!wire)
    .map((wire) => `${name(wire.from.connector)}:${wire.from.pin}`);
  return `Свить вместе ${keys.length} ${wiresWord(keys.length)}: ${ends.join(", ")} · шаг ${plan.twistPitchMm ? `${String(plan.twistPitchMm).replace(".", ",")} мм` : "по КД"}`;
}

/** Надписи свитых групп на чертеже и на Э4; без групп документ не меняется. */
export function withTwistNotes(document: HarnessDesignDocument, plan: GeneratorDrawingPlan): { document: HarnessDesignDocument; warnings: string[] } {
  const groups = plan.twistGroups ?? [];
  if (!groups.length || !document.connectors.length) return { document, warnings: [] };
  const drawingYs = document.connectors.flatMap((connector) => connector.contacts.map((contact) => connectorContactPosition(connector, contact.id, "drawing")?.y)
    .filter((y): y is number => typeof y === "number").concat(connector.positions.drawing.y));
  const drawingLeft = Math.min(...document.connectors.map((connector) => connector.positions.drawing.x));
  const drawingTop = Math.max(...drawingYs) + NOTE_GAP;
  const e4Top = Math.max(...document.connectors.map((connector) => connector.positions.e4.y + connectorE4TableGeometry(connector).height)) + NOTE_GAP;
  const graphics: DrawingGraphic[] = groups.flatMap((keys, index) => {
    const text = twistNoteText(plan, keys);
    const common = { kind: "text" as const, text, fontSize: NOTE_SIZE, color: NOTE_COLOR };
    return [
      { id: `gen-tw${index + 1}`, view: "drawing" as const, points: [{ x: Math.round(drawingLeft), y: Math.round(drawingTop + index * NOTE_STEP) }], ...common },
      { id: `gen-tw${index + 1}-e4`, view: "e4" as const, points: [{ x: 0, y: Math.round(e4Top + index * NOTE_STEP) }], ...common },
    ];
  });
  const documents = document.drawingDocuments ?? { tables: [], leaders: [], bomOrder: [] };
  const next = applyEditorCommand(document, { type: "set-drawing-documents", documents: { ...documents, graphics: [...(documents.graphics ?? []), ...graphics] } });
  const sizes = groups.map((keys) => keys.length).join(", ");
  return { document: next, warnings: [`Свитые группы (проводов: ${sizes}) показаны надписью — свитая пара в редакторе только из двух проводов.`] };
}
