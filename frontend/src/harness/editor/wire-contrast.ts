// ERP: a light conductor (белый, жёлтый, светло-серый, розовый…) disappears on the pale canvas and the grey pipe. Such a
// wire is stroked with a thin dark outline under its own colour — on Э4 and on the drawing, and in the SVG print that
// records the same canvas calls. The conductor keeps its real colour.

/** Outline under a light conductor. */
export const LIGHT_WIRE_OUTLINE = "#53636c";

/** Relative luminance above which a colour reads poorly on white (contrast below ~2:1). */
const LIGHT_LUMINANCE = 0.45;

const channel = (value: number) => {
  const c = Math.max(0, Math.min(255, value)) / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
};

function rgbOf(color: string): [number, number, number] | null {
  const text = color.trim().toLowerCase().replace(/\s+/g, "");
  if (text === "white") return [255, 255, 255];
  const hex = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/.exec(text)?.[1];
  if (hex) {
    const full = hex.length <= 4 ? [...hex].map((digit) => digit + digit).join("") : hex;
    return [0, 2, 4].map((at) => parseInt(full.slice(at, at + 2), 16)) as [number, number, number];
  }
  const rgb = /^rgba?\((\d+(?:\.\d+)?),(\d+(?:\.\d+)?),(\d+(?:\.\d+)?)(?:,[\d.]+)?\)$/.exec(text);
  return rgb ? [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])] : null;
}

/** A conductor colour too light to read on the canvas without an outline. Unknown colours are not light. */
export function isLightWireColor(color: string): boolean {
  const rgb = rgbOf(color);
  if (!rgb) return false;
  const [r, g, b] = rgb.map(channel) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > LIGHT_LUMINANCE;
}
