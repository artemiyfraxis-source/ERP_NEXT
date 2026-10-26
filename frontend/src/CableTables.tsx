import { PageWindow } from "./PageWindow";
import { useMemo, useState, type ReactNode } from "react";
import { DataTable, type DataCellEdit, type DataColumn } from "./DataTable";
import { CABLE_KIND_LABELS, CoaxCardView, cardColumns, freq, kindColumns, moveText, num, range, spanText } from "./CableStructure";
import {
  failureText,
  referencesApi,
  type CableDiameters,
  type CableKind,
  type CableRow,
  type CablesResult,
  type CoaxCard,
  type CoaxParam,
  type TechOperationRecord,
} from "./api";
import { CATEGORIES, CATEGORY_CELL, cableCategory, cableFamily, compareFamilyGroups } from "./cableCategory";
import { CABLE_SORT_HINT, SortHint, compareNames, compareNumbers, ethernetCategoryRank, firstNumber } from "./refSort";
import { coaxGroupOf, coaxOtherNames, compareCoaxGroups } from "./coaxFamilies";
import { coaxArticle, coaxSeries, coaxSupplier, coaxType, seriesRank } from "./CoaxConnectors";
import { cellFailure, fieldCellEdit, ownCellEdit } from "./CustomColumn";
import { MakersInput } from "./WireFieldWidgets";
import { MAKERS, STANDARD, makerSuggestions } from "./wireMakers";
import { arrangeCableColumns, knownCableColumn } from "./cableColumnOrder";

/* «Кабели и провода» (owner 04.10.2026: «объединить провода и кабели и коаксиальные … три вкладки»). One row per cable:
   a БД.ПРОВ row with the parameters, a coax cable joined with the D1·D2·D3 the ETK generator uses for its name. The
   list a row is in comes from the server (references/cables.py); the user moves a row from its editor. */

export { CABLE_KIND_LABELS };
const KINDS: CableKind[] = ["coax", "wire", "multi", "lan"];

/** «Вкладка» of a БД.ПРОВ row: the list it is in, and why (rule or a manual move). */
export function kindSelect(row: CableRow, value: CableKind, onChange: (kind: CableKind) => void): ReactNode {
  return <label className="cable-kind"><small>Вкладка</small>
    <select aria-label="Вкладка" value={value} onChange={(event) => onChange(event.target.value as CableKind)}>
      {KINDS.map((item) => <option key={item} value={item}>{CABLE_KIND_LABELS[item]}{item === row.auto_kind && row.manual ? " (по правилу)" : ""}</option>)}
    </select>
    {value !== row.kind && <span className="muted">Заполненные значения строки сохранятся при переносе.</span>}
    <span className="muted">{row.manual ? `перенесён вручную; по правилу — ${CABLE_KIND_LABELS[row.auto_kind].toLowerCase()}` : `по правилу: ${row.reason}`}</span>
  </label>;
}
const DIAMETERS = ["D1", "D2", "D3"] as const;
type Diameter = (typeof DIAMETERS)[number];
// СПР.КАБ coax table: D1 — центральная жила, D2 — по диэлектрику, D3 — по оплётке (not the jacket).
const DIA_TITLES: Record<Diameter, string> = { D1: "D1 жила, мм", D2: "D2 диэлектрик, мм", D3: "D3 оплётка, мм" };
const MARK = "Марка";
const TYPE = "Тип провода";
// The columns of a list come in the group order of `cableColumnOrder.ts` (owner 23.10.2026: «своя инфа в своём месте»),
// only the list's own (25.10.2026: nothing hidden — a column is shown or removed).
export const COAX_MATERIALS = ["Материал жилы", "Материал диэлектрика", "Материал экрана", "Материал оболочки"];
// Multicore (owner 15.10.2026: «изоляция слишком обобщённо, тут нет диэлектрика»): core, core insulation, screen if any,
// jacket; the old «Изоляция» text cell is no column of the list since 23.10.2026.
export const MULTI_MATERIALS = ["Материал жилы", "Материал изоляции жилы", "Материал экрана", "Материал оболочки"];
// «Ссылка» — the supplier's page of the cable (owner 19.10.2026, as on «Коаксиальные разъёмы»): «открыть» in the cell;
// «Ссылка на datasheet» — the maker's document, the same way.
const LINK = "Ссылка";
const DATASHEET = "Ссылка на datasheet";
/** A coax parameter column (owner 19.10.2026: «Параметры» out of the card, into the table): the БД.ПРОВ cell when it is
    filled, else the card's value. `field` — the БД.ПРОВ column (base or the owner's own), also the column key. */
type CoaxParamColumn = { field: string; title: string; param: CoaxParam };
export const COAX_PARAMS: CoaxParamColumn[] = [
  { field: "Волновое сопротивление, Ом", title: "Волновое сопротивление, Ом", param: "impedance_ohm" },
  { field: "Погонная ёмкость, пФ/м", title: "Погонная ёмкость, пФ/м", param: "capacitance_pf_m" },
  { field: "Радиус изгиба неподвижно, мм", title: "Радиус изгиба неподвижно, мм", param: "bend_static_mm" },
  { field: "Радиус изгиба многократно, мм", title: "Радиус изгиба многократно, мм", param: "bend_dynamic_mm" },
  { field: "Напряжение, В", title: "Напряжение макс., В", param: "voltage_max_v" },
  { field: "Масса, г/м", title: "Масса, г/м", param: "weight_g_m" },
];
// A new row starts in the list it was added from.
const NEW_TYPE: Record<CableKind, Record<string, string>> = {
  coax: { [TYPE]: "коаксиальный", "Число жил": "1" },
  wire: { [TYPE]: "монтажный", "Число жил": "1" },
  multi: { [TYPE]: "многожильный" },
  lan: { [TYPE]: "витая пара (LAN)" },
};
/** «Частота до, ГГц» of a coax cable (owner 25.10.2026: one frequency field, as at the connectors): its БД.ПРОВ cell,
    else the rated maximum operating frequency of its own card (`from` says so); a family card's («как у») is no value
    here, as in the VSWR norm (backend `coax_vswr.cable_max_ghz`). Never the cut-off or the VNA span. */
export const MAX_GHZ = "Частота до, ГГц";
export function coaxMaxGhz(row: CableRow): { text: string; from?: string } | null {
  const own = (row.record?.fields[MAX_GHZ] ?? "").trim();
  if (own) return { text: own };
  const value = row.card_exact ? row.card?.max_operating_ghz : null;
  return value !== null && value !== undefined && row.card ? { text: num(value), from: `рабочая частота по карточке ${row.card.name}` } : null;
}
/** A coax parameter of a row: its БД.ПРОВ cell, else the card's value — `from` says whose (a family card's is «как у»). */
export function coaxParam(row: CableRow, item: CoaxParamColumn): { text: string; from?: string; family?: boolean } | null {
  const own = (row.record?.fields[item.field] ?? "").trim();
  if (own) return { text: own };
  const value = row.card?.params[item.param];
  if (!row.card || value === undefined || value === null) return null;
  const note = row.card.param_notes[item.param];
  return { text: num(value), family: !row.card_exact,
    from: [row.card_exact ? `по карточке ${row.card.name}` : `как у ${row.card.name}`, note].filter(Boolean).join(" · ") };
}
const NUMBER = /^-?\d[\d\s]*([.,]\d+)?$/;
const cableKey = (value: string) => String(value || "").toLowerCase().replace(/[\s-]/g, "");
const diaText = (dia: CableDiameters) => DIAMETERS.map((name) => dia[name] || "—").join(" · ");
// The mark is not written from the table: it renames the cable (its D-row and links follow it) and its cell opens the
// record's window; the category is a choice in its cell.
const NOT_IN_CELL = new Set([MARK]);

type Draft = { fields: Record<string, string>; dia: Record<Diameter, string>; kind: CableKind };

/** An existing row edits its own cells (the server refuses a field the record has not); a new one — the БД.ПРОВ header. */
function draftOf(row: CableRow | null, columns: string[], kind: CableKind): Draft {
  return {
    fields: row ? Object.fromEntries(Object.entries(row.record?.fields ?? {}).filter(([name]) => columns.includes(name))) : Object.fromEntries(columns.map((name) => [name, NEW_TYPE[kind][name] ?? ""])),
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
  const cells = (row: CableRow): Record<string, string> => ({ "Марка": row.cable, ...(row.record?.fields ?? {}) });
  // «Многожильные» (owner 15.10.2026): groups by mark (ПВВС, КДВВГ, КДВЭВГ, F/UTP Cat5e…; each mark its own group), cores and section
  // as numbers inside.
  const rows = useMemo(() => {
    const own = data.rows.filter((row) => row.kind === kind);
    // Owner 25.10.2026: «размер, затем наименование» везде; the groups keep their order, the rows inside a group follow it.
    const fieldOf = (row: CableRow, name: string) => row.record?.fields[name] ?? "";
    if (kind === "multi") return own.sort((a, b) => compareFamilyGroups(cells(a), cells(b))
      || compareNumbers(firstNumber(fieldOf(a, "Сечение, мм2")), firstNumber(fieldOf(b, "Сечение, мм2")))
      || compareNumbers(firstNumber(fieldOf(a, "Число жил")), firstNumber(fieldOf(b, "Число жил")))
      || compareNames(a.cable, b.cable));
    if (kind === "lan") {
      const category = (row: CableRow) => ethernetCategoryRank(fieldOf(row, CATEGORY_CELL)) ?? ethernetCategoryRank(row.cable) ?? ethernetCategoryRank(fieldOf(row, "Тип провода"));
      return own.sort((a, b) => compareFamilyGroups(cells(a), cells(b))
        || compareNumbers(category(a), category(b))
        || compareNumbers(firstNumber(fieldOf(a, "Число пар")), firstNumber(fieldOf(b, "Число пар")))
        || compareNames(a.cable, b.cable));
    }
    if (kind === "coax") {
      const groups = compareCoaxGroups(own);
      return own.sort((a, b) => groups(a, b)
        || compareNumbers(firstNumber(fieldOf(a, "Наружный диаметр, мм")), firstNumber(fieldOf(b, "Наружный диаметр, мм")))
        || compareNames(a.cable, b.cable));
    }
    return own;
  }, [data.rows, kind]);
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
  // The list's own columns only (owner 20.10.2026): another list's are neither shown nor offered in «Колонки».
  const { columns: kindCols, own, removed } = kindColumns(data, kind);
  // «Категория» — the editor's choice list in the cell: empty — by the rule (named per row), else one of the categories.
  const categoryEdit: DataCellEdit<CableRow> | undefined = canEdit ? {
    ...(own.includes(CATEGORY_CELL) ? ownCellEdit<CableRow>(CATEGORY_CELL, (row) => row.record) : fieldCellEdit<CableRow>(CATEGORY_CELL, (row) => row.record)),
    options: (row) => [
      { value: "", label: `по правилу: ${cableCategory({ ...cells(row), [CATEGORY_CELL]: "" }).category}` },
      ...CATEGORIES.map((name) => ({ value: name, label: name })),
    ],
  } : undefined;
  const textColumn = (name: string): DataColumn<CableRow> => {
    const values = rows.map((row) => field(row, name)).filter((value) => value && value !== "—");
    const long = values.some((value) => value.length > 28);
    return {
      key: name, title: name, value: (row) => field(row, name),
      // ETHERNET: «Категория» — Cat5e / Cat6…, a cell like any other, not the shop category of the многожильные
      edit: name === CATEGORY_CELL && kind !== "lan" ? categoryEdit : canEdit && own.includes(name) ? ownCellEdit<CableRow>(name, (row) => row.record) : fieldEdit(name),
      remove: own.includes(name) ? removeColumn?.(name) : undefined,
      render: long ? (row) => <span className="cable-clip" title={field(row, name)}>{field(row, name)}</span> : undefined,
      numeric: values.length > 0 && values.every((value) => NUMBER.test(value)),
    };
  };
  // The card's value stands in for an empty БД.ПРОВ cell; the cell edit still writes (and starts from) the БД.ПРОВ value.
  const paramColumn = (item: CoaxParamColumn): DataColumn<CableRow> => {
    const base = textColumn(item.field);
    return {
      ...base, title: item.title, numeric: true,
      value: (row) => coaxParam(row, item)?.text ?? "",
      render: (row) => {
        const shown = coaxParam(row, item);
        if (!shown) return <span className="cell-empty">—</span>;
        return shown.from ? <span title={shown.from}>{shown.text}</span> : shown.text; // one colour whoever gives it (owner 19.10)
      },
      edit: base.edit ? { ...base.edit, value: (row) => field(row, item.field) } : undefined,
    };
  };
  const linkColumn = (name: string): DataColumn<CableRow> => ({
    ...textColumn(name),
    render: (row) => {
      const href = field(row, name).trim();
      if (!href) return <span className="cell-empty">—</span>;
      return /^https?:\/\//i.test(href)
        ? <a href={href} target="_blank" rel="noreferrer" title={href}
          aria-label={name === DATASHEET ? `Открыть datasheet ${row.cable}` : `Открыть страницу ${row.cable} у поставщика`}
          onClick={(event) => event.stopPropagation()}>открыть</a>
        : <span className="cable-clip" title={href}>{href}</span>;
    },
  });
  // «Частота до, ГГц»: the cell, else the card's rated frequency (as the VSWR norm reads it); the cell edit writes the cell.
  const maxGhzColumn = (): DataColumn<CableRow> => {
    const base = textColumn(MAX_GHZ);
    return {
      ...base, title: MAX_GHZ, numeric: true, hint: "Рабочая частота кабеля до, ГГц — по ней и полоса нормы КСВН; пусто — по карточке кабеля",
      value: (row) => coaxMaxGhz(row)?.text ?? "",
      render: (row) => {
        const shown = coaxMaxGhz(row);
        if (!shown) return <span className="cell-empty">—</span>;
        return shown.from ? <span title={shown.from}>{shown.text}</span> : shown.text;
      },
      // a cable row without the cell yet (the fill script has not run) takes it too: a base column of the list
      edit: base.edit ? { ...base.edit, enabled: (row) => Boolean(row.record), value: (row) => field(row, MAX_GHZ) } : undefined,
    };
  };
  // A coax parameter is drawn even when the list has no such БД.ПРОВ column yet: the card gives its value.
  const params: Record<string, DataColumn<CableRow>> = kind === "coax"
    ? { ...Object.fromEntries(COAX_PARAMS.map((item) => [item.field, paramColumn(item)])), [MAX_GHZ]: maxGhzColumn() } : {};
  const fieldNames = [...kindCols.filter((name) => name !== MARK), ...Object.keys(params).filter((name) => !kindCols.includes(name))];
  const allColumns: DataColumn<CableRow>[] = [
    { key: "cable", title: kind === "wire" ? "Марка провода" : "Марка кабеля", value: (row) => row.cable, render: (row) => <strong>{row.cable}</strong> },
    ...(kind === "coax" ? DIAMETERS.map((name): DataColumn<CableRow> => ({
      key: name, title: DIA_TITLES[name], numeric: true, value: (row) => row.dia?.[name] ?? "", edit: diaEdit(name),
      render: (row) => row.dia
        ? row.dia[name] || <span className="cell-empty">—</span>
        : <span className="cell-empty" title={`Нет D1–D3 — генератор возьмёт диаметры из записи разъёма${row.dia_hint ? `; похожий кабель: ${row.dia_hint.cable}` : ""}`}>—</span>,
    })) : []),
    // The datasheet — the maker's document: «открыть», as the supplier's page.
    ...fieldNames.map((name) => name === LINK || name === DATASHEET ? linkColumn(name) : params[name] ?? textColumn(name)),
    // The card's cut-off and VNA span, «Карточка», «Вкладка», «Почему здесь» are no columns since 25.10.2026: the cut-off
    // and the span — the cable's card (measurement settings), the list a row is in — the row window's «Вкладка».
  ];
  // Group order and the list's columns; a column the owner just added (not in the order) is shown.
  const columns = arrangeCableColumns(kind, allColumns, (key) => own.includes(key) && !knownCableColumn(key));
  const [openId, setOpenId] = useState("");
  const opened = kind === "coax" ? rows.find((row) => row.id === openId) ?? null : null;

  const detail = (row: CableRow) => <>
    {canEdit
      ? <CableEditor key={`${row.id}:${JSON.stringify(row.record?.fields ?? {})}:${row.dia ? diaText(row.dia) : ""}:${row.kind}`}
        row={row} kind={kind} data={data} onSaved={onSaved} onError={onError} />
      : <CableCard row={row} columns={cardColumns(kindCols, removed)} />}
  </>;

  return <>
    {opened && <CoaxCableWindow key={opened.id} row={opened} kind={kind} data={data} canEdit={canEdit} onSaved={onSaved} onError={onError} close={() => setOpenId("")} />}
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
      // A saved layout keeps its order and hidden set: the columns of 25.10 (nothing hidden) start from new keys —
      // personal and shared — so a layout saved before does not hide one of them (prod had no shared set on 25.10).
      storageKey={`db:cables:${kind}:2510`}
      sharedKey={`prov:${kind}:2510`}
      exportName={CABLE_KIND_LABELS[kind]}
      renderDetail={kind === "coax" ? undefined : detail}
      onRowClick={kind === "coax" ? (row) => setOpenId(row.id) : undefined}
      // Groups open folded (owner 26.10.2026: «всё свёрнуто… при необходимости будем открывать нужное»).
      collapsibleGroups={kind === "coax" || kind === "multi" || kind === "lan"}
      foldedByDefault
      groupBy={kind === "multi" || kind === "lan" ? (row) => cableFamily(cells(row)).family : kind === "coax" ? coaxGroup : undefined}
      // Each group sorts its own rows (owner 16.10: «у каждой группы своя»); every row of the list at once, no page.
      // A group is its own block with a heading and a table (owner 25.10.2026: «визуал БД один», as «Оболочки»).
      groupSort
      blocks
      pageSize={Infinity}
      presets={kind === "coax" ? [{ label: "Без D1–D3", test: (row) => !row.dia }, { label: "Без карточки", test: (row) => !row.card }]
        : rows.some((row) => !field(row, "Наружный диаметр, мм").trim()) ? [{ label: "Без Ø", test: (row) => !field(row, "Наружный диаметр, мм").trim() }] : undefined}
      emptyText={kind === "coax" ? "Коаксиальных кабелей нет — загрузите зеркало БД.ПРОВ и СПР.КАБ или добавьте запись." : "Записей нет."}
      toolbar={<><SortHint text={CABLE_SORT_HINT[kind]} />{toolbar}{canEdit && !adding && <button type="button" className="secondary small" onClick={() => setAdding(true)}>+ Запись</button>}</>}
    />
  </>;
}

const dash = <span className="cell-empty">—</span>;
const isoDate = (text: string) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(text.trim()); return m ? `${m[3]}.${m[2]}.${m[1]}` : text.trim(); };
const withUnit = (value: string, unit: string) => value ? `${value} ${unit}` : "";
const asNumber = (text: string) => text ? Number(text.replace(",", ".")) : null;

/** Attenuation at the VNA markers' frequencies from the card's points: «0,55 на 433 МГц · 1,65 на 2,45 ГГц (дБ/м)». */
function lossAtMarkers(card: CoaxCard | null | undefined): string {
  if (!card) return "";
  const parts: string[] = [];
  for (const marker of card.markers) {
    if (marker.peak || marker.freq_mhz === null) continue;
    const point = card.attenuation.filter((item) => item.freq_mhz === marker.freq_mhz).sort((a, b) => Number(a.interpolated) - Number(b.interpolated))[0];
    if (point) parts.push(`${point.interpolated ? "≈ " : ""}${num(Math.round(point.loss_db_m * 100) / 100)} на ${freq(marker.freq_mhz)}`);
  }
  return parts.length ? `${parts.join(" · ")} (дБ/м)` : "";
}

/** The cable's own data in sections (owner 19.10.2026: «нормальная карточка, открывающаяся, с нужными данными»), then the
    VNA markers and the compatible connectors; «Править» swaps in the record's editor. */
function CoaxCableWindow({ row, kind, data, canEdit, onSaved, onError, close }: {
  row: CableRow; kind: CableKind; data: CablesResult; canEdit: boolean;
  onSaved: (message: string) => Promise<void> | void; onError: (message: string) => void; close: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const f = (name: string) => (row.record?.fields[name] ?? "").trim();
  const card = row.card;
  const param = (field: string) => { const item = COAX_PARAMS.find((entry) => entry.field === field)!; return coaxParam(row, item)?.text ?? ""; };
  const layer = (...parts: string[]) => parts.filter(Boolean).join(" · ");
  const temp = f("Темп. от, °C") || f("Темп. до, °C")
    ? range(asNumber(f("Темп. от, °C")), asNumber(f("Темп. до, °C")))
    : range(card?.params.temp_min_c ?? null, card?.params.temp_max_c ?? null);
  const price = f("Цена поставщика, ₽/м");
  const link = f(LINK);
  const sections: Array<{ title: string; items: Array<[string, ReactNode]> }> = [
    { title: "Конструкция", items: [
      ["Жила (D1)", layer(f("Материал жилы"), f("Жила: проволок × диаметр, мм"), withUnit(row.dia?.D1 ?? "", "мм"))],
      ["Диэлектрик (D2)", layer(f("Материал диэлектрика"), withUnit(row.dia?.D2 ?? "", "мм"))],
      ["Экран (D3)", layer(f("Материал экрана"), withUnit(row.dia?.D3 ?? "", "мм"))],
      ["Оболочка (наружный)", layer(f("Материал оболочки"), withUnit(f("Наружный диаметр, мм"), "мм"))],
    ] },
    { title: "Электрические", items: [
      ["Волновое сопротивление", withUnit(param("Волновое сопротивление, Ом"), "Ом")],
      ["Погонная ёмкость", withUnit(param("Погонная ёмкость, пФ/м"), "пФ/м")],
      ["Частота до", coaxMaxGhz(row) ? `${coaxMaxGhz(row)!.text} ГГц` : ""],
      // measurement settings of the card (owner 25.10.2026: only here, not in the table)
      ["Диапазон VNA", card ? spanText(card) : ""],
      ["Частота среза", card?.fc_ghz != null ? `≈ ${num(card.fc_ghz)} ГГц` : ""],
      ["Напряжение", withUnit(param("Напряжение, В"), "В")],
      ["Затухание", lossAtMarkers(card)],
    ] },
    { title: "Механика и условия", items: [
      ["Радиус изгиба неподвижно", withUnit(param("Радиус изгиба неподвижно, мм"), "мм")],
      ["Радиус изгиба многократно", withUnit(param("Радиус изгиба многократно, мм"), "мм")],
      ["Масса", withUnit(param("Масса, г/м"), "г/м")],
      ["Температура", temp ? `${temp} °C` : ""],
    ] },
  ];
  const head: Array<[string, ReactNode]> = [
    ["Тип / семейство", card?.family || f(TYPE)],
    ["Поставщик", f("Поставщик")],
    ["Артикул поставщика", f("Артикул поставщика")],
    ["Ссылка", /^https?:\/\//i.test(link)
      ? <a href={link} target="_blank" rel="noreferrer" title={link} aria-label={`Открыть страницу ${row.cable} у поставщика`}>открыть</a> : link],
    ["Цена", price ? `${price} ₽/м${f("Дата цены") ? ` от ${isoDate(f("Дата цены"))}` : ""}` : ""],
    ["Стандарт", f(STANDARD)],
    ["Производители", f(MAKERS)],
    ["Datasheet", /^https?:\/\//i.test(f(DATASHEET))
      ? <a href={f(DATASHEET)} target="_blank" rel="noreferrer" title={f(DATASHEET)} aria-label={`Открыть datasheet ${row.cable}`}>открыть</a> : f(DATASHEET)],
    // no table column since 25.10.2026 — the card keeps it (provenance)
    ["Источник данных", f("Источник данных")],
  ];
  const grid = (items: Array<[string, ReactNode]>, label?: string) => <dl className="cable-sheet-grid" aria-label={label}>
    {items.map(([label, value]) => <div key={label} className={label === "Затухание" ? "wide" : undefined}><dt>{label}</dt><dd>{value || dash}</dd></div>)}
  </dl>;
  return <PageWindow label={`Карточка кабеля ${row.cable}`} width={900} dismissable={!editing} close={close}><div className="panel cable-sheet">
    <div className="cable-sheet-head">
      <h2>{row.cable}</h2>
      <div className="cable-sheet-actions">
        {canEdit && !editing && <button type="button" className="primary small" onClick={() => setEditing(true)}>Править</button>}
        <button type="button" className="secondary small" onClick={close} aria-label="Закрыть карточку">Закрыть</button>
      </div>
    </div>
    {editing
      ? <CableEditor key={`${row.id}:${JSON.stringify(row.record?.fields ?? {})}:${row.dia ? diaText(row.dia) : ""}:${row.kind}`}
        row={row} kind={kind} data={data} onCancel={() => setEditing(false)} onError={onError}
        onSaved={async (message) => { if (message) setEditing(false); await onSaved(message); }} />
      : <>
        {grid(head, "Основное")}
        <CoaxNames row={row} />
        {sections.map((section) => <section key={section.title} className="cable-sheet-sec" aria-label={section.title}>
          <h3>{section.title}</h3>{grid(section.items)}
        </section>)}
        {card
          ? <CoaxCardView compact card={card} exact={Boolean(row.card_exact)} aside={<CompatibleConnectors row={row} />} />
          : <div className="coax-card"><CompatibleConnectors row={row} /></div>}
      </>}
  </div></PageWindow>;
}

/** Other names of the very cable, above its card; nothing when they would only repeat the row. */
function CoaxNames({ row }: { row: CableRow }) {
  const names = coaxOtherNames(row);
  if (!names.length) return null;
  return <div className="reference-fields cable-card">
    <span><small>Другие названия</small>{names.join(" · ")}</span>
  </div>;
}

type ConnectorGroup = { series: string; type: string; mount: string; supplier: string; count: number; articles: string[]; prices: number[]; texts: string[] };
const priceOf = (text: string) => {
  const value = Number(text.replace(/\s/g, "").replace(",", "."));
  return text.trim() && Number.isFinite(value) ? value : null;
};
const priceText = (group: ConnectorGroup) => {
  if (group.prices.length) {
    const low = Math.min(...group.prices);
    const high = Math.max(...group.prices);
    return low === high ? num(low) : `${num(low)}–${num(high)}`;
  }
  return group.texts[0] ?? "";
};

/** «Совместимые разъёмы» (owner 19.10.2026: «они сами должны подтягиваться — из базы коаксиальных разъёмов»): the
    БД.КОАКС rows made for this cable, sent with the row, folded by series → type → mount → supplier with a count. */
export function CompatibleConnectors({ row }: { row: CableRow }) {
  const groups = useMemo(() => {
    const found = new Map<string, ConnectorGroup>();
    for (const item of row.connectors ?? []) {
      const fields = item.fields;
      const group: Omit<ConnectorGroup, "count" | "articles" | "prices" | "texts"> = {
        series: coaxSeries(fields), type: coaxType(fields) || "—", mount: (fields["Монтаж"] ?? "").trim(), supplier: coaxSupplier(fields),
      };
      const key = [group.series, group.type, group.mount, group.supplier].join("\u0001");
      const entry = found.get(key) ?? { ...group, count: 0, articles: [], prices: [], texts: [] };
      entry.count += 1;
      const article = coaxArticle(fields);
      if (article) entry.articles.push(article);
      const price = (fields["Цена, ₽"] ?? "").trim();
      const value = priceOf(price);
      if (value !== null) entry.prices.push(value);
      else if (price) entry.texts.push(price);
      found.set(key, entry);
    }
    return [...found.values()].sort((a, b) => seriesRank(a.series) - seriesRank(b.series) || a.series.localeCompare(b.series, "ru")
      // as on «Коаксиальные разъёмы»: the usual polarity before RP-, then the type
      || Number(/^RP[-\s]?/i.test(a.type)) - Number(/^RP[-\s]?/i.test(b.type))
      || a.type.localeCompare(b.type, "ru") || a.mount.localeCompare(b.mount, "ru") || a.supplier.localeCompare(b.supplier, "ru"));
  }, [row.connectors]);
  const total = row.connectors?.length ?? 0;
  return <table className="coax-mini coax-connectors" aria-label="Совместимые разъёмы">
    <caption>Совместимые разъёмы{total > 0 && <small>из БД.КОАКС{row.connectors_family ? ` · как у ${row.connectors_family}` : ""} · {total}</small>}</caption>
    {groups.length > 0 && <thead><tr><th>Серия</th><th>Тип</th><th>Монтаж</th><th>Поставщик</th><th className="num">Цена, ₽</th><th className="num" title="Разъёмов этого типа в БД.КОАКС">Шт.</th></tr></thead>}
    <tbody>{groups.length === 0
      ? <tr><td colSpan={6} className="muted">Разъёмов в БД.КОАКС нет</td></tr>
      : groups.map((group, index) => <tr key={`${group.series}|${group.type}|${group.mount}|${group.supplier}`}>
        <td>{index === 0 || groups[index - 1].series !== group.series ? <strong>{group.series}</strong> : ""}</td>
        <td className="wrap" title={group.articles.length ? group.articles.join(", ") : undefined}>{group.type}</td>
        <td>{group.mount || <span className="muted">—</span>}</td>
        <td>{group.supplier || <span className="muted">—</span>}</td>
        <td className="num">{priceText(group) || <span className="muted">—</span>}</td>
        <td className="num">{group.count}</td>
      </tr>)}</tbody>
  </table>;
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
  // An existing row also edits the cells removed from its list 25.10.2026 (no table column, the value stays its own).
  const listCols = kindColumns(data, row?.kind ?? kind);
  const kindCols = row ? cardColumns(listCols.columns, listCols.removed) : listCols.columns;
  const [draft, setDraft] = useState<Draft>(() => draftOf(row, kindCols, kind));
  const makerNames = useMemo(() => makerSuggestions(data.rows.map((item) => item.record?.fields[MAKERS] ?? "")), [data.rows]);
  const [busy, setBusy] = useState(false);
  const isCoax = row ? row.kind === "coax" || Boolean(row.dia) : kind === "coax";
  const diaOnly = Boolean(row && !row.record);
  const original = draftOf(row, kindCols, kind);
  const hint = row?.dia_hint ?? null;

  async function save() {
    if (busy) return;
    if (row?.record && draft.kind !== row.kind && !window.confirm(moveText(row.cable, draft.kind))) return;
    setBusy(true);
    const changedDia = DIAMETERS.some((name) => draft.dia[name].trim() !== original.dia[name].trim());
    let created: TechOperationRecord | null = null;
    try {
      let record = row?.record ?? null;
      if (!row) {
        record = created = await referencesApi.createRecord("prov", draft.fields, kind);
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
        ? draft.kind !== row.kind ? `«${row.cable}» перенесён во вкладку «${CABLE_KIND_LABELS[draft.kind]}», заполненные значения сохранены` : `«${row.cable}» сохранён`
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
