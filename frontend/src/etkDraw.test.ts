import { describe, expect, it } from "vitest";
import {
  etkParams, etkConnectionRows, etkCutEnds, etkDrawCoax, etkDrawCrimp, etkDrawHarness, etkDrawPinout, etkDrawTwist, etkDrawWires, etkFigure,
  etkTolerance, etkWireColor,
} from "./etkDraw";
import { buildPrintDocumentHtml } from "./print";
import { etkPrintPages } from "./etkPrint";
import type { ProjectTechcard } from "./api";

const housing = { title: "JST XH", art: "XHP-2", rows: 1, cols: 2, n: 2, kind: "grid", color: "#efe6cf", latch: "ramp",
  cavity: "square", labels: null, grid: [[1, 2]], round: null, conf: "medium", note: "", pitch: 2.5 };

describe("etkParams (шапка: Сторона А | Общие | Сторона В)", () => {
  const find = (items: Array<[string, string]>) => Object.fromEntries(items);

  it("fills both side columns of the crimp-both operation", () => {
    const params = etkParams({
      type: "cutWireCrimp", code: "CUT_WIRE+2crimp", fields: {
        lengthKd: "300мм", cutTolerance: "±5", strip: { a: true, b: true, lenA: "1,7", lenB: "2,5" },
        crimpEnds: [
          { ends: ["A"], art: "SSHL-002T-P0.2", wire: "UL1061", section: "30 AWG", applicator: "A4", crimpHeight: "0,44 - 0,48", pullForce: "5", strip: "1,7" },
          { ends: ["B"], art: "SXH-001T", wire: "UL1061", section: "30 AWG", applicator: "A2", crimpHeight: "0,60 - 0,75", pullForce: "9.8 - 39.2", strip: "2,5" },
        ],
      }, times: { operation_seconds: "4", setup_seconds: "3600" },
    });
    expect(params.sides.map((side) => side.title)).toEqual(["Сторона А", "Сторона В"]);
    expect(find(params.sides[0].items)).toEqual({
      "Терминал": "SSHL-002T-P0.2", "Аппликатор": "A4", "Высота обжима": "0,44–0,48 мм", "Отрыв": "≥ 5 Н", "Зачистка": "1,7 мм" });
    expect(find(params.sides[1].items)).toMatchObject({ "Терминал": "SXH-001T", "Аппликатор": "A2", "Отрыв": "9,8–39,2 Н", "Зачистка": "2,5 мм" });
    expect(find(params.common)).toEqual({
      "Длина по КД": "300мм", "Допуск реза": "±5 мм", "Внешний диаметр": "—", "Сечение жилы": "30 AWG", "Время": "4 с на шт. · подгот. 60 мин" });
  });

  it("jacketed cable on the rotor and at the connector: the jacket and the cores, two lines per side (owner 15.10)", () => {
    const strip = { a: true, b: true, lenA: "20", lenB: "20", coreA: "3,5", coreB: "" };
    const rotor = etkParams({ type: "cutWireStrip", code: "CUT_COAX+2strip", fields: { strip, wires: [{ name: "UL2575 26AWG", length: "400" }] } });
    expect(find(rotor.sides[0].items)).toEqual({ "Разделка оболочки": "20 мм", "Зачистка жил": "3,5 мм" });
    expect(find(rotor.sides[1].items)).toEqual({ "Разделка оболочки": "20 мм", "Зачистка жил": "не задано" });
    const byHand = etkParams({ type: "stripCable", code: "STRIP_CABLE", side: "A", fields: { strip: { a: true, b: false, lenA: "20", lenB: "", coreA: "3,5" } } });
    expect(byHand.sides.map((side) => [side.title, find(side.items)])).toEqual([["Сторона А", { "Разделка оболочки": "20 мм", "Зачистка жил": "3,5 мм" }]]);
    // Эскиз резки: у конца — оболочка и жилы.
    const svg = etkDrawWires({ type: "cutWireStrip", code: "CUT_COAX+2strip", fields: { strip, wires: [{ name: "UL2575 26AWG", length: "400" }] } });
    expect(svg).toContain("A 20 мм, жилы 3,5 мм");
    expect(svg).toContain("B 20 мм, жилы ?");
  });

  it("one-side crimp: side В has only the strip length, a free end without strip has no column", () => {
    const fields = { applicator: "A19", strip: { a: true, b: true, lenA: "1,2", lenB: "3" },
      crimpEnds: [{ ends: ["A"], art: "SSH-003T-P0.2-H", wire: "W", section: "28 AWG", applicator: "A19", crimpHeight: "0,43 – 0,47", pullForce: "", strip: "1,2" }] };
    const params = etkParams({ type: "cutWireCrimp", code: "CUT_WIRE+1crimp", side: "A", fields });
    expect(params.sides.map((side) => side.title)).toEqual(["Сторона А", "Сторона В"]);
    expect(params.sides[1].items).toEqual([["Зачистка", "3 мм"]]);
    expect(find(params.sides[0].items)).toMatchObject({ "Высота обжима": "0,43–0,47 мм", "Отрыв": "не задано" });
    const bare = etkParams({ type: "cutWireCrimp", code: "CUT_WIRE+1crimp", side: "A", fields: { ...fields, strip: { a: true, b: false, lenA: "1,2", lenB: "" } } });
    expect(bare.sides.map((side) => side.title)).toEqual(["Сторона А"]);
  });

  it("marks the tinned end and keeps side В of an issued ETK without lengths as «не задано»", () => {
    const issued = crimpTin({ strip: { a: true, b: false, lenA: "1,2", lenB: "" }, cutTolerance: "(+/-)1мм" });
    const params = etkParams(issued);
    expect(find(params.sides[1].items)).toEqual({ "Зачистка": "не задано (лужение)" });
    expect(find(params.common)["Допуск реза"]).toBe("±1 мм");
    expect(find(etkParams(crimpTin()).common)["Допуск реза"]).toBe("±3 мм");
  });

  it("reads wire diameters when the data has them", () => {
    const params = etkParams({ type: "cutWire", fields: { wires: [{ art: "МГТФ", outerDiameter: "1.2", conductorDiameter: "0.5" }] } });
    expect(find(params.common)).toMatchObject({ "Внешний диаметр": "1,2 мм", "Диаметр жилы": "0,5 мм" });
  });

  it("an operation without sides shows only «Общие»; legacy crimp fields stay there", () => {
    const params = etkParams({ type: "insMark", fields: { perItem: "2", params: "L = 300 мм", note: "из бухты" }, times: { operation_seconds: "6", setup_seconds: "120" } });
    expect(params.sides).toEqual([]);
    expect(params.common).toEqual([["На изделие", "2 шт."], ["Параметры", "L = 300 мм"], ["Примечание", "из бухты"], ["Время", "6 с на шт. · подгот. 2 мин"]]);
    expect(etkParams({ type: "prsTerm", fields: { applicator: "AP-1", crimpHeight: "" } }).common.slice(0, 2))
      .toEqual([["Аппликатор", "AP-1"], ["Высота обжима", "не задано"]]);
  });
});

/** 719К.2 КБ1, операция 1: резка 104 мм, сторона А обжата SSH-003T-P0.2-H на автомате, конец В облужен. */
function crimpTin(fields: Record<string, unknown> = {}, op: Record<string, unknown> = {}) {
  return {
    type: "cutWireCrimp", code: "CUT_WIRE+1crimp+tin", side: "A", connector: "обжим + лужение",
    components: [
      { art: "Силиконовый 28AWG черный", name: "Силиконовый 28AWG черный", norm: "10,4", unit: "М." },
      { art: "SSH-003T-P0.2-H", name: "Терминал", norm: "100", unit: "Шт." },
    ],
    fields: {
      cutTolerance: "±3 мм", lengthKd: "104мм", applicator: "A19", crimpHeight: "0,43 – 0,47",
      strip: { a: true, b: true, lenA: "1,2", lenB: "3" },
      wires: [{ name: "Силиконовый 28AWG черный", art: "Силиконовый 28AWG черный", qty: 2, length: 104, pins: [] }],
      ...fields,
    },
    ...op,
  };
}

describe("etkDrawWires — ends of the cut wire per automat combination", () => {
  const kinds = (operation: Record<string, unknown>) => {
    const ends = etkCutEnds(operation);
    return [ends.a.kind, ends.b.kind];
  };
  const marks = (svg: string) => [...svg.matchAll(/data-end="([AB]:\w+)"/g)].map((m) => m[1]);

  it("crimp A + tin B: terminal on A, tinned B with the tin strip length, L between wire tips", () => {
    const op = crimpTin();
    expect(etkCutEnds(op)).toEqual({
      a: { kind: "crimp", len: "1,2", terminal: "SSH-003T-P0.2-H" },
      b: { kind: "tin", len: "3", terminal: "" },
    });
    const svg = etkDrawWires(op);
    expect(new Set(marks(svg))).toEqual(new Set(["A:crimp", "B:tin"]));
    expect(svg).toContain("A 1,2 мм");
    expect(svg).toContain("B 3 мм");
    expect(svg).not.toContain("B ?");
    expect(svg).toContain("L = 104 мм ±3 мм");
    expect(svg).toContain("обжат терминал SSH-003T-P0.2-H");
    expect(svg).toContain(">лужение<");
    expect(svg).toContain("#c9ced3"); // припой, а не медь на конце В
  });

  it("crimp A only: terminal on A, B as the strip field says", () => {
    const bare = crimpTin({ strip: { a: true, b: false, lenA: "3,5", lenB: "" } }, { code: "CUT_WIRE+1crimp", connector: "обжим" });
    expect(kinds(bare)).toEqual(["crimp", "none"]);
    expect(marks(etkDrawWires(bare))).toEqual(["A:crimp"]);
    const stripped = crimpTin({ strip: { a: true, b: true, lenA: "3,5", lenB: "5" } }, { code: "CUT_WIRE+1crimp" });
    expect(kinds(stripped)).toEqual(["crimp", "strip"]);
    expect(etkDrawWires(stripped)).toContain("B 5 мм");
    expect(etkDrawWires(stripped)).not.toContain("лужение");
  });

  it("crimp B side and crimp A+B: the terminal is drawn on the crimped side(s)", () => {
    expect(kinds(crimpTin({}, { code: "CUT_WIRE+1crimp", side: "B" }))).toEqual(["strip", "crimp"]);
    const both = crimpTin({}, { code: "CUT_WIRE+2crimp" });
    expect(kinds(both)).toEqual(["crimp", "crimp"]);
    expect(new Set(marks(etkDrawWires(both)))).toEqual(new Set(["A:crimp", "B:crimp"]));
  });

  it("tin both and strip only", () => {
    const tinBoth = { type: "cutWireStrip", code: "CUT_WIRE+2tin", fields: {
      strip: { a: true, b: true, lenA: "3", lenB: "3" }, wires: [{ art: "ПВ3", qty: 1, length: 300 }] } };
    expect(kinds(tinBoth)).toEqual(["tin", "tin"]);
    expect(new Set(marks(etkDrawWires(tinBoth)))).toEqual(new Set(["A:tin", "B:tin"]));
    const strip = { type: "cutWireStrip", code: "CUT_WIRE+2strip", fields: {
      strip: { a: true, b: true, lenA: "5", lenB: "5" }, wires: [{ art: "ПВ3", qty: 1, length: 300 }] } };
    expect(kinds(strip)).toEqual(["strip", "strip"]);
    const svg = etkDrawWires(strip);
    expect(new Set(marks(svg))).toEqual(new Set(["A:strip", "B:strip"]));
    expect(svg).not.toContain("лужение");
    expect(svg).not.toContain("обжат");
    expect(kinds({ type: "cutWire", code: "CUT_WIRE_auto", fields: { wires: [{ art: "ПВ3" }] } })).toEqual(["none", "none"]);
  });
});

describe("etkDraw* sketches", () => {
  it("colours wires by name and draws cut wires with the length dimension", () => {
    expect(etkWireColor("ПВ3 красный")).toBe("#d7322d");
    expect(etkWireColor("ПВ3")).toBe("#6b7c86");
    const svg = etkDrawWires({ type: "cutWire", fields: { cutTolerance: "±5", strip: { a: true, lenA: "6" },
      wires: [{ art: "ПВ3 <черный>", qty: 2, length: 300 }] } });
    expect(svg).toContain("<svg");
    expect(svg).toContain("L = 300 мм ±5 мм");
    expect(svg).toContain("A 6 мм");
    expect(etkTolerance("(+/-)1мм")).toBe("±1 мм");
    expect(etkTolerance("±0.5")).toBe("±0,5 мм");
    expect(etkTolerance("+0,5/-1 мм")).toBe("+0,5/-1 мм");
    expect(svg).toContain("ПВ3 &lt;черный&gt; × 2");
    expect(etkDrawWires({ type: "prsTerm", fields: { wires: [{ art: "x" }] } })).toBe("");
  });

  it("draws the coax strip steps or says the base has no sizes", () => {
    expect(etkDrawCoax({ fields: { coax: { cable: "RG-174", l1: "3", l2: "5", l3: "9", d1: "0,5" } } })).toContain("L3 = 9 мм");
    expect(etkDrawCoax({ fields: { coax: { connector: "SMA", l2: "5" } } })).toContain("Размеров разделки нет в базе коакса для SMA");
    expect(etkDrawCoax({ fields: { coax: { cable: "RG-174", l1: "3", l2: "5", l3: "9" } } })).not.toContain("Сторона А");
  });

  it("draws any combination of L1–L3 and leaves out the absent layers and their dimensions", () => {
    const draw = (l: Record<string, string>) => etkDrawCoax({ fields: { coax: { cable: "RG-316", ...l } } });
    const bands = (svg: string) => [...svg.matchAll(/data-band="(\w+)"/g)].map((m) => m[1]).sort();
    const full = draw({ l1: "3", l2: "6", l3: "9" });
    expect(bands(full)).toEqual(["braid", "conductor", "dielectric", "jacket"]);
    ["L1 = 3 мм", "L2 = 6 мм", "L3 = 9 мм"].forEach((t) => expect(full).toContain(t));
    const clamp = draw({ l1: "3", l2: "6", l3: "" });
    expect(bands(clamp)).toEqual(["braid", "conductor", "jacket"]);
    expect(clamp).toContain("L2 = 6 мм");
    expect(clamp).not.toContain("L3 =");
    const twist = draw({ l1: "3", l2: "", l3: "9" });
    expect(bands(twist)).toEqual(["conductor", "dielectric", "jacket"]);
    expect(twist).toContain("L3 = 9 мм");
    expect(twist).not.toContain("L2 =");
    const only = draw({ l1: "3" });
    expect(bands(only)).toEqual(["conductor", "jacket"]);
    expect(only).toContain("L1 = 3 мм");
    expect(only).not.toMatch(/L[23] =/);
    expect(draw({ l1: "", l2: "6" })).toContain("Размеров разделки нет");
    const flush = draw({ l3: "21,25" });
    expect(bands(flush)).toEqual(["braid", "jacket"]);
    expect(flush).toContain("L3 = 21,25 мм");
    expect(flush).toContain("заподлицо с торцом");
    expect(flush).not.toMatch(/L[12] =/);
  });

  it("draws both ends of a cut with stripping on the machine, each with its side", () => {
    const drawn = etkDrawCoax({ type: "coaxCut", fields: {
      coax: { cable: "RG-316", connector: "MMCX", l1: "1,8", l2: "4,5", l3: "10" },
      coaxB: { cable: "RG-316", connector: "N", l1: "3", l2: "6", l3: "9" },
    } });
    expect(drawn.match(/<svg/g)).toHaveLength(2);
    expect(drawn).toContain("Сторона А");
    expect(drawn).toContain("Сторона В");
    expect(drawn).toContain("L3 = 10 мм");
    expect(drawn).toContain("L3 = 9 мм");
  });

  it("draws the crimp only for prsTerm with the crimp height field", () => {
    expect(etkDrawCrimp({ type: "prsTerm", fields: { crimpHeight: "", pullForce: "40" } })).toContain("⚠ нет в БД.ТЕР");
    expect(etkDrawCrimp({ type: "prsTerm", fields: {} })).toBe("");
  });

  it("draws the pinout, the housing op and the twist", () => {
    expect(etkDrawPinout({ type: "prsTerm", connector: "XS1", fields: { pinout: true, wires: [{ art: "красный", pins: [2] }] } }))
      .toContain("конт. 2 — красный");
    expect(etkDrawPinout({ type: "insTerm", housing, fields: { wires: [{ art: "синий", pins: [1] }] } }))
      .toContain("XHP-2 · JST XH, шаг 2,5 мм · вид со стороны проводов, защёлка сверху");
    expect(etkDrawTwist({ fields: { twist: { pitch: "20", pairs: "1) красный + черный" } } })).toContain("шаг 20 мм");
    // Order of renderEtk_: prsTerm never takes the pinout, it takes the crimp.
    expect(etkFigure({ type: "prsTerm", fields: { crimpHeight: "1", pinout: true, wires: [{ art: "a", pins: [1] }] } })).toContain("обжим по жиле");
  });

  it("shows the other end of each wire in the housing legend and lists the connections", () => {
    const links = [{ pin: 3, wire: "черный", dir: "from", peer: { end: "А1", pin: 1 } }, { pin: 1, wire: "черный", dir: "from", peer: { end: "А1", pin: 2 } }];
    const svg = etkDrawPinout({ type: "insTerm", housing, fields: { wires: [{ art: "черный", pins: [3, 1] }], links } });
    expect(svg).toContain("конт. 1 — черный <tspan font-weight=\"700\" fill=\"#00554f\">← А1 конт. 2</tspan>");
    expect(svg).toContain("конт. 3 — черный <tspan font-weight=\"700\" fill=\"#00554f\">← А1 конт. 1</tspan>");
    expect(etkConnectionRows([
      { wire: "черный", from: { end: "А1", conn: "SHR-06V-S-B", label: "", pin: 1 }, to: { end: "В1", conn: "SHR-06V-S-B", label: "GIMBAL", pin: 3 } },
      "мусор",
    ])).toEqual([["1", "черный", "А1 · SHR-06V-S-B", "1", "В1 GIMBAL · SHR-06V-S-B", "3"]]);
    expect(etkConnectionRows(undefined)).toEqual([]);
  });

  it("draws the harness scheme only from two nodes", () => {
    expect(etkDrawHarness({ nodes: [{ id: "A", conn: "A" }], edges: [] })).toBe("");
    const svg = etkDrawHarness({
      nodes: [{ id: "XHP-2", conn: "XHP-2", side: "A", depth: 0, housing }, { id: "Сростки", conn: "Сростки ×1", kind: "splice", depth: 1 }],
      edges: [{ from: "XHP-2", to: "Сростки", wires: ["красный"], count: 2, length: 300 }],
    });
    expect(svg).toContain("2 пров. · 300 мм");
    expect(svg).toContain(">JST XH · 2,5 мм<");
    expect(svg).toContain(">2 конт. · сторона А<");
  });
});

describe("etkPrint with sketches (etkPrintHtml_)", () => {
  it("puts the harness on the cover and each operation's sketch on its page", () => {
    const card = { id: "t", project: "P-1", item_index: "01", product_name: "Жгут", revision: 1, document: {
      topology: { nodes: [{ id: "A", conn: "A", depth: 0 }, { id: "B", conn: "B", depth: 1 }], edges: [{ from: "A", to: "B", wires: ["x"], count: 1, length: 0 }] },
      operations: [{ position: 1, code: "CUT_WIRE_auto", type: "cutWire", fields: { wires: [{ art: "ПВ3", qty: 1, length: 100 }] } }],
    } } as unknown as ProjectTechcard;
    const html = buildPrintDocumentHtml("ТК", etkPrintPages(card, []));
    expect(html).toContain("<h2>Схема жгута</h2><div class=\"fig\"><svg");
    expect(html).toContain("L = 100 мм");
    expect(html).not.toContain("Таблица соединений");
    (card.document as Record<string, unknown>).connections = [{ wire: "x", from: { end: "А1", conn: "A", pin: 1 }, to: { end: "В1", conn: "B", pin: 3 } }];
    // Таблица соединений есть — на титуле схема связей Э4 вместо схемы жгута и плоской таблицы (владелец 14.10).
    const linked = buildPrintDocumentHtml("ТК", etkPrintPages(card, []));
    expect(linked).toMatch(/<h2>Схема связей<\/h2>[\s\S]*data-end="А1"[\s\S]*data-end="В1"[\s\S]*data-a="А1·1" data-b="В1·3"/);
    expect(linked).not.toContain("<h2>Таблица соединений</h2>");
    expect(linked).not.toContain("<h2>Схема жгута</h2>");
  });
});

describe("coax operation sketches (796К.2 КБ1, owner 15.10: «эскизов нет»)", () => {
  const end = { cable: "RG316", connector: "MMCX-Male RG316", l1: "1,8", l2: "4,5", l3: "10", side: "А", pinType: "пайка" };

  it("draws every coax operation from its own data", () => {
    const cut = etkFigure({ type: "coaxCut", components: [{ art: "RG316", norm: "20,2", unit: "М." }], fields: { lengthKd: "1010мм", cutTolerance: "±8 мм" } });
    expect(cut).toContain("L = 1010 мм ±8 мм");
    expect(cut).toContain("RG316");
    const tut = etkFigure({ type: "coaxCutTut", components: [{ art: "ТУТ 5/2,5мм, черная", norm: "40", unit: "Шт." }], fields: { lengthKd: "20мм" } });
    expect(tut).toContain("L = 20 мм");
    expect(tut).toContain("на партию: 40 шт");
    const pin = etkFigure({ type: "coaxPin", fields: { coaxEnd: end } });
    expect(pin).toContain("Сторона А · MMCX-Male RG316");
    expect(pin).toContain("Центральный контакт: припаять на жилу");
    expect(pin).toContain("L1 = 1,8 мм · L2 = 4,5 мм · L3 = 10 мм");
    expect(etkFigure({ type: "coaxHousing", fields: { coaxEnd: { ...end, side: "В", connector: "N-111L" } } })).toContain("Сторона В · N-111L");
    const parts = { kind: "tube", name: "ТУТ 5/2,5мм, черная", count: 2, cable: "RG316", length: "20" };
    expect(etkFigure({ type: "coaxInsTut", fields: { coaxParts: parts } })).toContain("Надеть: ТУТ 5/2,5мм, черная × 2");
    expect(etkFigure({ type: "coaxHeat", fields: { coaxParts: parts } })).toContain("Усадить: ТУТ 5/2,5мм, черная × 2");
    expect(etkFigure({ type: "coaxShield", fields: { coaxParts: { ...parts, kind: "sleeve", name: "" } } })).toContain("обжать шестигранником");
    for (const svg of [cut, tut, pin]) expect(svg).not.toMatch(/undefined|NaN/);
  });

  it("keeps the strip sketch for stripping and draws nothing without data", () => {
    expect(etkFigure({ type: "coaxStrip", fields: { coax: end } })).toContain("L1 = 1,8 мм");
    expect(etkFigure({ type: "coaxPin", fields: {} })).toBe("");
    // Резка без длины — «по КД», а не пустой размер.
    expect(etkFigure({ type: "coaxCut", fields: {} })).toContain("L = по КД");
  });
});
