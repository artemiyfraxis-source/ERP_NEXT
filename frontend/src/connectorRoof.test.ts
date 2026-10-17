import { describe, expect, it } from "vitest";
import { connectorFace, connectorPicture, topFeature, type ConnectorHousing } from "./connectorArt";
import { connectorModel, hasModel } from "./connectorModel";
import { connectorTopView, hasTopView } from "./connectorTopView";
import { etkDrawHarness, etkDrawPinout } from "./etkDraw";
import catalog from "./fixtures/connectorCatalog.json";

// Каталог серий из backend (connector_catalog(): семейство → образцовый артикул → корпус). Обновить: см. connector_housings.py.
const items = catalog as unknown as Array<{ family: string; title: string; housing: ConnectorHousing }>;
const marks = (svg: string) => (svg.match(/data-top="(latch|slots)"/g) || []).length;

describe("крыша корпуса: замок или прорези (владелец 17.10)", () => {
  it("covers every catalog series", () => {
    expect(items.length).toBeGreaterThan(100);
  });

  it.each(items.map((i) => [i.family, i] as const))("%s: 3D-грань, вид сверху и торец показывают замок или прорези", (_family, item) => {
    const hs = item.housing, feature = topFeature(hs);
    if (!hasTopView(hs)) { expect(feature).toBeNull(); return; } // круглые, лепестки, D-Sub — крыши нет
    expect(feature).not.toBeNull();
    const want = `data-top="${feature}"`;
    expect(connectorPicture(hs, 150, 90).body).toContain(want);
    expect(connectorTopView(hs, 200, 140)!.body).toContain(want);
    expect(connectorFace(hs, 0, 0, 24, null).body).toContain(want);
    if (hasModel(hs)) {
      expect(connectorModel(hs, 150, 90)!.body).toContain(want);
      expect(connectorModel(hs, 150, 90, "top")!.body).toContain(want);
    }
  });

  it("gives a real latch to the series that have one and slots to the plain ones", () => {
    const by = (family: string) => topFeature(items.find((i) => i.family === family)!.housing);
    expect(by("molex-picolock-20")).toBe("latch");
    expect(by("hirose-df19")).toBe("latch");
    expect(by("jst-gh")).toBe("latch");
    expect(by("jst-suh")).toBe("slots");
    expect(by("molex-picoblade")).toBe("slots");
    expect(by("molex-sl-2r")).toBe("slots");
  });
});

describe("эскиз «Монтаж в разъём»: два вида, без 3D", () => {
  const hs = items.find((i) => i.family === "jst-suh")!.housing;
  const op = { type: "insTerm", housing: hs, fields: { wires: [{ art: "синий", pins: [1] }] } };

  it("draws exactly the wire face and the top view, never the perspective box", () => {
    const svg = etkDrawPinout(op);
    expect(svg).toContain("вид сверху, провода снизу");
    expect(marks(svg)).toBe(2);
    expect(svg).not.toContain(connectorPicture(hs, 150, 110, { 1: "синий" }).body);
  });

  it("draws the harness scheme with flat views too", () => {
    const svg = etkDrawHarness({ nodes: [{ id: "a", conn: "X1", depth: 0, housing: hs }, { id: "b", conn: "X2", depth: 1, housing: hs }],
      edges: [{ from: "a", to: "b", wires: ["синий"], count: 1 }] } as never);
    expect(svg).not.toContain(connectorPicture(hs, 156, 72, null).body);
    expect(marks(svg)).toBe(2);
  });
});
