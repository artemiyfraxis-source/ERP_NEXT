import { describe, expect, it } from "vitest";
import { assemblyLength, coaxAssemblyDrawing, coaxEndStateAt, coaxFamily, coaxPfArt } from "./etkPfCoaxArt";
import type { EtkPfGroup } from "./etkRoute";

const parse = (markup: string) => new DOMParser().parseFromString(markup, "image/svg+xml");
const texts = (markup: string) => [...parse(markup).querySelectorAll("text")].map((node) => node.textContent ?? "");
const group = (extra: Partial<EtkPfGroup> = {}): EtkPfGroup =>
  ({ key: "f", number: 5, label: "5", name: "Кабельная сборка", final: true, ops: [1], components: [], inputs: [], output: [], minutes: 0, ...extra });

const fakraF = { connectorType: "FAKRA-Female (тип Z)", gender: "розетка", connector: "59K13o-1M4A4", pinType: "обжим", shield: "обжим" };
const fakraBH = { connectorType: "FAKRA-Male bulkhead (тип Z)", gender: "вилка", connector: "2FA1-NZSP-C30E2", pinType: "пайка", shield: "обжим" };
const fakraRA = { connectorType: "FAKRA-Female R/A (тип Z)", gender: "розетка", connector: "59K25H-1M4A4", pinType: "обжим", shield: "обжим" };
const hfm = { type: "HFM-female (тип Z)", article: "AMK11D-1M4Z5-Z", pinType: "обжим", shield: "обжим" };

describe("final assembly: finished length, not the cut length (S3)", () => {
  it.each([[1200, 1250], [7500, 7555]])("finished %i mm is drawn; the cut length %i mm is not", (finished, cut) => {
    const config = { mode: "coax", coax: { lengthBasis: "finished", finishedLength: finished, cableLength: cut, sideA: fakraF, sideB: fakraF } };
    const svg = coaxPfArt(group({ name: `Кабельная сборка ${finished}mm` }), [{ position: 1, type: "coaxHeat" }], config)!;
    expect(texts(svg)).toContain(`L = ${finished} мм`);
    expect(svg).not.toContain(String(cut));
  });

  it("an explicit name conflict keeps the saved finished length and says so; missing length is marked, never replaced by the cut", () => {
    expect(assemblyLength({ coax: { lengthBasis: "finished", finishedLength: "1200" } }, "Сборка 1250mm")).toEqual({ value: "1200", note: "в названии 1250 мм — сверить с КД" });
    expect(assemblyLength({ coax: { lengthBasis: "finished", finishedLength: "" , cableLength: 1250 } }, "Сборка")).toEqual({ value: "", note: "" });
    expect(assemblyLength({ coax: { cableLength: 285 } }, "SMA-Male to SMA-Female RG316 275mm").value).toBe("275");
    expect(assemblyLength({ coax: { cableLength: 285 } }, "Сборка без длины").value).toBe("");
    const svg = coaxPfArt(group({ name: "Сборка" }), [{ position: 1, type: "coaxHeat" }], { mode: "coax", coax: { cableLength: 1250, sideA: fakraF, sideB: fakraF } })!;
    expect(texts(svg)).toContain("L — нет длины готового изделия");
    expect(svg).not.toContain("1250");
  });
});

describe("final assembly layout (S5, S9)", () => {
  it.each([
    [fakraF, fakraRA],
    [{ connectorType: "Очень длинное наименование разъёма для стороны А с дополнительными данными" }, { connectorType: "Ш".repeat(120) }],
    [{ connectorType: "<Разъём & тип>" }, fakraBH],
  ])("short end tags А/В over the connectors, full names in the legend, no caption collisions", (a, b) => {
    const svg = coaxAssemblyDrawing(a, b, "4855", "", "RTK 031");
    const document = parse(svg);
    expect(document.querySelector("parsererror")).toBeNull();
    const legend = [...document.querySelectorAll("[data-legend]")].map((node) => node.getAttribute("data-legend"));
    expect(legend.slice(0, 2)).toEqual(["А", "В"]);
    const joined = texts(svg).join(" ").replace(/\s+/g, "");
    for (const end of [a, b]) expect(joined).toContain(String((end as { connectorType: string }).connectorType).replace(/\s+/g, ""));
    expectNoTextOverlap(svg);
  });

  it("draws middle and end labels M1…M3 under clear covers and the connector colour from the data", () => {
    const svg = coaxAssemblyDrawing(fakraBH, fakraF, "7500", "", "RTK 031", {
      marks: [{ text: "FRX.708К.120903/AA.{№}", place: "middle" }, { text: "Camera 9", place: "A" }, { text: "Splitter B C1", place: "B" }],
      protect: "ТУТ 4/2", color: { color: "красный", code: "RD" },
    });
    expect(svg.match(/data-part="mark"/g)).toHaveLength(3);
    expect(svg.match(/data-part="cover"/g)).toHaveLength(3);
    expect(svg.match(/data-part="conn-color"/g)).toHaveLength(2);
    expect(texts(svg).join(" ")).toContain("FRX.708К.120903/AA.{№} · середина");
    expect(texts(svg)).toContain("ТУТ 4/2 поверх этикеток, усажена");
    expect(svg).not.toContain("на концах");
    expectNoTextOverlap(svg);
  });
});

describe("route state of an end (S10)", () => {
  const coax = { mode: "coax", coax: { tutArt: "ТУТ 4/2мм, черная", sideA: { type: "SMA-male", pinType: "пайка", shield: "обжим" } } };
  const document = (ops: Array<Record<string, unknown>>) => ({ build_config: coax, operations: ops.map((op, index) => ({ position: index + 1, ...op })),
    route: { blocks: [{ key: "a", ops: ops.map((_, index) => index + 1) }] } });
  const end = { type: "SMA-male", pinType: "пайка", shield: "обжим" };

  it("tube absent, preloaded (slid on, not shrunk) and shrunk are three different states", () => {
    expect(coaxEndStateAt(document([{ type: "coaxHousing", fields: { coaxEnd: { side: "А" } } }]), "A", 9, end).tube).toBe("none");
    const preloaded = document([{ type: "coaxInsTut", code: "INS_TUT" }, { type: "coaxInsSleeve" }, { type: "coaxHousing", fields: { coaxEnd: { side: "А" } } }]);
    expect(coaxEndStateAt(preloaded, "A", 3, end)).toMatchObject({ tube: "loose", shield: "loose", insert: true });
    const done = document([{ type: "coaxInsTut", code: "INS_TUT" }, { type: "coaxInsSleeve" }, { type: "coaxHousing", fields: { coaxEnd: { side: "А" } } },
      { type: "coaxShield" }, { type: "coaxHeat", code: "HEAT_TYT_short" }]);
    expect(coaxEndStateAt(done, "A", 5, end)).toMatchObject({ tube: "shrunk", shield: "crimped" });
  });

  it("a clear protective cover over labels is never drawn as an end tube; a clamp end gets no sleeve", () => {
    const covered = { build_config: { coax: { tutArt: "ТУТ 4/2мм, прозрачная", marks: [{ text: "M", place: "middle" }], markProtect: true } },
      operations: [{ position: 1, type: "coaxInsTut", code: "INS_TUT" }, { position: 2, type: "coaxHeat", code: "HEAT_TYT_short", fields: { coaxProtectiveHeat: true } }] };
    expect(coaxEndStateAt(covered, "A", 2, fakraF).tube).toBe("none");
    const clamp = { build_config: {}, operations: [{ position: 1, type: "coaxInsSleeve" }, { position: 2, type: "coaxHousing", fields: { coaxEnd: { side: "А" } } }] };
    expect(coaxEndStateAt(clamp, "A", 2, { type: "TNC-male", shield: "прижим" }).shield).toBe("clamp");
  });
});

describe("connector family dispatch", () => {
  it.each([
    [fakraF, "fakra"], [fakraRA, "fakra-ra"], [fakraBH, "fakra-bh"], [hfm, "hfm"], [{ connectorType: "SMA-Female bulkhead" }, "sma"],
    [{ connectorType: "SMA-Male R/A" }, "sma-ra"], [{ connectorType: "RP-SMA-Male" }, "sma"], [{ connectorType: "N-Female" }, "n"],
    [{ connectorType: "TNC-Male" }, "tnc"], [{ connectorType: "RP-TNC-Male" }, "tnc"], [{ connectorType: "MMCX-Male" }, "mmcx"], [{ connector: "X-1" }, "other"],
  ])("%o → %s", (end, family) => expect(coaxFamily(end)).toBe(family));
});

/** Independent conservative text box (0.62 em per glyph for bold, 0.56 em otherwise): no two captions intersect. */
function expectNoTextOverlap(markup: string) {
  const boxes = [...parse(markup).querySelectorAll("text")].filter((node) => (node.textContent ?? "").trim()).map((node) => {
    const size = Number(node.getAttribute("font-size")), weight = Number(node.getAttribute("font-weight") || 400);
    const width = Array.from(node.textContent ?? "").length * size * (weight >= 600 ? .62 : .56);
    let x = Number(node.getAttribute("x")), y = Number(node.getAttribute("y"));
    for (let parent = node.parentElement; parent; parent = parent.parentElement) {
      const move = parent.getAttribute("transform")?.match(/translate\(([-\d.]+),([-\d.]+)\)/);
      if (move) { x += Number(move[1]); y += Number(move[2]); }
    }
    const anchor = node.getAttribute("text-anchor");
    const left = anchor === "middle" ? x - width / 2 : anchor === "end" ? x - width : x;
    return { text: node.textContent, left, right: left + width, top: y - size * .78, bottom: y + size * .22 };
  });
  for (let i = 0; i < boxes.length; i += 1) for (let j = i + 1; j < boxes.length; j += 1) {
    const [a, b] = [boxes[i], boxes[j]];
    const overlap = Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1;
    expect(overlap, `${a.text} × ${b.text}`).toBe(false);
  }
}
