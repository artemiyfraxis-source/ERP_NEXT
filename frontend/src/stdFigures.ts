// Рисунки стандартов к строкам СТО ФРАК-001-2026 (вырезки ГОСТ Р 72182 и IPC/WHMA-A-620E): список загружается один раз
// за загрузку страницы и общий для всех карточек «Типовые дефекты» и печати ЭТК. Рисунки IPC — только для экрана ЭТК и
// цеховой печати; в комплект ТД заказчику — только `customerFigures` (ГОСТ).
import { techcardsApi, type StdFigure } from "./api";
import { stoRowCode } from "./etkDefectArt";

const esc = (value: string): string =>
  value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");

export type StdFigureIndex = ReadonlyMap<string, readonly StdFigure[]>;

const EMPTY: StdFigureIndex = new Map();
let loaded: StdFigureIndex | null = null;
let pending: Promise<StdFigureIndex> | null = null;

export function indexStdFigures(items: readonly StdFigure[]): StdFigureIndex {
  const out = new Map<string, StdFigure[]>();
  for (const figure of items) (out.get(figure.sto_code) ?? out.set(figure.sto_code, []).get(figure.sto_code)!).push(figure);
  for (const list of out.values()) list.sort((a, b) => a.position - b.position);
  return out;
}

/** Уже загруженный список (или null, пока не загружен). */
export function stdFiguresNow(): StdFigureIndex | null {
  return loaded;
}

/** Список рисунков — один запрос на загрузку страницы; ошибка — пустой список (карточки покажут свои рисунки), и
 *  следующий вызов попробует ещё раз. */
export function loadStdFigures(): Promise<StdFigureIndex> {
  if (loaded) return Promise.resolve(loaded);
  pending ??= techcardsApi.stdFigures()
    .then((answer) => (loaded = indexStdFigures(Array.isArray(answer?.items) ? answer.items : [])))
    .catch(() => EMPTY)
    .finally(() => { pending = null; });
  return pending;
}

/** Для тестов: забыть загруженный список. */
export function resetStdFigures(): void {
  loaded = null;
  pending = null;
}

/** Рисунки пункта дефекта по коду строки СТО в начале его названия; нет кода или рисунков — пусто. */
export function figuresForDefect(index: StdFigureIndex | null, name: string): readonly StdFigure[] {
  const code = stoRowCode(name);
  return (code && index?.get(code)) || [];
}

/** Только то, что можно отдать заказчику (комплект ТД): рисунки ГОСТ. Комплект берёт рисунки только через этот фильтр. */
export function customerFigures(figures: readonly StdFigure[]): StdFigure[] {
  return figures.filter((figure) => figure.customer_allowed);
}

export const ROLE_LABELS: Record<StdFigure["role"], string> = { norm: "Норма", defect: "Брак", ref: "" };

/** Печать ЭТК (цех): рисунки пункта в ряд, у каждого — «Норма» / «Брак» и подпись источника. `base` — адрес ERP
 *  (окно печати открывается отдельно, ссылки — абсолютные). */
export function stdFiguresPrintHtml(title: string, figures: readonly StdFigure[], base: string): string {
  if (!figures.length) return "";
  const cell = (figure: StdFigure) => {
    const role = ROLE_LABELS[figure.role];
    const color = figure.role === "norm" ? "#15803d" : "#b91c1c";
    return `<figure style="margin:0;flex:1 1 0;min-width:0;break-inside:avoid">` +
      (role ? `<div style="font-size:8pt;font-weight:700;color:${color}">${role}</div>` : "") +
      `<img src="${esc(base + techcardsApi.stdFigureUrl(figure))}" alt="${esc(figure.caption)}" style="display:block;max-width:100%;max-height:45mm;object-fit:contain"/>` +
      `<figcaption style="font-size:7pt;color:#555">${esc(figure.caption)}</figcaption></figure>`;
  };
  return `<div class="etk-std-print" style="break-inside:avoid;margin:2mm 0 3mm">` +
    `<div style="font-size:9pt;font-weight:600;margin-bottom:1mm">${esc(title)}</div>` +
    `<div style="display:flex;gap:4mm;align-items:flex-start">${figures.map(cell).join("")}</div></div>`;
}
