import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ItemTypesPanel, StockImportPanel } from "./WarehouseReferences";

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

const dictionary = { types: ["Кабель", "Лента"], counts: { "ТуТ": 3, "Кабель": 1 }, choices: ["Кабель", "Лента", "Термоусадочная трубка"] };

it("saves the dictionary as lines and renames a stock type in every row", async () => {
  const sent: Array<[string, unknown]> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.body) sent.push([path, JSON.parse(String(init.body))]);
    if (path === "/api/stock/item-types" && init?.method === "PUT") {
      return json({ ...dictionary, types: ["Лента", "Кабель"], message: "Справочник типов сохранён: 2." });
    }
    if (path === "/api/stock/item-types") return json(dictionary);
    if (path === "/api/stock/item-types/rename") return json({ message: "Тип «ТуТ» → «Термоусадочная трубка»: строк 3." });
    throw new Error(`Unexpected request: ${path}`);
  });
  const onChanged = vi.fn();

  render(<ItemTypesPanel onClose={() => undefined} onChanged={onChanged} />);
  const list = await screen.findByLabelText("Список типов");
  await waitFor(() => expect(list).toHaveValue("Кабель\nЛента"));
  expect(screen.getByText("нет в справочнике")).toBeInTheDocument(); // «ТуТ»

  fireEvent.change(list, { target: { value: "Лента\nКабель\n" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить список" }));
  expect(await screen.findByText("Справочник типов сохранён: 2.")).toBeInTheDocument();
  expect(sent[0]).toEqual(["/api/stock/item-types", { types: ["Лента", "Кабель", ""] }]);
  expect(onChanged).toHaveBeenLastCalledWith(expect.objectContaining({ types: ["Лента", "Кабель"] }), false);

  fireEvent.click(screen.getAllByRole("button", { name: "Переименовать" })[1]); // «ТуТ» sorts after «Кабель»
  fireEvent.change(screen.getByLabelText("Новый тип для «ТуТ»"), { target: { value: "Термоусадочная трубка" } });
  fireEvent.click(screen.getByRole("button", { name: "Переименовать 3 строк" }));
  expect(await screen.findByText("Тип «ТуТ» → «Термоусадочная трубка»: строк 3.")).toBeInTheDocument();
  expect(sent[1]).toEqual(["/api/stock/item-types/rename", { source: "ТуТ", target: "Термоусадочная трубка" }]);
  await waitFor(() => expect(onChanged).toHaveBeenLastCalledWith(expect.anything(), true));
});

it("previews the imported table and writes it only on the second button", async () => {
  const imports: Array<{ edits: unknown[]; apply: boolean }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const body = JSON.parse(String(init?.body));
    imports.push(body);
    return json(body.apply
      ? { applied: true, rows: 1, count: 1, changes: [], missing: [], missing_count: 0, errors: [] }
      : {
        applied: false, rows: 1, count: 1, missing: ["НЕТ.1"], missing_count: 1, errors: [],
        changes: [{ warehouse: "ЦАР", grn: "OLD.1", package: "", name: "A-1", column: "ТИП", was: "ТуТ", now: "Термоусадочная трубка" }],
      });
  });
  const onApplied = vi.fn();

  render(<StockImportPanel onClose={() => undefined} onApplied={onApplied} />);
  fireEvent.change(screen.getByLabelText("Таблица склада"), { target: { value: "ГРН\tТип\nOLD.1\tтут\nНЕТ.1\tЛента" } });
  fireEvent.click(screen.getByRole("button", { name: "Проверить" }));

  expect(await screen.findByText("Изменится полей: 1 в строках: 1. Проверь и подтверди.")).toBeInTheDocument();
  expect(screen.getByText("Не найдены на складе (1): НЕТ.1")).toBeInTheDocument();
  expect(imports).toEqual([{ apply: false, edits: [{ grn: "OLD.1", item_type: "тут" }, { grn: "НЕТ.1", item_type: "Лента" }] }]);

  fireEvent.click(screen.getByRole("button", { name: "Записать 1 изменений" }));
  expect(await screen.findByText("Записано: 1 полей в 1 строках. Каждая правка — в журнале склада.")).toBeInTheDocument();
  expect(imports[1].apply).toBe(true);
  expect(onApplied).toHaveBeenCalled();
});

it("says why a table cannot be checked without asking the server", async () => {
  const fetch = vi.spyOn(globalThis, "fetch");
  render(<StockImportPanel onClose={() => undefined} onApplied={() => undefined} />);
  fireEvent.change(screen.getByLabelText("Таблица склада"), { target: { value: "Тип\nКабель" } });
  fireEvent.click(screen.getByRole("button", { name: "Проверить" }));
  expect(await screen.findByText("В шапке нет колонки «ГРН» — без неё строки не найти.")).toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});
