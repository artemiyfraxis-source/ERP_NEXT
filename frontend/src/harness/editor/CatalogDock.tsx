import { useMemo, useState, type DragEvent } from "react";
import type { EditorCatalogItem, EditorCatalogSource } from "./editor-types";

export interface CatalogDockProps {
  readonly items: readonly EditorCatalogItem[];
  readonly sources?: readonly EditorCatalogSource[];
  readonly selectedSourceId?: string;
  readonly query?: string;
  readonly loadState?: "idle" | "loading" | "loading-more" | "ready" | "unpublished" | "error";
  readonly message?: string | null;
  readonly hasMore?: boolean;
  readonly expanded: boolean;
  readonly onExpandedChange: (expanded: boolean) => void;
  readonly onActivate: (item: EditorCatalogItem) => void;
  readonly onSourceChange?: (sourceId: string) => void;
  readonly onQueryChange?: (query: string) => void;
  readonly onLoadMore?: () => void;
  readonly onRetry?: () => void;
}

export function CatalogDock({
  items,
  sources = [],
  selectedSourceId,
  query,
  loadState = "ready",
  message = null,
  hasMore = false,
  expanded,
  onExpandedChange,
  onActivate,
  onSourceChange,
  onQueryChange,
  onLoadMore,
  onRetry,
}: CatalogDockProps) {
  const [localSourceId, setLocalSourceId] = useState("");
  const [localQuery, setLocalQuery] = useState("");
  const effectiveSources = useMemo(() => sources.length > 0 ? sources : [
    ...new Set(items.map((item) => item.category)),
  ].map((category) => ({ id: category, label: category, description: category })), [items, sources]);
  const effectiveSourceId = selectedSourceId ?? (localSourceId || effectiveSources[0]?.id || "");
  const effectiveQuery = query ?? localQuery;
  const locallyFilteredItems = useMemo(() => {
    if (sources.length > 0) return items;
    const normalized = effectiveQuery.trim().toLocaleLowerCase("ru");
    return items.filter((item) =>
      (!effectiveSourceId || item.category === effectiveSourceId) &&
      (!normalized || `${item.title} ${item.subtitle}`.toLocaleLowerCase("ru").includes(normalized)));
  }, [effectiveQuery, effectiveSourceId, items, sources.length]);
  const visibleItems = sources.length > 0 ? items : locallyFilteredItems;

  const changeSource = (sourceId: string) => {
    if (!onSourceChange) setLocalSourceId(sourceId);
    onSourceChange?.(sourceId);
  };

  const changeQuery = (value: string) => {
    if (!onQueryChange) setLocalQuery(value);
    onQueryChange?.(value);
  };

  const beginDrag = (event: DragEvent<HTMLButtonElement>, item: EditorCatalogItem) => {
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData("application/x-techmap-catalog-item", item.id);
    event.dataTransfer.setData("text/plain", item.title);
  };

  return (
    <section className={expanded ? "he-catalog expanded" : "he-catalog collapsed"} aria-label="Каталог объектов">
      <header className="he-catalog-header">
        <button
          className="he-catalog-toggle"
          type="button"
          aria-expanded={expanded}
          onClick={() => onExpandedChange(!expanded)}
        >
          <span aria-hidden="true">{expanded ? "⌄" : "⌃"}</span>
          Объекты и материалы
          <small>{items.length}{hasMore ? "+" : ""}</small>
        </button>
        {expanded && (
          <>
            <label className="he-catalog-search">
              <span aria-hidden="true">⌕</span>
              <span className="visually-hidden">Поиск по каталогу</span>
              <input
                type="search"
                placeholder="Артикул, название или характеристика"
                value={effectiveQuery}
                onChange={(event) => changeQuery(event.target.value)}
              />
            </label>
            <span className="he-catalog-hint">Перетащите на поле или нажмите дважды</span>
          </>
        )}
      </header>
      {expanded && (
        <div className="he-catalog-content">
          <nav className="he-catalog-categories" aria-label="Категории каталога">
            {effectiveSources.map((source) => (
              <button
                type="button"
                className={source.id === effectiveSourceId ? "active" : ""}
                aria-pressed={source.id === effectiveSourceId}
                title={source.description}
                key={source.id}
                onClick={() => changeSource(source.id)}
              >{source.label}</button>
            ))}
          </nav>
          <div className="he-catalog-results" role="list" aria-label="Результаты каталога">
            {(loadState === "loading" || loadState === "idle") ? (
              <div className="he-catalog-empty" role="status">Ищем в опубликованном справочнике…</div>
            ) : message ? (
              <div className={`he-catalog-empty ${loadState}`} role={loadState === "error" ? "alert" : "status"}>
                <span>{message}</span>
                {(loadState === "error" || loadState === "unpublished") && onRetry && <button type="button" onClick={onRetry}>Повторить</button>}
              </div>
            ) : visibleItems.length === 0 ? (
              <div className="he-catalog-empty">Ничего не найдено. Измените запрос или справочник.</div>
            ) : <>
              {visibleItems.map((item) => (
              <button
                type="button"
                className="he-catalog-card"
                role="listitem"
                key={item.id}
                draggable={item.placement === "connector"}
                onDragStart={(event) => beginDrag(event, item)}
                onDoubleClick={() => onActivate(item)}
                title={item.placement === "reference-only" ? "Справочная позиция. Размещение будет подключено с соответствующим инструментом." : "Перетащить на поле"}
              >
                <span className="he-catalog-thumbnail" style={{ "--catalog-accent": item.accent } as React.CSSProperties} aria-hidden="true">▣</span>
                <span>
                  <strong>{item.title}</strong>
                  <small>{item.subtitle}</small>
                </span>
                <span className="he-catalog-add" aria-hidden="true">＋</span>
              </button>
              ))}
              {hasMore && (
                <button className="he-catalog-more" type="button" disabled={loadState === "loading-more"} onClick={onLoadMore}>
                  {loadState === "loading-more" ? "Загружаем…" : "Показать ещё"}
                </button>
              )}
            </>}
          </div>
        </div>
      )}
    </section>
  );
}
