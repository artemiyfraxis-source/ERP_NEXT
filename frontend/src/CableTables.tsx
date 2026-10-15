import { PageWindow } from "./PageWindow";
import { useMemo, useState, type ReactNode } from "react";
import { DataTable, type DataCellEdit, type DataColumn } from "./DataTable";
import { CABLE_KIND_LABELS, CoaxCardView, num, spanText } from "./CableStructure";
import {
  failureText,
  referencesApi,
  type CableDiameters,
  type CableKind,
  type CableRow,
  type CablesResult,
  type TechOperationRecord,
} from "./api";
import { CATEGORIES, CATEGORY_CELL, cableCategory, cableFamily, cableGroup, compareByFamily, compareCables } from "./cableCategory";
import { coaxGroupOf, coaxNaming, sortCoax } from "./coaxFamilies";
import { cellFailure, ownCellEdit } from "./CustomColumn";
import { MakersInput } from "./WireFieldWidgets";
import { MAKERS, STANDARD, makerSuggestions } from "./wireMakers";

/* «Кабели и провода» (owner 04.10.2026: «объединить провода и кабели и коаксиальные … три вкладки»). One row per cable:
   a БД.ПРОВ row with the parameters, a coax cable joined with the D1·D2·D3 the ETK generator uses for its name. The
   list a row is in comes from the server (references/cables.py); the user moves a row from its editor. */

export { CABLE_KIND_LABELS };
const KINDS: CableKind[] = ["coax", "wire", "multi", "other"];

/** «Вкладка» of a БД.ПРОВ row: the list it is in, and why (rule or a manual move). */
export function kindSelect(row: CableRow, value: CableKind, onChange: (kind: CableKind) => void): ReactNode {
  return <label className="cable-kind"><small>Вкладка</small>
    <select aria-label="Вкладка" value={value} onChange={(event) => onChange(event.target.value as CableKind)}>
      {KINDS.map((item) => <option key={item} value={item}>{CABLE_KIND_LABELS[item]}{item === row.auto_kind && row.manual ? " (по правилу)" : ""}</option>)}
    </select>
    <span className="muted">{row.manual ? `перенесён вручную; по правилу — ${CABLE_KIND_LABELS[row.auto_kind].toLowerCase()}` : `по правилу: ${row.reason}`}</span>
  </label>;
}
const DIAMETERS = ["D1", "D2", "D3"] as const;
type Diameter = (typeof DIAMETERS)[number];
// СПР.КАБ coax table: D1 — центральная жила, D2 — по диэлектрику, D3 — по оплётке (not the jacket).
const DIA_TITLES: Record<Diameter, string> = { D1: "D1 жила, мм", D2: "D2 диэлектрик, мм", D3: "D3 оплётка, мм" };
const MARK = "Марка";
const TYPE = "Тип провода";
// The columns a technologist reads per list, in this order; every other БД.ПРОВ column is in «Колонки».
const SHOWN: Record<CableKind, string[]> = {
  // «Поставщик» (where the shop buys it) before «Производители» (owner 14.10: «тут больше надо не производитель, а поставщик»).
  coax: ["Наружный диаметр, мм", "Волновое сопротивление, Ом", "Изоляция", "Экран", TYPE, "Стандарт", "Поставщик", "Производители"],
  wire: [TYPE, "Сечение, мм2", "AWG", "Наружный диаметр, мм", "Жила: проволок × диаметр, мм", "Изоляция", "Цвета", "Темп. от, °C", "Темп. до, °C", "Напряжение, В"],
  multi: [TYPE, "Число жил", "Сечение, мм2", "AWG", "Наружный диаметр, мм", "Экран", "Изоляция", "Стандарт", "Производители"],
  other: [TYPE, "Число жил", "Сечение, мм2", "AWG", "Наружный диаметр, мм", "Экран", "Изоляция", "Волновое сопротивление, Ом", "Напряжение, В", "Стандарт", "Производители"],
};
// A new row starts in the list it was added from.
const NEW_TYPE: Record<CableKind, Record<string, string>> = {
  coax: { [TYPE]: "коаксиальный", "Число жил": "1" },
  wire: { [TYPE]: "монтажный", "Число жил": "1" },
  multi: { [TYPE]: "многожильный" },
  other: {},
};
const cardState = (row: CableRow) => !row.card ? "нет карточки"
  : `${row.card.verified ? `проверено${row.card.confidence ? `, ${row.card.confidence}` : ""}` : "не проверено"}${row.card_exact ? "" : ` · как у ${row.card.name}`}`;
const NUMBER = /^-?\d[\d\s]*([.,]\d+)?$/;
const cableKey = (value: string) => String(value || "").toLowerCase().replace(/[\s-]/g, "");
const diaText = (dia: CableDiameters) => DIAMETERS.map((name) => dia[name] || "—").join(" · ");
// Cells not written from the table: the mark renames the cable (its D-row and links follow it) and the category is a
// choice list — both stay in the record's own editor.
const NOT_IN_CELL = new Set([MARK, CATEGORY_CELL]);

type Draft = { fields: Record<string, string>; dia: Record<Diameter, string>; kind: CableKind };

/** An existing row edits its own cells (the server refuses a field the record has not); a new one — the БД.ПРОВ header. */
function draftOf(row: CableRow | null, columns: string[], kind: CableKind): Draft {
  return {
    fields: row ? { ...(row.record?.fields ?? {}) } : Object.fromEntries(columns.map((name) => [name, NEW_TYPE[kind][name] ?? ""])),
    dia: { D1: row?.dia?.D1 ?? "", D2: row?.dia?.D2 ?? "", D3: row?.dia?.D3 ?? "" },
    kind: row?.kind ?? kind,
  };
}

export function CableTable({ kind, data, canEdit, onSaved, onError, toolbar, removeColumn }: {
  kind: CableKind;
  data: CablesResult;
  canEdit: boolean;
  onSaved: (message: string) => Promise<void> | void;
  onError: (message: string) => void;
  toolbar?: ReactNode;
  /** «Удалить столбец» своего столбца БД.ПРОВ (окно подтверждения — у страницы). */
  removeColumn?: (name: string) => (() => void) | undefined;
}) {
  const [adding, setAdding] = useState(false);
  const grouped = kind === "multi" || kind === "other";
  const cells = (row: CableRow): Record<string, string> => ({ "Марка": row.cable, ...(row.record?.fields ?? {}) });
  // «Многожильные» (owner 15.10.2026): groups by mark family (ПВВС, КДВВГ / КДВЭВГ, F/UTP Cat5e…), cores and section
  // as numbers inside; «Прочие» — by category and screen, natural order inside.
  const rows = useMemo(() => {
    const own = data.rows.filter((row) => row.kind === kind);
    return kind === "multi" ? own.sort((a, b) => compareByFamily(cells(a), cells(b)))
      : grouped ? own.sort((a, b) => compareCables(cells(a), cells(b))) : kind === "coax" ? sortCoax(own) : own;
  }, [data.rows, kind, grouped]);
  const coaxGroup = useMemo(() => coaxGroupOf(rows), [rows]);
  const field = (row: CableRow, name: string) => row.record?.fields[name] ?? "";
  // A cell edited right in the table goes the way of the record's editor: one field with its old value (the server
  // refuses it when the record changed meanwhile), D1–D3 with the old three. No edit right — no editable cells.
  const fieldEdit = (name: string): DataCellEdit<CableRow> | undefined => canEdit && !NOT_IN_CELL.has(name) ? {
    enabled: (row) => Boolean(row.record && name in row.record.fields),
    save: async (row, value) => {
      try {
        await referencesApi.editRecord(row.record!.id, [{ field: name, old: field(row, name), value }]);
      } catch (reason) { throw cellFailure(reason); }
    },
  } : undefined;
  const diaEdit = (name: Diameter): DataCellEdit<CableRow> | undefined => canEdit ? {
    value: (row) => row.dia?.[name] ?? "",
    save: async (row, value) => {
      const old = Object.fromEntries(DIAMETERS.map((item) => [item, row.dia?.[item] ?? ""]));
      try {
        await referencesApi.saveCableDiameters({ cable: row.cable, dia_id: row.dia?.id ?? null, old, value: { ...old, [name]: value } });
      } catch (reason) { throw cellFailure(reason); }
    },
  } : undefined;

  // Rows stay one line high like a sheet: long text (insulation, screen, sources) is cut, full text on hover.
  const own = data.custom_columns ?? [];
  const textColumn = (name: string, hidden: boolean): DataColumn<CableRow> => {
    const values = rows.map((row) => field(row, name)).filter((value) => value && value !== "—");
    const long = values.some((value) => value.length > 28);
    return {
      key: name, title: name, value: (row) => field(row, name), hidden,
      edit: canEdit && own.includes(name) ? ownCellEdit<CableRow>(name, (row) => row.record) : fieldEdit(name),
      remove: own.includes(name) ? removeColumn?.(name) : undefined,
      render: long ? (row) => <span className="cable-clip" title={field(row, name)}>{field(row, name)}</span> : undefined,
      numeric: values.length > 0 && values.every((value) => NUMBER.test(value)),
    };
  };
  const shown = SHOWN[kind];
  const columns: DataColumn<CableRow>[] = [
    { key: "cable", title: kind === "wire" ? "Марка провода" : "Марка кабеля", value: (row) => row.cable, render: (row) => <strong>{row.cable}</strong> },
    ...(kind === "coax" ? DIAMETERS.map((name): DataColumn<CableRow> => ({
      key: name, title: DIA_TITLES[name], numeric: true, value: (row) => row.dia?.[name] ?? "", edit: diaEdit(name),
      render: (row) => row.dia
        ? row.dia[name] || <span className="muted">—</span>
        : <span className="tone-warn" title={`Нет D1–D3 — генератор возьмёт диаметры из записи разъёма${row.dia_hint ? `; похожий кабель: ${row.dia_hint.cable}` : ""}`}>—</span>,
    })) : []),
    ...(kind === "coax" ? [
      { key: "__span", title: "Диапазон VNA", value: (row: CableRow) => row.card ? spanText(row.card) : "" },
      { key: "__op", title: "Рабочая частота, ГГц", numeric: true, value: (row: CableRow) => row.card?.max_operating_ghz ?? "", render: (row: CableRow) => num(row.card?.max_operating_ghz) || <span className="cell-empty">—</span> },
      { key: "__fc", title: "Частота среза, ГГц", numeric: true, value: (row: CableRow) => row.card?.fc_ghz ?? "", render: (row: CableRow) => num(row.card?.fc_ghz) || <span className="cell-empty">—</span> },
      {
        key: "__card", title: "Карточка", value: cardState,
        render: (row: CableRow) => <span className={!row.card ? "muted" : row.card.verified && row.card.confidence !== "низкая" ? "tone-ok" : "tone-warn"}>{cardState(row)}</span>,
      },
    ] : []),
    ...shown.map((name) => textColumn(name, false)),
    ...data.columns.filter((name) => name !== MARK && !shown.includes(name)).map((name) => textColumn(name, !own.includes(name))),
    { key: "__reason", title: "Почему здесь", value: (row) => row.reason, hidden: !rows.some((row) => row.manual) },
  ];

  const detail = (row: CableRow) => <>
    {kind === "coax" && <CoaxNames row={row} />}
    {row.card && <CoaxCardView card={row.card} exact={Boolean(row.card_exact)} />}
    {canEdit
      ? <CableEditor key={`${row.id}:${JSON.stringify(row.record?.fields ?? {})}:${row.dia ? diaText(row.dia) : ""}:${row.kind}`}
        row={row} kind={kind} data={data} onSaved={onSaved} onError={onError} />
      : <CableCard row={row} columns={data.columns} />}
  </>;

  return <>
    {adding && <PageWindow label="Новая запись кабеля" width={900} close={() => setAdding(false)}><div className="panel db-edit">
      <div className="manual-stock-head"><h2>Новая запись: {CABLE_KIND_LABELS[kind].toLowerCase()}</h2></div>
      <CableEditor row={null} kind={kind} data={data} onSaved={async (message) => { setAdding(false); await onSaved(message); }} onError={onError} onCancel={() => setAdding(false)} />
    </div></PageWindow>}
    <DataTable
      key={kind}
      label={CABLE_KIND_LABELS[kind]}
      rows={rows}
      columns={columns}
      rowKey={(row) => row.id}
      rowLabel={(row) => row.cable}
      onEdited={() => onSaved("")}
      // The saved column set lists every hidden column, «Поставщик» among them until 14.10: coax starts from a new key.
      storageKey={kind === "coax" ? "db:cables:coax:2" : `db:cables:${kind}`}
      exportName={CABLE_KIND_LABELS[kind]}
      renderDetail={detail}
      collapsibleGroups={kind === "coax"}
      groupBy={kind === "multi" ? (row) => cableFamily(cells(row)).family : grouped ? (row) => cableGroup(cells(row)).label : kind === "coax" ? coaxGroup : undefined}
      presets={kind === "coax" ? [{ label: "Без D1–D3", test: (row) => !row.dia }, { label: "Без карточки", test: (row) => !row.card }]
        : rows.some((row) => !field(row, "Наружный диаметр, мм").trim()) ? [{ label: "Без Ø", test: (row) => !field(row, "Наружный диаметр, мм").trim() }] : undefined}
      emptyText={kind === "coax" ? "Коаксиальных кабелей нет — загрузите зеркало БД.ПРОВ и СПР.КАБ или добавьте запись." : "Записей нет."}
      toolbar={<>{toolbar}{canEdit && !adding && <button type="button" className="secondary small" onClick={() => setAdding(true)}>+ Запись</button>}</>}
    />
  </>;
}

/** Full name and aliases of a coax cable, above its card. */
function CoaxNames({ row }: { row: CableRow }) {
  const { fullName, aliases } = coaxNaming(row);
  if (!fullName && !aliases.length) return null;
  return <div className="reference-fields cable-card">
    {fullName && <span><small>Полное название</small>{fullName}</span>}
    {aliases.length > 0 && <span><small>Другие названия</small>{aliases.join(" · ")}</span>}
  </div>;
}

/** Read-only card: every БД.ПРОВ cell of the row and its D1·D2·D3. */
function CableCard({ row, columns }: { row: CableRow; columns: string[] }) {
  const cells = row.record ? columns.filter((name) => row.record?.fields[name]) : [];
  return <div className="reference-fields cable-card">
    {row.dia && DIAMETERS.map((name) => <span key={name}><small>{DIA_TITLES[name]}</small>{row.dia?.[name] || "—"}</span>)}
    {cells.map((name) => <span key={name}><small>{name}</small>{row.record?.fields[name]}</span>)}
    <span><small>Почему здесь</small>{row.reason}</span>
  </div>;
}

function CableEditor({ row, kind, data, onSaved, onError, onCancel }: {
  row: CableRow | null;
  kind: CableKind;
  data: CablesResult;
  onSaved: (message: string) => Promise<void> | void;
  onError: (message: string) => void;
  onCancel?: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(row, data.columns, kind));
  const makerNames = useMemo(() => makerSuggestions(data.rows.map((item) => item.record?.fields[MAKERS] ?? "")), [data.rows]);
  const [busy, setBusy] = useState(false);
  const isCoax = row ? row.kind === "coax" || Boolean(row.dia) : kind === "coax";
  const diaOnly = Boolean(row && !row.record);
  const original = draftOf(row, data.columns, kind);
  const hint = row?.dia_hint ?? null;

  async function save() {
    if (busy) return;
    setBusy(true);
    const changedDia = DIAMETERS.some((name) => draft.dia[name].trim() !== original.dia[name].trim());
    let created: TechOperationRecord | null = null;
    try {
      let record = row?.record ?? null;
      if (!row) {
        record = created = await referencesApi.createRecord("prov", draft.fields);
      }
      const cable = row ? row.cable : (draft.fields[MARK] || record?.display_text || "").trim();
      if (isCoax && changedDia && cable) {
        // A cable already in the D-table under this name (a new row named like it) edits that row, as the server keys it.
        const current = row ? row.dia : data.rows.find((item) => item.dia && cableKey(item.dia.cable) === cableKey(cable))?.dia ?? null;
        await referencesApi.saveCableDiameters({
          cable, dia_id: current?.id ?? null,
          old: Object.fromEntries(DIAMETERS.map((name) => [name, current?.[name] ?? ""])),
          value: draft.dia,
        });
      }
      if (row?.record) {
        const edits = Object.entries(draft.fields)
          .filter(([name, value]) => (row.record?.fields[name] ?? "") !== value)
          .map(([name, value]) => ({ field: name, old: row.record?.fields[name] ?? "", value }));
        if (edits.length) await referencesApi.editRecord(row.record.id, edits);
        if (draft.kind !== row.kind) await referencesApi.moveCable(row.record.id, draft.kind);
      }
      await onSaved(row
        ? draft.kind !== row.kind ? `«${row.cable}» перенесён во вкладку «${CABLE_KIND_LABELS[draft.kind]}»` : `«${row.cable}» сохранён`
        : `«${cable}» добавлен`);
    } catch (reason) {
      onError(failureText(reason, "Запись не сохранена"));
      // Part of it may be saved (the row, the move): show what the server has now.
      if (row || created) await onSaved("");
    } finally {
      setBusy(false);
    }
  }

  const setField = (name: string, value: string) => setDraft({ ...draft, fields: { ...draft.fields, [name]: value } });
  const setDia = (name: Diameter, value: string) => setDraft({ ...draft, dia: { ...draft.dia, [name]: value } });
  return <div className="cable-editor">
    {isCoax && <div className="cable-dia">
      {DIAMETERS.map((name) => <label key={name}><small>{DIA_TITLES[name]}</small>
        <input aria-label={`${name} кабеля`} inputMode="decimal" value={draft.dia[name]} onChange={(event) => setDia(name, event.target.value)} />
      </label>)}
      {hint && <button type="button" className="secondary small" onClick={() => setDraft({ ...draft, dia: { D1: hint.D1, D2: hint.D2, D3: hint.D3 } })}>
        Как у {hint.cable}: {diaText(hint)}
      </button>}
      {diaOnly && <span className="muted">{row?.dia ? "Кабеля нет в базе проводов, есть только его диаметры." : "Кабеля нет в базе проводов, есть только его карточка."}</span>}
    </div>}
    {!diaOnly && <div className="cable-form">
      {Object.keys(draft.fields).filter((name) => name !== MAKERS && name !== CATEGORY_CELL).map((name) => <label key={name} className={name === MARK || name === TYPE ? "wide" : undefined}><small>{name === STANDARD ? "Стандарт (ГОСТ / ТУ / UL)" : name}</small>
        <input aria-label={`Поле ${name}`} value={draft.fields[name] ?? ""} onChange={(event) => setField(name, event.target.value)} />
      </label>)}
      {CATEGORY_CELL in draft.fields && <label><small>Категория</small>
        <select aria-label="Категория" value={draft.fields[CATEGORY_CELL] ?? ""} onChange={(event) => setField(CATEGORY_CELL, event.target.value)}>
          <option value="">по правилу: {cableCategory({ ...draft.fields, [CATEGORY_CELL]: "" }).category}</option>
          {CATEGORIES.map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
      </label>}
      {MAKERS in draft.fields && <MakersInput value={draft.fields[MAKERS] ?? ""} onChange={(value) => setField(MAKERS, value)} suggestions={makerNames} />}
    </div>}
    <div className="form-actions cable-actions">
      {row?.record && kindSelect(row, draft.kind, (next) => setDraft({ ...draft, kind: next }))}
      <button type="button" className="primary" disabled={busy || (!row && !Object.values(draft.fields).some((value) => value.trim()))} onClick={() => void save()}>
        {row ? "Сохранить" : "Добавить запись"}
      </button>
      {onCancel && <button type="button" className="secondary" onClick={onCancel}>Отмена</button>}
    </div>
  </div>;
}
