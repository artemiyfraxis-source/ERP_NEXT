// Canonical vector drawing parts for catalog, routes and free drawing ends.
// C1 supplies the side profile. Coordinates are symbolic, never manufacturing mm.
export const artworkViewBox = "0 0 1200 240";
export const artworkEnds: readonly string[] = ["cut", "copper", "tin", "terminal", "sealed", "sealed-pin"];
export const artworkWireDiameter = 21.84;
export const artworkJacketAnchor = 222;

const palette = Object.freeze({ outline: "#303438", metal: "#929699", seal: "#c96358", copper: "#c88954" });

export function safeWireColor(value: string): string {
  return /^#[0-9a-fA-F]{6}$/.test(value) ? value : "#26609e";
}

function defs(): string {
  return `<defs><linearGradient id="bronze" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#e1ad77"/><stop offset="1" stop-color="#c88954"/></linearGradient>
    <linearGradient id="solder" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#e6e9eb"/><stop offset="1" stop-color="#a5adb2"/></linearGradient></defs>`;
}

export function bodyPart(color = "#26609e"): string {
  return `<g data-part="body"><rect x="220" y="109.08" width="760" height="21.84" fill="var(--wire-color,${safeWireColor(color)})" stroke="${palette.outline}" stroke-width="1.2"/></g>`;
}

function conductor(tinned: boolean): string {
  const edge = tinned ? "#728991" : "#874721";
  const lines = Array.from({ length: 6 }, (_, index) => `<path d="M158 ${113 + index * 2.8}H220" stroke="${edge}" stroke-width=".7"/>`).join("");
  return `<g data-part="${tinned ? "tin" : "copper"}"><path d="M158 112H220V128H158Z" fill="url(#${tinned ? "solder" : "bronze"})" stroke="${edge}" stroke-width="1.1"/>${lines}<path d="M220 109.08V130.92" stroke="${palette.outline}" stroke-width="1.2"/></g>`;
}

function cut(): string {
  return `<g data-part="cut"><path d="M220 109.08V130.92" stroke="${palette.outline}" stroke-width="1.2"/></g>`;
}

function terminalInsulation(color: string): string {
  // Jacket enters the rear crimp; the exposed strands remain between crimps.
  return `<g data-part="terminal-insulation"><path d="M456 62.5H519.05V114.5H456Z" fill="var(--wire-color,${safeWireColor(color)})" stroke="${palette.outline}" stroke-width="2"/></g>`;
}

function seal(): string {
  // A single rubber body with three rounded lips, not stripes on a rectangle.
  return `<g data-part="seal"><path data-profile="three-rib-seal" d="M461 44H529 Q550 44 564 30 Q576 18 585 18 Q593 18 593 26V34
    Q599 39 608 29 Q619 18 626 18 Q631 18 631 26V34 Q637 39 646 29 Q657 18 666 18 H675V23H679V153H675V158H666
    Q657 158 646 147 Q637 137 631 142V150 Q631 158 626 158 Q619 158 608 147 Q599 137 593 142V150
    Q593 158 585 158 Q576 158 564 146 Q550 132 529 132H461Z" fill="${palette.seal}" stroke="${palette.outline}" stroke-width="2"/>
    <path d="M593 34V142 M631 34V142 M675 23V153" fill="none" stroke="${palette.outline}" stroke-width="1.5"/></g>`;
}

function terminal(pin = false, color = "#26609e", sealed = false): string {
  // Reference coordinates are uniformly scaled, preserving its side silhouette.
  // Order is jacket -> rubber -> strands -> metal, shared by both contact types.
  const contact = pin
    ? `M43 83H180V101H43Z`
    : `M47 74H65V58L70 55H222V71Q222 80 229 80Q237 80 237 71V55H247V67Q247 79 259 82L323 91Q340 91 340 76Q343 80 348 80H429V76
       L430 90Q430 100 439 98L470 84Q481 78 481 67V35H520L525 128L542 133V141H457Q444 140 431 130H84Q79 110 65 110H47Z`;
  const pinCrimp = pin ? `<path d="M180 83H247V67Q247 79 259 82L323 91Q340 91 340 76Q343 80 348 80H429V76L430 90Q430 100 439 98L470 84Q481 78 481 67V35H520L525 128L542 133V141H457Q444 140 431 130H180Z" fill="${palette.metal}" stroke="${palette.outline}" stroke-width="2"/>` : "";
  return `<g data-part="${pin ? "sealed-pin-terminal" : "terminal"}" data-view="side" transform="translate(4 82.83) scale(.42)">
    ${terminalInsulation(color)}${sealed ? seal() : ""}
    <g data-part="crimp-strands"><path d="M426 87L481 69V88L430 104Z" fill="${palette.copper}"/>
      <path d="M430 90L480 73 M431 94L480 77 M434 97L480 81 M437 100L480 85" stroke="#874721" stroke-width="1.6"/></g>
    <g data-part="terminal-shell"><path data-profile="${pin ? "pin-contact" : "socket-contact"}" d="${contact}" fill="${palette.metal}" stroke="${palette.outline}" stroke-width="2" stroke-linejoin="round"/>${pinCrimp}
      ${pin ? "<!-- pin -->" : `<path d="M43 76Q42 75 42 80V105Q42 109 47 109V74 M48 109V113Q48 128 73 129H84 M66 57V73 M144 129V102Q144 100 148 100H214V129 M145 110H214 M159 100V129 M201 100V129 M237 55V71 M340 76V91 M429 77V90" fill="none" stroke="${palette.outline}" stroke-width="1.5"/>`}
      <path d="M431 129L456 138H525 M520 35L525 128H542" fill="none" stroke="${palette.outline}" stroke-width="1.5"/>
    </g>
  </g>`;
}

export function endPart(kind: string, color = "#26609e"): string {
  switch (kind) {
    case "cut": return cut();
    case "copper": return conductor(false);
    case "tin": return conductor(true);
    case "terminal": return terminal(false, color);
    case "sealed": return terminal(false, color, true);
    case "sealed-pin": return terminal(true, color, true);
    default: throw new Error(`Unknown wire end: ${kind}`);
  }
}

function escapeXml(value: string): string {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

export function renderWireSvg({ left, right, color = "#26609e", title = "Подготовленный провод" }: { left: string; right: string; color?: string; title?: string }): string {
  if (!artworkEnds.includes(left) || !artworkEnds.includes(right)) throw new Error("Unknown wire configuration");
  const safeColor = safeWireColor(color);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="240" viewBox="${artworkViewBox}" role="img" aria-labelledby="title" style="--wire-color:${safeColor}"><title id="title">${escapeXml(title)}</title>${defs()}${bodyPart(safeColor)}${endPart(left, safeColor)}<g transform="translate(1200 0) scale(-1 1)">${endPart(right, safeColor)}</g></svg>\n`;
}

export function renderPartSvg(part: string, color = "#26609e"): string {
  const content = part === "body" ? bodyPart(color) : endPart(part, color);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="240" viewBox="${artworkViewBox}" role="img">${defs()}${content}</svg>\n`;
}
