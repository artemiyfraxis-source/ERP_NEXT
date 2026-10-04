import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProcurementInWorkTable, linkHref, LINK_HINT } from "./ProcurementInWorkTable";
import type { ProcurementLine } from "./api";

function line(id: number, fields: Partial<ProcurementLine> = {}): ProcurementLine {
  return {
    id: `60000000-0000-0000-0000-00000000000${id}`, legacy_id: `SM-${id}`, status: "В работе", version: 2,
    designation: "D-1", planned_date: "2026-10-02", link: null, calculated_name: `Позиция ${id}`,
    calculated_quantity: "2", calculated_unit: "шт", supplier: null, site_name: null, design_article: null,
    actual_quantity: null, actual_unit: null, ...fields,
  } as ProcurementLine;
}

function renderTable(lines: ProcurementLine[], handlers: Partial<Parameters<typeof ProcurementInWorkTable>[0]> = {}) {
  return render(
    <ProcurementInWorkTable
      lines={lines} units={["шт", "кг"]} disabled={false} busyId=""
      onChanged={vi.fn()} onError={vi.fn()} onPutOnApproval={vi.fn()} onCancel={vi.fn()}
      renderDetail={() => <span>детали</span>} {...handlers}
    />,
  );
}

afterEach(() => vi.restoreAllMocks());

it("treats only http(s) and www as a link", () => {
  expect(linkHref("https://chipdip.ru/p/1")).toBe("https://chipdip.ru/p/1");
  expect(linkHref("www.chipdip.ru")).toBe("https://www.chipdip.ru");
  expect(linkHref("Склад")).toBeNull();
  expect(linkHref("")).toBeNull();
});

it("accepts free text in the link cell without a browser URL check, shows a hint and no clickable link", () => {
  renderTable([line(1, { link: "Склад" })]);
  const input = screen.getByLabelText("Ссылка SM-1") as HTMLInputElement;
  expect(input.type).toBe("text");
  expect(screen.getByText(LINK_HINT)).toBeInTheDocument();
  expect(screen.queryByLabelText("Открыть ссылку SM-1")).not.toBeInTheDocument();
  fireEvent.change(input, { target: { value: "https://chipdip.ru/p/1" } });
  expect(screen.queryByText(LINK_HINT)).not.toBeInTheDocument();
  expect(screen.getByLabelText("Открыть ссылку SM-1")).toHaveAttribute("href", "https://chipdip.ru/p/1");
});

it("saves every edited row in one call and refuses a row without supplier", async () => {
  const sent: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
    sent.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify({ lines: [], saved: 2, ready: 2, skipped: [], failed: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  const onError = vi.fn();
  const onChanged = vi.fn();
  renderTable([line(1), line(2), line(3)], { onError, onChanged });

  fireEvent.change(screen.getByLabelText("Ссылка SM-1"), { target: { value: "Склад" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить всё (1)" }));
  expect(onError).toHaveBeenLastCalledWith(expect.stringContaining("SM-1"));
  expect(screen.getByLabelText("Поставщик SM-1")).toHaveAttribute("aria-invalid", "true");
  expect(sent).toEqual([]);

  fireEvent.change(screen.getByLabelText("Поставщик SM-1"), { target: { value: "Фраксис" } });
  fireEvent.change(screen.getByLabelText("Поставщик SM-2"), { target: { value: "ЧипДип" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить всё (2)" }));
  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0]).toEqual({ rows: [
    expect.objectContaining({ line_id: line(1).id, supplier: "Фраксис", link: "Склад", version: 2 }),
    expect.objectContaining({ line_id: line(2).id, supplier: "ЧипДип", link: null }),
  ] });
  await waitFor(() => expect(onChanged).toHaveBeenCalledWith("Сохранено строк: 2 · готовы к запросу: 2"));
});

it("spreads a block pasted from Excel over columns and rows", () => {
  renderTable([line(1), line(2)]);
  fireEvent.paste(screen.getByLabelText("Поставщик SM-1"), {
    clipboardData: { getData: () => "ЧипДип\tЦАР\t5\tкг\nЭлитан\tЖУК\t7\tшт\n" },
  });
  expect((screen.getByLabelText("Поставщик SM-1") as HTMLInputElement).value).toBe("ЧипДип");
  expect((screen.getByLabelText("Наименование на сайте SM-1") as HTMLInputElement).value).toBe("ЦАР");
  expect((screen.getByLabelText("Факт. кол-во SM-1") as HTMLInputElement).value).toBe("5");
  expect((screen.getByLabelText("Ед. SM-1") as HTMLSelectElement).value).toBe("кг");
  expect((screen.getByLabelText("Поставщик SM-2") as HTMLInputElement).value).toBe("Элитан");
  expect(screen.getByRole("button", { name: "Сохранить всё (2)" })).toBeEnabled();
});

it("keeps rare actions in the row menu", () => {
  renderTable([line(1)]);
  expect(screen.queryByRole("menuitem")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Действия SM-1" }));
  expect(screen.getByRole("menuitem", { name: "Со склада" })).toBeEnabled();
  expect(screen.queryByRole("menuitem", { name: /Снять/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: /Замена/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("menuitem", { name: "Прослеживаемость, раздробить…" }));
  expect(screen.getByText("детали")).toBeInTheDocument();
});

it("closes the row menu on an outside click and opens it again from the button", () => {
  renderTable([line(1)]);
  fireEvent.click(screen.getByRole("button", { name: "Действия SM-1" }));
  expect(screen.getByRole("menu")).toBeInTheDocument();
  fireEvent.mouseDown(document.body);
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Действия SM-1" }));
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
});

it("names the site field «Наименование на сайте» and shows the request context read-only", () => {
  renderTable([line(1, {
    task_type: "Закупка", priority: "Срочный", category: "Общепром", item_type: "Комплектующие",
    calculated_quantity: "7", calculated_unit: "кг", site_name: "Разъём SMA на сайте",
  })]);
  const head = screen.getAllByRole("columnheader").map((cell) => cell.textContent);
  expect(head).toEqual([
    "Позиция", "Тип задачи", "Приоритет", "Категория", "Тип", "Кол-во (расчёт)", "Ед. (расчёт)",
    "Поставщик *", "Наименование на сайте", "Факт. кол-во *", "Ед. *", "Артикул", "Уточнённое наименование", "Ссылка", "",
  ]);
  expect(head).not.toContain("Площадка");
  const row = screen.getByText("Закупка").closest("tr")!;
  expect(Array.from(row.querySelectorAll("td")).slice(1, 7).map((cell) => cell.textContent)).toEqual(["Закупка", "Срочный", "Общепром", "Комплектующие", "7", "кг"]);
  expect(row.querySelectorAll("input[aria-label]")).toHaveLength(6);
  expect((screen.getByLabelText("Наименование на сайте SM-1") as HTMLInputElement).value).toBe("Разъём SMA на сайте");
});

it("does not offer the retired substitution or quality-document fields", () => {
  renderTable([line(1)], { renderDetail: () => <span>детали</span> });
  fireEvent.click(screen.getByRole("button", { name: "Действия SM-1" }));
  expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
    "Со склада", "Прослеживаемость, раздробить…", "На согласование", "Отменить строку",
  ]);
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

  fireEvent.click(screen.getByRole("button", { name: "Действия SM-1" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Со склада" }));
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
  fireEvent.click(screen.getByRole("button", { name: "Действия SM-1" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Со склада" }));
  fireEvent.click(await screen.findByLabelText("Партия ГРН-1"));
  fireEvent.click(screen.getByRole("button", { name: "Зарезервировать" }));
  expect(await screen.findByText("Больше доступного остатка")).toBeInTheDocument();
});

it("shows the covered quantity on the row and releases it from the menu", async () => {
  const posted: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    posted.push(`${init?.method} ${String(input)}`);
    return jsonResponse({ line: line(1), released: [cover] });
  });
  const onChanged = vi.fn();
  renderTable([line(1, { stock_covered: "1", to_buy: "1" } as Partial<ProcurementLine>)], { onChanged });
  expect(screen.getByText(/со склада: 1 шт · к закупке: 1/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Действия SM-1" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Снять «Со склада»" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledWith("SM-1: «Со склада» снято"));
  expect(posted).toEqual([`POST /api/procurement/${line(1).id}/stock-cover/release`]);
});
