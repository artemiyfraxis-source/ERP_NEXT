import { Fragment, useEffect, useMemo, useState } from "react";
import { CrimpCell, CRIMP_COLUMN_TITLE, crimpText } from "./CrimpCell";
import { DataTable, type DataColumn } from "./DataTable";
import {
  failureText, techcardsApi,
  type ConnectorFamily, type ConnectorSeries, type ConnectorSeriesHousing, type ConnectorSeriesMatch, type ConnectorTerminal,
} from "./api";
import { connectorFace, connectorFaceUnits, connectorSummary, type ConnectorHousing } from "./connectorArt";
import { connectorModel } from "./connectorModel";

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

/** Картинка серии в списке: модель по шаблону серии, иначе торец со стороны проводов. */
function Thumb({ housing }: { housing: ConnectorHousing }) {
  const model = connectorModel(housing, 92, 56);
  return model ? <Svg box={model} label={`Модель ${housing.title ?? ""}`} /> : <Face housing={housing} width={90} />;
}

// From this many contact widths on, a housing card gets a wider column so the face numbers stay readable.
const WIDE_UNITS = 9;

function HousingCard({ item, hit, wide = false }: { item: ConnectorSeriesHousing; hit: boolean; wide?: boolean }) {
  const hs = item.housing;
  const model = connectorModel(hs, 130, 84);
  return <div className={hit ? "conn-housing conn-hit" : "conn-housing"} id={`conn-${item.article}`}>
    <div className="conn-housing-head">
      <b>{item.article}</b><span>{item.n} конт.</span>
      {hs.conf !== "high" && <small className="conn-warn" title={hs.note || undefined}>нумерация не сверена</small>}
    </div>
    <div className="conn-housing-model">{model
      ? <Svg box={model} label={`Модель ${item.article}`} />
      : <small className="conn-muted">модель не сверена</small>}</div>
    <div className="conn-face"><Face housing={hs} width={wide ? 400 : 250} /></div>
    {item.headers.length > 0 && <small className="conn-headers">
      {item.headers.map((header) => <span key={header.article} title={header.title}>{header.article}<i> — {header.title.replace("Вилка на плату", "").replace(/^,?\s*/, "")}</i></span>)}
    </small>}
  </div>;
}

const TERMINAL_COLUMNS: DataColumn<ConnectorTerminal>[] = [
  { key: "article", title: "Артикул контакта", value: (t) => t.article, render: (t) => <b>{t.article}</b> },
  { key: "kind", title: "Контакт", value: (t) => t.kind },
  { key: "product", title: "Серия", value: (t) => t.product },
  { key: "series", title: "Корпуса", value: (t) => t.series },
  { key: "awg", title: "AWG", value: (t) => (t.awg_from && t.awg_to && t.awg_from !== t.awg_to ? `${t.awg_from}–${t.awg_to}` : t.awg_from || t.awg_to) },
  { key: "mm2", title: "Сечение, мм²", value: (t) => (t.mm2_from && t.mm2_to && t.mm2_from !== t.mm2_to ? `${t.mm2_from}–${t.mm2_to}` : t.mm2_from || t.mm2_to) },
  { key: "strip", title: "Зачистка, мм", value: (t) => t.strip, numeric: true },
  { key: "crimp", title: CRIMP_COLUMN_TITLE, value: (t) => crimpText({ conductor: t.crimp_height, pull: t.pull_force }),
    render: (t) => <CrimpCell values={{ conductor: t.crimp_height, pull: t.pull_force }} /> },
  { key: "applicator", title: "Аппликатор", value: (t) => t.applicator },
];

type ContactRow = NonNullable<ConnectorSeries["contacts"]>[number];
const CONTACT_COLUMNS: DataColumn<ContactRow>[] = [
  { key: "article", title: "Артикул контакта", value: (c) => c.article, render: (c) => <b>{c.article}</b> },
  { key: "wire", title: "Провод, исполнение", value: (c) => c.wire },
  { key: "base", title: "Во вкладке «Терминалы»", value: (c) => (c.in_base ? "есть" : "нет"),
    render: (c) => (c.in_base ? <span className="tone-ok">есть</span> : <span className="tone-warn">нет — добавить</span>) },
];

type CatalogRow = ConnectorSeries["catalog"][number];
const CATALOG_COLUMNS: DataColumn<CatalogRow>[] = [
  { key: "article", title: "Артикул", value: (c) => c.article, render: (c) => <b>{c.article}</b> },
  { key: "kind", title: "Тип", value: (c) => c.kind },
  { key: "wire", title: "Кабель", value: (c) => c.wire },
  { key: "maker", title: "Производитель", value: (c) => c.maker },
];

const missing = (series: ConnectorSeries) => (series.contacts ?? []).filter((c) => !c.in_base).length;

function SeriesView({ series, match, onBack }: { series: ConnectorSeries; match: ConnectorSeriesMatch | null; onBack: () => void }) {
  const hit = match?.series === series.id ? match.article : null;
  const range = countsText(series.counts);
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
            ? <span className="order-chip tone-ok">нумерация сверена с чертежом</span>
            : <span className="order-chip tone-warn">нумерация не сверена с чертежом</span>}
          {series.range_checked
            ? <span className="order-chip tone-neutral">ряд корпусов по каталогу {series.range_src.replace(", Housing", "")}</span>
            : <span className="order-chip tone-warn" title="Каталога серии в ERP нет — показаны корпуса, которые ERP уже знает">диапазон не сверен</span>}
        </>}
      {series.wire && <span className="order-chip tone-info">провод {wireText(series.wire)}</span>}
      {missing(series) > 0 && <button type="button" className="order-chip tone-warn conn-jump"
        onClick={() => document.getElementById("conn-contacts")?.scrollIntoView?.({ block: "start" })}>
        контактов нет в «Терминалы»: {missing(series)} ↓</button>}
      {series.housing_count > 0 && <span className="muted">вид со стороны проводов, контакт 1 — красная метка{series.note ? ` · ${series.note}` : ""}</span>}
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
    {series.contacts && series.contacts.length > 0 && <section className="conn-variant" id="conn-contacts">
      <h3>Контакты по каталогу <span className="muted">{series.contacts.length}
        {missing(series) ? ` · нет во вкладке «Терминалы»: ${missing(series)}` : " · все есть во вкладке «Терминалы»"}</span></h3>
      <DataTable label={`Контакты по каталогу ${series.title}`} rows={series.contacts} columns={CONTACT_COLUMNS} rowKey={(c) => c.article}
        storageKey="db:conn-contacts" exportName={`Контакты ${series.title}`} hideSearch compactBelow={30} />
    </section>}
    {(series.terminals.length > 0 || series.catalog.length === 0) && <section className="conn-variant">
      <h3>Терминалы <span className="muted">{series.terminals.length ? `${series.terminals.length} · из вкладки «Терминалы»` : ""}</span></h3>
      <DataTable label={`Терминалы ${series.title}`} rows={series.terminals} columns={TERMINAL_COLUMNS} rowKey={(t) => t.article}
        storageKey="db:conn-terminals" exportName={`Терминалы ${series.title}`} hideSearch compactBelow={30}
        emptyText="В «Терминалы» нет контактов этой серии." />
    </section>}
  </div>;
}

function SeriesCard({ series, onOpen }: { series: ConnectorSeries; onOpen: () => void }) {
  const range = countsText(series.counts);
  const bare = series.housing_count === 0;
  const facts = bare
    ? [series.terminals.length ? `${series.terminals.length} терминалов` : "", series.catalog.length ? `${series.catalog.length} артикулов` : ""]
    : [series.pitch ? `${comma(series.pitch)} мм` : "", range ? `${range} конт.` : "", `${series.housing_count} корп.`];
  return <button type="button" className="conn-series" onClick={onOpen} aria-label={`Серия ${series.title}`}>
    <span className="conn-series-pic">{series.sample && <Thumb housing={series.sample.housing} />}</span>
    <span className="conn-series-text">
      <b>{series.title}</b>
      <small>{facts.filter(Boolean).join(" · ") || "нет данных в базах"}</small>
      {bare ? <small className="tone-warn">корпуса не добавлены</small>
        : !series.checked ? <small className="conn-warn">нумерация не сверена</small>
          : !series.range_checked ? <small className="tone-warn">диапазон не сверен</small>
            : <small>по каталогу {series.maker}</small>}
    </span>
  </button>;
}

export function ConnectorGallery() {
  const [series, setSeries] = useState<ConnectorSeries[] | null>(null);
  const [error, setError] = useState("");
  const [article, setArticle] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [category, setCategory] = useState<string | null>(null);
  const [match, setMatch] = useState<ConnectorSeriesMatch | null>(null);
  const [unknown, setUnknown] = useState<{ article: string; housing: ConnectorFamily["housing"] | null } | null>(null);

  useEffect(() => {
    techcardsApi.connectorSeries().then((result) => setSeries(result.series))
      .catch((reason) => { setSeries([]); setError(failureText(reason, "База разъёмов не загрузилась")); });
  }, []);

  const query = article.trim().toUpperCase().replace(/\s+/g, "");
  const shown = useMemo(() => (series ?? []).filter((item) => !query
    || `${item.title} ${item.maker}`.toUpperCase().replace(/\s+/g, "").includes(query)
    || item.variants.some((variant) => variant.housings.some((h) => h.article.toUpperCase().includes(query)))
    || item.terminals.some((t) => t.article.toUpperCase().replace(/\s+/g, "").includes(query))
    || item.catalog.some((c) => c.article.toUpperCase().replace(/\s+/g, "").includes(query))), [series, query]);
  // Sections in the order the server sends them (fixed category order); counts follow the search.
  const sections = useMemo(() => {
    // Inside a section the server sends the series by maker, then pitch; a maker label only where makers differ.
    const out: Array<{ name: string; items: ConnectorSeries[]; makers: number }> = [];
    for (const item of shown) {
      const last = out.find((section) => section.name === item.category);
      if (last) last.items.push(item); else out.push({ name: item.category, items: [item], makers: 0 });
    }
    for (const section of out) section.makers = new Set(section.items.map((item) => item.maker)).size;
    return out;
  }, [shown]);
  const categories = useMemo(() => {
    const names: string[] = [];
    for (const item of series ?? []) if (!names.includes(item.category)) names.push(item.category);
    return names;
  }, [series]);

  async function check() {
    const value = article.trim();
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
  const visible = sections.filter((section) => category === null || section.name === category);
  const open = series.find((item) => item.id === openId) ?? null;
  // A housing listed in two series (Mini-Fit Plus contacts go into Mini-Fit Jr. housings) is counted once.
  const housings = new Set(series.flatMap((item) => item.variants.flatMap((variant) => variant.housings.map((h) => h.article)))).size;
  return <div className="conn-base">
    <form className="toolbar" onSubmit={(event) => { event.preventDefault(); void check(); }}>
      <input value={article} placeholder="Серия или артикул корпуса, например GHR-04V-S" aria-label="Артикул корпуса"
        onChange={(event) => { setArticle(event.target.value); setUnknown(null); }} />
      <button className="secondary small" type="submit">Проверить артикул</button>
      <span className="muted">{series.length} серий · {housings} корпусов</span>
    </form>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {unknown && (unknown.housing
      ? <div className="warning-inline">«{unknown.article}» — {connectorSummary(unknown.housing)}: серия без списка корпусов, рисунок по правилу семейства.
        <div className="conn-grid"><HousingCard item={{ article: unknown.article, n: unknown.housing.n || 0, housing: unknown.housing, headers: [] }} hit /></div></div>
      : <div className="warning-inline">«{unknown.article}» — серия не распознана: на схеме будет только название.</div>)}
    {!open && <div className="seg-tabs conn-cats" role="group" aria-label="Тип разъёмов">
      <button type="button" className={category === null ? "on" : ""} aria-pressed={category === null} onClick={() => setCategory(null)}>
        Все<span className="ship-tab-count">{shown.length}</span></button>
      {categories.map((name) => <button key={name} type="button" className={category === name ? "on" : ""} aria-pressed={category === name}
        onClick={() => setCategory(name)}>{name}<span className="ship-tab-count">{shown.filter((item) => item.category === name).length}</span></button>)}
    </div>}
    {open
      ? <SeriesView series={open} match={match} onBack={() => { setOpenId(null); setMatch(null); }} />
      : <div className="conn-sections">{visible.length
        ? visible.map((section) => <section key={section.name} className="conn-section">
          <h3>{section.name} <span className="muted">{section.items.length}</span></h3>
          <div className="conn-series-list">
            {section.items.map((item, index) => <Fragment key={item.id}>
              {section.makers > 1 && (index === 0 || section.items[index - 1].maker !== item.maker)
                && <div className="conn-maker">{item.maker || "Без производителя"}</div>}
              <SeriesCard series={item} onOpen={() => { setOpenId(item.id); setMatch(null); }} />
            </Fragment>)}
          </div></section>)
        : <div className="empty">Серий по «{article.trim()}» нет — нажмите «Проверить артикул», чтобы узнать, как ERP нарисует корпус.</div>}</div>}
  </div>;
}
