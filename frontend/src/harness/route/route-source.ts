// Ported from TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission (THIRD_PARTY_NOTICES.md).
// Original: src/Techmap.Client/src/manufacturing/route-source.ts @ 0a456d2f7bd23288111e871058ebf55bf14426cd (branch codex/integration-m5-22).
import { calculateWireCutLength, type ConnectorContact, type HarnessDesignDocument, type WireEndStripProfiles, type WireEndpoint } from "../editor/model";
import { coveringMeasuredLength } from "../editor/physical-coverings";

export interface RouteSourceRef {
  readonly kind: "wire" | "cable" | "covering" | "connector";
  readonly id: string;
}

export interface RouteSourceItem {
  readonly ref: RouteSourceRef;
  readonly title: string;
  /** Manufacturing cut length in millimetres; never a canvas distance. */
  readonly lengthMm: number | null;
  readonly color: string | null;
  readonly material: string;
  /** Source catalogue article/key for the material, when it is pinned. */
  readonly materialArticle: string;
  readonly section: string;
  readonly terminalFrom: string;
  readonly terminalTo: string;
  readonly stripProfiles?: WireEndStripProfiles;
  /** Cable members remain addressable for operations, but are not a second blank. */
  readonly cableId?: string;
}

/** Project the current harness into addressable production inputs without changing it. */
export function buildRouteSourceItems(document: HarnessDesignDocument): RouteSourceItem[] {
  const connectors = new Map(document.connectors.map(connector => [connector.id, connector]));
  const contacts = new Map(document.connectors.map(connector => [connector.id, new Map(connector.contacts.map(contact => [contact.id, contact]))]));
  const contactAt = (endpoint: WireEndpoint): ConnectorContact | undefined => contacts.get(endpoint.connectorId)?.get(endpoint.contactId);
  const endTitle = (endpoint: WireEndpoint): string => endpoint.junctionId ? "Узел" : endpoint.screenId ? "Экран"
    : `${connectors.get(endpoint.connectorId)?.designation || "Соединитель"}:${contactAt(endpoint)?.number ?? "?"}`;
  const cableByWire = new Map(document.cables.flatMap(cable => cable.memberWireIds.map(id => [id, cable.id] as const)));
  const items: RouteSourceItem[] = document.wires.map(wire => {
    const from = contactAt(wire.from), to = contactAt(wire.to);
    const colorContact = wire.colorSource ? contacts.get(wire.colorSource.connectorId)?.get(wire.colorSource.contactId)
      : wire.colorSource === null ? undefined : [from, to].find(contact => contact?.color.trim());
    const cableId = cableByWire.get(wire.id);
    return {
      ref: { kind: "wire", id: wire.id },
      title: `${endTitle(wire.from)} → ${endTitle(wire.to)}${wire.circuit.trim() ? ` · ${wire.circuit.trim()}` : ""}`,
      lengthMm: calculateWireCutLength(wire).cutLengthMm,
      color: colorContact?.color.trim() || wire.color.trim() || null,
      material: wire.materialBinding?.displayName || from?.wire.trim() || to?.wire.trim() || "",
      materialArticle: wire.materialBinding?.sourceKey || "",
      section: from?.wireSection?.trim() || to?.wireSection?.trim() || "",
      terminalFrom: from?.terminalArticle ?? "", terminalTo: to?.terminalArticle ?? "",
      ...(wire.stripProfiles ? { stripProfiles: wire.stripProfiles } : {}),
      ...(cableId ? { cableId } : {}),
    };
  });
  for (const [index, cable] of document.cables.entries()) items.push({
    ref: { kind: "cable", id: cable.id }, title: `Кабель ${index + 1}${cable.materialBinding ? ` · ${cable.materialBinding.displayName}` : ""}`,
    lengthMm: calculateWireCutLength(cable).cutLengthMm, color: null,
    material: cable.materialBinding?.displayName ?? "", materialArticle: cable.materialBinding?.sourceKey ?? "", section: "", terminalFrom: "", terminalTo: "",
  });
  for (const covering of document.physicalTopology?.coverings ?? []) items.push({
    ref: { kind: "covering", id: covering.id }, title: covering.name,
    lengthMm: coveringMeasuredLength(document, covering), color: covering.color || null,
    material: covering.material?.displayName ?? "", materialArticle: covering.material?.sourceKey ?? "", section: "", terminalFrom: "", terminalTo: "",
  });
  for (const connector of document.connectors) items.push({
    ref: { kind: "connector", id: connector.id }, title: connector.designation,
    lengthMm: null, color: null, material: connector.partNumber, materialArticle: connector.partNumber,
    section: "", terminalFrom: "", terminalTo: "",
  });
  return items;
}

/** Compact operator-facing designation used beside the route index. */
export function routeSourceDesignation(item: RouteSourceItem): string {
  const material = item.material.trim();
  const article = item.materialArticle.trim();
  const materialLabel = article && material && article !== material ? `${material} · арт. ${article}` : article || material;
  const section = item.section.trim();
  const normalize = (value: string) => value.toLocaleLowerCase().replace(/\s/g, "").replace(/,/g, ".");
  const sectionLabel = section && !normalize(material).includes(normalize(section))
    ? `сечение ${/^\d+(?:[.,]\d+)?$/.test(section) ? `${section} мм²` : section}` : "";
  return [materialLabel || "Материал не назначен", sectionLabel, item.color ? `цвет ${item.color}` : ""].filter(Boolean).join(" · ");
}
