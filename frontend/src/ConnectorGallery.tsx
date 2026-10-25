import { useCallback, useEffect, useMemo, useState } from "react";
import { CrimpCell } from "./CrimpCell";
import { cellFailure } from "./CustomColumn";
import { DataTable, type DataColumn } from "./DataTable";
import {
  failureText, referencesApi, techcardsApi,
  type ConnectorFamily, type ConnectorSeries, type ConnectorSeriesHousing, type ConnectorSeriesMatch, type ConnectorTerminal,
} from "./api";
import { connectorFace, connectorFaceUnits, connectorPicture, connectorSummary, type ConnectorHousing } from "./connectorArt";
import { connectorModel } from "./connectorModel";
import { connectorTopView } from "./connectorTopView";
import { SortHint } from "./refSort";

/* «База данных → Разъёмы» по сериям (владелец, 10.10.2026: «идти по сериям: серия GHR, внутри все доступные разъёмы
   и их модели»). Список серий → серия: все корпуса по каталогу производителя (модель + вид со стороны проводов),
   ответные вилки на плату, совместимые терминалы из «Терминалы» и диапазон провода. «Проверить артикул» открывает
   серию и подсвечивает корпус. */

const comma = (value: number | null | undefined) => (value == null ? "" : String(value).replace(".", ","));

/** Числа контактов коротко: 2–15; 1–16, 20; 8–34, чётные. */
export function countsText(counts: number[]): string {
  if (!counts.length) return "";
  if (counts.length > 2 && counts.every((n, i) => i === 0 || n - counts[i - 1] === 2)) {
    return `${counts[0]}–${counts[counts.length - 1]}, ${counts[0] % 2 ? "нечётные" : "чётные"}`;
  }
  const runs: string[] = [];
  let start = counts[0], prev = counts[0];
  for (const n of [...counts.slice(1), Number.NaN]) {
    if (n === prev + 1) { prev = n; continue; }
    runs.push(start === prev ? String(start) : `${start}–${prev}`);
    start = prev = n;
  }
  return runs.join(", ");
}

export function wireText(wire: ConnectorSeries["wire"]): string {
  if (!wire) return "";
  const parts = [];
  if (wire.awg_thin != null) parts.push(wire.awg_thin === wire.awg_thick ? `AWG ${wire.awg_thin}` : `AWG ${wire.awg_thin}–${wire.awg_thick}`);
  if (wire.mm2_min != null) parts.push(wire.mm2_min === wire.mm2_max ? `${comma(wire.mm2_min)} мм²` : `${comma(wire.mm2_min)}–${comma(wire.mm2_max)} мм²`);
  return parts.join(" · ");
}

function Svg({ box, label }: { box: { w: number; h: number; body: string }; label: string }) {
  return <svg viewBox={`0 0 ${box.w} ${box.h}`} width={box.w} height={box.h} role="img" aria-label={label}
    dangerouslySetInnerHTML={{ __html: box.body }} />;
}

function Face({ housing, width = 250 }: { housing: ConnectorHousing; width?: number }) {
  const round = housing.kind === "round" || housing.kind === "barrel";
  const step = round ? 20 : Math.max(12, Math.min(34, width / connectorFaceUnits(housing)));
  const face = connectorFace(housing, 10, 4, step, null);
  return <Svg box={{ w: face.w + 20, h: face.h + 8, body: face.body }} label="Вид со стороны проводов" />;
}

/** Картинка серии в списке — объёмный корпус у всех серий одного вида (владелец 16.10: «мешанина вида нужна
 *  однообразность»): модель по чертежу серии, а без неё — объёмная картинка серии по её раскладке. */
function Thumb({ housing }: { housing: ConnectorHousing }) {
  const model = connectorModel(housing, 92, 56) || connectorPicture(housing, 92, 56);
  return <Svg box={model} label={`Модель ${housing.title ?? ""}`} />;
}

// From this many contact widths on, a housing card gets a wider column so the face numbers stay readable.
const WIDE_UNITS = 9;

function HousingCard({ item, hit, wide = false }: { item: ConnectorSeriesHousing; hit: boolean; wide?: boolean }) {
  const hs = item.housing;
  // Два вида (владелец 16.10: «зачем мне три вида»): корпус, развёрнутый видом сверху, и торец с нумерацией контактов.
  const top = connectorTopView(hs, wide ? 240 : 170, 120);
  return <div className={hit ? "conn-housing conn-hit" : "conn-housing"} id={`conn-${item.article}`}>
    <div className="conn-housing-head">
      <b>{item.article}</b><span>{item.n} конт.</span>
      {hs.mate && <span title={hs.mate.src}>{hs.mate.contacts === "pin" ? "штыри" : "гнёзда"}</span>}
      {hs.conf !== "high" && <small className="conn-warn" title={hs.note || undefined}>нумерация не сверена</small>}
    </div>
    <div className="conn-housing-views">
      {top && <div className="conn-housing-top"><Svg box={top} label={`Вид сверху ${item.article}`} /></div>}
      <div className="conn-face"><Face housing={hs} width={wide ? 400 : 250} /></div>
    </div>
    {item.headers.length > 0 && <small className="conn-headers">
      {item.headers.map((header) => <span key={header.article} title={header.title}>{header.article}<i> — {header.title.replace("Вилка на плату", "").replace(/^,?\s*/, "")}</i></span>)}
    </small>}
  </div>;
}

const contactGender = (text: string) => (/^(female|гнезд)/i.test(text) ? "Female" : /^(male|штыр)/i.test(text) ? "Male" : "");
const terminalCrimp = (t: ConnectorTerminal) => ({ conductor: t.crimp_height, insulation: t.ins_crimp_height, pull: t.pull_force });

const range = (from: string, to: string) => (from && to && from !== to ? `${from}–${to}` : from || to);
const splitRange = (text: string): [string, string] => {
  const parts = text.split(/\s*[–—-]\s*/).map((part) => part.trim()).filter(Boolean);
  return parts.length > 1 ? [parts[0], parts[parts.length - 1]] : [parts[0] ?? "", parts[0] ?? ""];
};
const GENDERS = [{ value: "Female", label: "гнездо", aliases: ["Female", "гнездо"] }, { value: "Male", label: "штырь", aliases: ["Male", "штырь"] }];

/** Правка ячейки терминала в карточке серии — путь «Базы данных»: одна правка записи БД.ТЕР со старым значением, после
    записи карточка перечитывается. Без права на правку колонки только для чтения. */
function terminalColumns(save?: (t: ConnectorTerminal, edits: Array<{ field: string; value: string }>) => Promise<void>): DataColumn<ConnectorTerminal>[] {
  const cell = (fields: (t: ConnectorTerminal, text: string) => Array<{ field: string; value: string }>, value: (t: ConnectorTerminal) => string,
    extra: Partial<NonNullable<DataColumn<ConnectorTerminal>["edit"]>> = {}) => save
    ? { enabled: (t: ConnectorTerminal) => Boolean(t.id), value, save: (t: ConnectorTerminal, text: string) => save(t, fields(t, text.trim())), ...extra }
    : undefined;
  return [
    { key: "article", title: "Артикул контакта", value: (t) => t.article, render: (t) => <b>{t.article}</b> },
    { key: "kind", title: "Контакт", value: (t) => t.kind,
      edit: cell((t, text) => [{ field: "terType", value: text }, ...("terGender" in (t.raw ?? {}) ? [{ field: "terGender", value: contactGender(text) }] : [])],
        (t) => t.raw?.terType ?? "", { options: (t) => {
          const own = t.raw?.terType ?? "";
          return own && !GENDERS.some((g) => g.value === own) ? [{ value: own, label: t.kind }, ...GENDERS] : GENDERS;
        } }) },
    { key: "product", title: "Серия", value: (t) => t.product },
    { key: "series", title: "Корпуса", value: (t) => t.series },
    { key: "awg", title: "AWG", value: (t) => range(t.awg_from, t.awg_to),
      edit: cell((_t, text) => { const [from, to] = splitRange(text); return [{ field: "awgFrom", value: from }, { field: "awgTo", value: to }]; },
        (t) => range(t.awg_from, t.awg_to)) },
    { key: "mm2", title: "Сечение, мм²", value: (t) => range(t.mm2_from, t.mm2_to),
      edit: cell((_t, text) => { const [from, to] = splitRange(text); return [{ field: "mm2From", value: from }, { field: "mm2To", value: to }]; },
        (t) => range(t.mm2_from, t.mm2_to)) },
    { key: "strip", title: "Зачистка, мм", value: (t) => t.strip, numeric: true, edit: cell((_t, text) => [{ field: "terStrip", value: text }], (t) => t.strip) },
    { key: "conductor", title: "Обжим по жиле, мм", value: (t) => t.crimp_height, render: (t) => <CrimpCell values={terminalCrimp(t)} only="conductor" />,
      edit: cell((_t, text) => [{ field: "terCrimpHeight", value: text }], (t) => t.crimp_height) },
    { key: "insulation", title: "Обжим по изоляции, мм", value: (t) => t.ins_crimp_height ?? "", render: (t) => <CrimpCell values={terminalCrimp(t)} only="insulation" />,
      edit: cell((_t, text) => [{ field: "terInsCrimpHeight", value: text }], (t) => t.ins_crimp_height ?? "") },
    { key: "pull", title: "Отрыв, Н", value: (t) => t.pull_force, render: (t) => <CrimpCell values={terminalCrimp(t)} only="pull" />,
      edit: cell((t, text) => [{ field: "terPullForceMin", value: text }, ...(t.raw?.terPullForceMax ? [{ field: "terPullForceMax", value: "" }] : [])], (t) => t.pull_force) },
    { key: "applicator", title: "Аппликатор", value: (t) => t.applicator, edit: cell((_t, text) => [{ field: "terApplicator", value: text }], (t) => t.applicator) },
  ];
}

type CatalogRow = ConnectorSeries["catalog"][number];
const CATALOG_COLUMNS: DataColumn<CatalogRow>[] = [
  { key: "article", title: "Артикул", value: (c) => c.article, render: (c) => <b>{c.article}</b> },
  { key: "kind", title: "Тип", value: (c) => c.kind },
  { key: "wire", title: "Кабель", value: (c) => c.wire },
  { key: "maker", title: "Производитель", value: (c) => c.maker },
];

function SeriesView({ series, match, onBack, canEdit, onSaved }: {
  series: ConnectorSeries; match: ConnectorSeriesMatch | null; onBack: () => void; canEdit: boolean; onSaved: () => Promise<void>;
}) {
  const columns = useMemo(() => terminalColumns(canEdit ? async (t, edits) => {
    const raw = t.raw ?? {};
    try {
      await referencesApi.editRecord(t.id ?? "", edits.filter((edit) => edit.value !== (raw[edit.field] ?? ""))
        .map((edit) => ({ field: edit.field, old: raw[edit.field] ?? "", value: edit.value })));
    } catch (reason) { throw cellFailure(reason); }
    await onSaved();
  } : undefined), [canEdit, onSaved]);
  const hit = match?.series === series.id ? match.article : null;
  const range = countsText(series.counts);
  // Вилка и розетка серии различаются контактами (штыри / гнёзда) — легенда рисунка, только где сторона пары известна.
  const mated = series.variants.some((variant) => variant.housings.some((item) => item.housing.mate));
  useEffect(() => {
    if (hit) document.getElementById(`conn-${hit}`)?.scrollIntoView?.({ block: "center" });
  }, [hit]);
  return <div className="conn-series-view">
    <div className="conn-series-head">
      <button type="button" className="secondary small" onClick={onBack}>← Все серии</button>
      <h2>{series.title}</h2>
      <span className="muted">{[series.category, series.maker, series.pitch ? `шаг ${comma(series.pitch)} мм` : "", range ? `${range} конт.` : "", series.housing_count ? `${series.housing_count} корп.` : ""].filter(Boolean).join(" · ")}</span>
      {!series.sources?.length && series.datasheet && <a href={series.datasheet} target="_blank" rel="noreferrer">Чертёж производителя (PDF)</a>}
    </div>
    {series.sources && series.sources.length > 0 && <div className="conn-sources">
      <span className="muted">{series.sources.length > 1 ? "Документы производителя:" : "Документ производителя:"}</span>
      {series.sources.map((source) => <a key={source.url} href={source.url} target="_blank" rel="noreferrer">{source.title}</a>)}
    </div>}
    <div className="conn-facts">
      {series.housing_count === 0
        ? <span className="order-chip tone-warn" title="Каталога корпусов серии в ERP нет — показаны только данные из баз «Терминалы» и «Коакс»">корпуса не добавлены</span>
        : <>
          {series.checked
            ? <span className="order-chip tone-ok">{series.checked_on_part ? "нумерация сверена по детали" : "нумерация сверена с чертежом"}</span>
            : <span className="order-chip tone-warn">нумерация не сверена с чертежом</span>}
          {series.range_checked
            ? <span className="order-chip tone-neutral">ряд корпусов по каталогу {series.range_src.replace(", Housing", "")}</span>
            : <span className="order-chip tone-warn" title="Каталога серии в ERP нет — показаны корпуса, которые ERP уже знает">диапазон не сверен</span>}
        </>}
      {series.wire && <span className="order-chip tone-info">провод {wireText(series.wire)}</span>}
      {series.housing_count > 0
        ? <span className="muted">вид со стороны проводов, контакт 1 — красная метка{mated ? ", штырь — металл, гнездо — окно с кольцом" : ""}{series.note ? ` · ${series.note}` : ""}</span>
        : series.note && <span className="muted">{series.note}</span>}
    </div>
    {match?.series === series.id && !match.article && <div className="warning-inline">
      Такого корпуса нет в {series.range_checked ? "каталоге серии" : "известных ERP"} — ниже он нарисован по правилу серии.
      <div className="conn-grid"><HousingCard item={{ article: match.housing.art || "", n: match.housing.n || 0, housing: match.housing, headers: [] }} hit /></div>
    </div>}
    {series.variants.map((variant) => <section key={`${variant.family}-${variant.title}`} className="conn-variant">
      {series.variants.length > 1 && <h3>{variant.title} <span className="muted">{countsText(variant.housings.map((h) => h.n))} конт.</span></h3>}
      {[false, true].map((wide) => {
        const items = variant.housings.filter((item) => (connectorFaceUnits(item.housing) >= WIDE_UNITS) === wide);
        return items.length > 0 && <div key={String(wide)} className={wide ? "conn-grid conn-grid-wide" : "conn-grid"}>
          {items.map((item) => <HousingCard key={item.article} item={item} hit={item.article === hit} wide={wide} />)}</div>;
      })}
    </section>)}
    {series.catalog.length > 0 && <section className="conn-variant">
      <h3>Артикулы <span className="muted">{series.catalog.length} · из вкладки «Коакс»</span></h3>
      <DataTable label={`Артикулы ${series.title}`} rows={series.catalog} columns={CATALOG_COLUMNS} rowKey={(c) => c.article}
        storageKey="db:conn-catalog" exportName={`Коакс ${series.title}`} hideSearch compactBelow={30} />
    </section>}
    {(series.terminals.length > 0 || series.catalog.length === 0) && <section className="conn-variant">
      <h3>Терминалы <span className="muted">{series.terminals.length ? `${series.terminals.length} · из вкладки «Терминалы»` : ""}</span></h3>
      <DataTable label={`Терминалы ${series.title}`} rows={series.terminals} columns={columns} rowKey={(t) => t.article}
        storageKey="db:conn-terminals" exportName={`Терминалы ${series.title}`} hideSearch compactBelow={30}
        emptyText="В «Терминалы» нет контактов этой серии." />
    </section>}
  </div>;
}

/** Состояние серии одной строкой: чего в ней не хватает, иначе — по чьему каталогу ряд корпусов. */
export function seriesState(series: ConnectorSeries): { text: string; tone: string } {
  if (series.housing_count === 0) return { text: "корпуса не добавлены", tone: "tone-warn" };
  if (!series.checked) return { text: "нумерация не сверена", tone: "conn-warn" };
  if (!series.range_checked) return { text: "диапазон не сверен", tone: "tone-warn" };
  return { text: `по каталогу ${series.maker}`, tone: "muted" };
}

const blank = <span className="cell-empty">—</span>;
// «База данных → Разъёмы» таблицей в блоках по типу (владелец 25.10.2026: «визуал БД один»): строка — серия, щелчок
// открывает её корпуса, вилки и терминалы. Картинка серии — маленькая модель у названия (однообразно у всех, 16.10).
const SERIES_COLUMNS: DataColumn<ConnectorSeries>[] = [
  { key: "title", title: "Серия", value: (item) => item.title,
    render: (item) => <span className="conn-row-title"><span className="conn-row-pic" aria-hidden="true">{item.sample && <Thumb housing={item.sample.housing} />}</span><strong>{item.title}</strong></span> },
  { key: "maker", title: "Производитель", value: (item) => item.maker },
  { key: "pitch", title: "Шаг, мм", numeric: true, value: (item) => item.pitch ?? "", render: (item) => item.pitch == null ? blank : comma(item.pitch) },
  { key: "counts", title: "Контактов", wrap: true, maxWidth: 200, value: (item) => countsText(item.counts), render: (item) => countsText(item.counts) || blank },
  { key: "housings", title: "Корпусов", numeric: true, value: (item) => item.housing_count || "", render: (item) => item.housing_count || blank },
  { key: "terminals", title: "Терминалов", numeric: true, value: (item) => item.terminals.length || "", render: (item) => item.terminals.length || blank },
  { key: "catalog", title: "Артикулов", numeric: true, hidden: true, value: (item) => item.catalog.length || "", render: (item) => item.catalog.length || blank },
  { key: "wire", title: "Провод", value: (item) => item.wire ? wireText(item.wire) : "", render: (item) => item.wire ? wireText(item.wire) : blank },
  { key: "state", title: "Состояние", value: (item) => seriesState(item).text,
    render: (item) => { const state = seriesState(item); return <span className={state.tone}>{state.text}</span>; } },
];
// Быстрые отборы — чего не хватает серии (как «Без Ø» у кабелей); чип без строк не показывается.
const SERIES_PRESETS = [
  { label: "Корпуса не добавлены", test: (item: ConnectorSeries) => item.housing_count === 0 },
  { label: "Нумерация не сверена", test: (item: ConnectorSeries) => item.housing_count > 0 && !item.checked },
  { label: "Диапазон не сверен", test: (item: ConnectorSeries) => item.housing_count > 0 && item.checked && !item.range_checked },
];
// Серия, производитель, артикулы корпусов, терминалов и каталога — по правилам поиска баз (searchText.ts): «ghr 04»,
// «GHR-04V-S» и «ghr04vs» находят одну серию.
const seriesSearch = (item: ConnectorSeries) => [
  ...item.variants.flatMap((variant) => variant.housings.map((h) => h.article)),
  ...item.terminals.map((t) => t.article),
  ...item.catalog.map((c) => c.article),
  ...(item.contacts ?? []).map((c) => c.article),
];

export function ConnectorGallery({ canEdit = false }: { canEdit?: boolean }) {
  const [series, setSeries] = useState<ConnectorSeries[] | null>(null);
  const [error, setError] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [match, setMatch] = useState<ConnectorSeriesMatch | null>(null);
  const [unknown, setUnknown] = useState<{ article: string; housing: ConnectorFamily["housing"] | null } | null>(null);

  const reload = useCallback(() => techcardsApi.connectorSeries().then((result) => setSeries(result.series))
    .catch((reason) => { setSeries((current) => current ?? []); setError(failureText(reason, "База разъёмов не загрузилась")); }), []);
  useEffect(() => { void reload(); }, [reload]);

  async function check(query: string) {
    const value = query.trim();
    setUnknown(null);
    if (!value) { setMatch(null); return; }
    try {
      const found = (await techcardsApi.connectorSeries(value)).match;
      setMatch(found);
      if (found?.series) setOpenId(found.series);
      else setUnknown({ article: value, housing: found?.housing ?? null });
    } catch (reason) { setError(failureText(reason, "Артикул не проверен")); }
  }

  if (series === null) return <div className="empty">Загрузка…</div>;
  const open = series.find((item) => item.id === openId) ?? null;
  // A housing listed in two series (Mini-Fit Plus contacts go into Mini-Fit Jr. housings) is counted once.
  const housings = new Set(series.flatMap((item) => item.variants.flatMap((variant) => variant.housings.map((h) => h.article)))).size;
  return <div className="conn-base">
    {error && <div className="error-banner" role="alert">{error}</div>}
    {unknown && (unknown.housing
      ? <div className="warning-inline">«{unknown.article}» — {connectorSummary(unknown.housing)}: серия без списка корпусов, рисунок по правилу семейства.
        <div className="conn-grid"><HousingCard item={{ article: unknown.article, n: unknown.housing.n || 0, housing: unknown.housing, headers: [] }} hit /></div></div>
      : <div className="warning-inline">«{unknown.article}» — серия не распознана: на схеме будет только название.</div>)}
    {open && <SeriesView series={open} match={match} canEdit={canEdit} onSaved={reload} onBack={() => { setOpenId(null); setMatch(null); }} />}
    {/* The list stays mounted under an opened series: its search and scroll are there on «← Все серии». */}
    <div hidden={Boolean(open)}>
      <DataTable
        label="Разъёмы"
        rows={series}
        columns={SERIES_COLUMNS}
        rowKey={(item) => item.id}
        rowLabel={(item) => item.title}
        // Блок — тип разъёмов в порядке сервера (провод — плата, провод — провод…), внутри — производитель, шаг.
        groupBy={(item) => item.category}
        blocks
        storageKey="db:connectors:2510"
        exportName="Разъёмы"
        presets={SERIES_PRESETS.filter((preset) => series.some(preset.test))}
        searchText={seriesSearch}
        searchLabel="Серия или артикул корпуса"
        searchPlaceholder="Серия или артикул корпуса, например GHR-04V-S"
        onSearch={(query) => void check(query)}
        searchButton="Проверить артикул"
        onRowClick={(item) => { setOpenId(item.id); setMatch(null); setUnknown(null); }}
        rowTitle={(item) => `Открыть серию ${item.title}`}
        emptyText="Серий не найдено — «Проверить артикул» покажет, как ERP нарисует корпус."
        toolbar={<><SortHint text="тип, затем производитель и шаг" /><span className="muted sort-hint">{series.length} серий · {housings} корпусов</span></>}
      />
    </div>
  </div>;
}
