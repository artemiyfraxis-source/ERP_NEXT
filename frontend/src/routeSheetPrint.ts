// Сопроводительный лист изделия: по операциям — исполнитель, дата, партии материалов, отметки ОТК и ВП.
import { etkOperationTitle } from "./etkPrint";
import { cleanCodes, esc } from "./tdPrintSheets";
import type { RouteSheetOptions, TdPrintModel } from "./tdTypes";

const CSS = `
  @page { size: A4 landscape; margin: 8mm; }
  body { font-family: Arial, Helvetica, sans-serif; font-size: 9pt; color: #000; margin: 0; }
  h1 { font-size: 15pt; margin: 0 0 3mm; }
  table { width: 100%; border-collapse: collapse; margin: 2mm 0 4mm; }
  thead { display: table-header-group; }
  tr { page-break-inside: avoid; }
  th, td { border: 0.25mm solid #000; padding: 1mm 1.5mm; text-align: left; vertical-align: top; }
  th { background: #eee; }
  td.tall { height: 14mm; }
  .footer { margin-top: 6mm; font-size: 10pt; }
`;

export function buildRouteSheetHtml(model: TdPrintModel, options: RouteSheetOptions): string {
  const { issue, card } = model;
  const document = card.document as { operations?: unknown } | null | undefined;
  const operations = Array.isArray(document?.operations) ? document.operations as Record<string, unknown>[] : [];
  const product = cleanCodes(String(card.product_name ?? ""));
  const info: Array<[string, string]> = [
    ["Изделие", `${product} · ${card.project ?? ""} ${card.item_index ?? ""}`.trim()],
    ["Обозначение ВТД", issue.designations.vtd],
    ["Изменение", String(issue.change_no)],
    ["Ревизия ЭТК", String(issue.etk_revision)],
    ["Заводской номер", options.serial], ["Партия", options.batch], ["Количество", options.qty],
  ];
  const head = `<table><tbody>${[0, 4].map((from) => `<tr>${info.slice(from, from + 4).map(([label, value]) => `<th>${esc(label)}</th><td>${esc(value || "")}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  const body = operations.map((operation, index) =>
    `<tr><td>${String((index + 1) * 5).padStart(3, "0")}</td><td>${esc(cleanCodes(etkOperationTitle(operation)))}</td><td class="tall"></td><td></td><td></td><td></td><td></td></tr>`).join("");
  const table = `<table><thead><tr><th>№</th><th>Операция</th><th>Исполнитель (ФИО, подпись)</th><th>Дата</th><th>Партии материалов</th><th>Отметка ОТК</th><th>Отметка ВП</th></tr></thead><tbody>${body}</tbody></table>`;
  return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(`Сопроводительный лист ${product}`)}</title><style>${CSS}</style></head><body>` +
    `<h1>Сопроводительный лист</h1>${head}${table}<p class="footer">Изделие принял: ОТК ____________ / ________ ВП ____________ / ________ Дата ______</p></body></html>`;
}
