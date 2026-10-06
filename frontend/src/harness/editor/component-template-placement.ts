import {templateContactNumbers} from "./template-contact-numbering";
import { evaluateNumericExpressionV3 } from "../component-library/template-commands-v3";
import { resolveTemplateParameterValuesV2 } from "../component-library/template-repeat-v2";
import { materializeArticleVariantV3 } from "../component-library/template-article-materialization-v3";
import { drawingArrayContactRows } from "../component-library/drawing-array-contacts";
import { materializeGenerator } from "../component-library/drawing-generator";
import {
  parseConnectorSchematic,
  defaultLayerIds,
  validateConnectorLibraryMetadata,
  type ComponentTemplateArticleKeySnapshot,
  type ComponentTemplateAssetSnapshot,
  type ComponentTemplateContactRepresentationSnapshot,
  type ComponentTemplateContactSnapshot,
  type ComponentTemplateMaterializedSnapshot,
  type ConnectorContact,
  type ConnectorInstance,
  type Point,
} from "./model";
import {
  materializeArticleContactRowsV3,
  type MaterializedArticleContactRowV3,
} from "../component-library/template-article-contact-rows-v3";
import { findArticleDrawing } from "../component-library/drawing-bindings";
import { materializeE4ConnectorArticle } from "../component-library/e4-connector-series-table";
import { ArticleVariantMaterializationV3Error } from "../component-library/template-article-materialization-v3";
import {
  reconcileTemplateEnvelopeAssets,
  type TemplateEnvelopeAsset,
} from "../component-library/template-content";
import { validateTemplateContentV3 } from "../component-library/template-model-v3";
import type {
  ArticleKeyV3,
  ArticleVariantV3,
  TemplateContentV3,
} from "../component-library/template-model-v3";
import { validateTemplateContentV4, type TemplateContentV4 } from "../component-library/template-model-v4";
import {
  projectTemplateContentV5TableToV1,
  projectTemplateContentV5ToV3,
  validateTemplateContentV5,
  type TemplateContentV5,
} from "../component-library/template-model-v5";

type SupportedTemplateContent = TemplateContentV3 | TemplateContentV4 | TemplateContentV5;

/** The immutable data needed from the component-library response at placement time. */
export interface ComponentTemplatePlacementEnvelopeV3 {
  readonly templateId: string;
  readonly version: number;
  readonly versionSha256: string;
  readonly code: string;
  readonly name: string;
  readonly articleBindings: readonly ArticleKeyV3[];
  readonly assets: readonly TemplateEnvelopeAsset[];
  readonly content: SupportedTemplateContent;
}

export type ComponentTemplatePlacementEnvelope = ComponentTemplatePlacementEnvelopeV3;

export interface CreateComponentTemplateConnectorOptions {
  /** Internal authoring/legacy verification only; never submit as a new placement. */
  readonly legacyNumberingPreview?: boolean;
  readonly id: string;
  readonly designation: string;
  /** Either the full v3 variant or its stable variant ID. */
  readonly articleVariant?: (ArticleKeyV3 & { readonly id: string }) | string;
  /** Alias useful to catalog UIs that carry only the selected variant ID. */
  readonly articleVariantId?: string;
  readonly e4Position: Point;
  readonly drawingPosition?: Point;
  readonly layerIds?: Readonly<Partial<Record<"e4" | "drawing", string>>>;
}

/**
 * Materializes one selected v3 article into the editor's established
 * ConnectorInstance shape. Each runtime contact keeps the row key separately
 * from its numeric display number, so repeated template rows remain stable.
 */
export function createConnectorInstanceFromComponentTemplateV3(
  template: ComponentTemplatePlacementEnvelopeV3,
  options: CreateComponentTemplateConnectorOptions,
): ConnectorInstance {
  requireText(template.templateId, "ID шаблона компонента");
  requirePositiveInteger(template.version, "Версия шаблона компонента");
  requireSha256(template.versionSha256, "Хэш версии шаблона компонента");
  const code = requireBoundedText(template.code, "Код шаблона компонента", 120);
  const name = requireBoundedText(template.name, "Название шаблона компонента", 256);
  const content = template.content;
  if (content.schemaVersion !== 3 && content.schemaVersion !== 4 && content.schemaVersion !== 5)
    throw new Error("Для размещения требуется содержимое шаблона v3, v4 или v5.");
  const validation = content.schemaVersion === 5 ? validateTemplateContentV5(content)
    : content.schemaVersion === 4 ? validateTemplateContentV4(content) : validateTemplateContentV3(content);
  if (!validation.valid) throw new Error(`Шаблон v${content.schemaVersion} не прошёл проверку: ${validation.diagnostics[0]?.message ?? "повреждённое содержимое"}`);
  if (reconcileTemplateEnvelopeAssets(content, template.assets).diagnostics.length > 0) {
    throw new Error("Ресурсы шаблона не совпадают с ресурсами его закрепляемой версии.");
  }
  const id = requireText(options.id, "ID соединителя");
  const selectedVariant = options.articleVariant ?? options.articleVariantId ?? firstPlaceableArticleVariantId(content);
  if (selectedVariant === undefined) throw new Error("В шаблоне нет варианта артикула для размещения.");
  const variant = selectedArticleVariant(content, selectedVariant);
  const rows = materializePlacementRows(content, variant.id);
  if (rows.length < 1 || rows.length > 300) {
    throw new Error("В выбранном варианте должно быть от 1 до 300 контактов.");
  }
  const article = articleKey(variant);
  // articleVariants belongs to the immutable version content and is the
  // placement authority. The summary/envelope index can lag behind that
  // version after a series edit, so deriving the snapshot index from content
  // keeps every inspector-selectable article available without trusting stale
  // catalog metadata.
  const articleBindings = uniqueArticleKeys(content.articleVariants);
  const assets = template.assets.map(snapshotAsset);
  const numbers = options.legacyNumberingPreview ? rows.map((_, index) => index + 1)
    : templateContactNumbers(rows.map(row => row.number));
  const contacts = rows.map((row, index) => createContact(row, numbers[index]!, content, id));
  const snapshot: ComponentTemplateMaterializedSnapshot = freezeSnapshot({
    templateId: template.templateId,
    templateVersion: template.version,
    versionSha256: template.versionSha256,
    code,
    name,
    articleVariantId: variant.id,
    article,
    articleBindings,
    assets,
    contacts: rows.map((row) => snapshotContact(row, content)),
  });
  const connector: ConnectorInstance = {
    ...(content.schemaVersion === 5 ? { e4TableMode: true } : {}),
    id,
    designation: requireText(options.designation, "Обозначение соединителя"),
    libraryCode: code,
    partNumber: article.articleKey,
    contacts,
    schematic: {
      ...parseConnectorSchematic(content.schemaVersion === 5 ? content.e4Presentation : undefined),
      ...(content.schemaVersion === 3 ? {} : {
        showName: content.e4ConnectorTable.columns.find(column => column.id === "name")?.visible ?? true,
      }),
    },
    positions: {
      e4: copyPosition(options.e4Position),
      drawing: copyPosition(options.drawingPosition ?? options.e4Position),
    },
    layerIds: {
      e4: options.layerIds?.e4 ?? defaultLayerIds.connectors,
      drawing: options.layerIds?.drawing ?? defaultLayerIds.connectors,
    },
    libraryBinding: Object.freeze({
      mode: "template",
      ...(options.legacyNumberingPreview ? {} : {contactNumbering: "source-v1" as const}),
      templateId: template.templateId,
      templateVersion: template.version,
      versionSha256: template.versionSha256,
      articleVariantId: variant.id,
      article: Object.freeze({ ...article }),
      snapshot,
    }),
  };
  validateConnectorLibraryMetadata(connector);
  return connector;
}

/** Schema-neutral placement entry point. The v3 name remains as a compatibility alias. */
export const createConnectorInstanceFromComponentTemplate = createConnectorInstanceFromComponentTemplateV3;

/** A series card has no article selection; use its first article with E4 rows. */
export function firstPlaceableArticleVariantId(content: SupportedTemplateContent): string | undefined {
  if (content.schemaVersion === 3) return content.articleVariants[0]?.id;
  const rowCounts = new Map(content.e4ConnectorTable.articles.map(article => [article.articleVariantId, article.rows.length]));
  return content.articleVariants.find(article => (rowCounts.get(article.id) ?? 0) > 0)?.id
    ?? content.articleVariants[0]?.id;
}

/**
 * Re-materializes another article from the same immutable template version.
 * Editable contact values survive when their stable logical row remains present;
 * a v4 standard terminal initializes newly introduced rows.
 */
export function rematerializeComponentTemplateConnectorArticle(
  connector: ConnectorInstance,
  template: ComponentTemplatePlacementEnvelope,
  articleVariantId: string,
): ConnectorInstance {
  const binding = connector.libraryBinding;
  if (binding?.mode !== "template") throw new Error("Соединитель не привязан к шаблону компонента.");
  if (binding.templateId !== template.templateId || binding.templateVersion !== template.version ||
      binding.versionSha256 !== template.versionSha256) {
    throw new Error("Смена артикула возможна только внутри закреплённой версии шаблона.");
  }
  const materialized = createConnectorInstanceFromComponentTemplateV3(template, {
    id: connector.id,
    designation: connector.designation,
    articleVariantId,
    e4Position: connector.positions.e4,
    drawingPosition: connector.positions.drawing,
    layerIds: connector.layerIds,
  });
  const previous = new Map(connector.contacts.map(contact => [contact.logicalContactId, contact]));
  return {
    ...materialized,
    ...(connector.terminalCatalog ? { terminalCatalog: connector.terminalCatalog } : {}),
    schematic: connector.schematic,
    contacts: materialized.contacts.map(contact => {
      const old = previous.get(contact.logicalContactId);
      if (!old) return { ...contact, customValues: Object.fromEntries(connector.schematic.customFields.map(field => [field.id, ""])) };
      const terminalAllowed = !old.terminalArticle || materialized.libraryBinding?.mode !== "template" ||
        connector.terminalCatalog?.byContact[contact.logicalContactId ?? ""]?.includes(old.terminalArticle) ||
        materialized.libraryBinding.snapshot.contacts.find(item => item.logicalContactId === contact.logicalContactId)
          ?.allowedTerminalArticleKeys.some(item => item.articleKey === old.terminalArticle);
      return {
        ...contact,
        ...(old.nameOverride === undefined ? {} : { nameOverride: old.nameOverride }),
        circuit: old.circuit,
        terminalArticle: terminalAllowed ? old.terminalArticle : contact.terminalArticle,
        wire: old.wire,
        wireSection: old.wireSection,
        materialBinding: old.materialBinding,
        color: old.color,
        secondaryColor: old.secondaryColor,
        connectionStatus: old.connectionStatus,
        customValues: { ...old.customValues },
      };
    }),
  };
}

interface PlacementContactRow extends MaterializedArticleContactRowV3 {
  wire?: string;
  wireSection?: string;
  color?: string;
  secondaryColor?: string;
  customValues?: Record<string, string>;
  readonly standardTerminalArticleKey: ArticleKeyV3 | null;
}

function asV3Core(content: SupportedTemplateContent): TemplateContentV3 {
  if (content.schemaVersion === 3) return content;
  if (content.schemaVersion === 5) return projectTemplateContentV5ToV3(content);
  const { e4ConnectorTable: _table, ...core } = content;
  return { ...core, schemaVersion: 3 };
}

export function materializePlacementRows(
  content: SupportedTemplateContent,
  articleVariantId: string,
): readonly PlacementContactRow[] {
  const core = asV3Core(content);
  let coreRows: readonly MaterializedArticleContactRowV3[];
  try {
    coreRows = materializeArticleContactRowsV3(core, articleVariantId);
  } catch (error) {
    if (content.schemaVersion !== 5 || !(error instanceof ArticleVariantMaterializationV3Error) ||
        !["contact_count_below_fixed", "contact_count_not_divisible", "repeat_count_out_of_range",
          "ambiguous_group_repeat", "repeat_parameter_conflict"].includes(error.code)) throw error;
    // Electrical counts in v5 are independent of optional graphical repeats.
    // Preserve valid graphical parameters, but do not derive them from E4 counts.
    coreRows = materializeArticleContactRowsV3({ ...core,
      articleVariants: core.articleVariants.map(variant => ({ ...variant, contactGroups: null })),
    }, articleVariantId);
  }
  if (content.schemaVersion === 3) return coreRows.map(row => ({ ...row, standardTerminalArticleKey: null }));
  const table = content.schemaVersion === 5 ? projectTemplateContentV5TableToV1(content) : content.e4ConnectorTable;
  const tableArticle = materializeE4ConnectorArticle(table, articleVariantId);
  const generated = content.schemaVersion === 5 ? (content.drawingGenerators ?? []).filter(g => g.target === "drawing" && g.articles.some(a => a.articleId === articleVariantId)).map(g => materializeGenerator(core, table, content.drawingContactBindings ?? [], g, articleVariantId)) : [];
  const arrayContacts=content.schemaVersion===5?drawingArrayContactRows(core,table,content.drawingContactBindings??[],articleVariantId):[];
  const drawing=content.schemaVersion===5?findArticleDrawing(content.articleDrawings,articleVariantId,"drawing"):undefined;
  const drawingView=core.views.find(v=>drawing?.viewId?v.id===drawing.viewId:v.kind==="drawing");
  const port=drawingView?.bundlePorts.find(p=>drawing?.bundlePortIds?.includes(p.id));
  const materialized=port?materializeArticleVariantV3({...core,articleVariants:core.articleVariants.map(a=>({...a,contactGroups:null}))},articleVariantId):null;
  const values=materialized?resolveTemplateParameterValuesV2(materialized.repeatContent,materialized.repeatOptions):null;
  const common=port&&drawingView&&values?{viewId:drawingView.id,viewName:drawingView.name,viewKind:"drawing" as const,pointId:port.id,x:evaluateNumericExpressionV3(port.x,values),y:evaluateNumericExpressionV3(port.y,values),direction:port.direction}:null;
  const arrayPoints=new Set(arrayContacts.map(item=>`${item.viewId}:${item.point.prototypeContactPointId}`));
  const allowedByGroup = new Map(tableArticle.contactGroups.map(group => [
    group.contactTypeGroupId,
    group.allowedTerminalArticleKeys,
  ]));
  const coreByKey = new Map(coreRows.map(row => [row.key, row]));
  const explicitBindings = new Map(content.schemaVersion === 5 ? content.drawingContactBindings?.map(binding => [binding.seriesRowId, binding.logicalContactId]) : []);
  const explicitlyBoundContacts = new Set(explicitBindings.values());
  const mayUseIndexFallback = coreRows.length === tableArticle.rows.length;
  return tableArticle.rows.map((tableRow, index) => {
    // The E4 table is the published electrical model. The graphical core only
    // supplies optional point representations for rows it can materialize.
    const boundId = explicitBindings.get(tableRow.seriesRowId);
    const candidate = coreByKey.get(tableRow.seriesRowId) ?? (mayUseIndexFallback ? coreRows[index] : undefined);
    const core = boundId ? coreByKey.get(boundId) : candidate && !explicitlyBoundContacts.has(candidate.prototypeLogicalContactId) ? candidate : undefined;
    const allowed = content.schemaVersion === 5 ? content.compatibleTerminalArticleKeys
      : tableRow.contactTypeGroupId === null ? [] : allowedByGroup.get(tableRow.contactTypeGroupId) ?? [];
    return {
      key: tableRow.seriesRowId,
      wire: tableRow.wire,
      wireSection: tableRow.wireSection,
      color: tableRow.color,
      secondaryColor: tableRow.secondaryColor,
      customValues: tableRow.customValues,
      prototypeLogicalContactId: core?.prototypeLogicalContactId ?? tableRow.seriesRowId,
      representations: [...generated.flatMap(g=>g.points.filter(p=>p.row.seriesRowId===tableRow.seriesRowId).map(({point})=>({viewId:g.view.id,viewName:g.view.name,viewKind:"drawing" as const,pointId:point.id,x:point.x.kind==="constant"?point.x.value:0,y:point.y.kind==="constant"?point.y.value:0,direction:point.direction}))),...(common?[common]:[]),...(core?.representations ?? []).filter(r=>!arrayPoints.has(`${r.viewId}:${r.pointId}`)),...arrayContacts.filter(item=>item.row?.seriesRowId===tableRow.seriesRowId).map(item=>({viewId:item.viewId,viewName:item.viewName,viewKind:item.viewKind,pointId:item.point.prototypeContactPointId,occurrenceKey:item.point.key,x:item.point.x,y:item.point.y,direction:item.point.direction}))].filter(representation => {
        if(content.views.some(v=>v.contactPoints.some(p=>p.id===representation.pointId && p.shape)))return false;
        if (generated.length && representation.viewKind === "drawing") return generated.some(g=>g.points.some(p=>p.point.id===representation.pointId));
        const drawing = content.schemaVersion === 5 ? findArticleDrawing(content.articleDrawings,articleVariantId,"drawing") : undefined;
        return representation.viewKind !== "drawing" || (common ? representation.pointId===common.pointId : !drawing || drawing.contactPointIds.includes(representation.pointId));
      }),
      contactTypeGroupId: tableRow.contactTypeGroupId,
      number: tableRow.number,
      name: tableRow.name,
      circuitText: tableRow.circuitText,
      allowedTerminalArticleKeys: allowed,
      standardTerminalArticleKey: tableRow.standardTerminalArticleKey,
    };
  });
}

function selectedArticleVariant(
  content: SupportedTemplateContent,
  selected: (ArticleKeyV3 & { readonly id: string }) | string,
): SupportedTemplateContent["articleVariants"][number] {
  const id = typeof selected === "string" ? selected : selected.id;
  const variant = content.articleVariants.find((candidate) => candidate.id === id);
  if (!variant) throw new Error(`Вариант артикула ${id} отсутствует в шаблоне.`);
  return variant as SupportedTemplateContent["articleVariants"][number];
}

function articleKey(variant: ArticleKeyV3): ComponentTemplateArticleKeySnapshot {
  return Object.freeze({
    sourceId: requireBoundedText(variant.sourceId, "Источник артикула", 128).normalize("NFC").toLowerCase(),
    entityType: requireBoundedText(variant.entityType, "Тип артикула", 64).normalize("NFC").toLowerCase(),
    articleKey: requireBoundedText(variant.articleKey, "Ключ артикула", 512),
  });
}

function uniqueArticleKeys(values: readonly ArticleKeyV3[]): readonly ComponentTemplateArticleKeySnapshot[] {
  const result: ComponentTemplateArticleKeySnapshot[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    const normalized = articleKey(value);
    const key = `${normalized.sourceId}\0${normalized.entityType}\0${normalized.articleKey}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(normalized);
  }
  return Object.freeze(result);
}

function snapshotAsset(asset: TemplateEnvelopeAsset): ComponentTemplateAssetSnapshot {
  return Object.freeze({
    assetId: requireText(asset.assetId, "ID ресурса шаблона"),
    sha256: requireSha256(asset.sha256, "Хэш ресурса шаблона"),
    sizeBytes: requireInteger(asset.sizeBytes, "Размер ресурса шаблона", 0, 10 * 1024 * 1024),
    fileName: requireBoundedText(asset.fileName, "Имя ресурса шаблона", 255),
    mediaType: requireBoundedText(asset.mediaType, "Тип ресурса шаблона", 120),
  });
}

function createContact(
  row: PlacementContactRow,
  number: number,
  content: SupportedTemplateContent,
  connectorId: string,
): ConnectorContact {
  const group = row.contactTypeGroupId === null
    ? null
    : content.contactTypeGroups.find((candidate) => candidate.id === row.contactTypeGroupId);
  return {
    id: `${connectorId}:contact:${row.key}`,
    logicalContactId: row.key,
    number,
    contactType: group?.name ?? "",
    circuit: row.circuitText ?? "",
    terminalArticle: row.standardTerminalArticleKey?.articleKey ?? "",
    wire: row.wire ?? "",
    ...(row.wireSection === undefined ? {} : { wireSection: row.wireSection }),
    color: row.color ?? "",
    secondaryColor: row.secondaryColor ?? "",
    colorMode: row.color || row.secondaryColor ? "manual" : "auto",
    connectionStatus: "available",
    customValues: { ...row.customValues },
    libraryContact: null,
  };
}

function snapshotContact(
  row: PlacementContactRow,
  content: SupportedTemplateContent,
): ComponentTemplateContactSnapshot {
  const group = row.contactTypeGroupId === null
    ? null
    : content.contactTypeGroups.find((candidate) => candidate.id === row.contactTypeGroupId);
  return Object.freeze({
    logicalContactId: row.key,
    prototypeLogicalContactId: row.prototypeLogicalContactId,
    sourceNumber: row.number,
    name: row.name,
    circuitText: row.circuitText,
    contactTypeGroupId: row.contactTypeGroupId,
    contactType: group?.name ?? "",
    allowedTerminalArticleKeys: uniqueArticleKeys(row.allowedTerminalArticleKeys),
    representations: Object.freeze(row.representations.map((representation): ComponentTemplateContactRepresentationSnapshot =>
      Object.freeze({ ...representation }))),
  });
}

function freezeSnapshot(snapshot: ComponentTemplateMaterializedSnapshot): ComponentTemplateMaterializedSnapshot {
  Object.freeze(snapshot.article);
  Object.freeze(snapshot.articleBindings);
  Object.freeze(snapshot.assets);
  for (const contact of snapshot.contacts) {
    Object.freeze(contact.allowedTerminalArticleKeys);
    Object.freeze(contact.representations);
    Object.freeze(contact);
  }
  Object.freeze(snapshot.contacts);
  return Object.freeze(snapshot);
}

function requireText(value: unknown, name: string): string {
  if (typeof value !== "string" || !value.trim() || value.length > 1024) throw new Error(`${name} задано неверно.`);
  return value;
}

function copyPosition(position: Point): Point {
  if (!Number.isFinite(position.x) || !Number.isFinite(position.y)) throw new Error("Положение соединителя задано неверно.");
  return { x: position.x, y: position.y };
}

function requireBoundedText(value: unknown, name: string, maximum: number): string {
  const result = requireText(value, name);
  if (result.length > maximum) throw new Error(`${name} задано неверно.`);
  return result;
}

function requirePositiveInteger(value: unknown, name: string): number {
  return requireInteger(value, name, 1, 1_000_000);
}

function requireInteger(value: unknown, name: string, minimum: number, maximum: number): number {
  if (!Number.isSafeInteger(value) || Number(value) < minimum || Number(value) > maximum) throw new Error(`${name} задано неверно.`);
  return Number(value);
}

function requireSha256(value: unknown, name: string): string {
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) throw new Error(`${name} задан неверно.`);
  return value;
}
