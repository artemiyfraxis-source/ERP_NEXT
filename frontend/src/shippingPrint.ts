// Печать PL / DN отгрузки по образцу владельца (лист «ТЕСТ» таблицы «учет ТМЦ», PL-DN_№228К.20.2-2.docx):
// один лист A4 на форму, сетка из пяти колонок как в образце — шапка «подпись | значение | реквизиты»,
// таблица «GRN | № КБ | ОПИСАНИЕ | кол-во | Ед. изм.» в чёрной рамке, пустые строки для дописки от руки,
// «Штамп / подпись кладовщика». Данные строит backend (shipping/documents.py); печать — через тот же
// скрытый iframe, что и остальные бланки (print.ts → openPrintHtml).
import type { ShippingForm } from "./api";
import { openPrintHtml } from "./print";

function escapeHtml(value: string | undefined): string {
  return (value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/** Адрес сайта в реквизитах — ссылкой, как в образце (синий, подчёркнут). */
function sideHtml(text: string): string {
  return escapeHtml(text).replace(/https:\/\/[^\s<]+/g, (url) => `<a href="${url}">${url}</a>`);
}

// Ширины колонок образца (docx gridCol, twips): 2451 / 1794 / 5111 / 696 / 818.
const FORM_CSS = `
  @page { size: A4 portrait; margin: 9mm; }
  body { margin: 0; color: #000; font-family: Calibri, Carlito, Arial, sans-serif; font-size: 11pt; }
  .form { page-break-after: always; }
  .form:last-child { page-break-after: auto; }
  table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  col.c1 { width: 25%; } col.c2 { width: 16.5%; } col.c3 { width: 44.5%; } col.c4 { width: 6.4%; } col.c5 { width: 7.6%; }
  td, th { border: 1px solid #ccc; padding: 2px 4px; height: 19px; vertical-align: bottom; text-align: left; font-weight: normal; }
  .title td { font-size: 15pt; font-weight: bold; padding-top: 6px; }
  .label { font-weight: bold; white-space: nowrap; }
  .value { font-weight: bold; }
  .framed td { border-color: #000; }
  .measured { color: #980000; }
  .side { font-size: 10pt; }
  .side a { color: #00f; text-decoration: underline; }
  .gap td { border-color: #ccc; height: 12px; }
  .items th, .items td { border: 1px solid #000; }
  .items th { font-weight: bold; }
  .items .desc { font-size: 10pt; }
  .items .mid { text-align: center; }
  .sign td { padding-top: 10px; }
  .sign .signer { text-align: right; }
`;

const MEASURED = /^(Габариты|Вес)/;

function formHtml(form: ShippingForm): string {
  // Как в образце: блок с реквизитами отправителя — в чёрной рамке (в DN «Заказчик» над ней), строка без реквизитов —
  // значение на всю ширину; габариты и вес короба — тёмно-красным (их меряют на упаковке).
  const framedFrom = form.head.findIndex((row) => row.side);
  const head = form.head.map((row, index) => {
    const frame = framedFrom >= 0 && index >= framedFrom ? ' class="framed"' : "";
    return row.side
      ? `<tr${frame}><td class="label">${escapeHtml(row.label)}</td>` +
        `<td class="value${MEASURED.test(row.label) ? " measured" : ""}">${escapeHtml(row.value)}</td>` +
        `<td class="side" colspan="3">${sideHtml(row.side)}</td></tr>`
      : `<tr${frame}><td class="label">${escapeHtml(row.label)}</td><td class="value" colspan="4">${escapeHtml(row.value)}</td></tr>`;
  }).join("");
  // Отступы как в образце: в PL пустая строка под заголовком, в DN «Заказчик» сразу под ним и две пустые перед таблицей.
  const gap = `<tr class="gap"><td></td><td></td><td colspan="3"></td></tr>`;
  const columns = `<tr class="items">${form.columns.map((column, index) =>
    `<th${index === 1 ? ' class="mid"' : ""}>${escapeHtml(column)}</th>`).join("")}</tr>`;
  const rows = form.rows.map((row) => {
    const [grn, kb, name, quantity, unit] = row;
    return `<tr class="items"><td>${escapeHtml(grn)}</td><td class="mid">${escapeHtml(kb)}</td>` +
      `<td class="desc">${escapeHtml(name)}</td><td class="mid">${escapeHtml(quantity)}</td><td class="mid">${escapeHtml(unit)}</td></tr>`;
  }).join("");
  const blanks = `<tr class="items blank"><td></td><td></td><td></td><td></td><td></td></tr>`.repeat(form.blank_rows);
  return `<section class="form"><table aria-label="${escapeHtml(form.title)}">` +
    `<colgroup><col class="c1"><col class="c2"><col class="c3"><col class="c4"><col class="c5"></colgroup>` +
    `<tbody><tr class="title"><td colspan="3">${escapeHtml(form.title)}</td><td></td><td></td></tr>` +
    (form.kind === "dn" ? "" : gap) + head + gap.repeat(form.kind === "dn" ? 2 : 1) +
    columns + rows + blanks + gap +
    `<tr class="sign"><td colspan="2">${escapeHtml(form.sign_label)}</td><td class="signer">${escapeHtml(form.sign_name)}</td><td></td><td></td></tr>` +
    `</tbody></table></section>`;
}

/** HTML печати: каждая форма (PL на короб или DN) — отдельный лист. */
export function buildShippingFormsHtml(forms: ShippingForm[]): string {
  // Заголовок окна печати = имя файла при «Сохранить как PDF»: номер документа, как в папке проекта.
  const title = forms[0]?.head.find((row) => row.label.startsWith("Номер документа"))?.value || forms[0]?.title || "";
  return `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>` +
    `<style>${FORM_CSS}</style></head><body>${forms.map(formHtml).join("")}</body></html>`;
}

export function openShippingForms(forms: ShippingForm[]): void {
  openPrintHtml(buildShippingFormsHtml(forms));
}
