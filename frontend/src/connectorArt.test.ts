import { describe, expect, it } from "vitest";
import { connectorFace, connectorFaceUnits, connectorLook, connectorPicture, connectorSummary, shade } from "./connectorArt";
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

  it("draws JST SH by the JST drawing: B × 2.8 body with protrusions, ▽ over contact 1 on the right", () => {
    const sh = { ...gh, art: "SHR-02V-S-B", family: "jst-sh", title: "JST SH", pitch: 1.0, cols: 2, n: 2, latch: "mark", grid: [[2, 1]],
      body: { extra: 2.0, h: 2.8, rear_h: 2.1, len: 5.0, front_len: 1.8, ear_len: 1.0, win: [0.6, 1.3], ears: 1.0 } };
    const face = connectorFace(sh, 0, 0, 40, { 1: "черный" });
    expect(face.w).toBeCloseTo(5.0 * 40, 5);
    expect(face.body.match(/<rect/g)?.length).toBe(3 + 2);
    expect(connectorFaceUnits(sh)).toBe(5);
    const arrow = face.body.match(/fill="#d7322d"/g);
    expect(arrow?.length).toBe(1);
    const picture = connectorPicture(sh, 150, 90);
    expect(picture.w).toBeLessThanOrEqual(152);
  });

  it("draws GH, PH and the two-row Micro-Fit by their drawings: lock on top, windows below, contact 1 marked", () => {
    const ghBody = { ...gh, body: { extra: 2.5, h: 2.85, len: 5.7, lock_inset: 2.5, lock_h: 1.3, win: [0.7, 1.2] } };
    const face = connectorFace(ghBody, 0, 0, 30, null);
    expect(face.w).toBeCloseTo(6.25 * 30 / 1.25, 5);
    expect(face.body.match(/<rect/g)?.length).toBe(1 + 1 + 4);
    expect(face.body.match(/fill="#d7322d"/g)?.length).toBe(1);
    expect(connectorPicture(ghBody, 150, 90).w).toBeLessThanOrEqual(152);
    const ph = { ...gh, art: "PHR-4", family: "jst-ph", pitch: 2.0, latch: "windows", body: { extra: 3.8, h: 4.5, len: 6.85, win: [1.0, 1.6] } };
    expect(connectorFace(ph, 0, 0, 30, null).body.match(/<rect/g)?.length).toBe(1 + 4 + 4);
    const microfit = { ...gh, art: "43025-0600", family: "molex-microfit", pitch: 3.0, rows: 2, cols: 3, n: 6, latch: "lever", color: "#2b2b2b",
      grid: [[6, 5, 4], [3, 2, 1]], body: { extra: 3.85, h: 6.86, len: 14, lock_w: 3.86, lock_h: 1.4, win: [2.2, 2.2] } };
    const mf = connectorFace(microfit, 0, 0, 30, { 1: "красный" });
    expect(mf.w).toBeCloseTo(9.85 * 10, 5);
    expect(mf.body.match(/<rect/g)?.length).toBe(1 + 1 + 6);
    expect(connectorFaceUnits(microfit)).toBeCloseTo(9.85 / 3, 5);
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
