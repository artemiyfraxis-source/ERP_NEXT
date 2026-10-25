import { useEffect, useMemo, useState, type ReactNode } from "react";
import { DataTable, type CellValue, type DataCellEdit, type DataColumn, type DataPreset } from "./DataTable";
import { fieldCellEdit } from "./CustomColumn";
import { SortHint } from "./refSort";
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

/** Все колонки оболочек — одна таблица в блоках (владелец 25.10.2026: «визуал БД один»); у каждого раздела — свои из них
    (`GROUP_KEYS`), в одном порядке для всех: размеры раздела, серия, материал, температура, цвет, производитель, ссылка. */
function coveringColumns(raw: RawEdit): DataColumn<CoveringRow>[] {
  const number = (key: string, title: string, pick: (row: CoveringRow) => number | null, field?: string): DataColumn<CoveringRow> =>
    ({ key, title, numeric: true, value: (row) => pick(row), render: (row) => pick(row) === null ? dash : mm(pick(row)), edit: field ? raw(field) : undefined });
  return [
    {
      key: "name", title: "Наименование",
      value: (row) => row.name,
      render: (row) => <><strong>{row.name}</strong>{row.group_source === "вручную" && <small className="muted" title="Раздел задан вручную"> · вручную</small>}</>,
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
    { key: "temp", title: "Температура, °C", value: (row) => tempText(row.temp_min_c, row.temp_max_c), render: (row) => orDash(tempText(row.temp_min_c, row.temp_max_c)) },
    { key: "color", title: "Цвет", value: (row) => row.color, render: (row) => orDash(row.color), edit: raw("Цвет") },
    { key: "maker", title: "Производитель", value: (row) => row.maker, render: (row) => clip(row.maker), edit: raw("Производитель") },
    {
      key: "datasheet", title: "Ссылка", value: (row) => row.datasheet, edit: raw("Ссылка на datasheet"),
      render: (row) => row.datasheet ? <a href={row.datasheet} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>datasheet</a> : dash,
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
export function groupKeys(group: CoveringGroup, rows: CoveringRow[]): string[] {
  return GROUP_KEYS[group].filter((key) => !SPARSE[key] || rows.some((row) => row.group === group && SPARSE[key](row) !== null && SPARSE[key](row) !== ""));
}

const BEFORE = "Размер до, мм";
const AFTER = "Размер после, мм";
// «Без Ø» — строки, у которых размер не нашёлся ни в колонке, ни в названии (их генератор не подберёт по диаметру).
const sizeOf: Record<CoveringGroup, (row: CoveringRow) => number | null> = {
  tut: (row) => row.before_mm, ttk: (row) => row.before_mm, cambric: (row) => row.before_mm, other: (row) => row.before_mm,
  braid: (row) => row.nominal_mm ?? row.min_mm, corrugated: (row) => row.inner_mm, tape: (row) => row.width_mm ?? null,
};
// Чипы — над всеми разделами; чип без строк не показывается: пустой отбор — шум. «Без размера» — Ø или ширина ленты.
const presetsFor = (rows: CoveringRow[]): DataPreset<CoveringRow>[] => ([
  { label: "Без размера", test: (row) => sizeOf[row.group](row) === null },
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
function coveringText(row: CoveringRow): CellValue[] {
  const cell = (value: unknown): CellValue => typeof value === "string" || typeof value === "number" || value === null || value === undefined ? value : String(value);
  return Object.entries(row).flatMap(([key, value]) => key === "id" || key === "group" || key === "auto_group" ? []
    : Array.isArray(value) ? value.map(cell) : value !== null && typeof value === "object" ? [] : [cell(value)]);
}

export function CoveringsTable({ canEdit, onCount }: { canEdit: boolean; onCount?: (count: number) => void }) {
  const [data, setData] = useState<CoveringsResult | null>(null);
  // The БД.МАТ rows behind the coverings: a cell edit sends the column's old value as the record has it.
  const [records, setRecords] = useState<Map<string, TechOperationRecord>>(new Map());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

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

  // The blocks: a kind (ТУТ, ТТК…) in the owner's order, ТУТ/ТТК by the server's sub-kinds (`shrink_kind`) under their
  // kind's heading; inside — the server's order (size, then name). An unknown sub-kind goes last in its kind.
  const order = useMemo(() => {
    if (!data) return { rows: [] as CoveringRow[], block: (_row: CoveringRow) => "" };
    const groups: CoveringGroup[] = [...SHOWN, "other"];
    const kindsOf = (group: CoveringGroup) => (data.kinds?.[group] ?? []).length > 1 ? data.kinds![group]! : [];
    const kindAt = (row: CoveringRow) => { const kinds = kindsOf(row.group); const at = kinds.findIndex((kind) => kind.id === row.kind); return at < 0 ? kinds.length : at; };
    const rows = data.rows.map((row, index) => ({ row, index }))
      .sort((a, b) => groups.indexOf(a.row.group) - groups.indexOf(b.row.group) || kindAt(a.row) - kindAt(b.row) || a.index - b.index)
      .map((item) => item.row);
    const block = (row: CoveringRow) => kindsOf(row.group).length ? `${row.group}:${row.kind ?? ""}` : row.group;
    return { rows, block };
  }, [data]);

  if (error && !data) return <div className="error-banner" role="alert">{error}</div>;
  if (!data) return <div className="empty">Загрузка…</div>;

  const raw: RawEdit = (field) => canEdit ? fieldCellEdit<CoveringRow>(field, (row) => records.get(row.id)) : undefined;
  const groupOf = (block: string) => block.split(":")[0] as CoveringGroup;
  const kindTitle = (block: string) => {
    const [group, kind] = block.split(":");
    return data.kinds?.[group as CoveringGroup]?.find((item) => item.id === kind)?.title ?? "Другие";
  };
  return <div className="coverings">
    {error && <div className="error-banner" role="alert">{error}</div>}
    <DataTable
      label="Оболочки"
      rows={order.rows}
      columns={coveringColumns(raw)}
      rowKey={(row) => row.id}
      rowLabel={(row) => row.name}
      groupBy={order.block}
      groupLabel={(block) => block.includes(":") ? kindTitle(block) : data.titles[groupOf(block)]}
      blocks
      blockSection={(block) => block.includes(":") ? data.titles[groupOf(block)] : undefined}
      blockColumns={(block) => groupKeys(groupOf(block), data.rows)}
      presets={presetsFor(data.rows)}
      onEdited={load}
      storageKey="db:coverings:2510"
      exportName="Оболочки"
      // Поиск — по всем значениям строки (наименование, серия, производитель, материал, цвет, размеры, примечания), по
      // правилам searchText.ts: «тут 3.0» находит «ТУТ 3,0/1,5».
      searchText={coveringText}
      searchLabel="Поиск по оболочкам"
      searchPlaceholder="Наименование, серия, производитель, материал"
      renderDetail={canEdit ? (row) => <GroupChooser key={`${row.id}:${row.group_source}`} row={row} busy={busy} onChange={(value) => void move(row, value)} /> : undefined}
      emptyText="Ничего не найдено"
      toolbar={<SortHint text="вид (в ТУТ и ТТК), размер, затем наименование" />}
    />
  </div>;
}
