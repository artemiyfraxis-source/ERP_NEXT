// @vitest-environment node
import { expect, it } from "vitest";
import { buildHarnessDocument, planFromGenerator, type ConnectorSource } from "./generatorToHarness";
import { parseHarnessDesignDocument } from "./harness/editor/model";
import { designToScene } from "./harness/editor/HarnessDesignEditor";
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
