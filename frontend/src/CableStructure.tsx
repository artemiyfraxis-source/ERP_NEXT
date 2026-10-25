import { useMemo, useState, type ReactNode } from "react";
import { cellFailure, fieldCellEdit, ownCellEdit } from "./CustomColumn";
import { DataTable, type DataCellEdit, type DataColumn } from "./DataTable";
import {
  failureText,
  referencesApi,
  type CableKind,
  type CableRow,
  type CablesResult,
  type CoaxCard,
  type HookupPayload,
  type HookupWire,
  type TechOperationRecord,
  type WireGauge,
} from "./api";
import { canonicalColor, colorBackground, parseColors } from "./wireColors";
import { ColorPicker, MakersInput } from "./WireFieldWidgets";
import { MAKERS, STANDARD, makerSuggestions } from "./wireMakers";
import { arrangeCableColumns, knownCableColumn } from "./cableColumnOrder";

/* Structured cable reference (owner 10.10.2026: «наведи порядок в базах коаксиалов, монтажных проводов и сечений»):
   «Сечения AWG ↔ мм²» (one row per conductor size), «Монтажные провода» (series + conductor as a size row + insulation,
   outer Ø, voltage, temperature) and the coax card a tester reads at the VNA. Backend: references/cable_structure.py. */

export const CABLE_KIND_LABELS: Record<CableKind, string> = {
  coax: "Коаксиальные кабели", wire: "Монтажные провода", multi: "Многожильные кабели", lan: "ETHERNET",
};

/** The columns of one list (owner 20.10.2026: «у каждого своя структура»): the list's own БД.ПРОВ columns and the ones the
    owner added to it — never another list's. A server without the per-list sets (an old answer) gives the shared ones. */
export function kindColumns(data: CablesResult, kind: CableKind): { columns: string[]; own: string[]; removed: string[] } {
  const own = data.custom_columns_by_kind?.[kind] ?? (data.columns_by_kind ? [] : data.custom_columns ?? []);
  const columns = data.columns_by_kind?.[kind] ?? data.columns;
  return { columns: [...new Set([...columns, ...own])], own, removed: data.removed_columns_by_kind?.[kind] ?? [] };
}

/** The cells the card and editor of an EXISTING row show: the list's columns, then those removed from the list
    25.10.2026 (no table column; the row keeps the value and takes edits to it). A new row — the list's columns only. */
export const cardColumns = (columns: string[], removed: string[]): string[] => [...columns, ...removed.filter((name) => !columns.includes(name))];

/** What a move keeps, said in the confirmation of the move. */
export const moveText = (name: string, kind: CableKind) =>
  `Перенести «${name}» в «${CABLE_KIND_LABELS[kind]}»? Заполненные значения строки сохранятся, у списка будут свои столбцы.`;

/** A number as the shop reads it: comma decimal, no trailing zeros. */
export const num = (value: number | null | undefined): string =>
  value === null || value === undefined ? "" : String(value).replace(".", ",");
export const freq = (mhz: number | null): string =>
  mhz === null ? "" : mhz >= 1000 ? `${num(Math.round(mhz / 10) / 100)} ГГц` : `${num(mhz)} МГц`;
const signed = (value: number | null) => value === null ? "" : value > 0 ? `+${value}` : String(value).replace("-", "−");
export const range = (low: number | null, high: number | null) =>
  low === null && high === null ? "" : `${low === null ? "…" : signed(low)} … ${high === null ? "…" : signed(high)}`;
const TEMP_NUMBER = /^[+-]?\d+(?:[.,]\d+)?$/;
/** Temperature typed into one cell as the table shows it — «−60 … +200», also «-60...200», «от -60 до +200», «-60 – 200»,
    «… +80» (no lower bound) — into the pair the wire's window has; an empty text clears both. A text that is not a
    range is refused with the way to write it. */
export function parseTempRange(text: string): { min: string; max: string } {
  const plain = text.replace(/−/g, "-").replace(/°\s*[CС]?/gi, "").replace(/^\s*от\s*/i, "").trim();
  if (!plain) return { min: "", max: "" };
  const parts = plain.split(/\s*(?:…|\.{2,}|до|–|—|;|\/)\s*/i);
  let [low, high] = parts.length === 2 ? parts : ["", ""];
  if (parts.length !== 2) {
    const dashed = plain.match(/^([+-]?\d+(?:[.,]\d+)?)\s*-\s*([+-]?\d+(?:[.,]\d+)?)$/) ?? plain.match(/^(\S+)\s+(\S+)$/);
    if (!dashed) throw new Error("Температура — «от … до», например −60 … +200");
    [low, high] = [dashed[1], dashed[2]];
  }
  const bound = (value: string) => {
    const clean = value.trim();
    if (!clean || clean === "…") return "";
    if (!TEMP_NUMBER.test(clean)) throw new Error("Температура — «от … до», например −60 … +200");
    return clean.replace(/^\+/, "");
  };
  const min = bound(low);
  const max = bound(high);
  if (min && max && Number(min.replace(",", ".")) > Number(max.replace(",", "."))) throw new Error("Температура «от» больше «до»");
  return { min, max };
}

export const spanText = (card: CoaxCard): string =>
  card.span_to_ghz === null ? "" : `${freq(card.span_from_mhz)} – ${num(card.span_to_ghz)} ГГц`;

// БД.ПРОВ cells the structure stands for: edited here only through the structure, so both stay the same.
const MIRRORED = ["Сечение, мм2", "AWG", "Наружный диаметр, мм", "Изоляция", "Напряжение, В", "Темп. от, °C", "Темп. до, °C"];
const MARK = "Марка";
const TYPE = "Тип провода";

function gaugeOption(gauge: WireGauge): string {
  return [gauge.label, gauge.conductor_d_mm !== null ? `жила Ø ${num(gauge.conductor_d_mm)} мм` : ""].filter(Boolean).join(" · ");
}

// --- «Сечения AWG ↔ мм²» ---

// The stranding is a property of each wire (owner 12.10.2026), not of the size: the gauge form no longer shows it, and
// `strands` only rides along so a save does not wipe what the table still holds.
type GaugeDraft = { awg: string; section_mm2: string; conductor_d_mm: string; strands: string };
const gaugeDraft = (gauge: WireGauge | null): GaugeDraft => ({
  awg: gauge?.awg ?? "", section_mm2: num(gauge?.section_mm2), conductor_d_mm: num(gauge?.conductor_d_mm), strands: gauge?.strands ?? "",
});

export function GaugeTable({ gauges, canEdit, onSaved, onError }: {
  gauges: WireGauge[];
  canEdit: boolean;
  onSaved: (message: string) => Promise<void> | void;
  onError: (message: string) => void;
}) {
  const [adding, setAdding] = useState(false);
  const columns: DataColumn<WireGauge>[] = [
    { key: "awg", title: "AWG", value: (gauge) => gauge.awg, render: (gauge) => gauge.awg ? <strong>{gauge.awg}</strong> : <span className="muted">только ГОСТ</span> },
    { key: "section", title: "Сечение, мм²", numeric: true, value: (gauge) => gauge.section_mm2, render: (gauge) => num(gauge.section_mm2) },
    // Ø жилы правится прямо в ячейке тем же сохранением, что форма сечения; AWG и сечение — ключ строки, в форме.
    { key: "dia", title: "Ø жилы, мм", numeric: true, value: (gauge) => gauge.conductor_d_mm ?? "", render: (gauge) => num(gauge.conductor_d_mm) || <span className="muted">—</span>,
      edit: canEdit ? {
        value: (gauge) => num(gauge.conductor_d_mm),
        save: async (gauge, value) => {
          try { await referencesApi.saveGauge(gauge.id, { ...gaugeDraft(gauge), conductor_d_mm: value }); } catch (reason) { throw cellFailure(reason); }
        },
      } : undefined },
    { key: "wires", title: "Монтажных проводов", numeric: true, value: (gauge) => gauge.wires, render: (gauge) => gauge.wires || <span className="muted">—</span> },
  ];
  return <>
    {adding && <div className="panel db-edit">
      <div className="manual-stock-head"><h2>Новое сечение</h2></div>
      <GaugeEditor gauge={null} onSaved={async (message) => { setAdding(false); await onSaved(message); }} onError={onError} onCancel={() => setAdding(false)} />
    </div>}
    <DataTable
      label="Сечения AWG ↔ мм²"
      rows={gauges}
      columns={columns}
      rowKey={(gauge) => gauge.id}
      storageKey="db:gauges"
      sharedKey="gauges"
      exportName="Сечения AWG - мм2"
      renderDetail={canEdit ? (gauge) => <GaugeEditor key={`${gauge.id}:${gaugeOption(gauge)}`} gauge={gauge} onSaved={onSaved} onError={onError} /> : undefined}
      rowLabel={(gauge) => gauge.label}
      presets={gauges.some((gauge) => gauge.conductor_d_mm === null) ? [{ label: "Без Ø жилы", test: (gauge) => gauge.conductor_d_mm === null }] : undefined}
      onEdited={() => onSaved("")}
      emptyText="Таблица сечений пуста — добавьте сечение."
      toolbar={canEdit && !adding ? <button type="button" className="secondary small" onClick={() => setAdding(true)}>+ Сечение</button> : undefined}
    />
  </>;
}

function GaugeEditor({ gauge, onSaved, onError, onCancel }: {
  gauge: WireGauge | null;
  onSaved: (message: string) => Promise<void> | void;
  onError: (message: string) => void;
  onCancel?: () => void;
}) {
  const [draft, setDraft] = useState<GaugeDraft>(() => gaugeDraft(gauge));
  const [busy, setBusy] = useState(false);
  const field = (name: keyof GaugeDraft, label: string, decimal = false) => <label key={name}><small>{label}</small>
    <input aria-label={label} inputMode={decimal ? "decimal" : undefined} value={draft[name]} onChange={(event) => setDraft({ ...draft, [name]: event.target.value })} />
  </label>;
  async function run(action: () => Promise<unknown>, message: string) {
    if (busy) return;
    setBusy(true);
    try {
      await action();
      await onSaved(message);
    } catch (reason) {
      onError(failureText(reason, "Сечение не сохранено"));
    } finally {
      setBusy(false);
    }
  }
  const name = draft.awg ? `${draft.awg} AWG` : `${draft.section_mm2} мм²`;
  return <div className="cable-editor">
    <div className="cable-form">
      {field("awg", "AWG")}
      {field("section_mm2", "Сечение, мм²", true)}
      {field("conductor_d_mm", "Ø жилы, мм", true)}
    </div>
    <div className="form-actions cable-actions">
      {gauge && gauge.wires === 0 && <button type="button" className="link-button danger-link" disabled={busy}
        onClick={() => void run(() => referencesApi.deleteGauge(gauge.id), `Сечение ${gauge.label} удалено`)}>Удалить сечение</button>}
      {gauge && gauge.wires > 0 && <span className="muted">На этом сечении монтажных проводов: {gauge.wires}</span>}
      <button type="button" className="primary" disabled={busy || !draft.section_mm2.trim()}
        onClick={() => void run(() => referencesApi.saveGauge(gauge?.id ?? null, draft), gauge ? `Сечение ${name} сохранено` : `Сечение ${name} добавлено`)}>
        {gauge ? "Сохранить сечение" : "Добавить сечение"}
      </button>
      {onCancel && <button type="button" className="secondary" onClick={onCancel}>Отмена</button>}
    </div>
  </div>;
}

// --- «Монтажные провода» ---

const hookupPayload = (wire: Partial<HookupWire> | null | undefined): HookupPayload => ({
  series: wire?.series ?? "", gauge_id: wire?.gauge_id ?? "", insulation: wire?.insulation ?? "",
  outer_d_mm: num(wire?.outer_d_mm), voltage_v: num(wire?.voltage_v), temp_min_c: num(wire?.temp_min_c), temp_max_c: num(wire?.temp_max_c),
});

// Each colour is its own row (owner 20.10.2026: «имелось ввиду каждый цвет своя строка»): the row's colour, its dot
// and its name.
function WireColor({ name }: { name: string }) {
  const text = name.trim();
  if (!text) return <span className="muted">—</span>;
  const background = colorBackground(text);
  return <span className="wire-color">{background ? <i className="wire-dot" style={{ background }} aria-hidden="true" /> : <i className="wire-dot wire-dot-none" aria-hidden="true" />}{text}</span>;
}
/** A typed colour by the palette («кр» → «красный»), else as typed; a list is refused by the server in words. */
const oneColor = (value: string): string => canonicalColor(value.trim()) ?? value.trim();

const UNPARSED = "Не разобраны";
// The columns come in the group order of `cableColumnOrder.ts` with the list's «Только нужные» (owner 23.10.2026); the
// structure's «Изоляция» is no column (it repeats «Материал оболочки»), its value stays in the structure and the record.
const LINK = "Ссылка";
// The coax parameters (owner 19.10.2026: «эти параметры для монтажных не нужны») are not columns of this list at all.
const COAX_ONLY = new Set(["Погонная ёмкость, пФ/м", "Радиус изгиба неподвижно, мм", "Радиус изгиба многократно, мм"]);
const CORE_MATERIAL = "Материал жилы";
const JACKET_MATERIAL = "Материал оболочки";
type Shared = { jacket?: string; voltage?: string; temp?: string };

export function HookupWireTable({ data, canEdit, onSaved, onError, kindSelect, toolbar, removeColumn }: {
  data: CablesResult;
  canEdit: boolean;
  onSaved: (message: string) => Promise<void> | void;
  onError: (message: string) => void;
  /** The «Вкладка» control of a row (moving it to another list), from CableTables. */
  kindSelect: (row: CableRow, value: CableKind, onChange: (kind: CableKind) => void) => ReactNode;
  toolbar?: ReactNode;
  /** «Удалить столбец» своего столбца БД.ПРОВ (окно подтверждения — у страницы). */
  removeColumn?: (name: string) => (() => void) | undefined;
}) {
  const [adding, setAdding] = useState(false);
  const gauges = useMemo(() => data.gauges ?? [], [data.gauges]);
  const byId = useMemo(() => new Map(gauges.map((gauge) => [gauge.id, gauge])), [gauges]);
  // Grouped by series, sizes in a series by section as a number (the server's order is by text: 10, 16, 2,5, 4).
  const rows = useMemo(() => data.rows.filter((row) => row.kind === "wire").sort((a, b) =>
    (a.hookup ? 0 : 1) - (b.hookup ? 0 : 1)
    || (a.hookup?.series ?? "").localeCompare(b.hookup?.series ?? "", "ru")
    || (byId.get(a.hookup?.gauge_id ?? "")?.section_mm2 ?? 0) - (byId.get(b.hookup?.gauge_id ?? "")?.section_mm2 ?? 0)
    || a.cable.localeCompare(b.cable, "ru", { numeric: true })
    || (a.record?.fields[COLOR] ?? "").localeCompare(b.record?.fields[COLOR] ?? "", "ru")), [data.rows, byId]);
  // What every size of a series shares goes into the group header and is not repeated per row (a series of 2+ sizes only).
  const shared = useMemo(() => {
    const bySeries = new Map<string, CableRow[]>();
    for (const row of rows) if (row.hookup) bySeries.set(row.hookup.series, [...(bySeries.get(row.hookup.series) ?? []), row]);
    const result = new Map<string, Shared>();
    for (const [series, list] of bySeries) {
      if (list.length < 2) continue;
      const same = (pick: (wire: HookupWire, row: CableRow) => string) => {
        const values = new Set(list.map((row) => pick(row.hookup as HookupWire, row).trim()));
        const [only] = [...values];
        return values.size === 1 && only ? only : undefined;
      };
      result.set(series, {
        jacket: same((_wire, row) => row.record?.fields[JACKET_MATERIAL] ?? ""),
        voltage: same((wire) => wire.voltage_v === null ? "" : String(wire.voltage_v)),
        temp: same((wire) => range(wire.temp_min_c, wire.temp_max_c)),
      });
    }
    return result;
  }, [rows]);
  const sharedOf = (row: CableRow) => row.hookup ? shared.get(row.hookup.series) : undefined;
  const makerNames = useMemo(() => makerSuggestions(data.rows.map((item) => item.record?.fields[MAKERS] ?? "")), [data.rows]);
  const seriesColors = (series: string) => [...new Set(rows.filter((item) => item.hookup?.series === series.trim())
    .flatMap((item) => parseColors(item.record?.fields[COLOR] ?? "")))].join(", ");
  // The table itself appends « · N» — the number of sizes in the group.
  const groupLabel = (name: string) => {
    const own = name === UNPARSED ? undefined : shared.get(name);
    return [name, own?.jacket, own?.voltage && `${own.voltage} В`, own?.temp && `${own.temp} °C`].filter(Boolean).join(" · ");
  };
  const gaugeOf = (row: CableRow) => row.hookup ? byId.get(row.hookup.gauge_id) ?? null : null;
  // The structure is the mark's: a new colour of a structured mark takes it (`HookupEditor`).
  const hookupOf = (mark: string) => rows.find((item) => item.hookup && markKey(item.cable) === markKey(mark))?.hookup ?? null;
  const field = (row: CableRow, name: string) => row.record?.fields[name] ?? "";
  const clip = (text: string) => <span className="cable-clip" title={text}>{text}</span>;
  // «открыть» — the supplier's page of the wire, as on the coax lists.
  const supplierLink = (row: CableRow) => {
    const href = field(row, LINK).trim();
    if (!href) return null;
    return /^https?:\/\//i.test(href)
      ? <a href={href} target="_blank" rel="noreferrer" title={href} aria-label={`Открыть страницу ${row.cable} у поставщика`}
        onClick={(event) => event.stopPropagation()}>открыть</a>
      : clip(href);
  };
  const { columns: kindCols, own, removed } = kindColumns(data, "wire");
  const rowCols = cardColumns(kindCols, removed);
  // Every value of a parsed wire is written from its cell the way the wire's window writes it: the whole structure with
  // the old values (the server refuses it when the wire changed meanwhile). The size is a pick from «Сечения AWG ↔ мм²»,
  // the temperature is typed as «−60 … +200». A wire not parsed yet is set up in its window.
  const writeHookup = async (row: CableRow, change: Partial<HookupPayload>) => {
    const old = hookupPayload(row.hookup);
    try {
      await referencesApi.saveHookup(row.record!.id, { ...old, ...change, old });
    } catch (reason) { throw cellFailure(reason); }
  };
  const parsed = (row: CableRow) => Boolean(row.hookup && row.record);
  const hookupEdit = (key: "series" | "outer_d_mm" | "insulation" | "voltage_v"): DataCellEdit<CableRow> | undefined => canEdit ? {
    enabled: parsed,
    value: (row) => hookupPayload(row.hookup)[key],
    save: (row, value) => writeHookup(row, { [key]: value }),
  } : undefined;
  const sizes = useMemo(() => [...gauges].sort((a, b) => a.section_mm2 - b.section_mm2), [gauges]);
  const gaugeEdit = (by: "awg" | "section"): DataCellEdit<CableRow> | undefined => canEdit ? {
    enabled: parsed,
    value: (row) => row.hookup?.gauge_id ?? "",
    // AWG is typed or pasted as «22» / «22 AWG», the section as «0,35» / «0.35».
    options: () => sizes.map((gauge) => ({
      value: gauge.id, label: gaugeOption(gauge),
      aliases: by === "awg" ? (gauge.awg ? [gauge.awg, `${gauge.awg} AWG`, `AWG ${gauge.awg}`] : [])
        : [num(gauge.section_mm2), String(gauge.section_mm2), `${num(gauge.section_mm2)} мм²`],
    })),
    save: (row, value) => writeHookup(row, { gauge_id: value }),
  } : undefined;
  const tempEdit: DataCellEdit<CableRow> | undefined = canEdit ? {
    enabled: parsed,
    value: (row) => row.hookup ? range(row.hookup.temp_min_c, row.hookup.temp_max_c) : "",
    save: (row, value) => {
      const { min, max } = parseTempRange(value);
      return writeHookup(row, { temp_min_c: min, temp_max_c: max });
    },
  } : undefined;
  const base: DataColumn<CableRow>[] = [
    { key: "cable", title: "Марка провода", value: (row) => row.cable, render: (row) => <>
      <strong>{row.cable}</strong>
      {!row.hookup && <> <span className="tone-warn" title={row.hookup_problem || "Жила не выбрана из таблицы сечений"}>не разобран</span></>}
    </> },
    // The row's one colour (owner 20.10.2026: «каждый цвет своя строка») right after the mark — the rows of a mark
    // differ by it: a dot and a name; typed as text («кр») it goes by the palette, the picker is in the window.
    { key: "color", title: "Цвет", value: (row) => field(row, COLOR), render: (row) => <WireColor name={field(row, COLOR)} />,
      edit: canEdit ? fieldCellEdit<CableRow>(COLOR, (row) => row.record, true, oneColor) : undefined },
    // The series is also the group header (owner 16.10); shown again since 25.10.2026 («Серия» groups a mark's colours).
    {
      key: "series", title: "Серия", value: (row) => row.hookup?.series ?? "", edit: hookupEdit("series"),
      render: (row) => row.hookup ? row.hookup.series : <span className="tone-warn" title={row.hookup_problem || "Жила не выбрана из таблицы сечений"}>не разобран</span>,
    },
    { key: "awg", title: "AWG", numeric: true, value: (row) => gaugeOf(row)?.awg ?? "", edit: gaugeEdit("awg") },
    { key: "section", title: "Сечение, мм²", numeric: true, value: (row) => gaugeOf(row)?.section_mm2 ?? "", render: (row) => num(gaugeOf(row)?.section_mm2), edit: gaugeEdit("section") },
    // Ø жилы is the size row's (it changes with the size, or in «Сечения AWG ↔ мм²» for every wire of that size).
    { key: "core", title: "Ø жилы, мм", numeric: true, computed: true, hint: "По сечению — правится в «Сечения AWG ↔ мм²»", value: (row) => gaugeOf(row)?.conductor_d_mm ?? "", render: (row) => num(gaugeOf(row)?.conductor_d_mm) },
    { key: "strands", title: "Структура жилы", value: (row) => field(row, STRANDS), render: (row) => field(row, STRANDS) ? clip(field(row, STRANDS)) : <span className="muted">—</span>,
      edit: canEdit ? fieldCellEdit<CableRow>(STRANDS, (row) => row.record) : undefined },
    { key: "outer", title: "Ø наружный, мм", numeric: true, value: (row) => row.hookup?.outer_d_mm ?? "", render: (row) => num(row.hookup?.outer_d_mm), edit: hookupEdit("outer_d_mm") },
    { key: "voltage", title: "Напряжение, В", numeric: true, value: (row) => row.hookup?.voltage_v ?? "", render: (row) => sharedOf(row)?.voltage ? null : row.hookup?.voltage_v ?? "", edit: hookupEdit("voltage_v") },
    { key: "temp", title: "Температура, °C", value: (row) => row.hookup ? range(row.hookup.temp_min_c, row.hookup.temp_max_c) : "", render: (row) => sharedOf(row)?.temp ? null : row.hookup ? range(row.hookup.temp_min_c, row.hookup.temp_max_c) : "", edit: tempEdit },
    ...kindCols.filter((name) => name !== MARK && name !== CORE_COLORS && name !== COLOR && name !== STRANDS && !MIRRORED.includes(name) && !COAX_ONLY.has(name)).map((name): DataColumn<CableRow> => ({
      key: name, title: name, value: (row) => field(row, name),
      // The series' common jacket material is in the group header (owner 15.10: «Изоляция» — a rudiment).
      render: (row) => name === JACKET_MATERIAL && sharedOf(row)?.jacket ? null : name === LINK ? supplierLink(row) : clip(field(row, name)),
      // «Тип провода» is free text as in the window; the list a wire is in — the «Вкладка» cell.
      edit: !canEdit ? undefined : own.includes(name) ? ownCellEdit<CableRow>(name, (row) => row.record) : fieldCellEdit<CableRow>(name, (row) => row.record),
      remove: own.includes(name) ? removeColumn?.(name) : undefined,
    })),
  ];
  // Group order (core → jacket → outer Ø, owner 23.10.2026) and the list's columns only (25.10.2026: nothing hidden;
  // «Вкладка» / «Почему здесь» are the row window's «Вкладка», not columns); a column the owner just added is shown.
  const columns = arrangeCableColumns("wire", base, (key) => own.includes(key) && !knownCableColumn(key));
  const detail = (row: CableRow) => canEdit
    ? <HookupEditor key={`${row.id}:${JSON.stringify(row.hookup ?? null)}:${JSON.stringify(row.record?.fields ?? {})}:${row.kind}`}
      row={row} columns={rowCols} gauges={gauges} onSaved={onSaved} onError={onError} kindSelect={kindSelect} seriesColors={seriesColors} makerNames={makerNames} hookupOf={hookupOf} />
    : <HookupCard row={row} gauge={gaugeOf(row)} columns={rowCols} />;
  const unparsed = rows.filter((row) => !row.hookup).length;
  return <>
    {adding && <div className="panel db-edit">
      <div className="manual-stock-head"><h2>Новый монтажный провод</h2></div>
      <HookupEditor row={null} columns={kindCols} gauges={gauges} onSaved={async (message) => { setAdding(false); await onSaved(message); }} onError={onError} onCancel={() => setAdding(false)} kindSelect={kindSelect} seriesColors={seriesColors} makerNames={makerNames} hookupOf={hookupOf} />
    </div>}
    <DataTable
      label="Монтажные провода"
      rows={rows}
      columns={columns}
      rowKey={(row) => row.id}
      rowLabel={(row) => row.cable}
      // New keys with the columns of 25.10 (see CableTables): an older layout or shared set does not hide one of them.
      storageKey="db:cables:wire-structured:2510"
      sharedKey="prov:wire-structured:2510"
      groupBy={(row) => row.hookup?.series ?? UNPARSED}
      groupLabel={groupLabel}
      groupSort
      pageSize={Infinity}
      exportName="Монтажные провода"
      renderDetail={detail}
      presets={[
        ...(unparsed ? [{ label: "Не разобраны", test: (row: CableRow) => !row.hookup }] : []),
        { label: "Без Ø", test: (row: CableRow) => row.hookup?.outer_d_mm === null || row.hookup?.outer_d_mm === undefined },
        { label: "Без цвета", test: (row: CableRow) => !field(row, COLOR).trim() },
      ].filter((preset) => rows.some(preset.test))}
      onEdited={() => onSaved("")}
      emptyText="Монтажных проводов нет — добавьте провод."
      toolbar={<>{toolbar}{canEdit && !adding && <button type="button" className="secondary small" onClick={() => setAdding(true)}>+ Провод</button>}</>}
    />
  </>;
}

function HookupCard({ row, gauge, columns }: { row: CableRow; gauge: WireGauge | null; columns: string[] }) {
  const cells = row.record ? columns.filter((name) => row.record?.fields[name] && name !== MARK) : [];
  return <div className="reference-fields cable-card">
    <span><small>Жила</small>{gauge ? gaugeOption(gauge) : row.hookup_problem || "не выбрана"}</span>
    {cells.map((name) => <span key={name}><small>{name}</small>{row.record?.fields[name]}</span>)}
  </div>;
}

// A монтажный wire row has ONE colour («Цвет»); «Цвета» is the multicore list, no column of this list.
const COLOR = "Цвет";
const CORE_COLORS = "Цвета";
/** The ETK generator's cable key (backend `cable_key`): no case, spaces, hyphens. */
const markKey = (mark: string) => mark.toLowerCase().replace(/[\s-]/g, "");
const STRANDS = "Жила: проволок × диаметр, мм";
// Short labels of the form; the cell keeps its column name.
const CELL_LABELS: Record<string, string> = {
  [STRANDS]: "Структура жилы: проволок × Ø, мм", "Цена посл., ₽/м без НДС": "Цена посл., ₽/м", "Ссылка на datasheet": "Datasheet",
  "Источник данных": "Источник", "Волновое сопротивление, Ом": "Волновое, Ом", [STANDARD]: "Стандарт (ГОСТ / ТУ / UL)",
  "Сечение, мм2": "Сечение, мм²",
};
// «Число жил», «Ток, А», «Экран» are no columns of the list since 23.10.2026 (one value for all, ≤ 5 % filled).
const SECTION_CELLS = [[MARK, TYPE, STRANDS], [CORE_MATERIAL, JACKET_MATERIAL], ["Масса, г/м"],
  ["Поставщик", "Цена посл., ₽/м без НДС", "Закупок", "Ссылка на datasheet", "Источник данных"]];

function HookupEditor({ row, columns, gauges, onSaved, onError, onCancel, kindSelect, seriesColors, makerNames, hookupOf }: {
  seriesColors: (series: string) => string;
  /** The structure of a mark already in the list (another colour of it), null — none. */
  hookupOf?: (mark: string) => HookupWire | null;
  makerNames: string[];
  row: CableRow | null;
  columns: string[];
  gauges: WireGauge[];
  onSaved: (message: string) => Promise<void> | void;
  onError: (message: string) => void;
  onCancel?: () => void;
  kindSelect: (row: CableRow, value: CableKind, onChange: (kind: CableKind) => void) => ReactNode;
}) {
  const original = hookupPayload(row?.hookup ?? row?.hookup_suggestion);
  const ownFields = (record: TechOperationRecord | null) => record
    ? Object.fromEntries(Object.entries(record.fields).filter(([name]) => !MIRRORED.includes(name) && columns.includes(name)))
    : Object.fromEntries(columns.filter((name) => !MIRRORED.includes(name) && name !== CORE_COLORS).map((name) => [name, name === TYPE ? "монтажный" : ""]));
  const [wire, setWire] = useState<HookupPayload>(original);
  const [fields, setFields] = useState<Record<string, string>>(() => ownFields(row?.record ?? null));
  const [kind, setKind] = useState<CableKind>(row?.kind ?? "wire");
  const [busy, setBusy] = useState(false);
  const sorted = useMemo(() => [...gauges].sort((a, b) => a.section_mm2 - b.section_mm2), [gauges]);
  const changedWire = !row?.hookup || (Object.keys(wire) as Array<keyof HookupPayload>).some((name) => wire[name].trim() !== original[name].trim());

  async function save() {
    if (busy) return;
    if (row?.record && kind !== row.kind && !window.confirm(moveText(row.cable, kind))) return;
    setBusy(true);
    let record = row?.record ?? null;
    try {
      if (!record) {
        record = await referencesApi.createRecord("prov", fields, "wire");
      } else {
        const edits = Object.entries(fields)
          .filter(([name, value]) => (record?.fields[name] ?? "") !== value)
          .map(([name, value]) => ({ field: name, old: record?.fields[name] ?? "", value }));
        if (edits.length) record = await referencesApi.editRecord(record.id, edits);
      }
      // a new colour of a structured mark: the mark's structure is the old one, written only when changed here
      const shared = row ? null : hookupOf?.(fields[MARK] ?? "") ?? null;
      const sharedChanged = shared && (Object.keys(wire) as Array<keyof HookupPayload>).some((name) => wire[name].trim() !== hookupPayload(shared)[name].trim());
      if (changedWire && record && kind === "wire" && (!shared || sharedChanged)) {
        await referencesApi.saveHookup(record.id, { ...wire, old: row?.hookup ? hookupPayload(row.hookup) : shared ? hookupPayload(shared) : null });
      }
      if (row?.record && kind !== row.kind) await referencesApi.moveCable(row.record.id, kind);
      const mark = (fields[MARK] || record?.display_text || "").trim();
      await onSaved(row ? kind !== row.kind ? `«${mark}» перенесён во вкладку «${CABLE_KIND_LABELS[kind]}», заполненные значения сохранены` : `«${mark}» сохранён` : `«${mark}» добавлен`);
    } catch (reason) {
      onError(failureText(reason, "Провод не сохранён"));
      if (row || record) await onSaved("");
    } finally {
      setBusy(false);
    }
  }

  const input = (name: keyof HookupPayload, label: string, decimal = false) => <label key={name}><small>{label}</small>
    <input aria-label={label} inputMode={decimal ? "decimal" : undefined} value={wire[name]} onChange={(event) => setWire({ ...wire, [name]: event.target.value })} />
  </label>;
  const setCell = (name: string, value: string) => setFields({ ...fields, [name]: value });
  // The materials are own БД.ПРОВ columns: a wire without a value has no such cell yet, the form still offers it.
  const material = (name: string) => (name in fields || columns.includes(name)) && <label key={name}><small>{name}</small>
    <input aria-label={name} value={fields[name] ?? ""} onChange={(event) => setCell(name, event.target.value)} />
  </label>;
  const cell = (name: string) => name in fields && <label key={name} className={name === MARK || name === TYPE ? "wide" : undefined}><small>{CELL_LABELS[name] ?? name}</small>
    <input aria-label={CELL_LABELS[name] ?? name} value={fields[name] ?? ""} onChange={(event) => setCell(name, event.target.value)} />
  </label>;
  const grouped = new Set([...SECTION_CELLS.flat(), COLOR, STANDARD, MAKERS]);
  const rest = Object.keys(fields).filter((name) => !grouped.has(name));
  return <div className="cable-editor">
    {row && !row.hookup && <div className="warning-inline">Не разобран: {row.hookup_problem || "жила не выбрана"}. Поля заполнены по строке БД.ПРОВ — проверьте и сохраните.</div>}
    <section className="form-section"><h4>Основное</h4>
      <div className="cable-form hookup-form">
        {input("series", "Серия")}
        {cell(MARK)}
        {cell(TYPE)}
        <label className="wide"><small>Сечение</small>
          <select aria-label="Жила — сечение из таблицы" value={wire.gauge_id} onChange={(event) => setWire({ ...wire, gauge_id: event.target.value })}>
            <option value="">— выберите сечение —</option>
            {sorted.map((gauge) => <option key={gauge.id} value={gauge.id}>{gaugeOption(gauge)}</option>)}
          </select>
        </label>
        {cell(STRANDS)}
        {input("outer_d_mm", "Ø наружный, мм", true)}
      </div>
    </section>
    <section className="form-section"><h4>Материалы и режим</h4>
      <div className="cable-form">
        {material(CORE_MATERIAL)}
        {material(JACKET_MATERIAL)}
        {input("voltage_v", "Напряжение, В", true)}
        {input("temp_min_c", "Темп. от, °C", true)}
        {input("temp_max_c", "Темп. до, °C", true)}
      </div>
    </section>
    <section className="form-section"><h4>Доп. параметры</h4>
      <div className="cable-form">
        {cell("Масса, г/м")}
      </div>
      {COLOR in fields && <ColorPicker single label="Цвет" value={fields[COLOR] ?? ""} onChange={(value) => setCell(COLOR, value)} seriesColors={seriesColors(wire.series)} />}
    </section>
    <section className="form-section"><h4>Закупка и источник</h4>
      <div className="cable-form">
        {cell(STANDARD)}
        {cell("Поставщик")}
        {cell("Цена посл., ₽/м без НДС")}
        {cell("Закупок")}
        {cell("Ссылка на datasheet")}
        {cell("Источник данных")}
      </div>
      {MAKERS in fields && <MakersInput value={fields[MAKERS] ?? ""} onChange={(value) => setCell(MAKERS, value)} suggestions={makerNames} />}
    </section>
    {rest.length > 0 && <details className="form-section"><summary>Ещё параметры</summary>
      <div className="cable-form">{rest.map((name) => <label key={name}><small>{name}</small>
        <input aria-label={`Поле ${name}`} value={fields[name] ?? ""} onChange={(event) => setCell(name, event.target.value)} />
      </label>)}</div>
    </details>}
    <div className="form-actions cable-actions">
      {row?.record && kindSelect(row, kind, setKind)}
      <button type="button" className="primary" disabled={busy || (kind === "wire" && (!wire.gauge_id || !wire.series.trim())) || (!row && !(fields[MARK] ?? "").trim())} onClick={() => void save()}>
        {row ? "Сохранить" : "Добавить провод"}
      </button>
      {onCancel && <button type="button" className="secondary" onClick={onCancel}>Отмена</button>}
    </div>
  </div>;
}

// --- «Коаксиальные кабели»: the card ---

const KIND_LABELS: Record<string, string> = { nominal: "ном.", typical: "тип.", max: "макс." };
const CONFIDENCE_TONE: Record<string, string> = { высокая: "tone-ok", средняя: "tone-ok", низкая: "tone-warn" };

/** Russian decimal comma inside free text from the data file («7×0.102» → «7×0,102»). */
const ruDecimals = (text: string): string => text.replace(/(\d)\.(\d)/g, "$1,$2");
const LOSS_UNITS: Record<string, string> = { "dB/m": "дБ/м", "dB/100m": "дБ/100 м", "dB/100ft": "дБ/100 фут", "dB/ft": "дБ/фут", "dB/km": "дБ/км" };
type CoaxPoint = CoaxCard["attenuation"][number];
/** How a dB/m figure was obtained, in one word; the full wording stays in the tooltip and the footnote. */
const lossMethod = (point: CoaxPoint): string => point.interpolated
  ? (/^экстрапол/i.test(point.basis) ? "экстраполяция" : "интерполяция")
  : point.derived ? "пересчёт" : "таблица";
const lossMethodText = (point: CoaxPoint): string => {
  const between = point.basis.match(/между\s+([\d.,]+)\s+и\s+([\d.,]+)\s+МГц/i);
  if (point.interpolated) return between ? `${lossMethod(point)} ${num(Number(between[1]))}–${num(Number(between[2]))} МГц` : lossMethod(point);
  return point.basis && point.basis.length <= 24 ? ruDecimals(point.basis) : lossMethod(point);
};
const sourceName = (source: string): string => {
  try { return new URL(source).hostname.replace(/^www\./, ""); } catch { return source; }
};

/** The card of a coax cable holds only what the VNA check needs (owner 19.10.2026: «внутри карточки оставляем только
    параметры для векторника, ну и совместимые разъёмы»): header, markers, the source's attenuation, notes, sources.
    Construction and parameters are table columns; `aside` — the row's compatible connectors, next to the markers. */
export function CoaxCardView({ card, exact, aside, compact = false }: { card: CoaxCard; exact: boolean; aside?: ReactNode; compact?: boolean }) {
  // dB/m at a marker: a published point first (typical / nominal, then max), else the interpolated one.
  const lossAt = (mhz: number | null) => {
    const points = card.attenuation.filter((point) => point.freq_mhz === mhz);
    const rank = (point: CoaxPoint) => (point.interpolated ? 10 : 0) + (point.kind === "max" ? 1 : 0);
    return [...points].sort((a, b) => rank(a) - rank(b))[0] ?? null;
  };
  const onMarkers = new Set(card.markers.map((marker) => marker.freq_mhz));
  const datasheet = card.attenuation.some((point) => point.unit !== "dB/m" || point.kind === "max" || point.interpolated || !onMarkers.has(point.freq_mhz));
  const temp = range(card.params.temp_min_c ?? null, card.params.temp_max_c ?? null);
  const lossTemp = card.attenuation.find((point) => point.temp_c !== null)?.temp_c ?? null;
  const hasReference = card.markers.some((marker) => marker.vswr_ref !== null);
  const hasEstimate = card.markers.some((marker) => lossAt(marker.freq_mhz)?.interpolated);
  // The datasheet table drops a column nobody fills: the cable's own unit, the kind, the temperature.
  const showSource = card.attenuation.some((point) => point.unit !== "dB/m");
  const showKind = card.attenuation.some((point) => point.kind !== "nominal");
  const showTemp = card.attenuation.some((point) => point.temp_c !== null);
  const footnotes = [...new Set(card.attenuation.filter((point) => point.interpolated && /^экстрапол/i.test(point.basis)).map((point) => ruDecimals(point.basis)))];
  const flag = card.verified
    ? `проверено по datasheet${card.confidence ? ` · достоверность ${card.confidence}` : ""}`
    : card.confidence || "не проверено";
  const facts: Array<[string, string, string | undefined]> = [
    ["Диапазон VNA", spanText(card), undefined],
    ["Рабочая частота", card.max_operating_ghz !== null ? `до ${num(card.max_operating_ghz)} ГГц` : "", card.max_operating_note || undefined],
    ["Частота среза TE11", card.fc_ghz !== null ? `≈ ${num(card.fc_ghz)} ГГц` : "", card.fc_note ? ruDecimals(card.fc_note) : undefined],
    ["Температура", temp ? `${temp} °C` : "", undefined],
  ];
  return <section className="coax-card" aria-label={`Карточка кабеля ${card.name}`}>
    <header className="coax-card-head">
      <div className="coax-title">
        {compact
          ? !exact && <h3><small>Карточка семейства {card.name}</small></h3> // the window above already names the cable and its family
          : <h3>{card.name}{!exact && <small> — карточка семейства</small>}</h3>}
        {!compact && card.family && <span className="coax-desc">{ruDecimals(card.family)}</span>}
        <span className={`coax-flag ${card.verified ? CONFIDENCE_TONE[card.confidence] ?? "tone-ok" : "tone-warn"}`}>{flag}</span>
      </div>
      {!compact && <dl className="coax-facts">{facts.filter(([, value]) => value).map(([label, value, note]) => <div key={label} title={note}>
        <dt>{label}</dt><dd>{value}</dd>
      </div>)}</dl>}
    </header>
    {card.span_note && <p className="coax-note">Диапазон: {ruDecimals(card.span_note)}</p>}
    <div className="coax-grid">
      <div className="coax-col">
        <table className="coax-mini" aria-label="Маркеры VNA">
          <caption>Маркеры VNA <small>КСВН — норма приёмки (предложено)</small></caption>
          <thead><tr><th>Частота</th><th className="num">КСВН ≤</th>{hasReference && <th className="num" title="КСВН, который гарантирует паспорт эталонной сборки — по нему решается спорный брак">Паспорт ≤</th>}<th className="num">дБ/м{lossTemp !== null ? ` при ${num(lossTemp)} °C` : ""}</th></tr></thead>
          <tbody>{card.markers.length === 0
            ? <tr><td colSpan={hasReference ? 4 : 3} className="muted">Маркеров нет</td></tr>
            : card.markers.map((marker, index) => {
              const loss = lossAt(marker.freq_mhz);
              return <tr key={index} className={marker.peak ? "coax-peak" : undefined}>
                <td className={marker.peak ? "wrap" : undefined}>{marker.peak ? "Peak — максимум по диапазону" : freq(marker.freq_mhz)}</td>
                <td className="num" title={marker.note ? ruDecimals(marker.note) : undefined}>{marker.vswr_max !== null ? num(marker.vswr_max) : <span className="muted">—</span>}</td>
                {hasReference && <td className="num">{marker.vswr_ref !== null ? num(marker.vswr_ref) : <span className="muted">—</span>}</td>}
                <td className={`num${loss?.interpolated ? " coax-est" : ""}`} title={loss ? [ruDecimals(loss.basis), loss.derived ? `пересчитано из ${num(loss.value)} ${LOSS_UNITS[loss.unit] ?? loss.unit}` : ""].filter(Boolean).join("; ") || undefined : undefined}>
                  {loss ? <>{loss.interpolated ? "≈ " : ""}{num(Math.round(loss.loss_db_m * 100) / 100)}</> : <span className="muted">—</span>}
                </td>
              </tr>;
            })}</tbody>
        </table>
        {hasEstimate && <p className="coax-legend">≈ — в источнике нет точки на этой частоте: значение посчитано между соседними точками таблицы или по аппроксимации.</p>}
      </div>
      {aside && <div className="coax-col">{aside}</div>}
    </div>
    {datasheet && <div className="coax-wide"><table className="coax-mini coax-datasheet" aria-label="Затухание по источнику">
      <caption>Затухание по источнику</caption>
      <thead><tr>
        <th>Частота</th><th className="num">дБ/м</th>{showSource && <th className="num">В источнике</th>}{showKind && <th>Тип</th>}{showTemp && <th className="num">t, °C</th>}<th>Как получено</th><th>Источник</th>
      </tr></thead>
      <tbody>{card.attenuation.map((point, index) => <tr key={index}>
        <td>{freq(point.freq_mhz)}</td>
        <td className={`num${point.interpolated ? " coax-est" : ""}`}>{num(Math.round(point.loss_db_m * 1000) / 1000)}</td>
        {showSource && <td className="num">{point.unit !== "dB/m" ? <>{num(point.value)} <span className="muted">{LOSS_UNITS[point.unit] ?? point.unit}</span></> : <span className="muted">—</span>}</td>}
        {showKind && <td>{KIND_LABELS[point.kind] ?? point.kind}</td>}
        {showTemp && <td className="num">{num(point.temp_c) || "—"}</td>}
        <td className="wrap" title={point.basis ? ruDecimals(point.basis) : undefined}>{lossMethodText(point)}</td>
        <td>{point.source ? <a href={point.source} target="_blank" rel="noreferrer" title={point.source}>{sourceName(point.source)}</a> : <span className="muted">—</span>}</td>
      </tr>)}</tbody>
    </table>
    {footnotes.map((text) => <p key={text} className="coax-legend">Экстраполяция: {text}.</p>)}
    </div>}
    {card.notes && <p className="coax-note">{ruDecimals(card.notes)}</p>}
    {(card.sources.length > 0 || card.changes.length > 0) && <div className="coax-sources">
      {card.sources.length > 0 && <span>Источники: {card.sources.map((source, index) => <a key={index} href={source} target="_blank" rel="noreferrer" title={source}>{index + 1}</a>)}</span>}
      {card.changes.length > 0 && <details><summary>Исправлено при проверке: {card.changes.length}</summary><ul>{card.changes.map((change, index) => <li key={index}>{ruDecimals(change)}</li>)}</ul></details>}
    </div>}
  </section>;
}
