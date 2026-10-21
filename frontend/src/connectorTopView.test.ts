import { describe, expect, it } from "vitest";
import { connectorTopView, hasTopView } from "./connectorTopView";
import { housingCatalogItems, filterHousingItems } from "./harness/editor/editor-reference-catalog";
import { housingTopViewSvg } from "./harness/editor/HarnessDesignEditor";
import { connectorPicture, type ConnectorHousing } from "./connectorArt";
import type { ConnectorSeries } from "./api";

const picoblade: ConnectorHousing = { art: "51021-0400", family: "molex-picoblade", title: "Molex PicoBlade", pitch: 1.25, rows: 1, cols: 4, n: 4,
  kind: "grid", color: "#efe6cf", latch: "windows", cavity: "square", labels: null, grid: [[1, 2, 3, 4]], round: null };
const gh: ConnectorHousing = { ...picoblade, family: "jst-gh", latch: "ramp",
  body: { extra: 2.5, h: 2.85, len: 5.7, lock_inset: 2.5, lock_h: 1.3, win: [0.7, 1.2] } };
const minifit: ConnectorHousing = { ...picoblade, family: "molex-minifit-jr", pitch: 4.2, rows: 2, cols: 2, n: 4, latch: "lever", grid: [[4, 3], [2, 1]] };
const round: ConnectorHousing = { ...picoblade, kind: "round", cols: 1, grid: [[1]], round: [{ pin: 1, x: 0, y: 0 }] };

describe("connectorTopView — объёмный корпус, развёрнутый видом сверху", () => {
  it("is the housing picture seen from above: roof with the latch, the wire face with the contacts at the bottom", () => {
    const top = connectorTopView(gh, 200, 140)!, iso = connectorPicture(gh, 200, 140)!;
    expect(top.body).not.toBe(iso.body);
    expect(top.body).toContain("<polygon");
    expect(top.body).not.toContain("<text"); // без размеров и подписей — только сам корпус
    expect(top.w).toBeLessThanOrEqual(201);
    expect(top.h).toBeLessThanOrEqual(141);
  });

  it("paints the wire colours on the contacts of the wire face", () => {
    expect(connectorTopView(picoblade, 200, 140, { 1: "красный" })!.body).toContain("#d7322d");
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
    expect(svg).toContain("<polygon");
    expect(housingTopViewSvg(round)).toBeNull();
  });
});
