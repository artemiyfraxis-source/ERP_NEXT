import { catalogOuterDiameter } from "./drawing-thickness";
import type { ReferenceCatalogSearchRecord } from "../reference-catalog-api";
import { builtInWireReferences } from "./wire-reference-catalog";
import type { WireMaterialBinding } from "./model";

export interface WireDatabaseOption {
  readonly id: string;
  readonly diameterMm?:number;
  readonly mark: string;
  readonly section: string;
  readonly label: string;
  readonly detail: string;
  readonly searchText?: string;
  readonly color?: string;
  readonly materialBinding?: WireMaterialBinding;
}

export function isSelectableWirePayload(payload: Readonly<Record<string, unknown>>): boolean {
  return Boolean(formatWireSection(payload));
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim()
    : typeof value === "number" && Number.isFinite(value) ? String(value).replace(".", ",") : "";
}

function field(payload: Readonly<Record<string, unknown>>, ...keys: string[]): string {
  for (const key of keys) {
    const value = text(payload[key]);
    if (value) return value;
  }
  return "";
}

/** Presentation only: F/G and H/I stay separate in the reference payload.
 * 1C is a mounting stranded wire; its count is omitted only without a second group. */
export function formatWireSection(payload: Readonly<Record<string, unknown>>): string {
  const core = field(payload, "Core");
  const sectionC = field(payload, "Сечение C", "Сечение С");
  const pair = field(payload, "Pair");
  const sectionP = field(payload, "Сечение P", "Сечение Р");
  const part = (count: string, section: string) => {
    if (!section || !count || /^(?:0(?:[cpср])?|[-—])$/iu.test(count)) return "";
    return `${count}x${section}`;
  };
  const secondGroup = part(pair, sectionP);
  const firstGroup = /^1[сc]$/iu.test(core.replace(/\s/g, "")) && !secondGroup
    ? sectionC : part(core, sectionC);
  return [firstGroup, secondGroup].filter(Boolean).join(" | ")
    || sectionC || field(payload, "sectionMm2", "Сечение", "section", "awg", "AWG");
}

export function wireDatabaseOption(record: ReferenceCatalogSearchRecord, snapshot?: { readonly snapshotId: string; readonly snapshotSha256: string }): WireDatabaseOption {
  const mark = field(record.payload, "Марка", "mark", "name", "Название", "series", "Серия") || record.sourceKey;
  const section = formatWireSection(record.payload);
  const color = field(record.payload, "Цвет", "color", "Color");
  const diameterMm = catalogOuterDiameter(record.payload);
  return { id: record.recordId, diameterMm, mark, section, color, label: [mark, section].filter(Boolean).join(" · "),
    ...(snapshot && record.entityType === "wire" ? { materialBinding: {
      sourceId: "technology-wires", snapshotId: snapshot.snapshotId, snapshotSha256: snapshot.snapshotSha256,
      recordId: record.recordId, entityType: "wire" as const, sourceKey: record.sourceKey,
      displayName: [mark, section].filter(Boolean).join(" · "),
      ...(diameterMm === undefined ? {} : { outerDiameterMm: diameterMm }),
    } } : {}),
    detail: [
      /^1[сc]$/iu.test(field(record.payload, "Core").replace(/\s/g, "")) ? "монтажный многожильный провод" : "",
      ...["Артикул провода", "Артикул", "Цвет", "Производитель", "Категория"]
        .map(key => text(record.payload[key]) ? `${key}: ${text(record.payload[key])}` : ""),
    ].filter(Boolean).join(" · "),
    searchText: Object.values(record.payload).map(text).join(" ") };
}

export const builtInWireOptions: readonly WireDatabaseOption[] = builtInWireReferences.map(wire => ({
  id: wire.id, mark: wire.series,
  section: wire.size.system === "awg" ? `${wire.size.value}AWG` : `${text(wire.size.value)} мм²`,
  label: wire.designation, detail: "Встроенный справочник",
}));

export function filterWireOptions(options: readonly WireDatabaseOption[], query: string, section = ""): readonly WireDatabaseOption[] {
  const normalize = (value: string) => value.toLocaleLowerCase("ru").replace(/[.,]/g, ".");
  const tokens = normalize(query).split(/\s+/).filter(Boolean);
  const sectionKey = normalizeWireSectionChoice(section);
  return options.filter(option => (!sectionKey || normalizeWireSectionChoice(option.section) === sectionKey) &&
    tokens.every(token => normalize(`${option.label} ${option.detail} ${option.searchText ?? ""}`).includes(token)));
}

/** A mark is offered once; its section remains a separate choice. */
export function wireMarkChoices(options: readonly WireDatabaseOption[], query: string, section = ""): readonly string[] {
  return [...new Map(filterWireOptions(options, query, section)
    .map(option => [normalizeWireChoice(option.mark), option.mark] as const)).values()];
}

/** Values used by the two dependent wire selectors. Keep the complete
 * catalog rows separate: the editor only combines them for display. */
export function normalizeWireChoice(value: string): string {
  return value.trim().toLocaleLowerCase("ru-RU").replace(/ё/g, "е").replace(/[.,]/g, ".").replace(/\s+/g, "");
}

/** Unit labels on old contact presets do not change the imported size value.
 * No conversion between AWG and square millimetres is performed. */
export function normalizeWireSectionChoice(value: string): string {
  return normalizeWireChoice(value).replace(/awg|мм[²2]|mm[²2]/g, "");
}

export function wireSectionChoices(
  options: readonly WireDatabaseOption[],
  mark: string,
  current = "",
): readonly string[] {
  const normalizedMark = normalizeWireChoice(mark);
  const choices = options
    .filter(option => !normalizedMark || normalizeWireChoice(option.mark) === normalizedMark)
    .map(option => option.section.trim())
    .filter(Boolean);
  return [...new Set([current.trim(), ...choices].filter(Boolean))];
}
