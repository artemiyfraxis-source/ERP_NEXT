// Canonical drawing parts for both bundled SVGs and the editable catalog.
// Coordinates are shared by every end treatment. Right ends mirror at x=1200.
export const artworkViewBox = "0 0 1200 240";
export const artworkEnds: readonly string[] = ["cut", "copper", "tin", "terminal", "sealed", "sealed-pin"];

const palette = Object.freeze({
  outline: "#273942", metalDark: "#64747e", metalLight: "#eef4f5",
  copperDark: "#874721", tinDark: "#728991", sealDark: "#295d42",
});

export function safeWireColor(value: string): string {
  return /^#[0-9a-fA-F]{6}$/.test(value) ? value : "#26609e";
}

function defs(): string {
  return `<defs>
    <linearGradient id="wireBody" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#fff" stop-opacity=".34"/><stop offset=".28" stop-color="#fff" stop-opacity=".1"/><stop offset=".8" stop-color="#001a2b" stop-opacity=".12"/><stop offset="1" stop-color="#001a2b" stop-opacity=".3"/></linearGradient>
    <linearGradient id="silver" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#f8fafb"/><stop offset=".31" stop-color="#c5d0d5"/><stop offset=".56" stop-color="#e7edef"/><stop offset="1" stop-color="#889aa3"/></linearGradient>
    <linearGradient id="bronze" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#f3ba85"/><stop offset=".45" stop-color="#ca7e4a"/><stop offset="1" stop-color="#854725"/></linearGradient>
    <linearGradient id="solder" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#f7fbfb"/><stop offset=".54" stop-color="#b8c7cc"/><stop offset="1" stop-color="#80949c"/></linearGradient>
    <linearGradient id="rubber" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#89bb8d"/><stop offset=".44" stop-color="#397b55"/><stop offset="1" stop-color="#28543d"/></linearGradient>
  </defs>`;
}

export function bodyPart(color = "#26609e"): string {
  const jacket = safeWireColor(color);
  return `<g data-part="body"><rect x="220" y="94" width="760" height="52" fill="var(--wire-color,${jacket})" stroke="#294257" stroke-width="2.4"/>
    <rect x="222" y="96" width="756" height="48" fill="url(#wireBody)"/>
    <path d="M222 100H978" stroke="#fff" stroke-width="2.4" opacity=".33"/>
    <path d="M222 141H978" stroke="#173c59" stroke-width="2" opacity=".28"/></g>`;
}

function conductor(tinned: boolean): string {
  const metal = tinned ? "url(#solder)" : "url(#bronze)";
  const edge = tinned ? palette.tinDark : palette.copperDark;
  const lines = Array.from({ length: 12 }, (_, index) => {
    const y = 103 + index * 3;
    return `<path d="M158 ${y} C176 ${y - 2} 194 ${y + 2} 220 ${y}" fill="none" stroke="${edge}" stroke-width="1.2" opacity=".9"/>`;
  }).join("");
  return `<g data-part="${tinned ? "tin" : "copper"}"><path d="M158 102H220V138H158Z" fill="${metal}" stroke="${edge}" stroke-width="1.7"/>${lines}
    <path d="M158 102V138" stroke="${edge}" stroke-width="2"/>
    <path d="M220 94V146" stroke="#1c3952" stroke-width="2.6"/></g>`;
}

function cut(): string {
  return `<g data-part="cut"><path d="M220 94V146" fill="none" stroke="#21394c" stroke-width="2.6"/>
    <path d="M220 98V142" stroke="#e2e9ed" stroke-width="1.5" opacity=".48"/></g>`;
}

function seal(): string {
  return `<g data-part="seal"><path d="M202 91L211 85H263L274 93V147L263 155H211L202 149Z" fill="url(#rubber)" stroke="${palette.sealDark}" stroke-width="2.5"/>
    <path d="M218 85V155M231 84V156M244 84V156M257 85V155" stroke="#d4e7c9" stroke-width="8" opacity=".67"/>
    <path d="M221 85V155M234 84V156M247 84V156M260 85V155" stroke="#2f6546" stroke-width="3"/>
    <path d="M205 92H269M205 147H269" stroke="#b6d1a8" stroke-width="2" opacity=".65"/></g>`;
}

function terminalInsulation(color: string): string {
  // C2: the jacket crosses beneath the rear crimp and ends halfway through
  // the gap (x=162..181). These are illustration coordinates, not IPC limits.
  const jacket = safeWireColor(color);
  return `<g data-part="terminal-insulation">
    <path d="M222 94H171.5V146H222" fill="var(--wire-color,${jacket})" stroke="#294257" stroke-width="2.4"/>
    <path d="M222 96H172.7V144H222Z" fill="url(#wireBody)"/>
    <path d="M173 100H222" stroke="#fff" stroke-width="2.4" opacity=".33"/>
    <path d="M173 141H222" stroke="#173c59" stroke-width="2" opacity=".28"/>
  </g>`;
}

function terminal(pin = false, color = "#26609e", sealed = false): string {
  const copperLines = Array.from({ length: 10 }, (_, index) => {
    const y = 103 + index * 3.3;
    return `<path d="M79 ${y.toFixed(1)} C101 ${(y - 2).toFixed(1)} 116 ${(y + 2).toFixed(1)} 137 ${y.toFixed(1)} M161 ${y.toFixed(1)} C180 ${(y - 2).toFixed(1)} 195 ${(y + 2).toFixed(1)} 213 ${y.toFixed(1)}" fill="none" stroke="${palette.copperDark}" stroke-width="1.25"/>`;
  }).join("");
  const contact = pin
    ? `<path d="M26 110H69L78 101H91V139H78L69 130H26Z" fill="url(#silver)" stroke="${palette.outline}" stroke-width="2.5"/><path d="M27 116H70" stroke="#fff" stroke-width="2" opacity=".75"/>`
    : `<path d="M27 96H80V144H27Q22 144 22 139V101Q22 96 27 96Z" fill="url(#silver)" stroke="${palette.outline}" stroke-width="2.6"/><path d="M30 103H76V137H30Z" fill="#758992" stroke="#3c515d" stroke-width="1.7"/><path d="M34 108H72V132H34Z" fill="#dce6e9"/><path d="M62 107V133" stroke="#82959b" stroke-width="1.4"/><path d="M48 96L56 84L67 84L72 96" fill="url(#silver)" stroke="${palette.outline}" stroke-width="2"/>`;
  return `<g data-part="${pin ? "sealed-pin-terminal" : "terminal"}">
    <path d="M78 101H214V139H78Z" fill="url(#bronze)" stroke="${palette.copperDark}" stroke-width="1.8"/>${copperLines}
    ${terminalInsulation(color)}${sealed ? seal() : ""}
    <path d="M78 94H217V102H78Z M78 138H217V146H78Z" fill="url(#silver)" stroke="${palette.outline}" stroke-width="1.7"/>
    ${contact}
    <path d="M78 95H111V101C111 109 107 112 101 112H78Z M78 145H111V139C111 131 107 128 101 128H78Z" fill="url(#silver)" stroke="${palette.outline}" stroke-width="2.2"/>
    <path d="M132 98H154Q162 98 162 107V133Q162 142 154 142H132Z" fill="url(#silver)" stroke="${palette.outline}" stroke-width="2.2"/>
    <path d="M136 101V139M143 101V139M150 101V139" stroke="#81939b" stroke-width="1.6"/>
    <path d="M163 99H187V104Q187 114 181 114H163Z M163 141H187V136Q187 126 181 126H163Z" fill="url(#silver)" stroke="${palette.outline}" stroke-width="2.1"/>
    <path d="M188 92H217V103H199Q193 103 193 110V130Q193 137 199 137H217V148H188Q181 148 181 141V99Q181 92 188 92Z" fill="url(#silver)" stroke="${palette.outline}" stroke-width="2.3"/>
    <path d="M204 93V104M204 136V147" stroke="#758790" stroke-width="1.7"/>
    <path d="M93 95L112 88L114 97" fill="url(#silver)" stroke="${palette.outline}" stroke-width="1.7"/>
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
