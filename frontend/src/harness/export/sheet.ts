// Бумага для печати чертежа жгута: форматы А4–А1 (альбомная), поле чертежа над основной надписью и пересчёт
// «единицы чертежа → мм на листе». Без DOM — считается и проверяется отдельно.
//
// Единица чертежа в режиме «Реальные размеры, мм» М 1:N — это N мм изделия, то есть ровно 1 мм на бумаге в
// масштабе М 1:N. Поэтому «в масштабе» = 1 мм на листе на единицу чертежа; «по листу» вписывает чертёж в поле.

export type SheetId = "A4" | "A3" | "A2" | "A1";
export interface SheetFormat { readonly id: SheetId; readonly label: string; readonly widthMm: number; readonly heightMm: number }

/** Альбомные листы, мм. Порядок — от меньшего к большему: по нему ищется наименьший подходящий. */
export const SHEETS: readonly SheetFormat[] = [
  { id: "A4", label: "А4", widthMm: 297, heightMm: 210 },
  { id: "A3", label: "А3", widthMm: 420, heightMm: 297 },
  { id: "A2", label: "А2", widthMm: 594, heightMm: 420 },
  { id: "A1", label: "А1", widthMm: 841, heightMm: 594 },
];

/** Рамка листа отступает от края на 10 мм; основная надпись — полоса под полем чертежа. */
export const FRAME_MARGIN_MM = 10;
export const TITLE_BLOCK_HEIGHT_MM = 18;
/** Воздух между рамкой и чертежом. */
export const DRAWING_PADDING_MM = 4;

export const sheetFormat = (id: SheetId): SheetFormat => SHEETS.find((sheet) => sheet.id === id)!;

export interface Rect { readonly x: number; readonly y: number; readonly width: number; readonly height: number }

/** Рамка листа (внутри полей печати). */
export function frameRect(sheet: SheetFormat): Rect {
  return { x: FRAME_MARGIN_MM, y: FRAME_MARGIN_MM, width: sheet.widthMm - 2 * FRAME_MARGIN_MM, height: sheet.heightMm - 2 * FRAME_MARGIN_MM };
}
/** Основная надпись: нижняя полоса рамки. */
export function titleBlockRect(sheet: SheetFormat): Rect {
  const frame = frameRect(sheet);
  return { x: frame.x, y: frame.y + frame.height - TITLE_BLOCK_HEIGHT_MM, width: frame.width, height: TITLE_BLOCK_HEIGHT_MM };
}
/** Поле чертежа: рамка без основной надписи и без воздуха по краям. */
export function drawingArea(sheet: SheetFormat): Rect {
  const frame = frameRect(sheet);
  return {
    x: frame.x + DRAWING_PADDING_MM, y: frame.y + DRAWING_PADDING_MM,
    width: frame.width - 2 * DRAWING_PADDING_MM,
    height: frame.height - TITLE_BLOCK_HEIGHT_MM - 2 * DRAWING_PADDING_MM,
  };
}

export type ScaleMode = { readonly kind: "fit" } | { readonly kind: "true" };

export interface RealSizeSetting { readonly denominator: number }

export type Placement =
  | {
    readonly ok: true; readonly sheet: SheetFormat;
    /** Миллиметров бумаги на единицу чертежа. */
    readonly mmPerUnit: number;
    /** Где на листе лежит чертёж (мм от левого верхнего угла листа). */
    readonly box: Rect;
    /** Знаменатель масштаба изделия М 1:k; null — масштаб не определён (реальные размеры выключены). */
    readonly denominator: number | null;
    readonly scaleLabel: string;
  }
  | { readonly ok: false; readonly hint: string; readonly suggestion: SheetId | null };

const roundTo = (value: number, digits: number) => Math.round(value * 10 ** digits) / 10 ** digits;
const formatNumber = (value: number) => String(roundTo(value, value >= 10 ? 0 : value >= 1 ? 1 : 2)).replace(".", ",");

export function scaleLabel(denominator: number | null, mode: ScaleMode["kind"]): string {
  if (denominator === null) return "по листу";
  return mode === "true" ? `М 1:${formatNumber(denominator)}` : `по листу, около М 1:${formatNumber(denominator)}`;
}

/** Размер чертежа на листе при `mmPerUnit` мм на единицу. */
const sizeOnSheet = (width: number, height: number, mmPerUnit: number) => ({ width: width * mmPerUnit, height: height * mmPerUnit });
const fits = (sheet: SheetFormat, width: number, height: number) => {
  const area = drawingArea(sheet);
  return width <= area.width + 1e-9 && height <= area.height + 1e-9;
};

/** Наименьший лист, на котором чертёж `width × height` мм помещается в поле. */
export function smallestSheetFor(widthMm: number, heightMm: number): SheetFormat | null {
  return SHEETS.find((sheet) => fits(sheet, widthMm, heightMm)) ?? null;
}

/**
 * Расставляет чертёж (в единицах чертежа) на листе.
 * - «по листу»: вписывается в поле целиком, по центру; масштаб изделия считается, если включены реальные размеры;
 * - «в масштабе»: 1 мм на единицу, нужны реальные размеры; не помещается — отказ с подсказкой, какой лист нужен.
 */
export function placeOnSheet(
  content: { readonly width: number; readonly height: number },
  sheetId: SheetId,
  mode: ScaleMode,
  realSize: RealSizeSetting | null,
): Placement {
  const sheet = sheetFormat(sheetId);
  const width = Math.max(content.width, 1e-6), height = Math.max(content.height, 1e-6);
  const area = drawingArea(sheet);
  let mmPerUnit: number;
  if (mode.kind === "true") {
    if (!realSize) {
      return { ok: false, suggestion: null, hint: "Масштаб М 1:N доступен при включённых «Реальных размерах, мм» на вкладке «Вид». Выберите «По листу» или включите их." };
    }
    mmPerUnit = 1;
    const size = sizeOnSheet(width, height, mmPerUnit);
    if (!fits(sheet, size.width, size.height)) {
      const fitting = smallestSheetFor(size.width, size.height);
      const need = `${formatNumber(size.width)} × ${formatNumber(size.height)} мм`;
      return {
        ok: false, suggestion: fitting?.id ?? null,
        hint: fitting
          ? `Чертёж в масштабе М 1:${formatNumber(realSize.denominator)} занимает ${need} и не помещается на ${sheet.label}. Подойдёт ${fitting.label}.`
          : `Чертёж в масштабе М 1:${formatNumber(realSize.denominator)} занимает ${need} и не помещается даже на А1. Выберите «По листу» или увеличьте знаменатель масштаба на вкладке «Вид».`,
      };
    }
  } else {
    mmPerUnit = Math.min(area.width / width, area.height / height);
  }
  const size = sizeOnSheet(width, height, mmPerUnit);
  const box = { x: area.x + (area.width - size.width) / 2, y: area.y + (area.height - size.height) / 2, width: size.width, height: size.height };
  const denominator = realSize ? realSize.denominator / mmPerUnit : null;
  return { ok: true, sheet, mmPerUnit, box, denominator, scaleLabel: scaleLabel(denominator, mode.kind) };
}
