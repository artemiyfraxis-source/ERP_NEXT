import { describe, expect, it } from "vitest";
import { connectorFace, connectorPicture, connectorSummary, topFeature, type ConnectorHousing } from "./connectorArt";
import { connectorModel, hasModel } from "./connectorModel";
import { connectorTopView, hasTopView } from "./connectorTopView";
import { etkDrawHarness, etkDrawPinout } from "./etkDraw";
import catalog from "./fixtures/connectorCatalog.json";
import pairData from "./fixtures/connectorPairs.json";

// Каталог серий из backend (connector_catalog(): семейство → образцовый артикул → корпус, в том числе roof с листом-источником).
// Обновить: backend connector_housings.py `connector_catalog()` → этот JSON (одна строка, ensure_ascii=False).
const items = catalog as unknown as Array<{ family: string; title: string; housing: ConnectorHousing }>;
const marks = (svg: string) => (svg.match(/data-top="(latch|slots)"/g) || []).length;
const roofed = items.filter((i) => hasTopView(i.housing) && i.housing.roof);
const plain = items.filter((i) => hasTopView(i.housing) && !i.housing.roof);

// Серии с видом сверху, у которых лист производителя крыши не показал (или не получен): голая крыша, ничего не выдумываем.
// Закрыть серию = найти лист, записать roof в connector_housings.py (ROOF_EXPLICIT) и убрать её отсюда.
// Почему ещё без крыши (17.10, второй проход): JST RCY, RA — на листе окно/ничего не видно (RA: eRA недоступен; JWPF T —
// окно замка по eJWPF с. 2, закрыт третьим проходом);
// Hirose DF13-2R, DF11, DF1B (гнездо, 2 ряда, ES, DES), DF1BA, DF1B-DEP — по каталогу замок на обратной грани, на крыше только метка ▽;
// DF20 — на листе у колодки замка нет (металл только у вилки на плате), DF61 — «swing lock» без ясной формы крыши;
// JAE FI-X (в серии и FI-X30H без замка, и FI-X30HL), FI-S («friction lock» без формы); Connfly DS1070/DS1071/-01/1072/1074 —
// на чертеже корпус гладкий (DS1070 — лист на вилку); Molex SL 2 ряда, Board-in, Milli-Grid 51110 (замки только у 14–32 цепей),
// Mini50 негерметичный 34791 — листа с видом нет; HSM MX (Y/N: ребро есть не у всех), HSM HC и Samtec ISD2 (M/S: у S винты) —
// серия смешивает исполнения; Deutsch DT, TE Superseal — te.com не отдаёт лист, в Web Archive его нет; JR servo — листа
// производителя нет; USB-A, Amass XT/QS8 — замка на крыше нет, прорезей по листу тоже.
const UNSOURCED = [
  "jst-rcy", "jst-rcy-p", "molex-sl-2r", "connfly-ds1071", "amass-xt", "amass-qs8", "molex-milligrid",
  "jst-ra", "hirose-df13-2r", "hirose-df20", "hirose-df61", "hirose-df1b", "hirose-df1b-2r", "hirose-df1b-es",
  "hirose-df1b-des", "hirose-df1b-p2r", "hirose-df1ba-p", "hirose-df1ba-p2r", "hirose-df11", "jae-fix", "jae-fis",
  "connfly-ds1070", "connfly-ds1072", "connfly-ds1074", "connfly-ds1071-01", "molex-mini50-u", "hsm-mx508", "hsm-hc",
  "jr-servo", "samtec-isd2", "molex-boardin", "deutsch-dt", "te-superseal", "usb-a",
];

describe("крыша корпуса: замок или прорези — только по листу производителя (владелец 17.10)", () => {
  it("covers every catalog series", () => {
    expect(items.length).toBeGreaterThan(100);
  });

  it.each(roofed.map((i) => [i.family, i] as const))("%s: замок/прорези есть на 3D-грани, виде сверху и торце, лист указан", (_family, item) => {
    const hs = item.housing, feature = topFeature(hs), roof = hs.roof!;
    expect(roof.src.trim().length).toBeGreaterThan(5);
    expect(["center", "ends", "pair", "contacts", "keys"]).toContain(roof.at);
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
    expect(roof("jst-el")).toMatchObject({ kind: "latch", at: "center" });
    expect(roof("jst-lbt")).toMatchObject({ kind: "latch", at: "ends" });
    expect(roof("jae-fir")).toMatchObject({ kind: "latch", at: "ends" });
    expect(roof("hirose-df19")).toMatchObject({ kind: "latch", at: "ends" });
    expect(roof("molex-microfit-plus")).toMatchObject({ kind: "latch", at: "center" });
    expect(roof("molex-picoezmate")).toMatchObject({ kind: "slots", at: "contacts" });
    expect(roof("kls-2-50")).toMatchObject({ kind: "slots", at: "contacts" });
    expect(roof("hirose-df20")).toBeFalsy();
  });

  it("draws different geometry for different features", () => {
    const by = (family: string) => connectorFace(items.find((i) => i.family === family)!.housing, 0, 0, 24, null).body;
    expect(by("jst-sm-r")).not.toBe(by("jst-pa"));
    expect(by("jst-suh")).not.toBe(by("jst-sh"));
  });
});

// Пары «вилка — розетка» из backend (connector_housings.py `mating_pairs()` → connectorPairs.json, обновлять вместе с
// connectorCatalog.json): обе половины одной серии, у каждой — сторона пары по листу производителя (mate.src).
const pairs = pairData as unknown as Array<{ series: string; a: ConnectorHousing | null; b: ConnectorHousing | null }>;
const attr = (svg: string, name: string) => [...new Set((svg.match(new RegExp(`data-${name}="([a-z]+)"`, "g")) || []))];

describe("вилка и розетка одной серии нарисованы по-разному — по листу производителя (владелец 17.10)", () => {
  it("covers the wire-to-wire pairs of the catalogue", () => {
    expect(pairs.map((p) => p.series)).toEqual(expect.arrayContaining(["JST SM", "JST RCY", "JST EL", "JST JWPF",
      "Molex PicoBlade", "Molex Micro-Fit 3.0 (2 ряда)", "Molex Micro-Fit 3.0 (1 ряд)", "Molex Nano-Fit", "Hirose DF1B", "D-Sub"]));
  });

  it.each(pairs.map((p) => [p.series, p] as const))("%s: штыри и гнёзда, кожух и входящий корпус, плечо и упор — разные, лист указан", (_series, pair) => {
    const a = pair.a!, b = pair.b!;
    expect(a.mate && b.mate).toBeTruthy();
    [a, b].forEach((hs) => expect(hs.mate!.src.trim().length).toBeGreaterThan(5));
    expect(a.mate!.contacts).not.toBe(b.mate!.contacts);
    for (const hs of [a, b]) {
      const want = `data-contact="${hs.mate!.contacts}"`;
      const face = connectorFace(hs, 0, 0, 24, null).body, picture = connectorPicture(hs, 150, 90).body;
      expect(attr(face, "contact")).toEqual([want]);
      expect(attr(picture, "contact")).toEqual([want]);
      const top = connectorTopView(hs, 200, 140);
      if (top) expect(attr(top.body, "contact")).toEqual([want]);
      if (hs.mate!.shell) expect(picture).toContain(`data-shell="${hs.mate!.shell}"`);
      if (hs.mate!.shell === "outer") expect(face).toContain('data-shell="outer"');
      if (hs.mate!.lock && hs.roof?.kind === "latch") {
        expect(attr(face, "lock")).toEqual([`data-lock="${hs.mate!.lock}"`]);
        expect(attr(picture, "lock")).toEqual([`data-lock="${hs.mate!.lock}"`]);
      }
    }
    if (a.mate!.shell || b.mate!.shell) expect(a.mate!.shell).not.toBe(b.mate!.shell);
    if (a.mate!.lock || b.mate!.lock) expect(a.mate!.lock).not.toBe(b.mate!.lock);
    expect(connectorFace(a, 0, 0, 24, null).body).not.toBe(connectorFace(b, 0, 0, 24, null).body);
    expect(connectorPicture(a, 150, 90).body).not.toBe(connectorPicture(b, 150, 90).body);
  });

  it("draws JST SM by eSM: SMR — штыри в кожухе с упорами на торцах, SMP — гнёзда, входит, плечи с зацепами", () => {
    const sm = pairs.find((p) => p.series === "JST SM")!, smr = sm.a!, smp = sm.b!;
    expect(smr.art).toBe("SMR-04V-B");
    expect(smr.mate).toMatchObject({ gender: "receptacle", contacts: "pin", shell: "outer", lock: "catch" });
    expect(smp.mate).toMatchObject({ gender: "plug", contacts: "socket", shell: "inner", lock: "arm" });
    expect(smr.mate!.src).toContain("eSM");
    expect(connectorSummary(smr)).toContain("штыри");
    expect(connectorSummary(smp)).toContain("гнёзда");
    const pinout = (hs: ConnectorHousing) => etkDrawPinout({ type: "insTerm", housing: hs, fields: { wires: [{ art: "красный", pins: [1] }] } });
    expect(pinout(smr)).toContain("упоры замка по торцам сверху");
    expect(pinout(smp)).toContain("замок-плечи по торцам сверху");
    expect(pinout(smr)).toContain('data-contact="pin"');
    expect(pinout(smp)).toContain('data-contact="socket"');
  });

  it("draws Molex Micro-Fit by the Molex sheets: 43025 — гнёзда и защёлка, 43020 — штыри в кожухе с упором", () => {
    const mf = pairs.find((p) => p.series === "Molex Micro-Fit 3.0 (2 ряда)")!;
    expect(mf.a!.mate).toMatchObject({ gender: "receptacle", contacts: "socket", shell: "inner", lock: "arm" });
    expect(mf.b!.mate).toMatchObject({ gender: "plug", contacts: "pin", shell: "outer", lock: "catch" });
  });

  it("keeps the drawing of a series without a mating side as it was", () => {
    const gh = items.find((i) => i.family === "jst-gh")!.housing;
    expect(gh.mate).toBeFalsy();
    expect(connectorFace(gh, 0, 0, 24, null).body).not.toMatch(/data-(contact|shell|lock)/);
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
