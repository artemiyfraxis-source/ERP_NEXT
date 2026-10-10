// «Таблица соединений» чертежа: строки строит сервер (один провод — одна строка), таблица — общая `DataTable`.
// В правке марка, сечение, маркировка и длина меняются прямо в строке — командами редактора над текущим чертежом.
import { useEffect, useState, type CSSProperties } from "react";
import { DataTable, type DataColumn } from "../../DataTable";
import type { HarnessConnectionRow } from "../../api";
import { AnchoredPopover } from "../AnchoredPopover";
import type { EditorCommand } from "../editor/commands";
import { connectionTableColumnLabels, useConnectionTableSettings, type ConnectionTableColumnId, type ConnectionTableSettings } from "../editor/connection-table-settings";
import { pipeMeasuredWireLength } from "../editor/drawing-dimensions";
import type { HarnessDesignDocument } from "../editor/model";
import { builtInWireColors, resolveWireColorHex } from "../editor/wire-reference-catalog";
import { builtInWireOptions, filterWireOptions, normalizeWireChoice, normalizeWireSectionChoice, type WireDatabaseOption } from "../editor/wire-database";
import { useHarnessDocument } from "./documents-source";

type Wire = HarnessDesignDocument["wires"][number];

export function compactFieldStyle(value: string, minimum = 7, maximum = 28): CSSProperties {
  const length = Array.from(value.trim()).length + 2;
  return { width: `${Math.max(minimum, Math.min(maximum, length))}ch` };
}

function endContacts(document: HarnessDesignDocument, wire: Wire) {
  return [wire.from, wire.to].flatMap(end => {
    if (!("connectorId" in end)) return [];
    const connector = document.connectors.find(item => item.id === end.connectorId);
    return connector?.contacts.filter(contact => contact.id === end.contactId) ?? [];
  });
}

/** Марка и сечение провода в текущем чертеже — то, что правят пикеры строки (до ответа сервера). */
function wireMark(document: HarnessDesignDocument, wire: Wire): string {
  return endContacts(document, wire).find(contact => contact.wire.trim())?.wire.trim() ?? wire.materialBinding?.sourceKey ?? "";
}

function wireSection(document: HarnessDesignDocument, wire: Wire): string {
  return endContacts(document, wire).find(contact => contact.wireSection?.trim())?.wireSection?.trim() ?? "";
}

function ConnectionWirePicker({ document, wire, options, onCommand, onSearch }: {
  document: HarnessDesignDocument; wire: Wire; options: readonly WireDatabaseOption[];
  onCommand: (command: EditorCommand) => boolean; onSearch?: (query: string) => void;
}) {
  const mark = wireMark(document, wire);
  const [query, setQuery] = useState(mark);
  const [searchQuery, setSearchQuery] = useState("");
  const [open, setOpen] = useState(false);
  useEffect(() => setQuery(mark), [mark]);
  const marks = [...new Map(filterWireOptions(options, searchQuery).map(option => [option.mark, option])).values()];
  const apply = (option: WireDatabaseOption) => {
    for (const end of [wire.from, wire.to]) {
      if (!("connectorId" in end)) continue;
      const connector = document.connectors.find(item => item.id === end.connectorId);
      if (!connector?.contacts.some(contact => contact.id === end.contactId)) continue;
      onCommand({ type: "update-contact", connectorId: end.connectorId, contactId: end.contactId,
        wire: option.mark, wireSection: "", wireDiameterMm: null, materialBinding: null,
        ...(option.color ? { color: option.color } : {}) });
    }
    setQuery(option.mark);
    setOpen(false);
  };
  return <div className="he-connection-wire-picker" onClick={event => event.stopPropagation()}>
    <input aria-label={`Марка провода ${wire.id}`} value={query} placeholder={mark || "Выбрать марку"} autoComplete="off"
      style={compactFieldStyle(query || mark, 10, 18)}
      onChange={event => { setQuery(event.currentTarget.value); setSearchQuery(event.currentTarget.value); setOpen(true); onSearch?.(event.currentTarget.value); }}
      onFocus={() => { setQuery(mark); setSearchQuery(""); setOpen(true); onSearch?.(""); }} />
    {open && <AnchoredPopover className="e4cce-wire-suggestions he-connection-wire-suggestions" role="listbox" label={`Марки проводов ${wire.id}: ${marks.length} вариантов, список прокручивается`} open onClose={() => setOpen(false)}>
      <small className="he-connection-suggestion-count">{marks.length} {marks.length === 1 ? "марка" : "марок"} · прокрутите список</small>
      {marks.map(option => <button type="button" key={option.mark} onPointerDown={event => event.preventDefault()} onClick={() => apply(option)} title={option.detail}>{option.mark}</button>)}
      {marks.length === 0 && <small role="status">Совпадений нет</small>}
    </AnchoredPopover>}
  </div>;
}

function ConnectionSectionPicker({ document, wire, options, onCommand }: {
  document: HarnessDesignDocument; wire: Wire; options: readonly WireDatabaseOption[]; onCommand: (command: EditorCommand) => boolean;
}) {
  const mark = wireMark(document, wire);
  const current = wireSection(document, wire);
  const possible = options.filter(option => option.mark.trim().toLocaleLowerCase("ru-RU") === mark.trim().toLocaleLowerCase("ru-RU"));
  const sections = [...new Set([current, ...possible.map(option => option.section)].filter(Boolean))];
  return <select aria-label={`Сечение провода ${wire.id}`} value={current} disabled={!mark} style={compactFieldStyle(current, 7, 18)}
    title={mark ? "Сечения выбранной марки" : "Сначала выберите марку"} onClick={event => event.stopPropagation()}
    onChange={event => {
      const section = event.currentTarget.value;
      const option = possible.find(item => item.section === section);
      for (const end of [wire.from, wire.to]) {
        if (!end.connectorId || !end.contactId) continue;
        onCommand({ type: "update-contact", connectorId: end.connectorId, contactId: end.contactId,
          wire: mark, wireSection: section, wireDiameterMm: option?.diameterMm ?? null,
          materialBinding: option?.materialBinding ?? null, ...(option?.color ? { color: option.color } : {}) });
      }
    }}><option value="">—</option>{sections.map(section => <option key={section} value={section}>{section}</option>)}</select>;
}

/** Цвет из схемы, которого нет ни в палитре, ни у этой марки и сечения в справочнике проводов ERP, помечается. */
export function colorFound(row: HarnessConnectionRow, options: readonly WireDatabaseOption[]): boolean {
  if (row.color === "—") return true;
  const primaryLabel = row.color.split(" / ")[0]!.trim();
  const primaryHex = row.color_hex.toLocaleUpperCase();
  return builtInWireColors.some(reference => reference.hex.toLocaleUpperCase() === primaryHex ||
    normalizeWireChoice(reference.name) === normalizeWireChoice(primaryLabel)) || options.some(option => {
    if (normalizeWireChoice(option.mark) !== normalizeWireChoice(row.mark) ||
        normalizeWireSectionChoice(option.section) !== normalizeWireSectionChoice(row.section) || !option.color) return false;
    const optionHex = resolveWireColorHex(option.color, builtInWireColors, "").toLocaleUpperCase();
    return optionHex === primaryHex || normalizeWireChoice(option.color) === normalizeWireChoice(primaryLabel);
  });
}

function ColorCell({ row, found }: { row: HarnessConnectionRow; found: boolean }) {
  const hex = row.color_hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16));
  const ink = (r! * .299 + g! * .587 + b! * .114) > 160 ? "#163548" : "#fff";
  return <span className="he-connection-color" style={{ backgroundColor: row.color_hex, color: ink }}>{found ? row.color : `${row.color} (в базе не найден)`}</span>;
}

export function ConnectionsTable({ document, quantity, readOnly, selectedIds, onCommand, wireOptions = builtInWireOptions, wireDatabaseOptions, onWireSearch, settings }: {
  document: HarnessDesignDocument; quantity: number; readOnly: boolean; selectedIds: readonly string[];
  onCommand: (command: EditorCommand) => boolean; wireOptions?: readonly WireDatabaseOption[];
  wireDatabaseOptions?: readonly WireDatabaseOption[]; onWireSearch?: (query: string) => void; settings?: ConnectionTableSettings;
}) {
  const { data, error } = useHarnessDocument("connections", document, quantity);
  const live = useConnectionTableSettings();
  const [onlyRelated, setOnlyRelated] = useState(false);
  const colorOptions = wireDatabaseOptions ?? wireOptions;
  const wireOf = (row: HarnessConnectionRow) => readOnly ? undefined : document.wires.find(wire => wire.id === row.wire_id);
  const lengthLocked = (wire: Wire) => pipeMeasuredWireLength(document, wire.id).managed || !!document.drawingDocuments?.dimensions?.some(d => d.wireId === wire.id);
  const cell: Record<ConnectionTableColumnId, Omit<DataColumn<HarnessConnectionRow>, "key" | "title">> = {
    index: { value: row => row.index },
    mark: { value: row => row.mark, render: row => { const wire = wireOf(row); return wire ? <ConnectionWirePicker document={document} wire={wire} options={wireOptions} onCommand={onCommand} onSearch={onWireSearch} /> : row.mark; } },
    section: { value: row => row.section, render: row => { const wire = wireOf(row); return wire ? <ConnectionSectionPicker document={document} wire={wire} options={wireOptions} onCommand={onCommand} /> : row.section; } },
    marking: { value: row => row.marking, render: row => { const wire = wireOf(row); return wire
      ? <input style={compactFieldStyle(wire.circuit, 9, 24)} aria-label={`Маркировка ${wire.id}`} value={wire.circuit} onChange={e => onCommand({ type: "update-wire", wireId: wire.id, circuit: e.target.value })} />
      : row.marking; } },
    from: { value: row => row.from },
    color: { value: row => row.color, render: row => <ColorCell row={row} found={colorFound(row, colorOptions)} /> },
    to: { value: row => row.to },
    length: { numeric: true, value: row => row.length_mm, render: row => { const wire = wireOf(row); return wire && !lengthLocked(wire)
      ? <input style={compactFieldStyle(wire.lengthMm === null ? "" : String(wire.lengthMm), 8, 12)} aria-label={`Длина ${wire.id}`} type="number" min="0" value={wire.lengthMm ?? ""}
        onChange={e => onCommand({ type: "update-wire", wireId: wire.id, lengthMm: e.target.value === "" ? null : Number(e.target.value) })} />
      : <span title={wire ? "Длина по размерам на чертеже; добавки — в поправках провода" : undefined}>{row.length_mm === null ? "—" : row.length_mm.toLocaleString("ru-RU")}</span>; } },
    route: { value: row => row.route },
  };
  const columns: DataColumn<HarnessConnectionRow>[] = (settings ?? live).columns.filter(column => column.visible)
    .map(column => ({ key: column.id, title: connectionTableColumnLabels[column.id], ...cell[column.id] }));
  const rows = (data?.rows ?? []).filter(row => !onlyRelated || selectedIds.includes(row.wire_id));
  return <div className="he-document-content">
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!data && !error ? <div className="empty">Загрузка…</div>
      : <DataTable label="Таблица соединений" rows={rows} columns={columns} rowKey={row => row.wire_id} emptyText="В чертеже нет проводов."
        toolbar={<label className="check-inline"><input type="checkbox" checked={onlyRelated} onChange={e => setOnlyRelated(e.target.checked)} />Только связанные</label>} />}
  </div>;
}
