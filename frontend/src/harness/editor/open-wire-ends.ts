// ERP: open wire ends («хвост» of a splice) and splice ends on the canvas — both views. The ends are model data
// (`freeEnd` / `junctionId` endpoints, model.ts); the scene carries them as wire metadata and this draws the marks.
import type { EditorSceneObject } from "./editor-types";
import { isFreeEndpoint, isJunctionEndpoint, type WireInstance } from "./model";

const lengthText = (mm: number | null) => (mm === null ? "хвост" : `${String(mm).replace(".", ",")} мм`);

/** Scene metadata of a wire: `openFrom`/`openTo` — the open end's label (its length), `spliceFrom`/`spliceTo` — the end
 *  sits in a splice. */
export function openWireEndMetadata(wire: Pick<WireInstance, "from" | "to" | "lengthMm">): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [end, key] of [[wire.from, "From"], [wire.to, "To"]] as const) {
    if (isFreeEndpoint(end)) result[`open${key}`] = lengthText(wire.lengthMm);
    else if (isJunctionEndpoint(end)) result[`splice${key}`] = "true";
  }
  return result;
}

/** Open end: a hollow ring at the wire's end and its length beyond it; splice end on the drawing: the sleeve dot. */
export function drawOpenWireEnds(context: CanvasRenderingContext2D, object: EditorSceneObject, view: "e4" | "drawing", selected: boolean): void {
  const points = object.points ?? [];
  if (object.kind !== "wire" || points.length < 2) return;
  const meta = object.metadata ?? {};
  context.save();
  for (const end of ["From", "To"] as const) {
    const tip = end === "From" ? points[0]! : points.at(-1)!;
    const label = meta[`open${end}`];
    if (meta[`splice${end}`] === "true" && view === "drawing") {
      context.beginPath(); context.arc(tip.x, tip.y, 4.5, 0, Math.PI * 2);
      context.fillStyle = "#183b4d"; context.fill();
    }
    if (label === undefined) continue;
    const before = (end === "From" ? points.slice(1) : points.slice(0, -1).reverse()).find((p) => Math.hypot(p.x - tip.x, p.y - tip.y) > 1e-6);
    const length = before ? Math.hypot(tip.x - before.x, tip.y - before.y) : 1;
    const dir = before ? { x: (tip.x - before.x) / length, y: (tip.y - before.y) / length } : { x: 1, y: 0 };
    context.beginPath(); context.arc(tip.x, tip.y, 3.5, 0, Math.PI * 2);
    context.fillStyle = "#ffffff"; context.fill();
    context.lineWidth = 1.5; context.strokeStyle = selected ? "#1179ac" : "#344b59"; context.stroke();
    context.font = "600 11px Inter, Arial, sans-serif";
    context.fillStyle = selected ? "#1179ac" : "#253b4a";
    const horizontal = Math.abs(dir.x) >= Math.abs(dir.y);
    context.textAlign = horizontal ? (dir.x >= 0 ? "left" : "right") : "center";
    context.textBaseline = horizontal ? "middle" : dir.y > 0 ? "top" : "bottom";
    context.fillText(label, tip.x + dir.x * 8, tip.y + dir.y * 8);
  }
  context.restore();
}
