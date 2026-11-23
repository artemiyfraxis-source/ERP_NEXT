import { describe, expect, it } from "vitest";
import { coaxAssemblyDrawing } from "./etkPfCoaxArt";

const parse = (markup: string) => new DOMParser().parseFromString(markup, "image/svg+xml");

describe("assembly connector captions", () => {
  it.each([
    ["FAKRA-Female (тип Z)", "FAKRA-Female R/A (тип Z)"],
    ["Очень длинное наименование разъёма для стороны А с дополнительными данными", "Ш".repeat(120)],
    ["连接器🔌".repeat(24), "<Разъём & тип>"],
  ])("keeps both complete captions in separate columns: %s", (a, b) => {
    const document = parse(coaxAssemblyDrawing({ connectorType: a }, { connectorType: b }, "4855", "ТУТ 4/2", "RTK 031"));
    expect(document.querySelector("parsererror")).toBeNull();
    const captions = [...document.querySelectorAll("[data-coax-caption]")];
    expect(captions).toHaveLength(2);
    expect(captions.map((caption) => caption.getAttribute("aria-label"))).toEqual([a, b]);
    const [left, right] = captions;
    expect(Number(left.getAttribute("x")) + Number(left.getAttribute("width"))).toBeLessThan(Number(right.getAttribute("x")));
    for (const caption of captions) {
      const texts = [...caption.querySelectorAll("text")];
      expect(texts.map((text) => text.textContent).join("").replace(/\s/g, "")).toBe(caption.getAttribute("aria-label")!.replace(/\s/g, ""));
      texts.forEach((text, index) => {
        expect(Number(text.getAttribute("textLength"))).toBeLessThanOrEqual(Number(caption.getAttribute("width")));
        expect(text.getAttribute("lengthAdjust")).toBe("spacingAndGlyphs");
        if (index) expect(Number(text.getAttribute("y")) - Number(texts[index - 1].getAttribute("y"))).toBeGreaterThan(Number(text.getAttribute("font-size")));
      });
      const lastBaseline = Number(texts.at(-1)!.getAttribute("y"));
      const connectorY = Number(document.querySelector("g[transform]")!.getAttribute("transform")!.match(/translate\([\d.]+,([\d.]+)\)/)![1]);
      expect(connectorY - 10).toBeGreaterThan(lastBaseline + 8);
    }
    expect(document.documentElement.getAttribute("viewBox")!.split(" ").map(Number)[3]).toBeGreaterThan(Number(captions[0].getAttribute("height")) + 70);
  });

  it("keeps the cable length independent of caption layout", () => {
    const a = { type: "FAKRA-female (тип Z)" }, b = { type: "FAKRA-female R/A (тип Z)" };
    const short = parse(coaxAssemblyDrawing(a, b, "50", "", "RTK 031"));
    const long = parse(coaxAssemblyDrawing(a, b, "4855", "", "RTK 031"));
    expect(short.documentElement.getAttribute("viewBox")).toBe(long.documentElement.getAttribute("viewBox"));
    expect(long.documentElement.textContent).toContain("L = 4855 мм");
  });
});
