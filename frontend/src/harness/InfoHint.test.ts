// @vitest-environment node
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { InfoHint, infoHintPosition } from "./InfoHint";

describe("shared InfoHint", () => {
  it("uses the native top layer with an accessible description", () => {
    const html = renderToStaticMarkup(createElement(InfoHint, { children: "Справка размеров" }));
    expect(html).toContain('popover="manual"');
    expect(html).toContain('role="tooltip"');
    expect(html).toContain(`aria-describedby="${/role="tooltip" id="([^"]+)"/.exec(html)![1]}"`);
  });
  it("keeps a hint in the viewport at the right and bottom edges", () => {
    const p = infoHintPosition({left: 1260, top: 680, bottom: 700}, {width: 290, height: 240}, {width: 1280, height: 720});
    expect(p.left).toBe(982);
    expect(p.top).toBe(434);
    expect(p.left + 290).toBeLessThanOrEqual(1272);
    expect(p.top + 240).toBeLessThan(680);
  });
  it("places a top-edge hint below and caps long content to available space", () => {
    expect(infoHintPosition({left: 1, top: 8, bottom: 28}, {width: 290, height: 900}, {width: 320, height: 480}))
      .toEqual({left: 8, top: 34, maxHeight: 438});
  });
});
