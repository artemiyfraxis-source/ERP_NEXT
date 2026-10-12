import {
  parseHarnessDesignDocument,
  validateConnectorLibraryMetadata,
  type ConnectorContact,
  type ConnectorInstance,
  type ConnectorLibraryBinding as ModelConnectorLibraryBinding,
  type HarnessDesignDocument,
} from "./model";

export const connectorLibraryContactKinds = ["signal", "power", "third"] as const;

export type ConnectorLibraryContactKind = typeof connectorLibraryContactKinds[number];

export interface ConnectorContactCounts {
  readonly signal: number;
  readonly power: number;
  readonly third: number;
}

export type AllowedTerminalArticles = Readonly<Record<ConnectorLibraryContactKind, readonly string[]>>;

export interface ConnectorSeriesArticle {
  readonly partNumber: string;
  readonly contactCounts: ConnectorContactCounts;
  readonly allowedTerminalArticles: AllowedTerminalArticles;
}

/** A library item represents the whole connector series, not one housing article. */
export interface ConnectorSeries {
  readonly id: string;
  readonly name: string;
  readonly thirdContactTypeLabel: string;
  readonly articles: readonly ConnectorSeriesArticle[];
}

export type ConnectorLibraryBinding = Exclude<ModelConnectorLibraryBinding, { readonly mode: "template" }>;

export interface ConnectorLibraryContact {
  readonly kind: ConnectorLibraryContactKind;
  /** One-based ordinal within the contact kind. */
  readonly ordinal: number;
}

export type ConnectorSeriesContact = ConnectorContact & {
  /** Null means that the row belongs to a freely edited template instance. */
  readonly libraryContact: ConnectorLibraryContact | null;
};

export type ConnectorSeriesInstance = Omit<ConnectorInstance, "contacts"> & {
  readonly contacts: readonly ConnectorSeriesContact[];
  readonly libraryBinding: ConnectorLibraryBinding;
};

/**
 * The current server contract remains schemaVersion 1. Extra connector fields are
 * additive JSON fields, so this document can be saved by the existing MVP API.
 */
export type ConnectorSeriesHarnessDesignDocument = Omit<HarnessDesignDocument, "connectors"> & {
  readonly schemaVersion: 1;
  readonly connectors: readonly ConnectorSeriesInstance[];
};

export interface SeriesArticleSelectionResult {
  readonly connector: ConnectorSeriesInstance;
  /** Existing wires referencing these rows must be removed or explicitly remapped by the caller. */
  readonly removedContactIds: readonly string[];
}

export type FreeContactValues = Partial<Pick<
  ConnectorContact,
  "contactType" | "circuit" | "terminalArticle" | "wire" | "wireSection" | "color" | "secondaryColor" | "connectionStatus" | "customValues"
>>;

const maximumContactCount = 300;

export function defineConnectorSeries(value: ConnectorSeries): ConnectorSeries {
  const id = requireText(value.id, "ID серии");
  const name = requireText(value.name, "Название серии");
  const thirdContactTypeLabel = requireText(value.thirdContactTypeLabel, "Название третьего типа контакта");
  if (!Array.isArray(value.articles) || value.articles.length === 0) {
    throw new Error("Добавьте хотя бы один артикул серии.");
  }
  const articles = value.articles.map(normalizeSeriesArticle);
  requireUnique(articles.map((article) => article.partNumber), "Артикулы внутри серии должны быть уникальны.");
  return { id, name, thirdContactTypeLabel, articles };
}

export function defineConnectorSeriesLibrary(values: readonly ConnectorSeries[]): readonly ConnectorSeries[] {
  if (!Array.isArray(values)) throw new Error("Библиотека серий задана неверно.");
  const series = values.map(defineConnectorSeries);
  requireUnique(series.map((item) => item.id), "ID серий в библиотеке должны быть уникальны.");
  return series;
}

export function findConnectorSeriesArticle(
  series: ConnectorSeries,
  partNumber: string,
): ConnectorSeriesArticle {
  const normalizedPartNumber = requireText(partNumber, "Артикул соединителя");
  const article = series.articles.find((candidate) => candidate.partNumber === normalizedPartNumber);
  if (!article) throw new Error(`Артикул ${normalizedPartNumber} отсутствует в серии ${series.name}.`);
  return article;
}

export function selectConnectorSeriesArticle(
  connector: ConnectorInstance | ConnectorSeriesInstance,
  seriesValue: ConnectorSeries,
  partNumber: string,
): SeriesArticleSelectionResult {
  const series = defineConnectorSeries(seriesValue);
  const article = findConnectorSeriesArticle(series, partNumber);
  const current = asSeriesInstance(connector);
  if (current.libraryBinding.mode === "series" && current.libraryBinding.seriesId !== series.id) {
    throw new Error("Нельзя заменить серию уже привязанного библиотечного экземпляра.");
  }
  const canReuseRows = current.libraryBinding.mode === "series" && current.libraryBinding.seriesId === series.id;
  const reusableRows = new Map<string, ConnectorSeriesContact>();
  if (canReuseRows) {
    for (const contact of current.contacts) {
      if (contact.libraryContact) reusableRows.set(libraryContactKey(contact.libraryContact), contact);
    }
  }

  const contacts: ConnectorSeriesContact[] = [];
  let number = 1;
  for (const kind of connectorLibraryContactKinds) {
    for (let ordinal = 1; ordinal <= article.contactCounts[kind]; ordinal += 1) {
      const libraryContact = { kind, ordinal } as const;
      const previous = reusableRows.get(libraryContactKey(libraryContact));
      const allowedTerminals = article.allowedTerminalArticles[kind];
      contacts.push({
        id: connectorLibraryContactId(connector.id, libraryContact),
        number,
        contactType: contactTypeLabel(kind, series.thirdContactTypeLabel),
        circuit: previous?.circuit ?? "",
        terminalArticle: previous && allowedTerminals.includes(previous.terminalArticle)
          ? previous.terminalArticle
          : "",
        wire: previous?.wire ?? "",
        ...(previous?.wireSection === undefined ? {} : { wireSection: previous.wireSection }),
        ...(previous?.materialBinding === undefined ? {} : { materialBinding: previous.materialBinding }),
        color: previous?.color ?? "",
        secondaryColor: previous?.secondaryColor ?? "",
        connectionStatus: previous?.connectionStatus ?? "available",
        customValues: previous?.customValues ?? {},
        libraryContact,
      });
      number += 1;
    }
  }

  const retainedIds = new Set(contacts.map((contact) => contact.id));
  return {
    connector: {
      ...connector,
      partNumber: article.partNumber,
      contacts,
      libraryBinding: { mode: "series", seriesId: series.id, partNumber: article.partNumber },
    },
    removedContactIds: connector.contacts
      .map((contact) => contact.id)
      .filter((contactId) => !retainedIds.has(contactId)),
  };
}

export function createFreeConnectorInstance(
  connector: ConnectorInstance | ConnectorSeriesInstance,
): ConnectorSeriesInstance {
  return {
    ...connector,
    contacts: connector.contacts.map((contact) => ({ ...contact, libraryContact: null })),
    libraryBinding: { mode: "free" },
  };
}

export function appendFreeConnectorContact(
  connector: ConnectorSeriesInstance,
  values: FreeContactValues = {},
): ConnectorSeriesInstance {
  requireFreeInstance(connector);
  if (connector.contacts.length >= maximumContactCount) {
    throw new Error(`В соединителе может быть не более ${maximumContactCount} строк.`);
  }
  const number = nextFreeContactNumber(connector);
  const contact: ConnectorSeriesContact = {
    id: nextFreeContactId(connector, number),
    number,
    contactType: normalizeOptionalText(values.contactType),
    circuit: normalizeOptionalText(values.circuit),
    terminalArticle: normalizeOptionalText(values.terminalArticle),
    wire: normalizeOptionalText(values.wire),
    ...(values.wireSection === undefined ? {} : { wireSection: normalizeOptionalText(values.wireSection) }),
    color: normalizeOptionalText(values.color),
    secondaryColor: normalizeOptionalText(values.secondaryColor),
    connectionStatus: values.connectionStatus ?? "available",
    customValues: values.customValues ?? {},
    libraryContact: null,
  };
  return { ...connector, contacts: [...connector.contacts, contact] };
}

export function removeFreeConnectorContact(
  connector: ConnectorSeriesInstance,
  contactId: string,
): ConnectorSeriesInstance {
  requireFreeInstance(connector);
  if (!connector.contacts.some((contact) => contact.id === contactId)) throw new Error("Строка контакта не найдена.");
  return { ...connector, contacts: connector.contacts.filter((contact) => contact.id !== contactId) };
}

export function terminalArticleChoices(
  connector: ConnectorSeriesInstance,
  contactId: string,
  allTerminalArticles: readonly string[],
  seriesLibrary: readonly ConnectorSeries[] = [],
): readonly string[] {
  const contact = requireContact(connector, contactId);
  const catalog = normalizedCatalogArticles(allTerminalArticles);
  if (connector.libraryBinding.mode === "free") return catalog;
  if (!contact.libraryContact) throw new Error("У библиотечного контакта отсутствует тип и порядковый номер.");
  const series = requireBoundSeries(connector.libraryBinding, seriesLibrary);
  const article = findConnectorSeriesArticle(series, connector.libraryBinding.partNumber);
  const allowed = new Set(article.allowedTerminalArticles[contact.libraryContact.kind]);
  return catalog.filter((terminalArticle) => allowed.has(terminalArticle));
}

export function assignConnectorTerminalArticle(
  connector: ConnectorSeriesInstance,
  contactId: string,
  terminalArticle: string,
  allTerminalArticles: readonly string[],
  seriesLibrary: readonly ConnectorSeries[] = [],
): ConnectorSeriesInstance {
  requireContact(connector, contactId);
  const normalizedArticle = terminalArticle.trim();
  if (normalizedArticle && !terminalArticleChoices(connector, contactId, allTerminalArticles, seriesLibrary).includes(normalizedArticle)) {
    throw new Error(connector.libraryBinding.mode === "free"
      ? "Терминал отсутствует в общем справочнике."
      : "Терминал не разрешён для этого типа контакта выбранного артикула.");
  }
  return {
    ...connector,
    contacts: connector.contacts.map((contact) => contact.id === contactId
      ? { ...contact, terminalArticle: normalizedArticle }
      : contact),
  };
}

/** Free templates also allow an ad-hoc value that is not present in the catalog. */
export function enterFreeConnectorTerminalArticle(
  connector: ConnectorSeriesInstance,
  contactId: string,
  terminalArticle: string,
): ConnectorSeriesInstance {
  requireFreeInstance(connector);
  requireContact(connector, contactId);
  const normalizedArticle = terminalArticle.trim();
  return {
    ...connector,
    contacts: connector.contacts.map((contact) => contact.id === contactId
      ? { ...contact, terminalArticle: normalizedArticle }
      : contact),
  };
}

/**
 * Reads the established schemaVersion 1 format. Connectors without the new
 * additive metadata become free instances and keep all legacy contact values.
 */
export function parseConnectorSeriesHarnessDesignDocument(
  value: unknown,
  seriesLibrary?: readonly ConnectorSeries[],
): ConnectorSeriesHarnessDesignDocument {
  const parsed = parseHarnessDesignDocument(value);
  const rawDocument = requireRecord(value, "Документ жгута задан неверно.");
  if (!Array.isArray(rawDocument.connectors)) throw new Error("Соединители документа заданы неверно.");
  const rawConnectors = rawDocument.connectors;
  const connectors = parsed.connectors.map((connector, connectorIndex) => {
    if (connector.libraryBinding?.mode === "template") {
      throw new Error("Закреплённый экземпляр шаблона нужно читать через основную модель редактора.");
    }
    const rawConnector = requireRecord(rawConnectors[connectorIndex], "Соединитель задан неверно.");
    const binding = parseLibraryBinding(rawConnector.libraryBinding);
    const rawContacts = rawConnector.contacts;
    if (!Array.isArray(rawContacts)) throw new Error("Контакты соединителя заданы неверно.");
    const contacts = connector.contacts.map((contact, contactIndex): ConnectorSeriesContact => ({
      ...contact,
      libraryContact: binding.mode === "series"
        ? parseLibraryContact(requireRecord(rawContacts[contactIndex], "Контакт соединителя задан неверно.").libraryContact)
        : null,
    }));
    if (binding.mode === "series") {
      requireUnique(
        contacts.map((contact) => libraryContactKey(contact.libraryContact!)),
        "Библиотечные позиции контактов должны быть уникальны.",
      );
    }
    return { ...connector, contacts, libraryBinding: binding };
  });
  const document = { ...parsed, connectors };
  for (const connector of document.connectors) {
    validateConnectorLibraryMetadata(connector);
    if (seriesLibrary !== undefined) validateConnectorSeriesBinding(connector, seriesLibrary);
  }
  return document;
}

export function validateConnectorSeriesBinding(
  connector: ConnectorSeriesInstance,
  seriesLibrary: readonly ConnectorSeries[],
): void {
  if (connector.libraryBinding.mode === "free") {
    if (connector.contacts.some((contact) => contact.libraryContact !== null)) {
      throw new Error("Свободный экземпляр не должен ссылаться на позиции библиотечной серии.");
    }
    return;
  }
  const series = requireBoundSeries(connector.libraryBinding, seriesLibrary);
  if (connector.partNumber !== connector.libraryBinding.partNumber) {
    throw new Error("Артикул экземпляра не совпадает с выбранным артикулом серии.");
  }
  const expected = selectConnectorSeriesArticle(
    { ...connector, contacts: [], libraryBinding: { mode: "free" } },
    series,
    connector.libraryBinding.partNumber,
  ).connector.contacts;
  const expectedPositions = expected.map((contact) => libraryContactKey(contact.libraryContact!));
  const actualPositions = connector.contacts.map((contact) => {
    if (!contact.libraryContact) throw new Error("У библиотечного контакта отсутствует позиция в серии.");
    return libraryContactKey(contact.libraryContact);
  });
  if (expectedPositions.length !== actualPositions.length ||
      expectedPositions.some((position, index) => position !== actualPositions[index])) {
    throw new Error("Строки соединителя не соответствуют выбранному артикулу серии.");
  }
  for (const [index, contact] of connector.contacts.entries()) {
    const expectedContact = expected[index]!;
    const expectedLabel = contactTypeLabel(contact.libraryContact!.kind, series.thirdContactTypeLabel);
    if (contact.id !== expectedContact.id || contact.number !== expectedContact.number || contact.contactType !== expectedLabel) {
      throw new Error("ID, номер или тип библиотечного контакта изменён вручную.");
    }
    const article = findConnectorSeriesArticle(series, connector.libraryBinding.partNumber);
    if (contact.terminalArticle && !article.allowedTerminalArticles[contact.libraryContact!.kind].includes(contact.terminalArticle)) {
      throw new Error("Терминал не разрешён для этого типа контакта выбранного артикула.");
    }
  }
}

function normalizeSeriesArticle(value: ConnectorSeriesArticle): ConnectorSeriesArticle {
  const partNumber = requireText(value.partNumber, "Артикул соединителя");
  const contactCounts = normalizeContactCounts(value.contactCounts);
  const total = connectorLibraryContactKinds.reduce((sum, kind) => sum + contactCounts[kind], 0);
  if (total < 1 || total > maximumContactCount) {
    throw new Error(`Суммарное число контактов артикула должно быть от 1 до ${maximumContactCount}.`);
  }
  const allowedTerminalArticles = Object.fromEntries(connectorLibraryContactKinds.map((kind) => {
    const articles = uniqueNormalizedArticles(value.allowedTerminalArticles?.[kind] ?? []);
    return [kind, articles];
  })) as unknown as AllowedTerminalArticles;
  return { partNumber, contactCounts, allowedTerminalArticles };
}

function normalizeContactCounts(value: ConnectorContactCounts): ConnectorContactCounts {
  const result = {} as Record<ConnectorLibraryContactKind, number>;
  for (const kind of connectorLibraryContactKinds) {
    const count = value?.[kind];
    if (!Number.isSafeInteger(count) || count < 0 || count > maximumContactCount) {
      throw new Error("Количество контактов каждого типа должно быть целым числом от 0 до 300.");
    }
    result[kind] = count;
  }
  return result;
}

function parseLibraryBinding(value: unknown): ConnectorLibraryBinding {
  if (value === undefined) return { mode: "free" };
  const record = requireRecord(value, "Привязка соединителя к библиотеке задана неверно.");
  if (record.mode === "free") return { mode: "free" };
  if (record.mode !== "series") throw new Error("Режим соединителя задан неверно.");
  return {
    mode: "series",
    seriesId: requireText(record.seriesId, "ID серии"),
    partNumber: requireText(record.partNumber, "Артикул соединителя"),
  };
}

function parseLibraryContact(value: unknown): ConnectorLibraryContact {
  const record = requireRecord(value, "Позиция контакта в серии задана неверно.");
  if (!connectorLibraryContactKinds.includes(record.kind as ConnectorLibraryContactKind)) {
    throw new Error("Тип библиотечного контакта задан неверно.");
  }
  if (!Number.isSafeInteger(record.ordinal) || (record.ordinal as number) < 1 || (record.ordinal as number) > maximumContactCount) {
    throw new Error("Порядковый номер библиотечного контакта задан неверно.");
  }
  return { kind: record.kind as ConnectorLibraryContactKind, ordinal: record.ordinal as number };
}

function asSeriesInstance(connector: ConnectorInstance | ConnectorSeriesInstance): ConnectorSeriesInstance {
  if (connector.libraryBinding?.mode === "series" || connector.libraryBinding?.mode === "free") return {
    ...connector,
    libraryBinding: connector.libraryBinding,
    contacts: connector.contacts.map((contact) => ({
      ...contact,
      libraryContact: contact.libraryContact ?? null,
    })),
  };
  if (connector.libraryBinding?.mode === "template") {
    throw new Error("Закреплённый экземпляр шаблона нельзя редактировать как устаревшую серию.");
  }
  return createFreeConnectorInstance(connector);
}

function requireBoundSeries(
  binding: Extract<ConnectorLibraryBinding, { mode: "series" }>,
  seriesLibrary: readonly ConnectorSeries[],
): ConnectorSeries {
  const normalizedLibrary = defineConnectorSeriesLibrary(seriesLibrary);
  const rawSeries = normalizedLibrary.find((candidate) => candidate.id === binding.seriesId);
  if (!rawSeries) throw new Error(`Серия ${binding.seriesId} отсутствует в библиотеке.`);
  return defineConnectorSeries(rawSeries);
}

function requireFreeInstance(connector: ConnectorSeriesInstance): void {
  if (connector.libraryBinding.mode !== "free") {
    throw new Error("Строки библиотечного экземпляра определяются выбранным артикулом серии.");
  }
}

function requireContact(connector: ConnectorSeriesInstance, contactId: string): ConnectorSeriesContact {
  const contact = connector.contacts.find((candidate) => candidate.id === contactId);
  if (!contact) throw new Error("Строка контакта не найдена.");
  return contact;
}

function contactTypeLabel(kind: ConnectorLibraryContactKind, thirdContactTypeLabel: string): string {
  if (kind === "signal") return "сигнальный";
  if (kind === "power") return "силовой";
  return thirdContactTypeLabel;
}

function connectorLibraryContactId(connectorId: string, contact: ConnectorLibraryContact): string {
  return `${connectorId}:contact:${contact.kind}:${contact.ordinal}`;
}

function libraryContactKey(contact: ConnectorLibraryContact): string {
  return `${contact.kind}:${contact.ordinal}`;
}

function nextFreeContactId(connector: ConnectorSeriesInstance, seed: number): string {
  const ids = new Set(connector.contacts.map((contact) => contact.id));
  let suffix = seed;
  while (ids.has(`${connector.id}:contact:${suffix}`)) suffix += 1;
  return `${connector.id}:contact:${suffix}`;
}

function nextFreeContactNumber(connector: ConnectorSeriesInstance): number {
  const numbers = new Set(connector.contacts.map((contact) => contact.number));
  const maximum = connector.contacts.reduce((result, contact) => Math.max(result, contact.number), 0);
  if (maximum < maximumContactCount) return maximum + 1;
  for (let number = 1; number <= maximumContactCount; number += 1) {
    if (!numbers.has(number)) return number;
  }
  throw new Error(`В соединителе может быть не более ${maximumContactCount} строк.`);
}

function uniqueNormalizedArticles(values: readonly string[]): readonly string[] {
  if (!Array.isArray(values)) throw new Error("Список артикулов терминалов задан неверно.");
  const normalized = values.map((value) => requireText(value, "Артикул терминала"));
  requireUnique(normalized, "Артикулы терминалов в одном списке не должны повторяться.");
  return normalized;
}

function normalizedCatalogArticles(values: readonly string[]): readonly string[] {
  if (!Array.isArray(values)) throw new Error("Справочник терминалов задан неверно.");
  return [...new Set(values.map((value) => requireText(value, "Артикул терминала")))];
}

function normalizeOptionalText(value: string | undefined): string {
  return value?.trim() ?? "";
}

function requireText(value: unknown, label: string): string {
  if (typeof value !== "string" || value.trim().length === 0) throw new Error(`${label} не задан.`);
  return value.trim();
}

function requireUnique(values: readonly string[], message: string): void {
  if (new Set(values).size !== values.length) throw new Error(message);
}

function requireRecord(value: unknown, message: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error(message);
  return value as Record<string, unknown>;
}
