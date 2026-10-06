import { drawingTableColumnWidths } from "./drawing-table-column-widths";
import {validateCoveringLibrary,type CoveringLibrary} from "./covering-library";
import { drawingObjectPerimeter, type DrawingPerimeters } from "./drawing-object-perimeter";
import {initialLinearLeader} from "./drawing-leader-placement";
import { drawingLocalPoint, drawingPointToLocal } from "./drawing-scale";
import { moveDrawingDimension, validateDrawingDimensions, type DrawingDimension, type DimensionMode } from "./drawing-dimensions";
import type { EditorSceneObject } from "./editor-types";
import { findWireEndpoint, calculateWireCutLength, type HarnessDesignDocument, type Point, type WireEndpoint } from "./model";
import { coveringPaths, coveringMeasuredLength } from "./physical-coverings";
import { physicalNodePoint } from "./physical-ports";
import { physicalSegmentPoints } from "./physical-geometry";
import { movePositionLeaderOnRails, movePositionRail } from "./position-rail";
import { connectionTableColumnLabels, getConnectionTableSettings, type ConnectionTableColumnId } from "./connection-table-settings";
import { builtInWireColors, resolveWireColorHex } from "./wire-reference-catalog";
import { snapDrawingTranslation, type DrawingOutline, type DrawingSnaps } from "../component-library/drawing-geometry";
import { buildDrawingObjectIndices } from "./drawing-object-indices";
import { terminalArticleLabel } from "./terminal-article-label";
export { createPositionRail } from "./position-rail";

export interface DrawingTable { readonly id: string; readonly kind: "bom" | "connections" | "cut"; readonly position: Point; readonly dock?: "left" | "right" | "top" | "bottom"; readonly width?: number; readonly height?: number }
export interface PositionLeader { readonly id: string; readonly objectId: string; readonly rowKey: string; readonly anchorOffset: Point; readonly circle: Point; readonly anchorLocal?: Point; readonly hidden?: boolean }
export interface PositionRail { readonly id: string; readonly start: Point; readonly end: Point; readonly leaderIds: readonly string[] }
export interface DrawingSpecificationItem {
  readonly id: string;
  readonly kind: "abstract" | "manual";
  readonly type: string;
  readonly designation: string;
  readonly name: string;
  readonly amount: number | null;
  readonly unit: "шт." | "м" | "г" | "кг" | "л";
  readonly note: string;
  readonly objectId?: string;
  readonly sourceIdentity?: string;
  readonly position?: Point;
}
export type DrawingGraphicKind="contact"|"line"|"polyline"|"rectangle"|"ellipse"|"bezier"|"closedContour"|"text";
export interface DrawingGraphic {readonly id:string;readonly view:"drawing"|"e4";readonly kind:DrawingGraphicKind;readonly points:readonly Point[];readonly text?:string;readonly angle?:number;readonly fontFamily?:string;readonly fontSize?:number;readonly color?:string;readonly width?:number}
export interface DrawingDocuments { readonly graphics?:readonly DrawingGraphic[]; readonly coveringLibrary?:CoveringLibrary; readonly pipeOpacity?:number; readonly physicalScale?:number; readonly indexScale?:number; readonly indexOffsets?:Record<string,Point>; readonly leaderScale?:number; readonly dimensionScale?:number; readonly minimumCoveringOverlapPx?:number; /** Extra visible width at each OP/P covering transition edge, in drawing pixels. */ readonly opCoveringEdgePx?:number; readonly bendRadius?:number; /** Ratio between adjacent covering diameters (1:x). */ readonly coveringDiameterRatio?:number; readonly dimensionMode?:DimensionMode; readonly showDimensions?:boolean; readonly volumeShading?:boolean; readonly dimensions?:readonly DrawingDimension[]; readonly tables: readonly DrawingTable[]; readonly leaders: readonly PositionLeader[]; readonly rails?: readonly PositionRail[]; readonly bomOrder: readonly string[]; readonly bomText?: Record<string, {index?:string;designation?:string;name?:string;note?:string}>; readonly specificationItems?: readonly DrawingSpecificationItem[] }
export const emptyDrawingDocuments = (): DrawingDocuments => ({ tables: [], leaders: [], bomOrder: [], specificationItems: [] });
export interface BomRow {
  readonly key: string; readonly position: number; readonly index: string; readonly designation: string; readonly name: string;
  readonly amount: number | null; readonly unit: "шт." | "м" | "г" | "кг" | "л"; readonly note: string;
  readonly objectIds: readonly string[]; readonly sourceIdentity: string;
}

/** Material rows historically ended at sourceKey, before per-wire attributes. */
function legacyMaterialRowKey(key:string):string {
  try { const parts:unknown=JSON.parse(key);return Array.isArray(parts)&&parts[0]==="material"&&parts.length===7?JSON.stringify(parts.slice(0,6)):key; }
  catch { return key; }
}

/** Remove stale annotation references after an object or BOM row changes.
 * Leaders are keyed by stable object IDs; their row key is a projection and
 * must follow a replacement component instead of becoming a red orphan. */
export function reconcileDrawingDocuments(previous: HarnessDesignDocument, next: HarnessDesignDocument): HarnessDesignDocument {
  const docs = next.drawingDocuments;
  if (!docs) return next;
  if (!docs.leaders.length && !docs.bomOrder.length && !Object.keys(docs.bomText ?? {}).length &&
      !(docs.rails ?? []).some(rail => rail.leaderIds.length)) return next;
  const oldDocs = previous.drawingDocuments;
  const oldRows = oldDocs ? buildDrawingBom(previous) : [];
  const newRows = buildDrawingBom(next);
  const legacyRows = (key:string) => {
    if(newRows.some(row=>row.key===key))return [];
    const legacy=newRows.filter(row=>legacyMaterialRowKey(row.key)===key);
    if(legacy.length)return legacy;
    const old=oldRows.find(row=>row.key===key);
    return old?newRows.filter(row=>rowFamily(row.key)===rowFamily(key)&&row.objectIds.some(id=>old.objectIds.includes(id))):[];
  };
  const remap = new Map<string, string>();
  const rowFamily = (key: string): string => {
    const kind = JSON.parse(key)[0] as string;
    if (["free", "series", "connector"].includes(kind)) return "connector";
    if (["material", "material-unpinned"].includes(kind)) return "material";
    if (["terminal", "terminal-unpinned"].includes(kind)) return "terminal";
    if (["protection", "protection-unpinned"].includes(kind)) return "protection";
    return kind;
  };
  for (const oldRow of oldRows) {
    if (newRows.some(row => row.key === oldRow.key)) continue;
    const family = rowFamily(oldRow.key);
    const candidates = newRows.filter(row => rowFamily(row.key) === family &&
      row.objectIds.some(id => oldRow.objectIds.includes(id)));
    if (candidates.length === 1) remap.set(oldRow.key, candidates[0]!.key);
  }
  const validRows = new Set(newRows.map(row => row.key));
  const leaders = docs.leaders.flatMap(leader => {
    const legacy = legacyRows(leader.rowKey).find(row=>row.objectIds.includes(leader.objectId));
    const rowKey = legacy?.key ?? remap.get(leader.rowKey) ?? leader.rowKey;
    const row = newRows.find(candidate => candidate.key === rowKey && candidate.objectIds.includes(leader.objectId));
    const objectExists = drawingObjectOrigin(next, leader.objectId) !== null;
    return objectExists && row ? [{ ...leader, rowKey }] : [];
  });
  const leaderIds = new Set(leaders.map(leader => leader.id));
  const rails = docs.rails?.map(rail => ({ ...rail, leaderIds: rail.leaderIds.filter(id => leaderIds.has(id)) }))
    .filter((rail, index) => rail.leaderIds.length > 0 || docs.rails![index]!.leaderIds.length === 0);
  const bomOrder = [...new Set(docs.bomOrder.flatMap(key => {
    const legacy=legacyRows(key);return legacy.length?legacy.map(row=>row.key):[remap.get(key)??key];
  }).filter(key => validRows.has(key)))];
  const bomText = docs.bomText ? Object.fromEntries(Object.entries(docs.bomText).reduce<[string, NonNullable<DrawingDocuments["bomText"]>[string]][]>((items, [key, value]) => {
    const legacy=legacyRows(key);
    if(legacy.length){for(const [index,row] of legacy.entries()){
      const {index:oldIndex,...text}=value;
      items.push([row.key,{...text,...(index===0&&oldIndex!==undefined?{index:oldIndex}:{}),...docs.bomText?.[row.key]}]);
    }}else{
      const nextKey = remap.get(key) ?? key;
      if (validRows.has(nextKey)) items.push([nextKey, value]);
    }
    return items;
  }, [])) : undefined;
  if (leaders.length === docs.leaders.length && leaders.every((leader, index) => leader.rowKey === docs.leaders[index]!.rowKey) &&
      bomOrder.length === docs.bomOrder.length && bomOrder.every((key, index) => key === docs.bomOrder[index]) &&
      (!rails || rails.length === docs.rails?.length && rails.every((rail, index) => rail.leaderIds.length === docs.rails![index]!.leaderIds.length)) &&
      (!bomText || Object.keys(bomText).length === Object.keys(docs.bomText ?? {}).length && Object.keys(bomText).every(key=>key in (docs.bomText??{})) && ![...remap.keys()].some(key => key in (docs.bomText ?? {})))) return next;
  return { ...next, drawingDocuments: { ...docs, leaders, ...(rails ? { rails } : {}), bomOrder, ...(bomText ? { bomText } : {}) } };
}
const keyOf = (...values: unknown[]) => JSON.stringify(values);
const materialKey = (kind: string, binding: {sourceId:string;snapshotId:string;snapshotSha256:string;recordId:string;sourceKey:string}) => keyOf(kind,binding.sourceId,binding.snapshotId,binding.snapshotSha256,binding.recordId,binding.sourceKey);

/** Aggregation never merges textual articles across source snapshots. */
export function buildDrawingBom(document: HarnessDesignDocument, quantity = 1): BomRow[] {
  const rows = new Map<string, {index:string[];designation:string[];name:string;amountMicros:bigint;unknown:boolean;unit:"шт."|"м"|"г"|"кг"|"л";note:string;objectIds:Set<string>}>();
  const add = (key:string,id:string,designation:string,name:string,amount:number|null,unit:"шт."|"м"|"г"|"кг"|"л",note:string,index=designation) => {
    const row=rows.get(key) ?? {index:[],designation:[],name,amountMicros:0n,unknown:false,unit,note,objectIds:new Set<string>()};
    row.objectIds.add(id); if(index && !row.index.includes(index))row.index.push(index); if(designation && !row.designation.includes(designation)) row.designation.push(designation);
    row.unknown ||= amount===null;
    if(amount!==null) row.amountMicros+=BigInt(Math.round(amount*1e6));
    rows.set(key,row);
  };
  const indexedObjects = buildDrawingObjectIndices([
    ...document.wires.map((w, index) => ({ id: w.id, kind: "wire", metadata: { index: `W${index + 1}` } })),
    ...document.cables.map(c => ({ id: c.id, kind: "cable" })),
    ...(document.physicalTopology?.coverings ?? []).map(c => ({ id: c.id, kind: "physical-covering", metadata: { coveringKind: c.kind ?? (/термоусад/i.test(c.name) ? "heat-shrink" : "") } })),
  ]);
  const decodeArticleParts = (article: string): string[] => {
    const parts: string[] = [];
    let offset = 0;
    while (offset < article.length) {
      const colon = article.indexOf(":", offset);
      if (colon < 0 || !/^\d+$/.test(article.slice(offset, colon))) return [];
      const length = Number(article.slice(offset, colon)), end = colon + 1 + length;
      if (!Number.isSafeInteger(length) || end > article.length) return [];
      parts.push(article.slice(colon + 1, end));
      if (end === article.length) return parts;
      if (article[end] !== "|") return [];
      offset = end + 1;
    }
    return [];
  };
  for(const c of document.connectors) {
    const b=c.libraryBinding;
    const key=b?.mode==="template" ? keyOf("connector",b.templateId,b.templateVersion,b.versionSha256,b.article.sourceId,b.article.entityType,b.article.articleKey) : b?.mode==="series" ? keyOf("series",b.seriesId,c.partNumber) : keyOf("free",c.id);
    const description=(b?.mode==="template"?b.snapshot.name:b?.mode==="series"?c.libraryCode || b.seriesId:undefined) ?? "Соединитель";
    add(key,c.id,c.partNumber || "—",`${c.contacts.length} конт. — ${description}`,1,"шт.",b?.mode==="template"?`Библиотека v${b.templateVersion}`:b?.mode==="series"?"Встроенная серия":"Без библиотечной привязки",c.designation);
    for(const contact of c.contacts) if(contact.terminalArticle) {
      const identity=b?.mode==="template" ? keyOf("terminal",b.templateId,b.templateVersion,b.versionSha256,contact.terminalArticle,c.terminalCatalog?.versionSha256 ?? "") : keyOf("terminal-unpinned",c.id,contact.id);
      const article = contact.terminalDetails?.article || terminalArticleLabel(contact.terminalArticle);
      const name = contact.terminalDetails
        ? [contact.terminalDetails.manufacturer, contact.terminalDetails.series, contact.terminalDetails.description].filter(Boolean).join(" · ")
        : [decodeArticleParts(contact.terminalArticle)[0], decodeArticleParts(contact.terminalArticle)[3]].filter(Boolean).join(" · ") || "Терминал";
      add(identity,c.id,article,name,1,"шт.",b?.mode==="template"?"Контакт закреплённой серии":"Терминал без закреплённого источника",`${c.designation}:${contact.number}`);
    }
  }
  const cableMembers=new Set(document.cables.flatMap(c=>c.memberWireIds));
  for(const blank of [...document.wires.filter(w=>!cableMembers.has(w.id)),...document.cables]) {
    const b=blank.materialBinding;
    const cut=calculateWireCutLength(blank).cutLengthMm;
    const relatedWires = "circuit" in blank ? [blank] : blank.memberWireIds.map(id => document.wires.find(w => w.id === id)).filter((w): w is NonNullable<typeof w> => !!w);
    const contacts = relatedWires.flatMap(w => [w.from, w.to].flatMap(endpoint => {
      if (!("connectorId" in endpoint)) return [];
      const connector = document.connectors.find(item => item.id === endpoint.connectorId);
      const contact = connector?.contacts.find(item => item.id === endpoint.contactId);
      return contact ? [contact] : [];
    }));
    const marks = [...new Set(contacts.map(contact => contact.wire.trim()).filter(Boolean))];
    const sections = [...new Set(contacts.map(contact => contact.wireSection?.trim() ?? "").filter(Boolean))];
    const colors = [...new Set(contacts.map(contact => [contact.color, contact.secondaryColor].filter(Boolean).join("/")).filter(Boolean))];
    const attributes = [marks.join("/"), sections.join("/"), colors.join("/")].filter(Boolean).join(", ");
    const key=b ? keyOf(...JSON.parse(materialKey("material",b)) as unknown[],attributes) : keyOf("material-unpinned",blank.id);
    const article = b?.sourceKey ?? "";
    const index = "circuit" in blank ? relatedWires.map(w => indexedObjects.get(w.id)).filter(Boolean).join(", ") : indexedObjects.get(blank.id) ?? "";
    const articleParts = decodeArticleParts(article);
    const readableArticle = articleParts.length ? articleParts.filter(Boolean).join(" ") : /^\d+:/.test(article) ? b?.displayName ?? "—" : article;
    const designation = b ? [readableArticle, attributes].filter(Boolean).join(" — ") : attributes || "—";
    const colorName = colors.length ? `, цвет ${colors.join("/")}` : "";
    add(key,blank.id,designation,`${b?.displayName ?? "Материал не назначен"}${colorName}`,cut===null?null:cut/1000,"м",b?"По длине заготовки":"Нет закреплённого материала",index);
    for(const segment of document.physicalTopology?.segments.filter(s=>s.specificationItemId===blank.id)??[])rows.get(key)!.objectIds.add(segment.id);
  }
  for(const c of document.physicalTopology?.coverings ?? []) {
    const encoded = c.material?.sourceKey ?? "";
    const parts = decodeArticleParts(encoded);
    const article = parts.length ? parts.filter(Boolean).join(" ") : encoded ? /^\d+:/.test(encoded) ? c.material?.displayName ?? "—" : encoded : "—";
    const detailName = c.material?.displayName.trim() ?? "";
    const detailSuffix = detailName && detailName !== article
      ? detailName.replace(new RegExp(`^${article.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*(?:[·—,-]\\s*)?`, "i"), "").trim()
      : "";
    const designation = detailSuffix ? `${article} — ${detailSuffix}` : article;
    const key = c.material ? materialKey("protection",c.material) : keyOf("protection-unpinned",c.id);
    add(key,c.id,designation,c.material?.displayName ?? c.name,coveringMeasuredLength(document,c)===null?null:coveringMeasuredLength(document,c)!/1000,"м",c.material?"Защитное покрытие":"Материал защиты не назначен",indexedObjects.get(c.id) ?? c.name);
  }
  for(const item of document.drawingDocuments?.specificationItems ?? []) {
    if (item.kind === "abstract") continue;
    const key=keyOf("specification",item.id);
    add(key,item.id,item.designation,item.name,item.amount,item.unit,item.note || "Дополнительная позиция");
    for(const segment of document.physicalTopology?.segments.filter(s=>s.specificationItemId===item.id)??[])rows.get(key)!.objectIds.add(segment.id);
  }
  const order=document.drawingDocuments?.bomOrder ?? [];
  const keys=[...rows.keys()].sort((a,b)=>{const ai=order.indexOf(a),bi=order.indexOf(b);return (ai<0?Number.MAX_SAFE_INTEGER:ai)-(bi<0?Number.MAX_SAFE_INTEGER:bi);});
  return keys.map((key,i)=>{const r=rows.get(key)!,edit=document.drawingDocuments?.bomText?.[key];return {key,sourceIdentity:key,position:i+1,index:edit?.index??r.index.join(", "),designation:edit?.designation??r.designation.join(", "),name:edit?.name??r.name,amount:r.unknown?null:Number(r.amountMicros*BigInt(quantity))/1e6,unit:r.unit,note:edit?.note??r.note+(r.unknown?" · длина не задана":""),objectIds:[...r.objectIds]};});
}

const systemIdentifier = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Tables show operator-facing names only. Internal UUIDs are useful for
 * persistence and diagnostics, but make connection tables unreadable. */
function humanTableLabel(value: string | undefined, fallback: string): string {
  const text = value?.trim() ?? "";
  if (!text || systemIdentifier.test(text)) return fallback;
  const withoutIds = text.replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi, "").replace(/\\s{2,}/g, " ").trim();
  return withoutIds || fallback;
}

export function connectionConnectorLabel(document: HarnessDesignDocument, end: WireEndpoint): string {
  if (end.junctionId) return "Узел";
  if (end.screenId) return "Экран";
  const connector = document.connectors.find(item => item.id === end.connectorId);
  return humanTableLabel(connector?.designation, "Соединитель");
}

export function connectionEndLabel(document: HarnessDesignDocument, end: WireEndpoint): string {
  if (end.junctionId) return "Узел";
  if (end.screenId) return "Экран";
  const connector = connectionConnectorLabel(document, end);
  const contact = document.connectors.find(item => item.id === end.connectorId)?.contacts.find(item => item.id === end.contactId);
  return `${connector}:${contact?.number ?? "Контакт"}`;
}

/** Operator-facing endpoint label for the connection table, including the
 * selected connector article while retaining the positional designation. */
export function connectionTableEndLabel(document: HarnessDesignDocument, end: WireEndpoint): string {
  if (end.junctionId || end.screenId) return connectionEndLabel(document, end);
  const connector = document.connectors.find(item => item.id === end.connectorId);
  const contact = connector?.contacts.find(item => item.id === end.contactId);
  const article = connector?.partNumber?.trim();
  const designation = connector ? `${connector.designation}${article ? ` (${article})` : ""}` : "Соединитель";
  return `${designation}:${contact?.number ?? "Контакт"}`;
}

export function connectionWireColor(document: HarnessDesignDocument, wire: HarnessDesignDocument["wires"][number]): { readonly label: string; readonly hex: string } {
  const contacts = [wire.from, wire.to].flatMap(end => {
    if (!("connectorId" in end)) return [];
    const connector = document.connectors.find(item => item.id === end.connectorId);
    return connector?.contacts.filter(contact => contact.id === end.contactId) ?? [];
  });
  const primary = contacts.find(contact => contact.color.trim())?.color.trim() ?? wire.color;
  const secondary = contacts.find(contact => contact.secondaryColor?.trim())?.secondaryColor?.trim();
  const name = (value: string) => builtInWireColors.find(color => color.hex.toLocaleLowerCase() === value.toLocaleLowerCase())?.name ?? value;
  return { label: [name(primary), secondary ? name(secondary) : ""].filter(Boolean).join(" / ") || "—", hex: resolveWireColorHex(primary, builtInWireColors, wire.color || "#D9E2E7") };
}

function connectionTableValue(document: HarnessDesignDocument, wire: HarnessDesignDocument["wires"][number], id: ConnectionTableColumnId, index: number): string {
  if (id === "index") return `W${index + 1}`;
  if (id === "mark") return connectionWireMark(document, wire) || "—";
  if (id === "section") return connectionWireSection(document, wire) || "—";
  if (id === "marking") return wire.circuit || "—";
  if (id === "from") return connectionTableEndLabel(document, wire.from);
  if (id === "to") return connectionTableEndLabel(document, wire.to);
  if (id === "color") return connectionWireColor(document, wire).label;
  if (id === "length") return wire.lengthMm === null ? "—" : String(wire.lengthMm);
  return document.physicalTopology?.routes.find(route => route.wireId === wire.id)?.steps.length ? "Задан" : "Не задан";
}

export function connectionTableColumns(): readonly { readonly id: ConnectionTableColumnId; readonly label: string }[] {
  return getConnectionTableSettings().columns.filter(column => column.visible).map(column => ({ id: column.id, label: connectionTableColumnLabels[column.id] }));
}

export function connectionWireMark(document: HarnessDesignDocument, wire: HarnessDesignDocument["wires"][number]): string {
  const contacts = [wire.from, wire.to].flatMap(end => {
    if (!("connectorId" in end)) return [];
    const connector = document.connectors.find(item => item.id === end.connectorId);
    return connector?.contacts.filter(contact => contact.id === end.contactId) ?? [];
  });
  return contacts.find(contact => contact.wire.trim())?.wire.trim() ?? wire.materialBinding?.sourceKey ?? "";
}

export function connectionWireSection(document: HarnessDesignDocument, wire: HarnessDesignDocument["wires"][number]): string {
  const contacts = [wire.from, wire.to].flatMap(end => {
    if (!("connectorId" in end)) return [];
    const connector = document.connectors.find(item => item.id === end.connectorId);
    return connector?.contacts.filter(contact => contact.id === end.contactId) ?? [];
  });
  return contacts.find(contact => contact.wireSection?.trim())?.wireSection?.trim() ?? "";
}
export function drawingObjectOrigin(document:HarnessDesignDocument,id:string):Point|null {
  const extra=document.drawingDocuments?.specificationItems?.find(i=>i.id===id);if(extra)return extra.position ?? null;
  const connector=document.connectors.find(c=>c.id===id);if(connector)return connector.positions.drawing;
  const t=document.physicalTopology;
  const node=t?.nodes.find(n=>n.id===id);if(node)return physicalNodePoint(document,node);
  const segment=t?.segments.find(s=>s.id===id);if(segment)return physicalSegmentPoints(document,segment)[0] ?? null;
  const covering=t?.coverings?.find(c=>c.id===id);if(covering)return coveringPaths(document,covering)[0]?.[0] ?? null;
  const cable=document.cables.find(c=>c.id===id),wire=document.wires.find(w=>w.id===(cable?.memberWireIds[0] ?? id));
  if(wire){const route=t?.routes.find(r=>r.wireId===wire.id),step=route?.steps[0],seg=t?.segments.find(s=>s.id===step?.segmentId);if(seg){const p=physicalSegmentPoints(document,seg);return (step?.reverse?p.at(-1):p[0])??null;}return wire.drawingRoute[0] ?? findWireEndpoint(document,wire.from,"drawing") ?? null;}
  return null;
}

export function validateDrawingDocuments(value:unknown,document:HarnessDesignDocument):DrawingDocuments|undefined {
  if(value===undefined)return undefined;
  const fail=():never=>{throw new Error("Некорректные таблицы или позиционные выноски чертежа.");};
  if(!value||typeof value!=="object")return fail();
  const d=value as DrawingDocuments;
  if(d.dimensionMode!==undefined&&!["horizontal","vertical","aligned","path"].includes(d.dimensionMode))return fail();
  if(d.coveringLibrary!==undefined)validateCoveringLibrary(d.coveringLibrary);
  if(d.bendRadius!==undefined&&(typeof d.bendRadius!=="number"||!Number.isFinite(d.bendRadius)||d.bendRadius<0||d.bendRadius>200))return fail();
  if(d.indexScale!==undefined&&(typeof d.indexScale!=="number"||!Number.isFinite(d.indexScale)||d.indexScale<.25||d.indexScale>4))return fail();
  if(d.leaderScale!==undefined&&(typeof d.leaderScale!=="number"||!Number.isFinite(d.leaderScale)||d.leaderScale<.25||d.leaderScale>4))return fail();
  if(d.dimensionScale!==undefined&&(!Number.isFinite(d.dimensionScale)||d.dimensionScale<.25||d.dimensionScale>4))return fail();
  if(d.minimumCoveringOverlapPx!==undefined&&(!Number.isFinite(d.minimumCoveringOverlapPx)||d.minimumCoveringOverlapPx<20||d.minimumCoveringOverlapPx>500))return fail();
  if(d.opCoveringEdgePx!==undefined&&(!Number.isFinite(d.opCoveringEdgePx)||d.opCoveringEdgePx<0||d.opCoveringEdgePx>24))return fail();
  if(d.pipeOpacity!==undefined&&(!Number.isFinite(d.pipeOpacity)||d.pipeOpacity<0||d.pipeOpacity>1)||d.physicalScale!==undefined&&(!Number.isFinite(d.physicalScale)||d.physicalScale<.2||d.physicalScale>8)||d.coveringDiameterRatio!==undefined&&(!Number.isFinite(d.coveringDiameterRatio)||d.coveringDiameterRatio<1.1||d.coveringDiameterRatio>4)||d.showDimensions!==undefined&&typeof d.showDimensions!=="boolean"||d.volumeShading!==undefined&&typeof d.volumeShading!=="boolean")return fail();
  if(!Array.isArray(d.tables)||d.tables.length>20||!Array.isArray(d.leaders)||d.leaders.length>10000||d.rails!==undefined&&(!Array.isArray(d.rails)||d.rails.length>10000)||!Array.isArray(d.bomOrder)||d.bomOrder.length>50000)return fail();
  const ids=new Set([...document.connectors.map(c=>c.id),...document.wires.map(w=>w.id),...document.cables.map(c=>c.id),...document.physicalTopology?.nodes.map(n=>n.id)??[],...document.physicalTopology?.segments.map(s=>s.id)??[],...document.physicalTopology?.coverings?.map(c=>c.id)??[]]);
  const text=(s:unknown,max=128)=>typeof s==="string"&&s.trim().length>0&&s.length<=max;
  const point=(p:Point)=>p&&Number.isFinite(p.x)&&Number.isFinite(p.y)&&Math.abs(p.x)<=1e7&&Math.abs(p.y)<=1e7;
  if(d.indexOffsets!==undefined){if(!d.indexOffsets||typeof d.indexOffsets!=="object"||Array.isArray(d.indexOffsets)||Object.keys(d.indexOffsets).length>50000)return fail();for(const [id,offset] of Object.entries(d.indexOffsets))if(!text(id)||!point(offset))return fail();}
  if(d.graphics!==undefined){if(!Array.isArray(d.graphics)||d.graphics.length>10000)return fail();for(const candidate of d.graphics){const g=candidate as DrawingGraphic|null,counts:Record<DrawingGraphicKind,{min:number;max:number}>={contact:{min:1,max:1},line:{min:2,max:2},polyline:{min:2,max:256},rectangle:{min:2,max:2},ellipse:{min:2,max:2},bezier:{min:4,max:4},closedContour:{min:3,max:256},text:{min:1,max:1}};const range=g&&counts[g.kind];if(!g||!text(g.id)||ids.has(g.id)||!(g.view==="drawing"||g.view==="e4")||!range||!Array.isArray(g.points)||g.points.length<range.min||g.points.length>range.max||g.points.some((p:Point)=>!point(p))||g.kind==="text"&&(!text(g.text,1024)||g.angle!==undefined&&!Number.isFinite(g.angle)||g.fontFamily!==undefined&&(!text(g.fontFamily,128)||!/^[\p{L}\p{N} ,-]+$/u.test(g.fontFamily))||g.fontSize!==undefined&&(!Number.isFinite(g.fontSize)||g.fontSize<6||g.fontSize>144))||g.kind!=="text"&&(g.text!==undefined||g.angle!==undefined||g.fontFamily!==undefined||g.fontSize!==undefined)||g.color!==undefined&&(!/^#[0-9a-f]{6}$/i.test(g.color))||g.width!==undefined&&(!Number.isFinite(g.width)||g.width<.2||g.width>100))return fail();ids.add(g.id);}}
  for(const t of [...d.tables,...d.leaders]){if(!t||!text(t.id)||ids.has(t.id))return fail();ids.add(t.id);}
  for(const t of d.tables)if(!["bom","connections","cut"].includes(t.kind)||!point(t.position)||(t.dock!==undefined&&!["left","right","top","bottom"].includes(t.dock))||
    (t.width!==undefined&&(!Number.isFinite(t.width)||t.width<280||t.width>4000))||(t.height!==undefined&&(!Number.isFinite(t.height)||t.height<160||t.height>4000)))return fail();
  for(const l of d.leaders){if(ids.has(`${l.id}:anchor`))return fail();ids.add(`${l.id}:anchor`);}
  for(const l of d.leaders)if(!text(l.objectId)||!text(l.rowKey,4096)||!point(l.anchorOffset)||!point(l.circle)||(l.anchorLocal!==undefined&&!point(l.anchorLocal))||(l.hidden!==undefined&&typeof l.hidden!=="boolean"))return fail();
  const claimed=new Set<string>(),leaderIds=new Set(d.leaders.map(l=>l.id));
  for(const rail of d.rails??[]){
    if(!rail||!text(rail.id)||ids.has(rail.id)||ids.has(`${rail.id}:start`)||ids.has(`${rail.id}:end`)||!point(rail.start)||!point(rail.end)||Math.hypot(rail.end.x-rail.start.x,rail.end.y-rail.start.y)<24||!Array.isArray(rail.leaderIds)||rail.leaderIds.length>10000)return fail();
    ids.add(rail.id);ids.add(`${rail.id}:start`);ids.add(`${rail.id}:end`);
    // A deleted component may leave an old leader ID in a persisted rail. It
    // is safe to drop that membership; the leader itself is reconciled below.
    for(const leaderId of rail.leaderIds){if(!leaderIds.has(leaderId))continue;if(claimed.has(leaderId))return fail();claimed.add(leaderId);}
  }
  if(new Set(d.bomOrder).size!==d.bomOrder.length||d.bomOrder.some(k=>!text(k,4096)))return fail();
  if(d.bomText!==undefined){if(!d.bomText||typeof d.bomText!=="object"||Array.isArray(d.bomText)||Object.keys(d.bomText).length>50000)return fail();for(const [key,edit] of Object.entries(d.bomText)){if(!text(key,4096)||!edit||typeof edit!=="object"||Array.isArray(edit)||Object.entries(edit).some(([k,v])=>!["index","designation","name","note"].includes(k)||typeof v!=="string"||v.length>4096))return fail();}}
  if(d.specificationItems!==undefined){if(!Array.isArray(d.specificationItems)||d.specificationItems.length>50000)return fail();const itemIds=new Set<string>();for(const item of d.specificationItems){if(!item||!text(item.id)||itemIds.has(item.id)||!((item.kind==="abstract")||(item.kind==="manual"))||!text(item.type,256)||typeof item.designation!=="string"||item.designation.length>4096||!text(item.name,4096)||(!Number.isFinite(item.amount)&&item.amount!==null)||item.amount!==null&&(item.amount<0||item.amount>1e9)||!["шт.","м","г","кг","л"].includes(item.unit)||typeof item.note!=="string"||item.note.length>4096||item.position!==undefined&&!point(item.position)||item.objectId!==undefined&&!text(item.objectId)||item.sourceIdentity!==undefined&&!text(item.sourceIdentity,4096))return fail();itemIds.add(item.id);if(ids.has(item.id))return fail();ids.add(item.id);}}
  const dimensions=validateDrawingDimensions(d.dimensions,document);
  for(const item of dimensions??[]){if(ids.has(item.id))return fail();ids.add(item.id);}
  // Missing targets are intentionally retained and visibly diagnosed, never reassigned by proximity.
  return d;
}

export function drawingDocumentScene(document:HarnessDesignDocument,quantity=1,perimeters?:DrawingPerimeters,view:"drawing"|"e4"="drawing"):EditorSceneObject[] {
  const d=document.drawingDocuments;if(!d)return [];
  const rows=buildDrawingBom(document,quantity);
  const tables:EditorSceneObject[]=d.tables.filter(t=>t.kind!=="cut" && !t.dock).map(t=>{
    const connectionColumns = connectionTableColumns();
    const headers=t.kind==="bom"?["Поз.","Индекс","Обозначение","Наименование","Кол-во","Примечание"]:connectionColumns.map(column => column.label);
    const values=t.kind==="bom"?rows.map(r=>[String(r.position),r.index,r.designation,r.name,`${r.amount ?? "—"} ${r.unit}`,r.note]):document.wires.map((w,index)=>connectionColumns.map(column => connectionTableValue(document,w,column.id,index)));
    const widths=t.kind==="bom"?drawingTableColumnWidths(headers,values):connectionColumns.map(column => column.id === "from" || column.id === "to" ? 180 : column.id === "color" ? 100 : column.id === "marking" ? 120 : 100);
    return {id:t.id,kind:"drawing-table",layerId:"dimensions",label:t.kind==="bom"?`Спецификация · ${quantity} жгут(а)`:"Таблица соединений",x:t.position.x,y:t.position.y,width:widths.reduce((a,b)=>a+b,0),height:52+values.length*32,color:"#365568",metadata:{rowObjectIds:JSON.stringify(t.kind==="bom"?rows.map(r=>r.objectIds):document.wires.map(w=>[w.id])),headers:JSON.stringify(headers),rows:JSON.stringify(values),widths:JSON.stringify(widths)}};
  });
  const scale=d.leaderScale??1,radius=12*scale,anchorRadius=4*scale;
  const leaders:EditorSceneObject[]=d.leaders.filter(l=>!l.hidden).flatMap(l=>{
    const origin=drawingObjectOrigin(document,l.objectId),row=rows.find(r=>r.key===l.rowKey&&r.objectIds.includes(l.objectId));
    const connector=document.connectors.find(c=>c.id===l.objectId);
    const offset=l.anchorLocal&&connector?drawingLocalPoint(l.anchorLocal,connector.drawingPlacements):l.anchorOffset;
    const target=origin?{x:origin.x+offset.x,y:origin.y+offset.y}:l.circle;
    const anchor=drawingObjectPerimeter(document,l.objectId,target,perimeters)??l.circle;
    return [{id:l.id,kind:"position-leader",layerId:"dimensions",label:origin&&row?String(row.position):"?",x:l.circle.x-radius,y:l.circle.y-radius,width:radius*2,height:radius*2,color:origin&&row?"#365568":"#c23535",points:[anchor,l.circle]},
      {id:`${l.id}:anchor`,kind:"leader-anchor",layerId:"dimensions",label:"",x:anchor.x-anchorRadius,y:anchor.y-anchorRadius,width:anchorRadius*2,height:anchorRadius*2,color:origin&&row?"#365568":"#c23535"}];
  });
  const rails:EditorSceneObject[]=(d.rails??[]).flatMap(rail=>[
    {id:rail.id,kind:"position-rail",layerId:"dimensions",label:"Линия позиций",x:Math.min(rail.start.x,rail.end.x),y:Math.min(rail.start.y,rail.end.y),width:Math.abs(rail.end.x-rail.start.x),height:Math.abs(rail.end.y-rail.start.y),color:"#587084",points:[rail.start,rail.end]},
    ...(["start","end"] as const).map(end=>({id:`${rail.id}:${end}`,kind:"rail-handle" as const,layerId:"dimensions",label:"Конец линии позиций",x:rail[end].x-5,y:rail[end].y-5,width:10,height:10,color:"#587084"}))
  ]);
  const graphics:EditorSceneObject[]=(d.graphics??[]).filter(g=>g.view===view).map(g=>{const points=g.points,minX=Math.min(...points.map(p=>p.x)),minY=Math.min(...points.map(p=>p.y)),maxX=Math.max(...points.map(p=>p.x)),maxY=Math.max(...points.map(p=>p.y));return {id:g.id,kind:(`graphic-${g.kind.replace("closedContour","closed-contour")}`) as EditorSceneObject["kind"],layerId:g.view==="drawing"?"dimensions":"connectors",label:g.text??g.kind,x:minX,y:minY,width:Math.max(1,maxX-minX),height:Math.max(1,maxY-minY),color:g.color??"#253b4a",points,metadata:{graphicKind:g.kind,graphicView:g.view,graphicText:g.text??"",graphicAngle:String(g.angle??0),graphicFontFamily:g.fontFamily??"Arial",graphicFontSize:String(g.fontSize??16),graphicWidth:String(g.width??2)}};});
  if(view==="e4")return graphics;
  return [...graphics,...tables,...rails,...leaders,...(d.specificationItems??[]).filter(i=>i.position).map(i=>({id:i.id,kind:"specification-item" as const,layerId:"dimensions",label:i.designation || i.name,x:i.position!.x,y:i.position!.y,width:110,height:38,color:"#416579"}))];
}
export function moveDrawingAnnotation(document:HarnessDesignDocument,id:string,point:Point,perimeters?:DrawingPerimeters,snaps?:DrawingSnaps):DrawingDocuments|null {
  const d=document.drawingDocuments;if(!d)return null;
  const graphic=d.graphics?.find(g=>g.id===id);if(graphic){const origin={x:Math.min(...graphic.points.map(p=>p.x)),y:Math.min(...graphic.points.map(p=>p.y))},requested={x:point.x-origin.x,y:point.y-origin.y},targets:DrawingOutline[]=drawingDocumentScene(document,1,perimeters,graphic.view).filter(o=>o.id!==id&&o.points?.length).map(o=>({id:o.id,points:[...(o.points??[])],closed:o.metadata?.graphicKind==="closedContour",corners:o.points as Point[]})),outline:DrawingOutline={id:graphic.id,points:[...graphic.points],closed:graphic.kind==="closedContour",corners:[...graphic.points]},delta=snaps?snapDrawingTranslation(outline,requested,targets,snaps,8):requested;return {...d,graphics:d.graphics!.map(g=>g.id===id?{...g,points:g.points.map(p=>({x:p.x+delta.x,y:p.y+delta.y}))}:g)};}
  const dimension=moveDrawingDimension(document,id,point,perimeters);if(dimension)return dimension;
  const rail=movePositionRail(d,id,point);if(rail)return rail;
  if(d.specificationItems?.some(i=>i.id===id&&i.position))return {...d,specificationItems:d.specificationItems.map(i=>i.id===id?{...i,position:point}:i)};
  if(d.tables.some(t=>t.id===id)) return {...d,tables:d.tables.map(t=>t.id===id?{...t,position:point}:t)};
  const leader=d.leaders.find(l=>l.id===id||`${l.id}:anchor`===id);if(!leader)return null;
  const scale=d.leaderScale??1;
  if(leader.id===id)return movePositionLeaderOnRails(d,id,{x:point.x+12*scale,y:point.y+12*scale});
  const origin=drawingObjectOrigin(document,leader.objectId);if(!origin)return null;
  const anchor=drawingObjectPerimeter(document,leader.objectId,{x:point.x+4*scale,y:point.y+4*scale},perimeters);if(!anchor)return null;
  const offset={x:anchor.x-origin.x,y:anchor.y-origin.y},connector=document.connectors.find(c=>c.id===leader.objectId);
  return {...d,leaders:d.leaders.map(l=>l.id===leader.id?{...l,anchorOffset:offset,...(connector?{anchorLocal:drawingPointToLocal(offset,connector.drawingPlacements)}:{})}:l)};
}


/** Adds each row to every represented object once; one command remains one Undo. */
export function addDrawingPositions(document:HarnessDesignDocument,perimeters?:DrawingPerimeters,rowKeys?:readonly string[],createId=()=>crypto.randomUUID()):DrawingDocuments {
  const d=document.drawingDocuments??emptyDrawingDocuments(),leaders=[...d.leaders];
  const rows=buildDrawingBom(document).filter(r=>!rowKeys||rowKeys.includes(r.key));
  for(const row of rows)for(const objectId of row.objectIds){
    const origin=drawingObjectOrigin(document,objectId);if(!origin)continue;
    // The physical segment represents its assigned material on the drawing.
    if(document.physicalTopology?.segments.some(s=>s.specificationItemId===objectId))continue;
    const existing=leaders.find(l=>l.objectId===objectId&&l.rowKey===row.key);
    if(existing){if(existing.hidden)leaders[leaders.indexOf(existing)]={...existing,hidden:false};continue;}
    const siblings=leaders.filter(l=>l.objectId===objectId),last=siblings.reduce<PositionLeader|undefined>((right,l)=>!right||l.circle.x>right.circle.x?l:right,undefined);
    const linear=initialLinearLeader(document,objectId);
    const target=linear?{x:linear.point.x+linear.normal.x*.01,y:linear.point.y+linear.normal.y*.01}:{x:origin.x+10000,y:origin.y-10000};
    const edge=drawingObjectPerimeter(document,objectId,target,perimeters);if(!edge)continue;
    const scale=d.leaderScale??1;
    const circle=last?{x:last.circle.x+24*scale,y:last.circle.y}:{x:edge.x+48*scale,y:edge.y-48*scale};
    const anchor=linear?edge:drawingObjectPerimeter(document,objectId,circle,perimeters)!;
    const offset={x:anchor.x-origin.x,y:anchor.y-origin.y},connector=document.connectors.find(c=>c.id===objectId);
    leaders.push({id:createId(),objectId,rowKey:row.key,anchorOffset:offset,circle,...(connector?{anchorLocal:drawingPointToLocal(offset,connector.drawingPlacements)}:{})});
  }
  return {...d,leaders};
}

export function setDrawingPositionVisibility(document:HarnessDesignDocument,rowKey:string,visible:boolean,perimeters?:DrawingPerimeters):DrawingDocuments {
  const d=visible?addDrawingPositions(document,perimeters,[rowKey]):document.drawingDocuments??emptyDrawingDocuments();
  return {...d,leaders:d.leaders.map(l=>l.rowKey===rowKey?{...l,hidden:!visible}:l)};
}

/** Toggle every position annotation as one persisted drawing setting. Showing
 * positions also materializes missing leaders, so the toolbar action works on
 * an empty document and remains idempotent after a partial/manual placement. */
export function setDrawingPositionsVisibility(document:HarnessDesignDocument,visible:boolean,perimeters?:DrawingPerimeters):DrawingDocuments {
  const d=visible?addDrawingPositions(document,perimeters):document.drawingDocuments??emptyDrawingDocuments();
  return {...d,leaders:d.leaders.map(l=>({...l,hidden:!visible}))};
}
