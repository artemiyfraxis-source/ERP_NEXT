// @vitest-environment node
import {createElement} from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {E4ConnectorInspector} from "./E4ConnectorInspector";
import {createWire,applyEditorCommand} from "./commands";
import {designToScene} from "./HarnessDesignEditor";
import {connectorContactPosition} from "./model";
import {connectionEndLabel} from "./drawing-documents";
import {templateContactNumberingWarning} from "./template-contact-numbering";
import { describe, expect, it } from "vitest";
import {
  addBasicNodeV2,
  addContactPointV2,
  constantExpressionV2,
  createRepeatPrototypeV2,
  linkLogicalContactPointV2,
  newTemplateContentV2,
} from "../component-library/template-commands-v2";
import { upgradeTemplateContentV2ToV3 } from "../component-library/template-upgrade-v3";
import type { ArticleVariantV3, TemplateContentV3 } from "../component-library/template-model-v3";
import { upgradeTemplateContentV3ToV4 } from "../component-library/template-model-v4";
import { upgradeTemplateContentV4ToV5 } from "../component-library/template-model-v5";
import {
  createConnectorInstanceFromComponentTemplateV3,
  firstPlaceableArticleVariantId,
  rematerializeComponentTemplateConnectorArticle,
  type ComponentTemplatePlacementEnvelopeV3,
} from "./component-template-placement";
import { connectorE4TableGeometry, connectorContactName, createEmptyHarnessDesign, parseHarnessDesignDocument } from "./model";
import { componentPlacementRequest } from "./component-placement-api";
import { loadComponentTemplateForPlacement } from "./HarnessDesignEditor";

function fixture(target: number): ComponentTemplatePlacementEnvelopeV3 {
  let v2 = newTemplateContentV2();
  const e4 = v2.views[0]!;
  const drawing = v2.views[1]!;
  let fixedPointId: string;
  [v2, fixedPointId] = addContactPointV2(v2, e4.id, {
    number: "1", name: "Сигнал", contactType: "signal",
    x: constantExpressionV2(12), y: constantExpressionV2(18), direction: "right",
  });
  const fixedContactId = v2.views[0]!.contactPoints.find(point => point.id === fixedPointId)!.logicalContactId;
  let repeatedPointId: string;
  [v2, repeatedPointId] = addContactPointV2(v2, e4.id, {
    number: "2", name: "Повтор", contactType: "signal",
    x: constantExpressionV2(30), y: constantExpressionV2(40), direction: "left",
  });
  let prototypeNodeId: string;
  [v2, prototypeNodeId] = addBasicNodeV2(v2, e4.id, e4.layers[0]!.id, "rectangle");
  if (target > 2) {
    [v2] = createRepeatPrototypeV2(v2, {
      viewId: e4.id,
      layerId: e4.layers[0]!.id,
      prototypeNodeId,
      prototypePointId: repeatedPointId,
      count: target - 1,
      step: { x: constantExpressionV2(0), y: constantExpressionV2(10) },
    });
  }
  [v2] = linkLogicalContactPointV2(v2, drawing.id, fixedContactId, {
    x: constantExpressionV2(50), y: constantExpressionV2(60), direction: "right",
  });

  const content = upgradeTemplateContentV2ToV3(v2).content;
  const asset = {
    assetId: "10000000-0000-4000-8000-000000000099",
    fileName: "front.png",
    mediaType: "image/png",
    sha256: "b".repeat(64),
    sizeBytes: 123,
  };
  content.assets.push(asset);
  const groupId = content.contactTypeGroups[0]!.id;
  const variant: ArticleVariantV3 = {
    id: crypto.randomUUID(), sourceId: "БД.СОЕД", entityType: "connector", articleKey: `XH-${target}`,
    parameterValues: [],
    contactGroups: [{
      contactTypeGroupId: groupId,
      contactCount: target,
      allowedTerminalArticleKeys: [
        { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "T-1" },
        { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "T-2" },
      ],
    }],
  };
  const v3: TemplateContentV3 = { ...content, articleVariants: [...content.articleVariants, variant] };
  return {
    templateId: "template-component-1",
    version: 7,
    versionSha256: "a".repeat(64),
    code: "JST-XH",
    name: "JST XH",
    articleBindings: [{ sourceId: variant.sourceId, entityType: variant.entityType, articleKey: variant.articleKey }],
    assets: [asset],
    content: v3,
  };
}

describe("component template placement", () => {
  it("keeps template ID, published version, hash and article identical to the placed catalog version", async () => {
    const published = fixture(2);
    const template = await loadComponentTemplateForPlacement({
      getVersion: async (_id: string, version: number) => { if (version !== 7) throw new Error("wrong version"); return published; },
    } as never, published.templateId, 7);
    const preview = createConnectorInstanceFromComponentTemplateV3(template as typeof published, {
      id: crypto.randomUUID(), designation: "X1", e4Position: { x: 120, y: 100 },
      articleVariantId: published.content.articleVariants.at(-1)!.id,
    });
    const request = componentPlacementRequest(preview, 4, crypto.randomUUID());
    const binding = preview.libraryBinding;
    if (binding?.mode !== "template") throw new Error("Expected template");
    expect(request.sourceTemplateId).toBe(published.templateId);
    expect(request.sourceVersion).toBe(7);
    expect(request).toMatchObject(binding.article);
    expect(request.instance.libraryBinding).toMatchObject({
      templateId: request.sourceTemplateId, templateVersion: request.sourceVersion,
      versionSha256: published.versionSha256, article: binding.article,
      snapshot: { templateId: request.sourceTemplateId, templateVersion: request.sourceVersion,
        versionSha256: published.versionSha256, article: binding.article },
    });
    for (const mismatch of [
      { templateId: "wrong" }, { templateVersion: 6 }, { versionSha256: "b".repeat(64) },
      { article: { ...binding.article, articleKey: "WRONG" } },
    ]) {
      expect(() => componentPlacementRequest({ ...preview,
        libraryBinding: { ...binding, ...mismatch } }, 4, crypto.randomUUID())).toThrow();
    }
  });

  it("places the first real article when the cached envelope index is stale", () => {
    const template = fixture(2);
    const selected = template.content.articleVariants[0]!;
    const placed = createConnectorInstanceFromComponentTemplateV3({
      ...template,
      articleBindings: [{ sourceId: "obsolete", entityType: "connector", articleKey: "REMOVED" }],
    }, {
      id: "J-stale-index", designation: "X1", e4Position: { x: 1, y: 2 },
    });

    expect(placed.partNumber).toBe(selected.articleKey);
    expect(placed.libraryBinding).toMatchObject({
      mode: "template",
      articleVariantId: selected.id,
      snapshot: {
        articleBindings: template.content.articleVariants.map(({ sourceId, entityType, articleKey }) =>
          ({ sourceId: sourceId.toLowerCase(), entityType: entityType.toLowerCase(), articleKey })),
      },
    });
  });

  it("offers the series terminal list on every v5 E4 contact", () => {
    const base = fixture(2);
    if (base.content.schemaVersion !== 3) throw new Error("test fixture must be v3");
    const content = upgradeTemplateContentV4ToV5(upgradeTemplateContentV3ToV4(base.content).content).content;
    const template: ComponentTemplatePlacementEnvelopeV3 = { ...base, content };

    const placed = createConnectorInstanceFromComponentTemplateV3(template, {
      id: "J-v5", designation: "X1", articleVariantId: content.articleVariants.at(-1)!.id,
      e4Position: { x: 1, y: 2 },
    });

    expect(placed.libraryBinding?.mode === "template" && placed.libraryBinding.snapshot.contacts)
      .toSatisfy((contacts: readonly { allowedTerminalArticleKeys: readonly { articleKey: string }[] }[]) =>
        contacts.every(contact => contact.allowedTerminalArticleKeys.map(item => item.articleKey).join(",") === "T-1,T-2"));
  });

  it("places v5 electrical groups without requiring graphical contact prototypes", () => {
    const base = fixture(2);
    if (base.content.schemaVersion !== 3) throw new Error("v3 fixture required");
    const content = upgradeTemplateContentV4ToV5(upgradeTemplateContentV3ToV4(base.content).content).content;
    const independent = { ...content, logicalContacts: [], repeaters: [],
      views: content.views.map(view => ({ ...view, contactPoints: [] })) };
    const connector = createConnectorInstanceFromComponentTemplateV3({ ...base, content: independent }, {
      id: crypto.randomUUID(), designation: "XS1", e4Position: { x: 0, y: 0 },
      articleVariantId: content.articleVariants.at(-1)!.id,
    });
    expect(connector.contacts).toHaveLength(2);
    expect(connector.libraryBinding?.mode === "template" && connector.libraryBinding.snapshot.contacts)
      .toEqual(expect.arrayContaining([expect.objectContaining({ representations: [] })]));
  });

  it("places the first v5 article from the E4 table when no article is selected", () => {
    const base = fixture(2);
    const emptyCore = upgradeTemplateContentV2ToV3(newTemplateContentV2()).content;
    const first: ArticleVariantV3 = {
      id: crypto.randomUUID(), sourceId: "БД.СОЕД", entityType: "connector", articleKey: "XH-FIRST",
      parameterValues: [], contactGroups: null,
    };
    const second = { ...structuredClone(first), id: crypto.randomUUID(), articleKey: "XH-SECOND" };
    const v3: TemplateContentV3 = {
      ...emptyCore,
      articleVariants: [first, second],
    };
    const content = upgradeTemplateContentV4ToV5(upgradeTemplateContentV3ToV4(v3).content).content;
    const table = content.e4ConnectorTable as unknown as {
      seriesDefaults: Array<{ rowId: string; values: {
        number: string; name: string; circuitText: string | null; contactTypeGroupId: string | null;
        standardTerminalArticleKey: null;
      } }>;
      articles: Array<{ rows: Array<{ seriesRowId: string; overrides: Record<string, never> }> }>;
    };
    table.seriesDefaults.push({
      rowId: "real-v5-row",
      values: {
        number: "10", name: "FIRST E4 ROW", circuitText: "E4-CIRCUIT",
        contactTypeGroupId: null, standardTerminalArticleKey: null,
      },
    });
    table.articles[0]!.rows.push({ seriesRowId: "real-v5-row", overrides: {} });
    table.articles[1]!.rows.push({ seriesRowId: "real-v5-row", overrides: {} });
    const template: ComponentTemplatePlacementEnvelopeV3 = { ...base, assets: [], content };

    expect(firstPlaceableArticleVariantId(content)).toBe(first.id);
    const placed = createConnectorInstanceFromComponentTemplateV3(template, {
      id: "J-v5-first", designation: "X1", e4Position: { x: 1, y: 2 },
    });

    expect(placed.partNumber).toBe(first.articleKey);
    expect(placed.contacts).toHaveLength(1);
    expect(placed.contacts[0]).toMatchObject({
      logicalContactId: "real-v5-row",
      number: 10,
      circuit: "E4-CIRCUIT",
    });
    expect(placed.libraryBinding?.mode === "template" && placed.libraryBinding.snapshot.contacts[0])
      .toMatchObject({ sourceNumber: "10", name: "FIRST E4 ROW" });
  });

  it("materializes v4 E4 table values and remaps another article on stable series rows", () => {
    const base = fixture(2);
    if (base.content.schemaVersion !== 3) throw new Error("test fixture must be v3");
    const first = base.content.articleVariants.at(-1)!;
    const second = { ...structuredClone(first), id: crypto.randomUUID(), articleKey: "XH-2-B" };
    const v3: TemplateContentV3 = {
      ...base.content,
      schemaVersion: 3,
      articleVariants: [...base.content.articleVariants, second],
    };
    const content = upgradeTemplateContentV3ToV4(v3).content;
    const firstArticle = content.e4ConnectorTable.articles.find(article => article.articleVariantId === first.id)!;
    const firstDefault = content.e4ConnectorTable.seriesDefaults.find(row => row.rowId === firstArticle.rows[0]!.seriesRowId)!;
    firstDefault.values.name = "Э4 DATA";
    firstDefault.values.circuitText = "Э4-CIRCUIT";
    firstDefault.values.standardTerminalArticleKey =
      { sourceId: "БД.ТЕР", entityType: "terminal", articleKey: "T-1" };
    const template: ComponentTemplatePlacementEnvelopeV3 = {
      ...base,
      articleBindings: [first, second].map(({ sourceId, entityType, articleKey }) => ({ sourceId, entityType, articleKey })),
      content,
    };
    const placed = createConnectorInstanceFromComponentTemplateV3(template, {
      id: "J-v4", designation: "X1", articleVariantId: first.id, e4Position: { x: 1, y: 2 },
    });

    expect(placed.contacts[0]).toMatchObject({
      logicalContactId: firstArticle.rows[0]!.seriesRowId,
      circuit: "Э4-CIRCUIT",
      terminalArticle: "T-1",
    });
    expect(placed.libraryBinding?.mode === "template" && placed.libraryBinding.snapshot.contacts[0]).toMatchObject({
      name: "Э4 DATA",
      allowedTerminalArticleKeys: expect.arrayContaining([expect.objectContaining({ articleKey: "T-1" })]),
    });

    const edited = {
      ...placed,
      contacts: placed.contacts.map((contact, index) => index === 0
        ? { ...contact, wire: "UL1007", color: "Красный" }
        : contact),
    };
    const remapped = rematerializeComponentTemplateConnectorArticle(edited, template, second.id);
    expect(remapped.partNumber).toBe("XH-2-B");
    expect(remapped.contacts[0]).toMatchObject({
      id: placed.contacts[0]!.id,
      logicalContactId: placed.contacts[0]!.logicalContactId,
      wire: "UL1007",
      color: "Красный",
      terminalArticle: "T-1",
    });
  });

  it.each([2, 10])("materializes a %i-contact article and preserves stable logical IDs", target => {
    const template = fixture(target);
    const connector = createConnectorInstanceFromComponentTemplateV3(template, {
      id: `J${target}`,
      designation: `X${target}`,
      articleVariant: template.content.articleVariants.at(-1)!.id,
      e4Position: { x: 12, y: 18 },
    });

    expect(connector.contacts).toHaveLength(target);
    expect(connector.contacts[0]).toMatchObject({
      id: `J${target}:contact:${connector.contacts[0]!.logicalContactId}`,
      logicalContactId: connector.contacts[0]!.logicalContactId,
      number: 1,
      terminalArticle: "",
    });
    expect(new Set(connector.contacts.map(contact => contact.logicalContactId)).size).toBe(target);
    expect(connector.libraryBinding).toMatchObject({
      mode: "template", templateId: "template-component-1", templateVersion: 7,
      versionSha256: "a".repeat(64),
    });
    if (target === 10) {
      expect(connector.contacts[1]!.logicalContactId).toContain(":0:");
      expect(connector.libraryBinding?.mode === "template" && connector.libraryBinding.snapshot.contacts[1]!.sourceNumber).toBe("2");
    }
  });

  it("keeps the exact article, terminal compatibility and representations in the immutable snapshot", () => {
    const template = fixture(2);
    const connector = createConnectorInstanceFromComponentTemplateV3(template, {
      id: "J1", designation: "X1", articleVariant: template.content.articleVariants.at(-1)!, e4Position: { x: 1, y: 2 },
    });
    expect(connector.partNumber).toBe("XH-2");
    expect(connector.libraryBinding?.mode === "template" && connector.libraryBinding.snapshot.contacts[0]!.allowedTerminalArticleKeys)
      .toEqual([
        { sourceId: "бд.тер", entityType: "terminal", articleKey: "T-1" },
        { sourceId: "бд.тер", entityType: "terminal", articleKey: "T-2" },
      ]);
    expect(connector.libraryBinding?.mode === "template" && connector.libraryBinding.snapshot.contacts[0]!.representations)
      .toEqual(expect.arrayContaining([expect.objectContaining({ viewKind: "e4", x: 12, y: 18 })]));
    expect(connector.libraryBinding?.mode === "template" && connector.libraryBinding.snapshot.assets[0])
      .toEqual(expect.objectContaining({ fileName: "front.png", sha256: "b".repeat(64) }));
    expect(Object.isFrozen(connector.libraryBinding)).toBe(true);
    expect(Object.isFrozen(connector.libraryBinding?.mode === "template" ? connector.libraryBinding.snapshot : null)).toBe(true);
  });

  it("round-trips the template binding through the existing schema-1 harness parser", () => {
    const template = fixture(2);
    const connector = createConnectorInstanceFromComponentTemplateV3(template, {
      id: "J1", designation: "X1", articleVariant: template.content.articleVariants.at(-1)!.id, e4Position: { x: 0, y: 0 },
    });
    const document = {
      schemaVersion: 1 as const,
      connectors: [connector], wires: [], junctions: [], diffPairs: [], screens: [],
      views: {
        e4: { layers: [
          { id: "connectors", name: "Соединители", order: 1, visible: true, locked: false },
          { id: "wires", name: "Провода", order: 0, visible: true, locked: false },
          { id: "dimensions", name: "Размеры", order: 2, visible: true, locked: false },
        ], wireCrossingStyle: "none" as const },
        drawing: { layers: [
          { id: "connectors", name: "Соединители", order: 1, visible: true, locked: false },
          { id: "wires", name: "Провода", order: 0, visible: true, locked: false },
          { id: "dimensions", name: "Размеры", order: 2, visible: true, locked: false },
        ], wireCrossingStyle: "none" as const },
      },
    };
    const parsed = parseHarnessDesignDocument(JSON.parse(JSON.stringify(document)));
    expect(parsed.connectors[0]).toMatchObject({
      libraryBinding: { mode: "template", templateId: "template-component-1", templateVersion: 7 },
    });
    expect(parsed.connectors[0]!.contacts[0]!.logicalContactId).toBe(connector.contacts[0]!.logicalContactId);
  });

  it("round-trips a 512-character template article through the harness parser", () => {
    const template = fixture(2);
    const articleKey = "A".repeat(512);
    const selected = template.content.articleVariants.at(-1)!;
    selected.articleKey = articleKey;
    const longArticleTemplate = {
      ...template,
      articleBindings: [{ sourceId: selected.sourceId, entityType: selected.entityType, articleKey }],
    };
    const connector = createConnectorInstanceFromComponentTemplateV3(longArticleTemplate, {
      id: "J-long", designation: "X1", articleVariantId: selected.id, e4Position: { x: 0, y: 0 },
    });
    const document = {
      schemaVersion: 1 as const,
      connectors: [connector], wires: [], junctions: [], diffPairs: [], screens: [],
      views: {
        e4: { layers: [
          { id: "connectors", name: "Соединители", order: 1, visible: true, locked: false },
          { id: "wires", name: "Провода", order: 0, visible: true, locked: false },
          { id: "dimensions", name: "Размеры", order: 2, visible: true, locked: false },
        ], wireCrossingStyle: "none" as const },
        drawing: { layers: [
          { id: "connectors", name: "Соединители", order: 1, visible: true, locked: false },
          { id: "wires", name: "Провода", order: 0, visible: true, locked: false },
          { id: "dimensions", name: "Размеры", order: 2, visible: true, locked: false },
        ], wireCrossingStyle: "none" as const },
      },
    };

    expect(connector.partNumber).toBe(articleKey);
    const parsed = parseHarnessDesignDocument(JSON.parse(JSON.stringify(document)));
    expect(parsed.connectors[0]?.partNumber).toBe(articleKey);
    expect(parsed.connectors[0]?.libraryBinding).toMatchObject({
      mode: "template",
      article: { articleKey },
      snapshot: { article: { articleKey } },
    });
  });

  it("does not mutate the template or the selected variant", () => {
    const template = fixture(2);
    const before = structuredClone(template);
    createConnectorInstanceFromComponentTemplateV3(template, {
      id: "J1", designation: "X1", articleVariant: template.content.articleVariants.at(-1)!.id, e4Position: { x: 0, y: 0 },
    });
    expect(template).toEqual(before);
  });

  it("does not follow later library mutations and rejects a mismatched asset envelope", () => {
    const template = fixture(2);
    const connector = createConnectorInstanceFromComponentTemplateV3(template, {
      id: "J1", designation: "X1", articleVariantId: template.content.articleVariants.at(-1)!.id, e4Position: { x: 0, y: 0 },
    });
    template.content.logicalContacts[0]!.name = "Изменённая библиотека";
    template.content.articleVariants.at(-1)!.articleKey = "NEW";
    expect(connector.partNumber).toBe("XH-2");
    expect(connector.libraryBinding?.mode === "template" && connector.libraryBinding.snapshot.contacts[0]!.name)
      .toBe("Сигнал");

    const damaged = fixture(2);
    // Asset metadata is immutable by contract; emulate a corrupted payload at the
    // boundary where untrusted JSON is decoded instead of mutating the typed model.
    const damagedPayload = structuredClone(damaged) as unknown as {
      assets: Array<{ sha256: string; [key: string]: unknown }>;
    };
    damagedPayload.assets[0] = { ...damagedPayload.assets[0]!, sha256: "c".repeat(64) };
    expect(() => createConnectorInstanceFromComponentTemplateV3(damagedPayload as unknown as typeof damaged, {
      id: "J2", designation: "X2", articleVariantId: damaged.content.articleVariants.at(-1)!.id, e4Position: { x: 0, y: 0 },
    })).toThrow(/Ресурсы шаблона не совпадают/);
  });
});


describe("editable template contact purpose", () => {
  it.each([true, false])("uses the series name visibility %s in placement geometry", visible => {
    const template = fixture(2);
    const v5 = upgradeTemplateContentV4ToV5(upgradeTemplateContentV3ToV4(template.content as TemplateContentV3).content).content;
    const content = { ...v5, e4ConnectorTable: { ...v5.e4ConnectorTable,
      columns: v5.e4ConnectorTable.columns.map(column => column.id === "name" ? { ...column, visible } : column) } };
    const connector = createConnectorInstanceFromComponentTemplateV3({ ...template, content }, {
      id: "J1", designation: "X1", articleVariantId: content.articleVariants.at(-1)!.id, e4Position: { x: 0, y: 0 },
    });
    expect(connector.schematic.showName).toBe(visible);
    expect(connectorE4TableGeometry(connector).columns.some(column => column.kind === "custom" && column.id === "template-name")).toBe(visible);
  });

  it("retains explicit text and empty overrides through serialization and article rematerialization", () => {
    const template = fixture(2);
    const alternative = { ...template.content.articleVariants.at(-1)!, id: crypto.randomUUID(), articleKey: "XH-2-ALT" };
    (template.content as TemplateContentV3).articleVariants.unshift(alternative as ArticleVariantV3);
    const placed = createConnectorInstanceFromComponentTemplateV3(template, {
      id: "J1", designation: "X1", articleVariantId: template.content.articleVariants.at(-1)!.id, e4Position: { x: 0, y: 0 },
    });
    for (const nameOverride of ["Мой сигнал", ""]) {
      const edited = { ...placed, schematic: { ...placed.schematic, showName: false }, contacts: placed.contacts.map(contact => ({ ...contact, nameOverride })) };
      const restored = parseHarnessDesignDocument(JSON.parse(JSON.stringify({ ...createEmptyHarnessDesign(), connectors: [edited] }))).connectors[0]!;
      const changed = rematerializeComponentTemplateConnectorArticle(restored, template, alternative.id);
      expect(connectorContactName(changed, changed.contacts[0]!)).toBe(nameOverride);
      expect(changed.schematic.showName).toBe(false);
      expect(changed.partNumber).toBe("XH-2-ALT");
      expect(changed.libraryBinding).toMatchObject({ mode: "template", templateId: template.templateId, templateVersion: template.version, versionSha256: template.versionSha256 });
      expect(placed.partNumber).toBe("XH-2");
    }
    expect(connectorContactName(placed, placed.contacts[0]!)).toBe("Сигнал");
  });
});


describe("M-05A source contact numbering", () => {
  function numberedTemplate(numbers = ["10", "20"]) {
    const template=fixture(2);
    const content=upgradeTemplateContentV4ToV5(upgradeTemplateContentV3ToV4(template.content as TemplateContentV3).content).content;
    content.e4ConnectorTable.seriesDefaults.forEach((row,index)=>{row.values.number=numbers[index]!;});
    return {...template,content};
  }
  const place=(template=numberedTemplate(),id="J1",legacyNumberingPreview=false)=>createConnectorInstanceFromComponentTemplateV3(template,{
    id,designation:id,articleVariantId:template.content.articleVariants.at(-1)!.id,e4Position:{x:id==="J1"?0:600,y:0},legacyNumberingPreview,
  });

  it("keeps 10/20 in placement, E4, drawing labels and pinned snapshots after save/reopen",()=>{
    const template=numberedTemplate(),a=place(template),b=place(template,"J2");
    const wire=createWire("wire-10-20",{connectorId:a.id,contactId:a.contacts[0]!.id},{connectorId:b.id,contactId:b.contacts[1]!.id});
    const doc=parseHarnessDesignDocument(JSON.parse(JSON.stringify(applyEditorCommand({...createEmptyHarnessDesign(),connectors:[a,b]},{type:"add-wire",wire}))));
    expect(doc.connectors.map(c=>c.contacts.map(contact=>contact.number))).toEqual([[10,20],[10,20]]);
    expect(componentPlacementRequest(a,0,"command").instance.libraryBinding).toMatchObject({contactNumbering:"source-v1"});
    expect(doc.wires[0]!.from).toEqual(wire.from);expect(doc.wires[0]!.to).toEqual(wire.to);
    const e4=designToScene(doc,"e4").find(o=>o.id===a.id)!;
    expect(JSON.parse(e4.metadata!.rows!).map((row:{number:number})=>row.number)).toEqual([10,20]);
    expect(connectionEndLabel(doc,wire.from)).toContain("10");expect(connectionEndLabel(doc,wire.to)).toContain("20");
    for(const view of ["e4","drawing"] as const)expect(connectorContactPosition(doc.connectors[0]!,a.contacts[0]!.id,view)).toEqual(connectorContactPosition(a,a.contacts[0]!.id,view));
    const html=renderToStaticMarkup(createElement(E4ConnectorInspector,{connector:doc.connectors[0]!,disabled:false,mode:"canvas",onCommand:()=>{}}));
    expect(html).toContain(">10<");expect(html).toContain(">20<");
    expect(doc.connectors[0]!.libraryBinding).toEqual(a.libraryBinding);
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(doc)))).toEqual(doc);
  });

  it("restores legacy numbers atomically without moving contacts or reconnecting wires",()=>{
    const template=numberedTemplate(),a=place(template,"J1",true),b=place(template,"J2",true);
    const wire=createWire("legacy-wire",{connectorId:a.id,contactId:a.contacts[0]!.id},{connectorId:b.id,contactId:b.contacts[1]!.id});
    const legacy=applyEditorCommand({...createEmptyHarnessDesign(),connectors:[a,b]},{type:"add-wire",wire});
    const before=JSON.stringify(legacy),restored=parseHarnessDesignDocument(legacy);
    expect(restored.connectors[0]!.contacts.map(c=>c.number)).toEqual([10,20]);
    expect(restored.connectors[0]!.contacts.map(c=>c.id)).toEqual(a.contacts.map(c=>c.id));
    expect(restored.wires[0]!.from).toEqual(wire.from);expect(restored.wires[0]!.to).toEqual(wire.to);
    expect(restored.connectors[0]!.positions).toEqual(a.positions);
    expect(restored.connectors[0]!.libraryBinding).toMatchObject({contactNumbering:"source-v1",snapshot:(a.libraryBinding?.mode === "template" ? a.libraryBinding.snapshot : {})});
    expect(JSON.stringify(legacy)).toBe(before);
    expect(parseHarnessDesignDocument(JSON.parse(JSON.stringify(restored)))).toEqual(restored);
  });

  it.each([["A1","20"],["01","1"],["0","20"],["301","20"],["1.5","20"],["1e1","20"]])("rejects ambiguous or unsupported new numbers %s/%s without ordinal fallback",(a,b)=>{
    const template=numberedTemplate([a,b]);
    expect(()=>place(template)).toThrow(/Номер контакта|номера контактов/);
    const legacy=place(template,"J1",true);
    const restored=parseHarnessDesignDocument({...createEmptyHarnessDesign(),connectors:[legacy]});
    expect(restored.connectors[0]!.contacts.map(c=>c.number)).toEqual([1,2]);
    expect(templateContactNumberingWarning(restored.connectors[0]!)).toContain("Сохранена прежняя нумерация");
    expect(()=>componentPlacementRequest(legacy,0,"command")).toThrow(/исходные номера/);
  });

  it.each([false,true])("matches source numbers by logical ID when snapshot order changes (legacy %s)",legacy=>{
    const connector=place(numberedTemplate(),"J1",legacy);
    const binding=connector.libraryBinding!;
    if(binding.mode!=="template") throw new Error("expected template binding");
    const reordered={...binding,snapshot:{...binding.snapshot,contacts:[...binding.snapshot.contacts].reverse()}};
    const restored=parseHarnessDesignDocument({...createEmptyHarnessDesign(),connectors:[{...connector,libraryBinding:reordered}]});
    expect(restored.connectors[0]!.contacts.map(contact=>contact.number)).toEqual([10,20]);
  });

  it("rejects tampered numbered instances and unknown contract versions",()=>{
    const connector=place();
    const doc=()=>JSON.parse(JSON.stringify({...createEmptyHarnessDesign(),connectors:[connector]}));
    const wrong=doc();wrong.connectors[0].contacts[0].number=1;
    expect(()=>parseHarnessDesignDocument(wrong)).toThrow(/Контакты не соответствуют/);
    const future=doc();future.connectors[0].libraryBinding.contactNumbering="source-v2";
    expect(()=>parseHarnessDesignDocument(future)).toThrow(/Неизвестная версия/);
  });
});
