// Выгрузка чертежа жгута: PNG (растр на белом, по габаритам чертежа с полем) и SVG для печати (вектор).
// Чертёж рисует сам редактор через порт (`HarnessExportPort.paint`) — здесь только камера по габаритам и полотно.
import type { ExportBounds, ExportTarget, HarnessExportPort } from "./port";
import { SvgRecordingContext } from "./canvas-svg";

export class ExportError extends Error {}

/** Поле вокруг содержимого, единиц чертежа. */
export const EXPORT_MARGIN = 24;
export const PNG_SCALES = [1, 2, 4] as const;
export type PngScale = (typeof PNG_SCALES)[number];
/** Предел браузера на полотно (сторона) и разумный предел на площадь. */
const MAX_SIDE_PX = 16384, MAX_AREA_PX = 120_000_000;
/** Записываемый SVG рисуется в масштабе, при котором пиксельные штрихи (1 px) ложатся тонкой линией на бумаге. */
export const SVG_ZOOM = 3;

export interface Frame {
  /** Размер кадра в единицах чертежа вместе с полем. */
  readonly width: number; readonly height: number;
  /** Левый верхний угол кадра в единицах чертежа. */
  readonly x: number; readonly y: number;
}

/** Кадр: габариты чертежа плюс поле. Пустой чертёж выгружать нечего. */
export function frameOf(bounds: ExportBounds | null, margin = EXPORT_MARGIN): Frame {
  if (!bounds || ![bounds.minX, bounds.minY, bounds.maxX, bounds.maxY].every(Number.isFinite)) {
    throw new ExportError("На чертеже нет объектов — выгружать нечего.");
  }
  const minX = Math.min(bounds.minX, bounds.maxX), minY = Math.min(bounds.minY, bounds.maxY);
  return {
    x: minX - margin, y: minY - margin,
    width: Math.ceil(Math.abs(bounds.maxX - bounds.minX) + 2 * margin),
    height: Math.ceil(Math.abs(bounds.maxY - bounds.minY) + 2 * margin),
  };
}

/** Размер PNG в пикселях; слишком большой кадр — отказ с советом выбрать меньшее разрешение. */
export function pngSize(frame: Frame, scale: PngScale): { width: number; height: number } {
  const width = frame.width * scale, height = frame.height * scale;
  if (width > MAX_SIDE_PX || height > MAX_SIDE_PX || width * height > MAX_AREA_PX) {
    const lower = PNG_SCALES.filter((candidate) => candidate < scale && frame.width * candidate * frame.height * candidate <= MAX_AREA_PX
      && frame.width * candidate <= MAX_SIDE_PX && frame.height * candidate <= MAX_SIDE_PX);
    throw new ExportError(`Чертёж слишком велик для PNG ${scale}× (${width} × ${height} пикселей).${lower.length ? ` Выберите ${lower.at(-1)}×.` : " Выгрузите его в PDF."}`);
  }
  return { width, height };
}

const cameraFor = (frame: Frame, zoom: number) => ({ zoom, offsetX: -frame.x * zoom, offsetY: -frame.y * zoom });

/** PNG чертежа: белый фон, без сетки и выделения. */
export async function renderPng(port: HarnessExportPort, scale: PngScale): Promise<Blob> {
  const frame = frameOf(port.bounds);
  pngSize(frame, scale);
  const canvas = document.createElement("canvas");
  const target: ExportTarget = {
    clientWidth: frame.width, clientHeight: frame.height,
    get width() { return canvas.width; }, set width(value: number) { canvas.width = value; },
    get height() { return canvas.height; }, set height(value: number) { canvas.height = value; },
    getContext: (kind) => canvas.getContext(kind),
  };
  port.paint(target, cameraFor(frame, 1), scale);
  return new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((blob) => blob ? resolve(blob) : reject(new ExportError("Браузер не смог собрать PNG — выберите меньшее разрешение.")), "image/png"));
}

export interface SvgSheet {
  /** Содержимое `<svg>`: определения и элементы чертежа в пикселях кадра. */
  readonly markup: string;
  /** Размер кадра в пикселях записи (кадр × SVG_ZOOM) — это viewBox. */
  readonly width: number; readonly height: number;
  readonly frame: Frame;
}

/** Векторная запись чертежа для печати. */
/** Поле на печати меньше, чем у PNG: лист уже оставляет воздух вокруг чертежа. */
export const PRINT_MARGIN = 6;
export function renderSvg(port: HarnessExportPort): SvgSheet {
  const frame = frameOf(port.bounds, PRINT_MARGIN);
  const recorder = new SvgRecordingContext();
  const width = Math.ceil(frame.width * SVG_ZOOM), height = Math.ceil(frame.height * SVG_ZOOM);
  const target: ExportTarget = {
    clientWidth: width, clientHeight: height, width, height,
    getContext: () => recorder as unknown as CanvasRenderingContext2D,
  };
  port.paint(target, cameraFor(frame, SVG_ZOOM), 1);
  return { markup: recorder.markup(), width, height, frame };
}

/** Законченный `<svg>`: viewBox равен кадру чертежа (габариты плюс поле), внешних ссылок нет. */
export function svgDocument(sheet: SvgSheet, attributes = ""): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${sheet.width} ${sheet.height}"${attributes ? ` ${attributes}` : ""}>${sheet.markup}</svg>`;
}
