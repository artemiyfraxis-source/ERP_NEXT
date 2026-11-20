// Печать PL / DN отгрузки. Поля — форм 04.2 (PL) и 04.1 (DN) по образцам владельца 15.10.2026; вид — свой (владелец
// 20.11.2026: «PL отвратительно выглядит»): A4 книжный, шапка с логотипом и реквизитами Fraxis, номер документа крупно,
// данные отправки сеткой «подпись — значение», плотная таблица позиций с итогами, сводка мест, подписи «сдал / принял»,
// номер страницы в поле листа. Подписи только по-русски; пустая клетка — дописать от руки, ERP заглушек не пишет.
// Значения строит backend (shipping/documents.py); печать — через тот же скрытый iframe, что и остальные бланки
// (print.ts → openPrintHtml). Длинный текст переносится, лист не обрезается: таблица переходит на следующую страницу
// с повтором шапки столбцов.
import { api, type DeliveryNoteForm, type PackingListForm, type ShippingForm, type ShippingFormRow } from "./api";
import logo from "./assets/fraxis-logo.png?inline";
import { openPrintHtml } from "./print";

function escapeHtml(value: string | undefined): string {
  return (value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

// Реквизиты отправителя — как в образце; в ERP их больше нигде нет.
const REQUISITES = `ИНН 5040191181 / КПП 504001001 · ОГРН 1245000065100<br>140188, М.О., г. Жуковский, ул. Молодежная, д. 29<br>` +
  `+7 (495) 085-14-79 доб. 1 · +7 (910) 084-54-25<br>order@fraxis.ru · fraxis.ru`;

// Один вид для PL и DN: рубленый шрифт Windows, 9 pt, табличные цифры, тонкие серые линии, подписи клеток мелким серым.
const FORM_CSS = `
  @page { size: A4 portrait; margin: 11mm 11mm 13mm; }
  @page { @bottom-right { content: "Стр. " counter(page) " из " counter(pages); font: 7.5pt "Segoe UI", Arial, sans-serif; color: #555; } }
  * { box-sizing: border-box; }
  body { margin: 0; color: #111; font: 9pt/1.3 "Segoe UI", Arial, "Liberation Sans", sans-serif; font-variant-numeric: tabular-nums;
    -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  .form { page-break-after: always; break-after: page; }
  .form:last-child { page-break-after: auto; break-after: auto; }
  .top { display: grid; grid-template-columns: 1fr auto; gap: 6mm; align-items: start; padding-bottom: 3mm; border-bottom: 1.5pt solid #111; }
  .brand img { width: 46mm; display: block; }
  .requisites { margin-top: 2mm; font-size: 7.5pt; line-height: 1.35; color: #444; }
  .doc { text-align: right; }
  .doc .kind { font-size: 10.5pt; font-weight: 700; letter-spacing: .04em; }
  .doc .kind small { display: block; font-size: 7.5pt; font-weight: 400; letter-spacing: .08em; color: #555; text-transform: uppercase; }
  .doc .number { font-size: 19pt; font-weight: 700; line-height: 1.1; margin-top: 1.5mm; white-space: nowrap; }
  .doc .meta { margin-top: 1mm; font-size: 9pt; }
  .doc .meta b { font-weight: 700; }
  .doc .place-badge { display: inline-block; margin-top: 2mm; padding: 1mm 3mm; border: 1.5pt solid #111; font-size: 13pt; font-weight: 700; }
  .keep { break-inside: avoid; page-break-inside: avoid; }
  h2 { break-after: avoid; page-break-after: avoid; margin: 4.5mm 0 1.5mm; font-size: 8pt; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: #333; }
  table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  th, td { border: .6pt solid #9a9a9a; padding: 1.2mm 1.6mm; vertical-align: top; text-align: left; overflow-wrap: anywhere; }
  .fields th, .fields td { vertical-align: middle; }
  .fields th { background: #f1f1f1; font-weight: 400; color: #444; font-size: 8pt; }
  .fields td { font-weight: 600; }
  .fields td:empty::after, .boxrow td:empty::after { content: "\\00a0"; }
  .boxrow th { background: #f1f1f1; font-weight: 400; color: #444; font-size: 8pt; text-align: center; }
  .boxrow td { text-align: center; font-size: 12pt; font-weight: 700; height: 9mm; vertical-align: middle; }
  .items thead { display: table-header-group; }
  .items th, .summary th { background: #e6e6e6; font-size: 8pt; font-weight: 700; vertical-align: bottom; }
  .items tr { break-inside: avoid; page-break-inside: avoid; }
  .items tbody tr:nth-child(even) td { background: #fafafa; }
  .num, .items .num { text-align: right; white-space: nowrap; }
  .center { text-align: center; }
  .kb { font-weight: 700; white-space: nowrap; }
  .items tfoot td, .summary tfoot td { font-weight: 700; background: #f1f1f1; border-top: 1.2pt solid #111; }
  .summary .current td { font-weight: 700; background: #fff4cc; }
  .note { margin-top: 2mm; font-size: 8pt; color: #333; }
  .ticks > span { display: block; }
  .check { font-size: 11pt; line-height: 1; vertical-align: -1px; }
  .signs { display: grid; grid-template-columns: repeat(var(--signs, 2), 1fr); gap: 6mm; margin-top: 5mm; break-inside: avoid; page-break-inside: avoid; }
  .sign .who { font-size: 8pt; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: #333; }
  .sign .line { display: grid; grid-template-columns: 22mm 1fr; align-items: end; margin-top: 2.5mm; }
  .sign .line span { font-size: 8pt; color: #555; }
  .sign .line b { border-bottom: .6pt solid #111; min-height: 5mm; padding: 0 1mm; font-weight: 600; }
  .foot { margin-top: 4mm; font-size: 7pt; color: #777; }
`;

// Сетка «подпись — значение» в две пары: ширины одни на всех строках, и в строках на всю ширину тоже.
const FIELD_COLS = `<colgroup><col style="width:19%"><col style="width:31%"><col style="width:19%"><col style="width:31%"></colgroup>`;

function headRow(cells: [string, string, string?][]): string {
  return `<tr>${cells.map(([title, value, css]) => `<th scope="row">${escapeHtml(title)}</th><td${css ? ` class="${css}"` : ""}>${escapeHtml(value)}</td>`).join("")}</tr>`;
}

/** Строка «подпись — значение» на всю ширину. */
function wideRow(title: string, value: string, css = ""): string {
  return `<tr><th scope="row">${escapeHtml(title)}</th><td colspan="3"${css ? ` class="${css}"` : ""}>${escapeHtml(value)}</td></tr>`;
}

function top(kind: string, kindEn: string, number: string, meta: string, badge = ""): string {
  return `<header class="top"><div class="brand"><img src="${logo}" alt="Fraxis"><div class="requisites">${REQUISITES}</div></div>` +
    `<div class="doc"><div class="kind">${escapeHtml(kind)}<small>${escapeHtml(kindEn)}</small></div>` +
    `<div class="number">${escapeHtml(number)}</div><div class="meta">${meta}</div>${badge}</div></header>`;
}

function sign(who: string, lines: [string, string, string?][]): string {
  return `<div class="sign"><div class="who">${escapeHtml(who)}</div>` +
    lines.map(([caption, value, css]) => `<div class="line"><span>${escapeHtml(caption)}</span><b${css ? ` class="${css}"` : ""}>${escapeHtml(value)}</b></div>`).join("") + `</div>`;
}

function signs(blocks: string[]): string {
  return `<div class="signs" style="--signs:${blocks.length}">${blocks.join("")}</div>`;
}

/** КБ и артикул по клеткам; старый ответ без них — «КБ1 FRX.…» целиком в КБ. */
function partCells(row: ShippingFormRow): [string, string] {
  return row.item_index !== undefined ? [row.item_index, row.article ?? ""] : [row.part, ""];
}

const joinNonEmpty = (parts: (string | undefined)[], separator = " · ") => parts.map((part) => (part ?? "").trim()).filter(Boolean).join(separator);

function packingListHtml(form: PackingListForm): string {
  const date = `Дата упаковки <b>${escapeHtml(form.packed_at)}</b>`;
  const badge = `<div class="place-badge">Место ${escapeHtml(form.place)} из ${escapeHtml(form.places)}</div>`;
  const rows = form.rows.map((row, index) => {
    const [kb, article] = partCells(row);
    return `<tr><td class="num">${index + 1}</td><td class="kb">${escapeHtml(kb)}</td><td>${escapeHtml(article)}</td>` +
      `<td class="name">${escapeHtml(row.name)}</td><td>${escapeHtml(row.batch)}</td>` +
      `<td class="num qty">${escapeHtml(row.quantity)}</td><td class="center">${escapeHtml(row.unit)}</td></tr>`;
  }).join("");
  const summary = form.summary?.length
    ? `<div class="keep"><h2>Места отправки</h2><table class="summary"><colgroup><col style="width:8%"><col style="width:15%"><col style="width:13%">` +
      `<col style="width:9%"><col style="width:9%"><col style="width:11%"><col></colgroup>` +
      `<thead><tr><th class="num">Место</th><th>Вид упаковки</th><th>Габариты, см</th><th class="num">Нетто, кг</th>` +
      `<th class="num">Брутто, кг</th><th>Пломба</th><th>Состав</th></tr></thead><tbody>` +
      form.summary.map((place) => `<tr${place.place === form.place ? ` class="current"` : ""}><td class="num">${escapeHtml(place.place)}</td>` +
        `<td>${escapeHtml(place.package)}</td><td>${escapeHtml(place.dims)}</td><td class="num">${escapeHtml(place.net)}</td>` +
        `<td class="num">${escapeHtml(place.gross)}</td><td>${escapeHtml(place.seal)}</td><td>${escapeHtml(place.contents)}</td></tr>`).join("") +
      `</tbody><tfoot><tr><td colspan="4">Всего мест: ${escapeHtml(form.places)}</td><td class="num total-gross">${escapeHtml(form.total_gross)}</td><td colspan="2"></td></tr></tfoot></table></div>`
    : "";
  return `<section class="form pl" aria-label="${escapeHtml(form.title)} место ${escapeHtml(form.place)}">` +
    top("Упаковочная ведомость", "Packing list", form.title, date, badge) +
    `<h2>Отправка</h2><table class="fields parties">${FIELD_COLS}` +
    headRow([["Отправитель", form.shipper ?? "", "shipper"], ["Проект", form.project ?? "", "project"]]) +
    headRow([["Заказчик", form.customer ?? "", "customer"], ["Отправка", form.shipment ?? "", "shipment"]]) +
    headRow([["Грузополучатель", form.consignee ?? "", "consignee"], ["Отгрузочная ведомость", form.dn ?? "", "dn"]]) +
    wideRow("Адрес доставки", form.shipping_address ?? "", "shipping-address") +
    headRow([["Получатель", form.contact ?? "", "contact"], ["Телефон", form.phone ?? "", "phone"]]) +
    headRow([["Доставка", joinNonEmpty([form.terms, form.carrier]), "delivery"], ["Трек / ТТН", form.track ?? "", "track"]]) +
    `</table>` +
    `<h2>Характеристики места</h2>` +
    `<table class="boxrow"><thead><tr><th>Вид упаковки</th><th>Габариты (Ш×Д×В), см</th><th>Вес нетто, кг</th><th>Вес брутто, кг</th><th>Пломба</th></tr></thead>` +
    `<tbody><tr><td class="package">${escapeHtml(form.package)}</td><td class="dims">${escapeHtml(form.dims)}</td><td class="net">${escapeHtml(form.net)}</td>` +
    `<td class="gross">${escapeHtml(form.gross)}</td><td class="seal">${escapeHtml(form.seal)}</td></tr></tbody></table>` +
    `<h2>Содержимое места</h2><table class="items"><colgroup><col style="width:5%"><col style="width:9%"><col style="width:14%"><col>` +
    `<col style="width:15%"><col style="width:9%"><col style="width:6%"></colgroup>` +
    `<thead><tr><th class="num">№</th><th>КБ</th><th>Обозначение</th><th>Наименование</th><th>ГРН (этикетка)</th><th class="num">Кол-во</th><th class="center">Ед.</th></tr></thead>` +
    `<tbody>${rows}</tbody><tfoot><tr><td colspan="5" class="positions">Итого в месте: ${escapeHtml(form.positions || String(form.rows.length))}</td>` +
    `<td colspan="2" class="num quantity">${escapeHtml(form.quantity)}</td></tr></tfoot></table>` +
    summary +
    `<p class="note">Вес короба может различаться на ________ г из-за расхода упаковочных материалов.</p>` +
    signs([
      sign("Сдал — кладовщик", [["ФИО", form.packer, "packer"], ["Подпись", ""], ["Дата отгрузки", form.shipped_at, "shipped"]]),
      sign("Проверил", [["ФИО", ""], ["Подпись", ""], ["Дата", ""]]),
      sign("Принял — получатель", [["ФИО", ""], ["Подпись", ""], ["Дата", ""]]),
    ]) +
    `<div class="foot">Форма/Form 04.2 / Rev.4 / 25.05.2026</div></section>`;
}

const tick = (on: boolean) => `<span class="check">${on ? "☒" : "☐"}</span>`;

function deliveryNoteHtml(form: DeliveryNoteForm): string {
  // Заказ заказчика — «PO # номер»; без него номер проекта как есть.
  const po = form.po_from_card ? `PO # ${form.po}` : form.po;
  const rows = form.rows.map((row, index) => {
    const [kb, article] = partCells(row);
    return `<tr><td class="num">${index + 1}</td><td class="kb">${escapeHtml(kb)}</td><td>${escapeHtml(article)}</td>` +
      `<td class="name">${escapeHtml(row.name)}</td><td class="num qty">${escapeHtml(row.quantity)}</td><td class="center">${escapeHtml(row.unit)}</td>` +
      `<td>${escapeHtml(row.pl)}</td><td>${escapeHtml(row.boxes)}</td></tr>`;
  }).join("");
  return `<section class="form dn" aria-label="${escapeHtml(form.title)}">` +
    top("Отгрузочная ведомость", "Delivery note", form.title, `Дата <b class="date">${escapeHtml(form.date)}</b>`) +
    `<h2>Заказ</h2><table class="fields refs">${FIELD_COLS}` +
    headRow([["Заказчик", form.customer, "customer"], ["Заказ №", po, "po"]]) +
    headRow([["Номер договора", form.contract, "contract"], ["Номер спецификации", form.spec, "spec"]]) +
    headRow([["Номер счёта", form.invoice, "invoice"], ["Время работы", form.hours, "hours"]]) +
    `</table>` +
    `<h2>Данные о поставке</h2><table class="fields delivery">${FIELD_COLS}` +
    wideRow("Условия поставки", form.terms, "terms") +
    wideRow("Адрес доставки", form.shipping_address, "shipping-address") +
    headRow([["Получатель", form.contact, "contact"], ["Телефон", form.phone, "phone"]]) +
    headRow([["Перевозчик / ТК", form.carrier, "carrier"], ["№ ТТН / перевозки", form.track, "track"]]) +
    wideRow("Примечания для ТК", form.tk_note, "tk-note") +
    `</table>` +
    `<h2>Описание</h2><table class="items"><colgroup><col style="width:5%"><col style="width:9%"><col style="width:13%"><col>` +
    `<col style="width:8%"><col style="width:5.5%"><col style="width:13%"><col style="width:10%"></colgroup>` +
    `<thead><tr><th class="num">№</th><th>КБ</th><th>Обозначение</th><th>Наименование</th><th class="num">Кол-во</th><th class="center">Ед.</th>` +
    `<th>Упаковочная ведомость</th><th>Места</th></tr></thead><tbody>${rows}</tbody>` +
    `<tfoot><tr><td colspan="4" class="positions">Итого: ${escapeHtml(form.positions || String(form.rows.length))}</td>` +
    `<td colspan="2" class="num quantity">${escapeHtml(form.quantity)}</td><td colspan="2"></td></tr></tfoot></table>` +
    `<h2>Итоги и УПД</h2><table class="fields totals">${FIELD_COLS}` +
    headRow([["Мест (упаковок)", form.places, "places"], ["Общий вес брутто, кг", form.gross, "gross"]]) +
    `<tr><th scope="row">Номер УПД / ТТН</th><td class="upd">${escapeHtml(form.upd)}</td><th scope="row">УПД</th>` +
    `<td class="upd-ticks"><span class="ticks"><span>${tick(form.upd_edo)} передан по ЭДО</span>` +
    `<span>${tick(form.upd_paper)} бумажный в ${escapeHtml(form.paper_box)}</span></span></td></tr>` +
    `</table>` +
    `<h2>Примечания</h2><table class="fields notes">` +
    `<tr><td>1. Упаковочные ведомости (PL) лежат внутри мест; место (BXN) каждой позиции — в столбце «Места».</td></tr>` +
    (form.note ? `<tr><td class="note-text">2. ${escapeHtml(form.note)}</td></tr>` : "") + `</table>` +
    signs([
      sign("Сдал — кладовщик", [["ФИО", form.packer, "packer"], ["Подпись", ""], ["Дата отгрузки", form.shipped_at, "shipped"]]),
      sign("Принял — получатель / ТК", [["ФИО", ""], ["Подпись", ""], ["Дата", ""]]),
    ]) +
    `<div class="foot">Форма/Form 04.1 Rev.4 / 24.05.2026</div></section>`;
}

/** HTML печати: каждая форма (PL на короб или DN) — с нового листа A4. */
export function buildShippingFormsHtml(forms: ShippingForm[]): string {
  // Заголовок окна печати = имя файла при «Сохранить как PDF»: номер документа, как в папке проекта.
  const title = forms[0]?.title ?? "";
  const sheets = forms.map((form) => form.kind === "pl" ? packingListHtml(form) : deliveryNoteHtml(form));
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>` +
    `<style>${FORM_CSS}</style></head><body>${sheets.join("")}</body></html>`;
}

export function openShippingForms(forms: ShippingForm[]): void {
  openPrintHtml(buildShippingFormsHtml(forms));
}

/** Этикетка одного короба (shipping/documents.py `BoxLabel`): пустые вес и габариты — короб не измерен. */
export interface BoxLabel {
  shipper: string;
  consignee: string;
  address: string;
  /** Человек, которому передать груз, и его телефон; пусто — строки нет. */
  contact?: string;
  phone?: string;
  project: string;
  pl_number: string;
  place: string;
  items: { item_index: string; quantity: string; unit: string }[];
  net: string;
  dims: string;
}

export function fetchBoxLabels(shipmentId: string): Promise<{ labels: BoxLabel[] }> {
  return api<{ labels: BoxLabel[] }>(`/api/shipping/shipments/${shipmentId}/labels`);
}

// Этикетка короба — A6, по одной на лист: образца у владельца нет, раскладка своя. Сверху отправитель, крупно получатель
// и адрес, проект и PL, «Упаковка N/M» — самое крупное (его ищут глазами на складе ТК), состав КБ, внизу вес и габариты.
const LABEL_CSS = `
  @page { size: A6 portrait; margin: 5mm; }
  body { margin: 0; color: #000; font-family: Calibri, Carlito, Arial, sans-serif; font-size: 10pt; }
  .label { page-break-after: always; border: 1.5px solid #000; padding: 3mm; min-height: 128mm; box-sizing: border-box; display: flex; flex-direction: column; gap: 2mm; }
  .label:last-child { page-break-after: auto; }
  .caption { font-size: 7.5pt; text-transform: uppercase; letter-spacing: .04em; }
  .shipper { font-weight: bold; border-bottom: 1px solid #000; padding-bottom: 1.5mm; }
  .consignee { font-size: 13pt; font-weight: bold; line-height: 1.15; }
  .address { line-height: 1.2; }
  .refs { display: grid; grid-template-columns: 1fr 1fr; border-top: 1px solid #000; border-bottom: 1px solid #000; }
  .refs > div { padding: 1mm 0; }
  .refs > div + div { border-left: 1px solid #000; padding-left: 2mm; }
  .refs strong { display: block; font-size: 11pt; }
  .place { font-size: 22pt; font-weight: bold; text-align: center; padding: 1mm 0; }
  table { width: 100%; border-collapse: collapse; }
  th, td { border: 1px solid #000; padding: 1px 3px; text-align: left; }
  th { font-weight: bold; font-size: 8.5pt; }
  td.qty, th.qty { text-align: right; font-variant-numeric: tabular-nums; width: 22%; }
  td.unit, th.unit { width: 14%; }
  .measures { margin-top: auto; display: grid; grid-template-columns: 1fr 1fr; gap: 2mm; }
  .measures strong { display: block; font-size: 12pt; min-height: 5.5mm; border-bottom: 1px solid #000; }
`;

function labelHtml(label: BoxLabel): string {
  const items = label.items.map((item) =>
    `<tr><td>${escapeHtml(item.item_index)}</td><td class="qty">${escapeHtml(item.quantity)}</td><td class="unit">${escapeHtml(item.unit)}</td></tr>`).join("");
  return `<section class="label" aria-label="${escapeHtml(label.place)}">` +
    `<div class="shipper"><span class="caption">Отправитель</span><br>${escapeHtml(label.shipper)}</div>` +
    `<div><span class="caption">Организация</span><div class="consignee">${escapeHtml(label.consignee)}</div>` +
    `<div class="address">${escapeHtml(label.address)}</div></div>` +
    (label.contact || label.phone ? `<div class="recipient"><span class="caption">Получатель</span><br>` +
      `${escapeHtml([label.contact, label.phone].filter(Boolean).join(" · "))}</div>` : "") +
    `<div class="refs"><div><span class="caption">Проект</span><strong>${escapeHtml(label.project)}</strong></div>` +
    `<div><span class="caption">Упаковочная ведомость</span><strong>${escapeHtml(label.pl_number)}</strong></div></div>` +
    `<div class="place">${escapeHtml(label.place)}</div>` +
    `<table><thead><tr><th>№ КБ</th><th class="qty">кол-во</th><th class="unit">ед.</th></tr></thead><tbody>${items}</tbody></table>` +
    `<div class="measures"><div><span class="caption">Вес нетто, кг</span><strong class="net">${escapeHtml(label.net)}</strong></div>` +
    `<div><span class="caption">Габариты, см</span><strong class="dims">${escapeHtml(label.dims)}</strong></div></div>` +
    `</section>`;
}

/** HTML печати этикеток: каждая — отдельный лист A6. */
export function buildBoxLabelsHtml(labels: BoxLabel[]): string {
  const title = labels[0] ? `Этикетки ${labels[0].pl_number}` : "Этикетки";
  return `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>` +
    `<style>${LABEL_CSS}</style></head><body>${labels.map(labelHtml).join("")}</body></html>`;
}

export function openBoxLabels(labels: BoxLabel[]): void {
  openPrintHtml(buildBoxLabelsHtml(labels));
}
