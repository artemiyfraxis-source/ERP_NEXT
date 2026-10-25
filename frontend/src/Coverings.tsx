import { useEffect, useMemo, useState, type ReactNode } from "react";
import { DataTable, type DataCellEdit, type DataColumn, type DataPreset } from "./DataTable";
import { fieldCellEdit } from "./CustomColumn";
import { searchRows, searchWords } from "./searchText";
import { failureText, referencesApi, type CoveringGroup, type CoveringRow, type CoveringsResult, type TechOperationRecord } from "./api";

/* «База данных» → «Оболочки» (owner 12.10.2026, 15.10.2026): термоусадка без клея и с клеем, кембрик, гофра, оплётки,
   ленты — each its own table with the fields of its kind, in a kind by size as a number, then by name (the server sorts,
   `coverings.py`); the series is a column. «Прочее» shows only when a row is moved there. Nothing is deleted: a row moves between groups through
   the «Раздел» cell, «Авто» returns it to the classifier. Cells that are a БД.МАТ column of the row (производитель, цвет,
   материал, Ø до/после, усадка, ссылка) are edited in place through the record's own edit (old value checked); a value the
   parser took from the name shows in the cell, the editor opens on the column itself (empty — fill it). */

// The owner's order (15.10.2026): термоусадка, кембрик, гофра, оплётки, ленты; «Прочее» — only when not empty.
export const SHOWN: CoveringGroup[] = ["tut", "ttk", "cambric", "corrugated", "braid", "tape"];
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

type RawEdit = (field: string) => DataCellEdit<CoveringRow> | undefined;

function columnsFor(group: CoveringGroup, rows: CoveringRow[], raw: RawEdit): DataColumn<CoveringRow>[] {
  const name: DataColumn<CoveringRow> = {
    key: "name", title: "Наименование",
    value: (row) => row.name,
    render: (row) => <><strong>{row.name}</strong>{row.group_source === "вручную" && <small className="muted" title="Раздел задан вручную"> · вручную</small>}</>,
  };
  const material: DataColumn<CoveringRow> = {
    key: "material", title: "Материал", value: (row) => row.material, edit: raw("Материал"),
    render: (row) => row.material ? <span className="cable-clip" title={row.material}>{row.material_short && !row.material.toLocaleLowerCase("ru-RU").startsWith(row.material_short.toLocaleLowerCase("ru-RU")) ?`${row.material_short} · ${row.material}` : row.material}</span> : dash,
  };
  const temp: DataColumn<CoveringRow> = {
    key: "temp", title: "Температура, °C", value: (row) => tempText(row.temp_min_c, row.temp_max_c), render: (row) => orDash(tempText(row.temp_min_c, row.temp_max_c)),
  };
  const colour: DataColumn<CoveringRow> = { key: "color", title: "Цвет", value: (row) => row.color, render: (row) => orDash(row.color), edit: raw("Цвет") };
  const maker: DataColumn<CoveringRow> = { key: "maker", title: "Производитель", value: (row) => row.maker, render: (row) => clip(row.maker), edit: raw("Производитель") };
  const series: DataColumn<CoveringRow> = { key: "series", title: "Серия", value: (row) => row.series, render: (row) => clip(row.series), edit: raw("Серия") };
  const link: DataColumn<CoveringRow> = {
    key: "datasheet", title: "Ссылка", value: (row) => row.datasheet, edit: raw("Ссылка на datasheet"),
    render: (row) => row.datasheet ? <a href={row.datasheet} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>datasheet</a> : dash,
  };
  const number = (key: string, title: string, pick: (row: CoveringRow) => number | null, field?: string): DataColumn<CoveringRow> =>
    ({ key, title, numeric: true, value: (row) => pick(row), render: (row) => pick(row) === null ? dash : mm(pick(row)), edit: field ? raw(field) : undefined });
  const filled = (pick: (row: CoveringRow) => unknown) => rows.some((row) => pick(row) !== null && pick(row) !== "");
  if (group === "tut" || group === "ttk") return [
    name,
    number("before", "Ø до усадки, мм", (row) => row.before_mm, BEFORE),
    number("after", "Ø после, мм", (row) => row.after_mm, AFTER),
    { key: "ratio", title: "Усадка", value: (row) => row.ratio, render: (row) => orDash(row.ratio), edit: raw("Коэф. усадки") },
    { ...number("wall", "Стенка, мм", (row) => row.wall_mm), hidden: !filled((row) => row.wall_mm) },
    ...(group === "ttk" ? [{ key: "adhesive", title: "Клей", value: (row: CoveringRow) => row.adhesive, render: (row: CoveringRow) => orDash(row.adhesive) }] : []),
    series, material, temp, colour, maker, link,
  ];
  if (group === "cambric") return [name, number("before", "Ø, мм", (row) => row.before_mm, BEFORE), series, material, temp, colour, maker, link];
  if (group === "tape") return [name, number("width", "Ширина, мм", (row) => row.width_mm ?? null), series, material, temp, colour, maker, link];
  if (group === "braid") return [
    name,
    number("nominal", "Ø номинал, мм", (row) => row.nominal_mm),
    { key: "range", title: "Диапазон Ø, мм", value: (row) => row.min_mm, numeric: true, render: (row) => orDash(rangeText(row.min_mm, row.max_mm)) },
    material,
    { key: "density", title: "Плотность", value: (row) => row.density, render: (row) => orDash(row.density), hidden: !filled((row) => row.density) },
    series, temp, colour, maker, link,
  ];
  if (group === "corrugated") return [
    name,
    number("inner", "Ø внутр., мм", (row) => row.inner_mm, BEFORE),
    number("outer", "Ø наружн., мм", (row) => row.outer_mm, AFTER),
    { key: "split", title: "Разрезная", value: (row) => row.split === null ? "" : row.split ? "да" : "нет", render: (row) => row.split === null ? dash : row.split ? "да" : "нет" },
    series, material, temp, colour, maker, link,
  ];
  return [name, series, maker, material, temp, colour, link];
}

const BEFORE = "Размер до, мм";
const AFTER = "Размер после, мм";
// «Без Ø» — строки, у которых размер не нашёлся ни в колонке, ни в названии (их генератор не подберёт по диаметру).
const sizeOf: Record<CoveringGroup, (row: CoveringRow) => number | null> = {
  tut: (row) => row.before_mm, ttk: (row) => row.before_mm, cambric: (row) => row.before_mm, other: (row) => row.before_mm,
  braid: (row) => row.nominal_mm ?? row.min_mm, corrugated: (row) => row.inner_mm, tape: (row) => row.width_mm ?? null,
};
// Чип без строк не показывается: пустой отбор — шум.
const presetsFor = (group: CoveringGroup, rows: CoveringRow[]): DataPreset<CoveringRow>[] => ([
  { label: group === "tape" ? "Без ширины" : "Без Ø", test: (row) => sizeOf[group](row) === null },
  { label: "Без производителя", test: (row) => !row.maker.trim() },
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
function coveringText(row: CoveringRow): unknown[] {
  return Object.entries(row).flatMap(([key, value]) => key === "id" || key === "group" || key === "auto_group" ? []
    : Array.isArray(value) ? value : value !== null && typeof value === "object" ? [] : [value]);
}

export function CoveringsTable({ canEdit, onCount }: { canEdit: boolean; onCount?: (count: number) => void }) {
  const [data, setData] = useState<CoveringsResult | null>(null);
  // The БД.МАТ rows behind the coverings: a cell edit sends the column's old value as the record has it.
  const [records, setRecords] = useState<Map<string, TechOperationRecord>>(new Map());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");

  async function load() {
    try {
      const [result, mat] = await Promise.all([
        referencesApi.coverings(),
        canEdit ? referencesApi.techOperations("mat", "", 5000).then((value) => value.records, () => [] as TechOperationRecord[]) : Promise.resolve([] as TechOperationRecord[]),
      ]);
      setData(result);
      setRecords(new Map(mat.map((record) => [record.id, record])));
      onCount?.(result.rows.length);
      setError("");
    } catch (reason) {
      setError(failureText(reason, "Не удалось загрузить оболочки"));
    }
  }
  useEffect(() => { void load(); }, []);

  async function move(row: CoveringRow, group: string) {
    if (busy) return;
    setBusy(true);
    try {
      await referencesApi.setCoveringGroup(row.id, group);
      await load();
    } catch (reason) {
      setError(failureText(reason, "Раздел не сохранён"));
    } finally {
      setBusy(false);
    }
  }

  const words = searchWords(query);
  // Поиск — по всем значениям строки (наименование, серия, производитель, материал, цвет, размеры, примечания), по
  // правилам searchText.ts: «тут 3.0» находит «ТУТ 3,0/1,5».
  const visible = useMemo(() => searchRows(data?.rows ?? [], coveringText, query), [data, query]);

  if (error && !data) return <div className="error-banner" role="alert">{error}</div>;
  if (!data) return <div className="empty">Загрузка…</div>;

  const raw: RawEdit = (field) => canEdit ? fieldCellEdit<CoveringRow>(field, (row) => records.get(row.id)) : undefined;
  const table = (group: CoveringGroup, rows: CoveringRow[], all: CoveringRow[], label: string, storageKey: string) => <DataTable
    label={label}
    rows={rows}
    columns={columnsFor(group, all, raw)}
    rowKey={(row) => row.id}
    rowLabel={(row) => row.name}
    presets={presetsFor(group, all)}
    onEdited={load}
    storageKey={storageKey}
    hideSearch
    exportName={label}
    renderDetail={canEdit ? (row) => <GroupChooser key={`${row.id}:${row.group_source}`} row={row} busy={busy} onChange={(value) => void move(row, value)} /> : undefined}
    emptyText={words.length ? "Ничего не найдено" : "В этом разделе пока пусто"}
  />;
  const section = (group: CoveringGroup) => {
    const rows = visible.filter((row) => row.group === group);
    const all = data.rows.filter((row) => row.group === group);
    const kinds = data.kinds?.[group] ?? [];
    // ТУТ/ТТК: подгруппы по виду (`shrink_kind` на сервере), каждая — своя таблица; поиск скрывает пустые.
    if (kinds.length > 1) return <section className="cov-group" key={group} aria-label={data.titles[group]}>
      <h2 className="cov-title">{data.titles[group]} <span className="page-count">{data.counts[group]}</span></h2>
      {kinds.map((kind) => {
        const kindRows = rows.filter((row) => row.kind === kind.id);
        if (words.length && !kindRows.length) return null;
        return <div className="cov-kind" key={kind.id} role="group" aria-label={kind.title}>
          <h3 className="cov-subtitle">{kind.title} <span className="page-count">{all.filter((row) => row.kind === kind.id).length}</span></h3>
          {table(group, kindRows, all, kind.title, `db:coverings:${group}:${kind.id}:1`)}
        </div>;
      })}
    </section>;
    return <section className="cov-group" key={group} aria-label={data.titles[group]}>
      <h2 className="cov-title">{data.titles[group]} <span className="page-count">{data.counts[group]}</span></h2>
      {table(group, rows, all, data.titles[group], `db:coverings:${group}:2`)}
    </section>;
  };
  return <div className="coverings">
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="cov-toolbar">
      <form className="table-search" role="search" onSubmit={(event) => event.preventDefault()}>
        <input type="search" className="search" aria-label="Поиск по оболочкам" placeholder="Наименование, серия, производитель, материал" value={query} onChange={(event) => setQuery(event.target.value)} />
      </form>
      <span className="muted">Сортировка: вид (в ТУТ и ТТК), размер, затем наименование</span>
    </div>
    {SHOWN.map(section)}
    {data.counts.other > 0 && section("other")}
  </div>;
}
