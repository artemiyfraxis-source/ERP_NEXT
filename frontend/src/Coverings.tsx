import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { DataTable, type CellValue, type DataCellEdit, type DataColumn, type DataPreset } from "./DataTable";
import { AddColumnButton, fieldCellEdit, filledIn, ownCellEdit, useColumnRemoval } from "./CustomColumn";
import { PageWindow } from "./PageWindow";
import { fieldTitle } from "./referenceLabels";
import { SortHint } from "./refSort";
import { failureText, referencesApi, type CoveringGroup, type CoveringRow, type CoveringsResult, type CoveringTab, type ShrinkTemperature, type TechOperationRecord, type TechOperationResult } from "./api";

/* Independent «Термоусадка» and «Оболочки» storage catalogs share the block layout and record editor.
   The server owns routing; unresolved rows remain visible in their current catalog. Parsed values are display-only
   unless backed by an editable field, and removing a list column keeps the full payload in the record card. */

const CHOICES: Array<{ value: string; label: string }> = [
  { value: "", label: "Авто" }, { value: "ТУТ", label: "ТУТ" }, { value: "ТТК", label: "ТТК" }, { value: "Кембрик", label: "Кембрик" },
  { value: "Гофра", label: "Гофра" }, { value: "Оплётки", label: "Оплётки" }, { value: "Ленты", label: "Ленты" }, { value: "Прочее", label: "Прочее" },
];
const CHOICE_OF: Record<CoveringGroup, string> = {
  tut: "ТУТ", ttk: "ТТК", cambric: "Кембрик", corrugated: "Гофра", braid: "Оплётки", tape: "Ленты", other: "Прочее",
};

const mm = (value: number | null) => value === null ? "" : String(value).replace(".", ",");
const signed = (value: number) => value > 0 ? `+${value}` : String(value).replace("-", "−");
export const tempText = (low: number | null, high: number | null) =>
  low === null && high === null ? "" : `${low === null ? "…" : signed(low)} … ${high === null ? "…" : signed(high)}`;
export const rangeText = (low: number | null, high: number | null) =>
  low === null || high === null ? "" : `${mm(low)}–${mm(high)}`;
const dash = <span className="muted">—</span>;
const orDash = (text: string): ReactNode => text ? text : dash;
const clip = (text: string) => text ? <span className="cable-clip" title={text}>{text}</span> : dash;

const SHRINK_FIELDS = ["Температура усадки от, °C", "Температура усадки до, °C", "Вид температуры усадки",
  "Температура усадки по характеристикам, °C", "Источник температуры усадки", "Документ температуры усадки",
  "Страница температуры усадки", "Цитата температуры усадки"];
const SHRINK_LABELS = ["От, °C", "До, °C", "Вид значения", "По характеристикам, °C", "Источник (ссылка)", "Документ", "Страница", "Точная цитата"];
const SHRINK_KINDS = ["рекомендуемая", "диапазон", "минимальная", "оптимальная"];
export function shrinkTemperatureText(value?: ShrinkTemperature): string {
  if (value?.needs_review) {
    const low = value.raw_fields?.[SHRINK_FIELDS[0]] ?? "";
    const high = value.raw_fields?.[SHRINK_FIELDS[1]] ?? "";
    return low || high ? low === high ? low : `${low}…${high}` : "";
  }
  if (!value || (value.lower_c === null && value.upper_c === null)) return "";
  if (value.lower_c === null) return `≤${mm(value.upper_c)}`;
  if (value.upper_c === null) return value.kind ? `≥${mm(value.lower_c)}` : `${mm(value.lower_c)}…`;
  return value.lower_c === value.upper_c ? mm(value.lower_c) : `${mm(value.lower_c)}…${mm(value.upper_c)}`;
}
function shrinkTemperatureHint(value?: ShrinkTemperature): string {
  if (!value) return "";
  if (value.needs_review) return ["Проверить", value.validation_error,
    ...Object.entries(value.raw_fields ?? {}).map(([field, text]) => `${field}: ${text}`)].filter(Boolean).join("\n");
  return [value.kind, value.characteristic_c !== null ? `По характеристикам: ${mm(value.characteristic_c)} °C` : "",
    value.document, value.page ? `Страница: ${value.page}` : "", value.quote, value.source_url].filter(Boolean).join("\n");
}
function ShrinkTemperatureDetails({ value }: { value?: ShrinkTemperature }) {
  if (value?.needs_review) return <div className="reference-fields cable-card">
    <p className="tone-warn">Проверить: {value.validation_error}</p>
    {Object.entries(value.raw_fields ?? {}).filter(([, text]) => text).map(([field, text]) =>
      <span key={field} style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}><small>{field}</small>{text}</span>)}
  </div>;
  if (!value || !shrinkTemperatureText(value)) return <p className="muted">Температура усадки не задана</p>;
  return <div className="reference-fields cable-card">
    <span><small>Температура усадки, °C · {value.kind}</small>{shrinkTemperatureText(value)}</span>
    {value.characteristic_c !== null && <span><small>По характеристикам, °C</small>{mm(value.characteristic_c)}</span>}
    {(value.document || value.page) && <span><small>Документ</small>{[value.document, value.page ? `стр. ${value.page}` : ""].filter(Boolean).join(" · ")}</span>}
    {value.quote && <span style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}><small>Точная цитата</small>{value.quote}</span>}
    {/^https?:\/\//i.test(value.source_url) && <a href={value.source_url} target="_blank" rel="noreferrer">Открыть источник температуры усадки</a>}
  </div>;
}

type RawEdit = (field: string) => DataCellEdit<CoveringRow> | undefined;

/** Все колонки оболочек — одна таблица в блоках (владелец 25.10.2026: «визуал БД один»); у каждого раздела — свои из них
    (`GROUP_KEYS`), в одном порядке для всех: размеры раздела, серия, материал, температура, цвет, производитель, ссылка. */
function coveringColumns(raw: RawEdit, tab: CoveringTab = "cov"): DataColumn<CoveringRow>[] {
  const number = (key: string, title: string, pick: (row: CoveringRow) => number | null, field?: string): DataColumn<CoveringRow> =>
    ({ key, title, numeric: true, value: (row) => pick(row), render: (row) => pick(row) === null ? dash : mm(pick(row)), edit: field ? raw(field) : undefined });
  return [
    {
      key: "name", title: "Наименование",
      value: (row) => row.name,
      render: (row) => <><strong>{row.name}</strong>{row.group_source === "вручную" && <small className="muted" title="Раздел задан вручную"> · вручную</small>}{row.classification_status === "unknown" && <small className="tone-warn"> · Требует уточнения раздела</small>}</>,
    },
    number("before", "Ø до усадки, мм", (row) => row.before_mm, BEFORE),
    number("after", "Ø после, мм", (row) => row.after_mm, AFTER),
    { key: "ratio", title: "Усадка", value: (row) => row.ratio, render: (row) => orDash(row.ratio), edit: raw("Коэф. усадки") },
    number("wall", "Стенка, мм", (row) => row.wall_mm),
    { key: "adhesive", title: "Клей", value: (row) => row.adhesive, render: (row) => orDash(row.adhesive) },
    number("dia", "Ø, мм", (row) => row.before_mm, BEFORE),
    number("inner", "Ø внутр., мм", (row) => row.inner_mm, BEFORE),
    number("outer", "Ø наружн., мм", (row) => row.outer_mm, AFTER),
    { key: "split", title: "Разрезная", value: (row) => row.split === null ? "" : row.split ? "да" : "нет", render: (row) => row.split === null ? dash : row.split ? "да" : "нет" },
    number("nominal", "Ø номинал, мм", (row) => row.nominal_mm),
    { key: "range", title: "Диапазон Ø, мм", value: (row) => row.min_mm, numeric: true, render: (row) => orDash(rangeText(row.min_mm, row.max_mm)) },
    number("width", "Ширина, мм", (row) => row.width_mm ?? null),
    { key: "series", title: "Серия", value: (row) => row.series, render: (row) => clip(row.series), edit: raw("Серия") },
    {
      key: "material", title: "Материал", value: (row) => row.material, edit: raw("Материал"),
      render: (row) => row.material ? <span className="cable-clip" title={row.material}>{row.material_short && !row.material.toLocaleLowerCase("ru-RU").startsWith(row.material_short.toLocaleLowerCase("ru-RU")) ? `${row.material_short} · ${row.material}` : row.material}</span> : dash,
    },
    { key: "density", title: "Плотность", value: (row) => row.density, render: (row) => orDash(row.density) },
    { key: "temp", title: tab === "heat_shrink" ? "Рабочая температура, °C" : "Температура, °C", value: (row) => tempText(row.temp_min_c, row.temp_max_c), render: (row) => orDash(tempText(row.temp_min_c, row.temp_max_c)) },
    { key: "shrinkTemp", title: "Температура усадки, °C", numeric: true, value: (row) => shrinkTemperatureText(row.shrink_temperature),
      render: (row) => row.shrink_temperature?.needs_review || shrinkTemperatureText(row.shrink_temperature)
        ? <span title={shrinkTemperatureHint(row.shrink_temperature)}>{shrinkTemperatureText(row.shrink_temperature)}
          {row.shrink_temperature?.needs_review && <small className="tone-warn">{shrinkTemperatureText(row.shrink_temperature) ? " · " : ""}<span>Проверить</span></small>}</span> : dash },
    { key: "color", title: "Цвет", value: (row) => row.color, render: (row) => orDash(row.color), edit: raw("Цвет") },
    { key: "maker", title: "Производитель", value: (row) => row.maker, render: (row) => clip(row.maker), edit: raw("Производитель") },
    {
      key: "datasheet", title: "Ссылка", value: (row) => row.datasheet, edit: raw("Ссылка на datasheet"),
      render: (row) => row.datasheet ? <a href={row.datasheet} target="_blank" rel="noreferrer" aria-label={`Открыть даташит: ${row.name}`} onClick={(event) => event.stopPropagation()}>открыть</a> : dash,
    },
  ];
}

const COMMON = ["series", "material", "temp", "color", "maker", "datasheet"];
// The fields of each kind; «Стенка» and «Плотность» only where some row of the kind has them.
const GROUP_KEYS: Record<CoveringGroup, string[]> = {
  tut: ["name", "before", "after", "ratio", "wall", ...COMMON],
  ttk: ["name", "before", "after", "ratio", "wall", "adhesive", ...COMMON],
  cambric: ["name", "dia", ...COMMON],
  corrugated: ["name", "inner", "outer", "split", ...COMMON],
  braid: ["name", "nominal", "range", "density", ...COMMON],
  tape: ["name", "width", ...COMMON],
  other: ["name", ...COMMON],
};
const SPARSE: Record<string, (row: CoveringRow) => unknown> = { wall: (row) => row.wall_mm, density: (row) => row.density };
export function groupKeys(group: CoveringGroup, rows: CoveringRow[], tab: CoveringTab = "cov"): string[] {
  return GROUP_KEYS[group].flatMap((key) => key === "temp" && tab === "heat_shrink" ? [key, "shrinkTemp"] : [key])
    .filter((key) => !SPARSE[key] || rows.some((row) => row.group === group && SPARSE[key](row) !== null && SPARSE[key](row) !== ""));
}

const BEFORE = "Размер до, мм";
const AFTER = "Размер после, мм";
const RAW_FIELDS: Record<string, string[]> = {
  name: ["Наименование"], before: [BEFORE], after: [AFTER], ratio: ["Коэф. усадки"], adhesive: ["Клеевой слой"],
  dia: [BEFORE], inner: [BEFORE], outer: [AFTER], nominal: [BEFORE], range: [BEFORE, AFTER], width: [BEFORE],
  series: ["Серия"], material: ["Материал"], temp: ["Темп. от, °C", "Темп. до, °C"],
  shrinkTemp: SHRINK_FIELDS,
  color: ["Цвет"], maker: ["Производитель"], datasheet: ["Ссылка на datasheet"],
};
const COVERING_LABELS: Record<CoveringTab, string> = { heat_shrink: "Термоусадка", cov: "Оболочки" };
const GROUPS_BY_TAB: Record<CoveringTab, CoveringGroup[]> = {
  heat_shrink: ["tut", "ttk", "other"], cov: ["cambric", "corrugated", "braid", "tape", "other"],
};
// These are display columns, not additional stored fields. Their removal must not remove source dimensions.
const DERIVED_KEYS = new Set(["wall", "split", "nominal", "range", "width", "density", "temp", "shrinkTemp"]);
// «Без Ø» — строки, у которых размер не нашёлся ни в колонке, ни в названии (их генератор не подберёт по диаметру).
const sizeOf: Record<CoveringGroup, (row: CoveringRow) => number | null> = {
  tut: (row) => row.before_mm, ttk: (row) => row.before_mm, cambric: (row) => row.before_mm, other: (row) => row.before_mm,
  braid: (row) => row.nominal_mm ?? row.min_mm, corrugated: (row) => row.inner_mm, tape: (row) => row.width_mm ?? null,
};
// Чипы — над всеми разделами; чип без строк не показывается: пустой отбор — шум. «Без размера» — Ø или ширина ленты.
const presetsFor = (rows: CoveringRow[]): DataPreset<CoveringRow>[] => ([
  { label: "Без размера", test: (row) => sizeOf[row.group](row) === null },
  { label: "Без производителя", test: (row) => !row.maker.trim() },
  { label: "Уточнить раздел", test: (row) => row.classification_status === "unknown" },
] as DataPreset<CoveringRow>[]).filter((preset) => rows.some(preset.test));

function GroupChooser({ row, busy, onChange }: { row: CoveringRow; busy: boolean; onChange: (group: string) => void }) {
  const manual = row.group_source === "вручную";
  const current = manual ? CHOICE_OF[row.group] : "";
  return <div className="reference-fields cable-card">
    <label><small>Раздел</small>
      <select aria-label="Раздел оболочек" value={current} disabled={busy} onChange={(event) => onChange(event.target.value)}>
        {CHOICES.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
      </select>
    </label>
    {row.notes.length > 0 && <span><small>Взято из названия</small>{row.notes.join(", ")}</span>}
  </div>;
}

/** Значения строки оболочки для поиска: все поля, кроме служебных (id, раздел). */
function coveringText(row: CoveringRow): CellValue[] {
  const cell = (value: unknown): CellValue => typeof value === "string" || typeof value === "number" || value === null || value === undefined ? value : String(value);
  return Object.entries(row).flatMap(([key, value]) => key === "id" || key === "group" || key === "auto_group" ? []
    : Array.isArray(value) ? value.map(cell) : value !== null && typeof value === "object" ? [] : [cell(value)]);
}

export function CoveringsTable({ tab = "cov", revision = 0, canEdit, onCount, onChanged }: {
  tab?: CoveringTab; revision?: number; canEdit: boolean; onCount?: (count: number) => void; onChanged?: () => void;
}) {
  const label = COVERING_LABELS[tab];
  const [data, setData] = useState<CoveringsResult | null>(null);
  const [catalog, setCatalog] = useState<TechOperationResult>({ metadata: null, records: [] });
  const records = useMemo(() => new Map(catalog.records.map((record) => [record.id, record])), [catalog]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<{ original: TechOperationRecord | null; values: Record<string, string> } | null>(null);
  const [editError, setEditError] = useState("");
  const [discard, setDiscard] = useState(false);
  const [deleting, setDeleting] = useState<TechOperationRecord | null>(null);
  const request = useRef(0);
  const dirty = editing !== null && Object.entries(editing.values).some(([field, value]) => value !== (editing.original?.fields[field] ?? ""));

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  async function load() {
    const current = ++request.current;
    try {
      const [result, raw] = await Promise.all([
        referencesApi.coverings(tab), referencesApi.techOperations(tab, "", 5000),
      ]);
      if (current !== request.current) return;
      setData(result);
      setCatalog(raw);
      onCount?.(result.rows.length);
      setError("");
    } catch (reason) {
      if (current === request.current) setError(failureText(reason, `Не удалось загрузить «${label}»`));
    }
  }
  useEffect(() => { void load(); return () => { request.current += 1; }; }, [tab, revision]); // eslint-disable-line react-hooks/exhaustive-deps

  async function changed() {
    onChanged?.();
    await load();
  }

  const fields = catalog.columns ?? [...new Set(catalog.records.flatMap((record) => Object.keys(record.fields)))];
  const ownColumns = catalog.custom_columns ?? [];
  const removal = useColumnRemoval(tab, canEdit, (name) => {
    const derived = coveringColumns(() => undefined, tab).find((column) => DERIVED_KEYS.has(column.key) && column.title === name);
    return derived ? (data?.rows ?? []).filter((row) => {
      const value = derived.value(row);
      return value !== null && value !== undefined && value !== "";
    }).length : filledIn(catalog.records, name);
  }, changed, true);

  function startEdit(record: TechOperationRecord | null) {
    setEditError("");
    setEditing({ original: record, values: {
      ...Object.fromEntries(["Наименование", ...fields, ...(tab === "heat_shrink" ? SHRINK_FIELDS : [])].map((field) => [field, ""])),
      ...record?.fields,
    } });
  }

  function closeEditor() {
    if (busy) return;
    if (dirty) setDiscard(true); else setEditing(null);
  }

  async function saveRecord() {
    if (!editing || busy) return;
    setBusy(true); setEditError("");
    try {
      const original = editing.original;
      const edits = Object.entries(editing.values).filter(([field, value]) => value !== (original?.fields[field] ?? ""))
        .map(([field, value]) => ({ field, old: original?.fields[field] ?? "", value }));
      const saved = original
        ? edits.length ? await referencesApi.editRecord(original.id, edits) : original
        : await referencesApi.createRecord(tab, Object.fromEntries(Object.entries(editing.values)
          .filter(([field, value]) => !SHRINK_FIELDS.includes(field) || value !== "")));
      setEditing(null);
      setNotice(saved.tab === tab ? "Запись сохранена" : `Запись перенесена в «${COVERING_LABELS[saved.tab as CoveringTab] ?? "Материалы"}»`);
      await changed();
    } catch (reason) {
      setEditError(failureText(reason, "Запись не сохранена"));
    } finally { setBusy(false); }
  }

  async function deleteRecord() {
    if (!deleting || busy) return;
    setBusy(true); setEditError("");
    try {
      await referencesApi.deleteRecord(deleting.id);
      setDeleting(null); setEditing(null); setNotice("Запись удалена");
      await changed();
    } catch (reason) { setEditError(failureText(reason, "Запись не удалена")); }
    finally { setBusy(false); }
  }

  async function move(row: CoveringRow, group: string) {
    if (busy) return;
    setBusy(true);
    try {
      await referencesApi.setCoveringGroup(row.id, group);
      await changed();
    } catch (reason) {
      setError(failureText(reason, "Раздел не сохранён"));
    } finally {
      setBusy(false);
    }
  }

  // The blocks: a kind (ТУТ, ТТК…) in the owner's order, ТУТ/ТТК by the server's sub-kinds (`shrink_kind`) under their
  // kind's heading; inside — the server's order (size, then name). An unknown sub-kind goes last in its kind.
  const order = useMemo(() => {
    if (!data) return { rows: [] as CoveringRow[], block: (_row: CoveringRow) => "" };
    const groups = GROUPS_BY_TAB[tab];
    const kindsOf = (group: CoveringGroup) => (data.kinds?.[group] ?? []).length > 1 ? data.kinds![group]! : [];
    const kindAt = (row: CoveringRow) => { const kinds = kindsOf(row.group); const at = kinds.findIndex((kind) => kind.id === row.kind); return at < 0 ? kinds.length : at; };
    const rows = data.rows.map((row, index) => ({ row, index }))
      .sort((a, b) => groups.indexOf(a.row.group) - groups.indexOf(b.row.group) || kindAt(a.row) - kindAt(b.row) || a.index - b.index)
      .map((item) => item.row);
    const block = (row: CoveringRow) => kindsOf(row.group).length ? `${row.group}:${row.kind ?? ""}` : row.group;
    return { rows, block };
  }, [data, tab]);

  if (error && !data) return <div className="error-banner" role="alert">{error}</div>;
  if (!data) return <div className="empty">Загрузка…</div>;

  const raw: RawEdit = (field) => canEdit ? fieldCellEdit<CoveringRow>(field, (row) => records.get(row.id)) : undefined;
  const relevantKeys = new Set(GROUPS_BY_TAB[tab].flatMap((group) => groupKeys(group, data.rows, tab)));
  const removedColumns = new Set((catalog.removed_columns ?? []).map((title) =>
    tab === "heat_shrink" && title === "Температура, °C" ? "Рабочая температура, °C" : title));
  const parsedColumns = coveringColumns(raw, tab)
    .filter((column) => relevantKeys.has(column.key) && !removedColumns.has(column.title)
      && (!catalog.columns || !RAW_FIELDS[column.key] || RAW_FIELDS[column.key].every((field) => fields.includes(field))))
    .map((column) => ({ ...column, remove: column.key === "name" ? undefined
      : removal.removeOf(DERIVED_KEYS.has(column.key) ? column.title : RAW_FIELDS[column.key][0]) }));
  const parsedFields = new Set([...Object.values(RAW_FIELDS).flat(), "Группа", "Раздел оболочек"]);
  const extraFields = fields.filter((field) => ownColumns.includes(field)
    || (!parsedFields.has(field) && field !== "Источник данных"));
  const extraColumns: DataColumn<CoveringRow>[] = extraFields.map((field) => ({
    key: `field:${field}`, title: fieldTitle(field), value: (row) => records.get(row.id)?.fields[field] ?? "",
    render: (row) => clip(records.get(row.id)?.fields[field] ?? ""),
    hidden: !ownColumns.includes(field),
    edit: canEdit ? ownCellEdit(field, (row: CoveringRow) => records.get(row.id)) : undefined,
    remove: removal.removeOf(field),
  }));
  const sharedColumnAliases: Record<string, string[]> = {};
  for (const column of parsedColumns) {
    for (const field of RAW_FIELDS[column.key] ?? []) (sharedColumnAliases[field] ??= []).push(column.key);
  }
  for (const field of extraFields) sharedColumnAliases[field] = [`field:${field}`];
  const groupOf = (block: string) => block.split(":")[0] as CoveringGroup;
  const kindTitle = (block: string) => {
    const [group, kind] = block.split(":");
    return data.kinds?.[group as CoveringGroup]?.find((item) => item.id === kind)?.title ?? "Другие";
  };
  const editingParsed = data.rows.find((row) => row.id === editing?.original?.id);
  const cardParameters = editingParsed ? coveringColumns(() => undefined, tab).filter((column) => {
    if (!DERIVED_KEYS.has(column.key)) return false;
    const value = column.value(editingParsed);
    return value !== null && value !== undefined && value !== "";
  }) : [];
  return <div className="coverings">
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner" role="status">{notice}</div>}
    <DataTable
      label={label}
      rows={order.rows}
      columns={[...parsedColumns, ...extraColumns]}
      rowKey={(row) => row.id}
      rowLabel={(row) => row.name}
      groupBy={order.block}
      groupLabel={(block) => groupOf(block) === "other" && tab === "heat_shrink" ? "Раздел не определён" : block.includes(":") ? kindTitle(block) : data.titles[groupOf(block)]}
      blocks
      blockSection={(block) => block.includes(":") ? data.titles[groupOf(block)] : undefined}
      blockColumns={(block) => [...groupKeys(groupOf(block), data.rows, tab), ...extraColumns.map((column) => column.key)]}
      presets={presetsFor(data.rows)}
      onEdited={changed}
      storageKey={`db:${tab}:catalog-v1`}
      sharedKey={tab}
      sharedColumnAliases={sharedColumnAliases}
      exportName={label}
      // Поиск — по всем значениям строки (наименование, серия, производитель, материал, цвет, размеры, примечания), по
      // правилам searchText.ts: «тут 3.0» находит «ТУТ 3,0/1,5».
      searchText={(row) => [...coveringText(row), ...Object.values(records.get(row.id)?.fields ?? {})]}
      searchLabel={tab === "heat_shrink" ? "Поиск по термоусадке" : "Поиск по оболочкам"}
      searchPlaceholder="Наименование, серия, производитель, материал"
      renderDetail={canEdit || tab === "heat_shrink" ? (row) => <>
        {tab === "heat_shrink" && <ShrinkTemperatureDetails value={row.shrink_temperature} />}
        {canEdit && <><GroupChooser key={`${row.id}:${row.group_source}`} row={row} busy={busy} onChange={(value) => void move(row, value)} />
          <button type="button" className="secondary small" disabled={busy || !records.has(row.id)} onClick={() => startEdit(records.get(row.id)!)}>Править запись</button></>}
      </> : undefined}
      emptyText="Ничего не найдено"
      toolbar={<><SortHint text={tab === "heat_shrink" ? "вид, размер, затем наименование" : "размер, затем наименование"} />{canEdit && <>
        <AddColumnButton tab={tab} onAdded={changed} />
        <button type="button" className="secondary small" onClick={() => startEdit(null)}>+ Запись</button>
      </>}</>}
    />
    {removal.dialog}
    {editing && <PageWindow label={editing.original ? "Правка записи" : "Новая запись"} width={920} close={closeEditor}>
      <form className="panel db-edit" onSubmit={(event) => { event.preventDefault(); void saveRecord(); }}>
        <div className="manual-stock-head"><h2>{editing.original ? `Правка: ${editing.original.display_text}` : `Новая запись · ${label}`}</h2></div>
        {editError && <div className="error-banner" role="alert">{editError}</div>}
        {editingParsed && cardParameters.length > 0 && <div className="reference-fields">{cardParameters.map((column) => <div key={column.key}>
          <small>{column.title}</small><div>{column.render ? column.render(editingParsed) : String(column.value(editingParsed))}</div>
        </div>)}</div>}
        <div className="reference-fields db-form">{Object.entries(editing.values).filter(([field]) => !SHRINK_FIELDS.includes(field)).map(([field, value]) => <label key={field}>
          <small>{tab === "heat_shrink" && field.startsWith("Темп.") ? field.replace("Темп.", "Рабочая температура") : fieldTitle(field)}</small>
          {field === "Раздел оболочек"
            ? <select name={field} aria-label={`Поле ${field}`} value={value} onChange={(event) => setEditing({ ...editing, values: { ...editing.values, [field]: event.target.value } })}>
              {CHOICES.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
            </select>
            : <input name={field} autoComplete="off" aria-label={`Поле ${field}`} value={value} onChange={(event) => setEditing({ ...editing, values: { ...editing.values, [field]: event.target.value } })} />}
        </label>)}</div>
        {tab === "heat_shrink" && <>
          <h3>Температура усадки, °C</h3>
          <div className="cable-form">{SHRINK_FIELDS.map((field, index) => {
            const value = editing.values[field] ?? "";
            const update = (next: string) => setEditing({ ...editing, values: { ...editing.values, [field]: next } });
            return <label key={field} className={index === 4 ? "wide" : undefined} style={index === 7 ? { gridColumn: "1 / -1" } : undefined}>
              <small>{SHRINK_LABELS[index]}</small>
              {index === 2 ? <select name={field} aria-label={field} value={value} onChange={(event) => update(event.target.value)}>
                {value && !SHRINK_KINDS.includes(value) && <option value={value}>{value} — проверить</option>}
                <option value="">Не задан</option>{SHRINK_KINDS.map((kind) => <option key={kind} value={kind}>{kind}</option>)}
              </select> : index === 7 ? <textarea name={field} aria-label={field} autoComplete="off" rows={3} value={value} onChange={(event) => update(event.target.value)} />
                : <input name={field} aria-label={field} autoComplete="off" type="text"
                  inputMode={index === 4 ? "url" : [0, 1, 3].includes(index) ? "decimal" : undefined} value={value} onChange={(event) => update(event.target.value)} />}
            </label>;
          })}</div>
        </>}
        <div className="form-actions">
          <button type="submit" className="primary" disabled={busy}>{busy ? "Сохраняю…" : "Сохранить запись"}</button>
          <button type="button" className="secondary" disabled={busy} onClick={closeEditor}>Отмена</button>
          {editing.original && <button type="button" className="secondary danger-link" disabled={busy} onClick={() => { setEditError(""); setDeleting(editing.original); }}>Удалить запись</button>}
        </div>
      </form>
    </PageWindow>}
    {discard && <PageWindow label="Несохранённые изменения" width={460} close={() => setDiscard(false)}><div className="panel">
      <h2>Закрыть без сохранения?</h2><p>Изменения в карточке будут потеряны.</p>
      <div className="form-actions"><button type="button" className="primary" onClick={() => { setDiscard(false); setEditing(null); }}>Закрыть без сохранения</button>
        <button type="button" className="secondary" onClick={() => setDiscard(false)}>Продолжить правку</button></div>
    </div></PageWindow>}
    {deleting && <PageWindow label="Удалить запись" width={460} close={() => { if (!busy) setDeleting(null); }}><div className="panel">
      <h2>Удалить запись?</h2><p>{deleting.display_text}</p>
      {editError && <div className="error-banner" role="alert">{editError}</div>}
      <div className="form-actions"><button type="button" className="primary danger" disabled={busy} onClick={() => void deleteRecord()}>Подтвердить удаление</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => setDeleting(null)}>Отмена</button></div>
    </div></PageWindow>}
  </div>;
}
