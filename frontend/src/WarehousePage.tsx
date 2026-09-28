import { Fragment, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { warehouseApi, type WarehouseLot } from "./api";

export function WarehousePage() {
  const [lots, setLots] = useState<WarehouseLot[]>([]);
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [warehouse, setWarehouse] = useState("");
  const [includeZero, setIncludeZero] = useState(false);
  const [total, setTotal] = useState(0);
  const [adding, setAdding] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [moveLot, setMoveLot] = useState<WarehouseLot | null>(null);
  const [groupBy, setGroupBy] = useState<"" | "type" | "place" | "name" | "series">("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const requestVersion = useRef(0);
  const groupedLots = useMemo(() => warehouseGroups(lots, groupBy), [lots, groupBy]);

  async function loadAllLots() {
    const loaded: WarehouseLot[] = [];
    let cursor: string | undefined;
    let total = 0;
    do {
      const page = await warehouseApi.list({ after: cursor, query, includeZero, warehouse });
      loaded.push(...page.lots);
      total = page.total;
      cursor = page.next_cursor ?? undefined;
    } while (cursor);
    return { lots: [...new Map(loaded.map((lot) => [lot.id, lot])).values()], total };
  }

  async function reload() {
    const version = ++requestVersion.current;
    setLoading(true);
    setError("");
    try {
      const result = await loadAllLots();
      if (version !== requestVersion.current) return;
      setLots(result.lots);
      setTotal(result.total);
    } catch (reason) {
      if (version !== requestVersion.current) return;
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить склад");
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => void reload(), 250);
    return () => window.clearTimeout(timer);
  }, [query, warehouse, includeZero]);

  async function mutate(key: string, operation: () => Promise<unknown>) {
    if (busy || loading) return;
    requestVersion.current += 1;
    setBusy(key);
    setError("");
    try {
      await operation();
      const result = await loadAllLots();
      setLots(result.lots);
      setTotal(result.total);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Складская операция не выполнена");
      throw reason;
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="page stock-page">
      <header className="page-head">
        <h1>Склад</h1>
        <span className="page-count">{total}</span>
      </header>
      <p className="page-sub">Остатки партиями: одна поставка — одна партия со своим номером.</p>
      <p className="page-sub">Остаток считается из движений, поэтому на вопрос «почему столько» отвечает история, а не догадка.</p>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="filters">
        <button className="primary compact" disabled={loading || Boolean(busy)} onClick={() => setAdding((value) => !value)}>+ Внести позиции</button>
        <button className="secondary compact" disabled={loading || Boolean(busy)} onClick={() => { setMoveLot(null); setMoveOpen(true); }}>↔ Переместить</button>
        <input className="search" aria-label="Поиск по складу" placeholder="Поиск: артикул, серия, место, ГРН…" value={query} onChange={(event) => setQuery(event.target.value)} />
        <select aria-label="Склад" value={warehouse} onChange={(event) => setWarehouse(event.target.value)}>
          <option value="">Все склады</option><option value="ЦАР">ЦАР</option><option value="ЖУК">ЖУК</option>
        </select>
        <select aria-label="Группировка склада" value={groupBy} onChange={(event) => setGroupBy(event.target.value as typeof groupBy)}>
          <option value="">Без группировки</option><option value="type">По типу</option><option value="place">По месту (полка)</option><option value="name">По наименованию</option><option value="series">По серии</option>
        </select>
        <label className="check-inline"><input type="checkbox" checked={includeZero} onChange={(event) => setIncludeZero(event.target.checked)} />С нулевым остатком</label>
        <button className="secondary compact" disabled={loading || Boolean(busy)} onClick={() => void reload()}>{loading ? "Обновляем…" : "Обновить"}</button>
      </div>
      {adding && <ManualStockForm
        busy={Boolean(busy) || loading}
        knownLots={lots}
        onCancel={() => setAdding(false)}
        onAdded={async (item) => {
          requestVersion.current += 1;
          setBusy("manual");
          setError("");
          try {
            await warehouseApi.add([item]);
            setAdding(false);
            await reload();
          } catch (reason) {
            setError(reason instanceof Error ? reason.message : "Не удалось внести позицию");
          } finally {
            setBusy("");
          }
        }}
      />}
      {moveOpen && <MoveStockForm
        preset={moveLot}
        busy={Boolean(busy) || loading}
        mutate={mutate}
        onClose={() => setMoveOpen(false)}
      />}
      <div className="table-wrap">
        <table className="grid wide compact stock-table">
          <thead><tr><th>Тип</th><th>Партия</th><th className="num">Остаток</th><th className="num">Доступно</th><th>Ед.</th><th>Место</th><th>Серия</th><th>Артикул</th><th>Наименование</th><th>Назначение</th><th>Склад</th><th /></tr></thead>
          <tbody>{groupedLots.map((group) => <ArticleGroup
            key={group.key}
            group={group}
            expanded={expanded}
            toggle={(key) => setExpanded((current) => {
              const next = new Set(current);
              if (next.has(key)) next.delete(key); else next.add(key);
              return next;
            })}
            busy={busy}
            disabled={Boolean(busy) || loading}
            mutate={mutate}
            onMove={(item) => { setMoveLot(item); setMoveOpen(true); }}
          />)}</tbody>
        </table>
      </div>
      {!lots.length && !loading && <div className="empty">{includeZero ? "Склад пуст." : "Позиций с остатком нет. Включите «С нулевым остатком», чтобы увидеть израсходованные партии."}</div>}
    </section>
  );
}

type WarehouseArticle = {
  key: string;
  article: string;
  lots: WarehouseLot[];
};

type WarehouseGroup = {
  key: string;
  label: string;
  articles: WarehouseArticle[];
};

const warehouseCollator = new Intl.Collator("ru", { numeric: true, sensitivity: "base" });

function articleKey(lot: WarehouseLot) {
  const identity = (lot.article || lot.name).trim().replace(/\s+/g, " ").toLocaleLowerCase("ru");
  return identity || `\u0001грн ${lot.grn}/${lot.package}`;
}

function shelf(location: string) {
  return location.replace(/\s+/g, "").toUpperCase().match(/^(\d+[A-ZА-Я])/)?.[1] ?? location;
}

function warehouseGroups(lots: WarehouseLot[], groupBy: "" | "type" | "place" | "name" | "series"): WarehouseGroup[] {
  const groupMap = new Map<string, WarehouseLot[]>();
  for (const lot of lots) {
    const value = groupBy === "type" ? lot.item_type
      : groupBy === "place" ? shelf(lot.storage_location)
      : groupBy === "name" ? lot.name
      : groupBy === "series" ? lot.series
      : "";
    const key = value.trim();
    groupMap.set(key, [...(groupMap.get(key) ?? []), lot]);
  }
  return [...groupMap.entries()]
    .sort(([left], [right]) => !left ? 1 : !right ? -1 : warehouseCollator.compare(left, right))
    .map(([groupKey, groupLots]) => {
      const articleMap = new Map<string, WarehouseLot[]>();
      for (const lot of groupLots) {
        const key = articleKey(lot);
        articleMap.set(key, [...(articleMap.get(key) ?? []), lot]);
      }
      return {
        key: `${groupBy}:${groupKey}`,
        label: !groupBy ? "" : groupKey || ({ type: "Без типа", place: "Без места", name: "Без наименования", series: "Без серии" })[groupBy],
        articles: [...articleMap.entries()]
          .sort(([left], [right]) => warehouseCollator.compare(left, right))
          .map(([key, articleLots]) => ({ key: `${groupBy}:${groupKey}:${key}`, article: articleLots[0].article || articleLots[0].name, lots: articleLots })),
      };
    });
}

function distinct(lots: WarehouseLot[], field: keyof WarehouseLot) {
  return [...new Set(lots.map((lot) => String(lot[field] ?? "")).filter(Boolean))].join(" · ");
}

function quantity(lots: WarehouseLot[], field: "balance" | "available") {
  return String(Math.round(lots.reduce((sum, lot) => sum + Number(lot[field]), 0) * 1_000_000) / 1_000_000);
}

function ArticleGroup({ group, expanded, toggle, busy, disabled, mutate, onMove }: {
  group: WarehouseGroup;
  expanded: Set<string>;
  toggle: (key: string) => void;
  busy: string;
  disabled: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
  onMove: (lot: WarehouseLot) => void;
}) {
  return <>
    {group.label && <tr className="stock-group-row"><td colSpan={12}>{group.label} · {group.articles.length}</td></tr>}
    {group.articles.map((item) => {
      const open = expanded.has(item.key);
      return <Fragment key={item.key}>
        <tr className="stock-article-row">
          <td>{distinct(item.lots, "item_type") || "—"}</td>
          <td><button className="link-button table-link" aria-expanded={open} onClick={() => toggle(item.key)}>{open ? "Скрыть" : "Показать"} партии ({item.lots.length})</button><small>{distinct(item.lots, "grn")}</small></td>
          <td className="num"><strong>{quantity(item.lots, "balance")}</strong></td>
          <td className="num"><strong>{quantity(item.lots, "available")}</strong></td>
          <td>{distinct(item.lots, "unit")}</td>
          <td>{distinct(item.lots, "storage_location") || "—"}</td>
          <td>{distinct(item.lots, "series") || "—"}</td>
          <td className="text"><strong>{item.article || "Без артикула"}</strong></td>
          <td className="text">{distinct(item.lots, "name") || "—"}</td>
          <td>{distinct(item.lots, "purpose") || "—"}</td>
          <td>{distinct(item.lots, "warehouse")}</td>
          <td />
        </tr>
        {open && item.lots.map((lot) => <WarehouseRow key={`${lot.id}:${lot.version}`} lot={lot} busy={busy === lot.id} disabled={disabled} mutate={mutate} onMove={onMove} />)}
      </Fragment>;
    })}
  </>;
}

function ManualStockForm({ busy, knownLots, onCancel, onAdded }: {
  busy: boolean;
  knownLots: WarehouseLot[];
  onCancel: () => void;
  onAdded: (item: {
    warehouse: string;
    article: string;
    series: string;
    name: string;
    item_type: string;
    purpose: string;
    unit: string;
    quantity: string;
    storage_location: string;
  }) => Promise<void>;
}) {
  const [warehouse, setWarehouse] = useState("ЦАР");
  const [article, setArticle] = useState("");
  const [series, setSeries] = useState("");
  const [name, setName] = useState("");
  const [itemType, setItemType] = useState("");
  const [purpose, setPurpose] = useState("Склад");
  const [unit, setUnit] = useState("шт");
  const [quantity, setQuantity] = useState("");
  const [storageLocation, setStorageLocation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [articleSource, setArticleSource] = useState("");

  useEffect(() => {
    const currentArticle = article.trim();
    if (!currentArticle) {
      setArticleSource("");
      return;
    }

    const key = currentArticle.replace(/\s+/g, " ").toLocaleLowerCase("ru");
    const known = knownLots.find((lot) => lot.article.trim().replace(/\s+/g, " ").toLocaleLowerCase("ru") === key);
    if (known) {
      setName((current) => current || known.name);
      setSeries((current) => current || known.series);
      setItemType((current) => current || known.item_type);
      setUnit((current) => current || known.unit);
      setArticleSource("Подставлено по уже внесённой партии");
    }

    let cancelled = false;
    const timer = window.setTimeout(() => {
      void warehouseApi.articleInfo(currentArticle).then(({ info }) => {
        if (cancelled || !info) return;
        setName((current) => current || info.name);
        setSeries((current) => current || info.series);
        setItemType((current) => current || info.item_type);
        setArticleSource(`Подставлено: ${info.source}. Проверьте и при необходимости поправьте.`);
      }).catch(() => undefined);
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [article, knownLots]);

  return (
    <form className="panel manual-stock-form" onSubmit={async (event) => {
      event.preventDefault();
      if (submitting) return;
      setSubmitting(true);
      try {
        await onAdded({
          warehouse,
          article: article.trim(),
          series: series.trim(),
          name: name.trim(),
          item_type: itemType.trim(),
          purpose: purpose.trim(),
          unit: unit.trim(),
          quantity,
          storage_location: storageLocation.trim(),
        });
      } finally {
        setSubmitting(false);
      }
    }}>
      <div className="manual-stock-head"><h2>Быстрый ввод</h2><button type="button" className="link-button" onClick={onCancel}>Отмена</button></div>
      <div className="manual-stock-grid">
        <label>Артикул<input required value={article} onChange={(event) => {
          setArticleSource("");
          setArticle(event.target.value);
        }} /></label>
        <label>Наименование<input value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label>Серия<input value={series} onChange={(event) => setSeries(event.target.value)} /></label>
        <label>Тип<input value={itemType} onChange={(event) => setItemType(event.target.value)} /></label>
        <label>Количество<input aria-label="Количество для внесения" type="number" min="0.000001" step="any" required value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
        <label>Единица<input required value={unit} onChange={(event) => setUnit(event.target.value)} /></label>
        <label>Склад<select value={warehouse} onChange={(event) => setWarehouse(event.target.value)}><option value="ЦАР">ЦАР</option><option value="ЖУК">ЖУК</option></select></label>
        <label>Назначение<input required value={purpose} onChange={(event) => setPurpose(event.target.value)} /></label>
        <label>Место хранения<input aria-label="Место хранения для внесения" placeholder="1F4" required value={storageLocation} onChange={(event) => setStorageLocation(event.target.value)} /></label>
      </div>
      {articleSource && <p className="page-sub" role="status">{articleSource}</p>}
      <button className="primary" disabled={busy || submitting}>{submitting ? "Вносим…" : "Внести"}</button>
    </form>
  );
}

function MoveStockForm({ preset, busy, mutate, onClose }: {
  preset: WarehouseLot | null;
  busy: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<"lot" | "location">(preset ? "lot" : "location");
  const [warehouse, setWarehouse] = useState(preset?.warehouse ?? "ЦАР");
  const [source, setSource] = useState(preset?.grn ?? "");
  const [packageNumber, setPackageNumber] = useState(preset?.package ?? "");
  const [target, setTarget] = useState("");
  const [targetWarehouse, setTargetWarehouse] = useState("");
  const [error, setError] = useState("");
  const transfer = mode === "lot" && Boolean(targetWarehouse) && targetWarehouse !== warehouse;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      if (mode === "location") {
        await mutate("move-location", () => warehouseApi.moveLocation(warehouse, source, target));
      } else {
        let selected = preset?.grn === source.trim() && preset.package === packageNumber.trim() && preset.warehouse === warehouse ? preset : null;
        if (!selected) {
          const result = await warehouseApi.list({ query: source.trim(), includeZero: false, warehouse });
          selected = result.lots.find((item) => item.grn === source.trim() && item.package === packageNumber.trim()) ?? null;
        }
        if (!selected) throw new Error("Партия с таким ГРН и пакетом не найдена");
        await mutate(selected.id, () => warehouseApi.move(selected.id, selected.version, target.trim(), transfer ? targetWarehouse : ""));
      }
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Перемещение не выполнено");
    }
  }

  return <form className="panel manual-stock-form" onSubmit={(event) => void submit(event)}>
    <div className="manual-stock-head"><h2>Перемещение</h2><button type="button" className="link-button" onClick={onClose}>Отмена</button></div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <div className="manual-stock-grid">
      <label>Что переносим<select aria-label="Что переносим" value={mode} onChange={(event) => {
        const next = event.target.value as "lot" | "location";
        setMode(next);
        setSource(next === "lot" ? preset?.grn ?? "" : preset?.storage_location ?? "");
      }}><option value="lot">Одну партию по ГРН</option><option value="location">Всё место целиком</option></select></label>
      <label>Склад<select aria-label="Склад перемещения" value={warehouse} onChange={(event) => { setWarehouse(event.target.value); setTargetWarehouse(""); }}><option value="ЦАР">ЦАР</option><option value="ЖУК">ЖУК</option></select></label>
      <label>{mode === "lot" ? "ГРН" : "Откуда"}<input aria-label={mode === "lot" ? "ГРН для перемещения" : "Исходное место"} required value={source} onChange={(event) => setSource(event.target.value)} /></label>
      {mode === "lot" && <label>Пакет<input aria-label="Пакет для перемещения" value={packageNumber} onChange={(event) => setPackageNumber(event.target.value)} /></label>}
      {mode === "lot" && <label>На склад<select aria-label="Склад назначения" value={targetWarehouse} onChange={(event) => setTargetWarehouse(event.target.value)}><option value="">Тот же склад</option>{["ЦАР", "ЖУК"].filter((item) => item !== warehouse).map((item) => <option key={item} value={item}>{item}</option>)}</select></label>}
      <label>Куда<input aria-label="Новое место" placeholder={transfer ? "прежнее место" : "20B5"} required={!transfer} value={target} onChange={(event) => setTarget(event.target.value)} /></label>
    </div>
    {transfer && <p className="page-sub">Партия уедет на {targetWarehouse} целиком: с остатком и историей списаний. Пустое место — останется прежнее.</p>}
    <button className="primary" disabled={busy}>{busy ? "Перемещаем…" : "Переместить"}</button>
  </form>;
}

function WarehouseRow({ lot, busy, disabled, mutate, onMove }: {
  lot: WarehouseLot;
  busy: boolean;
  disabled: boolean;
  mutate: (key: string, operation: () => Promise<unknown>) => Promise<void>;
  onMove: (lot: WarehouseLot) => void;
}) {
  const [open, setOpen] = useState(false);
  const [quantity, setQuantity] = useState("");
  const [reason, setReason] = useState("");
  const [article, setArticle] = useState(lot.article);
  const [name, setName] = useState(lot.name);
  const [series, setSeries] = useState(lot.series);
  const [place, setPlace] = useState(lot.storage_location);

  function writeoff(event: FormEvent) {
    event.preventDefault();
    void mutate(lot.id, () => warehouseApi.writeOff(lot.id, lot.version, quantity, reason)).catch(() => undefined);
  }

  return (
    <>
      <tr>
        <td>{lot.item_type || "—"}</td><td><strong>{lot.grn}{lot.package ? ` · ${lot.package}` : ""}</strong></td><td className="num">{lot.balance}</td><td className="num">{lot.available}</td><td>{lot.unit}</td><td>{lot.storage_location || "без места"}</td><td>{lot.series || "—"}</td><td className="text"><strong>{lot.article || "—"}</strong></td><td className="text">{lot.name || "—"}</td><td>{lot.purpose || "—"}</td><td>{lot.warehouse}</td>
        <td><button className="link-button table-link" onClick={() => setOpen((value) => !value)}>Операции</button></td>
      </tr>
      {open && <tr className="stock-actions-row"><td colSpan={12}><div className="stock-actions">
        <button className="secondary small" disabled={disabled} onClick={() => onMove(lot)}>{busy ? "Сохраняем…" : "Переместить"}</button>
        <form className="inline-action" onSubmit={(event) => {
          event.preventDefault();
          void mutate(lot.id, () => warehouseApi.editIdentity(lot.id, lot.version, article.trim(), name.trim(), series.trim())).catch(() => undefined);
        }}>
          <label>Артикул<input required value={article} onChange={(event) => setArticle(event.target.value)} /></label><label>Наименование<input value={name} onChange={(event) => setName(event.target.value)} /></label><label>Серия<input value={series} onChange={(event) => setSeries(event.target.value)} /></label><button className="secondary small" disabled={disabled}>Сохранить карточку</button>
        </form>
        <form className="inline-action" onSubmit={(event) => {
          event.preventDefault();
          void mutate(lot.id, () => warehouseApi.correctLocation(lot.id, lot.version, place.trim())).catch(() => undefined);
        }}>
          <label>Место как есть<input aria-label="Место как есть" title="Ручная правка: любой текст (12C7, 12C7B, A-01…), пусто — без места" placeholder="без места" value={place} onChange={(event) => setPlace(event.target.value)} /></label><button className="secondary small" disabled={disabled}>Записать место</button>
        </form>
        <form className="inline-action" onSubmit={writeoff}>
          <label>Количество<input type="number" min="0.000001" step="any" required value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label><label>Основание<input required value={reason} onChange={(event) => setReason(event.target.value)} /></label><button className="primary small" disabled={disabled}>Списать</button>
        </form>
      </div></td></tr>}
    </>
  );
}
