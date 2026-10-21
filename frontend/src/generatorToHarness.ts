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
import { hasTopView } from "./connectorTopView";
import { housingFace } from "./harness/editor/housing-face";
import { endJoined, JACKET_STRIP_MM_, makeGen, parseDecimal_, sideRu_, spliceStripMm, tailStripMm, type GenData, type GenState, type Side } from "./tcGenLogic";
import { spliceLabel, spliceOf } from "./tcGenSplices";
import { markSpecs } from "./tcGenMarks";
import { coreColor, SHIELD_COLOR, wireSectionOf } from "./pinoutCheck";
import { planTwist } from "./twistGroups";
import { withTwistNotes } from "./twistDrawing";
import { applyEditorCommand, createConnector, createWire, type EditorCommand } from "./harness/editor/commands";
import {
  createConnectorInstanceFromComponentTemplate,
  type ComponentTemplatePlacementEnvelope,
} from "./harness/editor/component-template-placement";
import { normalizeConnectorArticle } from "./harness/editor/connector-pitch";
import { segmentDimensionKey, type DrawingDimension } from "./harness/editor/drawing-dimensions";
import type { DrawingDocuments } from "./harness/editor/drawing-documents";
import { zoneAnnotations } from "./generatorDrawingNotes";
import { DRAWING_VIEW_PLACEMENT_ID } from "./harness/editor/drawing-scale";
import { selectMaterializedContactRepresentation } from "./harness/editor/materialized-contact-representation";
import {
  connectorContactPosition,
  connectorE4TableGeometry,
  createEmptyHarnessDesign,
  createFreeEndpoint,
  createJunctionEndpoint,
  isFreeEndpoint,
  isJunctionEndpoint,
  wireEndpointE4Anchor,
  parseHarnessDesignDocument,
  templateTerminalChoices,
  type ConnectorInstance,
  type HarnessDesignDocument,
  type Point,
  type WireEndpoint,
  type WireStripProfileBinding,
} from "./harness/editor/model";
import type { CoveringKind, PhysicalCovering } from "./harness/editor/physical-coverings";
import { physicalNodeLocalPoint, physicalNodePoint } from "./harness/editor/physical-ports";
import type { PhysicalNode, PhysicalRoute, PhysicalSegment, PhysicalTopology } from "./harness/editor/physical-topology-model";
import { routePhysicalWires } from "./harness/editor/physical-wire-routing";
import { fanWidth, orderByPartners, placeDrawingLabels, trunkGeometry, trunkTopology, type TrunkGeometry, type TrunkParts } from "./drawingLayout";
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
  /** Трубка на разъёме ветви (ТТК поверх разъёма, форма «Трубка на разъёме»): наименование и длина, мм. */
  readonly shrink?: { readonly name: string; readonly lengthMm: number | null };
  /** Оплётка на ветви разъёма А («Оплётка ветви»): наименование и длина, мм. */
  readonly braid?: { readonly name: string; readonly lengthMm: number | null };
  /** Маркировка у разъёма (строки «Маркировка» с местом «У разъёма»): текст, материал, длина отрезка, мм. */
  readonly marks?: readonly { readonly text: string; readonly name: string; readonly lengthMm: number | null }[];
  /** «Ветвь по КД, мм» формы: размер ветви на схеме жгута как на СБ; нет — по длинам проводов. */
  readonly kdBranchMm?: number;
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
  /** Цепь («GND», «CAN_H») из распиновки генератора; "" — не задана. */
  readonly circuit: string;
  /** Экран кабеля, заведённый на контакты (позиция после жил). */
  readonly shield?: boolean;
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
  /** `zone` — общая трубка на зону сростков (по КД «ТУТ на месте Б»): `zone` — номер зоны. */
  readonly kind: "tut" | "sleeve" | "wrap" | "zone";
  readonly zone?: number;
  readonly name: string;
  readonly lengthMm: number | null;
  /** Конец жгута (разъём), у которого лежит отрезок; у оплётки/ленты — весь жгут. */
  readonly at?: string;
  readonly coveringKind: CoveringKind;
}

/** Провод сростка: с контакта разъёма своей стороны в сросток (`side` — сторона контакта). */
export interface PlanSpliceWire {
  readonly key: string;
  readonly mark: string;
  readonly section: string;
  readonly color: string;
  readonly circuit: string;
  readonly lengthMm: number | null;
  readonly side: Side;
  readonly end: { readonly connector: string; readonly pin: number };
}

/** Хвост сростка: провод из сростка со свободным концом своей длины («хвост 60 мм»). */
export type PlanTail = Omit<PlanSpliceWire, "side" | "end">;

/** Сросток формы (tcGenSplices.ts) — узел соединения чертежа: провода с контактов и хвосты сходятся в нём. */
export interface PlanSplice {
  readonly key: string;
  readonly label: string;
  readonly wires: readonly PlanSpliceWire[];
  readonly tails: readonly PlanTail[];
}

export interface GeneratorDrawingPlan {
  readonly connectors: readonly PlanConnector[];
  readonly wires: readonly PlanWire[];
  /** Сростки «Сложной сборки»; нет — пусто. */
  readonly splices?: readonly PlanSplice[];
  /** Зоны сростков — ключи сростков, лежащих физически вместе: на чертеже одна зона «С1–С4» вместо цепочки узлов. */
  readonly zones?: readonly (readonly string[])[];
  /** Провода с контакта в свободный конец (хвост без сростка): `end` — контакт, `side` — его сторона. */
  readonly tails?: readonly PlanSpliceWire[];
  /** Кабели в оболочке (на форме — кабель с числом жил больше одной). */
  readonly cables: readonly PlanCable[];
  /** Свитые пары — ключи проводов. */
  readonly pairs: readonly (readonly [string, string])[];
  /** Свитые группы из трёх проводов и больше — ключи проводов (в редакторе пара только из двух: группа — надписью). */
  readonly twistGroups?: readonly (readonly string[])[];
  readonly twistPitchMm: number | null;
  readonly coverings: readonly PlanCovering[];
  /** Зачистка концов в сростках, мм (форма, по умолчанию 5 мм); нет сростков — не задана. */
  readonly spliceStripMm?: number | null;
  /** Зачистка хвостов под лужение, мм (по умолчанию 3 мм); хвосты не лудятся — не задана. */
  readonly tailStripMm?: number | null;
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

/** Сечение из марки — в pinoutCheck.ts (его читает и схема связей генератора, без кода редактора). */
export { wireSectionOf };

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
    const designation = label || `XS${index + 1}`;
    const sh = ep.side === "A" ? gen.shrinkOf_(ep) : null;
    const slot = ep.side === "A" && gen.braidEp_(ep) ? S.wrap.slots.find((sl) => sl.kind === "braid" && sl.mat && gen.getCompById(sl.mat)) : undefined;
    const braidComp = slot ? gen.getCompById(slot.mat) : null;
    const braidLen = slot ? positive(ep.braidLen) ?? positive(gen.braidLenDefault_(slot.len)) : null;
    const marks = (S.mark ? markSpecs(S.markRows || [], D.components) : []).filter((m) => m.placeKind === "conn" && m.target.split(/[\s()]+/).includes(designation))
      .map((m) => ({ text: m.text, name: m.name, lengthMm: m.lengthMm ?? null }));
    return { key: ep.id, side: ep.side, designation, article: compText(ep.connId), terminal: record?.article ?? "", ...tut,
      ...(sh ? { shrink: { name: sh.comp.art || sh.comp.name || "", lengthMm: positive(sh.len) } } : {}),
      ...(braidComp ? { braid: { name: braidComp.art || braidComp.name || "", lengthMm: braidLen } } : {}),
      ...(marks.length ? { marks } : {}), ...(positive(ep.kdBranch) ? { kdBranchMm: positive(ep.kdBranch)! } : {}) };
  });

  const active = gen.activeWires_();
  const wires: PlanWire[] = [];
  const cables: PlanCable[] = [];
  // Сростки: провод, у которого один конец на контакте разъёма, а другой в сростке, — провод узла; провод из сростка
  // в свободный конец — хвост узла; провод с контакта в свободный конец — хвост без сростка.
  const splices = new Map<string, { wires: PlanSpliceWire[]; tails: PlanTail[]; loose: string[] }>();
  const tails: PlanSpliceWire[] = [];
  const spliced = new Map<number, number>();
  (S.splices || []).forEach((sp) => splices.set(sp.id, { wires: [], tails: [], loose: [] }));
  active.forEach((w) => {
    const mark = compText(w.compId);
    const length = positive(S.advancedLength ? w.length : S.drawingLength);
    const colors = gen.coreColors_(w);
    for (let k = 0; k < gen.unitCount_(w); k++) {
      const pinsOn = (side: Side) => gen.endpointsOnSide_(side).filter((ep) => gen.routedWireIdxs_(ep).includes(w.idx))
        .flatMap((ep) => { const p = gen.corePinsOf_(ep, w.idx)[k]; return p != null ? [{ connector: ep.id, pin: p }] : []; });
      const sides = (["A", "B"] as Side[]).map((side) => ({ side, pins: pinsOn(side), sp: spliceOf(S, { idx: w.idx, unit: k, side }) }));
      const [a, b] = sides;
      const join = sides.filter((x) => x.sp);
      const color = hex6(coreColor(colors[k])?.hex) || wireColorHex(mark), circuit = (w.nets?.[k] || "").trim();
      const tailAt = sides.find((x) => !x.pins.length && endJoined(S, w.idx, k, x.side) === "tail");
      if (!join.length) {
        const pinned = sides.find((x) => x.pins.length);
        if (!tailAt || !pinned) continue;
        spliced.set(w.idx, (spliced.get(w.idx) || 0) + 1);
        pinned.pins.forEach((pin, j) => tails.push({ key: `${w.idx}.${k}${j ? `.${j}` : ""}`, mark, section: wireSectionOf(mark), color, circuit,
          lengthMm: length, side: pinned.side, end: pin }));
        continue;
      }
      spliced.set(w.idx, (spliced.get(w.idx) || 0) + 1);
      const name = `${mark || "провод"}${length ? ` ${length} мм` : ""}`;
      if (join.length === 2) { warnings.push(`${name}: провод между сростками ${spliceLabel(S, a.sp!.id)} и ${spliceLabel(S, b.sp!.id)} — на чертеже не показан.`); continue; }
      const at = join[0], other = at === a ? b : a;
      const bucket = splices.get(at.sp!.id)!;
      if (other.pins.length) {
        other.pins.forEach((pin, j) => bucket.wires.push({ key: `${w.idx}.${k}${j ? `.${j}` : ""}`, mark, section: wireSectionOf(mark),
          color, circuit, lengthMm: length, side: other.side, end: pin }));
      } else if (tailAt === other) bucket.tails.push({ key: `${w.idx}.${k}`, mark, section: wireSectionOf(mark), color, circuit, lengthMm: length });
      else bucket.loose.push(`${name} (второй конец не задан)`);
    }
  });
  active.forEach((w, group) => {
    const mark = compText(w.compId);
    const routed = ordered.filter((ep) => gen.routedWireIdxs_(ep).includes(w.idx));
    const ends = routed.map((ep) => ({ ep, cores: gen.corePinsOf_(ep, w.idx) })).filter((x) => x.cores.some((p) => p != null));
    const qty = gen.unitCount_(w);
    if (ends.length < 2) {
      if (routed.length >= 2 && !spliced.has(w.idx)) warnings.push(`${mark || "Провод"}: распиновка не задана — на чертёж не попал.`);
      return;
    }
    const length = positive(S.advancedLength ? w.length : S.drawingLength);
    const colors = gen.coreColors_(w);
    const mine: PlanWire[] = [];
    let drawn = 0;
    // Жилы, затем экраны (экран на контактах двух разъёмов — провод чертежа «Экран …» внутри того же кабеля).
    for (let k = 0; k < qty + gen.shieldCount_(w); k++) {
      const at = ends.filter((x) => x.cores[k] != null);
      if (at.length < 2) continue;
      const shield = k >= qty;
      if (!shield) drawn++;
      const color = shield ? SHIELD_HEX : hex6(coreColor(colors[k])?.hex) || wireColorHex(mark);
      const circuit = (w.nets?.[k] || "").trim();
      at.slice(1).forEach((x, j) => mine.push({
        key: j ? `${w.idx}.${k}.${j}` : `${w.idx}.${k}`, group, unit: k, mark: shield ? `Экран ${mark}`.trim() : mark, section: shield ? "" : wireSectionOf(mark), color,
        circuit, lengthMm: length, from: { connector: at[0].ep.id, pin: at[0].cores[k]! }, to: { connector: x.ep.id, pin: x.cores[k]! },
        ...(shield ? { shield: true } : {}),
      }));
    }
    wires.push(...mine);
    if (drawn + (spliced.get(w.idx) || 0) < qty) warnings.push(`${mark || "Провод"}: на чертеже ${drawn} из ${qty} — у остальных нет контактов.`);
    const per = gen.wireCores_(w);
    if (per > 1) {
      const strip = positive(w.jacket || JACKET_STRIP_MM_);
      const cableOf = (unit: number) => (unit >= qty ? unit - qty : Math.floor(unit / per));
      for (let c = 0; c * per < qty; c++) {
        const keys = mine.filter((x) => cableOf(x.unit) === c).map((x) => x.key);
        if (keys.length) cables.push({ key: `${w.idx}#${c}`, mark, wireKeys: keys, lengthMm: length, stripMm: strip });
      }
    }
  });
  if (wires.some((wire) => wire.lengthMm === null)) warnings.push("Длина по чертежу не задана — провода без длины.");

  // Свивка (twistGroups.ts): группа из двух — свитая пара, из трёх и больше — группа.
  const twist = S.twist.enabled
    ? planTwist(S.twist.mode, S.twist.mode === "pairs" ? gen.getTwistPairs() : [gen.twistWireIndices_()],
      (group) => wires.filter((wire) => wire.group === group).map((wire) => wire.key))
    : { pairs: [], groups: [] };
  const pairs = twist.pairs;

  const coverings: PlanCovering[] = [];
  connectors.forEach((c) => {
    if (c.tutLengthMm === undefined) return;
    coverings.push({ kind: "tut", name: `ТУТ на пайку ${c.designation}`, lengthMm: c.tutLengthMm, at: c.key, coveringKind: "heat-shrink" });
  });
  // Сростки одной зоны: с одних разъёмов стороны А — одна группа ПФ («Пайка С1, С2»); есть общая термоусадка на жгут
  // («ТУТ на месте Б») — все сростки под одной трубкой, одна зона. Зона нужна уже здесь: общая трубка садится на неё.
  const zoneKeys = (list: readonly PlanSplice[]): string[][] => {
    if (!list.length) return [];
    if (S.hs.enabled && S.hs.rows.some((row) => gen.getCompById(row.compId) && parseInt(row.cnt, 10) > 0)) return [list.map((sp) => sp.key)];
    const groups = new Map<string, string[]>();
    list.forEach((sp) => {
      const ids = [...new Set(sp.wires.filter((w) => w.side === "A").map((w) => w.end.connector))].sort();
      const ids2 = [...new Set(sp.wires.filter((w) => w.side === "B").map((w) => w.end.connector))].sort();
      const key = ids.length ? `A:${ids.join(",")}` : ids2.length ? `B:${ids2.join(",")}` : `S:${sp.key}`;
      groups.set(key, [...(groups.get(key) ?? []), sp.key]);
    });
    return [...groups.values()];
  };
  const planSplicesEarly = [...splices].flatMap(([id, got]) => (got.wires.length ? [{ key: id, label: spliceLabel(S, id), wires: got.wires, tails: got.tails }] : []));
  const zones = zoneKeys(planSplicesEarly);
  if (S.hs.enabled) {
    const ends = connectors.map((c) => c.key);
    let next = 0, zoned = 0;
    S.hs.rows.forEach((row) => {
      const comp = gen.getCompById(row.compId);
      const count = Math.max(0, parseInt(row.cnt, 10) || 0);
      if (!comp || !count) return;
      for (let i = 0; i < count; i++) {
        // Первые трубки — по одной на зону сростков, остальные — у концов жгута.
        if (zoned < zones.length) { coverings.push({ kind: "zone", zone: zoned++, name: comp.art || comp.name, lengthMm: positive(row.len), coveringKind: "heat-shrink" }); continue; }
        coverings.push({ kind: "sleeve", name: comp.art || comp.name, lengthMm: positive(row.len), at: ends[next % Math.max(1, ends.length)], coveringKind: "heat-shrink" });
        next++;
      }
    });
  }
  // Оплётка на ветвях разъёмов (форма «Оплётка ветви») рисуется на ветвях; общая на весь жгут — только когда ветвей с ней нет.
  const braidOnBranches = connectors.some((c) => c.braid);
  if (S.wrap.enabled) {
    S.wrap.slots.forEach((slot) => {
      const comp = slot.mat ? gen.getCompById(slot.mat) : null;
      if (!comp || (braidOnBranches && slot.kind === "braid")) return;
      coverings.push({ kind: "wrap", name: comp.art || comp.name, lengthMm: positive(slot.len), coveringKind: slot.kind === "braid" ? "braid" : "tape" });
    });
  }
  const planSplices: PlanSplice[] = (S.splices || []).flatMap((sp) => {
    const got = splices.get(sp.id)!;
    const label = spliceLabel(S, sp.id);
    if (got.loose.length) warnings.push(`Сросток ${label}: ${got.loose.join(", ")} — на чертеже не показан, задайте второй конец.`);
    return got.wires.length ? [{ key: sp.id, label, wires: got.wires, tails: got.tails }] : [];
  });
  const tailed = tails.length > 0 || planSplices.some((sp) => sp.tails.length > 0);
  return { connectors, wires, cables, pairs, ...(twist.groups.length ? { twistGroups: twist.groups } : {}), twistPitchMm: positive(S.twist.pitch), coverings, warnings,
    ...(planSplices.length ? { splices: planSplices, zones: zoneKeys(planSplices), spliceStripMm: positive(spliceStripMm(S)) } : {}), ...(tails.length ? { tails } : {}),
    ...(tailed && S.tailTin?.enabled ? { tailStripMm: positive(tailStripMm(S)) } : {}) };
}

// ── Разъём по артикулу: библиотека компонентов или свободный корпус ─────────────────────────────────────────────────

export type ConnectorSource =
  | { readonly kind: "template"; readonly template: ComponentTemplatePlacementEnvelope; readonly articleVariantId: string }
  | { readonly kind: "free"; readonly contactCount: number | null; readonly libraryCode: string | null; readonly housing?: ConnectorHousing | null };

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
    const free: ConnectorSource = { kind: "free", contactCount: housing?.n ?? null, libraryCode: housing?.title?.trim() || housing?.family?.trim() || null,
      ...(housing && hasTopView(housing) ? { housing } : {}) };
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
/** Корпус из базы разъёмов (торец с гнёздами) — размеры рисунка; обычная рамка — null. */
const faceOf = (connector: ConnectorInstance) => (connector.housing && connector.housingView === "face" ? housingFace(connector.housing, null, connector.schematic.orientation === "contacts-left") : null);
/** Выход провода от торца разъёма из базы: короче, чем от рамки (провода уже идут из гнёзд). */
const FACE_EXIT = 56;
/** Раскладка Э4: колонка А слева, колонка В справа (просвет — по ступеньке на провод), между разъёмами по вертикали — 48.
 *  Всё в положительных координатах: редактор открывается с началом координат в левом верхнем углу поля. */
const E4_GAP_X = 240, E4_GAP_Y = 48;
/** Раскладка чертежа: от выхода А до выхода В ~ 520, узел ветвления посередине, разъёмы В разнесены по вертикали. */
const DRAWING_B_X = 700, DRAWING_GAP_Y = 90, EXIT = 60, DRAWING_TOP = 40;
/** Отрезок провода от контакта до общего узла разъёма — под ТУТ на пайку. */
const TUT_STUB = 60;
/** Хвост на схеме Э4: свободный конец от узла по горизонтали, следующий хвост того же узла — ступенькой ниже. */
const TAIL_E4 = 64, TAIL_E4_STEP = 22;
/** Узел двойного обжима на схеме Э4 — на столько от контакта по первому проводу. */
const DOUBLE_E4 = 18;
const COVER_COLOR: Record<CoveringKind, string> = {
  "heat-shrink": "#424c53", nylon: "#b19c77", braid: "#b19c77", "metal-braid": "#73838d", tape: "#b19c77", band: "#b19c77",
};
const DIRECTION_ANGLE = { right: 0, down: 90, left: 180, up: 270 } as const;
/** Оболочка многожильного кабеля на участке чертежа. */
const CABLE_JACKET = "#3b4652";
/** Экран (дренаж) на чертеже — лужёная медь, как на схеме распиновки. */
const SHIELD_HEX = hex6(SHIELD_COLOR);

function run(document: HarnessDesignDocument, command: EditorCommand): HarnessDesignDocument {
  return applyEditorCommand(document, command);
}

/** Диаметр жилы по сечению из марки («0,2 мм²», «24 AWG»), мм; сечение не прочитано — 1 мм. */
export function coreDiameterMm(section: string): number {
  const awg = /^(\d+)\s*AWG$/i.exec(section.trim());
  const mm2 = parseDecimal_(section.replace(/\s*мм².*$/, ""));
  const d = awg ? 0.127 * 92 ** ((36 - Number(awg[1])) / 39) : mm2 > 0 ? 2 * Math.sqrt(mm2 / Math.PI) : 1;
  return Math.max(0.01, Math.round(d * 100) / 100);
}

/** Зачистка конца провода на чертеже (владелец 17.10: зачистку под сросток «так же в чертеже надо обновить»): профиль
 *  разделки из одного слоя — жила, L1 — длина зачистки. Профиль рисует генератор, а не справочник БД.КОАКС: источник
 *  «генератор ТК», снимок и запись — постоянные. */
const GEN_STRIP_SNAPSHOT = "4f2a9c61-0d3e-4b57-9a18-7c5e2b0d1f17";
const GEN_STRIP_RECORD = "0".repeat(64);
export function generatorStripProfile(lengthMm: number, section: string, title: string): WireStripProfileBinding {
  const mm = String(lengthMm).replace(".", ",");
  return { sourceId: "генератор ТК", snapshotId: GEN_STRIP_SNAPSHOT, snapshotSha256: GEN_STRIP_RECORD, recordId: GEN_STRIP_RECORD,
    entityType: "coax-termination", sourceKey: `${title} ${mm} мм`, displayName: `${title} ${mm} мм`,
    layers: [{ index: 1, diameterMm: coreDiameterMm(section), stripLengthMm: lengthMm }] };
}

/** Документ чертежа по плану. Разъёмы — по `sources` (нет записи — свободный корпус по наибольшему контакту). Бросает
 *  ошибку, если результат не прошёл разбор редактора. */
export function buildHarnessDocument(plan: GeneratorDrawingPlan, sources: Readonly<Record<string, ConnectorSource>>): GeneratorDrawing {
  const warnings: string[] = [...plan.warnings];
  const spliceWires = [...(plan.splices || []).flatMap((sp) => sp.wires), ...(plan.tails || [])];
  if (!plan.wires.length && !spliceWires.length) throw new Error("Нет соединений по распиновке — задайте контакты проводов на разъёмах.");
  /** Концы проводов на контактах: провода с контакта на контакт, провода сростков и хвосты с контакта. */
  const contactEnds = [...plan.wires.flatMap((w) => [w.from, w.to].map((end) => ({ end, wire: w as Pick<PlanWire, "mark" | "section" | "color" | "circuit"> }))),
    ...spliceWires.map((w) => ({ end: w.end, wire: w }))];
  const used = plan.connectors.filter((c) => contactEnds.some((x) => x.end.connector === c.key));
  const ids = new Map(used.map((c, i) => [c.key, `gen-x${i + 1}`]));
  const maxPin = (key: string) => Math.max(0, ...contactEnds.filter((x) => x.end.connector === key).map((x) => x.end.pin));
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
        const missing = [...new Set(contactEnds.map((x) => x.end).filter((end) => end.connector === c.key && !numbers.has(end.pin)).map((end) => end.pin))];
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
      const free: Extract<ConnectorSource, { kind: "free" }> = source?.kind === "free" ? source : { kind: "free", contactCount: null, libraryCode: null };
      const count = Math.max(free.contactCount ?? 0, maxPin(c.key), 1);
      const base = createConnector(id, c.designation, count, { x: 0, y: 0 }, { x: 0, y: 0 }, c.article || c.designation);
      // Таблица как у Василия: №, Цепь, Терминал, Марка, Сечение, Цвет — «Тип» у свободного корпуса пуст, скрыт.
      // Корпус из базы разъёмов — торец серии с гнёздами (тот же рисунок, что в «Распиновке»), провода идут из гнёзд; нет гнёзд — рамка.
      const face = free.housing && housingFace(free.housing) ? { housing: free.housing, housingView: "face" as const } : {};
      connector = { ...base, libraryCode: free.libraryCode || "FREE", ...face, schematic: { ...base.schematic,
        baseColumns: base.schematic.baseColumns.map((column) => (column.key === "contactType" ? { ...column, visible: false } : column)) } };
      warnings.push(`${label(c)}: нет в библиотеке компонентов — нарисован свободным корпусом на ${count} конт.${free.contactCount ? "" : " (число контактов — по распиновке)"}`);
    }
    if (c.side === "B") connector = { ...connector, schematic: { ...connector.schematic, orientation: "contacts-left" } };
    doc = run(doc, { type: "add-connector", connector });
  }
  const connectorOf = (key: string) => doc.connectors.find((item) => item.id === ids.get(key))!;
  const contactOf = (key: string, pin: number) => connectorOf(key).contacts.find((contact) => contact.number === pin)!;

  // 2. Таблица контактов: марка, сечение, цвет, терминал и цепь — на обоих концах провода.
  const terminalWarned = new Set<string>();
  // Двойной обжим: второй провод в том же контакте строку таблицы не переписывает — в ней провод, что пришёл первым.
  const filled = new Set<string>();
  for (const { end, wire } of contactEnds) {
    if (filled.has(`${end.connector}:${end.pin}`)) continue;
    filled.add(`${end.connector}:${end.pin}`);
    {
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
        ...(wire.color ? { color: wire.color } : {}), ...(terminal ? { terminalArticle: terminal } : {}), ...(wire.circuit ? { circuit: wire.circuit } : {}),
      });
    }
  }

  // 3. Чертёж: А слева, В справа, по вертикали — вокруг нуля. Шаблон смотрит выходом не туда — поворот рисунка:
  //    А выходом вправо, В — влево. Колонка В — в порядке партнёров на стороне А (меньше перекрёстков).
  const sideOf = (side: Side) => used.filter((c) => c.side === side);
  const sideKey = new Map(used.map((c) => [c.key, c.side]));
  const links: Array<[string, string]> = [];
  plan.wires.forEach((w) => {
    const [a, b] = sideKey.get(w.from.connector) === "A" ? [w.from.connector, w.to.connector] : [w.to.connector, w.from.connector];
    if (sideKey.get(a) === "A" && sideKey.get(b) === "B") links.push([a, b]);
  });
  (plan.splices || []).forEach((sp) => sp.wires.filter((w) => w.side === "A")
    .forEach((a) => sp.wires.filter((w) => w.side === "B").forEach((b) => links.push([a.end.connector, b.end.connector]))));
  const columns = [sideOf("A"), orderByPartners(sideOf("A").map((c) => c.key), sideOf("B"), (c) => c.key, links)];
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
    if (connector.libraryBinding?.mode !== "template") return { top: 0, height: faceOf(connector)?.height ?? freeHeight(connector.contacts.length) };
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
    /** Узлы двойного обжима → разъём плана, у которого они стоят. */
    const doubled = new Map<string, string>();
    const e4Width = Math.max(0, ...columns[0].map((c) => connectorE4TableGeometry(connectorOf(c.key)).width));
    const heightsOf = (column: PlanConnector[]) => column.map((c) => connectorE4TableGeometry(connectorOf(c.key)).height);
    const total = (column: PlanConnector[]) => heightsOf(column).reduce((sum, h) => sum + h, 0) + E4_GAP_Y * Math.max(0, column.length - 1);
    const gapX = Math.max(E4_GAP_X, 30 * (plan.wires.length + spliceWires.length + (plan.splices || []).reduce((n, sp) => n + sp.tails.length, 0) + 4));
    const middle = Math.max(...columns.map(total)) / 2;
    columns.forEach((column, x) => {
      const heights = heightsOf(column);
      let y = x && dropB ? total(columns[0]) + E4_GAP_Y * 2 : middle - total(column) / 2;
      column.forEach((c, i) => {
        next = run(next, { type: "move-connector", connectorId: ids.get(c.key)!, view: "e4", position: { x: x ? e4Width + gapX : 0, y: Math.round(y) } });
        y += heights[i] + E4_GAP_Y;
      });
    });
    // Двойной обжим (два провода в одном контакте): у контакта редактора один вывод — второй провод приходит в узел на
    // первом проводе у самого контакта (как «подключение к проводу» в редакторе); на чертеже узел — у выхода разъёма.
    const taken = new Map<string, string>();
    plan.wires.forEach((wire, index) => {
      const id = `gen-w${index + 1}`;
      const from = { connectorId: ids.get(wire.from.connector)!, contactId: contactOf(wire.from.connector, wire.from.pin).id };
      const to = { connectorId: ids.get(wire.to.connector)!, contactId: contactOf(wire.to.connector, wire.to.pin).id };
      const keyOf = (end: PlanWire["from"]) => `${end.connector}:${end.pin}`;
      const shared = [wire.to, wire.from].find((end) => taken.has(keyOf(end)));
      if (shared) {
        try {
          const firstId = taken.get(keyOf(shared))!;
          const first = next.wires.find((w) => w.id === firstId)!;
          const contact = { connectorId: ids.get(shared.connector)!, contactId: contactOf(shared.connector, shared.pin).id };
          const pts = [wireEndpointE4Anchor(next, first.from)!.position, ...first.e4Route, wireEndpointE4Anchor(next, first.to)!.position];
          const atEnd = first.to.connectorId === contact.connectorId && (first.to as typeof contact).contactId === contact.contactId;
          const [tip, toward] = atEnd ? [pts[pts.length - 1], pts[pts.length - 2]] : [pts[0], pts[1]];
          const span = Math.hypot(toward.x - tip.x, toward.y - tip.y) || 1;
          const step = Math.min(DOUBLE_E4, span / 2);
          const position = { x: tip.x + ((toward.x - tip.x) / span) * step, y: tip.y + ((toward.y - tip.y) / span) * step };
          const tag = `gen-dc${index + 1}`;
          const other = shared === wire.to ? from : to;
          const node = createJunctionEndpoint(tag);
          next = run(next, { type: "create-junction", junction: { id: tag, position, wireIds: [firstId, id] },
            branchWire: createWire(id, shared === wire.to ? other : node, shared === wire.to ? node : other, wire.lengthMm, wire.circuit, wire.color || "#334155", 0, 0, 1, wire.color ? other : undefined) });
          done.set(wire.key, id);
          doubled.set(tag, shared.connector);
        } catch (reason) {
          failed.push(`${wire.mark || "Провод"} ${wire.from.pin}→${wire.to.pin}: двойной обжим не лёг на схему (${reason instanceof Error ? reason.message : "ошибка"}).`);
        }
        return;
      }
      try {
        next = run(next, { type: "add-wire", wire: createWire(id, from, to, wire.lengthMm, wire.circuit, wire.color || "#334155", 0, 0, 1, wire.color ? from : undefined) });
        done.set(wire.key, id);
        taken.set(keyOf(wire.from), id);
        taken.set(keyOf(wire.to), id);
      } catch (reason) {
        failed.push(`${wire.mark || "Провод"} ${wire.from.pin}→${wire.to.pin}: не лёг на схему (${reason instanceof Error ? reason.message : "ошибка"}).`);
      }
    });
    return { next, failed, done, doubled };
  };
  let wired = wireUp(false);
  if (wired.failed.length) {
    const dropped = wireUp(true);
    if (dropped.failed.length < wired.failed.length) wired = dropped;
  }
  doc = wired.next;
  const wireIds = wired.done;
  const crimpNodes = wired.doubled;
  const crimpAt = new Set(crimpNodes.values());
  warnings.push(...wired.failed);

  // 4а. Сростки — узлы соединения схемы: провода с контактов и хвосты сходятся в узле. Узел редактора держится не меньше
  //     чем на двух проводах, а у контакта один вывод: первый провод сростка кладётся с контакта на конец второго, узел —
  //     на середину его самого длинного участка вместе с ветвью (третий провод, а в сростке из двух — второй), затем первый
  //     провод переводится концом в узел и в узел заводятся остальные. Хвост — провод из узла в свободный конец своей длины.
  const e4At = (endpoint: WireEndpoint) => wireEndpointE4Anchor(doc, endpoint)!.position;
  /** Свободный конец хвоста на схеме: в ту сторону, где до таблиц разъёмов дальше (колонка А слева, В справа), по
   *  ступеньке на хвост; хвост не длиннее половины просвета — подпись длины встаёт за ним. */
  const aRight = Math.max(0, ...columns[0].map((c) => connectorOf(c.key).positions.e4.x + connectorE4TableGeometry(connectorOf(c.key)).width));
  const bLeft = Math.min(...columns[1].map((c) => connectorOf(c.key).positions.e4.x), aRight + E4_GAP_X);
  const tailEnd = (from: Point, index: number) => {
    const right = bLeft - from.x >= from.x - aRight;
    const room = Math.max(24, (right ? bLeft - from.x : from.x - aRight) / 2);
    return createFreeEndpoint({ x: Math.round(from.x + (right ? 1 : -1) * Math.min(TAIL_E4, room)), y: Math.round(from.y + index * TAIL_E4_STEP) });
  };
  /** Провода сростков и хвостов → сечение (диаметр жилы в профиле зачистки их концов). */
  const stripped = new Map<string, string>();
  (plan.splices || []).forEach((sp, index) => {
    const tag = `gen-sp${index + 1}`;
    const contact = (w: PlanSpliceWire) => ({ connectorId: ids.get(w.end.connector)!, contactId: contactOf(w.end.connector, w.end.pin).id });
    const node = createJunctionEndpoint(tag);
    const sorted = [...sp.wires].sort((a, b) => (a.side === b.side ? 0 : a.side === "A" ? -1 : 1));
    const members: Array<{ wire: PlanSpliceWire; tail?: never } | { tail: PlanTail; wire?: never }> = [...sorted.map((wire) => ({ wire })), ...sp.tails.map((tail) => ({ tail }))];
    let tails = 0;
    const memberWire = (m: (typeof members)[number], id: string, at: Point) => {
      if (m.tail) return createWire(id, node, tailEnd(at, tails++), m.tail.lengthMm, m.tail.circuit, m.tail.color || "#334155");
      const w = m.wire!;
      return createWire(id, w.side === "A" ? contact(w) : node, w.side === "A" ? node : contact(w), w.lengthMm, w.circuit, w.color || "#334155", 0, 0, 1, w.color ? contact(w) : undefined);
    };
    const [m0, m1] = members;
    try {
      if (!m1 || !m0.wire) { warnings.push(`Сросток ${sp.label}: на контактах один провод без хвостов — на чертеже не показан.`); return; }
      const first = `${tag}-w1`;
      const start = contact(m0.wire);
      const through = m1.wire ? contact(m1.wire) : tailEnd(e4At(start), 0);
      let next = run(doc, { type: "add-wire", wire: createWire(first, start, through, m0.wire.lengthMm, m0.wire.circuit, m0.wire.color || "#334155", 0, 0, 1, m0.wire.color ? start : undefined) });
      const line = next.wires.find((w) => w.id === first)!;
      const pts = [wireEndpointE4Anchor(next, line.from)!.position, ...line.e4Route, wireEndpointE4Anchor(next, line.to)!.position];
      let best = 0;
      pts.slice(1).forEach((p, i) => { if (Math.hypot(p.x - pts[i].x, p.y - pts[i].y) > Math.hypot(pts[best + 1].x - pts[best].x, pts[best + 1].y - pts[best].y)) best = i; });
      const position = { x: (pts[best].x + pts[best + 1].x) / 2, y: (pts[best].y + pts[best + 1].y) / 2 };
      const branchAt = members.length > 2 ? 2 : 1;
      next = run(next, { type: "create-junction", junction: { id: tag, position, wireIds: [first, `${tag}-w${branchAt + 1}`] }, branchWire: memberWire(members[branchAt], `${tag}-w${branchAt + 1}`, position) });
      next = run(next, { type: "reconnect-wire", wireId: first, end: "to", endpoint: node });
      members.forEach((m, j) => { if (j && j !== branchAt) next = run(next, { type: "add-wire", wire: memberWire(m, `${tag}-w${j + 1}`, position) }); });
      doc = next;
      members.forEach((m, j) => stripped.set(`${tag}-w${j + 1}`, (m.wire ?? m.tail)!.section));
    } catch (reason) {
      warnings.push(`Сросток ${sp.label}: узел на схему не лёг (${reason instanceof Error ? reason.message : "ошибка"}) — дорисуйте его в редакторе.`);
    }
  });
  // Хвосты без сростка: провод с контакта в свободный конец — от разъёма в сторону другой колонки.
  (plan.tails || []).forEach((tail, index) => {
    const from = { connectorId: ids.get(tail.end.connector)!, contactId: contactOf(tail.end.connector, tail.end.pin).id };
    try {
      doc = run(doc, { type: "add-wire", wire: createWire(`gen-t${index + 1}`, from, tailEnd(e4At(from), 0), tail.lengthMm, tail.circuit,
        tail.color || "#334155", 0, 0, 1, tail.color ? from : undefined) });
      stripped.set(`gen-t${index + 1}`, tail.section);
    } catch (reason) {
      warnings.push(`${tail.mark || "Провод"} ${tail.end.pin}→хвост: не лёг на схему (${reason instanceof Error ? reason.message : "ошибка"}).`);
    }
  });

  // Зачистка концов на чертеже — та же, что у станка резки: конец в сростке (5 мм по умолчанию), хвост под лужение (3 мм).
  stripped.forEach((section, wireId) => {
    const wire = doc.wires.find((w) => w.id === wireId);
    if (!wire) return;
    (["from", "to"] as const).forEach((end) => {
      const at = wire[end];
      const [mm, title] = isJunctionEndpoint(at) && at.junctionId.startsWith("gen-sp") ? [plan.spliceStripMm, "Зачистка под сросток"]
        : isFreeEndpoint(at) ? [plan.tailStripMm, "Зачистка хвоста"] : [null, ""];
      if (mm) doc = run(doc, { type: "set-wire-strip-profile", wireId, end, profile: generatorStripProfile(mm, section, title) });
    });
  });

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
  //    Разъём с ТУТ на пайку: у каждого контакта с проводом свой выход и свой короткий отрезок до общего узла разъёма —
  //    на нём трубка этого провода (ТУТ надевают на каждый провод, а не одной трубкой на весь пучок).
  const nodes: PhysicalNode[] = [];
  const nodeOf = new Map<string, string>();
  const segments: PhysicalSegment[] = [];
  const routed = (from: string, to: string): PhysicalSegment => ({ id: `gen-s${segments.length + 1}`, from, to, path: { kind: "routed", points: [] } });
  /** Отрезки «провод у контакта» разъёмов с ТУТ: ключ разъёма плана → участок и номер контакта. */
  const stubs = new Map<string, Array<{ segmentId: string; pin: number }>>();
  /** Выход разъёма на чертеже: точка за корпусом против середины занятых контактов (с ТУТ — ещё на отрезок дальше). */
  const exitOf = (c: PlanConnector) => {
    const connector = connectorOf(c.key);
    const wiresAt = (contactId: string) => doc.wires.filter((wire) => [wire.from, wire.to].some((end) => end.connectorId === connector.id && end.contactId === contactId));
    const touched = connector.contacts.filter((contact) => wiresAt(contact.id).length > 0);
    const points = touched.flatMap((contact) => { const p = connectorContactPosition(connector, contact.id, "drawing"); return p ? [p] : []; });
    const centre = points.length ? points.reduce((sum, p) => sum + p.y, 0) / points.length : connector.positions.drawing.y + extent(c).height / 2;
    const free = connector.libraryBinding?.mode !== "template";
    const face = faceOf(connector);
    const out = face ? FACE_EXIT : EXIT;
    const x = c.side === "A"
      ? (free ? connector.positions.drawing.x + (face?.width ?? FREE_WIDTH) : Math.max(...points.map((p) => p.x))) + out
      : (free ? connector.positions.drawing.x : Math.min(...points.map((p) => p.x))) - out;
    const stub = (c.tutLengthMm !== undefined || crimpAt.has(c.key)) && touched.length ? (c.side === "A" ? TUT_STUB : -TUT_STUB) : 0;
    return { connector, wiresAt, touched, centre, free, x, common: { x: x + stub, y: centre } };
  };
  // Жгут с несколькими разъёмами А, сростками или хвостами — один ствол (drawingLayout.ts): колонка В встаёт за концом
  // ствола, веером вокруг его линии (один разъём В — прямо на линии ствола).
  const aSide = sideOf("A"), bSide = sideOf("B");
  // Зоны сростков: узлы схемы, которые физически лежат вместе (plan.zones) — одна зона на стволе вместо цепочки узлов.
  const spliceTag = new Map((plan.splices || []).map((sp, i) => [sp.key, `gen-sp${i + 1}`]));
  const zonePlan = (plan.zones || []).map((keys, index) => ({ index, keys, junctions: keys.map((k) => spliceTag.get(k)!).filter((id) => doc.junctions.some((j) => j.id === id) && !crimpNodes.has(id)) }))
    .filter((zone) => zone.junctions.length > 0);
  const zoneJunctions = zonePlan.map((zone) => zone.junctions);
  const zonedJunctions = new Set(zoneJunctions.flat());
  const parts: TrunkParts = { aBranches: [], bBranches: [], zones: [] };
  const trunked = aSide.length > 1 || doc.junctions.length > 0 || doc.wires.some((wire) => isFreeEndpoint(wire.from) || isFreeEndpoint(wire.to));
  let geometry: TrunkGeometry | null = null;
  if (trunked) {
    const trunk = trunkGeometry(columns[0].map((c) => exitOf(c).common), doc.junctions.filter((j) => !crimpNodes.has(j.id) && !zonedJunctions.has(j.id)).length + zoneJunctions.length * 1.5);
    geometry = trunk;
    const column = columns[1], boxes = column.map(extent);
    let y = Math.max(DRAWING_TOP, trunk.trunkY - drawingTotal(column) / 2);
    column.forEach((c, i) => {
      doc = run(doc, { type: "move-connector", connectorId: ids.get(c.key)!, view: "drawing", position: { x: DRAWING_B_X, y: Math.round(y - boxes[i].top) } });
      y += boxes[i].height + DRAWING_GAP_Y;
    });
    const exits = column.map((c) => exitOf(c).common);
    const targetX = column.length > 1 ? trunk.endX + fanWidth(exits.map((p) => p.y - trunk.trunkY)) : trunk.endX;
    column.forEach((c, i) => {
      const at = connectorOf(c.key).positions.drawing;
      doc = run(doc, { type: "move-connector", connectorId: ids.get(c.key)!, view: "drawing",
        position: { x: Math.round(at.x + targetX - exits[i].x), y: Math.round(at.y + (column.length === 1 ? trunk.trunkY - exits[i].y : 0)) } });
    });
  }
  used.forEach((c, index) => {
    const { connector, wiresAt, touched, centre, free, x } = exitOf(c);
    const id = `gen-n${index + 1}`;
    const exit = (nodeId: string, world: Point, wireIds?: string[]): PhysicalNode => {
      const draft: PhysicalNode = { id: nodeId, connectorId: connector.id, position: { x: 0, y: 0 }, ...(wireIds ? { wireIds } : {}) };
      return { ...draft, position: physicalNodeLocalPoint(doc, draft, world), ...(free ? { direction: c.side === "A" ? "right" as const : "left" as const } : {}) };
    };
    nodeOf.set(c.key, id);
    // Разъём с двойным обжимом — как с ТУТ: у каждого контакта свой выход до общего узла; второй провод приходит в общий.
    if ((c.tutLengthMm === undefined && !crimpAt.has(c.key)) || !touched.length) {
      nodes.push(exit(id, { x, y: centre }));
      return;
    }
    nodes.push({ id, position: { x: Math.round(x + (c.side === "A" ? TUT_STUB : -TUT_STUB)), y: Math.round(centre) } });
    const own: Array<{ segmentId: string; pin: number }> = [];
    touched.forEach((contact, j) => {
      const nodeId = `${id}-${j + 1}`;
      const y = connectorContactPosition(connector, contact.id, "drawing")?.y ?? centre;
      nodes.push(exit(nodeId, { x, y }, wiresAt(contact.id).map((wire) => wire.id)));
      const segment = routed(nodeId, id);
      segments.push(segment);
      own.push({ segmentId: segment.id, pin: contact.number });
    });
    stubs.set(c.key, own);
  });
  const stubIds = new Set([...stubs.values()].flat().map((stub) => stub.segmentId));
  const sideOfId = (connectorId: string) => used.find((c) => ids.get(c.key) === connectorId)?.side ?? null;
  let spliceRoutes: PhysicalRoute[] = [];
  if (geometry) {
    spliceRoutes = trunkTopology({ doc, nodes, segments, geometry, sideOf: sideOfId,
      atConnector: new Map([...crimpNodes].map(([junction, key]) => [junction, nodeOf.get(key)!])),
      aExits: columns[0].map((c) => nodeOf.get(c.key)!), bExits: columns[1].map((c) => nodeOf.get(c.key)!), zones: zoneJunctions, out: parts });
  } else if (aSide.length === 1 && bSide.length === 1) {
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
  /** Ствол и ветки — без отрезков провода у контакта. */
  const main = segments.filter((segment) => !stubIds.has(segment.id));
  let topology: PhysicalTopology = { snap: true, nodes, segments, routes: spliceRoutes };
  topology = routePhysicalWires({ ...doc, physicalTopology: topology }, topology);
  // Участок, по которому идут все жилы кабеля и только они, — кабель в оболочке (ствол до разделки); ветки к разным
  // разъёмам после узла — жилы россыпью (кабель разделан, жилы разведены по разъёмам).
  // Канал участка (серый «пайп» редактора) — это оболочка: рисуем её только у кабеля в оболочке. Без кабеля жилы идут
  // голыми проводами своих цветов (свитые — свитыми), оплётка, лента и термоусадка рисуются своими отрезками ниже
  // (владелец 14.10: «тут же нет оплётки, почему на чертеже она есть»).
  {
    const through = (segmentId: string) => new Set(topology.routes.filter((route) => route.steps.some((step) => step.segmentId === segmentId)).map((route) => route.wireId));
    topology = { ...topology, segments: topology.segments.map((segment) => {
      if (stubIds.has(segment.id)) return { ...segment, opacity: 0 };
      const inside = through(segment.id);
      const cable = [...cableMembers].find(([, members]) => members.length === inside.size && members.every((id) => inside.has(id)));
      return cable ? { ...segment, specificationItemId: cable[0], mode: "round" as const, color: CABLE_JACKET } : { ...segment, opacity: 0 };
    }) };
  }
  doc = run(doc, { type: "set-physical-topology", topology });
  const unrouted = doc.wires.filter((wire) => !topology.routes.some((route) => route.wireId === wire.id)).length;
  if (unrouted) warnings.push(`${unrouted} провод(а) без трассы на чертеже — проложите их в редакторе.`);

  // 7. Оболочки: ТУТ на пайку — трубка на каждый провод у своего контакта; общая трубка — на зону сростков; трубка, маркировка
  //    и оплётка ветви — на ветви своего разъёма А (маркировка у разъёма, за ней трубка, оплётка поверх); отрезки
  //    термоусадки на жгут — у своего конца поверх пучка; оплётка/лента на жгут — по всему жгуту.
  const coverings: PhysicalCovering[] = [];
  const branchSegment = (key: string) => {
    const node = nodeOf.get(key);
    return main.find((s) => s.from === node || s.to === node) ?? null;
  };
  const spanOf = (key: string, from: number, to: number) => {
    const segment = branchSegment(key);
    if (!segment) return null;
    return segment.from === nodeOf.get(key) ? { segmentId: segment.id, from, to } : { segmentId: segment.id, from: 1 - to, to: 1 - from };
  };
  const endSpan = (key: string, share: number) => spanOf(key, 0, share);
  /** Выноски поверх чертежа: текст → участок и доля его длины, где лежит оболочка. */
  const callouts: Array<{ text: string; label: string; segmentId: string; at: number }> = [];
  const tubeShort = (name: string) => { const m = /^(.*?\d+(?:[.,]\d+)?\s*\/\s*\d+(?:[.,]\d+)?)/.exec(name); return (m ? m[1] : name).replace(/\s+/g, " ").trim(); };
  const mmText = (mm: number | null) => (mm === null ? "" : `, ${String(mm).replace(".", ",")} мм`);
  const stacked = new Map<string, number>();
  plan.coverings.forEach((covering) => {
    const length = covering.lengthMm === null ? { lengthMode: "auto" as const, lengthMm: null } : { lengthMode: "manual" as const, lengthMm: covering.lengthMm };
    const common = () => ({ id: `gen-c${coverings.length + 1}`, name: covering.name, kind: covering.coveringKind, width: 0, color: COVER_COLOR[covering.coveringKind], ...length });
    if (covering.kind === "wrap") {
      coverings.push({ ...common(), spans: main.map((s) => ({ segmentId: s.id, from: 0, to: 1 })) });
      return;
    }
    if (covering.kind === "zone") {
      const zone = parts.zones.find((z) => z.index === zonePlan.findIndex((p) => p.index === covering.zone));
      if (!zone) return;
      coverings.push({ ...common(), spans: [{ segmentId: zone.segmentId, from: 0, to: 1 }] });
      callouts.push({ text: `Трубка ${tubeShort(covering.name)}${mmText(covering.lengthMm)}`, label: `Трубка ${tubeShort(covering.name)}`, segmentId: zone.segmentId, at: 0.5 });
      return;
    }
    if (!covering.at || !ids.has(covering.at)) return;
    const own = covering.kind === "tut" ? stubs.get(covering.at) : undefined;
    if (own) {
      own.forEach((stub) => coverings.push({ ...common(), name: `${covering.name} · конт. ${stub.pin}`, spans: [{ segmentId: stub.segmentId, from: 0.1, to: 0.9 }] }));
      return;
    }
    const layer = stacked.get(covering.at) ?? 0;
    stacked.set(covering.at, layer + 1);
    const span = endSpan(covering.at, Math.min(0.45, 0.14 + layer * 0.06));
    if (span) coverings.push({ ...common(), spans: [span] });
  });
  used.forEach((c) => {
    const lay = (name: string, kind: CoveringKind, lengthMm: number | null, from: number, to: number, label: string) => {
      const span = spanOf(c.key, from, to);
      if (!span) return;
      const length = lengthMm === null ? { lengthMode: "auto" as const, lengthMm: null } : { lengthMode: "manual" as const, lengthMm };
      coverings.push({ id: `gen-c${coverings.length + 1}`, name: `${name} · ${c.designation}`, kind, width: 0, color: COVER_COLOR[kind], ...length, spans: [span] });
      callouts.push({ text: `${label}${lengthMm === null || label.startsWith("Маркировка") ? "" : mmText(lengthMm)}`, label, segmentId: span.segmentId, at: (span.from + span.to) / 2 });
    };
    const marks = c.marks ?? [];
    marks.forEach((mark, i) => lay(mark.name || "Маркировка", "heat-shrink", mark.lengthMm, 0.03 + i * 0.07, 0.09 + i * 0.07, `Маркировка «${mark.text}»`));
    if (c.shrink) lay(c.shrink.name, "heat-shrink", c.shrink.lengthMm, 0.11 + marks.length * 0.07, 0.3 + marks.length * 0.07, `Трубка ${tubeShort(c.shrink.name)}`);
    if (c.braid) lay(c.braid.name, "braid", c.braid.lengthMm, 0.14, 0.97, `Оплётка ${c.braid.name}`);
  });
  if (coverings.length) doc = run(doc, { type: "set-physical-topology", topology: { ...doc.physicalTopology!, coverings } });

  // Жилы без оболочки — голые цветные провода: без объёмной подсветки (она сереет цвета) и, если есть свивка, покрупнее,
  // чтобы цвета пар не тонули в подложке соседней пары.
  const bare = !doc.physicalTopology!.segments.some((segment) => segment.specificationItemId);
  const strands = bare ? { volumeShading: false, ...(plan.pairs.length ? { physicalScale: 1.8 } : {}) } : {};

  // 8. Размеры. Зона сростков (СБ владельца): размеры от центра ветвления — ветвь А, зона, ветви к В и жгут свободных
  //    концов; длины — из генератора (длины резки проводов зоны, длина общей трубки), провод доходит до середины зоны.
  //    Иначе — один участок А → В и одна длина у всех проводов: размер на участке (длины проводов идут от него).
  const lengths = [...new Set(doc.wires.map((wire) => wire.lengthMm))];
  const trunk = main.length === 1 ? doc.physicalTopology!.segments.find((segment) => segment.id === main[0].id) : undefined;
  // Пустые документы чертежа: без них редактор рисует «старые» размеры каждого провода косыми линиями через поле.
  let documents: DrawingDocuments = { tables: [], leaders: [], bomOrder: [], ...strands };
  if (zonePlan.length && parts.zones.length) {
    documents = { ...documents, ...zoneAnnotations({ doc, plan, zones: zonePlan, parts, trunkY: geometry!.trunkY, callouts, depthMm: (c) => { const src = sources[c.key]; return src?.kind === "free" ? src.housing?.body?.len ?? null : null; },
      connectorAt: (exitNode) => used.find((c) => nodeOf.get(c.key) === exitNode) }) };
  } else if (trunk && lengths.length === 1 && lengths[0] !== null) {
    const dimension: DrawingDimension = {
      id: "gen-d1", segmentId: trunk.id, from: 0, to: trunk.path.points.length + 1, pointCount: trunk.path.points.length + 2,
      routeKey: segmentDimensionKey(doc, trunk.id), mode: "aligned", modeOverride: false, offset: 40, lengthMm: lengths[0],
    };
    documents = { ...documents, showDimensions: true, dimensions: [dimension] };
  } else if (main.length > 1 && lengths.some((length) => length !== null)) {
    warnings.push("Длины ствола и веток генератор не знает — размеры на чертеже не проставлены, длины проводов перенесены.");
  }
  doc = run(doc, { type: "set-drawing-documents", documents });

  const twisted = withTwistNotes(doc, plan);
  doc = twisted.document;
  warnings.push(...twisted.warnings);
  // Подписи проводов и разъёмов — без наложений (и у прямого жгута, где провода идут одной трассой).
  doc = placeDrawingLabels(doc, sideOfId);

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
