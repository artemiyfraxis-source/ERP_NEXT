import { describe, expect, it } from "vitest";
import { connectorFace, connectorLook, connectorPicture, connectorSummary, shade } from "./connectorArt";
import { harnessPins } from "./ProjectEtk";

const gh = { art: "GHR-04V-S", family: "jst-gh", title: "JST GH", pitch: 1.25, rows: 1, cols: 4, n: 4, kind: "grid",
  color: "#efe6cf", latch: "ramp", cavity: "square", labels: null, grid: [[1, 2, 3, 4]], round: null };
const dsub = { art: "DS1033-09F", family: "dsub", title: "D-Sub", pitch: 2.77, rows: 2, cols: 9, n: 9, kind: "dsub",
  color: "#c9ccd0", latch: "none", cavity: "square", labels: null, male: false,
  grid: [[1, 0, 2, 0, 3, 0, 4, 0, 5], [0, 6, 0, 7, 0, 8, 0, 9, 0]], round: null };

describe("connectorArt — рисунки серий", () => {
  it("draws the wire side with numbers and wire colours in the occupied cavities", () => {
    const face = connectorFace(gh, 0, 0, 30, { 1: "МГТФ красный", 3: "МГТФ черный" });
    expect(face.body).toContain('fill="#d7322d"');
    expect(face.body).toContain(">4</text>");
    expect(face.w).toBeCloseTo((4 * 1.25 + 2 * connectorLook(gh).wall) * 30 / 1.25, 5);
  });

  it("keeps real proportions: a 4.2 mm Mini-Fit is wider than a 1.0 mm SH at the same pixels per mm", () => {
    const sh = { ...gh, family: "jst-sh", pitch: 1.0, latch: "mark" };
    const minifit = { ...gh, family: "molex-minifit-jr", pitch: 4.2, latch: "lever" };
    expect(connectorFace(minifit, 0, 0, 4.2 * 8, null).w).toBeGreaterThan(connectorFace(sh, 0, 0, 1.0 * 8, null).w * 3);
  });

  it("draws the D-Sub as a shell with two staggered rows and a 3D picture for the scheme", () => {
    const face = connectorFace(dsub, 0, 0, 24, null);
    expect(face.body.match(/<circle/g)?.length).toBe(9);
    const picture = connectorPicture(dsub, 150, 90);
    expect(picture.w).toBeLessThanOrEqual(152);
    expect(picture.body).toContain("<polygon");
    expect(connectorSummary(dsub)).toBe("D-Sub · 2,77 мм · 9 конт. · гнёзда");
  });

  it("shades faces lighter and darker", () => {
    expect(shade("#808080", 0.5)).toBe("#c0c0c0");
    expect(shade("#808080", -0.5)).toBe("#404040");
  });

  it("collects the pinout of mounting operations per scheme node", () => {
    expect(harnessPins([
      { connector_article: "GHR-04V-S", side: "A", fields: { wires: [{ art: "красный", pins: [1, 3] }, { art: "черный", pins: [2] }] } },
      { connector_article: "GHR-04V-S", side: "B", fields: { wires: [{ art: "красный", pins: [] }] } },
    ])).toEqual({ "GHR-04V-S::A": { 1: "красный", 3: "красный", 2: "черный" } });
  });
});
