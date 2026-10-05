// @vitest-environment node
import { describe, expect, it } from "vitest";
import { createConnector } from "./commands";
import {
  appendFreeConnectorContact,
  assignConnectorTerminalArticle,
  createFreeConnectorInstance,
  defineConnectorSeries,
  defineConnectorSeriesLibrary,
  enterFreeConnectorTerminalArticle,
  parseConnectorSeriesHarnessDesignDocument,
  removeFreeConnectorContact,
  selectConnectorSeriesArticle,
  terminalArticleChoices,
  validateConnectorSeriesBinding,
  type ConnectorSeries,
} from "./connector-series";
import { createEmptyHarnessDesign } from "./model";

const series: ConnectorSeries = {
  id: "deutsch-dt",
  name: "Deutsch DT",
  thirdContactTypeLabel: "Коаксиальный",
  articles: [
    {
      partNumber: "DT04-4P",
      contactCounts: { signal: 2, power: 1, third: 1 },
      allowedTerminalArticles: {
        signal: ["0460-202-16141", "0460-204-16141"],
        power: ["0460-215-16141"],
        third: ["COAX-01", "COAX-02"],
      },
    },
    {
      partNumber: "DT04-3P",
      contactCounts: { signal: 1, power: 2, third: 0 },
      allowedTerminalArticles: {
        signal: ["0460-204-16141"],
        power: ["0460-215-16141", "0460-220-16141"],
        third: [],
      },
    },
  ],
};

const terminalCatalog = [
  "0460-202-16141",
  "0460-204-16141",
  "0460-215-16141",
  "0460-220-16141",
  "COAX-01",
  "COAX-02",
  "UNIVERSAL-99",
];

describe("connector series model", () => {
  it("defines a series with several housing articles, typed counts and terminal lists", () => {
    const normalized = defineConnectorSeries(series);
    expect(normalized.articles.map((article) => article.partNumber)).toEqual(["DT04-4P", "DT04-3P"]);
    expect(normalized.articles[0]).toMatchObject({
      contactCounts: { signal: 2, power: 1, third: 1 },
      allowedTerminalArticles: {
        signal: ["0460-202-16141", "0460-204-16141"],
        third: ["COAX-01", "COAX-02"],
      },
    });
  });

  it("rejects ambiguous or impossible series definitions", () => {
    expect(() => defineConnectorSeries({
      ...series,
      articles: [...series.articles, series.articles[0]!],
    })).toThrow(/уникальны/);
    expect(() => defineConnectorSeries({
      ...series,
      articles: [{
        ...series.articles[0]!,
        contactCounts: { signal: 200, power: 101, third: 0 },
      }],
    })).toThrow(/от 1 до 300/);
    expect(() => defineConnectorSeries({
      ...series,
      articles: [{
        ...series.articles[0]!,
        allowedTerminalArticles: { signal: ["T-1", "T-1"], power: [], third: [] },
      }],
    })).toThrow(/не должны повторяться/);
    expect(() => defineConnectorSeriesLibrary([series, { ...series, name: "Duplicate" }])).toThrow(/ID серий/);
  });

  it("applies an article as deterministic rows grouped by contact kind", () => {
    const base = createConnector("x1", "XS1", 1, { x: 20, y: 40 });
    const result = selectConnectorSeriesArticle(base, series, "DT04-4P");

    expect(result.connector.libraryBinding).toEqual({
      mode: "series", seriesId: "deutsch-dt", partNumber: "DT04-4P",
    });
    expect(result.connector.partNumber).toBe("DT04-4P");
    expect(result.connector.contacts.map((contact) => ({
      id: contact.id,
      number: contact.number,
      type: contact.contactType,
      position: contact.libraryContact,
    }))).toEqual([
      { id: "x1:contact:signal:1", number: 1, type: "сигнальный", position: { kind: "signal", ordinal: 1 } },
      { id: "x1:contact:signal:2", number: 2, type: "сигнальный", position: { kind: "signal", ordinal: 2 } },
      { id: "x1:contact:power:1", number: 3, type: "силовой", position: { kind: "power", ordinal: 1 } },
      { id: "x1:contact:third:1", number: 4, type: "Коаксиальный", position: { kind: "third", ordinal: 1 } },
    ]);
    expect(result.removedContactIds).toEqual(["x1:contact:1"]);
    expect(() => validateConnectorSeriesBinding(result.connector, [series])).not.toThrow();
  });

  it("reconciles a selected article by stable typed position and reports removed contacts", () => {
    let connector = selectConnectorSeriesArticle(
      createConnector("x1", "XS1", 1, { x: 0, y: 0 }),
      series,
      "DT04-4P",
    ).connector;
    connector = {
      ...connector,
      contacts: connector.contacts.map((contact) => contact.libraryContact?.kind === "signal"
        ? { ...contact, circuit: `SIG-${contact.libraryContact.ordinal}`, terminalArticle: "0460-202-16141" }
        : contact),
    };

    const changed = selectConnectorSeriesArticle(connector, series, "DT04-3P");
    expect(changed.connector.contacts.map((contact) => contact.id)).toEqual([
      "x1:contact:signal:1", "x1:contact:power:1", "x1:contact:power:2",
    ]);
    expect(changed.connector.contacts[0]).toMatchObject({
      circuit: "SIG-1",
      // The old terminal is cleared because the new article only allows the other signal terminal.
      terminalArticle: "",
    });
    expect(changed.removedContactIds).toEqual(["x1:contact:signal:2", "x1:contact:third:1"]);
    expect(() => selectConnectorSeriesArticle(changed.connector, {
      ...series,
      id: "another-series",
    }, "DT04-3P")).toThrow(/заменить серию/);
  });

  it("restricts a library contact to terminals allowed for its type and present in the catalog", () => {
    let connector = selectConnectorSeriesArticle(
      createConnector("x1", "XS1", 1, { x: 0, y: 0 }),
      series,
      "DT04-4P",
    ).connector;
    expect(terminalArticleChoices(connector, "x1:contact:signal:1", terminalCatalog, [series])).toEqual([
      "0460-202-16141", "0460-204-16141",
    ]);
    connector = assignConnectorTerminalArticle(
      connector,
      "x1:contact:signal:1",
      "0460-204-16141",
      terminalCatalog,
      [series],
    );
    expect(connector.contacts[0]?.terminalArticle).toBe("0460-204-16141");
    expect(() => assignConnectorTerminalArticle(
      connector,
      "x1:contact:signal:1",
      "0460-215-16141",
      terminalCatalog,
      [series],
    )).toThrow(/не разрешён/);
    expect(() => appendFreeConnectorContact(connector)).toThrow(/определяются выбранным артикулом/);
  });

  it("supports an independent free template with manual rows and the whole terminal catalog", () => {
    let connector = createFreeConnectorInstance(createConnector("free-1", "XS9", 1, { x: 0, y: 0 }));
    connector = appendFreeConnectorContact(connector, { contactType: "Оптический", circuit: "FO-1" });
    expect(connector.contacts).toHaveLength(2);
    expect(connector.contacts[1]).toMatchObject({
      id: "free-1:contact:2", number: 2, contactType: "Оптический", circuit: "FO-1", libraryContact: null,
    });
    expect(terminalArticleChoices(connector, "free-1:contact:2", terminalCatalog)).toEqual(terminalCatalog);
    expect(terminalArticleChoices(connector, "free-1:contact:2", [...terminalCatalog, "UNIVERSAL-99"]))
      .toEqual(terminalCatalog);
    connector = assignConnectorTerminalArticle(
      connector,
      "free-1:contact:2",
      "UNIVERSAL-99",
      terminalCatalog,
    );
    expect(connector.contacts[1]?.terminalArticle).toBe("UNIVERSAL-99");
    connector = enterFreeConnectorTerminalArticle(connector, "free-1:contact:2", " SPECIAL-CRIMP ");
    expect(connector.contacts[1]?.terminalArticle).toBe("SPECIAL-CRIMP");
    expect(() => assignConnectorTerminalArticle(
      connector,
      "free-1:contact:2",
      "UNKNOWN",
      terminalCatalog,
    )).toThrow(/общем справочнике/);
    connector = removeFreeConnectorContact(connector, "free-1:contact:1");
    expect(connector.contacts.map((contact) => contact.id)).toEqual(["free-1:contact:2"]);
  });

  it("reads legacy schemaVersion 1 documents as free instances without losing rows", () => {
    const base = createEmptyHarnessDesign();
    const oldConnector = createConnector("legacy", "X1", 2, { x: 10, y: 20 });
    const value = { ...base, connectors: [{
      ...oldConnector,
      contacts: oldConnector.contacts.map((contact, index) => ({
        ...contact,
        contactType: index === 0 ? "Сигнальный" : "Силовой",
        terminalArticle: index === 0 ? "OLD-T-1" : "OLD-T-2",
      })),
    }] };

    const parsed = parseConnectorSeriesHarnessDesignDocument(value);
    expect(parsed.schemaVersion).toBe(1);
    expect(parsed.connectors[0]?.libraryBinding).toEqual({ mode: "free" });
    expect(parsed.connectors[0]?.contacts.map((contact) => ({
      type: contact.contactType,
      terminal: contact.terminalArticle,
      libraryContact: contact.libraryContact,
    }))).toEqual([
      { type: "Сигнальный", terminal: "OLD-T-1", libraryContact: null },
      { type: "Силовой", terminal: "OLD-T-2", libraryContact: null },
    ]);
  });

  it("round-trips additive series metadata while keeping schemaVersion 1", () => {
    const base = createEmptyHarnessDesign();
    const configured = selectConnectorSeriesArticle(
      createConnector("x1", "XS1", 1, { x: 0, y: 0 }),
      series,
      "DT04-3P",
    ).connector;
    const parsed = parseConnectorSeriesHarnessDesignDocument({ ...base, connectors: [configured] }, [series]);

    expect(parsed.schemaVersion).toBe(1);
    expect(parsed.connectors[0]?.libraryBinding).toEqual({
      mode: "series", seriesId: "deutsch-dt", partNumber: "DT04-3P",
    });
    expect(parsed.connectors[0]?.contacts.map((contact) => contact.libraryContact)).toEqual([
      { kind: "signal", ordinal: 1 },
      { kind: "power", ordinal: 1 },
      { kind: "power", ordinal: 2 },
    ]);
    expect(() => validateConnectorSeriesBinding(parsed.connectors[0]!, [series])).not.toThrow();
  });

  it("uses an available series library to reject semantically invalid persisted contacts", () => {
    const base = createEmptyHarnessDesign();
    const configured = selectConnectorSeriesArticle(
      createConnector("x1", "XS1", 1, { x: 0, y: 0 }),
      series,
      "DT04-4P",
    ).connector;
    const invalidTerminal = {
      ...configured,
      contacts: configured.contacts.map((contact, index) => index === 0
        ? { ...contact, terminalArticle: "0460-215-16141" }
        : contact),
    };
    expect(() => parseConnectorSeriesHarnessDesignDocument(
      { ...base, connectors: [invalidTerminal] },
      [series],
    )).toThrow(/Терминал не разрешён/);

    const invalidType = {
      ...configured,
      contacts: configured.contacts.map((contact, index) => index === 0
        ? { ...contact, contactType: "силовой" }
        : contact),
    };
    expect(() => parseConnectorSeriesHarnessDesignDocument(
      { ...base, connectors: [invalidType] },
      [series],
    )).toThrow(/ID, номер или тип/);
    expect(() => parseConnectorSeriesHarnessDesignDocument(
      { ...base, connectors: [configured] },
      [],
    )).toThrow(/отсутствует в библиотеке/);
  });
});
