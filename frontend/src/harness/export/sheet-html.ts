// Печатная страница чертежа жгута: лист А4–А1 альбомно, рамка, векторный чертёж и основная надпись.
// Страница уходит в диалог печати браузера («Печать / Сохранить как PDF») — PDF собирает браузер, файла на сервере нет.
import { pageRule } from "../../print";
import { escapeXml } from "./canvas-svg";
import { svgDocument, type SvgSheet } from "./render";
import { frameRect, titleBlockRect, type Placement, type Rect } from "./sheet";

export interface TitleBlockData {
  /** Позиция жгута, например «1.2 Жгут основной». */
  readonly position: string;
  readonly project: string;
  /** Номер сохранённой версии чертежа; null — чертёж ещё не сохранялся. */
  readonly version: number | null;
  /** Уже отформатированная дата печати (ДД.ММ.ГГГГ). */
  readonly date: string;
  /** Включены «Реальные размеры, мм»: сечения по каталогу, длины номинальные. */
  readonly realSizeOn: boolean;
}

export const NOT_TO_SCALE_NOTE = "Длины не в масштабе";

/** Имя файла выгрузки: `<проект>-<позиция>-в<версия>`; без символов, которые нельзя в имени файла. */
export function exportFileBase(project: string, position: string, version: number | null): string {
  const clean = (text: string) => text.replace(/[\\/:*?"<>|\u0000-\u001f]+/g, "").replace(/\s+/g, "_").replace(/^[._]+|[._]+$/g, "");
  return `${clean(project) || "проект"}-${clean(position) || "позиция"}-в${version ?? 0}`;
}

export const formatPrintDate = (date: Date) => date.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });

const mm = (value: number) => `${Math.round(value * 100) / 100}mm`;
const place = (rect: Rect) => `left:${mm(rect.x)};top:${mm(rect.y)};width:${mm(rect.width)};height:${mm(rect.height)}`;

const CSS = `
  html, body { margin: 0; padding: 0; background: #fff; }
  body { font-family: Arial, Helvetica, sans-serif; color: #000; }
  #sheet { position: relative; overflow: hidden; break-inside: avoid; page-break-after: avoid; }
  #frame { position: absolute; box-sizing: border-box; border: 0.5mm solid #000; }
  #drawing { position: absolute; display: block; }
  #title-block { position: absolute; box-sizing: border-box; display: grid; border: 0.5mm solid #000;
    grid-template-columns: 3fr 2fr 1fr 1.2fr 1.4fr; }
  #title-block .cell { box-sizing: border-box; padding: 1.2mm 2mm; border-left: 0.25mm solid #000; display: flex; flex-direction: column; justify-content: center; min-width: 0; }
  #title-block .cell:first-child { border-left: 0; }
  #title-block small { font-size: 6.5pt; color: #444; line-height: 1.1; }
  #title-block b { font-size: 10pt; font-weight: 600; line-height: 1.2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #title-block .note { font-size: 7.5pt; font-weight: 400; white-space: normal; }
`;

/** Печатная страница: `html` целиком (со `<style>` — его можно открыть как файл), `css` — те же правила отдельно. */
export interface SheetPage { readonly html: string; readonly css: string }

/** Лист по результату `placeOnSheet` (успешному). Размеры и места — правилами по id: встроенных `style`-атрибутов нет. */
export function buildSheetHtml(args: {
  readonly svg: SvgSheet; readonly placement: Extract<Placement, { ok: true }>; readonly data: TitleBlockData; readonly title: string;
}): SheetPage {
  const { svg, placement, data } = args;
  const { sheet, box } = placement;
  const frame = frameRect(sheet), block = titleBlockRect(sheet);
  const css = pageRule({ size: `${sheet.id} landscape`, marginMm: 0 }) + CSS +
    `#sheet { width: ${mm(sheet.widthMm)}; height: ${mm(sheet.heightMm - 0.5)}; }` +
    `#frame { ${place(frame)}; } #drawing { ${place(box)}; } #title-block { ${place(block)}; }`;
  const cell = (label: string, value: string, extra = "") =>
    `<div class="cell"><small>${escapeXml(label)}</small><b>${escapeXml(value)}</b>${extra}</div>`;
  const scale = cell("Масштаб", placement.scaleLabel, data.realSizeOn ? `<small class="note">${NOT_TO_SCALE_NOTE}</small>` : "");
  const attributes = `id="drawing" width="${mm(box.width)}" height="${mm(box.height)}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Чертёж жгута"`;
  const html = `<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>${escapeXml(args.title)}</title>` +
    `<style>${css}</style></head><body>` +
    `<div id="sheet"><div id="frame"></div>` +
    svgDocument(svg, attributes) +
    `<div id="title-block">` +
    cell("Позиция", data.position) + cell("Проект", data.project) +
    cell("Версия", data.version === null ? "не сохранена" : String(data.version)) + cell("Дата", data.date) + scale +
    `</div></div></body></html>`;
  return { html, css };
}

/**
 * Открывает лист в новой вкладке и вызывает печать браузера. false — всплывающее окно заблокировано.
 * Страница ERP запрещает встроенные стили (CSP `style-src 'self'`), и окно печати наследует это правило: `<style>` из
 * разметки там не действует, поэтому правила ставятся через CSSOM (`adoptedStyleSheets`), его CSP не блокирует.
 */
export function printSheet(page: SheetPage): boolean {
  const win = window.open("", "_blank");
  if (!win) return false;
  win.document.open();
  win.document.write(page.html);
  win.document.close();
  try {
    // Конструктор берётся из окна печати: таблица стилей должна принадлежать его документу (тип Window его не описывает).
    const SheetCtor = (win as Window & { CSSStyleSheet?: typeof CSSStyleSheet }).CSSStyleSheet ?? CSSStyleSheet;
    const sheet = new SheetCtor();
    sheet.replaceSync(page.css);
    win.document.adoptedStyleSheets = [...win.document.adoptedStyleSheets, sheet];
  } catch { /* старый браузер: остаётся <style> из разметки */ }
  win.focus();
  // Лист — один встроенный SVG: после разметки печатаем, чтобы диалог не открылся над пустой страницей.
  win.setTimeout(() => win.print(), 300);
  return true;
}
