// Печать PL / DN отгрузки по образцам владельца 15.10.2026 — PL533К.10-1.docx (форма 04.2 / Rev.4 / 25.05.2026) и
// DN533К.10-1.docx (форма 04.1 / Rev.4 / 24.05.2026): логотип Fraxis и реквизиты сверху, номер документа клетками,
// нумерованные разделы с серой полосой заголовка, подписи по-русски с английскими курсивом ниже, блок подписей внизу
// листа. Значения строит backend (shipping/documents.py); печать — через тот же скрытый iframe, что и остальные бланки
// (print.ts → openPrintHtml). В английских подписях образца исправлены опечатки («DECRIPTION», «PACKAGE SIZE, mm» при
// габаритах в см).
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
const REQUISITES = `ИНН 5040191181 / КПП 504001001<br>ОГРН 1245000065100<br>Адрес:140188, М.О., г. Жуковский, ул. Молодежная, д. 29<br>` +
  `Сайт: <a href="https://fraxis.ru">https://fraxis.ru</a><br>Телефон для связи: +7 (495) 085-14-79 Доб.1<br>+7 (910) 084-54-25<br>` +
  `Электронный адрес: <a href="mailto:order@fraxis.ru">order@fraxis.ru</a>`;

// Образец: Cambria 10 pt, английские подписи — курсив 8 pt тёмно-синим, серые клетки подписей D9D9D9, поля 12,7 мм.
const FORM_CSS = `
  @page { size: A4 portrait; margin: 12.7mm; }
  /* Серые клетки печатаются и без галочки «Фон» в окне печати. */
  body { margin: 0; color: #000; font-family: Cambria, Caladea, Georgia, serif; font-size: 10pt; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  .form { height: 270mm; display: flex; flex-direction: column; page-break-after: always; overflow: hidden; }
  .form:last-child { page-break-after: auto; }
  .top { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 4mm; }
  .brand { width: 80mm; text-align: center; }
  .brand img { width: 80mm; display: block; }
  .brand h1 { font-size: 15pt; margin: 4mm 0 0; letter-spacing: .02em; }
  .brand .en { font-size: 12pt; color: #1f497d; }
  .requisites { text-align: right; line-height: 1.35; }
  a { color: #00f; text-decoration: underline; }
  table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  td, th { border: 1px solid #000; padding: 1px 4px; vertical-align: middle; font-weight: normal; }
  .grey, th { background: #d9d9d9; }
  .lbl { text-align: right; line-height: 1.15; }
  .lbl.left { text-align: left; }
  .lbl.mid, th { text-align: center; line-height: 1.15; }
  i { display: block; font-size: 8pt; font-weight: bold; color: #1f497d; }
  .section { margin-top: 4mm; }
  .section td.head { background: #d9d9d9; font-size: 12pt; font-weight: bold; text-align: left; }
  .section td.head i { display: inline; margin-left: 2px; }
  .docno { width: 58mm; margin-top: 0; }
  .docno td.head { background: #fff; }
  .num td { font-size: 12pt; font-weight: bold; text-align: center; }
  .val { text-align: center; }
  .strong { font-weight: bold; font-size: 11pt; }
  .items td { height: 6mm; }
  .items .name { text-align: left; }
  .box { font-size: 18pt; font-weight: bold; text-align: center; }
  .sign { background: #d9d9d9; border: 0; border-collapse: separate; border-spacing: 0 4px; padding: 0 3mm; }
  .sign td { border: 0; }
  .sign td.white { background: #fff; height: 8mm; }
  .sign td.big { font-size: 18pt; }
  .spacer { flex: 1; }
  .bottom td.white { background: #fff; }
  .foot { display: flex; justify-content: space-between; font-size: 8pt; font-weight: bold; margin-top: 1mm; }
  .check { font-size: 20pt; text-align: center; line-height: 1; }
`;

/** Подпись клетки, как в образце: по-русски, ниже английская курсивом. */
function label(ru: string, en: string): string {
  return `${escapeHtml(ru)}<i>‘${escapeHtml(en)}’</i>`;
}

function sectionHead(colspan: number, ru: string, en: string): string {
  return `<tr><td class="head" colspan="${colspan}">${escapeHtml(ru)} <i>‘${escapeHtml(en)}’</i></td></tr>`;
}

function top(ru: string, en: string): string {
  return `<div class="top"><div class="brand"><img src="${logo}" alt="Fraxis"><h1>${escapeHtml(ru)}</h1>` +
    `<div class="en">${escapeHtml(en)}</div></div><div class="requisites">${REQUISITES}</div></div>`;
}

function foot(form: string, sheet: number): string {
  return `<div class="foot"><span>${escapeHtml(form)}</span><span>${sheet}</span></div>`;
}

/** Строки «Описания»: позиции и одна пустая пронумерованная — дописать от руки, как в образце. */
function itemRows(rows: ShippingFormRow[], cells: (row: ShippingFormRow) => string[]): string {
  const blank: ShippingFormRow = { part: "", name: "", quantity: "", unit: "", batch: "", pl: "", boxes: "" };
  return [...rows, blank].map((row, index) => {
    const [part, name, ...rest] = cells(row);
    return `<tr><td class="val">${index + 1}.</td><td class="val">${escapeHtml(part)}</td><td class="name">${escapeHtml(name)}</td>` +
      rest.map((value) => `<td class="val">${escapeHtml(value)}</td>`).join("") + "</tr>";
  }).join("");
}

function packingListHtml(form: PackingListForm, sheet: number): string {
  const [prefix, project, dash, index] = form.number;
  return `<section class="form" aria-label="${escapeHtml(form.title)}">${top("УПАКОВОЧНАЯ ВЕДОМОСТЬ", "PACKING LIST")}` +
    `<table class="section docno"><colgroup><col style="width:22%"><col style="width:46%"><col style="width:12%"><col style="width:20%"></colgroup>` +
    sectionHead(4, "1. Номер документа", "Doc. №") +
    `<tr class="num"><td>${escapeHtml(prefix)}</td><td>${escapeHtml(project)}</td><td>${escapeHtml(dash)}</td><td>${escapeHtml(index)}</td></tr></table>` +
    `<table class="section place"><colgroup><col style="width:30%"><col style="width:8%"><col style="width:4%"><col style="width:4%"><col style="width:8%"><col style="width:26%"><col style="width:20%"></colgroup>` +
    sectionHead(7, "2. ХАРАКТЕРИСТИКИ МЕСТА", "PACKAGE DESCRIPTION") +
    `<tr><td class="grey lbl">${label("УПАКОВКА №", "PACKAGE #")}</td><td class="val">BXN</td><td class="val">${escapeHtml(form.place)}</td>` +
    `<td class="val">/</td><td class="val">${escapeHtml(form.places)}</td><td class="grey lbl">${label("ВИД УПАКОВКИ", "PACKAGE TYPE")}</td>` +
    `<td class="val">${escapeHtml(form.package)}</td></tr>` +
    `<tr><td class="grey lbl">${label("ГАБАРИТЫ УПАКОВКИ (ШхДхВ), см", "PACKAGE SIZE, cm")}</td><td class="val strong dims" colspan="4">${escapeHtml(form.dims)}</td>` +
    `<td class="grey lbl">${label("ВЕС ИЗДЕЛИЙ (нетто), кг", "PRODUCT WEIGHT, kg")}</td><td class="val strong net">${escapeHtml(form.net)}</td></tr></table>` +
    `<table class="section items"><colgroup><col style="width:5.5%"><col style="width:16%"><col style="width:41%"><col style="width:13%"><col style="width:8.5%"><col style="width:16%"></colgroup>` +
    sectionHead(6, "3. ОПИСАНИЕ", "DESCRIPTION") +
    `<tr><th>#</th><th>${label("Чертежный номер/Артикул", "Part number")}</th><th>${label("Описание", "Description")}</th>` +
    `<th>${label("Кол-во", "QTY")}</th><th>${label("Ед. изм.", "E.A.")}</th><th>${label("Номер этикетки", "Batch number")}</th></tr>` +
    itemRows(form.rows, (row) => [row.part, row.name, row.quantity, row.unit, row.batch]) + `</table>` +
    `<table class="section notes">${sectionHead(1, "4. ПРИМЕЧАНИЯ", "NOTES")}` +
    `<tr><td><b>Вес короба может различаться на ________ грамм ввиду разницы расхода упаковочных материалов.</b></td></tr><tr><td>&nbsp;</td></tr></table>` +
    `<table class="section sign"><colgroup><col style="width:30%"><col style="width:70%"></colgroup>` +
    `<tr><td class="lbl">${label("Штамп/подпись исполнителя:", "Storekeeper stamp/sign")}</td><td class="white"></td></tr>` +
    `<tr><td class="lbl">${label("Дата упаковки:", "Packing Date")}</td><td class="white big packed">${escapeHtml(form.packed_at)}</td></tr>` +
    `<tr><td class="lbl">${label("Проверил:", "Checked")}</td><td class="white"></td></tr></table>` +
    `<div class="spacer"></div>` +
    `<table class="bottom"><colgroup><col style="width:17.5%"><col style="width:34%"><col style="width:22%"><col style="width:26.5%"></colgroup>` +
    `<tr><td class="grey lbl mid">${label("Вес (брутто) [кг]", "PACKAGE WEIGHT, kg")}</td><td class="white gross">${escapeHtml(form.gross)}</td>` +
    `<td class="grey lbl">${label("Номер пломбы", "Seal number")}</td><td class="white seal">${escapeHtml(form.seal)}</td></tr>` +
    `<tr><td class="grey lbl mid" rowspan="2">${label("ФИО:", "Name")}</td><td class="white val packer" rowspan="2">${escapeHtml(form.packer)}</td>` +
    `<td class="grey lbl">${label("Штамп/подпись кладовщика:", "Storekeeper stamp/sign")}</td><td class="white"></td></tr>` +
    `<tr><td class="grey lbl">${label("Дата упаковки:", "Shipping Date")}</td><td class="white val shipped">${escapeHtml(form.shipped_at)}</td></tr></table>` +
    foot("Форма/Form 04.2 / Rev.4 / 25.05.2026", sheet) + `</section>`;
}

const tick = (on: boolean) => `<td class="check">${on ? "☒" : "☐"}</td>`;

function deliveryNoteHtml(form: DeliveryNoteForm, sheet: number): string {
  const [prefix, project, dash, index] = form.number;
  // Сетка шапки образца: у трёх строк разные границы клеток — 10 колонок покрывают их все.
  const grid = [24.3, 6.8, 5.3, 8.3, 2.7, 8.6, 10, 1.6, 18.1, 14.3].map((width) => `<col style="width:${width}%">`).join("");
  const wide = (value: string, css = "") => `<td colspan="3"${css ? ` class="${css}"` : ""}>${escapeHtml(value)}</td>`;
  return `<section class="form" aria-label="${escapeHtml(form.title)}">${top("ОТГРУЗОЧНАЯ ВЕДОМОСТЬ", "DELIVERY NOTE")}` +
    `<table class="head"><colgroup>${grid}</colgroup>` +
    `<tr><td class="grey lbl mid">${label("Номер документа", "Ref №")}</td><td class="val strong">${escapeHtml(prefix)}</td>` +
    `<td class="val" colspan="2">${escapeHtml(project)}</td><td class="val">${escapeHtml(dash === "-" ? "−" : dash)}</td>` +
    `<td class="val strong">${escapeHtml(index)}</td><td class="grey lbl mid">${label("Дата", "Date")}</td>` +
    `<td class="strong date" colspan="3">${escapeHtml(form.date)}</td></tr>` +
    `<tr><td class="grey lbl mid">${label("Заказчик", "Customer")}</td><td class="grey lbl mid" colspan="4">${label("Заказ №", "Request #")}</td>` +
    `<td class="grey lbl mid" colspan="3">${label("Номер договора", "Contract number")}</td>` +
    `<td class="grey lbl mid">${label("Номер спецификации", "Specification number")}</td><td class="grey lbl mid">${label("Номер счета", "Invoice number")}</td></tr>` +
    `<tr class="refs"><td class="val customer">${escapeHtml(form.customer)}</td><td class="val strong">PO</td><td class="val strong">#</td>` +
    `<td class="val strong po" colspan="2">${escapeHtml(form.po)}</td><td class="val contract" colspan="3">${escapeHtml(form.contract)}</td>` +
    `<td class="val spec">${escapeHtml(form.spec)}</td><td class="val strong invoice">${escapeHtml(form.invoice)}</td></tr></table>` +
    `<table class="section delivery"><colgroup><col style="width:24%"><col style="width:28%"><col style="width:22%"><col style="width:26%"></colgroup>` +
    sectionHead(4, "1. ДАННЫЕ О ПОСТАВКЕ ИЗДЕЛИЙ", "DELIVERY DATA") +
    `<tr><td class="grey lbl">${label("Условия поставки:", "Delivery terms")}</td><td class="terms">${escapeHtml(form.terms)}</td>` +
    `<td class="grey lbl">${label("Оплачивает:", "Pays for")}</td><td class="pays">${escapeHtml(form.pays)}</td></tr>` +
    `<tr><td class="grey lbl">${label("Адрес заказчика:", "Customer address")}</td>${wide(form.customer_address, "customer-address")}</tr>` +
    `<tr><td class="grey lbl">${label("Адрес доставки изделий:", "Shipping address")}</td>${wide(form.shipping_address, "shipping-address")}</tr>` +
    `<tr><td class="grey lbl">Время работы:</td>${wide(form.hours, "hours")}</tr>` +
    `<tr><td class="grey lbl">${label("Контактное лицо заказчика:", "Customer contact person")}</td><td class="contact">${escapeHtml(form.contact)}</td>` +
    `<td class="grey lbl">${label("Телефон:", "Phone")}</td><td class="phone">${escapeHtml(form.phone)}</td></tr>` +
    `<tr><td class="grey lbl">${label("Наименование перевозчика/ТК:", "Name shipping company")}</td><td class="carrier">${escapeHtml(form.carrier)}</td>` +
    `<td class="grey lbl">${label("Номер ТТН/перевозки:", "Shipment number")}</td><td class="track">${escapeHtml(form.track)}</td></tr>` +
    `<tr><td class="grey lbl">${label("Примечания для перевозчика/ТК:", "Notes")}</td>${wide(form.tk_note, "tk-note")}</tr></table>` +
    `<table class="section items"><colgroup><col style="width:3.6%"><col style="width:15.5%"><col style="width:31.2%"><col style="width:10%"><col style="width:12.1%"><col style="width:15.5%"><col style="width:12.1%"></colgroup>` +
    sectionHead(7, "2. ОПИСАНИЕ", "DESCRIPTION") +
    `<tr><th>#</th><th>${label("Чертеж. номер/Арт.", "Part number")}</th><th>${label("Описание", "Description")}</th><th>${label("Кол-во", "QTY")}</th>` +
    `<th>${label("Ед.изм.", "e.a.")}</th><th>${label("Упаковочная ведомость №", "Packing list #")}</th><th>${label("Номер места", "Box number")}</th></tr>` +
    itemRows(form.rows, (row) => [row.part, row.name, row.quantity, row.unit, row.pl, row.boxes]) + `</table>` +
    `<table class="section notes"><colgroup><col style="width:4%"><col style="width:28%"><col style="width:12%"><col style="width:41%"><col style="width:15%"></colgroup>` +
    sectionHead(5, "3. ПРИМЕЧАНИЯ", "NOTES") +
    `<tr><td class="grey val" colspan="2">УПД передано посредством ЭДО</td>${tick(form.upd_edo)}` +
    `<td class="grey val">УПД в бумажном виде находятся в ${escapeHtml(form.paper_box)}</td>${tick(form.upd_paper)}</tr>` +
    `<tr><td class="val">1.</td><td colspan="4"><b>Упаковочные ведомости (PL) находятся внутри короба. Соотносить Упаковочную Ведомость (PL) ` +
    `с номером места BXN необходимо согласно п.2. Описание.</b></td></tr>` +
    `<tr><td class="val">2.</td><td colspan="4" class="note">${escapeHtml(form.note)}</td></tr></table>` +
    `<div class="spacer"></div>` +
    `<table class="bottom"><colgroup><col style="width:15%"><col style="width:17.5%"><col style="width:16.5%"><col style="width:22%"><col style="width:29%"></colgroup>` +
    `<tr><td class="grey lbl mid">${label("Общее кол-во упаковок [шт]", "Total boxes [e.a.]")}</td><td class="grey lbl mid">${label("Общий вес (брутто) [кг]", "Total weight [kg]")}</td>` +
    `<td class="grey lbl mid">${label("Номер УПД/ТТН", "UPD/TTN number")}</td><td class="grey lbl">${label("Штамп/подпись кладовщика:", "Storekeeper stamp/sign")}</td>` +
    `<td class="white val packer">${escapeHtml(form.packer)}</td></tr>` +
    `<tr><td class="val places">${escapeHtml(form.places)}</td><td class="val gross">${escapeHtml(form.gross)}</td><td class="val upd">${escapeHtml(form.upd)}</td>` +
    `<td class="grey lbl">${label("Дата отгрузки:", "Shipping Date")}</td><td class="white val shipped">${escapeHtml(form.shipped_at)}</td></tr></table>` +
    foot("Форма/Form 04.1 Rev.4 / 24.05.2026", sheet) + `</section>`;
}

/** HTML печати: каждая форма (PL на короб или DN) — отдельный лист A4. */
export function buildShippingFormsHtml(forms: ShippingForm[]): string {
  // Заголовок окна печати = имя файла при «Сохранить как PDF»: номер документа, как в папке проекта.
  const title = forms[0]?.title ?? "";
  const sheets = forms.map((form, index) => form.kind === "pl" ? packingListHtml(form, index + 1) : deliveryNoteHtml(form, index + 1));
  return `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>` +
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
    `<div><span class="caption">Получатель</span><div class="consignee">${escapeHtml(label.consignee)}</div>` +
    `<div class="address">${escapeHtml(label.address)}</div></div>` +
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
