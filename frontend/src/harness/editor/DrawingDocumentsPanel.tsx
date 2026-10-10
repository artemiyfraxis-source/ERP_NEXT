import type { DrawingPerimeters } from "./drawing-object-perimeter";
import { InfoHint } from "../InfoHint";
import type { HarnessDesignDocument } from "./model";
import { buildDrawingBom, drawingObjectOrigin, emptyDrawingDocuments, moveDrawingAnnotation, reconcileDrawingDocuments, type DrawingDocuments } from "./drawing-documents";

/** The drawing's tables and position leaders as settings of the drawing: put a table on the field, take it off, edit the
 *  selected leader, drop leaders whose object or row is gone. The tables' rows themselves are the server's
 *  (`panels/`, shown in the table windows). */
export function DrawingDocumentsPanel({ document, selectedId, onChange, perimeters, availableKinds = ["bom", "connections", "cut"] }: {
  document: HarnessDesignDocument; selectedId: string | null; onChange: (d: DrawingDocuments) => boolean;
  perimeters?: DrawingPerimeters; availableKinds?: readonly ("bom" | "connections" | "cut")[];
}) {
  const d = document.drawingDocuments ?? emptyDrawingDocuments();
  // The leaders on the field stand on the drawing's own BOM row keys — the same keys the server's rows carry.
  const rows = d.leaders.length ? buildDrawingBom(document) : [];
  const orphanLeaders = d.leaders.filter(l => !drawingObjectOrigin(document, l.objectId) || !rows.some(r => r.key === l.rowKey && r.objectIds.includes(l.objectId)));
  const leader = d.leaders.find(l => l.id === selectedId || `${l.id}:anchor` === selectedId);
  const addTable = (kind: "bom" | "connections" | "cut") => onChange({ ...d, tables: [...d.tables, { id: crypto.randomUUID(), kind, position: { x: 40 + d.tables.length * 30, y: 40 + d.tables.length * 30 } }] });
  const updateLeader = (key: "anchorOffset" | "circle", axis: "x" | "y", value: number) => {
    if (!leader) return;
    const point = { ...leader[key], [axis]: value };
    const origin = drawingObjectOrigin(document, leader.objectId); if (!origin) return;
    const docs = moveDrawingAnnotation(document, key === "circle" ? leader.id : `${leader.id}:anchor`, key === "circle" ? { x: point.x - 12, y: point.y - 12 } : { x: origin.x + point.x - 4, y: origin.y + point.y - 4 }, perimeters);
    if (docs) onChange(docs);
  };
  const label = (kind: "bom" | "connections" | "cut") => kind === "bom" ? "Спецификация" : kind === "cut" ? "Карта резки" : "Соединения";
  return <section className="he-relations" aria-label="Документы схемы"><details open={!!leader}>
    <summary>{availableKinds.length === 1 ? "Таблица соединений" : "Таблицы и выноски"}</summary>
    <header className="ui-section-heading"><InfoHint>{availableKinds.length === 1 ? "Таблицу соединений можно открыть на поле, передвинуть и закрепить у края." : "Таблицы на поле перетаскиваются за заголовок. Кружок выноски перемещается свободно, якорь — по периметру своего объекта; номер следует позиции спецификации."}</InfoHint></header>
    <div className="he-relations-actions">{availableKinds.map(kind => <button key={kind} className="ui-control" type="button" onClick={() => addTable(kind)}>{label(kind)} +</button>)}</div>
    {d.tables.filter(t => availableKinds.includes(t.kind)).map(t => <div className="he-relations-actions" key={t.id}><span>{label(t.kind)}</span><button className="ui-control" type="button" onClick={() => onChange({ ...d, tables: d.tables.filter(i => i.id !== t.id) })}>Убрать с поля</button></div>)}
    {leader && <div className="he-physical-fields"><strong>Выноска</strong>{(["anchorOffset", "circle"] as const).flatMap(key => (["x", "y"] as const).map(axis => <label key={`${key}${axis}`}>{key === "circle" ? "Круг" : "Якорь"} {axis}<input aria-label={`${key} ${axis}`} type="number" value={leader[key][axis]} onChange={e => updateLeader(key, axis, Number(e.target.value))} /></label>))}<button className="ui-control" type="button" onClick={() => onChange({ ...d, leaders: d.leaders.filter(l => l.id !== leader.id) })}>Удалить выноску</button></div>}
    {orphanLeaders.length > 0 && <div role="alert">Устаревшие выноски: {orphanLeaders.length}. <button type="button" className="ui-control" onClick={() => onChange(reconcileDrawingDocuments(document, document).drawingDocuments!)}>Удалить</button></div>}
  </details></section>;
}
