import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProcurementBySupplier, ProcurementSheetView } from "./ProcurementSheet";
import type { ProcurementLine } from "./api";

const base = {
  revision: "A", version: 1, request_date: "2026-09-16", initiator: "Иванов", department: "КБ",
  designation: "D-1", project_reference: null, priority: "Срочно", delivery_point: "Москва",
  planned_date: "2026-09-30", link: null, item_type: "Комплектующие", design_article: null,
  category: "Общепром", calculated_quantity: "2", calculated_unit: "шт", site_name: null,
  actual_quantity: null, actual_unit: null, quality_document: "Нет", supplier_request_reference: null,
  order_date: null, expected_arrival: null, invoice_url: null, paid_at: null, currency: null,
  actual_price: null, actual_vat_rate: null, actual_price_with_vat: null, actual_sum: null,
  source_currency: null, source_price: null, source_price_with_vat: null, source_sum: null,
  exchange_rate: null, exchange_rate_date: null, exchange_rate_manual: false, invoice_item_name: null,
  invoice_number: null, invoice_date: null, pay_missing: [], is_backorder: false, replacement: null,
  replacement_decision: null, replacement_open: false, permission_number: null, permission_date: null,
} satisfies Partial<ProcurementLine>;

function line(id: number, fields: Partial<ProcurementLine>): ProcurementLine {
  return {
    ...base, id: `40000000-0000-0000-0000-00000000000${id}`, legacy_id: `SM-${id}`, status: "Новый",
    calculated_name: `Позиция ${id}`, buyer: null, supplier: null, order_number: null, ...fields,
  } as ProcurementLine;
}

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

it("PR-11: groups open lines by supplier with stage chips and drills into one supplier", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/by-supplier") {
      return json({
        stages: ["Новый", "В работе", "Готов к запросу", "Жду счет", "В оплату"],
        lines: [
          line(1, { supplier: "ЧипДип", status: "В работе", buyer: "Петров" }),
          line(2, { supplier: "ЧипДип", status: "Жду счет", buyer: "Сидоров", order_number: "PO-001" }),
          line(3, { status: "Новый" }),
          line(4, { supplier: "ЧипДип", status: "Согласование" }),
        ],
      });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementBySupplier />);
  const tile = await screen.findByRole("button", { name: /ЧипДип/ });
  expect(tile).toHaveTextContent("В работе · 1");
  expect(tile).toHaveTextContent("заказов: 1");
  expect(tile).toHaveTextContent("поз.: 3");
  expect(tile).not.toHaveTextContent("Согласование");
  expect(screen.getByRole("button", { name: /Без поставщика/ })).toHaveTextContent("заказ не сформирован");

  fireEvent.click(tile);
  expect(await screen.findByRole("cell", { name: "Сидоров" })).toBeInTheDocument();
  expect(screen.getByText("Позиция 1")).toBeInTheDocument();
  expect(screen.queryByText("Позиция 3")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "← Все поставщики" }));
  fireEvent.change(screen.getByLabelText("Поиск по поставщикам"), { target: { value: "po-001" } });
  expect(screen.getByRole("button", { name: /ЧипДип/ })).toHaveTextContent("поз.: 1");
  expect(screen.queryByRole("button", { name: /Без поставщика/ })).not.toBeInTheDocument();
});

it("loads the whole sheet with server filters and pages by cursor", async () => {
  const requested: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    requested.push(path);
    if (path === "/api/procurement/meta") return json({ units: [], statuses: ["Новый", "В пути", "Готово"] });
    if (path === "/api/procurement/lines") return json({ lines: [line(1, {})], next_cursor: "c1", buyers: ["Петров"] });
    if (path === "/api/procurement/lines?after=c1") return json({ lines: [line(2, { status: "Готово" })], next_cursor: null, buyers: ["Петров"] });
    if (path === "/api/procurement/lines?q=%D1%80%D0%B0%D0%B7%D1%8A%D1%91%D0%BC&status=%D0%93%D0%BE%D1%82%D0%BE%D0%B2%D0%BE&buyer=%D0%9F%D0%B5%D1%82%D1%80%D0%BE%D0%B2") {
      return json({ lines: [line(2, { status: "Готово" })], next_cursor: null, buyers: ["Петров"] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementSheetView />);
  expect(await screen.findByText("Позиция 1")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Показать ещё" }));
  expect(await screen.findByText("Позиция 2")).toBeInTheDocument();

  const status = screen.getByLabelText("Статус") as HTMLSelectElement;
  await waitFor(() => expect(Array.from(status.options).map((option) => option.value)).toContain("Готово"));
  fireEvent.change(status, { target: { value: "Готово" } });
  await waitFor(() => expect((screen.getByLabelText("Закупщик") as HTMLSelectElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText("Закупщик"), { target: { value: "Петров" } });
  await waitFor(() => expect((screen.getByLabelText("Статус") as HTMLSelectElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText("Поиск"), { target: { value: "разъём" } });
  fireEvent.click(screen.getByRole("button", { name: "Найти" }));
  await waitFor(() => expect(screen.queryByText("Позиция 1")).not.toBeInTheDocument());
  expect(requested.some((path) => path.includes("status="))).toBe(true);
});

// ── Колонки листа: порядок блоков SUPPLYMAP, выбор колонок, формат ─────────────────────────────────

const ALL_COLUMNS = [
  // регистрация
  "ID", "Статус", "Ревизия", "Дата запроса", "Инициатор", "Отдел", "Закупщик",
  // классификация
  "Назначение", "Проект", "Тип задачи", "Цель", "Приоритет",
  // что закупаем
  "Категория", "Тип", "Артикул КД", "Наименование", "Наименование (сайт)",
  // поставщик
  "Поставщик", "Изготовитель", "Страна", "Ссылка", "Даташит",
  // количество: расчёт и факт рядом
  "Кол-во (расчёт)", "Ед. (расчёт)", "Кол-во (факт)", "Ед. (факт)",
  // заказ, замена, документ качества
  "№ заказа", "Дата заказа", "Заказ",
  "Замена", "Реш. замены", "№ разрешения", "Дата разрешения", "Файлы разрешения",
  "Нужен док. качества", "Док. качества", "Тип док.", "№ док. качества", "Дата док. качества",
  // цены: расчёт, затем факт; валюта
  "Цена (расчёт)", "НДС (расчёт)", "Цена с НДС (расчёт)", "Сумма (расчёт)",
  "Цена (факт)", "НДС (факт)", "Цена с НДС (факт)", "Сумма (факт)",
  "Валюта",
  // счёт и оплата
  "Наименование (счёт)", "№ счёта", "Дата счёта", "Счёт", "Оплата", "Дата оплаты",
  // сроки и комментарий
  "Точка отгрузки", "План. дата", "Ожид. дата", "Факт. дата", "Комментарий",
];

// Ключевые колонки: с «Комментарием» они умещаются в 16" без прокрутки вбок; закупщик отбирается в шапке, единица — в «Колонках».
const DEFAULT_COLUMNS = [
  "ID", "Статус", "Наименование", "Поставщик", "Кол-во (факт)", "№ заказа", "Сумма (факт)", "Ожид. дата", "Комментарий",
];

const headers = () => screen.getAllByRole("columnheader").map((cell) => cell.textContent?.replace(/\s*[▲▼]$/, ""));
const plain = (text: string | null) => (text ?? "").replace(/\s/g, " ");

function sheetMock(lines: ProcurementLine[], onPatch?: (path: string, body: Record<string, unknown>) => Response | Promise<Response>) {
  const requested: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    requested.push(`${init?.method ?? "GET"} ${path}`);
    if (path === "/api/procurement/meta") return json({ units: [], statuses: ["Новый", "В пути"] });
    if (path.startsWith("/api/procurement/lines")) return json({ lines, next_cursor: null, buyers: ["Петров"] });
    if (init?.method === "PATCH" && onPatch) return onPatch(path, JSON.parse(String(init.body)));
    throw new Error(`Unexpected request: ${path}`);
  });
  return requested;
}

function refused(status: number, detail: string) {
  return new Response(JSON.stringify({ detail }), { status, headers: { "Content-Type": "application/json" } });
}

it("shows the key columns of the whole sheet and keeps the rest in «Колонки» in SUPPLYMAP block order", async () => {
  sheetMock([line(1, { buyer: "Петров" })]);
  window.localStorage.clear();
  render(<ProcurementSheetView />);
  await screen.findByText("Позиция 1");
  expect(headers()).toEqual(DEFAULT_COLUMNS);

  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  const chooser = screen.getByRole("group", { name: "Показывать колонки" });
  const boxes = within(chooser).getAllByRole("checkbox");
  // В выборе — все колонки листа закупки, в порядке блоков SUPPLYMAP.
  expect(boxes.map((box) => box.closest("label")?.textContent)).toEqual(ALL_COLUMNS);
  for (const box of boxes) if (!(box as HTMLInputElement).checked) fireEvent.click(box);
  expect(headers()).toEqual(ALL_COLUMNS);

  // Выключенная колонка уходит из таблицы, остальные не двигаются.
  fireEvent.click(within(chooser).getByRole("checkbox", { name: "Ревизия" }));
  expect(headers()).toEqual(ALL_COLUMNS.filter((title) => title !== "Ревизия"));
});

it("remembers the chosen columns of the sheet apart from the old layout", async () => {
  sheetMock([line(1, { buyer: "Петров" })]);
  window.localStorage.clear();
  window.localStorage.setItem("erp-table:procurement-sheet", JSON.stringify({ hidden: ["task_type"] }));
  const { unmount } = render(<ProcurementSheetView />);
  await screen.findByText("Позиция 1");
  // Старый сохранённый набор колонок не раскрывает все пятьдесят с лишним колонок нового листа.
  expect(headers()).toEqual(DEFAULT_COLUMNS);
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Цена (факт)" }));
  unmount();

  render(<ProcurementSheetView />);
  await screen.findByText("Позиция 1");
  expect(headers()).toContain("Цена (факт)");
});

it("shows dates as 04.10.2026 and money right-aligned with ₽ or the currency code", async () => {
  sheetMock([line(1, {
    status: "Карантин", buyer: "Петров", request_date: "2026-09-16", expected_arrival: "2026-10-04", actual_arrival: "2026-10-03",
    actual_quantity: "2.5", actual_unit: "кг", currency: "RUB", actual_price: "1250.5", actual_price_with_vat: "1500.6", actual_vat_rate: "20", actual_sum: "3751.5",
    calculated_price: "100", calculated_price_with_vat: "122", calculated_sum: "244", paid_at: "2026-10-01T12:00:00+03:00", note: "Позвонить",
  }), line(2, { buyer: "Петров", currency: "USD", actual_sum: "99.9" })]);
  window.localStorage.clear();
  render(<ProcurementSheetView />);
  await screen.findByText("Позиция 1");
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  const chooser = screen.getByRole("group", { name: "Показывать колонки" });
  for (const title of ["Дата запроса", "Факт. дата", "Цена (факт)", "НДС (факт)", "Цена с НДС (факт)", "НДС (расчёт)", "Сумма (расчёт)", "Оплата", "Дата оплаты", "Валюта"]) {
    fireEvent.click(within(chooser).getByRole("checkbox", { name: title }));
  }
  const cellOf = (row: string, title: string) => {
    const index = headers().indexOf(title);
    return within(screen.getByText(row).closest("tr") as HTMLElement).getAllByRole("cell")[index];
  };
  expect(cellOf("Позиция 1", "Дата запроса")).toHaveTextContent("16.09.2026");
  expect(cellOf("Позиция 1", "Ожид. дата")).toHaveTextContent("04.10.2026");
  expect(cellOf("Позиция 1", "Факт. дата")).toHaveTextContent("03.10.2026");
  expect(cellOf("Позиция 1", "Дата оплаты")).toHaveTextContent("01.10.2026");
  expect(cellOf("Позиция 1", "Оплата")).toHaveTextContent("Оплачено");
  expect(plain(cellOf("Позиция 1", "Цена (факт)").textContent)).toBe("1 250,50 ₽");
  expect(plain(cellOf("Позиция 1", "Сумма (факт)").textContent)).toBe("3 751,50 ₽");
  expect(plain(cellOf("Позиция 1", "Сумма (расчёт)").textContent)).toBe("244,00 ₽");
  expect(cellOf("Позиция 1", "НДС (факт)")).toHaveTextContent("20 %");
  expect(cellOf("Позиция 1", "НДС (расчёт)")).toHaveTextContent("22 %");
  expect(plain(cellOf("Позиция 1", "Кол-во (факт)").textContent)).toBe("2,5 кг");
  expect(cellOf("Позиция 1", "Валюта")).toHaveTextContent("₽");
  expect(plain(cellOf("Позиция 2", "Сумма (факт)").textContent)).toBe("99,90 USD");
  for (const title of ["Цена (факт)", "Сумма (факт)", "Кол-во (факт)"]) expect(cellOf("Позиция 1", title)).toHaveClass("num");
});

// ── Правка «Комментария» и «Ожид. даты» в ячейке листа ──────────────────────────────────────────────

const own = (id: number, fields: Partial<ProcurementLine> = {}) => line(id, { status: "В пути", buyer: "Петров", expected_arrival: "2026-10-12", note: "Позвонить", version: 3, ...fields });

it("edits the note and the expected date of the buyer's own line right in the cell and shows the saved line", async () => {
  const sent: Array<{ path: string; body: Record<string, unknown> }> = [];
  const mine = own(1);
  sheetMock([mine, own(2, { buyer: "Сидоров", note: "Чужой" }), line(3, { buyer: "Петров" })], (path, body) => {
    sent.push({ path, body });
    return json({ line: path.endsWith("/note") ? { ...mine, note: body.note, version: 4 } : { ...mine, note: "Позвонить завтра", expected_arrival: body.expected_arrival, version: 5 } });
  });
  window.localStorage.clear();
  render(<ProcurementSheetView buyerName="Петров" />);
  await screen.findByText("Позиция 1");

  // Чужая строка и строка «Новый» — просто текст, без правки.
  expect(screen.queryByRole("button", { name: "Комментарий SM-2" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Ожид. дата SM-2" })).not.toBeInTheDocument();
  expect(screen.getByText("Чужой")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Комментарий SM-3" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Ожид. дата SM-1" })).toHaveTextContent("12.10.2026");

  fireEvent.click(screen.getByRole("button", { name: "Комментарий SM-1" }));
  const note = screen.getByRole("textbox", { name: "Комментарий SM-1" });
  expect(note).toHaveValue("Позвонить");
  fireEvent.change(note, { target: { value: "Позвонить завтра" } });
  fireEvent.keyDown(note, { key: "Enter" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Комментарий SM-1" })).toHaveTextContent("Позвонить завтра"));
  expect(sent).toEqual([{ path: `/api/procurement/${mine.id}/note`, body: { version: 3, note: "Позвонить завтра" } }]);

  // Дата уходит с версией, которую вернуло сохранение комментария, — не со старой.
  fireEvent.click(screen.getByRole("button", { name: "Ожид. дата SM-1" }));
  const date = screen.getByLabelText("Ожид. дата SM-1");
  fireEvent.change(date, { target: { value: "2026-10-20" } });
  fireEvent.blur(date);
  await waitFor(() => expect(screen.getByRole("button", { name: "Ожид. дата SM-1" })).toHaveTextContent("20.10.2026"));
  expect(sent[1]).toEqual({ path: `/api/procurement/${mine.id}/expected-arrival`, body: { version: 4, expected_arrival: "2026-10-20" } });
});

it("saves on leaving the field, cancels on Esc and sends nothing when the text did not change", async () => {
  const sent: string[] = [];
  const mine = own(1);
  sheetMock([mine], (path, body) => {
    sent.push(`${path} ${JSON.stringify(body)}`);
    return json({ line: { ...mine, note: body.note, version: 4 } });
  });
  window.localStorage.clear();
  render(<ProcurementSheetView buyerName="Петров" />);
  await screen.findByText("Позиция 1");

  fireEvent.click(screen.getByRole("button", { name: "Комментарий SM-1" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Комментарий SM-1" }), { target: { value: "не сохранять" } });
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Комментарий SM-1" }), { key: "Escape" });
  expect(screen.getByRole("button", { name: "Комментарий SM-1" })).toHaveTextContent("Позвонить");

  fireEvent.click(screen.getByRole("button", { name: "Комментарий SM-1" }));
  fireEvent.blur(screen.getByRole("textbox", { name: "Комментарий SM-1" }));
  expect(sent).toEqual([]);

  fireEvent.click(screen.getByRole("button", { name: "Комментарий SM-1" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Комментарий SM-1" }), { target: { value: "  " } });
  fireEvent.blur(screen.getByRole("textbox", { name: "Комментарий SM-1" }));
  await waitFor(() => expect(sent).toEqual([`/api/procurement/${mine.id}/note {"version":3,"note":""}`]));
});

it("says the line was changed on 409, rereads just that line and drops the stale draft", async () => {
  const mine = own(1);
  const fresh = { ...mine, note: "Уже поправили в приёмке", version: 9 };
  const requested: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    requested.push(`${init?.method ?? "GET"} ${path}`);
    if (path === "/api/procurement/meta") return json({ units: [], statuses: [] });
    // Перечитывание одной строки — поиск по её System ID; вернёт свежую версию.
    if (path === "/api/procurement/lines?q=SM-1") return json({ lines: [fresh], next_cursor: null, buyers: ["Петров"] });
    if (path === "/api/procurement/lines") return json({ lines: [mine], next_cursor: null, buyers: ["Петров"] });
    if (init?.method === "PATCH" && JSON.parse(String(init.body)).version === 3) return refused(409, "Конкурентное изменение: обновите данные и повторите команду");
    if (init?.method === "PATCH") return json({ line: { ...fresh, note: "ок", version: 10 } });
    throw new Error(`Unexpected request: ${path}`);
  });
  window.localStorage.clear();
  render(<ProcurementSheetView buyerName="Петров" />);
  await screen.findByText("Позиция 1");

  fireEvent.click(screen.getByRole("button", { name: "Комментарий SM-1" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Комментарий SM-1" }), { target: { value: "мой черновик" } });
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Комментарий SM-1" }), { key: "Enter" });

  expect(await screen.findByText("Строку изменили — обновите")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Комментарий SM-1" })).toHaveTextContent("Уже поправили в приёмке"));
  expect(requested).toContain("GET /api/procurement/lines?q=SM-1");
  expect(screen.queryByText("мой черновик")).not.toBeInTheDocument();

  // Следующая правка уходит уже с версией свежей строки, а старое сообщение снято.
  fireEvent.click(screen.getByRole("button", { name: "Комментарий SM-1" }));
  expect(screen.queryByText("Строку изменили — обновите")).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Комментарий SM-1" }), { target: { value: "ок" } });
  fireEvent.blur(screen.getByRole("textbox", { name: "Комментарий SM-1" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Комментарий SM-1" })).toHaveTextContent("ок"));
  const patches = vi.mocked(globalThis.fetch).mock.calls.filter(([, init]) => init?.method === "PATCH").map(([, init]) => JSON.parse(String(init?.body)));
  expect(patches).toEqual([{ version: 3, note: "мой черновик" }, { version: 9, note: "ок" }]);
});

it("shows the server text on 422 and keeps the field open with what was typed", async () => {
  const mine = own(1, { status: "Жду счет", expected_arrival: "2026-10-12" });
  sheetMock([mine], () => refused(422, "Ожидаемая дата поступления не может быть раньше заказа"));
  window.localStorage.clear();
  render(<ProcurementSheetView buyerName="Петров" />);
  await screen.findByText("Позиция 1");

  fireEvent.click(screen.getByRole("button", { name: "Ожид. дата SM-1" }));
  const date = screen.getByLabelText("Ожид. дата SM-1");
  fireEvent.change(date, { target: { value: "2020-01-01" } });
  fireEvent.keyDown(date, { key: "Enter" });
  expect(await screen.findByText("Ожидаемая дата поступления не может быть раньше заказа")).toBeInTheDocument();
  expect(screen.getByLabelText("Ожид. дата SM-1")).toHaveValue("2020-01-01");
});

// ── Даташит, файлы разрешения и документа качества, «без ГРН» ─────────────────────────────────────

const file = (id: string, kind: "permit" | "quality", file_name: string) => ({
  id, kind, file_name, url: `/api/procurement/files/${id}`, uploaded_by: "Петров", uploaded_at: "2026-10-09T10:00:00+03:00",
});

function showColumns(...titles: string[]) {
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  const chooser = screen.getByRole("group", { name: "Показывать колонки" });
  for (const title of titles) fireEvent.click(within(chooser).getByRole("checkbox", { name: title }));
}

it("edits Даташит of the buyer's own line, links the permit and quality files and keeps them after the save", async () => {
  const sent: Array<{ path: string; body: Record<string, unknown> }> = [];
  const mine = own(1, {
    datasheet: "https://www.example.com/ds.pdf",
    permit_files: [file("f1", "permit", "Разрешение 12.pdf")],
    quality_files: [file("f2", "quality", "Паспорт.pdf"), file("f3", "quality", "Сертификат.pdf")],
    quality_document: "Да",
  });
  sheetMock([mine, own(2, { buyer: "Сидоров", datasheet: "по запросу" })], (path, body) => {
    sent.push({ path, body });
    // Ответ правки не несёт файлов — лист их не теряет.
    return json({ line: { ...mine, permit_files: undefined, quality_files: undefined, datasheet: body.datasheet, version: 4 } });
  });
  window.localStorage.clear();
  render(<ProcurementSheetView buyerName="Петров" />);
  await screen.findByText("Позиция 1");
  showColumns("Даташит", "Файлы разрешения", "Нужен док. качества", "Док. качества");

  expect(screen.getByRole("button", { name: "Даташит SM-1" })).toHaveTextContent("example.com");
  expect(screen.getByRole("link", { name: "открыть" })).toHaveAttribute("href", "https://www.example.com/ds.pdf");
  expect(screen.queryByRole("button", { name: "Даташит SM-2" })).not.toBeInTheDocument();
  expect(screen.getByText("по запросу")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Разрешение 12.pdf" })).toHaveAttribute("href", "/api/procurement/files/f1");
  expect(screen.getByRole("link", { name: "Сертификат.pdf" })).toHaveAttribute("href", "/api/procurement/files/f3");

  fireEvent.click(screen.getByRole("button", { name: "Даташит SM-1" }));
  const field = screen.getByRole("textbox", { name: "Даташит SM-1" });
  fireEvent.change(field, { target: { value: "Описание у поставщика" } });
  fireEvent.keyDown(field, { key: "Enter" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Даташит SM-1" })).toHaveTextContent("Описание у поставщика"));
  expect(sent).toEqual([{ path: `/api/procurement/${mine.id}/datasheet`, body: { version: 3, datasheet: "Описание у поставщика" } }]);
  expect(screen.getByRole("link", { name: "Паспорт.pdf" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Разрешение 12.pdf" })).toBeInTheDocument();
});

it("marks a line closed «Есть на складе, без ГРН» in the status column of the sheet", async () => {
  sheetMock([
    line(1, { status: "Готово", buyer: "Петров", stock_without_grn: "2" } as Partial<ProcurementLine>),
    line(2, { status: "Готово", buyer: "Петров", stock_without_grn: "0" } as Partial<ProcurementLine>),
  ]);
  window.localStorage.clear();
  render(<ProcurementSheetView />);
  await screen.findByText("Позиция 1");
  const statusOf = (name: string) => screen.getByText(name).closest("tr")!.querySelectorAll("td")[1].textContent;
  expect(statusOf("Позиция 1")).toBe("Готовобез ГРН");
  expect(statusOf("Позиция 2")).toBe("Готово");
});

it("offers no in-cell edit without a buyer name", async () => {
  sheetMock([own(1)]);
  window.localStorage.clear();
  render(<ProcurementSheetView />);
  await screen.findByText("Позиция 1");
  expect(screen.getByText("Позвонить")).toBeInTheDocument();
  expect(screen.getByText("12.10.2026")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Комментарий SM-1" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Ожид. дата SM-1" })).not.toBeInTheDocument();
});
