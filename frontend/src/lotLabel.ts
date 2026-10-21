// Этикетка партии склада (владелец, 19.10.2026: «кнопки "печать этикетки"»): ГРН крупно, артикул, наименование,
// количество, место, склад, дата поступления. Тот же механизм, что у этикетки коробки: 58×40 мм, `openPrintHtml`,
// одна партия — одна страница, несколько партий — одна печать.
import { warehouseApi } from "./api";
import { formatDay } from "./format";
import { openPrintHtml, pageRule } from "./print";

export interface LotLabelData {
  grn: string;
  package?: string;
  article: string;
  name: string;
  /** Количество с единицей — как показывает карточка («650 шт»). */
  quantity: string;
  place: string;
  warehouse: string;
  /** Дата поступления, ISO; пусто — строка не печатается. */
  receivedAt?: string | null;
}

function escapeHtml(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

const LABEL_CSS = `
  body { font-family: Arial, Helvetica, sans-serif; margin: 0; color: #000; }
  .label { padding: 1mm; page-break-after: always; overflow: hidden; height: 35mm; }
  .label:last-child { page-break-after: auto; }
  .grn { font-size: 16pt; font-weight: 700; line-height: 1.1; word-break: keep-all; }
  .article { font-size: 10pt; font-weight: 700; margin-top: 1mm; }
  .name { font-size: 8pt; line-height: 1.15; max-height: 9mm; overflow: hidden; }
  .row { font-size: 9pt; margin-top: 1mm; }
  .qty { font-size: 12pt; font-weight: 700; }
  .meta { font-size: 8pt; margin-top: 1mm; }
`;

function labelHtml(label: LotLabelData): string {
  const grn = label.package ? `${label.grn} / ${label.package}` : label.grn;
  const received = label.receivedAt ? formatDay(label.receivedAt) : "";
  return `<div class="label">` +
    `<div class="grn">${escapeHtml(grn)}</div>` +
    `<div class="article">${escapeHtml(label.article)}</div>` +
    (label.name && label.name !== label.article ? `<div class="name">${escapeHtml(label.name)}</div>` : "") +
    `<div class="row"><span class="qty">${escapeHtml(label.quantity)}</span> · ${escapeHtml(label.place || "без места")} · ${escapeHtml(label.warehouse)}</div>` +
    (received ? `<div class="meta">Поступило ${escapeHtml(received)}</div>` : "") +
    `</div>`;
}

/** Этикетки 58×40 мм (термопринтер): по одной странице на партию. */
export function buildLotLabelsHtml(labels: LotLabelData[]): string {
  return `<!doctype html><html><head><meta charset="utf-8"><title>Этикетка партии</title>` +
    `<style>${LABEL_CSS}${pageRule({ size: "58mm 40mm", marginMm: 2 })}</style></head><body>` +
    `${labels.map(labelHtml).join("")}</body></html>`;
}

export function printLotLabels(labels: LotLabelData[]): void {
  if (labels.length) openPrintHtml(buildLotLabelsHtml(labels));
}

/** Дата поступления партии — самая ранняя запись её журнала; нет связи или записей — пусто (этикетка без даты). */
export async function lotReceivedAt(grn: string, pkg: string): Promise<string | null> {
  try {
    const { rows } = await warehouseApi.journal(grn);
    const times = rows
      .filter((row) => {
        const [rowGrn, rowPkg = ""] = row.lot.split(" / ");
        return rowGrn.trim().toUpperCase() === grn.toUpperCase() && (!rowPkg || !pkg || rowPkg.trim() === pkg);
      })
      .map((row) => row.occurred_at)
      .filter(Boolean)
      .sort();
    return times[0] ?? null;
  } catch {
    return null;
  }
}
