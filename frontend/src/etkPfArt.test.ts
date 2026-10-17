import { describe, expect, it } from "vitest";
import { etkWireColor } from "./etkDraw";
import { etkPfArt } from "./etkPfArt";
import type { EtkPfGroup } from "./etkRoute";

function group(extra: Partial<EtkPfGroup> = {}): EtkPfGroup {
  return { key: "b", number: 1, label: "2", name: "ПФ", final: false, ops: [1], components: [], inputs: [], output: [], minutes: 0, ...extra };
}

const cut = { position: 1, type: "cutWire", code: "CUT_WIRE_auto", fields: { wires: [{ name: "ПВАМ 0,5 черный", art: "ПВАМ 0,5 черный", qty: 2, length: 300 }] } };
const semis = [{ name: "Провода нарезаны: ПВАМ 0,5 черный — 1 шт по 1000 мм; ПВАМ 0,5 красный — 1 шт по 980 мм", norm: "10", unit: "шт" }];

describe("etkPfArt — эскиз полуфабриката в схеме ПФ", () => {
  it("draws the cut wires of a cutting ПФ with the drawing of the operation", () => {
    const art = etkPfArt({ group: group(), operations: [cut], document: {} })!;
    expect(art.main && "svg" in art.main ? art.main.svg : "").toContain("ПВАМ 0,5 черный × 2");
    expect(art.layers).toBe("");
  });

  it("draws a splice group as wires joined under the tube of its input, with the count from the harness scheme", () => {
    const ops = [
      { position: 3, type: "insTut", code: "INS_TUT", semis },
      { position: 4, type: "splice", code: "US_WELD", semis },
      { position: 5, type: "heatTut", code: "HEAT_TTK_short", semis },
    ];
    const art = etkPfArt({ group: group({ ops: [3, 4, 5] }), operations: ops, inputs: ["ПВАМ 0,5 черный ×3", "ТТК 4,8/1,6 ×3"],
      document: { topology: { nodes: [{ id: "s", conn: "Сростки ×3", kind: "splice" }] } } })!;
    const svg = art.main && "svg" in art.main ? art.main.svg : "";
    expect(svg).toContain("сросток ×3");
    expect(svg).toContain("под ТТК 4,8/1,6");
    expect(svg).toContain("#d7322d"); // красный провод входа
    // Трубки сростка уже на рисунке — слоями на пучке не повторяются.
    expect(art.layers).toBe("");
  });

  it("puts braid, tape and marking on a bundle strip with the material and its length per piece", () => {
    const ops = [
      { position: 9, type: "insWrap", code: "INS_WRAP", components: [{ art: "Изолента Temflex 1300, черная", norm: "4,45", unit: "М." }] },
      { position: 10, type: "insWrap", code: "INS_BRAID", components: [{ art: "Оплетка XS-3", norm: "10", unit: "М." }] },
      { position: 12, type: "insMark", code: "INS_MARK_HARNESS", fields: { marks: [{ target: "Разъём X1", text: "X1", qty: "1" }] } },
    ];
    const art = etkPfArt({ group: group({ ops: [9, 10, 12] }), operations: ops, document: {}, batch: 10 })!;
    expect(art.main).toBeNull();
    expect(art.layers).toContain("Изолента Temflex 1300");
    expect(art.layers).toContain("445 мм");
    expect(art.layers).toContain("Оплетка XS-3");
    expect(art.layers).toContain(">1 м<");
    expect(art.layers).toContain("url(#pfBraid)");
    // Метка — белой трубкой с надписью по КД.
    expect(art.layers).toContain(">X1<");
  });

  it("draws the product ПФ as the harness scheme and falls back to the technologist's picture when nothing is drawn", () => {
    const topology = { nodes: [{ id: "A", conn: "X1", side: "A", depth: 0 }, { id: "B", conn: "X2", side: "B", depth: 1 }],
      edges: [{ from: "A", to: "B", wires: ["W1"], count: 1, length: 300 }] };
    const final = etkPfArt({ group: group({ final: true }), operations: [{ position: 1, type: "insWrap", code: "INS_WRAP" }], document: { topology } })!;
    expect(final.main && "svg" in final.main ? final.main.svg : "").toContain("X2");
    const plain = { position: 1, type: "other", code: "OTHER" };
    expect(etkPfArt({ group: group(), operations: [plain], document: {}, image: () => "/sketch.png" })?.main).toEqual({ image: "/sketch.png" });
    expect(etkPfArt({ group: group(), operations: [plain], document: {} })).toBeNull();
  });

  it("draws a branch ПФ as the connector with its wires, the twisted pair, the braid with its length, the ТТК and the marking text", () => {
    const housing = { art: "174047-2", title: "TE Multilock 040", pitch: 2.5, rows: 1, cols: 4, n: 4, kind: "grid", color: "#2b2b2b",
      grid: [[1, 2, 3, 4]] };
    const operations = [
      { position: 1, type: "insTerm", code: "INS_terminal_1.00", connector: "174047-2", connector_article: "174047-2", housing,
        fields: { wires: [{ name: "ПВАМ 0,35 оранжевый", art: "ПВАМ 0,35 оранжевый", pins: [1] }, { name: "ПВАМ 0,35 белый", art: "ПВАМ 0,35 белый", pins: [2] }] } },
      { position: 2, type: "twist", code: "TWIST", fields: { twist: { pitch: "25", pairs: "1) ПВАМ 0,35 оранжевый + ПВАМ 0,35 белый", count: 1 } } },
      { position: 3, type: "insWrap", code: "INS_BRAID", components: [{ art: "Оплетка XS-3", norm: "10", unit: "М." }] },
      { position: 4, type: "insTut", code: "INS_TUT", components: [] },
      { position: 5, type: "insTut", code: "INS_TUT", components: [] },
      { position: 6, type: "heatTut", code: "HEAT_TTK_short", components: [] },
    ];
    const config = { ops: [
      { type: "insTerm", branch: "X4 «A6»" }, { type: "twist" },
      { type: "insWrap", wrapKind: "braid", wrapArt: "XS-3", wrapLen: 450 },
      { type: "insTut", tutArt: "ТТК 12/4", label: "Надеть ТТК X4 «A6» — ТТК 12/4" },
      { type: "insTut", markTube: true, label: "Надеть маркировку X4 «A6» — «A6»" },
      { type: "heatTut", tutArt: "ТТК 12/4", label: "Усадка ТТК на разъёме X4 «A6» — ТТК 12/4" },
    ] };
    const art = etkPfArt({ group: group({ ops: [1, 2, 3, 4, 5, 6] }), operations, document: { operations, build_config: config }, batch: 10 })!;
    const svg = art.main && "svg" in art.main ? art.main.svg : "";
    expect(svg).toContain('data-part="connector"');
    expect(svg).toContain("X4 «A6» · 174047-2");
    expect(svg).toContain('data-part="braid"');
    expect(svg).toContain(">XS-3<");
    expect(svg).toContain(">450 мм<");
    expect(svg).toContain('data-part="sleeve"');
    expect(svg).toContain(">ТТК 12/4<");
    expect(svg).toContain("усажена на разъёме");
    expect(svg).toContain('data-part="mark"');
    expect(svg).toContain(">A6<");
    expect(svg).toContain('data-part="twist"');
    // Разъём — видом сверху из базы (прорези вверх), провода уходят вправо.
    expect(svg).toContain('data-view="top"');
    expect(svg).toContain("вид сверху, провода справа, прорези вверх");
    // Оплётка начинается у тыла разъёма и идёт под ТТК; метка — поверх оплётки, после ТТК.
    const at = (part: string) => svg.indexOf(`data-part="${part}"`);
    expect(at("connector")).toBeLessThan(at("braid"));
    expect(at("braid")).toBeLessThan(at("sleeve"));
    expect(at("sleeve")).toBeLessThan(at("mark"));
    const x = (part: string) => Number(new RegExp(`data-part="${part}" x="([\\d.]+)"`).exec(svg)![1]);
    const w = (part: string) => Number(new RegExp(`data-part="${part}" x="[\\d.]+" y="[\\d.-]+" width="([\\d.]+)"`).exec(svg)![1]);
    expect(x("braid")).toBeLessThanOrEqual(x("sleeve") + 12); // нет зазора между разъёмом и оплёткой
    expect(x("mark")).toBeGreaterThanOrEqual(x("sleeve") + w("sleeve"));
    expect(x("mark") + w("mark")).toBeLessThanOrEqual(x("braid") + w("braid"));
    // Свивка внутри оплётки: рисуется под её сеткой, отдельного участка и подписи «шаг» после неё нет.
    expect(svg).toContain("свивка, шаг 25 мм");
    expect(svg.match(/data-part="twist"/g)).toHaveLength(1);
    expect(svg.indexOf('data-part="twist"')).toBeLessThan(at("braid"));
    expect(svg).not.toContain(">шаг 25 мм<");
    // Всё на одном рисунке — полосы слоёв под ним нет.
    expect(art.layers).toBe("");
    // Без свивки — ни свитой пары, ни её подписи.
    const plain = etkPfArt({ group: group({ ops: [1, 3, 4, 5, 6] }), operations: operations.filter((op) => op.position !== 2),
      document: { operations, build_config: config } })!;
    const second = plain.main && "svg" in plain.main ? plain.main.svg : "";
    expect(second).not.toContain('data-part="twist"');
    expect(second).toContain(">A6<");
    // Без оплётки свивка — отдельным участком со своей подписью; без ТТК и метки оплётка смыкается с разъёмом.
    const noBraid = etkPfArt({ group: group({ ops: [1, 2] }), operations: operations.slice(0, 2), document: { operations: operations.slice(0, 2), build_config: config } })!;
    const third = noBraid.main && "svg" in noBraid.main ? noBraid.main.svg : "";
    expect(third).toContain(">шаг 25 мм<");
    expect(third).not.toContain("внутри оплётки");
    // Серия без вида сверху — торец, как был.
    const round = { ...housing, kind: "round", grid: [[1, 2, 3, 4]] };
    const roundOps = [{ ...operations[0], housing: round }, operations[2]];
    const faced = etkPfArt({ group: group({ ops: [1, 3] }), operations: roundOps, document: {} })!;
    const fsvg = faced.main && "svg" in faced.main ? faced.main.svg : "";
    expect(fsvg).toContain('data-part="connector"');
    if (!fsvg.includes('data-view="top"')) expect(fsvg).toContain("вид со стороны проводов");
    // Монтаж в разъём без ничего на проводах — рисунок монтажа как был.
    const bare = etkPfArt({ group: group(), operations: [operations[0]], document: {} })!;
    expect(bare.main && "svg" in bare.main ? bare.main.svg : "").not.toContain('data-part="connector"');
  });

  // 713К.4 КБ1, ветвь X2 «I3»: JST SM на 2, пара красный/чёрный с шагом 20 мм, оплётка XP-3 110 мм, ТТК 8/2, метка «I3».
  const sm2 = { art: "SMR-02V-B", family: "jst-sm-r", title: "JST SM (розетка)", pitch: 2.5, rows: 1, cols: 2, n: 2, kind: "grid",
    color: "#2b2b2b", latch: "lever", cavity: "square", grid: [[1, 2]], roof: { kind: "latch", at: "ends" } };
  const i3 = (wires: Array<{ art: string; pins: number[] }>, pairs: string, wrapLen: unknown = 110) => {
    const operations = [
      { position: 1, type: "insTerm", code: "INS_terminal", connector: "X2", connector_article: "SMR-02V-B", housing: sm2, fields: { wires } },
      { position: 2, type: "twist", code: "TWIST", fields: { twist: { pitch: "20", pairs } } },
      { position: 3, type: "insWrap", code: "INS_BRAID", components: [{ art: "Оплетка XP-3", norm: "1,1", unit: "М." }] },
      { position: 4, type: "insTut", code: "INS_TUT", components: [] },
      { position: 5, type: "insTut", code: "INS_TUT", components: [] },
      { position: 6, type: "heatTut", code: "HEAT_TTK_short", components: [] },
    ];
    const config = { ops: [
      { type: "insTerm", branch: "X2 «I3»" }, { type: "twist" },
      { type: "insWrap", wrapKind: "braid", wrapArt: "XP-3", wrapLen },
      { type: "insTut", tutArt: "ТТК 8/2" }, { type: "insTut", markTube: true, label: "Надеть маркировку X2 «I3» — «I3»" },
      { type: "heatTut", tutArt: "ТТК 8/2" },
    ] };
    const art = etkPfArt({ group: group({ ops: [1, 2, 3, 4, 5, 6] }), operations, document: { operations, build_config: config }, batch: 10 })!;
    return art.main && "svg" in art.main ? art.main.svg : "";
  };
  const pair = [{ art: "МГТФ 0,35 красный", pins: [1] }, { art: "МГТФ 0,35 черный", pins: [2] }];
  const attr = (svg: string, part: string, name: string) => Number(svg.match(new RegExp(`data-part="${part}"[^>]*?\\s${name}="([\\d.]+)"`))?.[1]);

  it("draws the finished branch: braid from the connector under the ТТК, marking over the braid, twist to the braid's scale", () => {
    const svg = i3(pair, "1) МГТФ 0,35 красный + МГТФ 0,35 черный");
    // Разъём — видом сверху, с защёлкой серии.
    expect(svg).toContain('data-view="top"');
    expect(svg).toContain("защёлка сверху");
    const braid = { x: attr(svg, "braid", "x"), w: attr(svg, "braid", "width") };
    const sleeve = { x: attr(svg, "sleeve", "x"), w: attr(svg, "sleeve", "width") };
    const mark = { x: attr(svg, "mark", "x"), w: attr(svg, "mark", "width") };
    // Оплётка начинается под ТТК: её начало внутри трубки, а трубка заходит на разъём.
    expect(braid.x).toBeGreaterThanOrEqual(sleeve.x);
    expect(braid.x).toBeLessThan(sleeve.x + sleeve.w);
    expect(svg.indexOf('data-part="sleeve"')).toBeGreaterThan(svg.indexOf('data-part="braid"'));
    // Метка — поверх оплётки, за ТТК, а не в зазоре между ними.
    expect(mark.x).toBeGreaterThanOrEqual(sleeve.x + sleeve.w);
    expect(mark.x + mark.w).toBeLessThan(braid.x + braid.w);
    expect(svg.indexOf('data-part="mark"')).toBeGreaterThan(svg.indexOf('data-part="braid"'));
    // Свивка под сеткой — винтом в цветах пары, 110 / 20 = 5,5 витка на длине оплётки, и выходит из неё на конце.
    expect(svg.indexOf('data-part="twist"')).toBeLessThan(svg.indexOf('data-part="braid"'));
    expect(attr(svg, "twist", "data-turns")).toBeCloseTo(110 / 20, 1);
    expect(attr(svg, "twist", "data-pitch-px")).toBeCloseTo(braid.w / 5.5, 0);
    const xs = [...svg.matchAll(/[ML]([\d.]+),[\d.]+/g)].map((match) => Number(match[1]));
    expect(Math.max(...xs)).toBeGreaterThan(braid.x + braid.w + 30);
    expect(svg).toContain("#d7322d");
    expect(svg).toContain("url(#pfMesh)");
    expect(svg).toContain("свивка, шаг 20 мм");
    for (const text of [">110 мм<", ">ТТК 8/2<", "усажена на разъёме", ">I3<", ">XP-3<"]) expect(svg).toContain(text);
    // Длина оплётки в метрах — тот же масштаб.
    expect(attr(i3(pair, "1) МГТФ 0,35 красный + МГТФ 0,35 черный", "0,11 м"), "twist", "data-turns")).toBeCloseTo(5.5, 1);
    // Очень частый шаг на длинной оплётке не сливается в полосу: виток не мельче 22 px.
    const dense = i3(pair, "1) МГТФ 0,35 красный + МГТФ 0,35 черный", 2000);
    expect(attr(dense, "twist", "data-pitch-px")).toBeGreaterThanOrEqual(22);
    // Редкий шаг на короткой оплётке — в масштабе, без потолка в 110 px.
    expect(attr(i3(pair, "1) МГТФ 0,35 красный + МГТФ 0,35 черный", 50), "twist", "data-turns")).toBeCloseTo(50 / 20, 1);
  });

  it("draws two pairs as two thin helices and the wires out of a pair straight", () => {
    const wires = [
      { art: "МГТФ 0,35 красный", pins: [1] }, { art: "МГТФ 0,35 черный", pins: [2] },
      { art: "МГТФ 0,35 белый", pins: [3] }, { art: "МГТФ 0,35 синий", pins: [4] }, { art: "МГТФ 0,35 зеленый", pins: [5] },
    ];
    const svg = i3(wires, "1) МГТФ 0,35 красный + МГТФ 0,35 черный\n2) МГТФ 0,35 белый + МГТФ 0,35 синий");
    expect(svg.match(/data-part="twist"/g)).toHaveLength(1);
    expect(svg).toContain("свивка ×2, шаг 20 мм");
    const twist = svg.slice(svg.indexOf('data-part="twist"'), svg.indexOf("</g>", svg.indexOf('data-part="twist"')));
    // Две пары по два провода, каждый целиком и участками «спереди».
    expect(twist.match(/stroke-width="3.2"/g)!.length).toBeGreaterThanOrEqual(4);
    // Зелёный провод не в паре — прямой жилой пучка, не в винте.
    const green = etkWireColor("МГТФ 0,35 зеленый");
    expect(twist).not.toContain(green);
    expect(svg).toMatch(new RegExp(`<line [^>]*stroke="${green}"`));
  });
});
