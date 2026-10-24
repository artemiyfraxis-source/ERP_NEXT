import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OPEN_GROUP_LIMIT, ProcurementInWorkTable, linkHost, linkHref, positionsWord, LINK_HINT } from "./ProcurementInWorkTable";
import type { ProcurementLine } from "./api";

function line(id: number, fields: Partial<ProcurementLine> = {}): ProcurementLine {
  return {
    id: `60000000-0000-0000-0000-00000000000${id}`, legacy_id: `SM-${id}`, status: "В работе", version: 2,
    designation: "D-1", planned_date: "2026-10-02", link: null, calculated_name: `Позиция ${id}`,
    calculated_quantity: "2", calculated_unit: "шт", supplier: null, site_name: null, design_article: null,
    actual_quantity: null, actual_unit: null, ...fields,
  } as ProcurementLine;
}

type Props = Parameters<typeof ProcurementInWorkTable>[0];

function props(lines: ProcurementLine[], handlers: Partial<Props> = {}): Props {
  return {
    lines, units: ["шт", "кг"], disabled: false, busyId: "",
    onChanged: vi.fn(), onError: vi.fn(), onCancel: vi.fn(), onCancelMany: vi.fn(async () => undefined),
    onBuildRequests: vi.fn(async () => undefined), renderDetail: () => <span>детали</span>, ...handlers,
  };
}

function renderTable(lines: ProcurementLine[], handlers: Partial<Props> = {}) {
  return render(<ProcurementInWorkTable {...props(lines, handlers)} />);
}

const value = (label: string) => (screen.getByLabelText(label) as HTMLInputElement).value;

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("treats only http(s) and www as a link and labels it by the site", () => {
  expect(linkHref("https://chipdip.ru/p/1")).toBe("https://chipdip.ru/p/1");
  expect(linkHref("www.chipdip.ru")).toBe("https://www.chipdip.ru");
  expect(linkHref("Склад")).toBeNull();
  expect(linkHref("")).toBeNull();
  expect(linkHost("https://www.molex.com/en-us/products/part-detail/430250800")).toBe("molex.com");
});

it("shows a link as a short site label that opens it, and edits the link on a click in the cell", () => {
  renderTable([line(1, { link: "https://www.molex.com/en-us/products/430250800" })]);
  const open = screen.getByLabelText("Открыть ссылку SM-1");
  expect(open).toHaveTextContent("molex.com ↗");
  expect(open).toHaveAttribute("href", "https://www.molex.com/en-us/products/430250800");
  expect(open).toHaveAttribute("target", "_blank");

  fireEvent.click(screen.getByRole("button", { name: "Ссылка SM-1" }));
  const input = screen.getByRole("textbox", { name: "Ссылка SM-1" }) as HTMLInputElement;
  expect(input.value).toBe("https://www.molex.com/en-us/products/430250800");
  fireEvent.change(input, { target: { value: "www.chipdip.ru/product/1" } });
  fireEvent.blur(input);
  expect(screen.getByLabelText("Открыть ссылку SM-1")).toHaveTextContent("chipdip.ru ↗");
  expect(screen.getByText("изменено: 1")).toBeInTheDocument();
});

it("keeps free text in the link cell as amber text with the hint, without a clickable link", () => {
  renderTable([line(1, { link: "Склад" })]);
  const cell = screen.getByRole("button", { name: "Ссылка SM-1" });
  expect(cell).toHaveTextContent("Склад");
  expect(cell).toHaveClass("soft-warn");
  expect(cell.getAttribute("title")).toContain(LINK_HINT);
  expect(screen.queryByLabelText("Открыть ссылку SM-1")).not.toBeInTheDocument();
});

it("saves every edited row in one call and refuses a row without quantity", async () => {
  const sent: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
    if (String(_input).includes("price-history")) return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    sent.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify({ lines: [], saved: 2, ready: 2, skipped: [], failed: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  const onError = vi.fn();
  const onChanged = vi.fn();
  renderTable([line(1), line(2), line(3)], { onError, onChanged });

  fireEvent.click(screen.getByRole("button", { name: "Ссылка SM-1" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Ссылка SM-1" }), { target: { value: "Склад" } });
  fireEvent.change(screen.getByLabelText("Кол-во факт SM-1"), { target: { value: "0" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить всё (1)" }));
  expect(onError).toHaveBeenLastCalledWith(expect.stringContaining("SM-1"));
  expect(screen.getByLabelText("Кол-во факт SM-1")).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByLabelText("Кол-во факт SM-1").closest("td")).toHaveClass("invalid");
  expect(screen.getByLabelText("Поставщик SM-1")).not.toHaveAttribute("aria-invalid");
  expect(sent).toEqual([]);

  fireEvent.change(screen.getByLabelText("Кол-во факт SM-1"), { target: { value: "2" } });

  fireEvent.change(screen.getByLabelText("Поставщик SM-1"), { target: { value: "Фраксис" } });
  fireEvent.change(screen.getByLabelText("Поставщик SM-2"), { target: { value: "ЧипДип" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить всё (2)" }));
  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0]).toEqual({ rows: [
    {
      line_id: line(1).id, legacy_id: "SM-1", version: 2, design_article: null,
      site_name: null, link: "Склад", supplier: "Фраксис", actual_quantity: "2", actual_unit: "шт",
    },
    expect.objectContaining({ line_id: line(2).id, supplier: "ЧипДип", link: null }),
  ] });
  await waitFor(() => expect(onChanged).toHaveBeenCalledWith("Сохранено строк: 2 · готовы к запросу: 2"));
  expect(screen.getByRole("button", { name: "Сохранить всё" })).toBeDisabled();
});

it("counts changed rows in the header, highlights the save button and forgets a change typed back", () => {
  renderTable([line(1), line(2)]);
  const save = () => screen.getByRole("button", { name: /^Сохранить всё/ });
  expect(save()).toBeDisabled();
  expect(save()).toHaveClass("secondary");
  expect(screen.queryByText(/изменено:/)).not.toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Артикул SM-1"), { target: { value: "A-1" } });
  fireEvent.change(screen.getByLabelText("Артикул SM-2"), { target: { value: "A-2" } });
  expect(screen.getByText("изменено: 2")).toBeInTheDocument();
  expect(save()).toBeEnabled();
  expect(save()).toHaveClass("primary");
  expect(screen.getByLabelText("Артикул SM-1").closest("td")).toHaveClass("dirty");

  fireEvent.change(screen.getByLabelText("Артикул SM-2"), { target: { value: "" } });
  expect(screen.getByText("изменено: 1")).toBeInTheDocument();
  expect(screen.getByLabelText("Артикул SM-2").closest("td")).not.toHaveClass("dirty");
});

it("spreads a block pasted from Excel in the column order, skips the read-only price and fills ready rows too", () => {
  renderTable([line(1), line(2, { status: "Готов к запросу", supplier: "Было" }), line(3)]);
  // На экране «Было» (SM-2) стоит первой группой, затем «Без поставщика»: SM-1, SM-3.
  fireEvent.paste(screen.getByLabelText("Поставщик SM-1"), {
    clipboardData: { getData: () => "ЧипДип\tЦАР\t999\t5\tкг\tA-1\t99,50\twww.a.ru\nЭлитан\tЖУК\t999\t7\tшт\tA-2\t10\tСклад\n" },
  });
  expect(value("Поставщик SM-1")).toBe("ЧипДип");
  expect(value("Наименование на сайте SM-1")).toBe("ЦАР");
  expect(value("Кол-во факт SM-1")).toBe("5");
  expect((screen.getByLabelText("Ед. SM-1") as HTMLSelectElement).value).toBe("кг");
  expect(value("Артикул SM-1")).toBe("A-1");
  expect(screen.getByRole("button", { name: "Ссылка SM-1" }).closest("tr")).toHaveTextContent("a.ru ↗");
  expect(value("Поставщик SM-3")).toBe("Элитан");
  expect(value("Артикул SM-3")).toBe("A-2");
  // Строка «Готов к запросу» правится так же, как «В работе».
  expect(value("Поставщик SM-2")).toBe("Было");
  fireEvent.paste(screen.getByLabelText("Поставщик SM-2"), { clipboardData: { getData: () => "Вэлком\tМСК\n" } });
  expect(value("Поставщик SM-2")).toBe("Вэлком");
  expect(value("Наименование на сайте SM-2")).toBe("МСК");
  expect(screen.getByRole("button", { name: "Сохранить всё (3)" })).toBeEnabled();
});

it("keeps typed cells over a fresh copy of the row and fills only the untouched ones", async () => {
  const sent: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
    if (String(_input).includes("price-history")) return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    sent.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify({ lines: [], saved: 1, ready: 0, skipped: [], failed: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  const { rerender } = renderTable([line(1)], { filling: new Set([line(1).id]) });
  expect(screen.getByText("заполняется…")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Наименование на сайте SM-1"), { target: { value: "Моё название" } });

  // Сервер дозаполнил строку в фоне: поставщик и наименование на сайте, версия выросла.
  const filled = line(1, { version: 3, supplier: "ЧипДип", site_name: "С сайта" });
  rerender(<ProcurementInWorkTable {...props([filled])} />);
  expect(screen.queryByText("заполняется…")).not.toBeInTheDocument();
  expect(value("Поставщик SM-1")).toBe("ЧипДип");
  expect(value("Наименование на сайте SM-1")).toBe("Моё название");

  fireEvent.click(screen.getByRole("button", { name: "Сохранить всё (1)" }));
  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0]).toEqual({ rows: [expect.objectContaining({ version: 3, supplier: "ЧипДип", site_name: "Моё название" })] });
});

it("folds the request context into the position cell and marks ready rows", () => {
  renderTable([
    line(1, { task_type: "Закупка", priority: "Срочный", category: "Общепром", item_type: "Комплектующие", calculated_quantity: "7", calculated_unit: "кг", site_name: "Разъём SMA на сайте" }),
    line(2, { status: "Готов к запросу", task_type: "Пополнение", priority: "Обычный", category: "Провод", item_type: "Материалы", supplier: "Кабель.ру" }),
  ]);
  const head = screen.getAllByRole("columnheader").map((cell) => cell.textContent);
  expect(head).toEqual([
    "", "Позиция", "Поставщик", "Наим. на сайте", "Кол-во расч.", "Кол-во факт", "Ед.", "Артикул", "Цена расч.", "Ссылка", "Комментарий", "",
  ]);
  // Обязательные колонки — точкой (класс), а не «*» в тексте; полное название — в подсказке.
  const required = screen.getAllByRole("columnheader").filter((cell) => cell.classList.contains("required")).map((cell) => cell.textContent);
  expect(required).toEqual(["Поставщик", "Кол-во факт", "Ед."]);
  expect(screen.getByRole("columnheader", { name: "Кол-во расч." }).getAttribute("title")).toContain("Кол-во по расчёту");

  const first = screen.getByText("SM-1").closest("td")!;
  expect(first).toHaveTextContent("Позиция 1");
  expect(within(first).getByText("Срочный")).toHaveClass("tone-danger");
  expect(first).toHaveTextContent("Закупка · Общепром · Комплектующие · расчёт 7 кг");
  expect(value("Наименование на сайте SM-1")).toBe("Разъём SMA на сайте");

  const ready = screen.getByText("SM-2").closest("tr")!;
  expect(within(ready).getByText("Готов к запросу")).toHaveClass("tone-ok");
  expect(within(ready).queryByText("Обычный")).not.toBeInTheDocument();
  expect(within(ready).getByLabelText("Поставщик SM-2")).toBeEnabled();
  expect(value("Поставщик SM-2")).toBe("Кабель.ру");
  expect(screen.queryByRole("button", { name: "Заполнить данные" })).not.toBeInTheDocument();
  expect(screen.queryByText("Уточн. наименование")).not.toBeInTheDocument();
});

it("shows the calculated price with VAT, else without, as read-only money", () => {
  renderTable([
    line(1, { calculated_price: "100", calculated_price_with_vat: "120" }),
    line(2, { calculated_price: "50" }),
    line(3),
  ]);
  expect(within(screen.getByText("SM-1").closest("tr")!).getByText("120,00 ₽")).toBeInTheDocument();
  expect(within(screen.getByText("SM-2").closest("tr")!).getByText("50,00 ₽")).toBeInTheDocument();
  expect(screen.getByText("120,00 ₽").closest("td")).not.toHaveClass("edit");
});

it("says 1 позиция, 2 позиции, 5 позиций, 11 позиций, 21 позиция", () => {
  expect([1, 2, 4, 5, 11, 12, 21, 22, 25].map(positionsWord)).toEqual([
    "1 позиция", "2 позиции", "4 позиции", "5 позиций", "11 позиций", "12 позиций", "21 позиция", "22 позиции", "25 позиций",
  ]);
});

const GROUP = "Сформировать запрос";

it("groups rows by saved supplier ignoring case, sums the calculated amount and puts «Без поставщика» last", () => {
  renderTable([
    line(1),
    line(2, { supplier: "ЧипДип", calculated_sum: "100.5" }),
    line(3, { supplier: " чипдип ", calculated_sum: "200" }),
    line(4, { supplier: "Аргус", calculated_sum: "10" }),
  ]);
  const rows = Array.from(document.querySelectorAll("tr.group-row"));
  expect(rows).toHaveLength(3);
  expect(rows[0].querySelector(".group-title")).toHaveTextContent("Аргус");
  expect(rows[0].querySelector(".group-meta")).toHaveTextContent("1 позиция · 10,00 ₽ по расчёту");
  expect(rows[1].querySelector(".group-title")).toHaveTextContent("ЧипДип");
  expect(rows[1].querySelector(".group-meta")).toHaveTextContent("2 позиции · 300,50 ₽ по расчёту");
  expect(rows[2].querySelector(".group-title")).toHaveTextContent("Без поставщика");
  expect(rows[2].querySelector(".group-meta")).toHaveTextContent("1 позиция");
  expect(within(rows[2] as HTMLElement).queryByRole("button", { name: GROUP })).not.toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: GROUP })).toHaveLength(2);
  // Порядок строк — как групп.
  expect(screen.getAllByText(/^SM-\d$/).map((cell) => cell.textContent)).toEqual(["SM-4", "SM-2", "SM-3", "SM-1"]);
});

it("builds the request for the group's ready lines and blocks the button while the group has unsaved edits", async () => {
  const onBuildRequests = vi.fn(async () => undefined);
  const lines = [
    line(1, { supplier: "ЧипДип", status: "Готов к запросу" }),
    line(2, { supplier: "ЧипДип", status: "Готов к запросу" }),
    line(3, { supplier: "Аргус", status: "Готов к запросу" }),
  ];
  renderTable(lines, { onBuildRequests });
  const [argus, chip] = screen.getAllByRole("button", { name: GROUP });
  expect(chip).toBeEnabled();
  fireEvent.click(chip);
  await waitFor(() => expect(onBuildRequests).toHaveBeenCalledWith([lines[0], lines[1]]));

  fireEvent.change(screen.getByLabelText("Артикул SM-1"), { target: { value: "A-1" } });
  expect(chip).toBeDisabled();
  expect(chip.getAttribute("title")).toContain("Сохранить всё");
  expect(argus).toBeEnabled();
});

it("keeps the group button off when the group has no ready lines", () => {
  renderTable([line(1, { supplier: "ЧипДип" })]);
  const button = screen.getByRole("button", { name: GROUP });
  expect(button).toBeDisabled();
  expect(button.getAttribute("title")).toContain("Готов к запросу");
});

it("does not send an empty supplier that was not touched", async () => {
  const sent: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
    if (String(_input).includes("price-history")) return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    sent.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify({ lines: [], saved: 1, ready: 0, skipped: [], failed: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  renderTable([line(1)]);
  fireEvent.change(screen.getByLabelText("Артикул SM-1"), { target: { value: "A-1" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить всё (1)" }));
  await waitFor(() => expect(sent).toHaveLength(1));
  const row = (sent[0].rows as Record<string, unknown>[])[0];
  expect(row).not.toHaveProperty("supplier");
  expect(row).toMatchObject({ design_article: "A-1", actual_quantity: "2", actual_unit: "шт" });
});

it("keeps every row action visible on the row (no «⋯» menu), without approval", () => {
  renderTable([line(1)]);
  expect(screen.queryByText("⋯")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Действия SM-1" })).not.toBeInTheDocument();
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  const actions = within(screen.getByRole("group", { name: "Действия SM-1" }));
  expect(actions.getAllByRole("button").map((item) => item.textContent)).toEqual([
    "Со склада", "Документы…", "Есть на складе, без ГРН", "Отменить строку", "История",
  ]);
  // Полное название — в имени и подсказке кнопки: колонка действий узкая.
  expect(actions.getByRole("button", { name: "Документы, прослеживаемость…" })).toHaveAttribute("title", "Документы, прослеживаемость…");
  fireEvent.click(actions.getByRole("button", { name: "Документы, прослеживаемость…" }));
  expect(screen.getByText("детали")).toBeInTheDocument();
});

it("on a touch screen puts the secondary row actions into an «Ещё» menu: opens, lists them, runs one, closes on Esc", () => {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("coarse"), media: query, addEventListener: () => {}, removeEventListener: () => {} }));
  const onCancel = vi.fn();
  renderTable([line(1)], { onCancel });
  const actions = within(screen.getByRole("group", { name: "Действия SM-1" }));
  expect(actions.getAllByRole("button").map((item) => item.textContent)).toEqual(["Со склада", "Документы…", "Ещё"]);
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  const more = actions.getByRole("button", { name: "Ещё: Действия SM-1" });
  fireEvent.click(more);
  const menu = within(screen.getByRole("menu"));
  expect(menu.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Есть на складе, без ГРН", "Отменить строку", "История"]);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  fireEvent.click(more);
  fireEvent.click(within(screen.getByRole("menu")).getByRole("menuitem", { name: "Отменить строку" }));
  expect(onCancel).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
});

it("selects all, a Shift range, and runs bulk actions on the chosen rows", async () => {
  const onBuildRequests = vi.fn(async () => undefined);
  const onCancelMany = vi.fn(async () => undefined);
  const lines = [line(1), line(2), line(3, { status: "Готов к запросу" }), line(4, { status: "Готов к запросу" }), line(5)];
  renderTable(lines, { onBuildRequests, onCancelMany });
  expect(screen.queryByRole("toolbar", { name: "Действия с выбранными" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByLabelText("Выбрать SM-2"));
  fireEvent.click(screen.getByLabelText("Выбрать SM-4"), { shiftKey: true });
  const bar = screen.getByRole("toolbar", { name: "Действия с выбранными" });
  expect(bar).toHaveTextContent("Выбрано: 3");
  expect(["SM-1", "SM-2", "SM-3", "SM-4", "SM-5"].map((id) => (screen.getByLabelText(`Выбрать ${id}`) as HTMLInputElement).checked))
    .toEqual([false, true, true, true, false]);

  // Запросы — только по строкам «Готов к запросу» среди выбранных.
  fireEvent.click(within(bar).getByRole("button", { name: "Сформировать запросы поставщикам (2)" }));
  await waitFor(() => expect(onBuildRequests).toHaveBeenCalledWith([lines[2], lines[3]]));
  await waitFor(() => expect(screen.queryByRole("toolbar", { name: "Действия с выбранными" })).not.toBeInTheDocument());

  fireEvent.click(screen.getByLabelText("Выбрать все"));
  expect(screen.getByText("Выбрано: 5")).toBeInTheDocument();
  fireEvent.click(within(screen.getByRole("toolbar", { name: "Действия с выбранными" })).getByRole("button", { name: "Отменить строку" }));
  await waitFor(() => expect(onCancelMany).toHaveBeenCalledWith(lines));
});

it("opens «Со склада» under every chosen row at once", () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ line: line(1), query: "", lots: [], covers: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  renderTable([line(1), line(2), line(3)]);
  fireEvent.click(screen.getByLabelText("Выбрать SM-1"));
  fireEvent.click(screen.getByLabelText("Выбрать SM-3"));
  fireEvent.click(within(screen.getByRole("toolbar", { name: "Действия с выбранными" })).getByRole("button", { name: "Со склада" }));
  expect(screen.getAllByLabelText("Поиск на складе")).toHaveLength(2);
});

it("shows the progress and the per-line result of a bulk action under the header", () => {
  const { rerender } = renderTable([line(1)], { bulk: { label: "Отменяем строки", done: 12, total: 40 } });
  expect(screen.getByRole("status")).toHaveTextContent("Отменяем строки: 12 из 40");
  const onCloseBulk = vi.fn();
  rerender(<ProcurementInWorkTable {...props([line(1)], { onCloseBulk, bulk: { label: "Отменяем строки", done: 40, total: 40, summary: "Отменено: 39 из 40", failures: ["SM-7: Строка оплачена"] } })} />);
  expect(screen.getByRole("status")).toHaveTextContent("Отменено: 39 из 40");
  expect(screen.getByText("SM-7: Строка оплачена")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));
  expect(onCloseBulk).toHaveBeenCalled();
});

it("says what to do when nothing is in work", () => {
  renderTable([]);
  expect(screen.getByRole("heading", { name: /В работе/ })).toHaveTextContent("В работе 0");
  expect(screen.getByText(/Нет строк в работе/)).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});

const cover = { id: "c1", line_id: "x", label: "", lot_id: "lot-1", warehouse: "ЦАР", grn: "ГРН-7", package: "П1", storage_location: "A-1", unit: "шт", quantity: "2", consumed: "0", consumed_by: null, active: true, created_at: "", created_by: "Петров", released_at: null };
const lot = (id: string, fields: Record<string, unknown> = {}) => ({
  id, version: 1, warehouse: "ЦАР", grn: `ГРН-${id}`, package: "П1", article: "A-9", series: "", name: "Разъём", item_type: "Комплектующие",
  purpose: "", unit: "шт", storage_location: "A-1", balance: "5", reserved: "0", available: "5", unit_matches: true, holders: [], reserve_note: "", ...fields,
});
const jsonResponse = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

it("reserves a lot from stock with min(available, to_buy) as the default quantity", async () => {
  const posted: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path.includes("/stock-candidates")) {
      return jsonResponse({ line: line(1), query: "A-9", lots: [lot("1"), lot("2", { unit: "м", unit_matches: false })], covers: [] });
    }
    if (path.endsWith("/stock-cover") && init?.method === "POST") {
      posted.push(JSON.parse(String(init.body)));
      return jsonResponse({ line: line(1), cover });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  const onChanged = vi.fn();
  renderTable([line(1)], { onChanged });

  fireEvent.click(within(screen.getByRole("group", { name: "Действия SM-1" })).getByRole("button", { name: "Со склада" }));
  expect(await screen.findByLabelText("Партия ГРН-1")).toBeInTheDocument();
  expect((screen.getByLabelText("Поиск на складе") as HTMLInputElement).value).toBe("A-9");
  expect(screen.getByLabelText("Партия ГРН-2")).toBeDisabled();
  fireEvent.click(screen.getByLabelText("Партия ГРН-1"));
  expect((screen.getByLabelText("Количество со склада") as HTMLInputElement).value).toBe("2");
  fireEvent.click(screen.getByRole("button", { name: "Зарезервировать" }));

  await waitFor(() => expect(posted).toEqual([{ version: 2, lot_id: "1", quantity: "2" }]));
  await waitFor(() => expect(onChanged).toHaveBeenCalledWith("SM-1: со склада 2 шт (ГРН ГРН-7)"));
});

it("shows the server's refusal text when the reserve is rejected", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input).includes("/stock-candidates")) return jsonResponse({ line: line(1), query: "", lots: [lot("1")], covers: [] });
    if (init?.method === "POST") return jsonResponse({ detail: "Больше доступного остатка" }, 422);
    throw new Error("unexpected");
  });
  renderTable([line(1)]);
  fireEvent.click(within(screen.getByRole("group", { name: "Действия SM-1" })).getByRole("button", { name: "Со склада" }));
  fireEvent.click(await screen.findByLabelText("Партия ГРН-1"));
  fireEvent.click(screen.getByRole("button", { name: "Зарезервировать" }));
  expect(await screen.findByText("Больше доступного остатка")).toBeInTheDocument();
});

it("shows the covered quantity on the row and releases it from the menu", async () => {
  const posted: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input).includes("price-history")) return jsonResponse({});
    posted.push(`${init?.method} ${String(input)}`);
    return jsonResponse({ line: line(1), released: [cover] });
  });
  const onChanged = vi.fn();
  renderTable([line(1, { stock_covered: "1", to_buy: "1" } as Partial<ProcurementLine>)], { onChanged });
  expect(screen.getByText(/со склада: 1 шт · к закупке: 1/)).toBeInTheDocument();
  fireEvent.click(within(screen.getByRole("group", { name: "Действия SM-1" })).getByRole("button", { name: "Снять «Со склада»" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledWith("SM-1: «Со склада» снято"));
  expect(posted).toEqual([`POST /api/procurement/${line(1).id}/stock-cover/release`]);
});

it("marks «Есть на складе, без ГРН» with the quantity to buy and an optional warehouse, no lot", async () => {
  const posted: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input).endsWith("/stock-cover") && init?.method === "POST") {
      posted.push(JSON.parse(String(init.body)));
      return jsonResponse({ line: line(1), cover: { ...cover, lot_id: null, grn: "", warehouse: "ЖУК", quantity: "2" } });
    }
    throw new Error(`Unexpected request: ${String(input)}`);
  });
  const onChanged = vi.fn();
  renderTable([line(1)], { onChanged });

  fireEvent.click(within(screen.getByRole("group", { name: "Действия SM-1" })).getByRole("button", { name: "Есть на складе, без ГРН" }));
  const form = screen.getByRole("form", { name: "Есть на складе, без ГРН SM-1" });
  expect(within(form).queryByLabelText("Поиск на складе")).not.toBeInTheDocument();
  expect((screen.getByLabelText("Количество без ГРН") as HTMLInputElement).value).toBe("2");
  expect((screen.getByLabelText("Склад без ГРН") as HTMLSelectElement).value).toBe("");
  fireEvent.change(screen.getByLabelText("Склад без ГРН"), { target: { value: "ЖУК" } });
  fireEvent.click(within(form).getByRole("button", { name: "Отметить со склада" }));

  await waitFor(() => expect(posted).toEqual([{ version: 2, quantity: "2", warehouse: "ЖУК" }]));
  await waitFor(() => expect(onChanged).toHaveBeenCalledWith("SM-1: есть на складе 2 шт, без ГРН"));
  expect(screen.queryByRole("form", { name: "Есть на складе, без ГРН SM-1" })).not.toBeInTheDocument();
});

it("binds a ГРН to a cover made without one: the whole quantity, a lot with less is not offered", async () => {
  const posted: string[] = [];
  const lotless = { ...cover, id: "c9", lot_id: null, grn: "", warehouse: "", quantity: "3" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path.includes("/stock-candidates")) {
      return jsonResponse({ line: line(1), query: "A-9", lots: [lot("1", { available: "2" }), lot("2")], covers: [lotless] });
    }
    if (path.endsWith("/stock-cover/c9/bind") && init?.method === "POST") {
      posted.push(String(init.body));
      return jsonResponse({ line: line(1), cover: { ...lotless, lot_id: "2", grn: "ГРН-2" } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  const onChanged = vi.fn();
  renderTable([line(1, { stock_covered: "3", to_buy: "0" } as Partial<ProcurementLine>)], { onChanged });

  fireEvent.click(within(screen.getByRole("group", { name: "Действия SM-1" })).getByRole("button", { name: "Со склада" }));
  expect(await screen.findByText(/Без ГРН: 3 шт/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Привязать ГРН" }));
  expect(screen.getByRole("button", { name: "Отменить привязку" })).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("Партия ГРН-1"));
  expect(screen.getByRole("button", { name: "Привязать ГРН ГРН-1" })).toBeDisabled();
  fireEvent.click(screen.getByLabelText("Партия ГРН-2"));
  expect(screen.getByLabelText("Количество со склада")).toBeDisabled();
  expect((screen.getByLabelText("Количество со склада") as HTMLInputElement).value).toBe("3");
  fireEvent.click(screen.getByRole("button", { name: "Привязать ГРН ГРН-2" }));

  await waitFor(() => expect(posted).toEqual([JSON.stringify({ version: 2, lot_id: "2" })]));
  await waitFor(() => expect(onChanged).toHaveBeenCalledWith("SM-1: ГРН ГРН-2 привязан, резерв 3 шт"));
});

// 62 строки у 4 поставщиков: у 1-2 по OPEN_GROUP_LIMIT+1 строк (свёрнуты), у 3-4 по 10 (открыты).
function bigQueue(): ProcurementLine[] {
  const rows: ProcurementLine[] = [];
  for (let s = 1; s <= 4; s += 1) {
    const count = s <= 2 ? OPEN_GROUP_LIMIT + 1 : 10;
    for (let k = 0; k < count; k += 1) {
      const n = rows.length + 1;
      rows.push(line(n, { id: `row-${n}`, legacy_id: `SM-${n}`, supplier: `Поставщик ${String(s).padStart(2, "0")}`, status: "Готов к запросу" }));
    }
  }
  return rows;
}

const head = (title: string) => screen.getByRole("button", { name: `Группа ${title}` });

it("starts groups of more than 20 rows folded without inputs and keeps open ones editable", () => {
  window.localStorage.clear();
  renderTable(bigQueue());
  expect(document.querySelectorAll("tr.group-row")).toHaveLength(4);
  expect(head("Поставщик 01")).toHaveAttribute("aria-expanded", "false");
  expect(head("Поставщик 03")).toHaveAttribute("aria-expanded", "true");
  // Свёрнутые: SM-1..SM-42 без полей; открытые: SM-43..SM-62 с полями.
  expect(screen.queryByLabelText("Кол-во факт SM-1")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Кол-во факт SM-42")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Кол-во факт SM-43")).toBeInTheDocument();
  expect(screen.getAllByLabelText(/^Кол-во факт SM-/)).toHaveLength(20);
  // Шапка свёрнутой группы держит число, сумму и «Сформировать запрос».
  const row = head("Поставщик 01").closest("tr")!;
  expect(row).toHaveTextContent("21 позиция");
  expect(within(row).getByRole("button", { name: "Сформировать запрос" })).toBeEnabled();
});

it("remembers an opened or folded group after remount", () => {
  window.localStorage.clear();
  const first = renderTable(bigQueue());
  fireEvent.click(head("Поставщик 01"));
  fireEvent.click(head("Поставщик 03"));
  expect(screen.getByLabelText("Кол-во факт SM-1")).toBeInTheDocument();
  expect(screen.queryByLabelText("Кол-во факт SM-43")).not.toBeInTheDocument();
  first.unmount();

  renderTable(bigQueue());
  expect(head("Поставщик 01")).toHaveAttribute("aria-expanded", "true");
  expect(head("Поставщик 03")).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByLabelText("Кол-во факт SM-1")).toBeInTheDocument();
  expect(screen.queryByLabelText("Кол-во факт SM-43")).not.toBeInTheDocument();
  expect(JSON.parse(window.localStorage.getItem("erp-table:procurement-inwork-groups")!).groups).toEqual({ "поставщик 01": true, "поставщик 03": false });
});

it("still saves an edit made in a group that was folded afterwards", async () => {
  window.localStorage.clear();
  const sent: { rows: Record<string, unknown>[] }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
    if (String(_input).includes("price-history")) return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    sent.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify({ lines: [], saved: 1, ready: 1, skipped: [], failed: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  renderTable(bigQueue());
  fireEvent.change(screen.getByLabelText("Артикул SM-43"), { target: { value: "A-43" } });
  fireEvent.click(head("Поставщик 03"));
  expect(screen.queryByLabelText("Артикул SM-43")).not.toBeInTheDocument();
  expect(head("Поставщик 03").closest("tr")).toHaveTextContent("изменено: 1");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить всё (1)" }));
  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0].rows).toEqual([expect.objectContaining({ line_id: "row-43", design_article: "A-43" })]);
});

it("shows the editable comment cell of «В работе» like the order blocks: a bordered field with «добавить»", () => {
  const editing = { onSaved: vi.fn(), onConflict: vi.fn() };
  renderTable([line(1), line(2, { note: "Ждём ответа" })], { editing });
  const empty = screen.getByRole("button", { name: "Комментарий SM-1" });
  expect(empty).toHaveClass("cell-edit");
  expect(empty).toHaveTextContent("добавить");
  expect(empty.querySelector(".cell-edit-empty")).not.toBeNull();
  const filled = screen.getByRole("button", { name: "Комментарий SM-2" });
  expect(filled).toHaveClass("cell-edit");
  expect(filled).toHaveTextContent("Ждём ответа");
});
