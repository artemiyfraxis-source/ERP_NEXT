/*
 * «Построить чертёж» в генераторе ТК (владелец 12.10.2026: «после того как тут распихается, я хочу чертёжик вот как у
 * Василия получить»). Состояние генератора — разъёмы сторон А/В, кабели, распиновка, свивка, ТУТ и оболочки — становится
 * документом чертежа жгута портированного редактора (harness/editor): разъёмы с таблицами контактов (схема Э4), провода
 * с контакта на контакт, ствол от стороны А до узла ветвления и ветка на каждый разъём В (физическая трасса чертежа),
 * свитые пары, оболочки и размер ствола.
 *
 * Документ собирается командами самого редактора (`applyEditorCommand`: add-connector, update-contact, add-wire,
 * create-diff-pair, set-physical-topology, set-drawing-documents) и в конце читается его же разбором
 * (`parseHarnessDesignDocument`) — то, что не открылось бы в редакторе, сюда не попадает. Модуль грузится лениво (вместе с
 * кодом редактора), в основной чанк генератора не входит.
 *
 * Части:
 *  • planFromGenerator(D, S) — чистое чтение формы: что рисовать (без справочников и сети);
 *  • buildHarnessDocument(plan, sources) — чистая сборка документа по плану и найденным разъёмам библиотеки;
 *  • resolveConnectorSources — разъём по артикулу в библиотеке компонентов (сеть), иначе свободный корпус на N контактов;
 *  • buildGeneratorDrawing — всё вместе для кнопки.
 */
import { wireColorKnown, type ConnectorHousing } from "./connectorArt";
import { JACKET_STRIP_MM_, makeGen, parseDecimal_, sideRu_, type GenData, type GenState, type Side } from "./tcGenLogic";
import { coreColor } from "./pinoutCheck";
import { applyEditorCommand, createConnector, createWire, type EditorCommand } from "./harness/editor/commands";
import {
  createConnectorInstanceFromComponentTemplate,
  type ComponentTemplatePlacementEnvelope,
} from "./harness/editor/component-template-placement";
import { normalizeConnectorArticle } from "./harness/editor/connector-pitch";
import { segmentDimensionKey, type DrawingDimension } from "./harness/editor/drawing-dimensions";
import { DRAWING_VIEW_PLACEMENT_ID } from "./harness/editor/drawing-scale";
import { selectMaterializedContactRepresentation } from "./harness/editor/materialized-contact-representation";
import {
  connectorContactPosition,
  connectorE4TableGeometry,
  createEmptyHarnessDesign,
  parseHarnessDesignDocument,
  templateTerminalChoices,
  type ConnectorInstance,
  type HarnessDesignDocument,
  type Point,
} from "./harness/editor/model";
import type { CoveringKind, PhysicalCovering } from "./harness/editor/physical-coverings";
import { physicalNodeLocalPoint, physicalNodePoint } from "./harness/editor/physical-ports";
import type { PhysicalNode, PhysicalSegment, PhysicalTopology } from "./harness/editor/physical-topology-model";
import { routePhysicalWires } from "./harness/editor/physical-wire-routing";
import type { ComponentTemplateApi, ComponentTemplateSummary } from "./harness/component-library/component-template-api";

// ── План: что рисовать ──────────────────────────────────────────────────────────────────────────────────────────

export interface PlanConnector {
  /** Разъём формы (Endpoint.id). */
  readonly key: string;
  readonly side: Side;
  /** «XS1» — подпись разъёма в «Сложной сборке», иначе по порядку А → В. */
  readonly designation: string;
  /** Артикул корпуса из состава ("" — разъём не выбран). */
  readonly article: string;
  /** Артикул терминала по БД.ТЕР ("" — пайка, терминал не выбран или его нет в БД.ТЕР). */
  readonly terminal: string;
  /** ТУТ на концах пайки: длина, мм (null — длина не задана); undefined — ТУТ нет. */
  readonly tutLengthMm?: number | null;
}

export interface PlanWire {
  /** «idx.k» — k-й провод кабеля формы. */
  readonly key: string;
  /** Позиция кабеля в списке кабелей формы (по ней задана свивка). */
  readonly group: number;
  readonly unit: number;
  readonly mark: string;
  readonly section: string;
  /** Цвет изоляции, #RRGGBB ("" — в наименовании цвета нет). */
  readonly color: string;
  readonly lengthMm: number | null;
  readonly from: { readonly connector: string; readonly pin: number };
  readonly to: { readonly connector: string; readonly pin: number };
}

/** Многожильный кабель: его жилы — провода плана (`wireKeys`), оболочка и длина реза — у кабеля. */
export interface PlanCable {
  readonly key: string;
  readonly mark: string;
  readonly wireKeys: readonly string[];
  readonly lengthMm: number | null;
  /** Снятие оболочки на концах, мм (разделка кабеля из строки «Кабели»). */
  readonly stripMm: number | null;
}

export interface PlanCovering {
  readonly kind: "tut" | "sleeve" | "wrap";
  readonly name: string;
  readonly lengthMm: number | null;
  /** Конец жгута (разъём), у которого лежит отрезок; у оплётки/ленты — весь жгут. */
  readonly at?: string;
  readonly coveringKind: CoveringKind;
}

export interface GeneratorDrawingPlan {
  readonly connectors: readonly PlanConnector[];
  readonly wires: readonly PlanWire[];
  /** Кабели в оболочке (на форме — кабель с числом жил больше одной). */
  readonly cables: readonly PlanCable[];
  /** Свитые пары — ключи проводов. */
  readonly pairs: readonly (readonly [string, string])[];
  readonly twistPitchMm: number | null;
  readonly coverings: readonly PlanCovering[];
  readonly warnings: readonly string[];
}

const round3 = (value: number) => Math.round(value * 1000) / 1000;
const positive = (value: unknown): number | null => {
  const number = parseDecimal_(value);
  return number > 0 ? round3(number) : null;
};

/** «#222» → «#222222», #RRGGBB как есть, иное — "". */
const hex6 = (value: string | null | undefined) => {
  const color = (value ?? "").toUpperCase();
  return /^#[0-9A-F]{3}$/.test(color) ? `#${[...color.slice(1)].map((digit) => digit + digit).join("")}` : /^#[0-9A-F]{6}$/.test(color) ? color : "";
};

/** Цвет жилы по наименованию — #RRGGBB, как его хранит чертёж («#222» → «#222222»); нет цвета — "". */
export function wireColorHex(mark: string): string {
  return hex6(wireColorKnown(mark));
}

/** Сечение из марки: «24 AWG», иначе число с запятой («МГТФ 0,12» → «0,12 мм²»); нет — "". */
export function wireSectionOf(mark: string): string {
  const awg = /(\d+)\s*awg/i.exec(mark);
  if (awg) return `${awg[1]} AWG`;
  const decimals = mark.replace(/\d+\s*[хx×*]\s*(?=\d)/g, " ").match(/\d+[.,]\d+/g);
  return decimals ? `${decimals[decimals.length - 1].replace(".", ",")} мм²` : "";
}

/** Что рисовать по форме генератора: те же соединения, что уходят в ЭТК (`connections_`: жила за жилой — её контакт
 *  на первом разъёме, А раньше В, ↔ её контакт на каждом следующем), но с номером кабеля и разъёмом формы. Жилы кабеля
 *  в оболочке собраны в кабель чертежа; жила на двух разъёмах одной стороны — два провода (ветвь). */
export function planFromGenerator(D: GenData, S: GenState): GeneratorDrawingPlan {
  const gen = makeGen(D, S);
  const warnings: string[] = [];
  const ordered = (["A", "B"] as Side[]).flatMap((side) => gen.endpointsOnSide_(side));
  const compText = (id: string) => { const c = gen.getCompById(id); return c ? c.art || c.name || "" : ""; };
  const connectors: PlanConnector[] = ordered.map((ep, index) => {
    const label = gen.isComplex_() ? (ep.label || "").trim() : "";
    const term = ep.method === "crimp" && ep.termId ? gen.getCompById(ep.termId) : null;
    const record = term ? gen.findTerRecord_(term.art || term.name) : null;
    if (term && !record) warnings.push(`${sideRu_(ep.side)}${gen.endpointOrdinal_(ep)}: терминала «${term.art || term.name}» нет в БД.ТЕР — в таблице контактов он не указан.`);
    const tut = ep.method === "solder" && !ep.tutOff ? { tutLengthMm: positive(ep.tutLen) } : {};
    return { key: ep.id, side: ep.side, designation: label || `XS${index + 1}`, article: compText(ep.connId), terminal: record?.article ?? "", ...tut };
  });

  const active = gen.activeWires_();
  const wires: PlanWire[] = [];
  const cables: PlanCable[] = [];
  active.forEach((w, group) => {
    const mark = compText(w.compId);
    const routed = ordered.filter((ep) => gen.routedWireIdxs_(ep).includes(w.idx));
    const ends = routed.map((ep) => ({ ep, cores: gen.corePinsOf_(ep, w.idx) })).filter((x) => x.cores.some((p) => p != null));
    const qty = gen.unitCount_(w);
    if (ends.length < 2) {
      if (routed.length >= 2) warnings.push(`${mark || "Провод"}: распиновка не задана — на чертёж не попал.`);
      return;
    }
    const length = positive(S.advancedLength ? w.length : S.drawingLength);
    const colors = gen.coreColors_(w);
    const mine: PlanWire[] = [];
    let drawn = 0;
    for (let k = 0; k < qty; k++) {
      const at = ends.filter((x) => x.cores[k] != null);
      if (at.length < 2) continue;
      drawn++;
      const color = hex6(coreColor(colors[k])?.hex) || wireColorHex(mark);
      at.slice(1).forEach((x, j) => mine.push({
        key: j ? `${w.idx}.${k}.${j}` : `${w.idx}.${k}`, group, unit: k, mark, section: wireSectionOf(mark), color,
        lengthMm: length, from: { connector: at[0].ep.id, pin: at[0].cores[k]! }, to: { connector: x.ep.id, pin: x.cores[k]! },
      }));
    }
    wires.push(...mine);
    if (drawn < qty) warnings.push(`${mark || "Провод"}: на чертеже ${drawn} из ${qty} — у остальных нет контактов.`);
    const per = gen.wireCores_(w);
    if (per > 1) {
      const strip = positive(w.jacket || JACKET_STRIP_MM_);
      for (let c = 0; c * per < qty; c++) {
        const keys = mine.filter((x) => Math.floor(x.unit / per) === c).map((x) => x.key);
        if (keys.length) cables.push({ key: `${w.idx}#${c}`, mark, wireKeys: keys, lengthMm: length, stripMm: strip });
      }
    }
  });
  if (wires.some((wire) => wire.lengthMm === null)) warnings.push("Длина по чертежу не задана — провода без длины.");

  const pairs: Array<[string, string]> = [];
  if (S.twist.enabled) {
    const unitsOf = (group: number) => wires.filter((wire) => wire.group === group);
    if (S.twist.mode === "pairs") {
      gen.getTwistPairs().forEach(([a, b]) => {
        const left = unitsOf(a), right = unitsOf(b);
        for (let i = 0; i < Math.min(left.length, right.length); i++) pairs.push([left[i].key, right[i].key]);
      });
    } else {
      const units = gen.twistWireIndices_().flatMap(unitsOf);
      if (units.length === 2) pairs.push([units[0].key, units[1].key]);
      else if (units.length > 2) warnings.push(`Свивка всех проводов вместе (${units.length}) — на чертеже пары только по два провода, свивка не показана.`);
    }
  }

  const coverings: PlanCovering[] = [];
  connectors.forEach((c) => {
    if (c.tutLengthMm === undefined) return;
    coverings.push({ kind: "tut", name: `ТУТ на пайку ${c.designation}`, lengthMm: c.tutLengthMm, at: c.key, coveringKind: "heat-shrink" });
  });
  if (S.hs.enabled) {
    const ends = connectors.map((c) => c.key);
    let next = 0;
    S.hs.rows.forEach((row) => {
      const comp = gen.getCompById(row.compId);
      const count = Math.max(0, parseInt(row.cnt, 10) || 0);
      if (!comp || !count) return;
      for (let i = 0; i < count; i++) {
        coverings.push({ kind: "sleeve", name: comp.art || comp.name, lengthMm: positive(row.len), at: ends[next % Math.max(1, ends.length)], coveringKind: "heat-shrink" });
        next++;
      }
    });
  }
  if (S.wrap.enabled) {
    S.wrap.slots.forEach((slot) => {
      const comp = slot.mat ? gen.getCompById(slot.mat) : null;
      if (!comp) return;
      coverings.push({ kind: "wrap", name: comp.art || comp.name, lengthMm: positive(slot.len), coveringKind: slot.kind === "braid" ? "braid" : "tape" });
    });
  }
  return { connectors, wires, cables, pairs, twistPitchMm: positive(S.twist.pitch), coverings, warnings };
}

// ── Разъём по артикулу: библиотека компонентов или свободный корпус ─────────────────────────────────────────────────

export type ConnectorSource =
  | { readonly kind: "template"; readonly template: ComponentTemplatePlacementEnvelope; readonly articleVariantId: string }
  | { readonly kind: "free"; readonly contactCount: number | null; readonly libraryCode: string | null };

const sameArticle = (left: string, right: string) => {
  const a = normalizeConnectorArticle(left);
  return !!a && a === normalizeConnectorArticle(right);
};

/** Шаблон библиотеки, у которого среди артикулов есть этот (сравнение — как в базе разъёмов: без производителя,
 *  пробелов и регистра). */
export function findTemplateFor(article: string, templates: readonly ComponentTemplateSummary[]): ComponentTemplateSummary | null {
  return templates.find((template) => template.articleBindings.some((binding) => sameArticle(binding.articleKey, article))) ?? null;
}

/** Разъёмы плана: шаблон библиотеки компонентов по артикулу; нет его — свободный корпус на столько контактов, сколько
 *  знает база разъёмов (`housings`), и серией из неё в подвале таблицы. */
export async function resolveConnectorSources(
  plan: GeneratorDrawingPlan,
  housings: Readonly<Record<string, ConnectorHousing | null | undefined>>,
  templates: Pick<ComponentTemplateApi, "list" | "getVersion"> | null,
): Promise<{ sources: Record<string, ConnectorSource>; warnings: string[] }> {
  const warnings: string[] = [];
  let summaries: readonly ComponentTemplateSummary[] = [];
  if (templates) {
    try { summaries = await templates.list(); } catch { warnings.push("Библиотека компонентов не прочитана — разъёмы нарисованы свободными корпусами."); }
  }
  const loaded = new Map<string, ComponentTemplatePlacementEnvelope | null>();
  const sources: Record<string, ConnectorSource> = {};
  for (const connector of plan.connectors) {
    const housing = connector.article ? housings[connector.article] ?? null : null;
    const free: ConnectorSource = { kind: "free", contactCount: housing?.n ?? null, libraryCode: housing?.title?.trim() || housing?.family?.trim() || null };
    const summary = connector.article && templates ? findTemplateFor(connector.article, summaries) : null;
    if (summary && templates) {
      const key = `${summary.templateId}@${summary.version}`;
      if (!loaded.has(key)) {
        try {
          const full = await templates.getVersion(summary.templateId, summary.version);
          const content = full.content;
          loaded.set(key, content.schemaVersion === 3 || content.schemaVersion === 4 || content.schemaVersion === 5
            ? { templateId: full.templateId, version: full.version, versionSha256: full.versionSha256, code: full.code, name: full.name,
              articleBindings: full.articleBindings, assets: full.assets, content }
            : null);
        } catch { loaded.set(key, null); }
      }
      const template = loaded.get(key);
      const variant = template?.content.articleVariants.find((item) => sameArticle(item.articleKey, connector.article));
      if (template && variant) { sources[connector.key] = { kind: "template", template, articleVariantId: variant.id }; continue; }
    }
    sources[connector.key] = free;
  }
  return { sources, warnings };
}

// ── Сборка документа ─────────────────────────────────────────────────────────────────────────────────────────────

export interface GeneratorDrawing {
  readonly document: HarnessDesignDocument;
  readonly warnings: readonly string[];
  readonly connectors: number;
  readonly wires: number;
}

/** Свободный корпус на чертеже: прямоугольник 118 в ширину, контакты по правому краю через 16 (как его рисует редактор). */
const FREE_WIDTH = 118;
const freeHeight = (contacts: number) => Math.max(72, 44 + contacts * 16);
/** Раскладка Э4: колонка А слева, колонка В справа (просвет — по ступеньке на провод), между разъёмами по вертикали — 48.
 *  Всё в положительных координатах: редактор открывается с началом координат в левом верхнем углу поля. */
const E4_GAP_X = 240, E4_GAP_Y = 48;
/** Раскладка чертежа: от выхода А до выхода В ~ 520, узел ветвления посередине, разъёмы В разнесены по вертикали. */
const DRAWING_B_X = 700, DRAWING_GAP_Y = 90, EXIT = 60, DRAWING_TOP = 40;
const COVER_COLOR: Record<CoveringKind, string> = {
  "heat-shrink": "#424c53", nylon: "#b19c77", braid: "#b19c77", "metal-braid": "#73838d", tape: "#b19c77", band: "#b19c77",
};
const DIRECTION_ANGLE = { right: 0, down: 90, left: 180, up: 270 } as const;
/** Оболочка многожильного кабеля на участке чертежа. */
const CABLE_JACKET = "#3b4652";

function run(document: HarnessDesignDocument, command: EditorCommand): HarnessDesignDocument {
  return applyEditorCommand(document, command);
}

/** Документ чертежа по плану. Разъёмы — по `sources` (нет записи — свободный корпус по наибольшему контакту). Бросает
 *  ошибку, если результат не прошёл разбор редактора. */
export function buildHarnessDocument(plan: GeneratorDrawingPlan, sources: Readonly<Record<string, ConnectorSource>>): GeneratorDrawing {
  const warnings: string[] = [...plan.warnings];
  if (!plan.wires.length) throw new Error("Нет соединений по распиновке — задайте контакты проводов на разъёмах.");
  const used = plan.connectors.filter((c) => plan.wires.some((w) => w.from.connector === c.key || w.to.connector === c.key));
  const ids = new Map(used.map((c, i) => [c.key, `gen-x${i + 1}`]));
  const maxPin = (key: string) => Math.max(0, ...plan.wires.flatMap((w) => [w.from, w.to].filter((end) => end.connector === key).map((end) => end.pin)));
  const label = (c: PlanConnector) => `${c.designation}${c.article ? ` (${c.article})` : ""}`;

  // 1. Разъёмы: шаблон библиотеки или свободный корпус; сторона В — таблица контактами влево (к стороне А).
  // Версия контракта записи 1 — минимальная: без неё сервер принимает чертёж за экспорт старого TECHMAP-GRAPHER.
  let doc: HarnessDesignDocument = { ...createEmptyHarnessDesign(), requiredWriterContractVersion: 1 };
  for (const c of used) {
    const id = ids.get(c.key)!;
    const source = sources[c.key];
    let connector: ConnectorInstance | null = null;
    if (source?.kind === "template") {
      try {
        connector = createConnectorInstanceFromComponentTemplate(source.template, {
          id, designation: c.designation, articleVariantId: source.articleVariantId, e4Position: { x: 0, y: 0 }, drawingPosition: { x: 0, y: 0 },
        });
        const numbers = new Set(connector.contacts.map((contact) => contact.number));
        const missing = [...new Set(plan.wires.flatMap((w) => [w.from, w.to]).filter((end) => end.connector === c.key && !numbers.has(end.pin)).map((end) => end.pin))];
        if (missing.length) {
          warnings.push(`${label(c)}: в шаблоне библиотеки нет контактов ${missing.join(", ")} — разъём нарисован свободным корпусом.`);
          connector = null;
        }
      } catch (reason) {
        warnings.push(`${label(c)}: шаблон библиотеки не разместился (${reason instanceof Error ? reason.message : "ошибка"}) — свободный корпус.`);
        connector = null;
      }
    }
    if (!connector) {
      const free = source?.kind === "free" ? source : { contactCount: null, libraryCode: null };
      const count = Math.max(free.contactCount ?? 0, maxPin(c.key), 1);
      const base = createConnector(id, c.designation, count, { x: 0, y: 0 }, { x: 0, y: 0 }, c.article || c.designation);
      // Таблица как у Василия: №, Цепь, Терминал, Марка, Сечение, Цвет — «Тип» у свободного корпуса пуст, скрыт.
      connector = { ...base, libraryCode: free.libraryCode || "FREE", schematic: { ...base.schematic,
        baseColumns: base.schematic.baseColumns.map((column) => (column.key === "contactType" ? { ...column, visible: false } : column)) } };
      warnings.push(`${label(c)}: нет в библиотеке компонентов — нарисован свободным корпусом на ${count} конт.${free.contactCount ? "" : " (число контактов — по распиновке)"}`);
    }
    if (c.side === "B") connector = { ...connector, schematic: { ...connector.schematic, orientation: "contacts-left" } };
    doc = run(doc, { type: "add-connector", connector });
  }
  const connectorOf = (key: string) => doc.connectors.find((item) => item.id === ids.get(key))!;
  const contactOf = (key: string, pin: number) => connectorOf(key).contacts.find((contact) => contact.number === pin)!;

  // 2. Таблица контактов: марка, сечение, цвет, терминал — на обоих концах провода.
  const terminalWarned = new Set<string>();
  for (const wire of plan.wires) {
    for (const end of [wire.from, wire.to]) {
      const c = used.find((item) => item.key === end.connector)!;
      const connector = connectorOf(end.connector);
      const contact = contactOf(end.connector, end.pin);
      let terminal = c.terminal;
      if (terminal && connector.libraryBinding?.mode === "template") {
        const allowed = templateTerminalChoices(connector, contact.logicalContactId);
        const match = allowed.find((key) => key === terminal) ?? allowed.find((key) => sameArticle(key, terminal));
        if (!match && !terminalWarned.has(c.key)) warnings.push(`${label(c)}: терминал ${terminal} не входит в совместимые у шаблона — в таблице не указан.`);
        terminalWarned.add(c.key);
        terminal = match ?? "";
      }
      doc = run(doc, {
        type: "update-contact", connectorId: connector.id, contactId: contact.id, wire: wire.mark, wireSection: wire.section,
        ...(wire.color ? { color: wire.color } : {}), ...(terminal ? { terminalArticle: terminal } : {}),
      });
    }
  }

  // 3. Чертёж: А слева, В справа, по вертикали — вокруг нуля. Шаблон смотрит выходом не туда — поворот рисунка:
  //    А выходом вправо, В — влево.
  const sideOf = (side: Side) => used.filter((c) => c.side === side);
  const columns = [sideOf("A"), sideOf("B")];
  for (const c of used) {
    const connector = connectorOf(c.key);
    if (connector.libraryBinding?.mode !== "template") continue;
    const direction = connector.contacts.map((contact) => selectMaterializedContactRepresentation(connector, contact.id, "drawing")?.direction).find(Boolean);
    if (!direction) continue;
    const turn = ((DIRECTION_ANGLE[c.side === "A" ? "right" : "left"] - DIRECTION_ANGLE[direction] + 360) % 360);
    if (turn) doc = run(doc, { type: "set-drawing-placement", connectorId: connector.id, drawingId: DRAWING_VIEW_PLACEMENT_ID, rotationDegrees: turn > 180 ? turn - 360 : turn });
  }
  const extent = (c: PlanConnector) => {
    const connector = connectorOf(c.key);
    if (connector.libraryBinding?.mode !== "template") return { top: 0, height: freeHeight(connector.contacts.length) };
    const ys = connector.contacts.map((contact) => connectorContactPosition(connector, contact.id, "drawing")?.y ?? 0)
      .map((y) => y - connector.positions.drawing.y);
    return { top: Math.min(...ys) - 30, height: Math.max(...ys) - Math.min(...ys) + 60 };
  };
  const drawingTotal = (column: PlanConnector[]) => column.map(extent).reduce((sum, box) => sum + box.height, 0) + DRAWING_GAP_Y * Math.max(0, column.length - 1);
  const drawingMiddle = DRAWING_TOP + Math.max(...columns.map(drawingTotal)) / 2;
  columns.forEach((column, x) => {
    const boxes = column.map(extent);
    let y = drawingMiddle - drawingTotal(column) / 2;
    column.forEach((c, i) => {
      doc = run(doc, { type: "move-connector", connectorId: ids.get(c.key)!, view: "drawing", position: { x: x ? DRAWING_B_X : 0, y: Math.round(y - boxes[i].top) } });
      y += boxes[i].height + DRAWING_GAP_Y;
    });
  });

  // 4. Схема Э4 и провода с контакта на контакт (длина — длина по чертежу). Колонка А слева, В справа, строки напротив.
  //    Перекрёст в соседних строках ортогональный трассировщик Э4 с его зазорами не разводит — тогда колонка В уходит
  //    ниже колонки А целиком, и каждый провод спускается своей ступенькой.
  const placed = doc;
  const wireUp = (dropB: boolean) => {
    let next = placed;
    const failed: string[] = [];
    const done = new Map<string, string>();
    const e4Width = Math.max(0, ...columns[0].map((c) => connectorE4TableGeometry(connectorOf(c.key)).width));
    const heightsOf = (column: PlanConnector[]) => column.map((c) => connectorE4TableGeometry(connectorOf(c.key)).height);
    const total = (column: PlanConnector[]) => heightsOf(column).reduce((sum, h) => sum + h, 0) + E4_GAP_Y * Math.max(0, column.length - 1);
    const gapX = Math.max(E4_GAP_X, 30 * (plan.wires.length + 4));
    const middle = Math.max(...columns.map(total)) / 2;
    columns.forEach((column, x) => {
      const heights = heightsOf(column);
      let y = x && dropB ? total(columns[0]) + E4_GAP_Y * 2 : middle - total(column) / 2;
      column.forEach((c, i) => {
        next = run(next, { type: "move-connector", connectorId: ids.get(c.key)!, view: "e4", position: { x: x ? e4Width + gapX : 0, y: Math.round(y) } });
        y += heights[i] + E4_GAP_Y;
      });
    });
    plan.wires.forEach((wire, index) => {
      const id = `gen-w${index + 1}`;
      const from = { connectorId: ids.get(wire.from.connector)!, contactId: contactOf(wire.from.connector, wire.from.pin).id };
      const to = { connectorId: ids.get(wire.to.connector)!, contactId: contactOf(wire.to.connector, wire.to.pin).id };
      try {
        next = run(next, { type: "add-wire", wire: createWire(id, from, to, wire.lengthMm, "", wire.color || "#334155", 0, 0, 1, wire.color ? from : undefined) });
        done.set(wire.key, id);
      } catch (reason) {
        failed.push(`${wire.mark || "Провод"} ${wire.from.pin}→${wire.to.pin}: не лёг на схему (${reason instanceof Error ? reason.message : "ошибка"}).`);
      }
    });
    return { next, failed, done };
  };
  let wired = wireUp(false);
  if (wired.failed.length) {
    const dropped = wireUp(true);
    if (dropped.failed.length < wired.failed.length) wired = dropped;
  }
  doc = wired.next;
  const wireIds = wired.done;
  warnings.push(...wired.failed);

  // 4б. Кабели в оболочке: жилы — провода чертежа, собранные в кабель (длина реза и снятие оболочки — у кабеля).
  const cableMembers = new Map<string, string[]>();
  plan.cables.forEach((cable, index) => {
    const members = cable.wireKeys.map((key) => wireIds.get(key)).filter((id): id is string => !!id);
    if (!members.length) return;
    const id = `gen-cab${index + 1}`;
    const fits = cable.stripMm !== null && (cable.lengthMm === null || cable.stripMm * 2 < cable.lengthMm);
    try {
      doc = run(doc, { type: "add-cable", cable: {
        id, memberWireIds: members, lengthMm: cable.lengthMm, endCorrectionFromMm: 0, endCorrectionToMm: 0, cutRoundingStepMm: 1,
        ...(fits ? { sheathStrip: { fromMm: cable.stripMm, toMm: cable.stripMm } } : {}),
      } });
      cableMembers.set(id, members);
    } catch (reason) {
      warnings.push(`${cable.mark || "Кабель"}: кабель на чертеже не собран (${reason instanceof Error ? reason.message : "ошибка"}) — жилы нарисованы проводами.`);
    }
  });

  // 5. Свитые пары.
  plan.pairs.forEach(([a, b], index) => {
    const first = wireIds.get(a), second = wireIds.get(b);
    if (!first || !second) return;
    try {
      doc = run(doc, { type: "create-diff-pair", group: { id: `gen-p${index + 1}`, wireIds: [first, second], step: plan.twistPitchMm ?? 20, amplitude: 7, variant: 1 } });
    } catch {
      warnings.push(`Свитая пара ${index + 1}: у проводов нет общего участка на схеме — свивка не показана.`);
    }
  });

  // 6. Физическая трасса: выход у каждого разъёма, узел ветвления, ствол А → узел и ветка на каждый разъём В.
  const nodes: PhysicalNode[] = [];
  const nodeOf = new Map<string, string>();
  used.forEach((c, index) => {
    const connector = connectorOf(c.key);
    const id = `gen-n${index + 1}`;
    const touched = connector.contacts.filter((contact) => doc.wires.some((wire) => [wire.from, wire.to].some((end) => end.contactId === contact.id)));
    const points = touched.flatMap((contact) => { const p = connectorContactPosition(connector, contact.id, "drawing"); return p ? [p] : []; });
    const centre = points.length ? points.reduce((sum, p) => sum + p.y, 0) / points.length : connector.positions.drawing.y + extent(c).height / 2;
    const free = connector.libraryBinding?.mode !== "template";
    const x = c.side === "A"
      ? (free ? connector.positions.drawing.x + FREE_WIDTH : Math.max(...points.map((p) => p.x))) + EXIT
      : (free ? connector.positions.drawing.x : Math.min(...points.map((p) => p.x))) - EXIT;
    const draft: PhysicalNode = { id, connectorId: connector.id, position: { x: 0, y: 0 } };
    const world: Point = { x, y: centre };
    nodes.push({ ...draft, position: physicalNodeLocalPoint(doc, draft, world), ...(free ? { direction: c.side === "A" ? "right" as const : "left" as const } : {}) });
    nodeOf.set(c.key, id);
  });
  const segments: PhysicalSegment[] = [];
  const routed = (from: string, to: string): PhysicalSegment => ({ id: `gen-s${segments.length + 1}`, from, to, path: { kind: "routed", points: [] } });
  const aSide = sideOf("A"), bSide = sideOf("B");
  if (aSide.length === 1 && bSide.length === 1) {
    segments.push(routed(nodeOf.get(aSide[0].key)!, nodeOf.get(bSide[0].key)!));
  } else {
    const exits = (list: PlanConnector[]) => list.map((c) => nodes.find((n) => n.id === nodeOf.get(c.key))!);
    const world = (node: PhysicalNode) => physicalNodePoint(doc, node);
    const left = Math.max(...exits(aSide).map((n) => world(n).x), FREE_WIDTH + EXIT);
    const right = Math.min(...exits(bSide).map((n) => world(n).x), DRAWING_B_X - EXIT);
    const ys = exits(aSide.length === 1 ? aSide : bSide).map((n) => world(n).y);
    const junction: PhysicalNode = { id: "gen-j1", position: { x: Math.round(left + (right - left) * 0.5), y: Math.round(ys.reduce((s, y) => s + y, 0) / Math.max(1, ys.length)) } };
    nodes.push(junction);
    aSide.forEach((c) => segments.push(routed(nodeOf.get(c.key)!, junction.id)));
    bSide.forEach((c) => segments.push(routed(junction.id, nodeOf.get(c.key)!)));
  }
  let topology: PhysicalTopology = { snap: true, nodes, segments, routes: [] };
  topology = routePhysicalWires({ ...doc, physicalTopology: topology }, topology);
  // Участок, по которому идут все жилы кабеля и только они, — кабель в оболочке (ствол до разделки); ветки к разным
  // разъёмам после узла — жилы россыпью (кабель разделан, жилы разведены по разъёмам).
  if (cableMembers.size) {
    const through = (segmentId: string) => new Set(topology.routes.filter((route) => route.steps.some((step) => step.segmentId === segmentId)).map((route) => route.wireId));
    topology = { ...topology, segments: topology.segments.map((segment) => {
      const inside = through(segment.id);
      const cable = [...cableMembers].find(([, members]) => members.length === inside.size && members.every((id) => inside.has(id)));
      return cable ? { ...segment, specificationItemId: cable[0], mode: "round" as const, color: CABLE_JACKET } : segment;
    }) };
  }
  doc = run(doc, { type: "set-physical-topology", topology });
  const unrouted = doc.wires.filter((wire) => !topology.routes.some((route) => route.wireId === wire.id)).length;
  if (unrouted) warnings.push(`${unrouted} провод(а) без трассы на чертеже — проложите их в редакторе.`);

  // 7. Оболочки: ТУТ на пайку и отрезки термоусадки — у своего конца, оплётка/лента — по всему жгуту.
  const coverings: PhysicalCovering[] = [];
  const endSpan = (key: string, share: number) => {
    const node = nodeOf.get(key);
    const segment = segments.find((s) => s.from === node || s.to === node);
    if (!segment) return null;
    return segment.from === node ? { segmentId: segment.id, from: 0, to: share } : { segmentId: segment.id, from: 1 - share, to: 1 };
  };
  const stacked = new Map<string, number>();
  plan.coverings.forEach((covering) => {
    const id = `gen-c${coverings.length + 1}`;
    const length = covering.lengthMm === null ? { lengthMode: "auto" as const, lengthMm: null } : { lengthMode: "manual" as const, lengthMm: covering.lengthMm };
    const common = { id, name: covering.name, kind: covering.coveringKind, width: 0, color: COVER_COLOR[covering.coveringKind], ...length };
    if (covering.kind === "wrap") {
      coverings.push({ ...common, spans: segments.map((s) => ({ segmentId: s.id, from: 0, to: 1 })) });
      return;
    }
    if (!covering.at || !ids.has(covering.at)) return;
    const layer = stacked.get(covering.at) ?? 0;
    stacked.set(covering.at, layer + 1);
    const span = endSpan(covering.at, Math.min(0.45, 0.14 + layer * 0.06));
    if (span) coverings.push({ ...common, spans: [span] });
  });
  if (coverings.length) doc = run(doc, { type: "set-physical-topology", topology: { ...doc.physicalTopology!, coverings } });

  // 8. Размер: один участок А → В и одна длина у всех проводов — размер на участке (длины проводов идут от него).
  const lengths = [...new Set(doc.wires.map((wire) => wire.lengthMm))];
  if (segments.length === 1 && lengths.length === 1 && lengths[0] !== null) {
    const segment = doc.physicalTopology!.segments[0];
    const dimension: DrawingDimension = {
      id: "gen-d1", segmentId: segment.id, from: 0, to: segment.path.points.length + 1, pointCount: segment.path.points.length + 2,
      routeKey: segmentDimensionKey(doc, segment.id), mode: "aligned", modeOverride: false, offset: 40, lengthMm: lengths[0],
    };
    doc = run(doc, { type: "set-drawing-documents", documents: { tables: [], leaders: [], bomOrder: [], showDimensions: true, dimensions: [dimension] } });
  } else {
    // Пустые документы чертежа: без них редактор рисует «старые» размеры каждого провода косыми линиями через поле.
    doc = run(doc, { type: "set-drawing-documents", documents: { tables: [], leaders: [], bomOrder: [] } });
    if (segments.length > 1 && lengths.some((length) => length !== null)) {
      warnings.push("Длины ствола и веток генератор не знает — размеры на чертеже не проставлены, длины проводов перенесены.");
    }
  }

  const document = JSON.parse(JSON.stringify(doc)) as HarnessDesignDocument;
  parseHarnessDesignDocument(document);
  return { document, warnings: [...new Set(warnings)], connectors: doc.connectors.length, wires: doc.wires.length };
}

// ── Кнопка: план → разъёмы → документ ────────────────────────────────────────────────────────────────────────────

export async function buildGeneratorDrawing(
  D: GenData, S: GenState,
  housings: Readonly<Record<string, ConnectorHousing | null | undefined>>,
  templates: Pick<ComponentTemplateApi, "list" | "getVersion"> | null,
): Promise<GeneratorDrawing> {
  const plan = planFromGenerator(D, S);
  const { sources, warnings } = await resolveConnectorSources(plan, housings, templates);
  const built = buildHarnessDocument(plan, sources);
  return { ...built, warnings: [...warnings, ...built.warnings] };
}

// ── «Ручные правки»: какую версию чертежа построил генератор ─────────────────────────────────────────────────────
// Версия, сохранённая кнопкой, запоминается в браузере за проектом и сборкой. Текущая версия чертежа другая (или
// запомненной нет — чертёж делали в редакторе, загрузили или строили в другом браузере) — перед перезаписью спросить.

const stampKey = (project: string, index: string) => `erp.generatorDrawing.${project}::${index}`;

export function generatedRevision(project: string, index: string): number | null {
  try {
    const value = Number(window.localStorage.getItem(stampKey(project, index)));
    return Number.isSafeInteger(value) && value > 0 ? value : null;
  } catch { return null; }
}

export function rememberGeneratedRevision(project: string, index: string, revision: number | null): void {
  try {
    if (revision === null) window.localStorage.removeItem(stampKey(project, index));
    else window.localStorage.setItem(stampKey(project, index), String(revision));
  } catch { /* без хранилища браузера — спросим и в следующий раз */ }
}
