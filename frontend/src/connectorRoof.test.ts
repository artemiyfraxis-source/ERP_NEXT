import { describe, expect, it } from "vitest";
import { connectorFace, connectorPicture, topFeature, type ConnectorHousing } from "./connectorArt";
import { connectorModel, hasModel } from "./connectorModel";
import { connectorTopView, hasTopView } from "./connectorTopView";
import { etkDrawHarness, etkDrawPinout } from "./etkDraw";
import catalog from "./fixtures/connectorCatalog.json";

// Каталог серий из backend (connector_catalog(): семейство → образцовый артикул → корпус, в том числе roof с листом-источником).
// Обновить: backend connector_housings.py `connector_catalog()` → этот JSON (одна строка, ensure_ascii=False).
const items = catalog as unknown as Array<{ family: string; title: string; housing: ConnectorHousing }>;
const marks = (svg: string) => (svg.match(/data-top="(latch|slots)"/g) || []).length;
const roofed = items.filter((i) => hasTopView(i.housing) && i.housing.roof);
const plain = items.filter((i) => hasTopView(i.housing) && !i.housing.roof);

// Серии с видом сверху, у которых лист производителя крыши не показал (или не получен): голая крыша, ничего не выдумываем.
// Закрыть серию = найти лист, записать roof в connector_housings.py (ROOF_EXPLICIT) и убрать её отсюда.
const UNSOURCED = [
  "jst-rcy", "jst-rcy-p", "molex-sl-2r", "te-multilock-040", "connfly-ds1071", "amass-xt", "amass-qs8", "molex-milligrid",
  "jst-el", "jst-el-n", "jst-gvh", "jst-lbt", "jst-pnd", "jst-sqs", "jst-vb", "jst-ghd", "jst-znd", "jst-jwpf", "jst-ra",
  "hirose-df13-2r", "hirose-df19", "hirose-df20", "hirose-df61", "hirose-df1b", "hirose-df1b-2r", "hirose-df1b-es",
  "hirose-df1b-des", "hirose-df1b-p2r", "hirose-df1ba-p", "hirose-df1ba-p2r", "hirose-df11", "jae-fix", "jae-fis", "jae-fir",
  "connfly-ds1070", "connfly-ds1072", "connfly-ds1073", "connfly-ds1074", "connfly-ds1071-01", "kls-2-50", "molex-picoezmate",
  "molex-mini50-s", "molex-mini50-u", "hsm-mx508", "hsm-hc", "jr-servo", "samtec-isd2", "molex-microfit-plus", "molex-minilock",
  "molex-sl-1r", "molex-boardin", "deutsch-dt", "te-superseal", "usb-a",
];

describe("крыша корпуса: замок или прорези — только по листу производителя (владелец 17.10)", () => {
  it("covers every catalog series", () => {
    expect(items.length).toBeGreaterThan(100);
  });

  it.each(roofed.map((i) => [i.family, i] as const))("%s: замок/прорези есть на 3D-грани, виде сверху и торце, лист указан", (_family, item) => {
    const hs = item.housing, feature = topFeature(hs), roof = hs.roof!;
    expect(roof.src.trim().length).toBeGreaterThan(5);
    expect(["center", "ends", "contacts", "keys"]).toContain(roof.at);
    expect(feature).toBe(roof.kind);
    const want = `data-top="${feature}"`;
    expect(connectorPicture(hs, 150, 90).body).toContain(want);
    expect(connectorTopView(hs, 200, 140)!.body).toContain(want);
    expect(connectorFace(hs, 0, 0, 24, null).body).toContain(want);
    if (hasModel(hs)) {
      expect(connectorModel(hs, 150, 90)!.body).toContain(want);
      expect(connectorModel(hs, 150, 90, "top")!.body).toContain(want);
    }
  });

  it.each(plain.map((i) => [i.family, i] as const))("%s: листа с крышей нет — крыша голая, замок не выдуман", (_family, item) => {
    const hs = item.housing;
    expect(topFeature(hs)).toBeNull();
    expect(connectorPicture(hs, 150, 90).body).not.toContain("data-top");
    expect(connectorTopView(hs, 200, 140)!.body).not.toContain("data-top");
    expect(connectorFace(hs, 0, 0, 24, null).body).not.toContain("data-top");
  });

  it("keeps the list of series without a sourced roof explicit", () => {
    expect([...new Set(plain.map((i) => i.family))].sort()).toEqual([...new Set(UNSOURCED)].sort());
  });

  it("has no roof for round, lug and D-Sub bodies", () => {
    items.filter((i) => !hasTopView(i.housing)).forEach((i) => expect(topFeature(i.housing)).toBeNull());
  });

  it("gives each series its own feature from its sheet", () => {
    const roof = (family: string) => items.find((i) => i.family === family)!.housing.roof;
    expect(roof("jst-gh")).toMatchObject({ kind: "latch", at: "center" });
    expect(roof("jst-sh")).toMatchObject({ kind: "slots", at: "contacts" });
    expect(roof("jst-suh")).toMatchObject({ kind: "slots", at: "contacts" });
    expect(roof("jst-sm-r")).toMatchObject({ kind: "latch", at: "ends" });
    expect(roof("jst-sm-p")).toMatchObject({ kind: "latch", at: "ends" });
    expect(roof("molex-picolock-20")).toMatchObject({ kind: "latch", at: "ends" });
    expect(roof("molex-zerohachi")).toMatchObject({ kind: "latch", at: "ends" });
    expect(roof("molex-milligrid-151192")).toMatchObject({ kind: "latch", at: "ends" });
    expect(roof("molex-picoblade")).toMatchObject({ kind: "slots", at: "contacts" });
    expect(roof("molex-minispox")).toMatchObject({ kind: "slots", at: "contacts" });
    expect(roof("molex-cgrid3-1r")).toMatchObject({ kind: "slots", at: "keys" });
    expect(roof("hirose-df19")).toBeFalsy();
  });

  it("draws different geometry for different features", () => {
    const by = (family: string) => connectorFace(items.find((i) => i.family === family)!.housing, 0, 0, 24, null).body;
    expect(by("jst-sm-r")).not.toBe(by("jst-pa"));
    expect(by("jst-suh")).not.toBe(by("jst-sh"));
  });
});

describe("эскиз ветви ПФ: торец разъёма несёт признак серии (JST SM)", () => {
  it("shows the lock arms on the SM face", () => {
    const hs = items.find((i) => i.family === "jst-sm-r")!.housing;
    expect(marks(connectorFace(hs, 0, 0, 24, { 1: "красный", 2: "синий" }).body)).toBe(1);
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
