// «Спецификация» чертежа: строки и расход на заданное число жгутов строит сервер, таблица — общая `DataTable`.
// Строка знает свой ключ — тот же, по которому на чертеже стоят номера позиций, порядок строк и правленые тексты, —
// поэтому правка текста, порядок и показ номера остаются правкой чертежа (команды редактора).
import { useState } from "react";
import { AutoTextarea } from "../../AutoTextarea";
import { DataTable, type DataColumn } from "../../DataTable";
import type { HarnessBomRow } from "../../api";
import { addDrawingPositions, drawingObjectOrigin, emptyDrawingDocuments, setDrawingPositionVisibility, type DrawingDocuments } from "../editor/drawing-documents";
import type { DrawingPerimeters } from "../editor/drawing-object-perimeter";
import type { HarnessDesignDocument } from "../editor/model";
import { compactFieldStyle } from "./ConnectionsTable";
import { formatNumber, useHarnessDocument } from "./documents-source";

type TextField = "index" | "designation" | "name" | "note";

function EyeIcon({ shown }: { shown: boolean }) {
  return <svg width="16" height="16" viewBox="0 0 20 20" aria-hidden="true"><path d="M2 10s3-5 8-5 8 5 8 5-3 5-8 5-8-5-8-5Z" fill="none" stroke="currentColor" /><circle cx="10" cy="10" r="2.5" fill="none" stroke="currentColor" />{!shown && <path d="m3 3 14 14" stroke="currentColor" />}</svg>;
}

export function SpecificationTable({ document, quantity, readOnly, selectedIds, onChange, onReveal, perimeters }: {
  document: HarnessDesignDocument; quantity: number; readOnly: boolean; selectedIds: readonly string[];
  onChange: (documents: DrawingDocuments) => boolean; onReveal: (ids: readonly string[]) => void; perimeters?: DrawingPerimeters;
}) {
  const { data, error } = useHarnessDocument("bom", document, quantity);
  const [onlyRelated, setOnlyRelated] = useState(false);
  const d = document.drawingDocuments ?? emptyDrawingDocuments();
  const all = data?.rows ?? [];
  const shown = (row: HarnessBomRow) => d.leaders.some(leader => leader.rowKey === row.key && !leader.hidden);
  // Своя правка текста видна сразу, до ответа сервера.
  const text = (row: HarnessBomRow, field: TextField) => d.bomText?.[row.key]?.[field] ?? row[field];
  const edit = (row: HarnessBomRow, field: TextField, value: string) =>
    onChange({ ...d, bomText: { ...d.bomText, [row.key]: { ...d.bomText?.[row.key], [field]: value } } });
  const up = (row: HarnessBomRow) => {
    const keys = all.map(item => item.key), i = keys.indexOf(row.key);
    if (i > 0) { [keys[i - 1], keys[i]] = [keys[i]!, keys[i - 1]!]; onChange({ ...d, bomOrder: keys }); }
  };
  const textColumn = (field: TextField, title: string, min: number, max: number): DataColumn<HarnessBomRow> => ({
    key: field, title, value: row => text(row, field), wrap: readOnly,
    render: readOnly ? undefined : row => <input style={compactFieldStyle(text(row, field), min, max)} aria-label={`${title} позиции ${row.position}`}
      value={text(row, field)} onClick={e => e.stopPropagation()} onChange={e => edit(row, field, e.target.value)} />,
  });
  const columns: DataColumn<HarnessBomRow>[] = [
    { key: "position", title: "Поз.", numeric: true, value: row => row.position, render: row => <span className="he-bom-position">
      <button className="ui-control" type="button" title="Показать на чертеже" onClick={e => { e.stopPropagation(); onReveal(row.object_ids); }}>{row.position}</button>
      <button className="ui-control he-position-toggle" type="button" disabled={readOnly || !row.object_ids.some(id => drawingObjectOrigin(document, id))}
        aria-label={`${shown(row) ? "Скрыть" : "Показать"} позицию ${row.position} у всех обозначений`} title="Показать / скрыть номер у всех обозначений" aria-pressed={shown(row)}
        onClick={e => { e.stopPropagation(); onChange(setDrawingPositionVisibility(document, row.key, !shown(row), perimeters)); }}><EyeIcon shown={shown(row)} /></button>
    </span> },
    textColumn("index", "Индекс", 7, 28),
    textColumn("designation", "Обозначение", 7, 28),
    textColumn("name", "Наименование", 14, 44),
    { key: "amount", title: "Кол-во", numeric: true, value: row => row.amount, render: row => `${formatNumber(row.amount, 6)} ${row.unit}` },
    { ...textColumn("note", "Примечание", 10, 30), render: readOnly ? undefined : row => <span className="he-bom-note-cell">
      <AutoTextarea style={compactFieldStyle(text(row, "note"), 12, 40)} aria-label={`Примечание позиции ${row.position}`} value={text(row, "note")}
        onClick={e => e.stopPropagation()} onChange={value => edit(row, "note", value)} />
      <button className="ui-control" type="button" title="Поднять строку" aria-label={`Поднять позицию ${row.position}`} disabled={row.position === 1}
        onClick={e => { e.stopPropagation(); up(row); }}>↑</button>
    </span> },
  ];
  const rows = all.filter(row => !onlyRelated || row.object_ids.some(id => selectedIds.includes(id)));
  return <div className="he-document-content">
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!data && !error ? <div className="empty">Загрузка…</div>
      : <DataTable label="Спецификация" rows={rows} columns={columns} rowKey={row => row.key} emptyText="В чертеже нет материалов."
        toolbar={<>
          <label className="check-inline"><input type="checkbox" checked={onlyRelated} onChange={e => setOnlyRelated(e.target.checked)} />Только связанные</label>
          {!readOnly && <button className="secondary small" type="button" onClick={() => onChange(addDrawingPositions(document, perimeters))}>Добавить позиции</button>}
          <span className="muted">На {quantity} жгут(а)</span>
        </>} />}
  </div>;
}
