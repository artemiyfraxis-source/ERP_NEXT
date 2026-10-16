import { describe, expect, it } from "vitest";
import { connectorTopView, hasTopView } from "./connectorTopView";
import { housingCatalogItems, filterHousingItems } from "./harness/editor/editor-reference-catalog";
import { housingTopViewSvg } from "./harness/editor/HarnessDesignEditor";
import type { ConnectorHousing } from "./connectorArt";
import type { ConnectorSeries } from "./api";

const picoblade: ConnectorHousing = { art: "51021-0400", family: "molex-picoblade", title: "Molex PicoBlade", pitch: 1.25, rows: 1, cols: 4, n: 4,
  kind: "grid", color: "#efe6cf", latch: "windows", cavity: "square", labels: null, grid: [[1, 2, 3, 4]], round: null };
const gh: ConnectorHousing = { ...picoblade, family: "jst-gh", latch: "ramp",
  body: { extra: 2.5, h: 2.85, len: 5.7, lock_inset: 2.5, lock_h: 1.3, win: [0.7, 1.2] } };
const minifit: ConnectorHousing = { ...picoblade, family: "molex-minifit-jr", pitch: 4.2, rows: 2, cols: 2, n: 4, latch: "lever", grid: [[4, 3], [2, 1]] };
const round: ConnectorHousing = { ...picoblade, kind: "round", cols: 1, grid: [[1]], round: [{ pin: 1, x: 0, y: 0 }] };

describe("connectorTopView — вид сверху корпуса", () => {
  it("shows pitch, A, B, contact 1 and the wire entry at the bottom", () => {
    const view = connectorTopView(picoblade, 300, 200)!;
    expect(view.body).toContain(">1,25</text>");
    expect(view.body).toContain("A 3,75");
    expect(view.body).toContain("Конт. 1");
    expect(view.body).toContain("вход проводов");
    expect(view.body.match(/<text/g)?.length).toBe(5);
  });

  it("marks B as approximate unless the body comes from the maker's drawing", () => {
    expect(connectorTopView(picoblade, 300, 200)!.body).toContain("B ≈");
    const exact = connectorTopView(gh, 300, 200)!;
    expect(exact.estimated).toBe(false);
    expect(exact.body).toContain("B 6,25");
    expect(exact.body).not.toContain("B ≈");
    expect(exact.depthMm).toBe(5.7);
  });

  it("scales width with pitch × columns", () => {
    expect(connectorTopView({ ...picoblade, cols: 8, n: 8, grid: [[1, 2, 3, 4, 5, 6, 7, 8]] }, 400, 200)!.widthMm)
      .toBeGreaterThan(connectorTopView(picoblade, 400, 200)!.widthMm);
  });

  it("points to the lower row when contact 1 is hidden under the upper row", () => {
    expect(connectorTopView(minifit, 300, 200)!.body).toContain("нижний ряд");
  });

  it("has a plain drawing without dimensions for list thumbnails", () => {
    const thumb = connectorTopView(picoblade, 92, 56, false)!;
    expect(thumb.body).not.toContain("<text");
    expect(thumb.w).toBeLessThanOrEqual(92 + 1);
  });

  it("gives no top view to round housings", () => {
    expect(hasTopView(round)).toBe(false);
    expect(connectorTopView(round, 200, 100)).toBeNull();
  });
});

describe("вид сверху в редакторе чертежей", () => {
  const series = [{ title: "JST GH", variants: [{ family: "jst-gh", title: "GH", housings: [
    { article: "GHR-04V-S", n: 4, housing: gh, headers: [] }, { article: "GHR-04V-S", n: 4, housing: gh, headers: [] },
    { article: "ROUND-1", n: 1, housing: round, headers: [] }] }] }] as unknown as ConnectorSeries[];

  it("lists housings once, only those with a top view, as draggable free connectors", () => {
    const items = housingCatalogItems(series);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ title: "GHR-04V-S", placement: "connector", templateKind: "free", defaultPartNumber: "GHR-04V-S" });
    expect(items[0]!.subtitle).toBe("JST GH · 4 конт. · шаг 1,25 мм");
    expect(filterHousingItems(items, "ghr")).toHaveLength(1);
    expect(filterHousingItems(items, "xyz")).toHaveLength(0);
  });

  it("drops the same top view as an SVG picture", () => {
    const svg = housingTopViewSvg(gh)!;
    expect(svg.startsWith("<svg xmlns=")).toBe(true);
    expect(svg).toContain("B 6,25");
    expect(housingTopViewSvg(round)).toBeNull();
  });
});
