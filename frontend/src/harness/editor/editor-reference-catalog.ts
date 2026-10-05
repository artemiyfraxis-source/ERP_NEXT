import { catalogOuterDiameter } from "./drawing-thickness";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LocalSession } from "../local-session";
import {
  createReferenceCatalogApi,
  ReferenceCatalogApiError,
  type ReferenceCatalogSearchRecord,
} from "../reference-catalog-api";
import type { RuntimeConfig } from "../runtime-config";
import {
  createComponentTemplateApi,
  type ComponentTemplateSummary,
} from "../component-library/component-template-api";
import { builtInConnectorTemplates } from "./connector-series-demo";
import { normalizeWireStripProfileBinding } from "./model";
import { wireDatabaseOption, formatWireSection, builtInWireOptions, isSelectableWirePayload, type WireDatabaseOption } from "./wire-database";
import type {
  CoaxTerminationCatalogCandidate,
  CoaxTerminationCatalogDiagnostic,
  CoaxTerminationCatalogLayer,
  EditorCatalogItem,
  EditorCatalogSource,
} from "./editor-types";

interface RemoteCatalogSource extends EditorCatalogSource {
  readonly catalogSourceId?: string;
  readonly entityTypes: readonly string[];
  readonly accent: string;
}

const localSource: EditorCatalogSource = {
  id: "built-in-connectors",
  label: "Соединители",
  description: "Универсальные соединители до подключения профильной базы",
};

const componentLibrarySource: EditorCatalogSource = {
  id: "component-library",
  label: "Библиотека",
  description: "Версионируемые компоненты с общими видами Э4 и чертежа",
};

export const remoteEditorCatalogSources: readonly RemoteCatalogSource[] = [
  { id: "technology-wires", label: "Провода", description: "Каталог проводов E3:AJ: марка и комплексное сечение", entityTypes: ["wire", "cable"], accent: "#356c88" },
  { id:"technology-protection", catalogSourceId:"technology-database", label:"Защита", description:"Защитные покрытия из опубликованного справочника: тип protective-covering", entityTypes:["protective-covering"], accent:"#758087" },
  {
    id: "technology-database",
    label: "Провода (универсальная база)",
    description: "Провода и кабели из универсально опубликованного справочника",
    entityTypes: ["wire", "cable", "coax-cable"],
    accent: "#356c88",
  },
  {
    id: "technology-terminals",
    label: "Терминалы",
    description: "Опубликованный справочник БД.ТЕР",
    entityTypes: ["terminal"],
    accent: "#8a6635",
  },
  {
    id: "technology-coax-cables",
    label: "Кабели",
    description: "Диаметры коаксиальных кабелей из СПР.КАБ",
    entityTypes: ["coax-cable"],
    accent: "#596d78",
  },
  {
    id: "technology-coax-terminations",
    label: "Разделка коаксиала",
    description: "Послойные диаметры и длины разделки из БД.КОАКС",
    entityTypes: ["coax-termination"],
    accent: "#6d5b78",
  },
];

export const editorCatalogSources: readonly EditorCatalogSource[] = [
  componentLibrarySource,
  localSource,
  ...remoteEditorCatalogSources,
];

export const builtInConnectorItems: readonly EditorCatalogItem[] = builtInConnectorTemplates.map((template) => ({
  id: template.id,
  title: template.title,
  subtitle: template.description,
  category: "Соединители",
  accent: template.kind === "series" ? "#3f718b" : "#71808a",
  placement: "connector",
  templateKind: template.kind,
  ...(template.kind === "series" ? {
    seriesId: template.seriesId,
    defaultPartNumber: template.defaultPartNumber,
  } : {}),
}));

function displayValue(value: unknown): string | null {
  if (typeof value === "string") return value.trim() || null;
  if (typeof value === "number" && Number.isFinite(value)) return String(value).replace(".", ",");
  return null;
}

function firstValue(payload: Readonly<Record<string, unknown>>, ...keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = displayValue(payload[key]);
    if (value) return value;
  }
  return null;
}

function range(
  payload: Readonly<Record<string, unknown>>,
  fromKey: string,
  toKey: string,
  unit: string,
): string | null {
  const from = displayValue(payload[fromKey]);
  const to = displayValue(payload[toKey]);
  if (from && to) return from === to ? `${from} ${unit}` : `${from}–${to} ${unit}`;
  if (from) return `от ${from} ${unit}`;
  if (to) return `до ${to} ${unit}`;
  return null;
}

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const sha256Pattern = /^[0-9a-f]{64}$/i;

function positiveDecimal(value: unknown): number | null {
  if (typeof value === "string") {
    const normalized = value.trim().replace(",", ".");
    if (!normalized || normalized === "-" || normalized === "—") return null;
    value = normalized;
  }
  if (typeof value !== "number" && typeof value !== "string") return null;
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}

function positiveIndex(value: unknown): number | null {
  const number = typeof value === "number" ? value :
    typeof value === "string" && value.trim() ? Number(value.trim()) : Number.NaN;
  return Number.isSafeInteger(number) && number > 0 ? number : null;
}

/** Converts one published БД.КОАКС row into a future bindable value without
 * hiding partial source data. Indexes are never compacted: [1, 3] stays [1, 3]. */
export function normalizeCoaxTerminationCatalogCandidate(
  sourceId: string,
  record: ReferenceCatalogSearchRecord,
  snapshot?: { readonly snapshotId: string; readonly snapshotSha256: string },
): CoaxTerminationCatalogCandidate {
  const diagnostics: CoaxTerminationCatalogDiagnostic[] = [];
  const layers: CoaxTerminationCatalogLayer[] = [];
  const rawLayers = Array.isArray(record.payload.layers) ? record.payload.layers : [];

  for (const rawLayer of rawLayers) {
    if (typeof rawLayer !== "object" || rawLayer === null || Array.isArray(rawLayer)) {
      diagnostics.push({ code: "layer-invalid", message: "Слой разделки имеет неверный формат." });
      continue;
    }
    const layer = rawLayer as Readonly<Record<string, unknown>>;
    const index = positiveIndex(layer.index);
    if (index === null) {
      diagnostics.push({ code: "layer-invalid", message: "У слоя разделки не указан корректный индекс." });
      continue;
    }
    const diameterMm = positiveDecimal(layer.diameterMm);
    const stripLengthMm = positiveDecimal(layer.stripLengthMm);
    layers.push({ index, diameterMm, stripLengthMm });
    if (diameterMm === null) diagnostics.push({
      code: "layer-diameter-missing", layerIndex: index,
      message: `Для слоя D${index} не указан диаметр.`,
    });
    if (stripLengthMm === null) diagnostics.push({
      code: "layer-strip-length-missing", layerIndex: index,
      message: `Для слоя L${index} не указана длина разделки.`,
    });
  }

  if (layers.length === 0) diagnostics.push({
    code: "layers-missing", message: "В записи нет активных слоёв разделки.",
  });
  const indexes = new Set<number>();
  for (const layer of layers) {
    if (indexes.has(layer.index)) diagnostics.push({
      code: "layer-index-duplicate", layerIndex: layer.index,
      message: `Индекс слоя ${layer.index} повторяется.`,
    });
    indexes.add(layer.index);
  }

  const hasRecordIdentity = sourceId.trim() !== "" && record.entityType === "coax-termination" &&
    record.sourceKey.trim() !== "" && sha256Pattern.test(record.recordId);
  if (!hasRecordIdentity) diagnostics.push({
    code: "record-identity-invalid",
    message: "Запись разделки не содержит точную идентификацию источника.",
  });
  const hasSnapshotIdentity = snapshot !== undefined &&
    uuidPattern.test(snapshot.snapshotId) && sha256Pattern.test(snapshot.snapshotSha256);
  if (!hasSnapshotIdentity) diagnostics.push({
    code: "snapshot-identity-missing",
    message: "Для привязки требуется точная версия опубликованного справочника.",
  });

  let binding = diagnostics.length === 0 && snapshot ? {
    sourceId,
    snapshotId: snapshot.snapshotId,
    snapshotSha256: snapshot.snapshotSha256,
    recordId: record.recordId,
    entityType: "coax-termination" as const,
    sourceKey: record.sourceKey,
    layers: layers.map((layer) => ({
      index: layer.index,
      diameterMm: layer.diameterMm!,
      stripLengthMm: layer.stripLengthMm!,
    })),
  } : null;
  if (binding) {
    try {
      normalizeWireStripProfileBinding({ ...binding, displayName: record.sourceKey });
    } catch (error) {
      diagnostics.push({
        code: "layer-invalid",
        message: error instanceof Error ? error.message : "Профиль разделки имеет неверные значения.",
      });
      binding = null;
    }
  }
  return { state: binding ? "ready" : "incomplete", layers, diagnostics, binding };
}

export function referenceRecordToEditorCatalogItem(
  source: RemoteCatalogSource,
  record: ReferenceCatalogSearchRecord,
  snapshot?: { readonly snapshotId: string; readonly snapshotSha256: string },
): EditorCatalogItem {
  const payload = record.payload;
  const details: string[] = [];
  const coaxTerminationCandidate = record.entityType === "coax-termination"
    ? normalizeCoaxTerminationCatalogCandidate(source.id, record, snapshot)
    : undefined;
  if (record.entityType === "terminal") {
    const family = firstValue(payload, "productName", "series", "connectorType", "manufacturer");
    const section = range(payload, "sectionFromMm2", "sectionToMm2", "мм²") ??
      range(payload, "awgFrom", "awgTo", "AWG");
    const insulation = range(payload, "insulationDiameterFromMm", "insulationDiameterToMm", "мм Ø изоляции");
    const strip = firstValue(payload, "stripLengthMm");
    if (family) details.push(family);
    if (section) details.push(section);
    if (insulation) details.push(insulation);
    if (strip) details.push(`зачистка ${strip} мм`);
  } else if (record.entityType === "coax-cable") {
    const layers = Array.isArray(payload.layers) ? payload.layers : [];
    const diameters = layers.flatMap((layer) => {
      if (typeof layer !== "object" || layer === null || Array.isArray(layer)) return [];
      const item = layer as Record<string, unknown>;
      const index = displayValue(item.index);
      const diameter = displayValue(item.diameterMm);
      return index && diameter ? [`D${index} ${diameter} мм`] : [];
    });
    details.push(...diameters.slice(0, 3));
  } else if (record.entityType === "coax-termination") {
    for (const layer of coaxTerminationCandidate?.layers ?? []) {
      const diameter = layer.diameterMm === null ? `D${layer.index} —` : `D${layer.index} ${displayValue(layer.diameterMm)} мм`;
      const length = layer.stripLengthMm === null ? `L${layer.index} —` : `L${layer.index} ${displayValue(layer.stripLengthMm)} мм`;
      details.push(`${diameter} / ${length}`);
    }
    if (coaxTerminationCandidate?.state === "incomplete") details.push("данные неполные");
  } else if (record.entityType === "awg-reference") {
    // Read-only rendering for old snapshots; this source is hidden from new
    // imports and selection.
    const section = firstValue(payload, "sectionMm2");
    const conductor = firstValue(payload, "conductorDiameterMm");
    if (section) details.push(`${section} мм²`);
    if (conductor) details.push(`Ø жилы ${conductor} мм`);
  } else if (record.entityType === "wire" || record.entityType === "cable") {
    const name = firstValue(payload, "name", "Название", "mark", "Марка", "series", "Серия");
    const section = formatWireSection(payload);
    const color = firstValue(payload, "color", "Цвет");
    if (name) details.push(name);
    if (section) details.push(section);
    if (color) details.push(color);
  } else if (record.entityType === "protective-covering") {
    const name = firstValue(payload, "name", "Название", "Наименование", "productName", "series", "Серия", "mark", "Марка");
    const size = firstValue(payload, "size", "Размер", "crossSection", "section", "Сечение", "wallThicknessMm", "thicknessMm", "diameterMm", "innerDiameterMm");
    const color = firstValue(payload, "color", "Цвет");
    if (name) details.push(name);
    if (size) details.push(size);
    if (color) details.push(color);
  } else {
    const name = firstValue(payload, "name", "productName", "series", "manufacturer");
    if (name) details.push(name);
  }
  return {
    id: `reference:${source.id}:${record.recordId}`,
    title: source.id === "technology-wires" ? wireDatabaseOption(record).label : record.sourceKey,
    subtitle: details.length > 0 ? details.join(" · ") : `${source.label} · характеристики не заполнены`,
    category: source.label,
    accent: source.accent,
    placement: "reference-only",
    sourceId: source.id,
    snapshotId: snapshot?.snapshotId,
    snapshotSha256: snapshot?.snapshotSha256,
    recordId: record.recordId,
    sourceKey: record.sourceKey,
    entityType: record.entityType,
    referenceDisplayName: record.entityType === "wire" || record.entityType === "cable"
      ? source.id === "technology-wires" ? wireDatabaseOption(record).label
        : firstValue(payload, "name", "Название", "mark", "Марка", "series", "Серия") ?? record.sourceKey
      : record.entityType === "protective-covering"
        ? [...new Set([firstValue(payload, "name", "Название", "Наименование", "productName", "series", "Серия", "mark", "Марка"), firstValue(payload, "size", "Размер", "crossSection", "section", "Сечение", "wallThicknessMm", "thicknessMm", "diameterMm", "innerDiameterMm"), firstValue(payload, "color", "Цвет")].filter((value): value is string => !!value))].join(" · ") || record.sourceKey
      : undefined,
    outerDiameterMm:catalogOuterDiameter(payload),
    coaxTerminationCandidate,
  };
}

export function filterBuiltInConnectors(query: string): readonly EditorCatalogItem[] {
  const normalized = query.trim().toLocaleLowerCase("ru");
  if (!normalized) return builtInConnectorItems;
  return builtInConnectorItems.filter((item) =>
    `${item.title} ${item.subtitle}`.toLocaleLowerCase("ru").includes(normalized));
}

export function componentTemplateSummaryToEditorCatalogItem(
  template: ComponentTemplateSummary,
): EditorCatalogItem {
  return componentTemplateSummaryToEditorCatalogItems(template)[0]!;
}

export function componentTemplateSummaryToEditorCatalogItems(
  template: ComponentTemplateSummary,
): readonly EditorCatalogItem[] {
  const articleCount = template.articleBindings.length;
  const articleRemainder100 = articleCount % 100;
  const articleRemainder10 = articleCount % 10;
  const articleWord = articleRemainder100 >= 11 && articleRemainder100 <= 14
    ? "артикулов"
    : articleRemainder10 === 1 ? "артикул"
      : articleRemainder10 >= 2 && articleRemainder10 <= 4 ? "артикула" : "артикулов";
  return [{
    id: `component-template:${template.templateId}:${template.version}`,
    title: template.code,
    subtitle: articleCount > 0
      ? `${template.name} · ${articleCount} ${articleWord} · версия ${template.version}`
      : `${template.name} · версия ${template.version}`,
    category: componentLibrarySource.label,
    accent: "#496b88",
    placement: "connector",
    componentTemplateId: template.templateId,
    componentTemplateVersion: template.version,
    componentArticles: template.articleBindings,
  }];
}

export function filterComponentTemplates(
  templates: readonly ComponentTemplateSummary[],
  query: string,
): readonly EditorCatalogItem[] {
  const normalized = query.trim().toLocaleLowerCase("ru");
  return templates
    .flatMap(componentTemplateSummaryToEditorCatalogItems)
    .filter((item) => !normalized || [
      item.title,
      item.subtitle,
      ...(item.componentArticles ?? []).map((article) => article.articleKey),
    ].join(" ").toLocaleLowerCase("ru").includes(normalized));
}

type CatalogLoadState = "idle" | "loading" | "loading-more" | "ready" | "unpublished" | "error";

export interface EditorReferenceCatalogState {
  readonly sources: readonly EditorCatalogSource[];
  readonly selectedSourceId: string;
  readonly query: string;
  readonly items: readonly EditorCatalogItem[];
  readonly loadState: CatalogLoadState;
  readonly message: string | null;
  readonly hasMore: boolean;
  readonly selectSource: (sourceId: string) => void;
  readonly changeQuery: (query: string) => void;
  readonly loadMore: () => void;
  readonly retry: () => void;
}

export interface TerminalArticleLookupState {
  readonly articles: readonly string[];
  readonly records: readonly ReferenceCatalogSearchRecord[];
  readonly search: (query: string) => void;
}

/** Searches the active БД.ТЕР snapshot independently of the catalog tab.
 * The inline free connector keeps manual input, while the datalist can query
 * the whole published terminal reference as the operator types. */
export function useTerminalArticleLookup(
  config: RuntimeConfig,
  session: LocalSession,
): TerminalArticleLookupState {
  const api = useMemo(() => createReferenceCatalogApi(config, session), [config, session]);
  const [query, setQuery] = useState<string | null>(null);
  const [debouncedQuery, setDebouncedQuery] = useState<string | null>(null);
  const [articles, setArticles] = useState<readonly string[]>([]);
  const [records, setRecords] = useState<readonly ReferenceCatalogSearchRecord[]>([]);
  const generation = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    void api.getActive("technology-terminals").then(snapshot => {
      if (controller.signal.aborted || !snapshot) return;
      setRecords(snapshot.records.filter(record => record.entityType === "terminal"));
      setArticles(snapshot.records.filter(record => record.entityType === "terminal").map(record => record.sourceKey));
    }).catch(() => undefined);
    return () => controller.abort();
  }, [api]);

  useEffect(() => {
    if (query === null) return;
    const timer = window.setTimeout(() => setDebouncedQuery(query), 220);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (debouncedQuery === null) return;
    const currentGeneration = ++generation.current;
    const controller = new AbortController();
    void api.searchCatalog("technology-terminals", {
      text: debouncedQuery.trim() || null,
      exactSourceKey: null,
      entityTypes: ["terminal"],
      filters: [],
      filterLogic: "all",
      sort: debouncedQuery.trim() ? "relevance" : "source-key-asc",
      pageSize: 30,
      cursor: null,
    }, controller.signal).then((page) => {
      if (generation.current !== currentGeneration) return;
      setRecords(current => [...new Map([...current, ...page.items].map(item => [item.sourceKey, item])).values()]);
      setArticles([...new Set(page.items.map((item) => item.sourceKey))]);
    }).catch(() => undefined);
    return () => controller.abort();
  }, [api, debouncedQuery]);

  return { articles, records, search: setQuery };
}

const pageSize = 30;

/** Debounced lookup stays independent from the currently selected side catalog. */
export function useWireDatabaseLookup(config: RuntimeConfig, session: LocalSession) {
  const api = useMemo(() => createReferenceCatalogApi(config, session), [config, session]);
  const [query, setQuery] = useState<string | null>(null);
  const [options, setOptions] = useState<readonly WireDatabaseOption[]>(builtInWireOptions);
  const [databaseOptions, setDatabaseOptions] = useState<readonly WireDatabaseOption[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void api.getActive("technology-wires").then(snapshot => {
      if (controller.signal.aborted) return;
      if (!snapshot) {
        setDatabaseOptions([]);
        setOptions(builtInWireOptions);
        setMessage("Каталог проводов E3:AJ ещё не загружен. Откройте «Справочники» → «Подключить базу проводов».");
        return;
      }
      const next = snapshot.records.filter(record => record.entityType === "wire" && isSelectableWirePayload(record.payload))
        .map(record => wireDatabaseOption(record, { snapshotId: snapshot.snapshotId, snapshotSha256: snapshot.sha256 }));
      setDatabaseOptions(next);
      setOptions(next.length ? next : builtInWireOptions);
      setMessage(next.length ? null : "Каталог проводов пуст. Загрузите лист «Провода», диапазон E3:AJ.");
    }).catch(() => {
      if (!controller.signal.aborted) {
        setDatabaseOptions([]);
        setMessage("Не удалось прочитать каталог проводов. Проверьте справочник в разделе «Справочники».");
      }
    });
    return () => controller.abort();
  }, [api]);
  useEffect(() => {
    if (query === null) return;
    if (!query.trim()) {
      setOptions(databaseOptions.length ? databaseOptions : builtInWireOptions);
      setMessage(null);
      return;
    }
    const controller = new AbortController();
    setOptions([]);
    setMessage("Поиск в базе проводов…");
    const timer = window.setTimeout(() => {
      void api.searchCatalog("technology-wires", { text: query.trim() || null, exactSourceKey: null,
        entityTypes: ["wire"], filters: [], filterLogic: "all", sort: query.trim() ? "relevance" : "source-key-asc",
        pageSize: 50, cursor: null }, controller.signal).then(page => {
        if (controller.signal.aborted) return;
        setOptions(page.items.filter(item => isSelectableWirePayload(item.payload)).map(item => wireDatabaseOption(item, page)));
        setMessage(page.items.length === 0 ? "Совпадений в базе нет. Можно ввести марку и сечение вручную." : page.nextCursor ? "Показаны первые 50 вариантов. Уточните поиск." : null);
      }).catch(error => {
        if (controller.signal.aborted) return;
        setOptions(builtInWireOptions);
        setMessage(error instanceof ReferenceCatalogApiError && error.code === "catalog_active_snapshot_not_found"
          ? "База проводов ещё не загружена. Доступны встроенные варианты и ручной ввод."
          : "Не удалось прочитать базу проводов. Доступны встроенные варианты и ручной ввод.");
      });
    }, 220);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [api, databaseOptions, query]);
  return { options, databaseOptions, message, search: setQuery };
}

export function useEditorReferenceCatalog(
  config: RuntimeConfig,
  session: LocalSession,
): EditorReferenceCatalogState {
  const api = useMemo(() => createReferenceCatalogApi(config, session), [config, session]);
  const componentApi = useMemo(() => createComponentTemplateApi(config, session), [config, session]);
  const [selectedSourceId, setSelectedSourceId] = useState(componentLibrarySource.id);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [remoteItems, setRemoteItems] = useState<readonly EditorCatalogItem[]>([]);
  const [componentTemplates, setComponentTemplates] = useState<readonly ComponentTemplateSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadState, setLoadState] = useState<CatalogLoadState>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const requestGeneration = useRef(0);
  const source = remoteEditorCatalogSources.find((item) => item.id === selectedSourceId) ?? null;
  const isComponentLibrary = selectedSourceId === componentLibrarySource.id;

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!isComponentLibrary) return;
    const generation = ++requestGeneration.current;
    setComponentTemplates([]);
    setNextCursor(null);
    setLoadState("loading");
    setMessage(null);
    void componentApi.list().then((templates) => {
      if (requestGeneration.current !== generation) return;
      setComponentTemplates(templates);
      setLoadState("ready");
    }).catch((error: unknown) => {
      if (requestGeneration.current !== generation) return;
      setLoadState("error");
      setMessage(error instanceof Error ? error.message : "Не удалось загрузить библиотеку компонентов.");
    });
  }, [componentApi, isComponentLibrary, reloadToken]);

  useEffect(() => {
    if (isComponentLibrary) return;
    if (!source) {
      requestGeneration.current += 1;
      setRemoteItems([]);
      setNextCursor(null);
      setLoadState("ready");
      setMessage(null);
      return;
    }
    const generation = ++requestGeneration.current;
    const controller = new AbortController();
    setRemoteItems([]);
    setNextCursor(null);
    setLoadState("loading");
    setMessage(null);
    void api.searchCatalog(source.catalogSourceId ?? source.id, {
      text: debouncedQuery.trim() || null,
      exactSourceKey: null,
      entityTypes: source.entityTypes,
      filters: [],
      filterLogic: "all",
      sort: debouncedQuery.trim() ? "relevance" : "source-key-asc",
      pageSize,
      cursor: null,
    }, controller.signal).then((page) => {
      if (requestGeneration.current !== generation) return;
      setRemoteItems(page.items.map((record) => referenceRecordToEditorCatalogItem({...source,id:source.catalogSourceId ?? source.id}, record, page)));
      setNextCursor(page.nextCursor);
      setLoadState("ready");
    }).catch((error: unknown) => {
      if (controller.signal.aborted || requestGeneration.current !== generation) return;
      if (error instanceof ReferenceCatalogApiError && error.code === "catalog_active_snapshot_not_found") {
        setLoadState("unpublished");
        setMessage(`Справочник «${source.label}» ещё не опубликован.`);
        return;
      }
      setLoadState("error");
      setMessage(error instanceof Error ? error.message : "Не удалось загрузить справочник.");
    });
    return () => controller.abort();
  }, [api, debouncedQuery, isComponentLibrary, reloadToken, source]);

  const loadMore = useCallback(() => {
    if (!source || !nextCursor || loadState === "loading" || loadState === "loading-more") return;
    const generation = ++requestGeneration.current;
    setLoadState("loading-more");
    setMessage(null);
    void api.searchCatalog(source.catalogSourceId ?? source.id, {
      text: debouncedQuery.trim() || null,
      exactSourceKey: null,
      entityTypes: source.entityTypes,
      filters: [],
      filterLogic: "all",
      sort: debouncedQuery.trim() ? "relevance" : "source-key-asc",
      pageSize,
      cursor: nextCursor,
    }).then((page) => {
      if (requestGeneration.current !== generation) return;
      setRemoteItems((current) => {
        const existing = new Set(current.map((item) => item.id));
        return [...current, ...page.items
          .map((record) => referenceRecordToEditorCatalogItem({...source,id:source.catalogSourceId ?? source.id}, record, page))
          .filter((item) => !existing.has(item.id))];
      });
      setNextCursor(page.nextCursor);
      setLoadState("ready");
    }).catch((error: unknown) => {
      if (requestGeneration.current !== generation) return;
      if (error instanceof ReferenceCatalogApiError && (
        error.code === "catalog_cursor_snapshot_changed" || error.code === "catalog_cursor_invalid"
      )) {
        setReloadToken((value) => value + 1);
        return;
      }
      setLoadState("error");
      setMessage(error instanceof Error ? error.message : "Не удалось загрузить следующую страницу.");
    });
  }, [api, debouncedQuery, loadState, nextCursor, source]);

  return {
    sources: editorCatalogSources,
    selectedSourceId,
    query,
    items: isComponentLibrary
      ? filterComponentTemplates(componentTemplates, query)
      : source ? remoteItems : filterBuiltInConnectors(query),
    loadState,
    message,
    hasMore: !isComponentLibrary && source !== null && nextCursor !== null,
    selectSource: (sourceId) => {
      if (editorCatalogSources.some((item) => item.id === sourceId)) {
        requestGeneration.current += 1;
        setSelectedSourceId(sourceId);
      }
    },
    changeQuery: (value) => {
      requestGeneration.current += 1;
      setQuery(value);
    },
    loadMore,
    retry: () => setReloadToken((value) => value + 1),
  };
}
