import { describe, expect, it } from "vitest";
import {
  cutEnds, depthOrder, genArtHeight, genCardHeight, genCards, layoutByDepth, linkGeometry, opStages, opsMinutes, schemeArt, widestRow, EMPTY_ART,
} from "./routeCards";
import { deriveRoute, type RouteOp } from "./routeLogic";
import type { PinoutEnd, PinUnit } from "./pinoutCheck";
import type { OpSpec } from "./tcGenLogic";

const op = (position: number, type: string, label: string, extra: Partial<RouteOp> = {}): RouteOp =>
  ({ position, key: `${type}${position}`, type, code: "", label, minutes: null, ...extra });

describe("раскладка рядами по глубине (алгоритм Василия)", () => {
  it("places cards exactly where generateRouteV3 does: 32 + column·304 across, 32 + depth·328 down", () => {
    // Заготовки проводов и оболочек, слепок из обеих, финал.
    const places = layoutByDepth(["wire", "cover", "fragment", "final"],
      [{ from: "wire", to: "fragment" }, { from: "cover", to: "fragment" }, { from: "fragment", to: "final" }]);
    expect(Object.fromEntries(places)).toEqual({
      wire: { x: 32, y: 32 }, cover: { x: 336, y: 32 }, fragment: { x: 32, y: 360 }, final: { x: 32, y: 688 },
    });
  });

  it("takes the deepest input for the row, keeps list order inside a row and survives a cycle", () => {
    const links = [{ from: "a", to: "c" }, { from: "b", to: "c" }, { from: "a", to: "b" }];
    expect(depthOrder(["a", "b", "c"], links).depth).toEqual(new Map([["a", 0], ["b", 1], ["c", 2]]));
    // Карточка, чей вход ещё не стоит, ждёт его, даже если в списке раньше.
    expect(depthOrder(["c", "a"], [{ from: "a", to: "c" }]).order).toEqual(["a", "c"]);
    expect(layoutByDepth(["x", "y"], [{ from: "x", to: "y" }, { from: "y", to: "x" }]).size).toBe(2);
    expect(widestRow(["a", "b", "c", "d"], [{ from: "a", to: "d" }, { from: "b", to: "d" }, { from: "c", to: "d" }])).toBe(3);
  });

  it("gives a row the height of its tallest card when the cards differ", () => {
    const heights: Record<string, number> = { a: 100, b: 180, c: 60 };
    const places = layoutByDepth(["a", "b", "c"], [{ from: "a", to: "c" }, { from: "b", to: "c" }],
      (id) => ({ width: 200, height: heights[id] }), { gapX: 16, gapY: 40, margin: 4 });
    expect(places.get("b")).toEqual({ x: 220, y: 4 });
    expect(places.get("c")).toEqual({ x: 4, y: 4 + 180 + 40 });
  });
});

describe("связь карточек", () => {
  const card = (x: number, y: number) => ({ x, y, width: 200, height: 100 });
  it("is a straight line under the input and an orthogonal line with rounded corners to the side", () => {
    expect(linkGeometry(card(0, 0), card(0, 200))).toEqual({ d: "M100,100 V200", mid: { x: 100, y: 150 } });
    const side = linkGeometry(card(0, 0), card(300, 200));
    expect(side.d).toBe("M100,100 V160 Q100,168 108,168 H392 Q400,168 400,176 V200");
    expect(side.mid).toEqual({ x: 250, y: 168 });
  });
  it("bends smoothly when the target was dragged above its input", () => {
    expect(linkGeometry(card(0, 300), card(300, 0)).d).toMatch(/^M100,400 C/);
  });
});

describe("карточки «Схемы полуфабрикатов» генератора", () => {
  // Резка, резка ТУТ, надеть ТУТ, опрессовка и монтаж А1, маркировка, монтаж В1, усадка — по правилам ЭТК.
  const OPS = [
    op(1, "cutWireStrip", "Резка с разделкой", { code: "CUT_WIRE+2strip", minutes: 10 }),
    op(2, "cutTut", "Резка ТУТ — 4×20 мм", { code: "CUT_TUT_short", minutes: 2 }),
    op(3, "insTut", "Надеть ТУТ на провод", { minutes: 3 }),
    op(4, "prsTerm", "Опрессовка терминалов А1", { side: "A", minutes: 4 }),
    op(5, "insTerm", "Монтаж в разъём А1", { side: "A", minutes: 1 }),
    op(6, "insMark", "Маркировка", { minutes: 1 }),
    op(7, "insTerm", "Монтаж в разъём В1", { side: "B", minutes: 2 }),
    op(8, "heatTut", "Усадка ТУТ", { minutes: 1 }),
  ];

  it("assigns every operation to its semi-finished product: a sideless one prepares the next side, the last ones finish", () => {
    expect([...opStages(OPS).entries()].sort(([a], [b]) => a - b).map(([, stage]) => stage))
      .toEqual(["wires", "tubes", "A", "A", "A", "B", "B", "final"]);
  });

  it("groups the ETK blocks into wires, tubes, side А, side В and the finished cable with their links", () => {
    const { cards, links } = genCards(deriveRoute(OPS), OPS);
    expect(cards.map((card) => [card.id, card.kind, card.title, card.ops.map((item) => item.position)])).toEqual([
      ["wires", "semiFinished", "Нарезанные провода", [1]],
      ["tubes", "semiFinished", "Нарезанные трубки", [2]],
      ["A", "assembly", "Сторона А собрана", [3, 4, 5]],
      ["B", "assembly", "Сторона В собрана", [6, 7]],
      ["final", "final", "Готовый кабель", [8]],
    ]);
    expect(links.map((link) => `${link.from}>${link.to}`)).toEqual(["wires>A", "tubes>A", "A>B", "B>final"]);
    expect(links.every((link) => link.qty === 1)).toBe(true);
    // Минуты на штуку при партии 5: (3 + 4 + 1) / 5.
    expect(opsMinutes(cards[2].ops, 5)).toBeCloseTo(1.6);
    expect(opsMinutes([op(1, "cutWire", "Резка")], 5)).toBeNull();
  });

  it("makes the last side the finished cable when nothing follows it and drops back links of interleaved sides", () => {
    // Коаксиал: разделка А, разделка В, пайка А, пайка В — стрелки блоков ходят А → В → А.
    const coax = [op(1, "coaxCut", "Резка кабеля"), op(2, "coaxStrip", "Разделка А", { side: "A" }), op(3, "coaxStrip", "Разделка В", { side: "B" }),
      op(4, "coaxPin", "Пайка А", { side: "A" }), op(5, "coaxPin", "Пайка В", { side: "B" })];
    const { cards, links } = genCards(deriveRoute(coax), coax);
    expect(cards.map((card) => [card.title, card.final])).toEqual([["Нарезанный кабель", false], ["Сторона А собрана", false], ["Готовый кабель", true]]);
    expect(links.map((link) => `${link.from}>${link.to}`)).toEqual(["wires>A", "A>B"]);
  });

  it("puts the number on a link from its single arrow and nothing when the arrows differ", () => {
    const route = deriveRoute(OPS);
    const edited = { ...route, edges: route.edges.map((edge) => (edge.from === "b2" ? { ...edge, per_unit: 4 } : edge)) };
    expect(genCards(edited, OPS).links.find((link) => link.from === "tubes")?.qty).toBe(4);
    const twoArrows = { ...route, edges: [...route.edges, { from: "b1", to: "b4", per_unit: 2 }] };
    expect(genCards(twoArrows, OPS).links.find((link) => link.from === "wires")?.qty).toBeNull();
  });

  it("orders the side cards by the operations when the technologist put side В first", () => {
    const ops = [op(1, "cutWire", "Резка"), op(2, "insTerm", "Монтаж В1", { side: "B" }), op(3, "insTerm", "Монтаж А1", { side: "A" })];
    const { cards, links } = genCards(deriveRoute(ops), ops);
    expect(cards.map((card) => card.title)).toEqual(["Нарезанные провода", "Сторона В собрана", "Готовый кабель"]);
    expect(links.map((link) => `${link.from}>${link.to}`)).toEqual(["wires>B", "B>A"]);
  });

  it("sizes a card by its picture and operations", () => {
    expect(genCardHeight(3, 0)).toBe(64 + 3 * 22 + 20);
    expect(genCardHeight(2, 72, 40)).toBe(64 + 80 + 2 * 40 + 20);
    const art = { ...EMPTY_ART, wires: Array.from({ length: 6 }, (_, i) => ({ key: `w${i}`, label: "", title: "", color: "#000000", left: "cut" as const, right: "cut" as const })) };
    expect(genArtHeight("wires", art, false)).toBe(4 * 22 + 16 + 8);
    expect(genArtHeight("A", art, false)).toBe(0);
  });
});

describe("рисунки карточек генератора", () => {
  it("draws the ends of a cut wire from its cut operation", () => {
    expect(cutEnds(null)).toEqual({ left: "cut", right: "cut" });
    expect(cutEnds({ type: "cutWireStrip", strip: { a: true, b: false } })).toEqual({ left: "copper", right: "cut" });
    expect(cutEnds({ type: "cutWireCrimp", side: "A", strip: { a: true, b: true } })).toEqual({ left: "terminal", right: "copper" });
    expect(cutEnds({ type: "cutWireCrimp", side: "A", tin: true })).toEqual({ left: "terminal", right: "tin" });
    expect(cutEnds({ type: "cutWireCrimp", side: "A", both: true })).toEqual({ left: "terminal", right: "terminal" });
  });

  it("labels wires «А1:1 → В1:1» from the pinout, faces with the wire of each contact, tubes without «Резка»", () => {
    const ends = [{ id: "e1", key: "А1", side: "A", conn: "XHP-2" }, { id: "e2", key: "В1", side: "B", conn: "XHP-2" }] as PinoutEnd[];
    const units = [
      { key: "u1", idx: 0, unit: 0, num: "1", compId: "c", name: "МГТФ красный", color: "#d7322d", length: 150, at: { e1: 1, e2: 2 } },
      { key: "u2", idx: 1, unit: 0, num: "2", compId: "c", name: "МГТФ чёрный", color: null, length: 0, at: { e1: 2, e2: null } },
    ] as PinUnit[];
    const specs = [
      { type: "cutWireStrip", opKey: "cutWire", label: "Резка", wireIdxs: [0, 1], strip: { a: true, lenA: "3", b: true, lenB: "3" } },
      { type: "cutTut", opKey: "cutTut", label: "Резка ТУТ — 4×20мм (на пайку)" },
    ] as OpSpec[];
    const housing = { rows: 1, cols: 2, color: "#fff", grid: [[1, 2]] };
    const art = schemeArt(specs, ends, units, { "XHP-2": housing }, null);
    expect(art.wires.map((wire) => [wire.label, wire.left, wire.right, wire.color])).toEqual([
      ["А1:1 → В1:2", "copper", "copper", "#d7322d"], ["А1:2 → В1:?", "copper", "copper", "#6b7c86"],
    ]);
    expect(art.wires[0].title).toBe("1 · МГТФ красный · 150 мм");
    expect(art.tubes).toEqual([{ key: "cutTut", label: "ТУТ — 4×20мм (на пайку)", count: 0 }]);
    expect(art.faces.A).toEqual([{ key: "А1", label: "А1 · XHP-2", housing, byPin: { 1: "МГТФ красный", 2: "МГТФ чёрный" } }]);
    expect(art.faces.B[0].byPin).toEqual({ 2: "МГТФ красный" });
    // Сторона без разъёмов — свободный конец.
    const free = schemeArt([], [ends[0]], [{ ...units[0], at: { e1: 3 } }], {}, { side: "B", ends: [] });
    expect(free.wires[0].label).toBe("А1:3 → своб. конец");
    expect(free.faces.A[0].housing).toBeNull();
  });
});
