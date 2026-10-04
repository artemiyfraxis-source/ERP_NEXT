// «Типовые дефекты» в форме правки операции: текст карточки ↔ группы с дефектами. Разбирает тот же parseDefects, что
// и показ ЭТК (что видно в ЭТК, то и правится). Хранится по-прежнему текст: его печатает ЭТК и читает сборка
// (etk_builder._labelled_defects ждёт заголовки групп «1. Группа:»). Поэтому обратно без потерь: неизменённый текст
// уходит байт в байт, неизменённая группа — своими исходными строками (номер заголовка — по новому порядку),
// строки вне групп (вступление, заголовок без дефектов «Согласно IPC…:») — на своём месте, изменённое — каноном
// «N. Группа:» / «Дефект: причина».
import { parseDefects, type DefectGroup, type DefectItem, type Defects } from "./etkOperation";

export type { DefectItem };
/** Группа формы; `origin` — её номер в разборе исходного текста (null — новая). */
export type DefectEditGroup = DefectGroup & { origin: number | null };
export type DefectsEdit = { intro: string[]; groups: DefectEditGroup[] };

/** Черновик формы: группы и дефекты, как их показывает ЭТК. */
export function defectsDraft(text: string): DefectsEdit {
  const parsed = read(text);
  return { intro: parsed.intro, groups: parsed.groups.map((group, origin) => ({ ...group, origin })) };
}

/** Правится ли текст списком: пустой или разобран хотя бы в один дефект. Иначе форма даёт текст как есть. */
export function defectsStructured(text: string): boolean {
  return !text.trim() || parseDefects(text).groups.some((group) => group.items.length > 0);
}

/** Список → текст. `original` — текст карточки до правки. */
export function serializeDefects(draft: DefectsEdit, original: string): string {
  const next = tidy(draft);
  const before = read(original);
  if (sameDefects(next, before)) return original;
  const blocks = rawBlocks(original, before);
  const numbered = !blocks || !blocks.some((block) => block.heading && block.groups.length)
    || blocks.some((block) => block.heading && block.groups.length && NUMBER.test(block.lines[0]));
  const out: string[] = [];
  let position = 0;

  const writeGroup = (group: DefectEditGroup) => {
    if (group.title) out.push(`${numbered ? `${++position}. ` : ""}${clean(group.title)}:`);
    out.push(...group.items.map(itemLine));
  };
  if (!blocks) {
    out.push(...next.intro);
    next.groups.forEach(writeGroup);
    return out.join("\n");
  }

  const written = new Set<RawBlock>();
  const writeBlock = (block: RawBlock) => {
    written.add(block);
    const mine = next.groups.filter((group) => group.origin !== null && block.groups.includes(group.origin));
    const intact = mine.length === block.groups.length
      && mine.every((group, index) => group.origin === block.groups[index] && sameGroup(group, before.groups[block.groups[index]]));
    if (intact) {
      const [first, ...rest] = block.lines;
      const renumbered = block.heading && NUMBER.test(first)
        ? first.replace(NUMBER, (_, lead: string, mark: string) => `${lead}${++position}${mark}`) : first;
      out.push([renumbered, ...rest].join("\n"));
      return;
    }
    out.push(...block.intro);
    mine.forEach(writeGroup);
  };
  for (const group of next.groups) {
    if (group.origin === null) { writeGroup(group); continue; }
    const at = blocks.findIndex((block) => block.groups.includes(group.origin as number));
    if (at < 0) { writeGroup(group); continue; }
    blocks.slice(0, at + 1).forEach((block) => { if (!written.has(block)) writeBlock(block); });
  }
  blocks.forEach((block) => { if (!written.has(block)) writeBlock(block); });
  return out.join("\n");
}

const NUMBER = /^(\s*)\d+([.)])/;

type RawBlock = { lines: string[]; heading: boolean; intro: string[]; groups: number[] };

/** Пустые дефекты и группы без дефектов не сохраняются (как пустые строки оснастки и шагов на сервере). */
function tidy(draft: DefectsEdit): DefectsEdit {
  const groups = draft.groups
    .map((group) => ({
      title: group.title.trim(),
      items: group.items.map((item) => ({ name: item.name.trim(), text: item.text.trim() })).filter((item) => item.name || item.text),
      origin: group.origin,
    }))
    .filter((group) => group.items.length);
  return { intro: draft.intro.map((line) => line.trim()).filter(Boolean), groups };
}

/** Разбор текста без пустых дефектов; номера групп черновика (`origin`) — по нему. */
function read(text: string): DefectsEdit {
  const parsed = parseDefects(text);
  return tidy({ intro: parsed.intro, groups: parsed.groups.map((group) => ({ ...group, origin: null })) });
}

function sameGroup(a: DefectGroup, b: DefectGroup): boolean {
  return a.title === b.title && a.items.length === b.items.length
    && a.items.every((item, index) => item.name === b.items[index].name && item.text === b.items[index].text);
}

function sameDefects(a: Defects, b: Defects): boolean {
  return a.intro.length === b.intro.length && a.intro.every((line, index) => line === b.intro[index])
    && a.groups.length === b.groups.length && a.groups.every((group, index) => sameGroup(group, b.groups[index]));
}

/** Исходный текст по блокам: до первой группы, затем от строки, которую parseDefects читает заголовком группы
 *  (проверка пробным дефектом под ней: заголовок без дефектов разбор отбрасывает), до следующей такой. У блока —
 *  номера его групп в разборе всего текста. Разбор строчный, поэтому блоки вместе дают тот же разбор; если вдруг
 *  нет — null, и текст пишется каноном целиком. */
function rawBlocks(text: string, whole: Defects): RawBlock[] | null {
  const blocks: RawBlock[] = [{ lines: [], heading: false, intro: [], groups: [] }];
  for (const line of text ? text.split("\n") : []) {
    if (opensGroup(line)) blocks.push({ lines: [line], heading: true, intro: [], groups: [] });
    else blocks[blocks.length - 1].lines.push(line);
  }
  let index = 0;
  const intro: string[] = [];
  for (const block of blocks) {
    const parsed = read(block.lines.join("\n"));
    block.intro = parsed.intro;
    intro.push(...parsed.intro);
    for (const group of parsed.groups) {
      if (!whole.groups[index] || !sameGroup(group, whole.groups[index])) return null;
      block.groups.push(index++);
    }
  }
  const same = index === whole.groups.length && intro.length === whole.intro.length && intro.every((line, at) => line === whole.intro[at]);
  return same ? blocks.filter((block) => block.lines.length) : null;
}

function opensGroup(line: string): boolean {
  if (!line.trim()) return false;
  const parsed = parseDefects(`${line}\nПроба: проба`);
  return !parsed.intro.length && Boolean(parsed.groups[0]?.title);
}

/** Одна строка без «:» и «;» — иначе разбор разрежет название или примет его за заголовок группы. */
function clean(value: string): string {
  return value.replace(/\s*\n\s*/g, " ").replace(/\s*[:;]\s*/g, ", ").trim();
}

function itemLine(item: DefectItem): string {
  const name = clean(item.name).replace(/^\d+[.)]\s*/, "");
  const text = item.text.replace(/\s*\n\s*/g, " ").trim();
  if (!name) return text;
  // Название без причины со строчной буквы разбор приклеил бы к причине предыдущего дефекта.
  return text ? `${name}: ${text}` : name[0].toUpperCase() + name.slice(1);
}
