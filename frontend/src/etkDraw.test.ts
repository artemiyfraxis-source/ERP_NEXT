import { describe, expect, it } from "vitest";
import {
  etkChips, etkConnectionRows, etkCutEnds, etkDrawCoax, etkDrawCrimp, etkDrawHarness, etkDrawPinout, etkDrawTwist, etkDrawWires, etkFigure,
  etkTolerance, etkWireColor,
} from "./etkDraw";
import { buildPrintDocumentHtml } from "./print";
import { etkPrintPages } from "./etkPrint";
import type { ProjectTechcard } from "./api";

const housing = { title: "JST XH", art: "XHP-2", rows: 1, cols: 2, n: 2, kind: "grid", color: "#efe6cf", latch: "ramp",
  cavity: "square", labels: null, grid: [[1, 2]], round: null, conf: "medium", note: "", pitch: 2.5 };

describe("etkChips (renderEtk_ chips)", () => {
  it("lists crimp fields, strip, twist and БД.ОП time like SUPPLYMAP", () => {
    expect(etkChips({
      fields: { applicator: "AP-1", crimpHeight: "0,85", pullForce: "40", strip: { a: true, b: true, lenA: "5", lenB: "" } },
      times: { operation_seconds: "3.5", setup_seconds: "180" },
    })).toEqual([
      ["Аппликатор", "AP-1"], ["Высота обжима", "0,85 мм"], ["Отрыв", "40 Н"], ["Зачистка", "A 5 мм · B ?"],
      ["Время", "3,5 с на шт. · подгот. 3 мин"],
    ]);
    expect(etkChips({ fields: { crimpHeight: "", twist: { pitch: "", count: 2 }, lengthKd: "300мм", cutTolerance: "±5" } })).toEqual([
      ["Высота обжима", "⚠ нет данных"], ["Длина по КД", "300мм"], ["Допуск реза", "±5 мм"], ["Шаг свивки", "по КД"], ["Пар", "2"],
      ["Время", "— · подгот. —"],
    ]);
  });

  it("shows the crimp height range and the tinned end of the cut-crimp-tin operation", () => {
    const chips = Object.fromEntries(etkChips(crimpTin()));
    expect(chips["Высота обжима"]).toBe("0,43 – 0,47 мм");
    expect(chips["Допуск реза"]).toBe("±3 мм");
    expect(chips["Зачистка"]).toBe("A 1,2 мм · B 3 мм (лужение)");
    // Выданная ЭТК: длины под лужение нет, конец В всё равно лужёный и зачищен.
    const issued = crimpTin({ strip: { a: true, b: false, lenA: "1,2", lenB: "" }, cutTolerance: "(+/-)1мм" });
    expect(Object.fromEntries(etkChips(issued))).toMatchObject({ "Зачистка": "A 1,2 мм · B ? (лужение)", "Допуск реза": "±1 мм" });
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

  it("shows the manual-assembly quantity per item, parameters and note in the header", () => {
    expect(etkChips({
      fields: { perItem: "2", params: "L = 300 мм", note: "провод из бухты" },
      times: { operation_seconds: "6", setup_seconds: "120" },
    })).toEqual([
      ["На изделие", "2 шт."], ["Параметры", "L = 300 мм"], ["Примечание", "провод из бухты"],
      ["Время", "6 с на шт. · подгот. 2 мин"],
    ]);
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
    expect(etkDrawCoax({ fields: { coax: { connector: "SMA", l1: "3" } } })).toContain("Размеров разделки нет в базе коакса для SMA");
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
    expect(buildPrintDocumentHtml("ТК", etkPrintPages(card, []))).toMatch(/<h2>Таблица соединений<\/h2>[\s\S]*А1 · A[\s\S]*В1 · B/);
  });
});
