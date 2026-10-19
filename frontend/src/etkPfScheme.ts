// Картинки ПФ «Сложной сборки» на размер карточки схемы ПФ (владелец 18.10: «визуал последних ПФ тоже слабый»):
// сростки — по эскизу на каждый, трубка ТУТ на жгуте в месте Б — ветви сходятся под одну трубку, изделие — плоский обзор
// жгута (разъёмы с обозначениями, ветви, зона сростков, трубки). Рисуются в масштабе 1:1 карточки (~320 px шириной):
// подписи не мельче 10 px. Данные — из конфигурации сборки: `build_config.ops` (разъёмы, провода) и `build_config.gen_state`
// (снимок формы генератора: какие провода в каком сростке, хвосты, трубки). Без снимка — эскиз по тому, что есть.
import { connectorTopView } from "./connectorTopView";
import { etkWireColor, type EtkHousing } from "./etkDraw";
import { coreColor } from "./pinoutCheck";
import { parseCorePins_ } from "./tcGenLogic";
import { setCustomColors } from "./wireColors";

type Row = Record<string, unknown>;
export type Drawing = { width: number; height: number; body: string };

const INK = "#17272b";
const MUTED = "#5f7470";
const TEAL = "#00706a";
const TUBE = "#2f3b40";
const COPPER = "#c88954";

function obj(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}
function rows(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") as Row[] : [];
}
function str(value: unknown): string {
  return value == null ? "" : String(value);
}
function num(value: unknown): number {
  const parsed = Number(str(value).replace(",", "."));
  return Number.isFinite(parsed) ? parsed : 0;
}
function esc(value: unknown): string {
  return str(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}
function cut(text: string, size: number): string {
  return text.length > size ? `${text.slice(0, size - 1)}…` : text;
}
const f = (value: number) => value.toFixed(1);

/** «ТТК 3/1мм, черная» → «ТТК 3/1»; без размера — первое слово названия, до запятой. */
export function tubeShort(text: string): string {
  const match = text.match(/(ТТК|ТУТ)\s*(\d+(?:[.,]\d+)?)\s*\/\s*(\d+(?:[.,]\d+)?)/i);
  return match ? `${match[1].toUpperCase()} ${match[2]}/${match[3]}` : cut(text.split(/,\s+/)[0].trim(), 14);
}

/** «4 провода», «1 провод», «5 проводов». */
export function wiresWord(count: number): string {
  const tail = count % 100, last = count % 10;
  return `${count} ${tail > 10 && tail < 20 ? "проводов" : last === 1 ? "провод" : last >= 2 && last <= 4 ? "провода" : "проводов"}`;
}

const text = (x: number, y: number, value: string, size = 11, weight = 600, color = INK, anchor = "middle") =>
  `<text x="${f(x)}" y="${f(y)}" text-anchor="${anchor}" font-size="${size}" font-weight="${weight}" fill="${color}">${esc(value)}</text>`;

/** Пучок жил кривой от (x1,y1) к (x2,y2): тёмная подложка и по полоске на жилу в её цвете. */
function strands(x1: number, y1: number, x2: number, y2: number, colors: string[], k = 2.6): string {
  if (!colors.length) return "";
  const mx = (x1 + x2) / 2;
  const line = (shift: number) => `M${f(x1)},${f(y1 + shift)} C${f(mx)},${f(y1 + shift)} ${f(mx)},${f(y2 + shift)} ${f(x2)},${f(y2 + shift)}`;
  let body = `<path d="${line(0)}" fill="none" stroke="${INK}" stroke-width="${f(colors.length * k + 1.6)}" stroke-linecap="round"/>`;
  colors.forEach((color, index) => {
    body += `<path d="${line((index - (colors.length - 1) / 2) * k)}" fill="none" stroke="${color}" stroke-width="${f(k - 0.4)}"/>`;
  });
  return body;
}

/** Разъём плоским контуром: обозначение внутри, замок сверху (по нему видно, где верх). */
function connectorBox(x: number, y: number, w: number, h: number, label: string): string {
  return `<rect data-part="connector" x="${f(x)}" y="${f(y)}" width="${w}" height="${h}" rx="4" fill="#f3f8f7" stroke="${TEAL}" stroke-width="1.3"/>` +
    `<rect x="${f(x + w / 2 - 5)}" y="${f(y - 3)}" width="10" height="4" rx="1.5" fill="#f3f8f7" stroke="${TEAL}" stroke-width="1.1"/>` +
    (label ? text(x + w / 2, y + h / 2 + 4, cut(label, 5), 11, 800, INK) : "");
}

/** Разъём видом сверху — как на ПФ 2 (pfBranchSvg): модель корпуса `connectorTopView`, развёрнутая так, чтобы выход проводов
 *  смотрел на жгут. Сторона А — слева от жгута: провода уходят вправо (та же развёртка, что на ПФ 2); сторона Б — справа,
 *  провода влево (зеркально по вертикали). `edge` — внешний край разъёма (левый у А, правый у Б), `cy` — середина по высоте.
 *  Корпуса нет или вида сверху у серии нет — null. `wireX` — где провода выходят, `pinY(pin)` — высота контакта. */
export type TopConnector = { svg: string; wireX: number; top: number; height: number; pinY: (pin: number) => number };
export function topConnector(housing: EtkHousing | null | undefined, byPin: Record<number, string>, side: "A" | "B",
  edge: number, cy: number, maxDepth: number, maxWidth: number): TopConnector | null {
  const view = housing ? connectorTopView(housing, maxWidth, maxDepth, byPin) : null;
  if (!housing || !view) return null;
  const fy = cy - view.w / 2, x0 = side === "A" ? edge : edge - view.h;
  const matrix = side === "A" ? `0,1,1,0,${f(x0)},${f(fy)}` : `0,1,-1,0,${f(x0 + view.h)},${f(fy)}`;
  const columns = housing.grid.map((row) => row.length), cols = Math.max(1, housing.cols, ...columns);
  const pinY = (pin: number) => {
    const col = housing.grid.map((row) => row.indexOf(pin)).find((index) => index >= 0);
    return col === undefined ? cy : fy + ((col + 0.5) / cols) * view.w;
  };
  return { svg: `<g data-part="connector" data-view="top" data-side="${side}" transform="matrix(${matrix})">${view.body}</g>`,
    wireX: side === "A" ? x0 + view.h : x0, top: fy, height: view.w, pinY };
}

// ── Модель жгута из снимка генератора ──

type Target = { kind: "conn"; label: string } | { kind: "splice"; id: string } | { kind: "tail" };
export type HarnessModel = {
  /** `cores` — цвета жил многожильного кабеля (hex, по порядку контактов); пусто — провод одного цвета. */
  wires: Array<{ name: string; color: string; length: number; cores: string[] }>;
  /** Что на конце провода: `endA[idx]` — на стороне А, `endB[idx]` — на стороне Б. */
  endA: Record<number, Target>; endB: Record<number, Target>;
  conns: Array<{ label: string; side: "A" | "B"; wires: number[]; braidLen: string; tube: string; tubeLen: string;
    /** Контакт каждого провода (по номеру провода) и цвет по контакту — для вида сверху; корпус — из операции монтажа. */
    pins: Record<number, number>; byPin: Record<number, string>; housing: EtkHousing | null;
    /** Все контакты провода (у многожильного кабеля — по жиле на контакт). */
    pinList?: Record<number, number[]> }>;
  splices: Array<{ id: string; name: string; ends: Array<{ idx: number; side: "A" | "B" }> }>;
  braid: string; spliceTube: string; spliceTubeLen: string; harnessTube: string; harnessTubeLen: string;
};

/** Есть ли на обзоре жгута что соединить: провод, у которого известны оба конца (разъём или сросток с каждой стороны). */
export function overviewWired(model: HarnessModel): boolean {
  return model.wires.some((wire, idx) => {
    const a = model.endA[idx], b = model.endB[idx];
    return Boolean(wire && a && b && a.kind !== "tail" && b.kind !== "tail");
  });
}

/** Модель жгута по `build_config.gen_state`; нет снимка формы (или в нём нет проводов и разъёмов) — null. */
export function harnessModel(config: Row, housingOf: (label: string) => EtkHousing | null = () => null): HarnessModel | null {
  const gen = obj(config.gen_state), S = obj(gen.S);
  const wireRows = rows(S.wires), endpoints = rows(S.endpoints);
  if (!wireRows.length || !endpoints.length) return null;
  const kit = new Map(rows(gen.kit).map((item) => [str(item.id), str(item.key).split("|").slice(1).join("|")]));
  const nameOf = (id: unknown) => kit.get(str(id)) ?? "";
  const wires: HarnessModel["wires"] = [];
  setCustomColors(config.colorHex as Record<string, string> | undefined);
  wireRows.forEach((wire) => {
    const name = nameOf(wire.compId), color = etkWireColor(name);
    const own = (Array.isArray(wire.coreColors) ? wire.coreColors : []).map((item) => coreColor(str(item) || undefined)?.hex ?? "");
    wires[Math.round(num(wire.idx))] = { name, color, length: num(wire.length), cores: own.some(Boolean) ? own.map((hex) => hex || color) : [] };
  });
  const endA: HarnessModel["endA"] = {}, endB: HarnessModel["endB"] = {};
  const put = (side: unknown, idx: unknown, target: Target) => { (str(side) === "A" ? endA : endB)[Math.round(num(idx))] = target; };
  const sideCount = { A: 0, B: 0 };
  const conns = endpoints.map((ep) => {
    // Простая сборка (генератор без обозначений): «X1» по порядку стороны; провода — по распиновке, если списка нет.
    const sideKey = str(ep.side) === "A" ? "A" : "B";
    sideCount[sideKey] += 1;
    const label = str(ep.label).trim() || `X${sideKey === "A" ? sideCount.A : endpoints.filter((item) => str(item.side) === "A").length + sideCount.B}`;
    const given = (Array.isArray(ep.wireIdxs) ? ep.wireIdxs : []).map((idx) => Math.round(num(idx)));
    const list = given.length ? given : Object.keys(obj(ep.pins)).map((idx) => Math.round(num(idx)));
    list.forEach((idx) => put(ep.side, idx, { kind: "conn", label }));
    const shrink = ep.shrinkOn ? nameOf(ep.shrinkMat) : "";
    const pins: Record<number, number> = {}, byPin: Record<number, string> = {}, pinList: Record<number, number[]> = {};
    Object.entries(obj(ep.pins)).forEach(([idx, pin]) => {
      const at = Math.round(num(idx)), wire = wires[at];
      parseCorePins_(pin).forEach((number, k) => {
        if (!number || number <= 0) return;
        pins[at] ??= number;
        (pinList[at] ||= []).push(number);
        byPin[number] = wire?.cores[k] || wire?.name || "";
      });
    });
    return { label, side: (str(ep.side) === "A" ? "A" : "B") as "A" | "B", wires: list, braidLen: str(ep.braidLen).trim(),
      tube: shrink ? tubeShort(shrink) : "", tubeLen: shrink ? str(ep.shrinkLen).trim() : "", pins, byPin, pinList, housing: housingOf(label) };
  });
  const splices = rows(S.splices).map((splice, index) => {
    const ends = rows(splice.ends).map((end) => ({ idx: Math.round(num(end.idx)), side: (str(end.side) === "A" ? "A" : "B") as "A" | "B" }));
    ends.forEach((end) => put(end.side, end.idx, { kind: "splice", id: str(splice.id) }));
    return { id: str(splice.id), name: `С${index + 1}`, ends };
  });
  rows(S.tails).forEach((tail) => put(tail.side, tail.idx, { kind: "tail" }));
  const spl = obj(S.spl), hs = obj(S.hs), hsRow = rows(hs.rows)[0];
  const braidSlot = rows(obj(S.wrap).slots).find((slot) => str(slot.kind) === "braid" && str(slot.mat));
  const braid = braidSlot ? nameOf(braidSlot.mat) : "";
  return {
    wires, endA, endB, conns, splices,
    braid: /^[a-z0-9-]+$/i.test(braid) ? braid.toUpperCase() : braid,
    spliceTube: spl.enabled !== false && nameOf(spl.tutMat) ? tubeShort(nameOf(spl.tutMat)) : "",
    spliceTubeLen: str(spl.tut).trim(),
    harnessTube: hs.enabled !== false && hsRow && nameOf(hsRow.compId) ? tubeShort(nameOf(hsRow.compId)) : "",
    harnessTubeLen: hsRow ? str(hsRow.len).trim() : "",
  };
}

// ── ПФ «Пайка С…»: эскиз на каждый сросток ──

export type SpliceEnd = { color: string; dest: string; tail: boolean };
/** Один сросток: провода слева (пришли от разъёмов А), справа (уходят к Б и хвосты), длины хвостов в мм. */
export type SpliceSketch = { name: string; left: SpliceEnd[]; right: SpliceEnd[]; tails: number[] };

/** Эскизы сростков по именам «С1», «С2» из снимка генератора: какие провода сошлись, куда идёт другой их конец. */
export function spliceSketches(model: HarnessModel, names: string[]): SpliceSketch[] {
  return names.flatMap((name) => {
    const splice = model.splices.find((item) => item.name === name);
    if (!splice) return [];
    const sketch: SpliceSketch = { name, left: [], right: [], tails: [] };
    splice.ends.forEach((end) => {
      // Конец провода в сростке на стороне Б — провод пришёл от А (слева), на стороне А — уходит к Б (справа).
      const far = end.side === "B" ? model.endA[end.idx] : model.endB[end.idx];
      const wire = model.wires[end.idx], color = wire?.color ?? etkWireColor("");
      const tail = far?.kind === "tail";
      const dest = far?.kind === "conn" ? far.label : far?.kind === "splice" ? model.splices.find((item) => item.id === far.id)?.name ?? "" : "";
      if (tail && wire?.length) sketch.tails.push(Math.round(wire.length));
      (end.side === "B" ? sketch.left : sketch.right).push({ color, dest, tail });
    });
    return [sketch];
  });
}

/** Эскиз сростков без снимка формы: `count` сростков по `size` проводов цветами входа, половина слева, половина справа. */
export function spliceSketchesPlain(names: string[], size: number, colors: string[]): SpliceSketch[] {
  const palette = colors.length ? colors : ["#2b2b2b", "#d7322d"];
  const count = Math.max(1, size);
  const ends = Array.from({ length: count }, (_, index): SpliceEnd => ({ color: palette[index % palette.length], dest: "", tail: false }));
  const half = Math.ceil(count / 2);
  return names.map((name) => ({ name, left: ends.slice(0, half), right: ends.slice(half), tails: [] }));
}

const SW = 160, SH = 94;

function spliceOne(s: SpliceSketch, ox: number, oy: number, tube: string): string {
  const cy = oy + 46, nl = Math.max(1, s.left.length), nr = Math.max(1, s.right.length);
  const gap = Math.max(nl, nr) > 4 ? 8 : 11, span = (Math.max(nl, nr) - 1) * gap;
  const total = s.left.length + s.right.length;
  const px = ox + 80;
  let body = text(ox + 4, oy + 14, `${s.name} · ${wiresWord(total)}`, 11, 700, INK, "start");
  const wire = (d: string, color: string) => `<path d="${d}" fill="none" stroke="${INK}" stroke-width="6.4" stroke-linecap="round"/><path d="${d}" fill="none" stroke="${color}" stroke-width="4.6" stroke-linecap="round"/>`;
  s.left.forEach((end, index) => {
    const y = cy + (index - (s.left.length - 1) / 2) * gap;
    body += wire(`M${f(ox + 32)},${f(y)} C${f(ox + 54)},${f(y)} ${f(px - 26)},${f(cy)} ${f(px)},${f(cy)}`, end.color);
    if (end.dest) body += text(ox + 28, y + 3.5, cut(end.dest, 4), 10, 600, MUTED, "end");
  });
  s.right.forEach((end, index) => {
    const y = cy + (index - (s.right.length - 1) / 2) * gap;
    body += wire(`M${f(px)},${f(cy)} C${f(px + 26)},${f(cy)} ${f(ox + 104)},${f(y)} ${f(ox + 120)},${f(y)}`, end.color);
    if (end.tail) body += `<circle cx="${f(ox + 120)}" cy="${f(y)}" r="2.8" fill="#d8dde0" stroke="${INK}" stroke-width=".8"/>`;
    const caption = end.tail ? "хвост" : end.dest;
    if (caption) body += text(ox + 125, y + 3.5, cut(caption, 5), 10, 600, MUTED, "start");
  });
  // Спай и трубка поверх: прозрачная, провода и пятно припоя видны под ней.
  const th = span + 18;
  body += `<ellipse cx="${f(px)}" cy="${f(cy)}" rx="9" ry="6" fill="${COPPER}" stroke="${INK}" stroke-width=".8"/>` +
    `<rect data-part="splice-tube" x="${f(ox + 56)}" y="${f(cy - th / 2)}" width="48" height="${f(th)}" rx="9" fill="${TUBE}" fill-opacity=".74" stroke="${INK}" stroke-width="1.3"/>`;
  if (tube) body += text(ox + SW / 2, oy + 80, tube, 10, 700, INK);
  if (s.tails.length) body += text(ox + SW / 2, oy + 92, `${s.tails.length > 1 ? "хвосты" : "хвост"} ${[...new Set(s.tails)].join(", ")} мм`, 10, 400, MUTED);
  return body;
}

/** ПФ «Пайка С…»: по эскизу на сросток рядом (до четырёх): провода своих цветов сходятся в спай, поверх — трубка с её
 *  размером и длиной, число проводов в заголовке, у концов проводов — куда они идут, свободные — «хвост». */
export function spliceDrawing(sketches: SpliceSketch[], tube: string): Drawing {
  const shown = sketches.slice(0, 4), cols = Math.min(2, shown.length), lines = Math.ceil(shown.length / 2);
  const width = SW * Math.max(2, cols), left = (width - cols * SW) / 2;
  let body = "";
  shown.forEach((sketch, index) => {
    const ox = left + (index % 2) * SW, oy = Math.floor(index / 2) * SH;
    body += spliceOne(sketch, ox, oy, tube);
    if (index % 2 === 1 || (cols === 2 && index === 0)) body += `<line x1="${SW}" y1="${oy + 8}" x2="${SW}" y2="${oy + SH - 8}" stroke="#dbe7e5"/>`;
  });
  if (sketches.length > shown.length) body += text(width - 4, lines * SH - 2, `ещё ${sketches.length - shown.length}`, 10, 600, MUTED, "end");
  return { width, height: lines * SH, body };
}

// ── ПФ «ТУТ на месте Б»: ветви сходятся под одну трубку ──

/** Ветвь на рисунке: обозначение разъёма, его провода (цвет и контакт), корпус — как в операции монтажа. */
export type TubeBranch = { label: string; wires: Array<{ color: string; pin: number }>; housing?: EtkHousing | null; byPin?: Record<number, string> };

/** Обозначение рядом с разъёмом: у А — слева от него, у Б — справа. */
const designator = (side: "A" | "B", x: number, cy: number, label: string) =>
  text(x, cy + 4, cut(label, 4), 11, 800, INK, side === "A" ? "start" : "end");

/** Ветви слева (разъёмы стороны А видом сверху, провода выходят вправо) сходятся пучком под одну трубку на жгуте; справа
 *  пучок расходится к разъёмам стороны Б (провода выходят влево). Каждый провод — от своего контакта своим цветом.
 *  На трубке — материал и длина; обозначения — рядом с разъёмами. */
export function harnessTubeDrawing(left: TubeBranch[], right: TubeBranch[], tube: string, len: string): Drawing {
  const rowH = 30, rowsCount = Math.max(left.length, right.length, 3), W = 320, bodyH = rowsCount * rowH + 6, cy = 3 + bodyH / 2;
  let body = "";
  const side = (list: TubeBranch[], kind: "A" | "B") => {
    const mirror = kind === "B", inner = mirror ? 208 : 116, total = list.reduce((n, item) => n + item.wires.length, 0);
    let used = 0;
    list.forEach((item, index) => {
      const y = 3 + (bodyH * (index + 0.5)) / list.length;
      const conn = topConnector(item.housing, item.byPin ?? {}, kind, mirror ? W - 24 : 24, y, 36, 26);
      const exit = conn ? conn.wireX : mirror ? W - 24 - 34 : 24 + 34;
      body += conn ? conn.svg : connectorBox(mirror ? W - 24 - 34 : 24, y - 10, 34, 20, "");
      body += designator(kind, mirror ? W - 1 : 1, y, item.label);
      const wires = [...item.wires].sort((a, b) => a.pin - b.pin);
      wires.forEach((wire, k) => {
        const from = conn && wire.pin > 0 ? conn.pinY(wire.pin) : y + (k - (wires.length - 1) / 2) * 3;
        const to = cy + (used + k - (total - 1) / 2) * 2.6;
        body += strands(exit + (mirror ? -1 : 1), from, inner, to, [wire.color], 2.2);
      });
      used += wires.length;
    });
  };
  side(left, "A");
  side(right, "B");
  body += `<rect data-part="harness-tube" x="116" y="${f(cy - 18)}" width="92" height="36" rx="11" fill="${TUBE}" fill-opacity=".94" stroke="${INK}" stroke-width="1.3"/>` +
    text(162, cy - 2, tube || "Термоусадка", 12, 800, "#fff") + (len ? text(162, cy + 12, `${len} мм`, 11, 600, "#e4ecea") : "");
  return { width: W, height: Math.round(bodyH + 8), body };
}

// ── ПФ «изделие»: плоский обзор жгута ──

/** Плоский обзор жгута: разъёмы А слева (оплётка и трубка у разъёма), провода своих цветов, посередине — зона сростков под
 *  одной трубкой на жгуте (каждый сросток под своей), справа разъёмы Б; хвосты — короткими отростками. */
export function harnessOverview(model: HarnessModel): Drawing {
  const W = 320, top = 26, rowH = 30;
  const left = model.conns.filter((conn) => conn.side === "A"), right = model.conns.filter((conn) => conn.side === "B");
  const lines = Math.max(left.length, model.splices.length, right.length, 1);
  const spread = (count: number, index: number) => top + (lines * rowH * (index + 0.5)) / count;
  type Place = { x: number; y: number; conn?: TopConnector | null; pins?: Record<number, number[]> };
  const at: Record<string, Place> = {}, hasLeaf: Record<string, boolean> = {};
  const key = (target: Target) => (target.kind === "conn" ? `c:${target.label}` : target.kind === "splice" ? `s:${target.id}` : "t");
  let body = "", back = "", wiresBody = "";
  // Разъёмы видом сверху, как на ПФ 2: А слева (провода вправо, оплётка и трубка у разъёма), Б справа (провода влево);
  // обозначение рядом; нет корпуса — плоский контур.
  left.forEach((conn, index) => {
    const y = spread(left.length, index), tc = topConnector(conn.housing, conn.byPin, "A", 24, y, 34, 26);
    const exit = tc ? tc.wireX : 24 + 32;
    body += (tc ? tc.svg : connectorBox(24, y - 11, 32, 22, "")) + designator("A", 1, y, conn.label);
    if (conn.braidLen) body += `<rect data-part="braid" x="${f(exit + 1)}" y="${f(y - 6)}" width="16" height="12" rx="4" fill="url(#pfBraid)" stroke="${INK}" stroke-width=".9"/>`;
    if (conn.tube) body += `<rect data-part="branch-tube" x="${f(exit + 1)}" y="${f(y - 7)}" width="8" height="14" rx="3" fill="${TUBE}" stroke="${INK}" stroke-width=".9"/>`;
    at[`c:${conn.label}`] = { x: exit + (conn.braidLen ? 18 : 3), y, conn: tc, pins: conn.pinList ?? {} };
  });
  right.forEach((conn, index) => {
    const y = spread(right.length, index), tc = topConnector(conn.housing, conn.byPin, "B", W - 24, y, 34, 26);
    const exit = tc ? tc.wireX : W - 24 - 32;
    body += (tc ? tc.svg : connectorBox(W - 24 - 32, y - 11, 32, 22, "")) + designator("B", W - 1, y, conn.label);
    at[`c:${conn.label}`] = { x: exit - 2, y, conn: tc, pins: conn.pinList ?? {} };
  });
  model.splices.forEach((splice, index) => { at[`s:${splice.id}`] = { x: 118, y: spread(model.splices.length, index) }; });
  // Провода: по паре (откуда, куда) — пучок своих цветов, у разъёма — на высоте его контактов; хвост — отросток от сростка.
  const edges = new Map<string, { from: string; to: string; colors: string[]; idxs: number[] }>();
  model.wires.forEach((wire, idx) => {
    const a = model.endA[idx], b = model.endB[idx];
    if (!wire || !a || !b) return;
    if (a.kind === "tail" || b.kind === "tail") { hasLeaf[key(a.kind === "tail" ? b : a)] = true; return; }
    const id = `${key(a)}>${key(b)}`;
    const edge = edges.get(id) ?? edges.set(id, { from: key(a), to: key(b), colors: [], idxs: [] }).get(id)!;
    edge.colors.push(...(wire.cores.length ? wire.cores : [wire.color]));
    edge.idxs.push(idx);
  });
  const anchorY = (place: Place, idxs: number[]) => {
    const ys = idxs.flatMap((idx) => place.pins?.[idx] ?? []).filter((pin) => !!pin && !!place.conn).map((pin) => place.conn!.pinY(pin));
    return ys.length ? ys.reduce((sum, y) => sum + y, 0) / ys.length : place.y;
  };
  edges.forEach((edge) => {
    const from = at[edge.from], to = at[edge.to];
    if (!from || !to) return;
    const out = edge.from.startsWith("s:") ? 28 : 0, into = edge.to.startsWith("s:") ? -2 : 0;
    wiresBody += strands(from.x + out, anchorY(from, edge.idxs), to.x + into, anchorY(to, edge.idxs), edge.colors.slice(0, 4), 2.4);
  });
  // Зона сростков под одной трубкой на жгуте.
  if (model.splices.length) {
    back += `<rect data-part="harness-tube" x="96" y="${top - 2}" width="94" height="${lines * rowH + 4}" rx="12" fill="${TUBE}" fill-opacity=".08" stroke="${TUBE}" stroke-width="1.4" stroke-dasharray="5 3"/>` +
      text(143, 14, [model.harnessTube || "Трубка на жгуте", model.harnessTubeLen && `${model.harnessTubeLen} мм`].filter(Boolean).join(" · "), 11, 700, INK);
  }
  model.splices.forEach((splice) => {
    const p = at[`s:${splice.id}`];
    body += text(p.x + 2, p.y - 9, splice.name, 10, 700, INK, "start") +
      `<rect data-part="splice-tube" x="${f(p.x - 2)}" y="${f(p.y - 6)}" width="30" height="12" rx="6" fill="${TUBE}" stroke="${INK}" stroke-width="1"/>` +
      `<circle cx="${f(p.x + 13)}" cy="${f(p.y)}" r="2.6" fill="${COPPER}"/>`;
    if (hasLeaf[`s:${splice.id}`]) {
      body += `<path d="M${f(p.x + 28)},${f(p.y)} h26" stroke="${INK}" stroke-width="4.8" stroke-linecap="round"/><path d="M${f(p.x + 28)},${f(p.y)} h26" stroke="#8a97a0" stroke-width="3" stroke-linecap="round"/>` +
        `<circle cx="${f(p.x + 55)}" cy="${f(p.y)}" r="2.4" fill="#d8dde0" stroke="${INK}" stroke-width=".8"/>` + text(p.x + 31, p.y + 14, "хвост", 10, 600, MUTED, "start");
    }
  });
  // Легенда: что значат штриховка и тёмная вставка.
  const y0 = top + lines * rowH + 18;
  const mm = (len: string) => (len ? ` · ${len} мм` : "");
  const branchTubes = [...new Set(left.filter((conn) => conn.tube).map((conn) => conn.tube))];
  const branchLen = left.find((conn) => conn.tube)?.tubeLen ?? "";
  const braids = left.filter((conn) => conn.braidLen);
  let legend = "", ly = y0;
  const item = (swatch: string, label: string) => { legend += swatch.replace("{y}", String(ly - 9)) + text(26, ly, cut(label, 48), 10, 400, MUTED, "start"); ly += 13; };
  const braidSwatch = `<rect x="2" y="{y}" width="18" height="10" rx="3" fill="url(#pfBraid)" stroke="${INK}" stroke-width=".8"/>`;
  const tubeSwatch = `<rect x="2" y="{y}" width="18" height="10" rx="5" fill="${TUBE}" stroke="${INK}" stroke-width=".8"/>`;
  if (braids.length) item(braidSwatch, `оплётка ${model.braid} · ${braids[0].braidLen} мм на ветвях`.replace(/\s+/g, " "));
  if (branchTubes.length) item(tubeSwatch, `${branchTubes.join(", ")}${mm(branchLen)} на ветвях`);
  if (model.spliceTube) item(tubeSwatch, `${model.spliceTube}${mm(model.spliceTubeLen)} на сростках`);
  return { width: W, height: Math.round(ly - 4), body: back + wiresBody + body + legend };
}
