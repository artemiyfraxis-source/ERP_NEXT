import { useEffect, useRef } from "react";
import type { EditorSceneObject } from "./editor-types";
import { wireBlankEnds, wireBlankEndLabels, type WireBlankEnd } from "../WireBlankCatalog";

export function freeWireIdsForEnd(wires: readonly EditorSceneObject[], end: "from" | "to"): string[] {
  return wires.filter(wire => wire.metadata?.[end === "from" ? "freeFrom" : "freeTo"] === "true").map(wire => wire.id);
}

export function FreeWireEndsPanel({ objects, selectedIds, disabled, onStyle, onStyles, focusTarget }: {
  readonly objects: readonly EditorSceneObject[]; readonly selectedIds: readonly string[]; readonly disabled: boolean;
  readonly onStyle: (wireId: string, end: "from" | "to", style: WireBlankEnd) => void;
  readonly onStyles: (wireIds: readonly string[], end: "from" | "to", style: WireBlankEnd) => void;
  readonly onEndpoint: (wireId: string, end: "from" | "to", position: { x: number; y: number }) => void;
  readonly onBulkX?: (wireIds: readonly string[], end: "from" | "to", x: number) => void;
  readonly focusTarget?: { readonly wireId: string; readonly end: "from" | "to"; readonly requestId: number } | null;
}) {
  const sectionRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!focusTarget) return;
    const control = sectionRef.current?.querySelector<HTMLSelectElement>(`[data-free-wire-id="${CSS.escape(focusTarget.wireId)}"][data-free-wire-end="${focusTarget.end}"]`);
    control?.focus();
  }, [focusTarget]);
  const wires = objects.filter(item => item.kind === "wire" && selectedIds.includes(item.id) && (item.metadata?.freeFrom === "true" || item.metadata?.freeTo === "true"));
  if (!wires.length) return null;
  const style = (wire: EditorSceneObject, end: "from" | "to") => wire.drawingEndStyles?.[end] ?? "cut";
  const endLabel = (end: "from" | "to") => end === "from" ? "Начало" : "Конец";
  const availableEnds = (["from", "to"] as const).filter(end => freeWireIdsForEnd(wires, end).length > 0);
  return <section ref={sectionRef} className="he-free-wire-ends" aria-label="Оконцовки свободных проводов">
    <header className="he-free-wire-heading"><h3>Оконцовки свободных проводов</h3><span>{wires.length}</span></header>
    {wires.map(wire => <div className="he-free-wire-row" key={wire.id}><strong>{wire.label || wire.id}</strong>{(["from", "to"] as const).filter(end => wire.metadata?.[end === "from" ? "freeFrom" : "freeTo"] === "true").map(end => {
      return <label className="he-free-wire-end" key={end}><span className="he-free-wire-end-label">{endLabel(end)}</span>
        <select data-free-wire-id={wire.id} data-free-wire-end={end} aria-label={`${wire.label || wire.id}: режим ${end}`} value={style(wire, end)} disabled={disabled} onChange={event => onStyle(wire.id, end, event.target.value as WireBlankEnd)}>{wireBlankEnds.map(item => <option key={item} value={item}>{wireBlankEndLabels[item]}</option>)}</select>
      </label>;
    })}</div>)}
    {wires.length > 1 && <div className="he-free-wire-bulk"><strong>Для всех выбранных концов</strong>{availableEnds.map(end => { const eligibleWireIds = freeWireIdsForEnd(wires, end); return <label className="he-free-wire-bulk-row" key={end}><span>{endLabel(end)}</span><select aria-label={`Общий режим ${end}`} defaultValue="" disabled={disabled} onChange={event => { if (event.target.value) onStyles(eligibleWireIds, end, event.target.value as WireBlankEnd); event.currentTarget.value = ""; }}><option value="">Выбрать режим…</option>{wireBlankEnds.map(item => <option key={item} value={item}>{wireBlankEndLabels[item]}</option>)}</select></label>; })}</div>}
  </section>;
}
