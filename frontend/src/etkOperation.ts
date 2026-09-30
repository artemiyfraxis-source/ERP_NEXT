// Данные операции ЭТК, которых нет отдельными полями в карточке операции (SUPPLYMAP БД.ОП-ТК / БД.ШАГИ / БД.ОП-ОБ):
// режим пайки (жало и температура — в шаблонах они только в тексте шагов) и типовые дефекты, разобранные из
// текста в список с пиктограммами. Считается при показе и печати, поэтому выданные ТК перевыпускать не нужно.

type Row = Record<string, unknown>;

function obj(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

function list(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") as Row[] : [];
}

function str(value: unknown): string {
  return value == null ? "" : String(value);
}

/** Порядок разделов оснастки: инструмент → оборудование → расходники, затем прочее (SUPPLYMAP PortalApp:4533). */
export const TOOLING_ORDER = ["Инструмент", "Оборудование", "Расходники", "Матрицы", "Оснастка"];

export function toolingSections(sections: string[]): string[] {
  const rank = (section: string) => {
    const index = TOOLING_ORDER.indexOf(section);
    return index < 0 ? TOOLING_ORDER.length : index;
  };
  return [...new Set(sections)].sort((a, b) => rank(a) - rank(b));
}

export type SolderMode = {
  method: "iron" | "bath" | "induction";
  tip: string;
  temperature: string;
  tolerance: string;
  /** true — значение не из карты операции, а по умолчанию ERP (надо уточнить технологу). */
  tipAssumed: boolean;
  temperatureAssumed: boolean;
};

const TIP_CLASSES: Array<[RegExp, string]> = [
  [/(особо|очень)\s+тонк|прецизионн/i, "Очень тонкое конусное, Ø 0,4–0,8 мм"],
  [/тонк/i, "Тонкое конусное / микроклин, 1,0–1,6 мм"],
  [/массивн|широк/i, "Массивное клиновидное (долото), 3,2–5,0 мм"],
  [/клин|конус/i, "Клиновидное, 2,4–3,2 мм"],
];

function isSoldering(operation: Row): boolean {
  const card = obj(operation.card);
  const names = `${str(operation.title)} ${str(card.name)} ${str(operation.type)}`;
  return /^SDR/i.test(str(operation.code)) || /пайк|лужен/i.test(names);
}

/** Режим пайки операции: жало паяльника и температура. Сначала — параметры оборудования и текст шагов карты
 *  (параметры/программа ванны «200 °C»; в шагах «Паяльная станция, тонкое жало; t° 320–350 °C», допуск «(+/-) 10 °C»); чего в карте нет — по умолчанию
 *  для Sn63Pb37: паяльник 320–350 °C (от 370 °C — массивное жало), ванна лужения 250–270 °C. */
export function solderMode(operation: Row): SolderMode | null {
  if (!isSoldering(operation)) return null;
  const card = obj(operation.card);
  const steps = list(card.steps);
  const tooling = list(card.tooling);
  const equipment = tooling.filter((tool) => str(tool.section) === "Оборудование");
  const equipmentNames = equipment.map((tool) => str(tool.name)).join(" ");
  const method: SolderMode["method"] = /ванн/i.test(equipmentNames) ? "bath" : /индукц/i.test(equipmentNames) ? "induction" : "iron";
  const stepText = steps.map((step) => str(step.text)).join("\n");
  const sources = [...equipment.map((tool) => `${str(tool.parameters)} ${str(tool.program)}`), stepText];
  let temperature = "";
  for (const source of sources) {
    const match = source.match(/(\d{3})\s*[–—-]\s*(\d{3})\s*°\s*[CС]/i) || source.match(/(\d{3})\s*°\s*[CС]/i);
    if (match) { temperature = match[2] ? `${match[1]}–${match[2]} °C` : `${match[1]} °C`; break; }
  }
  const toleranceMatch = steps.map((step) => str(step.tolerance)).join(" ").match(/(?:\(\+\/-\)|±|\+\/-)\s*(\d+)\s*°/);
  const tolerance = toleranceMatch ? `±${toleranceMatch[1]} °C` : "±10 °C";
  const tipLine = stepText.split("\n").find((line) => /жал/i.test(line) && TIP_CLASSES.some(([pattern]) => pattern.test(line.split(/жал/i)[0])));
  if (method === "bath") {
    return { method, tip: "Не требуется — лужение погружением в ванну", temperature: temperature || "250–270 °C", tolerance,
      tipAssumed: false, temperatureAssumed: !temperature };
  }
  if (method === "induction") {
    return { method, tip: "Не требуется — индукционный нагрев", temperature: temperature || "По программе установки, до растекания припоя",
      tolerance: temperature ? tolerance : "", tipAssumed: false, temperatureAssumed: !temperature };
  }
  const upper = Number((temperature.match(/(\d{3})\s*°/) || [])[1] || 0);
  const tipFromCard = tipLine ? TIP_CLASSES.find(([pattern]) => pattern.test(tipLine.split(/жал/i)[0]))?.[1] || "" : "";
  const tip = tipFromCard || (upper >= 370 ? TIP_CLASSES[2][1] : upper >= 350 ? TIP_CLASSES[3][1] : "Клиновидное, 1,6–2,4 мм");
  return { method, tip, temperature: temperature || (upper >= 370 ? "350–380 °C" : "320–350 °C"), tolerance,
    tipAssumed: !tipFromCard, temperatureAssumed: !temperature };
}

export type DefectKind =
  | "bridge" | "wicking" | "cold" | "lack" | "excess" | "crimp" | "strands" | "insulation"
  | "seating" | "housing" | "bent" | "pinout" | "geometry" | "generic";

export type DefectItem = { name: string; text: string; kind: DefectKind };
export type DefectGroup = { title: string; items: DefectItem[] };
export type Defects = { intro: string[]; groups: DefectGroup[] };

const KINDS: Array<[DefectKind, RegExp]> = [
  ["bridge", /перемычк|замыкан|bridge|\bкз\b/i],
  ["wicking", /затекан|wicking|капилляр/i],
  ["cold", /холодн|матов|непрогрев|зернист|рыхл/i],
  ["lack", /недолив|недостат|непропай|не пропа/i],
  ["excess", /избыт|наплыв|сосульк|пик|остры[ей] выступ|капл|облой/i],
  ["crimp", /обжим|опрессов|обжат|перепрессов|втулк|гильз|нахл[её]ст|усили/i],
  ["strands", /распуш|ёрш|ерш|вытягив|подрез|проволоч|вылет|выход жил|прядей|бахром|опл[её]т|скручив|волоск|экран|фольг|дренаж|раскрут|перерез|обрыв/i],
  ["insulation", /оплавл|обуглив|изоляц|надрез|царап|смят|рван|задир|потерт|диэлектр|оболочк|порез|надрыв|прожог|усадк|заусен|защемлен|перелом|сплющ|остры[ей] кромк/i],
  ["seating", /посадк|недосыл|усик|фиксац|зафиксир|замк|защ[её]лк|стопор|заход|до упора|pull|затянут|закрепл|дожат|смещ|глубин|не дош|не на месте|выходит/i],
  ["housing", /корпус|пластик|трещин|растрескив|манжет|площадк|отслоен/i],
  ["bent", /деформ|изгиб|банан|замят|перекрут|складк/i],
  ["geometry", /длин|размер|геометр|зазор|косой|высот|габарит|ступен|шаг/i],
  ["pinout", /распиновк|перепут|пропуск|порядок|отсутств|резистор|компонент|артикул|несоответ|маркир|маркер|нечитаем/i],
];

export function defectKind(name: string, text = ""): DefectKind {
  for (const source of [name, text]) {
    const found = KINDS.find(([, pattern]) => pattern.test(source));
    if (found) return found[0];
  }
  return "generic";
}

const EXPLAINS = /^(Приводит|Это|Признак|Допустим|Снижает|Возника|Происходит|Характерн|Причин|Вед[её]т|Влеч[её]т|Нарушает|При |Так как|Что )/;

function listItem(fragment: string): DefectItem | null {
  const clean = fragment.trim().replace(/[.;]\s*$/, "");
  if (clean.length < 2) return null;
  const upper = clean[0].toUpperCase() + clean.slice(1);
  const paren = upper.match(/^(.+?)\s*\(([^()]+)\)$/);
  const name = paren ? paren[1] : upper;
  const text = paren ? paren[2][0].toUpperCase() + paren[2].slice(1) : "";
  return { name, text, kind: defectKind(name, text) };
}

/** Текст «Типовые дефекты» из карты операции → группы с пунктами. Форматы шаблонов SUPPLYMAP (БД.ОП-ТК):
 *  «1. Группа:» + строки «Название: признак» (бывают склеены без перевода строки — «…ТУТ.Недолив припоя: …»),
 *  группы без номера «Термические дефекты (Раздел 4):», строка-перечень «Типовые дефекты (NG): а; б (признак); в.»
 *  и строки-предложения без двоеточия «Надрезы, царапины или обрыв проволочек жилы.». */
export function parseDefects(raw: unknown): Defects {
  const text = str(raw).replace(/\r/g, "").replace(/([.)»:])(?=[А-ЯЁA-Z«][^.:;\n]{1,70}:)/g, "$1\n");
  const intro: string[] = [];
  const groups: DefectGroup[] = [];
  const current = () => {
    if (!groups.length) groups.push({ title: "", items: [] });
    return groups[groups.length - 1];
  };
  const pushList = (rest: string) => rest.split(";").map(listItem).forEach((item) => item && current().items.push(item));
  for (const source of text.split("\n")) {
    const line = source.trim().replace(/^[-•*–]\s*/, "");
    if (!line) continue;
    if (/^(типовые дефекты|в соответствии|для данной операции)/i.test(line) && (/:\s*$/.test(line) || !line.includes(":"))) {
      intro.push(line.replace(/:\s*$/, ""));
      continue;
    }
    const lead = line.match(/^((?:типовые дефекты|в соответствии)[^:]*):\s*(.*)$/i);
    if (lead) {
      intro.push(lead[1].trim());
      if (lead[2]) pushList(lead[2]);
      continue;
    }
    const group = line.match(/^(?:\d+[.)]\s*)?([^:;]{2,120}?)\s*:\s*$/) || line.match(/^\d+[.)]\s*([^:;]{2,120}?)\s*$/);
    if (group) { groups.push({ title: group[1].trim(), items: [] }); continue; }
    const item = line.replace(/^\d+[.)]\s*/, "").match(/^([^:;]{2,80}?)\s*:\s*(.+)$/);
    if (item) {
      const name = item[1].trim();
      current().items.push({ name, text: item[2].trim(), kind: defectKind(name, item[2]) });
      continue;
    }
    const last = groups.length ? groups[groups.length - 1].items.at(-1) : undefined;
    if (last && /^[a-zа-яё]/.test(line)) { last.text = `${last.text} ${line}`.trim(); continue; }
    if (line.includes(";")) { pushList(line); continue; }
    // «Название. Пояснение.» — пояснение, если начинается как следствие; иначе каждое предложение — свой дефект.
    const sentences = line.split(/(?<=[^.\s]{2}\.)\s+(?=[А-ЯЁA-Z«])/);
    const explains = sentences.length > 1 && EXPLAINS.test(sentences[1]);
    const names = explains ? [sentences[0]] : sentences;
    const note = explains ? sentences.slice(1).join(" ") : "";
    for (const sentence of names) {
      const name = sentence.replace(/\.\s*$/, "");
      current().items.push({ name, text: note, kind: defectKind(name, note) });
    }
  }
  return { intro, groups: groups.filter((item) => item.items.length) };
}

const WIRE = '<rect x="2" y="16" width="28" height="12" rx="3" fill="#8aa1b1"/><rect x="30" y="19" width="20" height="6" fill="#c8793a"/>';
const RED = "#d93025";
const SOLDER = "#b8bec4";

/** Пиктограмма дефекта 72×44: провод/контакт серым-медным, место дефекта — красным. */
export function defectPicto(kind: DefectKind): string {
  const body: Record<DefectKind, string> = {
    bridge: `<rect x="14" y="6" width="10" height="32" fill="${SOLDER}"/><rect x="48" y="6" width="10" height="32" fill="${SOLDER}"/>` +
      `<path d="M24 20 Q36 12 48 20 L48 26 Q36 32 24 26 Z" fill="${RED}" opacity=".85"/>`,
    wicking: `${WIRE}<rect x="16" y="17" width="16" height="10" fill="${SOLDER}"/><path d="M30 10 L16 10 M20 6 L16 10 L20 14" stroke="${RED}" stroke-width="2" fill="none"/>`,
    cold: `${WIRE}<ellipse cx="44" cy="22" rx="14" ry="9" fill="#9aa0a6"/>` +
      [34, 40, 46, 52, 38, 44, 50].map((x, i) => `<circle cx="${x}" cy="${i < 4 ? 19 : 25}" r="1.6" fill="${RED}"/>`).join(""),
    lack: `<path d="M34 8 L34 36 L62 36 L62 8" stroke="#5f6b73" stroke-width="3" fill="none"/><rect x="35.5" y="29" width="25" height="5.5" fill="${SOLDER}"/>` +
      `<line x1="30" y1="15" x2="66" y2="15" stroke="${RED}" stroke-width="1.5" stroke-dasharray="3 2"/><rect x="2" y="16" width="20" height="12" rx="3" fill="#8aa1b1"/><rect x="22" y="19" width="26" height="6" fill="#c8793a"/>`,
    excess: `${WIRE}<ellipse cx="46" cy="22" rx="16" ry="12" fill="${SOLDER}"/><path d="M52 33 L55 42 L58 33" fill="${SOLDER}"/>` +
      `<ellipse cx="46" cy="22" rx="20" ry="16" fill="none" stroke="${RED}" stroke-width="1.8" stroke-dasharray="4 3"/>`,
    crimp: `${WIRE}<path d="M34 14 L60 14 L60 30 L34 30 Z" fill="#c9cfd4" stroke="#5f6b73"/><path d="M40 14 L44 22 L40 30 M50 14 L54 22 L50 30" stroke="${RED}" stroke-width="2" fill="none"/>`,
    strands: `<rect x="2" y="16" width="28" height="12" rx="3" fill="#8aa1b1"/><path d="M30 20 L50 20 M30 22 L52 22 M30 24 L50 24" stroke="#c8793a" stroke-width="2"/>` +
      `<path d="M30 19 L56 8 M30 25 L56 36 M30 22 L60 22" stroke="${RED}" stroke-width="1.8"/>`,
    insulation: `${WIRE}<path d="M8 16 L12 22 L16 16 L20 22 L24 16" stroke="${RED}" stroke-width="2" fill="#fff"/><ellipse cx="22" cy="28" rx="7" ry="3" fill="#3c2f2f" opacity=".7"/>`,
    seating: `<rect x="36" y="6" width="30" height="32" rx="3" fill="#d7dce0" stroke="#5f6b73"/><rect x="16" y="19" width="30" height="6" fill="#c8793a"/>` +
      `<rect x="2" y="17" width="14" height="10" rx="2" fill="#8aa1b1"/><path d="M34 32 L22 32 M26 28 L22 32 L26 36" stroke="${RED}" stroke-width="2" fill="none"/>`,
    housing: `<rect x="12" y="6" width="48" height="32" rx="4" fill="#d7dce0" stroke="#5f6b73"/><path d="M34 6 L30 16 L37 22 L32 32 L35 38" stroke="${RED}" stroke-width="2.2" fill="none"/>`,
    bent: `<rect x="2" y="18" width="24" height="10" rx="2" fill="#d7dce0" stroke="#5f6b73"/><path d="M26 23 L44 23 Q52 23 58 12" stroke="#c8793a" stroke-width="4" fill="none"/>` +
      `<path d="M44 23 L66 23" stroke="${RED}" stroke-width="1.5" stroke-dasharray="3 2"/>`,
    pinout: `<path d="M4 12 L68 32" stroke="#1a73e8" stroke-width="4"/><path d="M4 32 L68 12" stroke="#f9ab00" stroke-width="4"/>` +
      `<path d="M28 14 L44 30 M44 14 L28 30" stroke="${RED}" stroke-width="2.5"/>`,
    geometry: `${WIRE}<path d="M2 36 L50 36 M6 32 L2 36 L6 40 M46 32 L50 36 L46 40" stroke="${RED}" stroke-width="1.6" fill="none"/>` +
      `<text x="56" y="40" font-size="9" fill="${RED}" font-family="sans-serif">L≠</text>`,
    generic: `<path d="M36 5 L66 39 L6 39 Z" fill="#fdecea" stroke="${RED}" stroke-width="2"/><text x="33" y="34" font-size="20" font-weight="700" fill="${RED}" font-family="sans-serif">!</text>`,
  };
  return `<svg viewBox="0 0 72 44" width="72" height="44" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${body[kind]}</svg>`;
}
