import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ItemTypesPanel, RackLayoutPanel, StockImportPanel, StockJournalPanel } from "./WarehouseReferences";

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

it("shows the warehouse journal newest first and searches it on the server", async () => {
  const paths: string[] = [];
  const row = (kind: string, event: string) => ({
    occurred_at: "2026-09-22T10:00:00+03:00", kind, lot: "OLD.26.0000012", name: "Кабель", event, who: "Иванов", warehouse: "ЦАР",
  });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    paths.push(path);
    if (path === "/api/stock/journal") return json({ rows: [row("Событие", "Перемещение: 1F4 → 2A1"), row("Списание", "−2 шт · Брак · проведено")], total: 900, truncated: 898 });
    return json({ rows: [row("Возврат", "+1 шт · Излишек · проведено")], total: 1, truncated: 0 });
  });

  render(<StockJournalPanel onClose={() => undefined} />);
  expect(await screen.findByText("Перемещение: 1F4 → 2A1")).toBeInTheDocument();
  expect(screen.getByText("Показано 2 из 900 — уточни поиск, чтобы увидеть более старые.")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Поиск по журналу склада"), { target: { value: " возврат " } });
  expect(await screen.findByText("+1 шт · Излишек · проведено", {}, { timeout: 2000 })).toBeInTheDocument();
  expect(paths.at(-1)).toBe("/api/stock/journal?q=%D0%B2%D0%BE%D0%B7%D0%B2%D1%80%D0%B0%D1%82");
});

it("«Раскладка»: edits racks, default shelves and a special rack and shows the saved summary", async () => {
  let saved: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input) === "/api/stock/rack-layout" && init?.method === "PUT") {
      saved = JSON.parse(String(init.body));
      return new Response(JSON.stringify({ layout: { racks: 5, shelves: 7, perRack: { "2": 3 } }, message: "Стеллажей: 5, полок по умолчанию: 7, особых стеллажей: 1." }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    return new Response(JSON.stringify({ layout: { racks: 32, shelves: 26, perRack: {} } }), { status: 200, headers: { "Content-Type": "application/json" } });
  });

  render(<RackLayoutPanel onClose={() => undefined} />);
  fireEvent.change(await screen.findByLabelText("Стеллажей"), { target: { value: "5" } });
  fireEvent.change(screen.getByLabelText("Полок по умолчанию"), { target: { value: "7" } });
  fireEvent.click(screen.getByRole("button", { name: "+ особый стеллаж" }));
  fireEvent.change(screen.getByLabelText("Особый стеллаж 1"), { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText("Полок на особом стеллаже 1"), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить раскладку" }));

  expect(await screen.findByText("Стеллажей: 5, полок по умолчанию: 7, особых стеллажей: 1.")).toBeInTheDocument();
  expect(saved).toEqual({ racks: 5, shelves: 7, perRack: { "2": 3 } });
});
