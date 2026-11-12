import { BRASS, DIEL, EDGE, INK, JACKET, label, line, METAL, MUTED, rect, TUBE } from "./etkPfCoaxArt";

export type RfBodyPart = "nut" | "body" | "insulator" | "contact" | "ferrule" | "tail" | "gasket";
export interface RfBodySegment {
  part: RfBodyPart;
  x_from: number;
  x_to: number;
  r_in: number;
  r_out: number;
  src: unknown;
}
export interface RfContactDepth { min: number | null; max: number | null; src: unknown }
export interface RfBodyRecord {
  id: string;
  gender: string;
  angle: string | number;
  pin_type: string;
  shield_type: string;
  cables: readonly string[];
  segments: readonly RfBodySegment[];
  interface: {
    depth?: RfContactDepth | null;
    thread?: string | { value: string; src: unknown } | null;
    hex?: number | string | { value: number | string; src: unknown } | null;
    /** Axial REF coordinate in mm; a named plane uses the drawing origin (x = 0). */
    ref_plane?: number | string | null;
  };
}

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const mm = (value: number) => value.toLocaleString("ru-RU", { useGrouping: false, maximumFractionDigits: 6 });
const colours: Record<RfBodyPart, string> = {
  nut: METAL, body: METAL, insulator: DIEL, contact: BRASS, ferrule: METAL, tail: METAL, gasket: TUBE,
};

/** Same accepted forms as the ETK contact-depth callout, plus the catalogue's numeric limits. */
function depthText(value: RfContactDepth | string | number | null | undefined): string {
  if (value && typeof value === "object") {
    const { min, max } = value;
    if ((min != null && (!finite(min) || min < 0)) || (max != null && (!finite(max) || max < 0))) return "";
    if (min != null && max != null) return min > max ? "" : `${mm(min)}${min === max ? "" : `…${mm(max)}`} мм`;
    return min != null ? `не менее ${mm(min)} мм` : max != null ? `не более ${mm(max)} мм` : "";
  }
  if (typeof value === "number") return finite(value) && value >= 0 ? `${mm(value)} мм` : "";
  const raw = (value ?? "").trim().replace(/\s*мм\s*$/i, "");
  const bound = /^(не\s+менее|не\s+более)\s+(\d+(?:[.,]\d+)?)$/i.exec(raw);
  if (bound) return `${bound[1].toLowerCase().replace(/\s+/g, " ")} ${bound[2].replace(/\./g, ",")} мм`;
  const nominal = /^(\d+(?:[.,]\d+)?)\s*\(номинал\)$/i.exec(raw);
  if (nominal) return `${nominal[1].replace(/\./g, ",")} мм (номинал)`;
  const range = /^(\d+(?:[.,]\d+)?)(?:\s*(?:\.{2,3}|…|–|—|-)\s*(\d+(?:[.,]\d+)?))?$/.exec(raw);
  if (!range || (range[2] && Number(range[1].replace(",", ".")) > Number(range[2].replace(",", ".")))) return "";
  return `${range[1].replace(/\./g, ",")}${range[2] ? `…${range[2].replace(/\./g, ",")}` : ""} мм`;
}

/** Pixel-space hatching, clipped arithmetically: no shared SVG IDs or scaled text/strokes. */
function cut(x1: number, x2: number, y: number, height: number, fill: string): string {
  let art = rect(x1, x2, y, height, fill);
  const width = x2 - x1;
  for (let offset = 7; offset < width + height; offset += 7) {
    const a = Math.max(0, offset - height), b = Math.min(width, offset);
    art += line(x1 + a, y + height - offset + a, x1 + b, y + height - offset + b, EDGE, .5);
  }
  return art;
}

/** Longitudinal section on a 640 px canvas. x grows from the mating face toward the cable.
 *  Segment geometry is authoritative; depth overrides change the tolerance annotation, not the supplied geometry.
 *  Cable continuation illustrates the tail/ferrule bore fit; it is not a cable construction specification. */
export function rfBodySection(body: RfBodyRecord, contactDepth?: RfContactDepth | string | number | null): { height: number; body: string } | null {
  if (!["0", "0°", "straight", "прямой"].includes(String(body.angle).trim().toLowerCase())) return null;
  const female = /^(female|розетка)$/i.test(body.gender);
  if (!female && !/^(male|вилка)$/i.test(body.gender)) return null;
  const segments = body.segments;
  if (!segments.length || segments.some((s) => !(s.part in colours) ||
    ![s.x_from, s.x_to, s.r_in, s.r_out].every(finite) || s.x_to <= s.x_from || s.r_in < 0 || s.r_out <= s.r_in)) return null;

  const contact = segments.filter((s) => s.part === "contact").sort((a, b) => a.x_from - b.x_from)[0];
  const face = Math.min(...segments.filter((s) => s.part === "body").map((s) => s.x_from));
  const ref = body.interface.ref_plane;
  if (typeof ref === "number" && !finite(ref)) return null;
  const hasRef = ref != null && ref !== "";
  const plane = hasRef ? (typeof ref === "number" ? ref : 0) : finite(face) ? face : Math.min(...segments.map((s) => s.x_from));
  const first = Math.min(plane, ...segments.map((s) => s.x_from));
  const last = Math.max(plane, ...segments.map((s) => s.x_to));
  const radius = Math.max(...segments.map((s) => s.r_out));
  const cableFit = segments.filter((s) => (s.part === "tail" || s.part === "ferrule") && s.r_in > 0);
  const cableLength = cableFit.length ? (last - first) * .3 : 0;
  const scale = Math.min(560 / (last - first + cableLength), 180 / (2 * radius));
  if (!finite(scale) || scale <= 0) return null;
  const cy = 58 + radius * scale, gx = (x: number) => 40 + (x - first) * scale;
  let art = line(24, cy, 616, cy, MUTED, .7, "6 4");

  if (cableFit.length) {
    const start = Math.min(...cableFit.map((s) => s.x_from));
    const bore = Math.min(...cableFit.map((s) => s.r_in));
    const jacket = Math.max(...cableFit.map((s) => s.r_in));
    art += `<g data-part="cable">${rect(gx(last), gx(last + cableLength), cy - jacket * scale, 2 * jacket * scale, JACKET)}` +
      rect(gx(start), gx(last + cableLength), cy - bore * scale, 2 * bore * scale, DIEL) + `</g>`;
  }

  for (const s of segments) {
    const x1 = gx(s.x_from), x2 = gx(s.x_to), outer = s.r_out * scale, inner = s.r_in * scale;
    art += `<g data-part="${s.part}"${s.part === "contact" ? ` data-contact="${female ? "socket" : "pin"}"` : ""}>`;
    if (inner === 0) art += cut(x1, x2, cy - outer, 2 * outer, colours[s.part]);
    else art += cut(x1, x2, cy - outer, outer - inner, colours[s.part]) + cut(x1, x2, cy + inner, outer - inner, colours[s.part]);
    // Conventional thread strokes on the nut's inner edge or the female body's outer edge.
    if (body.interface.thread && (s.part === "nut" || (female && s.part === "body"))) {
      const r = s.part === "nut" ? inner : outer;
      art += `<g data-part="thread">`;
      for (let x = x1 + 4; x < x2 - 2; x += 7) {
        const tooth = Math.min(3, (outer - inner) / 2);
        art += line(x, cy - r, x + 2, cy - r + tooth) + line(x, cy + r, x + 2, cy + r - tooth);
      }
      art += `</g>`;
    }
    art += `</g>`;
  }

  art += line(gx(plane), 38, gx(plane), cy + radius * scale + 8, INK, 1, "5 3") +
    label(gx(plane), 28, hasRef ? "REF" : "торец", 12, 700);
  const depth = depthText(contactDepth) || depthText(body.interface.depth);
  const dimensionY = cy + radius * scale + 28;
  if (depth && contact) {
    const x1 = gx(plane), x2 = gx(contact.x_from), left = Math.min(x1, x2), right = Math.max(x1, x2);
    art += `<g data-part="contact-depth">` + line(x1, cy, x1, dimensionY + 6, MUTED, .9, "3 3") +
      line(x2, cy, x2, dimensionY + 6, MUTED, .9, "3 3") + line(left - 16, dimensionY, right + 16, dimensionY, INK, 1);
    // External arrows keep sub-millimetre dimensions legible without changing the section scale.
    for (const [x, direction] of [[left, -1], [right, 1]]) {
      art += line(x, dimensionY, x + direction * 7, dimensionY - 3, INK, 1) + line(x, dimensionY, x + direction * 7, dimensionY + 3, INK, 1);
    }
    art += label(320, dimensionY + 22, `заглубление ${depth}`, 12, 700) + `</g>`;
  }
  const names: Record<RfBodyPart, string> = {
    nut: "гайка", body: "корпус", insulator: "изолятор", contact: female ? "контакт-гнездо" : "контакт-штырь",
    ferrule: "обжимная гильза", tail: "хвостовик", gasket: "уплотнитель",
  };
  const legend = [...new Set(segments.map((s) => s.part))].map((part) => ({ name: names[part], fill: colours[part] }));
  if (cableFit.length) legend.push({ name: "кабель", fill: JACKET });
  const legendY = dimensionY + (depth && contact ? 52 : 24);
  legend.forEach(({ name, fill }, index) => {
    const x = 24 + (index % 3) * 204, y = legendY + Math.floor(index / 3) * 22;
    art += rect(x, x + 12, y - 10, 12, fill) + label(x + 18, y, name, 12, 600, INK, "start");
  });
  return { height: legendY + Math.ceil(legend.length / 3) * 22, body: art };
}
