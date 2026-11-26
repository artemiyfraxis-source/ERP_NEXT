import { describe, expect, it } from "vitest";
import { etkFigure } from "./etkDraw";

/*
 * Геометрия эскизов коакса (ревью 23.11, S1/S2/S4/S6/S7): разъём своего семейства, гнездо у розетки, выноски касаются
 * деталей, линии по ролям, подписи не налезают. Фикстуры — значения из 708К и 796К (не выгрузка прода).
 */
const parse = (markup: string) => new DOMParser().parseFromString(markup.slice(0, markup.indexOf("</svg>") + 6), "image/svg+xml");
type Box = { left: number; right: number; top: number; bottom: number };

/** Box of a primitive in canvas coordinates: ancestor `translate(a,b) scale(sx[,sy])` applied. */
function boxOf(node: Element): Box | null {
  const n = (name: string) => Number(node.getAttribute(name));
  let xs: number[] = [], ys: number[] = [];
  if (node.tagName === "rect") { xs = [n("x"), n("x") + n("width")]; ys = [n("y"), n("y") + n("height")]; }
  else if (node.tagName === "ellipse") { xs = [n("cx") - n("rx"), n("cx") + n("rx")]; ys = [n("cy") - n("ry"), n("cy") + n("ry")]; }
  else if (node.tagName === "line") { xs = [n("x1"), n("x2")]; ys = [n("y1"), n("y2")]; }
  else if (node.tagName === "polygon") {
    const points = (node.getAttribute("points") ?? "").trim().split(/\s+/).map((pair) => pair.split(",").map(Number));
    xs = points.map((point) => point[0]); ys = points.map((point) => point[1]);
  } else return null;
  for (let parent = node.parentElement; parent; parent = parent.parentElement) {
    const transform = parent.getAttribute("transform") ?? "";
    const scale = transform.match(/scale\(([-\d.]+)(?:,([-\d.]+))?\)/), move = transform.match(/translate\(([-\d.]+),([-\d.]+)\)/);
    if (scale) { const sx = Number(scale[1]), sy = Number(scale[2] ?? scale[1]); xs = xs.map((x) => x * sx); ys = ys.map((y) => y * sy); }
    if (move) { xs = xs.map((x) => x + Number(move[1])); ys = ys.map((y) => y + Number(move[2])); }
  }
  return { left: Math.min(...xs), right: Math.max(...xs), top: Math.min(...ys), bottom: Math.max(...ys) };
}

/** Every leader ends on its own part: distance from the end dot to the nearest primitive of that part ≤ 0,5. */
function leaderGaps(markup: string): Array<{ target: string; gap: number }> {
  const document = parse(markup);
  return [...document.querySelectorAll('g[data-role="leader"][data-target]')].filter((leader) => leader.querySelector("circle")).map((leader) => {
    const target = leader.getAttribute("data-target")!, dot = leader.querySelector("circle")!;
    const [x, y] = [Number(dot.getAttribute("cx")), Number(dot.getAttribute("cy"))];
    const shapes = [...document.querySelectorAll(`[data-part="${target}"]`)].flatMap((part) => [part, ...part.querySelectorAll("*")]).map(boxOf).filter(Boolean) as Box[];
    const gap = Math.min(Infinity, ...shapes.map((box) => Math.hypot(Math.max(box.left - x, 0, x - box.right), Math.max(box.top - y, 0, y - box.bottom))));
    return { target, gap };
  });
}

function textBoxes(markup: string) {
  return [...parse(markup).querySelectorAll("text")].filter((node) => (node.textContent ?? "").trim() && node.getAttribute("fill") !== "#fff").map((node) => {
    const size = Number(node.getAttribute("font-size")), weight = Number(node.getAttribute("font-weight") || 400);
    const width = Array.from(node.textContent ?? "").length * size * (weight >= 600 ? .62 : .56);
    const x = Number(node.getAttribute("x")), y = Number(node.getAttribute("y")), anchor = node.getAttribute("text-anchor");
    const left = anchor === "middle" ? x - width / 2 : anchor === "end" ? x - width : x;
    return { text: node.textContent ?? "", left, right: left + width, top: y - size * .78, bottom: y + size * .22 };
  });
}

const end = (extra: Record<string, unknown>) => ({ cable: "RTK 031", side: "А", pinType: "обжим", shield: "обжим", l1: "3", l2: "4,6", l3: "9,7", ...extra });
const FIXTURES: Array<[string, Record<string, unknown>, string, string, "socket" | "pin"]> = [
  ["FAKRA прямой, контакт", end({ connectorType: "FAKRA-Female (тип Z)", gender: "розетка", connector: "59K13o-1M4A4", contactDepth: "0,18…0,68" }), "coaxHousing", "INS_FAKRA_ST_CONTACT", "socket"],
  ["FAKRA прямой, корпус", end({ connectorType: "FAKRA-Female (тип Z)", gender: "розетка", connector: "59K13o-1M4A4", contactDepth: "0,18…0,68" }), "coaxHousing", "INS_FAKRA_ST_HOUSING", "socket"],
  ["FAKRA прямой, крышка", end({ connectorType: "FAKRA-Female (тип Z)", gender: "розетка", connector: "59K13o-1M4A4" }), "coaxCapScrew", "INS_FAKRA_ST_SEAL", "socket"],
  ["FAKRA угловой, крышка", end({ connectorType: "FAKRA-Female R/A (тип Z)", gender: "розетка", connector: "59K25H-1M4A4", contactDepth: "0,2…0,5" }), "coaxCapCrimp", "PRS_FAKRA_RA_CAP", "socket"],
  ["FAKRA проходной", end({ connectorType: "FAKRA-Male bulkhead (тип Z)", gender: "вилка", connector: "2FA1-NZSP-C30E2", pinType: "пайка" }), "coaxHousing", "INS_FAKRA_BH_CONTACT", "pin"],
  ["FAKRA вилка", end({ connectorType: "FAKRA-Male (тип Z)", gender: "вилка" }), "coaxHousing", "INS_housing_COAX", "pin"],
  ["HFM, контакт", end({ connectorType: "HFM-Female (тип Z)", gender: "розетка", connector: "AMK11D-1M4Z5-Z", contactDepth: "0,90…1,20" }), "coaxHousing", "INS_HFM_CONTACT", "socket"],
  ["HFM, QUAD", end({ connectorType: "HFM-Female (тип Z)", gender: "розетка", connector: "AMK11D-1M4Z5-Z", contactDepth: "0,90…1,20" }), "coaxHousing", "INS_HFM_HOUSING", "socket"],
  ["N розетка", end({ connectorType: "N-Female", gender: "розетка", pinType: "пайка" }), "coaxHousing", "INS_housing_COAX", "socket"],
  ["N вилка", end({ connectorType: "N-Male", gender: "вилка", pinType: "пайка" }), "coaxHousing", "INS_housing_COAX", "pin"],
  ["MMCX вилка", end({ connectorType: "MMCX-Male", gender: "вилка", pinType: "пайка" }), "coaxHousing", "INS_housing_COAX", "pin"],
  ["SMA вилка", end({ connectorType: "SMA-Male", gender: "вилка", connector: "SMA-C174P", pinType: "пайка", contactDepth: "0,38…1,14" }), "coaxHousing", "INS_housing_COAX", "pin"],
  ["SMA розетка", end({ connectorType: "SMA-Female bulkhead", gender: "розетка", connector: "SMA-C316F", pinType: "пайка", contactDepth: "1,88…2,23" }), "coaxHousing", "INS_housing_COAX", "socket"],
  ["SMA угловой", end({ connectorType: "SMA-Male R/A", gender: "вилка", pinType: "внутри корпуса" }), "coaxSolderShield", "SDR_COAX_housing", "pin"],
];

describe("mount sketch per connector family (S1, S2, S6, S7)", () => {
  it.each(FIXTURES)("%s", (_name, coaxEnd, type, code, gender) => {
    const svg = etkFigure({ type, code, fields: { coaxEnd } });
    const family = /SMA-Male R\/A/.test(String(coaxEnd.connectorType)) ? "sma-ra" : "";
    // Non-SMA families are a labelled schematic, never the SMA section.
    if (/^SMA-(Male|Female)/.test(String(coaxEnd.connectorType)) && !family) expect(svg).toContain('data-geometry="section"');
    else {
      expect(svg).toContain('data-geometry="schematic"');
      expect(svg).toMatch(/Схема: [^<]*, без масштаба · форма деталей — по чертежу изготовителя/);
      expect(svg).not.toMatch(/накидная гайка|корпус с резьбой/);
    }
    // Female → socket with an opening and no pointed tip; male → pin with a tip.
    expect(svg).toContain(`data-part="contact" data-state="${gender}"`);
    if (gender === "socket") { expect(svg).toContain('data-part="socket-bore"'); expect(svg).not.toContain('data-part="pin-tip"'); }
    else expect(svg).toContain('data-part="pin-tip"');
    // Leaders touch their parts.
    const gaps = leaderGaps(svg);
    expect(gaps.length).toBeGreaterThanOrEqual(3);
    for (const { target, gap } of gaps) expect(gap, target).toBeLessThanOrEqual(.5);
    // Line roles: extension/leader/shelf/dimension lines are continuous; dashes only for hidden contours.
    const document = parse(svg);
    for (const node of document.querySelectorAll("[stroke-dasharray]")) expect(node.getAttribute("data-role")).toBe("hidden");
    expect(document.querySelectorAll('[data-role="leader"] line').length).toBe(gaps.length + document.querySelectorAll('[data-target="detail-origin"]').length);
    // No numeric scale on an unscaled detail; captions do not collide.
    expect(svg).not.toMatch(/\(\d+:\d+\)/);
    const boxes = textBoxes(svg);
    for (let i = 0; i < boxes.length; i += 1) for (let j = i + 1; j < boxes.length; j += 1) {
      const [a, b] = [boxes[i], boxes[j]];
      const hit = Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1;
      expect(hit, `${a.text} × ${b.text}`).toBe(false);
    }
    expect(svg).not.toMatch(/undefined|NaN/);
  });

  it("stage parts follow the operation: plastic housing only from the housing step, seal and cap from the seal step, HFM shows its 4 channels", () => {
    const figure = (code: string, type = "coaxHousing", extra: Record<string, unknown> = {}) =>
      etkFigure({ type, code, fields: { coaxEnd: end({ connectorType: "FAKRA-Female (тип Z)", gender: "розетка", ...extra }) } });
    expect(figure("INS_FAKRA_ST_CONTACT")).not.toContain('data-part="housing"');
    expect(figure("INS_FAKRA_ST_CONTACT")).toMatch(/data-part="ferrule" data-state="loose"/);
    expect(figure("INS_FAKRA_ST_HOUSING")).toContain('data-part="housing"');
    expect(figure("INS_FAKRA_ST_HOUSING")).toMatch(/data-part="ferrule" data-state="crimped"/);
    expect(figure("INS_FAKRA_ST_SEAL", "coaxCapScrew")).toMatch(/data-part="seal"[^]*data-part="cap"/);
    const quad = etkFigure({ type: "coaxHousing", code: "INS_HFM_HOUSING", fields: { coaxEnd: end({ connectorType: "HFM-Female (тип Z)", gender: "розетка" }) } });
    expect(quad).toContain("корпус QUAD, 4 канала");
    expect(quad).toContain('data-part="support"');
    expect(quad).toMatch(/data-part="channel-2"[^]*data-role="hidden"/);
  });

  it.each([["0,18…0,68", "0,18…0,68"], ["0,90…1,20", "0,90…1,20"], ["не менее 2,50", "≥ 2,50"], ["9,14 (номинал)", "9,14"]])(
    "depth %s: detail «А» with a continuous origin outline and the value on the dimension line", (depth, onLine) => {
      const svg = etkFigure({ type: "coaxHousing", code: "INS_FAKRA_ST_CONTACT", fields: { coaxEnd: end({ connectorType: "FAKRA-Female (тип Z)", gender: "розетка", contactDepth: depth }) } });
      const document = parse(svg);
      expect(document.querySelector('[data-role="detail-origin"]')!.hasAttribute("stroke-dasharray")).toBe(false);
      expect([...document.querySelectorAll("[data-detail-id]")].map((node) => node.textContent)).toEqual(["А", "А"]);
      expect([...document.querySelectorAll('[data-role="dimension"] text')].map((node) => node.textContent)).toContain(onLine);
      expect(svg).toContain("Вид А — без масштаба");
    });

  it("the central-contact sketch: socket for a female end (crimp and solder), pin for a male one", () => {
    for (const [type, pinType] of [["coaxPinCrimp", "обжим"], ["coaxPin", "пайка"]]) {
      const female = etkFigure({ type, fields: { coaxEnd: end({ connectorType: "HFM-Female (тип Z)", gender: "розетка", pinType }) } });
      expect(female).toContain('data-state="socket"');
      expect(female).toContain('data-part="socket-bore"');
      expect(female).not.toContain('data-part="pin-tip"');
      expect(female).toContain("Центральный контакт (гнездо)");
    }
    const male = etkFigure({ type: "coaxPin", fields: { coaxEnd: end({ connectorType: "SMA-Male", gender: "вилка", pinType: "пайка" }) } });
    expect(male).toContain('data-part="pin-tip"');
  });

  it("strip and twist dimensions use continuous extension lines", () => {
    const strip = etkFigure({ type: "coaxCut", fields: { lengthKd: "1250мм", coax: end({ dimensionOrder: "" }), coaxB: end({}) } });
    expect(strip).toContain('data-role="extension"');
    expect(strip).not.toContain("stroke-dasharray");
    const twist = etkFigure({ type: "twist", fields: { twist: { pairs: "1) ПВАМ черный + ПВАМ красный", pitch: "15" } } });
    expect(twist).toContain('data-role="extension"');
    expect(twist).not.toContain("stroke-dasharray");
  });
});
