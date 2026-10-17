// @vitest-environment node
import { expect, it } from "vitest";
import { buildHarnessDocument, planFromGenerator, type ConnectorSource } from "./generatorToHarness";
import { parseHarnessDesignDocument } from "./harness/editor/model";
import { designToScene } from "./harness/editor/HarnessDesignEditor";
import { getDrawingWireStripProfileGeometries } from "./harness/editor/CanvasViewport";
import { collectStripCallouts, stripNotesLine } from "./harness/editor/strip-callouts";
import { DSA, fullHarness } from "./tcGenRealHarness.fixture";

// Длина зачистки читается на чертеже (владелец 17.10): у сростка «зач. 5 мм» один раз на узел, у хвоста «зач. 3 мм, луж.».
it("the drawing scene carries the strip length text: one per splice, one per tinned tail, plus the requirements line", () => {
  const plan = planFromGenerator(DSA, fullHarness());
  const sources = Object.fromEntries(plan.connectors.map((c) => [c.key, { kind: "free", contactCount: null, libraryCode: null } as ConnectorSource]));
  const doc = parseHarnessDesignDocument(JSON.parse(JSON.stringify(buildHarnessDocument(plan, sources).document)));
  const callouts = collectStripCallouts(designToScene(doc, "drawing"));
  const splices = callouts.filter((c) => c.kind === "splice"), tails = callouts.filter((c) => c.kind === "tail");
  expect(splices.map((c) => c.text)).toEqual(["зач. 5 мм", "зач. 5 мм", "зач. 5 мм", "зач. 5 мм"]);
  expect(new Set(splices.map((c) => c.key)).size).toBe(4);
  expect(tails.map((c) => c.text)).toEqual(Array(4).fill("зач. 3 мм, луж."));
  expect(stripNotesLine(callouts)).toBe("Зачистка концов под сросток — 5 мм; хвосты — 3 мм с лужением");
  // Вид «Схема связей» подписей зачистки не несёт.
  expect(collectStripCallouts(designToScene(doc, "e4"))).toEqual([]);
});

// Полоса зачистки — в масштабе чертежа: 3 мм на хвосте в 60 мм — малая доля хвоста; на сростке — лишь метка у узла.
it("strip bands follow the drawing's length scale and the callouts stay clear of wire labels", () => {
  const plan = planFromGenerator(DSA, fullHarness());
  const sources = Object.fromEntries(plan.connectors.map((c) => [c.key, { kind: "free", contactCount: null, libraryCode: null } as ConnectorSource]));
  const doc = parseHarnessDesignDocument(JSON.parse(JSON.stringify(buildHarnessDocument(plan, sources).document)));
  const scene = designToScene(doc, "drawing");
  let tails = 0, splices = 0;
  for (const wire of scene.filter((o) => o.kind === "wire")) {
    const route = (wire.points ?? []).slice(1).reduce((s, p, i) => s + Math.hypot(p.x - wire.points![i]!.x, p.y - wire.points![i]!.y), 0);
    for (const g of getDrawingWireStripProfileGeometries(wire, "drawing")) {
      const tail = (g.end === "from" ? wire.metadata?.openFrom : wire.metadata?.openTo) !== undefined;
      if (tail) { tails += 1; expect(g.totalLength).toBeLessThanOrEqual(Math.max(4, route * (3 / 60)) + 1e-6); expect(g.totalLength).toBeGreaterThanOrEqual(4 - 1e-6); }
      else { splices += 1; expect(g.totalLength).toBeLessThanOrEqual(4 + 1e-6); }
      expect(g.primitives[0]!.polygon.some((p, i, all) => Math.hypot(p.x - all[(i + 1) % 4]!.x, p.y - all[(i + 1) % 4]!.y) > 6)).toBe(false);
    }
  }
  expect([tails, splices]).toEqual([4, 14]);
  const labels = scene.filter((o) => o.kind === "object-index");
  for (const c of collectStripCallouts(scene)) {
    const w = c.text.length * 6, x0 = c.align === "left" ? c.x : c.x - w;
    const y0 = c.baseline === "bottom" ? c.y - 13 : c.baseline === "middle" ? c.y - 6.5 : c.y;
    for (const l of labels) expect(x0 < l.x + l.width && x0 + w > l.x && y0 < l.y + l.height && y0 + 13 > l.y).toBe(false);
  }
});
