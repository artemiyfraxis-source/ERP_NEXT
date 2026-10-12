import { useMemo, useState, type ReactNode } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import {
  failureText,
  referencesApi,
  type CableKind,
  type CableRow,
  type CablesResult,
  type CoaxCard,
  type CoaxParam,
  type HookupPayload,
  type HookupWire,
  type TechOperationRecord,
  type WireGauge,
} from "./api";

/* Structured cable reference (owner 10.10.2026: «наведи порядок в базах коаксиалов, монтажных проводов и сечений»):
   «Сечения AWG ↔ мм²» (one row per conductor size), «Монтажные провода» (series + conductor as a size row + insulation,
   outer Ø, voltage, temperature) and the coax card a tester reads at the VNA. Backend: references/cable_structure.py. */

export const CABLE_KIND_LABELS: Record<CableKind, string> = {
  coax: "Коаксиальные кабели", wire: "Монтажные провода", multi: "Многожильные кабели", other: "Прочие кабели",
};

/** A number as the shop reads it: comma decimal, no trailing zeros. */
export const num = (value: number | null | undefined): string =>
  value === null || value === undefined ? "" : String(value).replace(".", ",");
const freq = (mhz: number | null): string =>
  mhz === null ? "" : mhz >= 1000 ? `${num(Math.round(mhz / 10) / 100)} ГГц` : `${num(mhz)} МГц`;
const signed = (value: number | null) => value === null ? "" : value > 0 ? `+${value}` : String(value).replace("-", "−");
const range = (low: number | null, high: number | null) =>
  low === null && high === null ? "" : `${low === null ? "…" : signed(low)} … ${high === null ? "…" : signed(high)}`;
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
    { key: "dia", title: "Ø жилы, мм", numeric: true, value: (gauge) => gauge.conductor_d_mm ?? "", render: (gauge) => num(gauge.conductor_d_mm) || <span className="muted">—</span> },
    { key: "strands", title: "Скрутка, проволок × Ø", value: (gauge) => gauge.strands },
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
      exportName="Сечения AWG - мм2"
      renderDetail={canEdit ? (gauge) => <GaugeEditor key={`${gauge.id}:${gaugeOption(gauge)}:${gauge.strands}`} gauge={gauge} onSaved={onSaved} onError={onError} /> : undefined}
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
      {field("strands", "Скрутка, проволок × Ø")}
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

export function HookupWireTable({ data, canEdit, onSaved, onError, kindSelect, toolbar }: {
  data: CablesResult;
  canEdit: boolean;
  onSaved: (message: string) => Promise<void> | void;
  onError: (message: string) => void;
  /** The «Вкладка» control of a row (moving it to another list), from CableTables. */
  kindSelect: (row: CableRow, value: CableKind, onChange: (kind: CableKind) => void) => ReactNode;
  toolbar?: ReactNode;
}) {
  const [adding, setAdding] = useState(false);
  const gauges = useMemo(() => data.gauges ?? [], [data.gauges]);
  const byId = useMemo(() => new Map(gauges.map((gauge) => [gauge.id, gauge])), [gauges]);
  const rows = useMemo(() => data.rows.filter((row) => row.kind === "wire"), [data.rows]);
  const gaugeOf = (row: CableRow) => row.hookup ? byId.get(row.hookup.gauge_id) ?? null : null;
  const field = (row: CableRow, name: string) => row.record?.fields[name] ?? "";
  const clip = (text: string) => <span className="cable-clip" title={text}>{text}</span>;
  const columns: DataColumn<CableRow>[] = [
    { key: "cable", title: "Марка провода", value: (row) => row.cable, render: (row) => <strong>{row.cable}</strong> },
    {
      key: "series", title: "Серия", value: (row) => row.hookup?.series ?? "",
      render: (row) => row.hookup ? row.hookup.series : <span className="tone-warn" title={row.hookup_problem || "Жила не выбрана из таблицы сечений"}>не разобран</span>,
    },
    { key: "awg", title: "AWG", numeric: true, value: (row) => gaugeOf(row)?.awg ?? "" },
    { key: "section", title: "Сечение, мм²", numeric: true, value: (row) => gaugeOf(row)?.section_mm2 ?? "", render: (row) => num(gaugeOf(row)?.section_mm2) },
    { key: "core", title: "Ø жилы, мм", numeric: true, value: (row) => gaugeOf(row)?.conductor_d_mm ?? "", render: (row) => num(gaugeOf(row)?.conductor_d_mm) },
    { key: "outer", title: "Ø наружный, мм", numeric: true, value: (row) => row.hookup?.outer_d_mm ?? "", render: (row) => num(row.hookup?.outer_d_mm) },
    { key: "insulation", title: "Изоляция", value: (row) => row.hookup?.insulation ?? "", render: (row) => clip(row.hookup?.insulation ?? "") },
    { key: "voltage", title: "Напряжение, В", numeric: true, value: (row) => row.hookup?.voltage_v ?? "" },
    { key: "temp", title: "Температура, °C", value: (row) => row.hookup ? range(row.hookup.temp_min_c, row.hookup.temp_max_c) : "" },
    { key: "colors", title: "Цвета", value: (row) => field(row, "Цвета"), render: (row) => clip(field(row, "Цвета")) },
    ...data.columns.filter((name) => name !== MARK && name !== "Цвета" && !MIRRORED.includes(name)).map((name): DataColumn<CableRow> => ({
      key: name, title: name, value: (row) => field(row, name), hidden: true, render: (row) => clip(field(row, name)),
    })),
    { key: "__reason", title: "Почему здесь", value: (row) => row.reason, hidden: !rows.some((row) => row.manual) },
  ];
  const detail = (row: CableRow) => canEdit
    ? <HookupEditor key={`${row.id}:${JSON.stringify(row.hookup ?? null)}:${JSON.stringify(row.record?.fields ?? {})}:${row.kind}`}
      row={row} columns={data.columns} gauges={gauges} onSaved={onSaved} onError={onError} kindSelect={kindSelect} />
    : <HookupCard row={row} gauge={gaugeOf(row)} columns={data.columns} />;
  const unparsed = rows.filter((row) => !row.hookup).length;
  return <>
    {adding && <div className="panel db-edit">
      <div className="manual-stock-head"><h2>Новый монтажный провод</h2></div>
      <HookupEditor row={null} columns={data.columns} gauges={gauges} onSaved={async (message) => { setAdding(false); await onSaved(message); }} onError={onError} onCancel={() => setAdding(false)} kindSelect={kindSelect} />
    </div>}
    <DataTable
      label="Монтажные провода"
      rows={rows}
      columns={columns}
      rowKey={(row) => row.id}
      storageKey="db:cables:wire-structured"
      exportName="Монтажные провода"
      renderDetail={detail}
      presets={unparsed ? [{ label: "Не разобраны", test: (row) => !row.hookup }] : undefined}
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

function HookupEditor({ row, columns, gauges, onSaved, onError, onCancel, kindSelect }: {
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
    ? Object.fromEntries(Object.entries(record.fields).filter(([name]) => !MIRRORED.includes(name)))
    : Object.fromEntries(columns.filter((name) => !MIRRORED.includes(name)).map((name) => [name, name === TYPE ? "монтажный" : name === "Число жил" ? "1" : ""]));
  const [wire, setWire] = useState<HookupPayload>(original);
  const [fields, setFields] = useState<Record<string, string>>(() => ownFields(row?.record ?? null));
  const [kind, setKind] = useState<CableKind>(row?.kind ?? "wire");
  const [busy, setBusy] = useState(false);
  const sorted = useMemo(() => [...gauges].sort((a, b) => a.section_mm2 - b.section_mm2), [gauges]);
  const changedWire = !row?.hookup || (Object.keys(wire) as Array<keyof HookupPayload>).some((name) => wire[name].trim() !== original[name].trim());

  async function save() {
    if (busy) return;
    setBusy(true);
    let record = row?.record ?? null;
    try {
      if (!record) {
        record = await referencesApi.createRecord("prov", fields);
      } else {
        const edits = Object.entries(fields)
          .filter(([name, value]) => (record?.fields[name] ?? "") !== value)
          .map(([name, value]) => ({ field: name, old: record?.fields[name] ?? "", value }));
        if (edits.length) record = await referencesApi.editRecord(record.id, edits);
      }
      if (changedWire && record && kind === "wire") {
        await referencesApi.saveHookup(record.id, { ...wire, old: row?.hookup ? hookupPayload(row.hookup) : null });
      }
      if (row?.record && kind !== row.kind) await referencesApi.moveCable(row.record.id, kind);
      const mark = (fields[MARK] || record?.display_text || "").trim();
      await onSaved(row ? kind !== row.kind ? `«${mark}» перенесён во вкладку «${CABLE_KIND_LABELS[kind]}»` : `«${mark}» сохранён` : `«${mark}» добавлен`);
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
  return <div className="cable-editor">
    {row && !row.hookup && <div className="warning-inline">Не разобран: {row.hookup_problem || "жила не выбрана"}. Поля заполнены по строке БД.ПРОВ — проверьте и сохраните.</div>}
    <div className="cable-form hookup-form">
      {input("series", "Серия (ПВАМ, МГТФ, UL1007…)")}
      <label className="wide"><small>Жила — сечение из таблицы</small>
        <select aria-label="Жила — сечение из таблицы" value={wire.gauge_id} onChange={(event) => setWire({ ...wire, gauge_id: event.target.value })}>
          <option value="">— выберите сечение —</option>
          {sorted.map((gauge) => <option key={gauge.id} value={gauge.id}>{gaugeOption(gauge)}</option>)}
        </select>
      </label>
      {input("outer_d_mm", "Ø наружный, мм", true)}
      {input("insulation", "Изоляция")}
      {input("voltage_v", "Напряжение, В", true)}
      {input("temp_min_c", "Темп. от, °C", true)}
      {input("temp_max_c", "Темп. до, °C", true)}
    </div>
    <div className="cable-form">
      {Object.keys(fields).map((name) => <label key={name} className={name === MARK || name === TYPE ? "wide" : undefined}><small>{name}</small>
        <input aria-label={`Поле ${name}`} value={fields[name] ?? ""} onChange={(event) => setFields({ ...fields, [name]: event.target.value })} />
      </label>)}
    </div>
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

const PARAM_LABELS: Array<[CoaxParam, string, string]> = [
  ["impedance_ohm", "Волновое сопротивление", "Ом"], ["velocity_pct", "Коэф. укорочения", "%"],
  ["capacitance_pf_m", "Погонная ёмкость", "пФ/м"], ["shielding_db", "Экранировка", "дБ"],
  ["bend_static_mm", "Радиус изгиба, неподвижно", "мм"], ["bend_dynamic_mm", "Радиус изгиба, многократно", "мм"],
  ["voltage_max_v", "Напряжение, макс.", "В"], ["max_power_w", "Мощность, макс.", "Вт"], ["weight_g_m", "Масса", "г/м"],
];
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

export function CoaxCardView({ card, exact }: { card: CoaxCard; exact: boolean }) {
  // dB/m at a marker: a published point first (typical / nominal, then max), else the interpolated one.
  const lossAt = (mhz: number | null) => {
    const points = card.attenuation.filter((point) => point.freq_mhz === mhz);
    const rank = (point: CoaxPoint) => (point.interpolated ? 10 : 0) + (point.kind === "max" ? 1 : 0);
    return [...points].sort((a, b) => rank(a) - rank(b))[0] ?? null;
  };
  const onMarkers = new Set(card.markers.map((marker) => marker.freq_mhz));
  const datasheet = card.attenuation.some((point) => point.unit !== "dB/m" || point.kind === "max" || point.interpolated || !onMarkers.has(point.freq_mhz));
  const temp = range(card.params.temp_min_c ?? null, card.params.temp_max_c ?? null);
  const params = PARAM_LABELS.filter(([key]) => card.params[key] !== undefined);
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
        <h3>{card.name}{!exact && <small> — карточка семейства</small>}</h3>
        {card.family && <span className="coax-desc">{ruDecimals(card.family)}</span>}
        <span className={`coax-flag ${card.verified ? CONFIDENCE_TONE[card.confidence] ?? "tone-ok" : "tone-warn"}`}>{flag}</span>
      </div>
      <dl className="coax-facts">{facts.filter(([, value]) => value).map(([label, value, note]) => <div key={label} title={note}>
        <dt>{label}</dt><dd>{value}</dd>
      </div>)}</dl>
    </header>
    {card.span_note && <p className="coax-note">Диапазон: {ruDecimals(card.span_note)}</p>}
    <div className="coax-grid">
      <div className="coax-col">
        {card.layers.length > 0 && <table className="coax-mini" aria-label="Конструкция">
          <caption>Конструкция</caption>
          <thead><tr><th>Слой</th><th>Материал</th><th className="num">Ø, мм</th></tr></thead>
          <tbody>{card.layers.map((layer) => <tr key={layer.layer}>
            <td>{layer.label}</td><td className="wrap">{layer.material ? ruDecimals(layer.material) : "—"}</td><td className="num">{num(layer.diameter_mm) || "—"}</td>
          </tr>)}</tbody>
        </table>}
        {card.connectors.length > 0 && <table className="coax-mini" aria-label="Разъёмы">
          <caption>Разъёмы</caption>
          <thead><tr><th>Разъём</th><th>Предел разъёма</th><th>Диапазон сборки</th></tr></thead>
          <tbody>{card.connectors.map((connector) => <tr key={connector.family}>
            <td className="wrap">{connector.family}</td>
            <td className="wrap" title={connector.max_note ? ruDecimals(connector.max_note) : undefined}>{connector.max_ghz !== null ? `до ${num(connector.max_ghz)} ГГц` : connector.max_note ? ruDecimals(connector.max_note) : "—"}</td>
            <td>{connector.span_to_ghz !== null ? <strong>до {num(connector.span_to_ghz)} ГГц</strong> : <span className="muted">как у кабеля</span>}</td>
          </tr>)}</tbody>
        </table>}
      </div>
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
      {params.length > 0 && <div className="coax-col">
        <table className="coax-mini" aria-label="Параметры кабеля">
          <caption>Параметры</caption>
          <tbody>{params.map(([key, label, unit]) => <tr key={key} title={card.param_notes[key] ? ruDecimals(card.param_notes[key]) : undefined}>
            <th scope="row">{label}</th>
            <td className="num">{num(card.params[key])} <span className="muted">{unit}</span>{key === "max_power_w" && card.params.max_power_at_mhz !== undefined ? ` на ${freq(card.params.max_power_at_mhz)}` : ""}</td>
          </tr>)}</tbody>
        </table>
        {card.param_notes.mechanical && <p className="coax-legend">{ruDecimals(card.param_notes.mechanical)}</p>}
      </div>}
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
