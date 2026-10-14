import { describe, expect, it } from "vitest";
import { designToScene } from "./harness/editor/HarnessDesignEditor";
import { physicalFixture } from "./harness/editor/physical-topology-fixture";
import { buildRouteSourceItems } from "./harness/route/route-source";
import { appendRouteV3Fragment, captureRouteV3Selection, createInitialRouteV3, generateRouteV3, parseRouteV3, type RouteV3Document } from "./harness/route/route-v3-model";
import {
  addCard, autoLayout, connectCards, editableRoute, insertOnLink, libraryOperation, moveCard, nodeMeta, removeLink, updateCard, wireThumbs,
} from "./harnessRouteLogic";

const source = physicalFixture();
const scene = designToScene(source, "drawing");

/** Как «Построить по чертежу» на сервере: заготовки резки, один слепок всего чертежа, финал. */
function built(): RouteV3Document {
  const whole = captureRouteV3Selection(source, scene, scene.map((object) => object.id));
  const doc = appendRouteV3Fragment(createInitialRouteV3(source), { ...whole, id: "whole", title: "Сборка жгута", mode: "source", backgroundOpacity: .2, createdAt: "2026-10-12" });
  return editableRoute(JSON.parse(JSON.stringify(generateRouteV3(doc, source))))!;
}

describe("маршрут позиции на холсте", () => {
  it("reads the server route into the editable form and refuses a damaged one", () => {
    const route = built();
    expect(route.nodes.map((node) => [node.id, node.kind, node.title])).toEqual([
      ["prepared:wire", "semiFinished", "Нарезанные провода"], ["fragment:whole", "assembly", "Сборка жгута"], ["route-v3:final", "final", "Готовый жгут"],
    ]);
    expect(route.nodes.find((node) => node.id === "fragment:whole")?.inputNodeIds).toEqual(["prepared:wire"]);
    expect(editableRoute({ version: 2 })).toBeNull();
  });

  it("inserts a card on a link between its two cards, keeps the composition and moves the rest down", () => {
    const route = built();
    const edge = route.edges.find((item) => item.to === "fragment:whole")!;
    const before = route.nodes.find((node) => node.id === "fragment:whole")!;
    const next = insertOnLink(route, edge.id, "new");
    const card = next.nodes.find((node) => node.id === "new")!;
    expect([card.kind, card.title, card.x, card.y]).toEqual(["assembly", "Сборка 2", 32, 32 + 264 + 64]);
    expect(next.edges.map((item) => `${item.from}>${item.to}`)).toEqual(["fragment:whole>route-v3:final", "prepared:wire>new", "new>fragment:whole"]);
    // Связь от заготовок переносит те же провода, что и раньше, — состав цели не меняется.
    expect(card.refs).toEqual(route.edges.find((item) => item.id === edge.id)!.refs);
    expect(next.nodes.find((node) => node.id === "fragment:whole")!.refs).toEqual(before.refs);
    expect(next.nodes.find((node) => node.id === "fragment:whole")!.y).toBe(before.y + 328);
    expect(next.nodes.find((node) => node.id === "fragment:whole")!.inputNodeIds).toEqual(["new"]);
    expect(parseRouteV3(JSON.parse(JSON.stringify(next)))).not.toBeNull();
  });

  it("removes a link and recounts the composition of its target", () => {
    const route = built();
    const edge = route.edges.find((item) => item.to === "fragment:whole")!;
    const next = removeLink(route, edge.id);
    expect(next.edges.some((item) => item.id === edge.id)).toBe(false);
    expect(next.nodes.find((node) => node.id === "fragment:whole")!.inputNodeIds).toEqual([]);
  });

  it("keeps one final and no link out of it", () => {
    const route = addCard(built(), "extra");
    expect(() => updateCard(route, "extra", { kind: "final" })).toThrow("Финал уже есть — «Готовый жгут»");
    expect(() => connectCards(route, "route-v3:final", "extra")).toThrow("Из финала связь не выходит");
    expect(() => connectCards(route, "route-v3:final", "prepared:wire")).toThrow();
    const renamed = updateCard(route, "extra", { title: "Жгут с маркировкой", quantity: 2 });
    expect(renamed.nodes.find((node) => node.id === "extra")).toMatchObject({ title: "Жгут с маркировкой", quantity: 2, operatorConfirmed: false });
  });

  it("lays the cards out again in rows and moves one card", () => {
    const route = moveCard(built(), "route-v3:final", 900, 40);
    expect(route.nodes.find((node) => node.id === "route-v3:final")).toMatchObject({ x: 900, y: 40 });
    const laid = autoLayout(route);
    expect(laid.nodes.map((node) => [node.x, node.y])).toEqual([[32, 32], [32, 360], [32, 688]]);
  });

  it("takes an operation from the ERP library with its БД.ОП norm and card code", () => {
    const operation = libraryOperation({ id: "t1", code: "PRS_TERMINAL_auto", side: "A", title: "PRS_TERMINAL_auto | Опрессовка терминалов (А)", name: "", category: "Опрессовка", norms: { operation_seconds: "6", setup_seconds: "1800" } });
    expect(operation).toMatchObject({ title: "Опрессовка терминалов (А)", kind: "static", quantity: 1, minutesEach: 0.1, code: "PRS_TERMINAL_auto", templateId: "t1" });
    const route = updateCard(built(), "route-v3:final", { operations: [operation, { ...operation, id: "o2", quantity: 4 }] });
    expect(nodeMeta(route.nodes.find((node) => node.id === "route-v3:final")!)).toBe("6 комплектующих · 1 шт · 0,5 мин");
  });

  it("draws the cut wires «A:1 → B:1» with their colour", () => {
    const route = built();
    const wires = wireThumbs(route, route.nodes[0], buildRouteSourceItems(source));
    expect(wires.map((wire) => wire.label)).toEqual(["A:1 → B:1", "A:2 → C:1", "B:2 → C:2"]);
    expect(wires.every((wire) => /^#[0-9a-f]{6}$/i.test(wire.color))).toBe(true);
  });
});
