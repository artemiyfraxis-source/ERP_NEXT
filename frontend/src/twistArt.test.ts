import { describe, expect, it } from "vitest";
import { twistedPairSvg, twistWire } from "./twistArt";

describe("twistArt — свитая пара", () => {
  it("two wires cross every half pitch and take turns in front", () => {
    const axis = () => 50;
    const a = twistWire(0, 100, axis, 21, 6, 0), b = twistWire(0, 100, axis, 21, 6, 1);
    let crossings = 0;
    a.points.forEach(([, y], k) => { if (k && (a.points[k - 1][1] - b.points[k - 1][1]) * (y - b.points[k][1]) < 0) crossings += 1; });
    // Пять оборотов на 100 при шаге 20 — десять пересечений.
    expect(crossings).toBeGreaterThanOrEqual(9);
    expect(crossings).toBeLessThanOrEqual(10);
    // Провода в противофазе.
    expect(a.points[5][1] - 50).toBeCloseTo(-(b.points[5][1] - 50), 6);
    expect(a.front.length).toBeGreaterThan(2);
    expect(b.front.length).toBeGreaterThan(2);
  });

  it("draws the pair with an outline and the front runs on top, in the colours of the wires", () => {
    const svg = twistedPairSvg(0, 120, 20, 30, 6, ["#ffffff", "#2255cc"]);
    expect(svg).toContain('data-part="twist"');
    expect(svg).toContain('stroke="#ffffff"');
    expect(svg).toContain('stroke="#2255cc"');
    expect((svg.match(/<path/g) ?? []).length).toBeGreaterThan(8);
  });
});
