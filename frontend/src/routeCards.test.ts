import { describe, expect, it } from "vitest";
import {
  blockCards, crimpWires, cutEnds, depthOrder, genArtHeight, genCardHeight, genCards, inputsHeight, layoutByDepth, linkGeometry, opStages, opsMinutes,
  schemeArt, widestRow, EMPTY_ART, FLOW_GAP_X, headExtra, inputsHeightWrapped, layoutFlow, levelCount, opRowHeight, pickFlow, textLines, turnPath,
} from "./routeCards";
import { blockTitle, deriveRoute, parseRoute, routeLevels, routeProblem, type RouteOp } from "./routeLogic";
import type { PinoutEnd, PinUnit } from "./pinoutCheck";
import { makeGen, type OpSpec } from "./tcGenLogic";
import { DSA, fullHarness } from "./tcGenRealHarness.fixture";

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
  it("goes around a card standing right on the way, so the link stays visible", () => {
    // Обход — общей шиной по левому краю схемы: вниз под ряд источника, влево за самую левую карточку, вниз, вправо над целью.
    const blocker = { x: 40, y: 150, width: 120, height: 80 };
    const around = linkGeometry(card(0, 0), card(0, 400), [blocker]);
    expect(around.d).toContain("L2,382");
    expect(around.d.endsWith("L100,400")).toBe(true);
    expect(around.mid).toEqual({ x: 51, y: 388 });
    expect(linkGeometry(card(0, 0), card(0, 400), [{ x: 600, y: 150, width: 100, height: 80 }]).d).toBe("M100,100 V400");
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
    op(3, "insTut", "Надеть ТУТ на провод", { key: "insTut", minutes: 3 }),
    op(4, "prsTerm", "Опрессовка терминалов А1", { side: "A", minutes: 4 }),
    op(5, "insTerm", "Монтаж в разъём А1", { side: "A", minutes: 1 }),
    op(6, "insMark", "Маркировка", { minutes: 1 }),
    op(7, "insTerm", "Монтаж в разъём В1", { side: "B", minutes: 2 }),
    op(8, "heatTut", "Усадка ТУТ", { minutes: 1 }),
  ];

  it("assigns every operation to its semi-finished product: a sideless one prepares the next side, the last ones finish", () => {
    expect([...opStages(OPS).entries()].sort(([a], [b]) => a - b).map(([, stage]) => stage))
      .toEqual(["wires", "tubes", "crimpedA", "crimpedA", "A", "B", "B", "final"]);
  });

  it("groups the ETK blocks into wires, tubes, crimped wires, side А, side В and the finished cable with their links", () => {
    const { cards, links } = genCards(deriveRoute(OPS), OPS);
    expect(cards.map((card) => [card.id, card.kind, card.title, card.ops.map((item) => item.position)])).toEqual([
      ["wires", "semiFinished", "Нарезанные провода", [1]],
      ["tubes", "semiFinished", "Нарезанные трубки", [2]],
      ["crimpedA", "semiFinished", "Обжатые провода, сторона А", [3, 4]],
      ["A", "assembly", "Сторона А собрана", [5]],
      ["B", "assembly", "Сторона В собрана", [6, 7]],
      ["final", "final", "Готовый кабель", [8]],
    ]);
    expect(links.map((link) => `${link.from}>${link.to}`)).toEqual(["wires>crimpedA", "tubes>crimpedA", "crimpedA>A", "A>B", "B>final"]);
    expect(links.every((link) => link.qty === 1)).toBe(true);
    // Минуты на штуку при партии 5: (3 + 4) / 5.
    expect(opsMinutes(cards[2].ops, 5)).toBeCloseTo(1.4);
    expect(opsMinutes([op(1, "cutWire", "Резка")], 5)).toBeNull();
  });

  it("makes the last side the finished cable when nothing follows it and drops back links of interleaved sides", () => {
    // Монтаж А1, монтаж В1, монтаж А2: сторона А с последней операцией — готовый кабель, стрелка из неё назад в В
    // (А1 → В1) карточки не связывает.
    const ops = [op(1, "cutWire", "Резка"), op(2, "insTerm", "Монтаж А1", { side: "A" }), op(3, "insTerm", "Монтаж В1", { side: "B" }),
      op(4, "insTerm", "Монтаж А2", { side: "A" })];
    const { cards, links } = genCards(deriveRoute(ops), ops);
    expect(cards.map((card) => [card.title, card.final])).toEqual([["Нарезанные провода", false], ["Сторона В собрана", false], ["Готовый кабель", true]]);
    expect(links.map((link) => `${link.from}>${link.to}`)).toEqual(["wires>A", "B>A"]);
  });

  it("solders the coax centre contacts before the housings as a ПФ per side, in the order of work (796К.2 КБ1, 15.10)", () => {
    // Пайка А, пайка В, корпус А, корпус В: раньше пайка В лежала в «Стороне В собрана» после корпуса А.
    const coax = [op(1, "coaxCut", "Резка кабеля"), op(2, "coaxPin", "Пайка А", { side: "A" }), op(3, "coaxPin", "Пайка В", { side: "B" }),
      op(4, "coaxHousing", "Корпус А", { side: "A" }), op(5, "coaxHousing", "Корпус В", { side: "B" })];
    const { cards, links } = genCards(deriveRoute(coax), coax);
    expect(cards.map((card) => [card.title, card.ops.map((item) => item.position)])).toEqual([
      ["Нарезанный кабель", [1]], ["Кабель с центральным контактом, сторона А", [2]], ["Кабель с центральным контактом, сторона В", [3]],
      ["Сторона А собрана", [4]], ["Готовый кабель", [5]],
    ]);
    expect(links.map((link) => `${link.from}>${link.to}`)).toEqual(["wires>crimpedA", "crimpedA>crimpedB", "crimpedB>A", "A>B"]);
    // Угловой контакт В паяется внутри корпуса — это сборка стороны В, не своя карточка.
    const angled = [op(1, "coaxCut", "Резка кабеля"), op(2, "coaxPin", "Пайка А", { side: "A" }), op(3, "coaxInsSleeve", "Втулка"),
      op(4, "coaxPin", "Пайка В внутри корпуса", { side: "B" }), op(5, "coaxHousing", "Корпус А", { side: "A" })];
    expect([...opStages(angled).entries()].sort(([a], [b]) => a - b).map(([, stage]) => stage)).toEqual(["wires", "crimpedA", "B", "B", "A"]);
  });

  it("keeps coax stripping with the cut cable, never in a side card (owner 14.10)", () => {
    // Резка, разделка А, разделка В, резка ТУТ, пайки — разделка в первой карточке.
    const separate = [op(1, "coaxCut", "Резка кабеля"), op(2, "coaxStrip", "Разделка А", { side: "A", code: "STRIP_COAX_auto" }),
      op(3, "coaxStrip", "Разделка В", { side: "B", code: "STRIP_COAX_auto" }), op(4, "coaxCutTut", "Резка ТУТ", { code: "CUT_TUT_short" }),
      op(5, "coaxPin", "Пайка А", { side: "A" }), op(6, "coaxPin", "Пайка В", { side: "B" }), op(7, "coaxInsTut", "Монтаж ТУТ")];
    expect([...opStages(separate).entries()]).toEqual([[1, "wires"], [2, "wires"], [3, "wires"], [4, "tubes"], [5, "A"], [6, "B"], [7, "final"]]);
    const split = genCards(deriveRoute(separate), separate);
    expect(split.cards.map((card) => [card.title, card.ops.map((item) => item.position)])).toEqual([
      ["Нарезанный и разделанный кабель", [1, 2, 3]], ["Нарезанные трубки", [4]], ["Сторона А собрана", [5]], ["Сторона В собрана", [6]],
      ["Готовый кабель", [7]],
    ]);
    // Трубки идут туда, где их надевают («Монтаж ТУТ»), а не в первую операцию после резки.
    expect(split.links.map((link) => `${link.from}>${link.to}`)).toEqual(["wires>A", "tubes>final", "A>B", "B>final"]);
    // Резка с разделкой на станке — одна операция CUT_COAX+2strip.
    const machine = [op(1, "coaxCut", "Резка с разделкой (А и В)", { code: "CUT_COAX+2strip" }), op(2, "coaxPin", "Пайка А", { side: "A" })];
    expect(genCards(deriveRoute(machine), machine).cards[0].title).toBe("Нарезанный и разделанный кабель");
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

describe("карточка «Обжатые провода»", () => {
  const titles = (ops: RouteOp[]) => genCards(deriveRoute(ops), ops).cards.map((card) => card.title);
  const linkIds = (ops: RouteOp[]) => genCards(deriveRoute(ops), ops).links.map((link) => link.id);

  it("shows the owner's НВ4 harness («Сложная сборка») as its own ПФ graph: a card per block with its level and what comes in", () => {
    // 713К.4 КБ1 (tcGenRealHarness.fixture.ts): схема ПФ генератора — карточка на блок, а не стадии генератора.
    const gen = makeGen(DSA, fullHarness());
    const specs = gen.getActiveOps().filter((spec) => gen.templateIdFor(spec));
    const ops: RouteOp[] = specs.map((spec, index) => ({ position: index + 1, key: spec.opKey, type: spec.type, code: spec.tmplHint || "",
      label: spec.label, minutes: null, ...(spec.side ? { side: spec.side } : {}) }));
    const route = parseRoute((gen.buildConfig() as { route: unknown }).route)!;
    expect(routeProblem(route, ops)).toBeNull();
    const { cards, links } = blockCards(route, ops, (block) => blockTitle(block, ops, "Жгут НВ4"), routeLevels(route));
    expect(cards.map((card) => `${card.level} ${card.title} [${card.kind}]`)).toEqual([
      "1.1 Резка + обжим SYM, зачистка — X1, X2 [semiFinished]", "1.2 Резка + обжим T-DS1071, зачистка — X5, X6 [semiFinished]",
      "1.3 Резка хвостов + лужение [semiFinished]", "1.4 Резка + обжим SHF, зачистка — X3 [semiFinished]",
      "1.5 Резка + обжим А и В — X4→X6 [semiFinished]",
      "1.6 Резка трубок [semiFinished]", "1.7 Резка оплётки [semiFinished]", "1.8 Маркировка [semiFinished]",
      "2.1 Ветвь X1 «5», сторона А [assembly]", "2.2 Ветвь X2 «I3», сторона А [assembly]", "2.3 Ветвь X3 «C», сторона А [assembly]",
      "2.4 Ветвь X4 «A6», сторона А [assembly]",
      "3.1 Пайка С1, С2 [assembly]", "3.2 Пайка С3, С4 [assembly]", "4 ТУТ на месте Б [assembly]", "5 Кабельная сборка Жгут НВ4 [final]",
    ]);
    expect(cards.find((card) => card.id === "pf-A1")!.inputs).toEqual([
      "НВ4 0,5 красный, черный — 2 шт", "ТТК 8/2 — 1 шт", "Оплётка XP-3 120 мм — 1 шт", "Маркировка «5» — 1 шт"]);
    expect(inputsHeight(5)).toBe(4 * 16 + 16 + 8);
    expect(links).toHaveLength(route.edges.length);
    expect(widestRow(cards.map((card) => card.id), links)).toBe(8);
  });

  it("cuts the owner's cable 14.10 as cut, crimp А, side А, crimp В, side В — each fed by the previous one", () => {
    // I-DS1071-SCB01X4 (А) ↔ PHR-4 (В): ЭТК 01 Резка, 02 Опрессовка А, 03 Монтаж А, 04 Опрессовка В, 05 Монтаж В.
    const ops = [op(1, "cutWire", "Резка"), op(2, "prsTerm", "Опрессовка А1", { side: "A" }), op(3, "insTerm", "Монтаж А1", { side: "A" }),
      op(4, "prsTerm", "Опрессовка В1", { side: "B" }), op(5, "insTerm", "Монтаж В1", { side: "B" })];
    expect(titles(ops)).toEqual(["Нарезанные провода", "Обжатые провода, сторона А", "Сторона А собрана", "Обжатые провода, сторона В", "Готовый кабель"]);
    expect(linkIds(ops)).toEqual(["wires>crimpedA", "crimpedA>A", "A>crimpedB", "crimpedB>B"]);
  });

  it("puts the crimp of a side right before it when a side operation stands earlier in the ETK", () => {
    const ops = [op(1, "cutWire", "Резка"), op(2, "stripCable", "Скрутка экрана А1", { key: "shieldA1", side: "A" }),
      op(3, "prsTerm", "Опрессовка А1", { side: "A" }), op(4, "insTerm", "Монтаж А1", { side: "A" }), op(5, "heatTut", "Усадка")];
    const { cards } = genCards(deriveRoute(ops), ops);
    expect(cards.map((card) => [card.id, card.ops.map((item) => item.position)])).toEqual([["wires", [1]], ["crimpedA", [3]], ["A", [2, 4]], ["final", [5]]]);
  });

  it("names the wires card «Нарезанные и обжатые провода» after the cutting machine crimps, with a crimped card only for the hand crimps left", () => {
    const machine = [op(1, "cutWireCrimp", "Резка с обжимом А и В"), op(2, "insTerm", "Монтаж А1", { side: "A" }), op(3, "insTerm", "Монтаж В1", { side: "B" })];
    expect(titles(machine)).toEqual(["Нарезанные и обжатые провода", "Сторона А собрана", "Готовый кабель"]);
    const handB = [op(1, "cutWireCrimp", "Резка с обжимом А"), op(2, "insTerm", "Монтаж А1", { side: "A" }),
      op(3, "prsTerm", "Опрессовка В1 (двойной обжим)", { side: "B" }), op(4, "insTerm", "Монтаж В1", { side: "B" })];
    expect(titles(handB)).toEqual(["Нарезанные и обжатые провода", "Сторона А собрана", "Обжатые провода, сторона В", "Готовый кабель"]);
    expect(linkIds(handB)).toEqual(["wires>A", "A>crimpedB", "crimpedB>B"]);
    // Станок обжал, руками — только разделка кабеля: отдельной карточки нет, разделка у своей стороны.
    const stripOnly = [op(1, "cutWireCrimp", "Резка с обжимом А"), op(2, "insTerm", "Монтаж А1", { side: "A" }),
      op(3, "stripCable", "Разделка кабеля под пайку В1", { key: "stripCableB1", side: "B" }), op(4, "solderConn", "Пайка разъёма В1", { side: "B" })];
    expect(titles(stripOnly)).toEqual(["Нарезанные и обжатые провода", "Сторона А собрана", "Готовый кабель"]);
  });

  it("keeps the ТУТ for soldering and the cable stripping of a side without a hand crimp with that side; the crimp of А is its own", () => {
    const ops = [op(1, "cutWireStrip", "Резка с разделкой"), op(2, "cutTut", "Резка ТУТ"), op(3, "prsTerm", "Опрессовка А1", { side: "A" }),
      op(4, "insTerm", "Монтаж А1", { side: "A" }), op(5, "insTut", "Надеть ТУТ на провод", { key: "insTut" }),
      op(6, "stripCable", "Разделка кабеля под пайку В1", { key: "stripCableB1", side: "B" }),
      op(7, "stripCable", "Скрутка экрана В1", { key: "shieldB1", side: "B" }), op(8, "solderConn", "Пайка разъёма В1", { side: "B" }),
      op(9, "heatTut", "Усадка ТУТ", { key: "heatTut" })];
    expect([...opStages(ops).entries()].sort(([a], [b]) => a - b).map(([, stage]) => stage))
      .toEqual(["wires", "tubes", "crimpedA", "A", "B", "B", "B", "B", "final"]);
    // ТУТ надевают на провода стороны В перед пайкой — трубки идут в неё.
    expect(linkIds(ops)).toEqual(["wires>crimpedA", "tubes>B", "crimpedA>A", "A>B", "B>final"]);
  });

  it("leaves a cable without a hand crimp as it was, and finishing tinning or a lug crimped last with the finished cable", () => {
    const solder = [op(1, "cutWire", "Резка"), op(2, "stripCable", "Разделка кабеля под пайку А1", { key: "stripCableA1", side: "A" }),
      op(3, "solderConn", "Пайка разъёма А1", { side: "A" }), op(4, "tin", "Лужение")];
    expect(titles(solder)).toEqual(["Нарезанные провода", "Сторона А собрана", "Готовый кабель"]);
    const tinned = [op(1, "cutWire", "Резка"), op(2, "prsTerm", "Опрессовка А1", { side: "A" }), op(3, "insTerm", "Монтаж А1", { side: "A" }), op(4, "tin", "Лужение")];
    expect(titles(tinned)).toEqual(["Нарезанные провода", "Обжатые провода, сторона А", "Сторона А собрана", "Готовый кабель"]);
    expect(opStages(tinned).get(4)).toBe("final");
    const lug = [op(1, "cutWire", "Резка"), op(2, "prsTerm", "Опрессовка наконечников А1", { side: "A" })];
    expect(titles(lug)).toEqual(["Нарезанные провода", "Готовый кабель"]);
    // Только сростки, без опрессовки.
    const splices = [op(1, "cutWire", "Резка"), op(2, "splice", "Сростки"), op(3, "insTerm", "Монтаж А1", { side: "A" })];
    expect(titles(splices)).toEqual(["Нарезанные провода", "Провода со сростками", "Готовый кабель"]);
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
    // Первый провод — цвет из наименования; второму цвет жилы выбран на схеме распиновки (бело-синий: основной — белый).
    const units: PinUnit[] = [
      { key: "u1", idx: 0, unit: 0, num: "1", compId: "c", name: "МГТФ красный", color: "#d7322d", stripe: null, colorName: "", baseName: null, length: 150, at: { e1: 1, e2: 2 } },
      { key: "u2", idx: 1, unit: 0, num: "2", compId: "c", name: "МГТФ", color: "#f4f4f4", stripe: "#2463c7", colorName: "бело-синий", baseName: "белый", length: 0, at: { e1: 2, e2: null } },
    ];
    const specs = [
      { type: "cutWireStrip", opKey: "cutWire", label: "Резка", wireIdxs: [0, 1], strip: { a: true, lenA: "3", b: true, lenB: "3" } },
      { type: "cutTut", opKey: "cutTut", label: "Резка ТУТ — 4×20мм (на пайку)" },
    ] as OpSpec[];
    const housing = { rows: 1, cols: 2, color: "#fff", grid: [[1, 2]] };
    const art = schemeArt(specs, ends, units, { "XHP-2": housing }, null);
    expect(art.wires.map((wire) => [wire.label, wire.left, wire.right, wire.color])).toEqual([
      ["А1:1 → В1:2", "copper", "copper", "#d7322d"], ["А1:2 → В1:?", "copper", "copper", "#f4f4f4"],
    ]);
    expect(art.wires[0].title).toBe("1 · МГТФ красный · 150 мм");
    expect(art.tubes).toEqual([{ key: "cutTut", label: "ТУТ — 4×20мм (на пайку)", count: 0 }]);
    expect(art.faces.A).toEqual([{ key: "А1", label: "А1 · XHP-2", housing, byPin: { 1: "МГТФ красный", 2: "белый" } }]);
    expect(art.faces.B[0].byPin).toEqual({ 2: "МГТФ красный" });
    // Сторона без разъёмов — свободный конец.
    const free = schemeArt([], [ends[0]], [{ ...units[0], at: { e1: 3 } }], {}, { side: "B", ends: [] });
    expect(free.wires[0].label).toBe("А1:3 → своб. конец");
    expect(free.faces.A[0].housing).toBeNull();
    expect(art.crimped).toEqual([]);
  });

  it("draws the crimped wires with a terminal on every end crimped by hand, and only the wires that got one", () => {
    const ends = [{ id: "a1", key: "А1", side: "A", conn: "SMR-02V-B" }, { id: "b1", key: "В1", side: "B", conn: "SCB01X3" }] as PinoutEnd[];
    const unit = (key: string, idx: number, at: PinUnit["at"]): PinUnit =>
      ({ key, idx, unit: 0, num: String(idx + 1), compId: "c", name: "НВ4 0,2 красный", color: "#d7322d", stripe: null, colorName: "", baseName: null, length: 160, at });
    const units = [unit("u1", 0, { a1: 1, b1: 1 }), unit("u2", 1, { b1: 2 }), unit("u3", 2, {})];
    const specs = [
      { type: "cutWire", opKey: "cutWire", label: "Резка", wireIdxs: [0, 1, 2] },
      { type: "prsTerm", opKey: "prsTermA1", side: "A", epId: "a1", label: "Опрессовка терминалов А1" },
      { type: "prsTerm", opKey: "prsTermB1", side: "B", epId: "b1", label: "Опрессовка терминалов В1" },
    ] as OpSpec[];
    const art = schemeArt(specs, ends, units, {}, null);
    expect(art.wires.map((wire) => [wire.left, wire.right])).toEqual([["cut", "cut"], ["cut", "cut"], ["cut", "cut"]]);
    expect(art.crimped.map((wire) => [wire.key, wire.label, wire.left, wire.right])).toEqual([
      ["u1", "А1:1 → В1:1", "terminal", "terminal"], ["u2", "В1:2", "cut", "terminal"],
    ]);
    expect(genArtHeight("crimped", art, false)).toBe(2 * 22 + 8);
    // Обжим одной стороны — провода, обжатые на ней, наконечник только у её конца.
    expect(art.crimpedBy?.A.map((wire) => [wire.key, wire.left, wire.right])).toEqual([["u1", "terminal", "cut"]]);
    expect(art.crimpedBy?.B.map((wire) => [wire.key, wire.left, wire.right])).toEqual([["u1", "cut", "terminal"], ["u2", "cut", "terminal"]]);
    expect(genArtHeight("crimpedA", art, false)).toBe(22 + 8);
    expect(crimpWires("crimpedB", art)).toBe(art.crimpedBy?.B);
    // Обжато на станке резки — в обжатые не попадает.
    const machine = schemeArt([{ type: "cutWireCrimp", opKey: "cutWire", side: "A", label: "Резка с обжимом А", wireIdxs: [0] } as OpSpec], ends, [units[0]], {}, null);
    expect(machine.crimped).toEqual([]);
  });
});

describe("схема слева направо (владелец 25.10: цепочка занимала 40 % ширины)", () => {
  const chain = [{ from: "kit", to: "mid" }, { from: "mid", to: "final" }];
  it("picks left-to-right for a chain and narrow branches, top-down for a wide fan-in, top-down when columns do not fit", () => {
    expect(levelCount(["kit", "mid", "final"], chain)).toBe(3);
    expect(pickFlow(["kit", "mid", "final"], chain, 1500)).toBe("right");
    expect(pickFlow(["a", "b", "final"], [{ from: "a", to: "final" }, { from: "b", to: "final" }], 1500)).toBe("right");
    const fan = ["a", "b", "c", "d", "final"];
    expect(pickFlow(fan, fan.slice(0, 4).map((from) => ({ from, to: "final" })), 1500)).toBe("down");
    expect(pickFlow(["kit", "mid", "final"], chain, 600)).toBe("down");
    expect(pickFlow(["kit", "mid", "final"], chain, 0)).toBe("down");
    expect(pickFlow(["only"], [], 1500)).toBe("down");
  });

  it("lays levels out as columns: depth along x, level neighbours stacked", () => {
    const size = () => ({ width: 200, height: 100 });
    const places = layoutFlow(["a", "b", "final"], [{ from: "a", to: "final" }, { from: "b", to: "final" }], size, { gapX: 16, gapY: 40, margin: 4 }, "right");
    expect(places.get("a")).toEqual({ x: 4, y: 4 });
    expect(places.get("b")).toEqual({ x: 4, y: 144 });
    expect(places.get("final")).toEqual({ x: 4 + 200 + FLOW_GAP_X, y: 4 });
  });

  it("turns a link path back from rotated coordinates: pairs swap, V and H trade places", () => {
    expect(turnPath("M10,20 V50 Q10,60 18,60 H40 L1,2")).toBe("M20,10H50Q60,10 60,18V40L2,1");
  });

  it("draws a left-to-right link from the right side of the source to the left side of the target", () => {
    const from = { x: 0, y: 0, width: 200, height: 100 }, to = { x: 300, y: 0, width: 200, height: 100 };
    expect(linkGeometry(from, to, [], "right")).toEqual({ d: "M200,50H300", mid: { x: 250, y: 50 } });
    const bend = linkGeometry(from, { ...to, y: 150 }, [], "right");
    expect(bend.d.startsWith("M200,50H")).toBe(true);
    expect(bend.d.endsWith("H300")).toBe(true);
  });
});

describe("широкий ряд и перенос текста (владелец 25.10: названия обрезались, 8 заготовок в одной строке)", () => {
  const size = () => ({ width: 200, height: 100 });
  const spacing = { gapX: 10, gapY: 20, margin: 4 };
  it("wraps a row wider than maxPerRow onto the next lines, list order kept, levels below start under the last line", () => {
    const ids = ["a", "b", "c", "d", "e", "final"];
    const links = ["a", "b", "c", "d", "e"].map((from) => ({ from, to: "final" }));
    const places = layoutByDepth(ids, links, size, spacing, 3);
    expect([places.get("a")!.x, places.get("b")!.x, places.get("c")!.x]).toEqual([4, 214, 424]);
    expect([places.get("d")!, places.get("e")!]).toEqual([{ x: 4, y: 124 }, { x: 214, y: 124 }]);
    expect(places.get("final")).toEqual({ x: 4, y: 244 });
    // Без ограничения — прежняя раскладка одним рядом.
    expect(layoutByDepth(ids, links, size, spacing).get("e")).toEqual({ x: 4 + 4 * 210, y: 4 });
  });

  it("counts text lines and grows rows and head by them; one line keeps the old height", () => {
    expect(textLines("Резка", 200, 6.4, 3)).toBe(1);
    expect(textLines("x".repeat(100), 200, 6.4, 3)).toBe(3);
    expect(textLines("x".repeat(1000), 200, 6.4, 3)).toBe(3);
    expect(opRowHeight("Резка", 280, 22)).toBe(22);
    expect(opRowHeight("Надеть оплётку X1 «5» — XP-3, 120 мм, на кабель и на разъём", 280, 22)).toBeGreaterThan(22);
    expect(headExtra("Ветвь X1", "", "1 оп.", 280)).toBe(0);
    expect(headExtra("Резка + обжим SYM, зачистка — X1, X2, X3, X4", "", "1 оп.", 260)).toBeGreaterThan(0);
    expect(inputsHeightWrapped([], 280)).toBe(0);
    expect(inputsHeightWrapped(["ТТК 8/2 — 1 шт"], 280)).toBe(24);
  });

  it("routes a link that skips a row along the left bus, below its own row", () => {
    const from = { x: 20, y: 20, width: 200, height: 100 }, to = { x: 20, y: 400, width: 200, height: 100 };
    const blocker = { x: 20, y: 200, width: 200, height: 100 };
    const bus = linkGeometry(from, to, [blocker]);
    expect(bus.d).toContain("L10,");
    expect(bus.mid.y).toBe(388);
  });
});
