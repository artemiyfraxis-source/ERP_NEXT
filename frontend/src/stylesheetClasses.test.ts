// @ts-ignore — типы node в проекте не подключены, таблицы стилей читаются напрямую из src
import { readdirSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

declare const process: { cwd(): string };

// A component stylesheet (purchasingBuy.css, rfq.css …) is global like styles.css: a bare `.name { … }` rule there
// restyles every element with that class on any screen. 23.10 purchasingBuy.css declared `.buy-grid { display: grid }`
// for its supplier cards, and the «В работе» table in «Очереди» (also `.buy-grid`) fell apart into a grid.
const SRC = `${process.cwd()}/src`;
// responsive.css exists to override styles.css; the rest are deliberate shared names, checked by hand.
const OVERRIDES = new Set(["responsive.css"]);
const KNOWN = new Set([".coax-card", ".coax-card-head", ".palette-group"]);

function bareClassRules(css: string): Set<string> {
  const found = new Set<string>();
  for (const match of css.matchAll(/^\s*(\.[A-Za-z][\w-]*)\s*\{/gm)) found.add(match[1]);
  return found;
}

describe("component stylesheets", () => {
  it("do not redefine a class that styles.css already styles", () => {
    const shared = bareClassRules(readFileSync(`${SRC}/styles.css`, "utf8") as string);
    const files = readdirSync(SRC) as string[];
    const clashes = files
      .filter((file) => file.endsWith(".css") && file !== "styles.css" && !OVERRIDES.has(file))
      .flatMap((file) => [...bareClassRules(readFileSync(`${SRC}/${file}`, "utf8") as string)]
        .filter((name) => shared.has(name) && !KNOWN.has(name))
        .map((name) => `${file}: ${name}`));
    expect(clashes).toEqual([]);
  });
});
