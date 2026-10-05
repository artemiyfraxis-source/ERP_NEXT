import {
  createFreeConnectorInstance,
  defineConnectorSeriesLibrary,
  selectConnectorSeriesArticle,
  type ConnectorSeries,
} from "./connector-series";
import {
  createDefaultConnectorBaseColumns,
  defaultLayerIds,
  type ConnectorContact,
  type ConnectorInstance,
  type Point,
} from "./model";

export interface BuiltInSeriesConnectorTemplate {
  readonly id: string;
  readonly kind: "series";
  readonly title: string;
  readonly description: string;
  readonly seriesId: string;
  readonly defaultPartNumber: string;
}

export interface BuiltInFreeConnectorTemplate {
  readonly id: string;
  readonly kind: "free";
  readonly title: string;
  readonly description: string;
  readonly partNumber: string;
  readonly defaultContactCount: number;
}

export type BuiltInConnectorTemplate = BuiltInSeriesConnectorTemplate | BuiltInFreeConnectorTemplate;

export interface CreateBuiltInConnectorOptions {
  readonly id: string;
  readonly designation: string;
  readonly e4Position: Point;
  readonly drawingPosition?: Point;
  /** Overrides the default series article. Ignored by a free template. */
  readonly partNumber?: string;
  /** Overrides the initial row count of a free template. Ignored by a series template. */
  readonly freeContactCount?: number;
}

export interface CreateConnectorPlacementOptions {
  readonly id: string;
  readonly designation: string;
  readonly e4Position: Point;
  readonly drawingPosition?: Point;
}

/** Detailed demo data for terminals referenced by the JST XH series. */
export interface BuiltInTerminalReference {
  readonly article: string;
  readonly awgFrom: number;
  readonly awgTo: number;
  readonly sectionFromMm2: number;
  readonly sectionToMm2: number;
  readonly insulationDiameterFromMm: number;
  readonly insulationDiameterToMm: number;
}

export const builtInJstXhTerminalReferences: readonly BuiltInTerminalReference[] = [
  {
    article: "SXH-001T-P0.6N",
    awgFrom: 26,
    awgTo: 22,
    sectionFromMm2: 0.13,
    sectionToMm2: 0.33,
    insulationDiameterFromMm: 1.3,
    insulationDiameterToMm: 1.9,
  },
  {
    article: "SXH-002T-P0.6",
    awgFrom: 30,
    awgTo: 26,
    sectionFromMm2: 0.05,
    sectionToMm2: 0.13,
    insulationDiameterFromMm: 0.9,
    insulationDiameterToMm: 1.3,
  },
  {
    article: "SXH-001T-P0.6",
    awgFrom: 28,
    awgTo: 22,
    sectionFromMm2: 0.08,
    sectionToMm2: 0.33,
    insulationDiameterFromMm: 0.9,
    insulationDiameterToMm: 1.9,
  },
];

const jstXhTerminalArticles = builtInJstXhTerminalReferences.map(({ article }) => article);
const jstXhPartNumbers = [
  "XHP-1",
  "XHP-2",
  "XHP-2(10.0)-U",
  ...Array.from({ length: 4 }, (_, index) => `XHP-${index + 3}`),
  "XHP-6(5.0)-U",
  ...Array.from({ length: 10 }, (_, index) => `XHP-${index + 7}`),
  "XHP-20",
] as const;

function jstXhContactCount(partNumber: string): number {
  if (partNumber === "XHP-2(10.0)-U") return 2;
  if (partNumber === "XHP-6(5.0)-U") return 6;
  const count = /^XHP-(\d+)$/.exec(partNumber)?.[1];
  if (!count) throw new Error(`Не удалось определить число контактов JST XH для ${partNumber}.`);
  return Number(count);
}

/**
 * Demo reference data. Articles and compatibility lists are illustrative and
 * can later be replaced by the persistent library without changing instances.
 */
export const builtInConnectorSeries: readonly ConnectorSeries[] = defineConnectorSeriesLibrary([
  {
    id: "xs-demo-series",
    name: "Серия XS",
    thirdContactTypeLabel: "коаксиальный",
    articles: [
      {
        partNumber: "XS-04",
        contactCounts: { signal: 2, power: 1, third: 1 },
        allowedTerminalArticles: {
          signal: ["TERM-SIG-05", "TERM-SIG-075"],
          power: ["TERM-PWR-15", "TERM-PWR-25"],
          third: ["TERM-COAX-50", "TERM-COAX-75"],
        },
      },
      {
        partNumber: "XS-10",
        contactCounts: { signal: 6, power: 2, third: 2 },
        allowedTerminalArticles: {
          signal: ["TERM-SIG-05", "TERM-SIG-075", "TERM-SIG-10"],
          power: ["TERM-PWR-15", "TERM-PWR-25"],
          third: ["TERM-COAX-50", "TERM-COAX-75"],
        },
      },
    ],
  },
  {
    id: "jst-xh",
    name: "JST XH",
    thirdContactTypeLabel: "третий",
    articles: jstXhPartNumbers.map((partNumber) => ({
      partNumber,
      contactCounts: { signal: jstXhContactCount(partNumber), power: 0, third: 0 },
      allowedTerminalArticles: {
        signal: jstXhTerminalArticles,
        power: [],
        third: [],
      },
    })),
  },
]);

export const freeConnectorTemplateCatalogId = "catalog-connector-free";

export function connectorSeriesCatalogId(seriesId: string): string {
  return `catalog-connector-series:${requireText(seriesId, "ID серии")}`;
}

export const builtInFreeConnectorTemplate: BuiltInFreeConnectorTemplate = {
  id: freeConnectorTemplateCatalogId,
  kind: "free",
  title: "Свободный соединитель",
  description: "Независимые строки и терминалы из всей базы",
  partNumber: "FREE-CONNECTOR",
  defaultContactCount: 4,
};

export const builtInConnectorTemplates: readonly BuiltInConnectorTemplate[] = [
  {
    id: connectorSeriesCatalogId("xs-demo-series"),
    kind: "series",
    title: "Серия XS",
    description: "Артикулы XS-04 и XS-10",
    seriesId: "xs-demo-series",
    defaultPartNumber: "XS-04",
  },
  {
    id: connectorSeriesCatalogId("jst-xh"),
    kind: "series",
    title: "JST XH",
    description: "Корпуса XHP с 1–16 и 20 сигнальными контактами",
    seriesId: "jst-xh",
    defaultPartNumber: "XHP-2",
  },
  builtInFreeConnectorTemplate,
];

const legacyArticleAliases = {
  "catalog-xs-04": { templateId: connectorSeriesCatalogId("xs-demo-series"), partNumber: "XS-04" },
  "catalog-xs-10": { templateId: connectorSeriesCatalogId("xs-demo-series"), partNumber: "XS-10" },
} as const;

export function findBuiltInConnectorSeries(seriesId: string): ConnectorSeries {
  const series = builtInConnectorSeries.find((candidate) => candidate.id === seriesId);
  if (!series) throw new Error(`Встроенная серия ${seriesId} не найдена.`);
  return series;
}

export function findBuiltInConnectorTemplate(templateId: string): BuiltInConnectorTemplate {
  const resolvedId = templateId in legacyArticleAliases
    ? legacyArticleAliases[templateId as keyof typeof legacyArticleAliases].templateId
    : templateId;
  const template = builtInConnectorTemplates.find((candidate) => candidate.id === resolvedId);
  if (!template) throw new Error(`Встроенный шаблон ${templateId} не найден.`);
  return template;
}

export function createConnectorInstanceFromSeries(
  series: ConnectorSeries,
  partNumber: string,
  options: CreateConnectorPlacementOptions,
): ConnectorInstance {
  const seed = createConnectorSeed(options, partNumber, []);
  return selectConnectorSeriesArticle(seed, series, partNumber).connector;
}

/** Applies a series to a prebuilt MVP connector; the first article is the default. */
export function createConnectorFromSeries(
  base: ConnectorInstance,
  series: ConnectorSeries,
  partNumber: string = series.articles[0]?.partNumber ?? "",
): ConnectorInstance {
  return selectConnectorSeriesArticle(base, series, partNumber).connector;
}

export function createConnectorInstanceFromFreeTemplate(
  template: BuiltInFreeConnectorTemplate,
  options: CreateConnectorPlacementOptions & { readonly contactCount?: number },
): ConnectorInstance {
  const contactCount = options.contactCount ?? template.defaultContactCount;
  requireContactCount(contactCount);
  const contacts = Array.from({ length: contactCount }, (_, index): ConnectorContact => ({
    id: `${options.id}:contact:${index + 1}`,
    number: index + 1,
    contactType: "",
    circuit: "",
    terminalArticle: "",
    wire: "",
    color: "",
    secondaryColor: "",
    connectionStatus: "available",
    customValues: {},
    libraryContact: null,
  }));
  return createFreeConnectorInstance(createConnectorSeed(options, template.partNumber, contacts));
}

/** Creates a ready-to-place ConnectorInstance from either new or legacy built-in catalog IDs. */
export function createBuiltInConnectorInstance(
  templateId: string,
  options: CreateBuiltInConnectorOptions,
): ConnectorInstance {
  const template = findBuiltInConnectorTemplate(templateId);
  if (template.kind === "free") {
    return createConnectorInstanceFromFreeTemplate(template, {
      ...options,
      contactCount: options.freeContactCount,
    });
  }
  const aliasPartNumber = templateId in legacyArticleAliases
    ? legacyArticleAliases[templateId as keyof typeof legacyArticleAliases].partNumber
    : undefined;
  const partNumber = options.partNumber ?? aliasPartNumber ?? template.defaultPartNumber;
  return createConnectorInstanceFromSeries(findBuiltInConnectorSeries(template.seriesId), partNumber, options);
}

function createConnectorSeed(
  options: CreateConnectorPlacementOptions,
  partNumber: string,
  contacts: readonly ConnectorContact[],
): ConnectorInstance {
  const id = requireText(options.id, "ID соединителя");
  const designation = requireText(options.designation, "Обозначение соединителя");
  const normalizedPartNumber = requireText(partNumber, "Артикул соединителя");
  const drawingPosition = options.drawingPosition ?? options.e4Position;
  return {
    id,
    designation,
    libraryCode: "FREE",
    partNumber: normalizedPartNumber,
    contacts,
    schematic: {
      orientation: "contacts-right",
      baseColumns: createDefaultConnectorBaseColumns(),
      customFields: [],
    },
    positions: { e4: options.e4Position, drawing: drawingPosition },
    layerIds: { e4: defaultLayerIds.connectors, drawing: defaultLayerIds.connectors },
  };
}

function requireContactCount(value: number): void {
  if (!Number.isSafeInteger(value) || value < 1 || value > 300) {
    throw new Error("Число строк свободного соединителя должно быть от 1 до 300.");
  }
}

function requireText(value: string, name: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${name} не задано.`);
  return normalized;
}
