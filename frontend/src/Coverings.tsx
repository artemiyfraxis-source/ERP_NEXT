import { useEffect, useMemo, useState, type ReactNode } from "react";
import { DataTable, type DataColumn } from "./DataTable";
import { failureText, referencesApi, type CoveringGroup, type CoveringRow, type CoveringsResult } from "./api";

/* «База данных» → «Оболочки» (owner 12.10.2026): термоусадка без клея и с клеем, оплётки, гофра — each its own table with
   the fields of its kind, grouped by series, sizes in a series by Ø as a number (the server sorts, `coverings.py`).
   «Прочее» (ленты, кембрик) is collapsed under a toggle. Nothing is deleted: a row moves between groups through
   the «Раздел» cell, «Авто» returns it to the classifier. */

const SHOWN: CoveringGroup[] = ["tut", "ttk", "braid", "corrugated"];
const CHOICES: Array<{ value: string; label: string }> = [
  { value: "", label: "Авто" }, { value: "ТУТ", label: "ТУТ" }, { value: "ТТК", label: "ТТК" },
  { value: "Оплётки", label: "Оплётки" }, { value: "Гофра", label: "Гофра" }, { value: "Прочее", label: "Прочее" },
];
const NO_SERIES = "Без серии";

const mm = (value: number | null) => value === null ? "" : String(value).replace(".", ",");
const signed = (value: number) => value > 0 ? `+${value}` : String(value).replace("-", "−");
export const tempText = (low: number | null, high: number | null) =>
  low === null && high === null ? "" : `${low === null ? "…" : signed(low)} … ${high === null ? "…" : signed(high)}`;
export const rangeText = (low: number | null, high: number | null) =>
  low === null || high === null ? "" : `${mm(low)}–${mm(high)}`;
const dash = <span className="muted">—</span>;
const orDash = (text: string): ReactNode => text ? text : dash;
const clip = (text: string) => text ? <span className="cable-clip" title={text}>{text}</span> : dash;

function columnsFor(group: CoveringGroup, rows: CoveringRow[]): DataColumn<CoveringRow>[] {
  const name: DataColumn<CoveringRow> = {
    key: "name", title: "Наименование",
    value: (row) => row.name,
    render: (row) => <><strong>{row.name}</strong>{row.group_source === "вручную" && <small className="muted" title="Раздел задан вручную"> · вручную</small>}</>,
  };
  const material: DataColumn<CoveringRow> = {
    key: "material", title: "Материал", value: (row) => row.material,
    render: (row) => row.material ? <span className="cable-clip" title={row.material}>{row.material_short && !row.material.toLocaleLowerCase("ru-RU").startsWith(row.material_short.toLocaleLowerCase("ru-RU")) ?`${row.material_short} · ${row.material}` : row.material}</span> : dash,
  };
  const temp: DataColumn<CoveringRow> = {
    key: "temp", title: "Температура, °C", value: (row) => tempText(row.temp_min_c, row.temp_max_c), render: (row) => orDash(tempText(row.temp_min_c, row.temp_max_c)),
  };
  const colour: DataColumn<CoveringRow> = { key: "color", title: "Цвет", value: (row) => row.color, render: (row) => orDash(row.color) };
  const maker: DataColumn<CoveringRow> = { key: "maker", title: "Производитель", value: (row) => row.maker, render: (row) => clip(row.maker) };
  const link: DataColumn<CoveringRow> = {
    key: "datasheet", title: "Ссылка", value: (row) => row.datasheet,
    render: (row) => row.datasheet ? <a href={row.datasheet} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>datasheet</a> : dash,
  };
  const number = (key: string, title: string, pick: (row: CoveringRow) => number | null): DataColumn<CoveringRow> =>
    ({ key, title, numeric: true, value: (row) => pick(row), render: (row) => pick(row) === null ? dash : mm(pick(row)) });
  const filled = (pick: (row: CoveringRow) => unknown) => rows.some((row) => pick(row) !== null && pick(row) !== "");
  if (group === "tut" || group === "ttk") return [
    name,
    number("before", "Ø до усадки, мм", (row) => row.before_mm),
    number("after", "Ø после, мм", (row) => row.after_mm),
    { key: "ratio", title: "Усадка", value: (row) => row.ratio, render: (row) => orDash(row.ratio) },
    { ...number("wall", "Стенка, мм", (row) => row.wall_mm), hidden: !filled((row) => row.wall_mm) },
    ...(group === "ttk" ? [{ key: "adhesive", title: "Клей", value: (row: CoveringRow) => row.adhesive, render: (row: CoveringRow) => orDash(row.adhesive) }] : []),
    material, temp, colour, maker, link,
  ];
  if (group === "braid") return [
    name,
    number("nominal", "Ø номинал, мм", (row) => row.nominal_mm),
    { key: "range", title: "Диапазон Ø, мм", value: (row) => row.min_mm, numeric: true, render: (row) => orDash(rangeText(row.min_mm, row.max_mm)) },
    material,
    { key: "density", title: "Плотность", value: (row) => row.density, render: (row) => orDash(row.density), hidden: !filled((row) => row.density) },
    temp, colour, maker, link,
  ];
  if (group === "corrugated") return [
    name,
    number("inner", "Ø внутр., мм", (row) => row.inner_mm),
    number("outer", "Ø наружн., мм", (row) => row.outer_mm),
    { key: "split", title: "Разрезная", value: (row) => row.split === null ? "" : row.split ? "да" : "нет", render: (row) => row.split === null ? dash : row.split ? "да" : "нет" },
    material, temp, colour, maker, link,
  ];
  return [name, maker, material, temp, colour, link];
}

function GroupChooser({ row, busy, onChange }: { row: CoveringRow; busy: boolean; onChange: (group: string) => void }) {
  const manual = row.group_source === "вручную";
  const current = manual ? CHOICES.find((choice) => choice.label.toLocaleLowerCase("ru-RU") === ({ tut: "тут", ttk: "ттк", braid: "оплётки", corrugated: "гофра", other: "прочее" })[row.group])?.value ?? "" : "";
  return <div className="reference-fields cable-card">
    <label><small>Раздел</small>
      <select aria-label="Раздел оболочек" value={current} disabled={busy} onChange={(event) => onChange(event.target.value)}>
        {CHOICES.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
      </select>
    </label>
    {row.notes.length > 0 && <span><small>Взято из названия</small>{row.notes.join(", ")}</span>}
  </div>;
}

export function CoveringsTable({ canEdit, onCount }: { canEdit: boolean; onCount?: (count: number) => void }) {
  const [data, setData] = useState<CoveringsResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [showOther, setShowOther] = useState(false);

  async function load() {
    try {
      const result = await referencesApi.coverings();
      setData(result);
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

  const words = query.trim().toLocaleLowerCase("ru-RU").split(/\s+/).filter(Boolean);
  const visible = useMemo(() => (data?.rows ?? []).filter((row) => {
    const text = [row.name, row.series, row.maker, row.material, row.color].join(" ").toLocaleLowerCase("ru-RU");
    return words.every((word) => text.includes(word));
  }), [data, query]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error && !data) return <div className="error-banner" role="alert">{error}</div>;
  if (!data) return <div className="empty">Загрузка…</div>;

  const section = (group: CoveringGroup) => {
    const rows = visible.filter((row) => row.group === group);
    return <section className="cov-group" key={group} aria-label={data.titles[group]}>
      <h2 className="cov-title">{data.titles[group]} <span className="page-count">{data.counts[group]}</span></h2>
      <DataTable
        label={data.titles[group]}
        rows={rows}
        columns={columnsFor(group, data.rows.filter((row) => row.group === group))}
        rowKey={(row) => row.id}
        storageKey={`db:coverings:${group}`}
        groupBy={(row) => row.series_family || NO_SERIES}
        hideSearch
        exportName={data.titles[group]}
        renderDetail={canEdit ? (row) => <GroupChooser key={`${row.id}:${row.group_source}`} row={row} busy={busy} onChange={(value) => void move(row, value)} /> : undefined}
        emptyText={words.length ? "Ничего не найдено" : "В этом разделе пока пусто"}
      />
    </section>;
  };
  return <div className="coverings">
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="cov-toolbar">
      <form className="table-search" role="search" onSubmit={(event) => event.preventDefault()}>
        <input type="search" className="search" aria-label="Поиск по оболочкам" placeholder="Наименование, серия, производитель, материал" value={query} onChange={(event) => setQuery(event.target.value)} />
      </form>
      <span className="muted">Сортировка: серия, затем Ø</span>
    </div>
    {SHOWN.map(section)}
    <div className="cov-other">
      <button type="button" className="secondary small" aria-expanded={showOther} onClick={() => setShowOther(!showOther)}>
        {showOther ? "Скрыть прочее" : "Показать прочее"} · {data.counts.other}
      </button>
      {showOther && section("other")}
    </div>
  </div>;
}
