import { describe, expect, it } from "vitest";
import { rfBodySection, type RfBodyRecord, type RfBodySegment } from "./rfBodySection";

// Synthetic millimetre geometry, not a manufacturer's dimensional specification.
const src = "тестовый чертёж";
const segment = (part: RfBodySegment["part"], x_from: number, x_to: number, r_in: number, r_out: number): RfBodySegment =>
  ({ part, x_from, x_to, r_in, r_out, src });
const male: RfBodyRecord = {
  id: "test-male", gender: "male", angle: 0, pin_type: "solder", shield_type: "crimp", cables: ["RG316"],
  segments: [segment("nut", 0, 6, 2.5, 4), segment("body", 2, 11, 1.5, 2.4),
    segment("insulator", 2, 9, .5, 1.5), segment("contact", .76, 10, 0, .5),
    segment("tail", 10, 14, .8, 1.5), segment("ferrule", 12, 18, 1.5, 1.9), segment("gasket", 6, 7, 2.4, 2.5)],
  interface: { depth: { min: .38, max: 1.14, src }, thread: "1/4-36", hex: 8, ref_plane: 0 },
};
const female: RfBodyRecord = {
  ...male, id: "test-female", gender: "female",
  segments: male.segments.filter((s) => s.part !== "nut").map((s) => s.part === "body" ? { ...s, x_from: 0 } :
    s.part === "contact" ? { ...s, x_from: 2, r_in: .25 } : s),
  interface: { ...male.interface, depth: { min: 1.88, max: 2.23, src }, ref_plane: null },
};
const fixture = { series: "TEST", title: "Тестовый разъём", standard: [{ doc: "Тест", fig: "1", url: "" }], bodies: [male, female] };
const svg = (record = male, override?: Parameters<typeof rfBodySection>[1]) => {
  const result = rfBodySection(record, override)!;
  return new DOMParser().parseFromString(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 ${result.height}">${result.body}</svg>`, "image/svg+xml");
};
const partRects = (doc: Document, part: string) => [...doc.querySelectorAll(`g[data-part="${part}"] > rect`)];
const coordinate = (node: Element, attr: string) => Number(node.getAttribute(attr));

describe("rfBodySection", () => {
  it("draws a male pin, coupling nut and internal thread using the shared schema", () => {
    const doc = svg(fixture.bodies[0]);
    expect(doc.querySelector('[data-contact="pin"]')).not.toBeNull();
    expect(doc.querySelector('[data-contact="socket"]')).toBeNull();
    expect(partRects(doc, "nut")).toHaveLength(2);
    expect(doc.querySelector('[data-part="nut"] [data-part="thread"]')).not.toBeNull();
    expect(partRects(doc, "contact")).toHaveLength(1);
    expect(doc.documentElement.textContent).toContain("контакт-штырь");
  });

  it("draws a female socket with an open bore and an external threaded body", () => {
    const doc = svg(fixture.bodies[1]);
    expect(doc.querySelector('[data-contact="socket"]')).not.toBeNull();
    expect(doc.querySelector('[data-part="nut"]')).toBeNull();
    const walls = partRects(doc, "contact");
    expect(walls).toHaveLength(2);
    expect(coordinate(walls[1], "y")).toBeGreaterThan(coordinate(walls[0], "y") + coordinate(walls[0], "height"));
    expect(doc.querySelector('[data-part="body"] [data-part="thread"]')).not.toBeNull();
    expect(doc.documentElement.textContent).toContain("контакт-гнездо");
  });

  it("hatches cut walls, keeps bores clear and draws the ferrule, gasket and cable dielectric", () => {
    const doc = svg();
    for (const part of ["nut", "body", "insulator", "contact", "ferrule", "tail", "gasket"]) {
      expect(doc.querySelectorAll(`[data-part="${part}"] > line`).length).toBeGreaterThan(0);
    }
    expect(partRects(doc, "body")).toHaveLength(2);
    expect(partRects(doc, "cable")).toHaveLength(2);
    const text = doc.documentElement.textContent;
    for (const name of ["гайка", "корпус", "изолятор", "обжимная гильза", "кабель", "уплотнитель"]) expect(text).toContain(name);
  });

  it("uses a single scale for axial and radial dimensions", () => {
    const doc = svg(), nut = partRects(doc, "nut")[0], body = partRects(doc, "body")[0];
    const scale = coordinate(nut, "width") / 6;
    expect(coordinate(body, "width") / 9).toBeCloseTo(scale, 1);
    expect(coordinate(nut, "height") / 1.5).toBeCloseTo(scale, 1);
    expect((coordinate(body, "x") - coordinate(nut, "x")) / 2).toBeCloseTo(scale, 1);
  });

  it("preserves proportions and canvas fit when all dimensions are multiplied", () => {
    const scaled: RfBodyRecord = { ...male, segments: male.segments.map((s) => ({ ...s,
      x_from: s.x_from * 10, x_to: s.x_to * 10, r_in: s.r_in * 10, r_out: s.r_out * 10 })) };
    expect(partRects(svg(scaled), "nut")[0].outerHTML).toBe(partRects(svg(), "nut")[0].outerHTML);
  });

  it("draws the default depth from REF to the actual contact coordinate", () => {
    const doc = svg(), dimension = doc.querySelector('[data-part="contact-depth"]')!;
    expect(dimension.textContent).toBe("заглубление 0,38…1,14 мм");
    expect(doc.documentElement.textContent).toContain("REF");
    const extensions = dimension.querySelectorAll('line[stroke-dasharray="3 3"]');
    expect(coordinate(extensions[0], "x1")).toBe(40);
    expect(coordinate(extensions[1], "x1")).toBe(coordinate(partRects(doc, "contact")[0], "x"));
  });

  it("uses the body face when REF is absent", () => {
    const doc = svg(female);
    expect(doc.documentElement.textContent).toContain("торец");
    expect(doc.documentElement.textContent).toContain("1,88…2,23 мм");
  });

  it("honours a nonzero numeric REF plane rather than the nut face", () => {
    const doc = svg({ ...male, interface: { ...male.interface, ref_plane: 2 } });
    const extension = doc.querySelector('[data-part="contact-depth"] line')!;
    expect(coordinate(extension, "x1")).toBe(coordinate(partRects(doc, "body")[0], "x"));
  });

  it("keeps the depth label centred even when REF and contact are at the right edge", () => {
    const doc = svg({ ...male, segments: [segment("body", 0, 10, .6, 1), segment("contact", 9, 10, 0, .5)],
      interface: { ref_plane: 9, depth: { min: 0, max: 0, src } } });
    const text = doc.querySelector('[data-part="contact-depth"] text')!;
    expect(coordinate(text, "x")).toBe(320);
    expect(text.getAttribute("text-anchor")).toBe("middle");
    expect(text.textContent).toBe("заглубление 0 мм");
  });

  it.each(["0.50-0.90", "0,50…0,90 мм"])("uses end contactDepth override %s", (override) => {
    const result = rfBodySection(male, override)!;
    expect(result.body).toContain("заглубление 0,50…0,90 мм");
    expect(result.body).not.toContain("0,38…1,14");
  });

  it.each([
    ["не менее 2,50", "не менее 2,50 мм"], ["не более 1.75", "не более 1,75 мм"],
    ["9,14 (номинал)", "9,14 мм (номинал)"], [0, "0 мм"],
    [{ min: .4, max: .8, src }, "0,4…0,8 мм"],
    [{ min: null, max: .25, src }, "не более 0,25 мм"],
  ])("accepts supported depth form %j", (override, expected) => {
    expect(rfBodySection(male, override)!.body).toContain(`заглубление ${expected}`);
  });

  it("falls back to the interface depth when the override is empty", () => {
    expect(rfBodySection(male, "")!.body).toContain("0,38…1,14 мм");
  });

  it("omits the depth dimension when neither source contains a value", () => {
    const empty = { ...male, interface: { ...male.interface, depth: { min: null, max: null, src } } };
    expect(rfBodySection(empty, "")!.body).not.toContain('data-part="contact-depth"');
    expect(rfBodySection({ ...empty, interface: {} })!.body).not.toContain("заглубление");
  });

  it("rejects nonfinite and reversed depth values without emitting invalid SVG", () => {
    for (const depth of [{ min: NaN, max: 1, src }, { min: 2, max: 1, src }, { min: null, max: Infinity, src }]) {
      const result = rfBodySection({ ...male, interface: { depth } })!;
      expect(result.body).not.toMatch(/NaN|Infinity|contact-depth/);
    }
  });

  it.each([90, "angled", "R/A", "угловой", "unknown"])("returns null for an unsupported angle %s", (angle) => {
    expect(rfBodySection({ ...male, angle })).toBeNull();
  });

  it("rejects invalid geometry and unknown gender instead of generating NaN", () => {
    for (const changes of [{ x_to: Infinity }, { r_in: -1 }, { r_out: 0 }, { x_from: NaN }, { x_to: 0 }]) {
      expect(rfBodySection({ ...male, segments: [{ ...male.segments[0], ...changes }] })).toBeNull();
    }
    expect(rfBodySection({ ...male, segments: [] })).toBeNull();
    expect(rfBodySection({ ...male, gender: "unknown" })).toBeNull();
  });

  it("keeps all labels at least 12 px on a 640 px canvas with no scaled text", () => {
    const result = rfBodySection(male)!;
    const doc = svg();
    expect(doc.querySelector("parsererror")).toBeNull();
    expect(result.height).toBeGreaterThan(0);
    expect(result.body).not.toMatch(/NaN|Infinity|transform=|url\(#/);
    expect([...doc.querySelectorAll("text")].every((node) => coordinate(node, "font-size") >= 12)).toBe(true);
    for (const node of doc.querySelectorAll("rect")) {
      expect(coordinate(node, "x")).toBeGreaterThanOrEqual(0);
      expect(coordinate(node, "x") + coordinate(node, "width")).toBeLessThanOrEqual(640);
      expect(coordinate(node, "y") + coordinate(node, "height")).toBeLessThan(result.height);
    }
  });

  it("is deterministic and leaves source records untouched", () => {
    const before = JSON.stringify(fixture);
    expect(rfBodySection(male)).toEqual(rfBodySection(male));
    expect(JSON.stringify(fixture)).toBe(before);
  });
});
