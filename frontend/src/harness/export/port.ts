// Что редактор отдаёт странице для выгрузки листа: границы чертежа и «нарисуй сцену сюда». Сцена (объекты, слои,
// шаблоны разъёмов, текстуры) живёт внутри поля чертежа; страница видит только этот порт и его не меняет.
import type { EditorCamera } from "../editor/editor-types";

export interface ExportBounds { readonly minX: number; readonly minY: number; readonly maxX: number; readonly maxY: number }

/** Полотно, на которое рисует `paint`: настоящее (PNG) или записывающее (SVG). */
export interface ExportTarget {
  clientWidth: number;
  clientHeight: number;
  width: number;
  height: number;
  getContext(kind: "2d"): CanvasRenderingContext2D | null;
}

export interface HarnessExportPort {
  readonly view: "e4" | "drawing";
  /** Габариты содержимого в единицах чертежа; null — чертёж пуст. */
  readonly bounds: ExportBounds | null;
  /** Знаменатель N режима «Реальные размеры, мм» М 1:N; null — режим выключен. */
  readonly realSizeDenominator: number | null;
  /** Рисует чертёж без сетки, выделения и подсказок на белом фоне; `pixelRatio` — пикселей на пиксель камеры. */
  readonly paint: (target: ExportTarget, camera: EditorCamera, pixelRatio: number) => void;
}
