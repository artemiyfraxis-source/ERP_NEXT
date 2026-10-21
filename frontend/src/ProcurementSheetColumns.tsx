import type { DataColumn } from "./DataTable";
import type { ProcurementLine } from "./api";
import { formatDay } from "./format";
import { StatusChip } from "./StatusChip";
import { DatasheetCell, ExpectedDateCell, NoteCell, type LineEditing } from "./ProcurementCellEdit";
import { lineFilesColumn } from "./ProcurementLineDocuments";
import { waitsForGrn } from "./ProcurementStockPicker";
import { projectLabel } from "./internalProjects";

/* Колонки «Весь лист» — все колонки листа закупки в порядке блоков SUPPLYMAP (Config.gs SHEET_ORDER.PURCHASE):
   регистрация → классификация → что закупаем → поставщик → кол-во (расчёт и факт) → заказ → замена →
   документ качества → цены (расчёт, факт) → валюта → счёт и оплата → сроки → комментарий.
   Видны ключевые (на 16" вместе с «Комментарием» без прокрутки вбок), остальные включаются в «Колонках». System ID (колонка «ID») стоит первым, а не последним, как в листе:
   таблица широкая, первая колонка залипает слева и именно по ней строку узнают.
   Даташит правится в своих строках закупщика, как «Комментарий»; «Файлы разрешения» и «Док. качества» — ссылки на
   файлы, приложенные в карточке строки («Документы…»). */

const MINUS = "−";

/** «1 250,50»: сумма с пробелом тысяч и копейками через запятую; пусто — пустая строка, не «0». */
export function formatAmount(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const amount = Number(value);
  if (!Number.isFinite(amount)) return String(value);
  return amount.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 4 }).replace("-", MINUS);
}

/** «1 250,50 ₽»: рубли без кода, иная валюта — её кодом. */
export function formatMoney(value: string | number | null | undefined, currency?: string | null): string {
  const text = formatAmount(value);
  if (!text || !Number.isFinite(Number(value))) return text;
  const code = String(currency ?? "").trim().toUpperCase();
  return `${text} ${!code || code === "RUB" || code === "РУБ" ? "₽" : code}`;
}

/** Количество с запятой: «2,5». */
export function formatQuantity(value: string | number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value).replace(".", ",");
}

function formatPercent(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const rate = Number(value);
  return Number.isFinite(rate) ? `${String(Math.round(rate * 10) / 10).replace(".", ",")} %` : "";
}

/** НДС (Расчет): заявка хранит цену без НДС и с НДС, ставку выводим из них. */
export function calculatedVatRate(line: ProcurementLine): number | null {
  const net = Number(line.calculated_price);
  const gross = Number(line.calculated_price_with_vat);
  if (!line.calculated_price || !line.calculated_price_with_vat || !(net > 0) || !Number.isFinite(gross)) return null;
  return Math.round((gross / net - 1) * 1000) / 10;
}

/** «300 м»: количество с единицей в одной ячейке — так колонка узкая; отдельная «Ед.» остаётся в «Колонках». */
const withUnit = (quantity: string | null | undefined, unit: string | null | undefined) =>
  quantity ? `${formatQuantity(quantity)} ${unit ?? ""}`.trim() : "";

const yesNo = (value: boolean | null) => (value === null ? "" : value ? "Да" : "Нет");
const dayOf = (value: string | null | undefined) => formatDay(value);

type SheetEditing = { editing: LineEditing; buyerName: string; disabled?: boolean; dense?: boolean };

/** Колонки листа. Без `edit` — только чтение (обзор «По поставщикам» берёт из них нужные). */
export function buildSheetColumns(edit?: SheetEditing): DataColumn<ProcurementLine>[] {
  const own = (line: ProcurementLine) => Boolean(edit && edit.buyerName && line.buyer === edit.buyerName);
  const text = (key: string, title: string, value: (line: ProcurementLine) => string | null | undefined, extra: Partial<DataColumn<ProcurementLine>> = {}): DataColumn<ProcurementLine> =>
    ({ key, title, value: (line) => value(line) ?? "", ...extra });
  const day = (key: string, title: string, value: (line: ProcurementLine) => string | null | undefined, extra: Partial<DataColumn<ProcurementLine>> = {}): DataColumn<ProcurementLine> =>
    ({ key, title, value: (line) => value(line) ?? "", render: (line) => dayOf(value(line)), ...extra });
  const money = (key: string, title: string, value: (line: ProcurementLine) => string | null | undefined, currency: (line: ProcurementLine) => string | null | undefined, extra: Partial<DataColumn<ProcurementLine>> = {}): DataColumn<ProcurementLine> =>
    ({ key, title, value: (line) => value(line) ?? "", numeric: true, render: (line) => formatMoney(value(line), currency(line)), ...extra });
  const link = (key: string, title: string, value: (line: ProcurementLine) => string | null | undefined, word: string): DataColumn<ProcurementLine> =>
    ({ key, title, value: (line) => value(line) ?? "", hidden: true, render: (line) => value(line) ? <a href={value(line)!} target="_blank" rel="noreferrer">{word}</a> : "—" });
  const factCurrency = (line: ProcurementLine) => line.currency;

  return [
    // регистрация
    text("legacy_id", "ID", (line) => line.legacy_id, { hint: "System ID" }),
    // «Готово · без ГРН»: закрыто «Есть на складе, без ГРН», партия ещё не привязана (как в «Моих позициях»).
    text("status", "Статус", (line) => line.status, {
      filter: true, hint: "СТАТУС ЗАПРОСА",
      render: (line) => (waitsForGrn(line) ? <><StatusChip status={line.status} /><small className="tone-info">без ГРН</small></> : <StatusChip status={line.status} />),
    }),
    text("revision", "Ревизия", (line) => line.revision, { hidden: true }),
    day("request_date", "Дата запроса", (line) => line.request_date, { hidden: true }),
    text("initiator", "Инициатор", (line) => line.initiator, { hidden: true, filter: true }),
    text("department", "Отдел", (line) => line.department, { hidden: true, filter: true }),
    text("buyer", "Закупщик", (line) => line.buyer, { filter: true, hidden: true }),
    // классификация
    text("designation", "Назначение", (line) => line.designation, { hidden: true }),
    text("project", "Проект", (line) => projectLabel(line.project_reference), { hidden: !edit?.dense, filter: Boolean(edit?.dense) }),
    text("task_type", "Тип задачи", (line) => line.task_type, { filter: true, hidden: true }),
    text("purpose", "Цель", (line) => line.purpose, { filter: true, hidden: true }),
    text("priority", "Приоритет", (line) => line.priority, { filter: true, hidden: true }),
    // что закупаем
    text("category", "Категория", (line) => line.category, { filter: true, hidden: true }),
    text("item_type", "Тип", (line) => line.item_type, { filter: true, hidden: true }),
    text("article", "Артикул КД", (line) => line.design_article, { hidden: true }),
    text("name", "Наименование", (line) => line.calculated_name, { wrap: true, hint: "НАИМЕНОВАНИЕ (Расчет)" }),
    text("site_name", "Наименование (сайт)", (line) => line.site_name, { wrap: true, hidden: true }),
    // поставщик
    text("supplier", "Поставщик", (line) => line.supplier, { filter: true }),
    text("manufacturer", "Изготовитель", (line) => line.manufacturer, { hidden: true }),
    text("country", "Страна", (line) => line.country, { hidden: true }),
    link("link", "Ссылка", (line) => line.link, "открыть"),
    text("datasheet", "Даташит", (line) => line.datasheet, {
      hidden: true, wrap: true,
      render: (line) => <DatasheetCell line={line} editing={edit?.editing} canEdit={own(line)} disabled={edit?.disabled} />,
    }),
    // количество: расчёт и факт рядом
    { key: "calc_qty", title: "Кол-во (расчёт)", value: (line) => line.calculated_quantity, numeric: true, hidden: true, render: (line) => withUnit(line.calculated_quantity, line.calculated_unit) },
    text("calc_unit", "Ед. (расчёт)", (line) => line.calculated_unit, { hidden: true }),
    { key: "fact_qty", title: "Кол-во (факт)", value: (line) => line.actual_quantity ?? "", numeric: true, render: (line) => withUnit(line.actual_quantity, line.actual_unit) },
    text("fact_unit", "Ед. (факт)", (line) => line.actual_unit, { hidden: true }),
    // заказ
    text("order", "№ заказа", (line) => line.order_number),
    day("order_date", "Дата заказа", (line) => line.order_date, { hidden: true }),
    link("order_url", "Заказ", (line) => line.order_url, "открыть"),
    // замена
    text("replacement", "Замена", (line) => yesNo(line.replacement), { hidden: true }),
    text("replacement_decision", "Реш. замены", (line) => line.replacement_decision, { hidden: true }),
    text("permission_number", "№ разрешения", (line) => line.permission_number, { hidden: true }),
    day("permission_date", "Дата разрешения", (line) => line.permission_date, { hidden: true }),
    { key: "permit_files", title: "Файлы разрешения", hidden: true, wrap: true, ...lineFilesColumn("permit") },
    // документ по качеству: нужен ли (из заявки; файл документа качества ставит «Да»), файлы, тип, №, дата
    text("quality_required", "Нужен док. качества", (line) => line.quality_document, { hidden: true, filter: true }),
    { key: "quality_files", title: "Док. качества", hidden: true, wrap: true, ...lineFilesColumn("quality") },
    text("quality_type", "Тип док.", (line) => line.quality_document_type, { hidden: true }),
    text("quality_number", "№ док. качества", (line) => line.quality_document_number, { hidden: true }),
    day("quality_date", "Дата док. качества", (line) => line.quality_document_date, { hidden: true }),
    // цены: расчёт, затем факт
    money("calc_price", "Цена (расчёт)", (line) => line.calculated_price, () => null, { hidden: true }),
    { key: "calc_vat", title: "НДС (расчёт)", value: (line) => calculatedVatRate(line) ?? "", numeric: true, hidden: true, render: (line) => formatPercent(calculatedVatRate(line)) },
    money("calc_price_vat", "Цена с НДС (расчёт)", (line) => line.calculated_price_with_vat, () => null, { hidden: true }),
    money("calc_sum", "Сумма (расчёт)", (line) => line.calculated_sum, () => null, { hidden: true }),
    money("price", "Цена (факт)", (line) => line.actual_price, factCurrency, { hidden: true }),
    { key: "vat", title: "НДС (факт)", value: (line) => line.actual_vat_rate ?? "", numeric: true, hidden: true, render: (line) => formatPercent(line.actual_vat_rate) },
    money("price_vat", "Цена с НДС (факт)", (line) => line.actual_price_with_vat, factCurrency, { hidden: true }),
    money("sum", "Сумма (факт)", (line) => line.actual_sum, factCurrency),
    // валюта
    text("currency", "Валюта", (line) => line.currency, { hidden: true, render: (line) => (line.currency ? (/^(RUB|РУБ)$/i.test(line.currency) ? "₽" : line.currency) : "") }),
    // счёт и оплата
    text("invoice_item", "Наименование (счёт)", (line) => line.invoice_item_name, { wrap: true, hidden: true }),
    text("invoice_number", "№ счёта", (line) => line.invoice_number, { hidden: true }),
    day("invoice_date", "Дата счёта", (line) => line.invoice_date, { hidden: true }),
    link("invoice", "Счёт", (line) => line.invoice_url, "открыть"),
    text("paid", "Оплата", (line) => (line.paid_at ? "Оплачено" : ""), { hidden: true }),
    day("paid_at", "Дата оплаты", (line) => line.paid_at, { hidden: true }),
    // сроки
    text("delivery_point", "Точка отгрузки", (line) => line.delivery_point, { hidden: true }),
    day("planned", "План. дата", (line) => line.planned_date, { hidden: true }),
    day("expected", "Ожид. дата", (line) => line.expected_arrival, {
      render: (line) => <ExpectedDateCell line={line} editing={edit?.editing} canEdit={own(line)} disabled={edit?.disabled} />,
    }),
    day("actual_arrival", "Факт. дата", (line) => line.actual_arrival, { hidden: true }),
    // свободная заметка закупщика
    text("note", "Комментарий", (line) => line.note, {
      wrap: true,
      render: (line) => <NoteCell line={line} editing={edit?.editing} canEdit={own(line)} disabled={edit?.disabled} />,
    }),
  ];
}
