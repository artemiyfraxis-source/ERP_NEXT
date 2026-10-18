import { describe, expect, it } from "vitest";
import { connectorFace, connectorFaceUnits, connectorPicture, connectorSummary, pairCavity, topFeature, type ConnectorHousing } from "./connectorArt";
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
const unsourced = plain.filter((i) => !i.housing.roof_plain);

// Серии с видом сверху без крыши. Лист производителя показал голую крышу — housing.roof_plain несёт этот лист
// (connector_housings.py ROOF_EXPLICIT / ROOF_BY_ARTICLE, kind "none"). Остальные — здесь, с причиной: листа нет нигде.
// Закрыть серию = найти лист, записать roof (или kind "none") в connector_housings.py и убрать её отсюда.
// 17.10 (вечер, третий проход): закрыты все серии, у которых лист нашёлся. Остались:
// JST RA — eRA.pdf снят с jst-mfg.com (404), в Web Archive копии нет (CDX пуст);
// Amass QS8 — в каталогах Amass (china-amass.com, 2026) серии QS8 нет, производитель корпуса неизвестен;
// сервоконнектор JR / Futaba — у корпусов цеха нет ни производителя, ни артикула.
const UNSOURCED = ["jst-ra", "amass-qs8", "jr-servo"];

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
    expect([...new Set(unsourced.map((i) => i.family))].sort()).toEqual([...new Set(UNSOURCED)].sort());
  });

  it.each(plain.filter((i) => i.housing.roof_plain).map((i) => [i.family, i] as const))("%s: голая крыша записана по листу производителя", (_family, item) => {
    expect(item.housing.roof_plain!.trim().length).toBeGreaterThan(10);
    expect(topFeature(item.housing)).toBeNull();
  });

  it("has no roof for round, lug and D-Sub bodies", () => {
    items.filter((i) => !hasTopView(i.housing)).forEach((i) => expect(topFeature(i.housing)).toBeNull());
  });

  it("gives each series its own feature from its sheet", () => {
    const roof = (family: string) => items.find((i) => i.family === family)!.housing.roof;
    expect(roof("jst-gh")).toMatchObject({ kind: "latch", at: "center" });
    expect(roof("jst-sh")).toMatchObject({ kind: "slots", at: "contacts" });
    expect(roof("jst-suh")).toMatchObject({ kind: "slots", at: "contacts" });
    expect(roof("jst-sm-r")).toMatchObject({ kind: "latch", at: "center" });
    expect(roof("jst-sm-p")).toMatchObject({ kind: "latch", at: "center" });
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
    expect(roof("hirose-df20")).toMatchObject({ kind: "slots", at: "contacts" });
    expect(roof("hirose-df61")).toMatchObject({ kind: "latch", at: "center" });
    expect(roof("hirose-df1b-p2r")).toMatchObject({ kind: "latch", at: "center" });
    expect(roof("molex-mini50-u")).toMatchObject({ kind: "latch", at: "center" });
    expect(roof("samtec-isd2")).toMatchObject({ kind: "slots", at: "contacts" });
    expect(roof("jae-fix")).toMatchObject({ kind: "latch", at: "ends" });
    expect(roof("deutsch-dt")).toMatchObject({ kind: "latch", at: "center" });
    expect(roof("usb-a")).toMatchObject({ kind: "latch", at: "pair" });
    expect(roof("jst-rcy-p")).toMatchObject({ kind: "slots", at: "contacts" });
    expect(roof("jst-rcy")).toBeFalsy();
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

  it("draws JST SM by eSM: SMR — штыри в кожухе с рычагом замка, SMP — гнёзда, входит, упор замка и плечи в панель", () => {
    const sm = pairs.find((p) => p.series === "JST SM")!, smr = sm.a!, smp = sm.b!;
    expect(smr.art).toBe("SMR-04V-B");
    expect(smr.mate).toMatchObject({ gender: "receptacle", contacts: "pin", shell: "outer", lock: "arm" });
    expect(smp.mate).toMatchObject({ gender: "plug", contacts: "socket", shell: "inner", lock: "catch" });
    expect(smr.mate!.src).toContain("eSM");
    expect(connectorSummary(smr)).toContain("штыри");
    expect(connectorSummary(smp)).toContain("гнёзда");
    const pinout = (hs: ConnectorHousing) => etkDrawPinout({ type: "insTerm", housing: hs, fields: { wires: [{ art: "красный", pins: [1] }] } });
    expect(pinout(smr)).toContain("рычаг замка сверху");
    expect(pinout(smp)).toContain("упор замка сверху, плечи крепления в панель по торцам");
    expect(pinout(smr)).toContain('data-contact="pin"');
    expect(pinout(smp)).toContain('data-contact="socket"');
  });

  it("draws SMR and SMP as different parts by the eSM outlines, not only by the contacts", () => {
    // eSM с. 3 (4 конт.): SMR — кожух 13 × 7,5 × 8 и корпус 10,5 за ним (длина 18,2), рычаг замка на 1,2 над кожухом;
    // SMP — 10,5 × 7,4 × 11,7 и плечи крепления в панель по 3,2 с каждого торца ряда.
    const sm = pairs.find((p) => p.series === "JST SM")!, smr = sm.a!, smp = sm.b!;
    const ratio = (box: { w: number; h: number }) => box.w / box.h;
    const parts = (svg: string) => attr(svg, "part");
    const views = (hs: ConnectorHousing) => ({
      face: connectorFace(hs, 0, 0, 24, null), top: connectorTopView(hs, 200, 140)!,
      iso: connectorModel(hs, 150, 90)!, picture: connectorPicture(hs, 150, 90),
    });
    const r = views(smr), p = views(smp);
    for (const view of ["face", "top", "iso", "picture"] as const) {
      expect(parts(r[view].body)).toEqual(['data-part="hood"']);
      expect(parts(p[view].body)).toEqual(['data-part="wings"']);
      expect(attr(r[view].body, "lock")).toEqual(['data-lock="arm"']);
      expect(attr(p[view].body, "lock")).toEqual(['data-lock="catch"']);
    }
    // Торец: розетка почти квадратная (кожух 13 × 8,7 с рычагом), вилка широкая (16,9 с плечами × 7,4).
    expect(ratio(p.face) / ratio(r.face)).toBeGreaterThan(1.3);
    expect(connectorFaceUnits(smr)).toBeCloseTo(13 / 2.5, 5);
    expect(connectorFaceUnits(smp)).toBeCloseTo((10.5 + 6.4) / 2.5, 5);
    // Вид сверху: розетка вытянута вдоль провода (18,2 × 13), вилка шире своей длины (16,9 × 11,7).
    expect(ratio(r.top)).toBeLessThan(0.9);
    expect(ratio(p.top)).toBeGreaterThan(1.1);
    expect(r.top.body).not.toBe(p.top.body);
  });

  it("draws the SM cavities to scale against the housing and the SMP catch as a solid key (owner 18.10: «маленькие дырки», «у SMP не понятен ключ»)", () => {
    const sm = pairs.find((p) => p.series === "JST SM")!;
    for (const hs of [sm.a!, sm.b!]) {
      const [cw, ch] = pairCavity(hs), pitch = hs.pitch!;
      // Полость — вход обжимного контакта: 0,8 × 1,04 шага (2,0 × 2,6 мм), а не окно штыря 1,0 мм.
      expect(cw / pitch).toBeCloseTo(0.8, 5);
      expect(ch / pitch).toBeCloseTo(1.04, 5);
      expect(cw).toBeGreaterThan(hs.body!.win![0] * 1.5);
      const svg = connectorFace(hs, 0, 0, 44, null).body, k = 44 / pitch;
      const rects = [...svg.matchAll(/<rect x="[\d.-]+" y="[\d.-]+" width="([\d.]+)" height="([\d.]+)" rx="[\d.]+" fill="none" stroke="#(?:aeb7bc|7d878c)"/g)];
      expect(rects).toHaveLength(hs.cols);
      expect(Number(rects[0][1])).toBeCloseTo(cw * k, 1);
      expect(Number(rects[0][2])).toBeCloseTo(ch * k, 1);
    }
    // Ключ SMP виден: сплошной скошенный упор над корпусом (не штриховой контур), у SMR — рычаг с рифлением; оба в <g data-top="latch">.
    const smp = connectorFace(sm.b!, 0, 0, 44, null).body, smr = connectorFace(sm.a!, 0, 0, 44, null).body;
    for (const svg of [smp, smr]) expect(marks(svg)).toBe(1);
    expect(smp).not.toContain("stroke-dasharray");
    expect(smp).toMatch(/data-lock="catch"><path d="M[^"]+ z"/);
    expect(smr).toMatch(/data-lock="arm"><rect/);
  });

  it("draws JST SUH by its end view A × 1.25 (eSUH side view), not by the 3.1 mm front view with lance slots", () => {
    const hs = items.find((i) => i.family === "jst-suh")!.housing;
    expect(hs.body).toMatchObject({ h: 1.25, win: [0.6, 0.8] });
    expect(hs.body!.win_y).toBeUndefined();
    const box = connectorFace(hs, 0, 0, 44, null);
    // Торец плоский: ширина A = (n−1)·0,8 + 1,6 мм при толщине 1,25 мм — в 3 раза шире высоты.
    expect(box.w / (box.h - 7 - 6)).toBeGreaterThan(3);
  });

  it("draws wire-end cavities by the maker drawings: Mini-Fit Jr funnel (SD-5557-003), Connfly DS1070 / DS1072 entries", () => {
    const of = (id: string) => items.find((i) => i.family === id)!.housing;
    const mj = of("molex-minifit-jr");
    // Molex SD-5557-003: воронка 3,6 мм при шаге 4,2 (0,86 шага), отверстие 1,9 × 2,1 со срезанными углами.
    expect(mj.body!.cav).toMatchObject({ kind: "funnel", w: 3.6, h: 3.6, chamfer: 0.7 });
    const funnel = connectorFace(mj, 0, 0, 42, null).body;
    expect(funnel.match(/data-cavity="funnel"/g)).toHaveLength(mj.cols * mj.rows);
    expect(funnel).toMatch(/<polygon points="(?:[\d.-]+,[\d.-]+ ){5}[\d.-]+,[\d.-]+"/); // шестиугольник: два среза
    const side = Number(/data-cavity="funnel"><rect x="[\d.-]+" y="[\d.-]+" width="([\d.]+)"/.exec(funnel)![1]);
    expect(side / 42).toBeCloseTo(3.6 / 4.2, 1);
    // Connfly «Female Housing»: ширина корпуса (N−1)·шаг + extra и вход контакта по листу — крупнее прежнего квадрата 0,8 шага.
    const widths: Record<string, number> = { "connfly-ds1070": 3.2, "connfly-ds1072": 4.24 };
    for (const [id, extra] of Object.entries(widths)) {
      const hs = of(id);
      expect(hs.body!.extra).toBe(extra);
      expect(hs.body!.cav!.kind).toBe("bevel");
      expect(hs.body!.cav!.h).toBeGreaterThan(hs.body!.cav!.w);
      expect(connectorFace(hs, 0, 0, 30, null).body.match(/data-cavity="bevel"/g)).toHaveLength(hs.cols);
    }
  });

  it("marks where the wire-end geometry came from and draws DF11 / DF13 / Micro-Fit by the wire-end photos", () => {
    const of = (id: string) => items.find((i) => i.family === id)!.housing;
    // Серии с нарисованным торцом проводов несут источник: drawing — чертёж производителя, photo — снято с фото детали.
    const sourced: Record<string, "drawing" | "photo"> = {
      "jst-sm-r": "drawing", "jst-sm-p": "drawing", "jst-suh": "drawing", "molex-minifit-jr": "drawing", "molex-minifit-jr-bk": "drawing",
      "connfly-ds1070": "drawing", "connfly-ds1072": "drawing", "hirose-df11": "photo", "hirose-df13": "photo", "molex-microfit": "photo",
    };
    for (const [id, kind] of Object.entries(sourced)) {
      const b = of(id).body!;
      expect(b.geom, id).toBe(kind);
      expect(b.geom_url, id).toMatch(/^https:\/\//);
    }
    // DF11: на торце проводов — крупные квадратные полости ~1,7 мм при шаге 2,0 (а не мелкие входы под штырь 1,2 мм).
    expect(of("hirose-df11").body!.win).toEqual([1.7, 1.7]);
    // DF13: гребёнка высоких пазов, а не квадраты.
    const [w, h] = of("hirose-df13").body!.win as number[];
    expect(h / w).toBeGreaterThan(2.5);
    expect(connectorFace(of("hirose-df13"), 0, 0, 44, null).body).toContain('data-top="latch"');
    // Micro-Fit 43025: полость ~0,8 шага 3,0 мм.
    expect((of("molex-microfit").body!.win as number[])[0] / 3.0).toBeCloseTo(0.8, 5);
  });

  it("every series with a wire-end face names its source: drawing, photo or an equivalent family that has one", () => {
    const of = (id: string) => items.find((i) => i.family === id)!.housing;
    const photo = ["jst-xh", "jst-ph", "jst-vh"];
    const equivalent: Record<string, string> = {
      "connfly-ds1066": "jst-ph", "connfly-ds1069": "jst-xh", "connfly-ds1073": "jst-vh", "connfly-ds1074": "jst-vh",
      "jst-eh": "jst-xh", "jst-zh": "jst-ph", "jst-gh": "jst-ph", "jst-sh": "jst-ph", "jst-nsh": "jst-ph",
      "hirose-df13-2r": "hirose-df13", "hirose-df1b": "jst-xh", "hirose-df1b-2r": "jst-xh",
    };
    for (const id of photo) expect(of(id).body!.geom, id).toBe("photo");
    for (const [id, base] of Object.entries(equivalent)) {
      const b = of(id).body!;
      expect(b.geom, id).toBe("equivalent");
      expect(b.geom_of, id).toBe(base);
      expect(b.geom_url, id).toMatch(/^https:\/\//);
      // эквивалент ссылается на серию, у которой источник «photo» или «drawing»
      expect(["photo", "drawing"]).toContain(of(base).body!.geom);
      // торец проводов нарисован полостями крупнее старых «эскизных» окон каждой серии и в пределах корпуса
      const [w, h] = b.win as number[];
      expect(w).toBeLessThan(of(id).pitch!);
      expect(h).toBeLessThan(b.h);
    }
    // VH: у вилки с рычагом полости в нижней части грани под рычагом, номера читаются на крупных окнах.
    expect(of("jst-vh").body!.win).toEqual([2.8, 3.4]);
    expect(connectorFace(of("jst-vh"), 0, 0, 44, null).body).toContain('data-top="latch"');
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
