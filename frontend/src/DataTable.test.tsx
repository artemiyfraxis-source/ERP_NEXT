import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DataTable, compareCells, matchesWords, tableText, type DataColumn } from "./DataTable";

interface Row { id: string; name: string; supplier: string; qty: number; status: string }

const rows: Row[] = [
  { id: "1", name: "Разъём JST XH", supplier: "Чип и Дип", qty: 10, status: "В пути" },
  { id: "2", name: "Провод МГТФ 0,12", supplier: "Кабель-Торг", qty: 2, status: "Принято" },
  { id: "3", name: "Разъём Molex", supplier: "Кабель-Торг", qty: 100, status: "В пути" },
];

const columns: DataColumn<Row>[] = [
  { key: "name", title: "Наименование", value: (row) => row.name },
  { key: "supplier", title: "Поставщик", value: (row) => row.supplier, filter: true },
  { key: "qty", title: "Кол-во", value: (row) => row.qty, numeric: true },
  { key: "status", title: "Статус", value: (row) => row.status, hidden: true },
];

function names() {
  return screen.getAllByRole("row").slice(1).map((row) => within(row).queryAllByRole("cell").map((cell) => cell.textContent).join("|"));
}

afterEach(() => { window.localStorage.clear(); vi.restoreAllMocks(); });

it("searches every word in any column, like SUPPLYMAP", () => {
  expect(matchesWords(["Разъём Molex", "Кабель-Торг"], "molex кабель")).toBe(true);
  expect(matchesWords(["Разъём Molex", "Кабель-Торг"], "molex чип")).toBe(false);
  expect(matchesWords(["x"], "  ")).toBe(true);
});

it("sorts numbers as numbers, text in Russian order, empties last", () => {
  expect([10, 2, 100].sort((a, b) => compareCells(a, b, true))).toEqual([2, 10, 100]);
  expect(["б", "", "А"].sort((a, b) => compareCells(a, b))).toEqual(["А", "б", ""]);
  expect(compareCells("1,5", "10", true)).toBeLessThan(0);
});

it("exports the shown columns as a tab table", () => {
  expect(tableText(rows.slice(0, 1), columns.slice(0, 2))).toBe("Наименование\tПоставщик\r\nРазъём JST XH\tЧип и Дип\r\n");
});

it("filters by search, column value and preset, and sorts by the header", () => {
  render(
    <DataTable
      rows={rows}
      columns={columns}
      rowKey={(row) => row.id}
      label="Поставки"
      presets={[{ label: "В пути", test: (row) => row.status === "В пути" }]}
    />,
  );
  expect(screen.queryByText("Статус")).toBeNull(); // hidden by default
  fireEvent.change(screen.getByLabelText("Поиск: Поставки"), { target: { value: "разъём" } });
  expect(names()).toHaveLength(2);
  fireEvent.change(screen.getByLabelText("Фильтр: Поставщик"), { target: { value: "Кабель-Торг" } });
  expect(names()).toEqual(["Разъём Molex|Кабель-Торг|100"]);
  fireEvent.change(screen.getByLabelText("Поиск: Поставки"), { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("Фильтр: Поставщик"), { target: { value: "" } });

  fireEvent.click(screen.getByRole("button", { name: "В пути · 2" }));
  expect(names()).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Все · 3" }));

  fireEvent.click(screen.getByRole("button", { name: "Кол-во" }));
  expect(names().map((line) => line.split("|")[2])).toEqual(["2", "10", "100"]);
  fireEvent.click(screen.getByRole("button", { name: "Кол-во ▲" }));
  expect(names().map((line) => line.split("|")[2])).toEqual(["100", "10", "2"]);
  expect(screen.getByText("Показано 3 из 3")).toBeTruthy();
});

it("runs a bulk action on the selected rows and clears the selection", async () => {
  const run = vi.fn();
  render(<DataTable rows={rows} columns={columns} rowKey={(row) => row.id} bulkActions={[{ label: "Взять в работу", run }]} />);

  fireEvent.click(screen.getByLabelText("Выбрать 1"));
  fireEvent.click(screen.getByLabelText("Выбрать 3"));
  fireEvent.click(await screen.findByRole("button", { name: "Взять в работу" }));

  expect(run).toHaveBeenCalledWith([rows[0], rows[2]]);
  await screen.findByLabelText("Выбрать все показанные");
  expect(screen.queryByText("Выбрано: 2")).toBeNull();
});

it("selects a range of rows with Shift+click and clears it the same way", () => {
  const run = vi.fn();
  render(<DataTable rows={rows} columns={columns} rowKey={(row) => row.id} bulkActions={[{ label: "Взять в работу", run }]} />);
  const checked = () => ["1", "2", "3"].map((id) => (screen.getByLabelText(`Выбрать ${id}`) as HTMLInputElement).checked);

  fireEvent.click(screen.getByLabelText("Выбрать 1"));
  fireEvent.click(screen.getByLabelText("Выбрать 3"), { shiftKey: true });
  expect(checked()).toEqual([true, true, true]);
  expect(screen.getByRole("toolbar")).toHaveTextContent("Выбрано: 3");

  fireEvent.click(screen.getByLabelText("Выбрать 2"));
  fireEvent.click(screen.getByLabelText("Выбрать 1"), { shiftKey: true });
  expect(checked()).toEqual([false, false, true]);
});

it("groups rows, opens a row detail and remembers hidden columns", () => {
  const { unmount } = render(
    <DataTable
      rows={rows}
      columns={columns}
      rowKey={(row) => row.id}
      storageKey="test"
      groupBy={(row) => row.supplier}
      renderDetail={(row) => <p>Карточка {row.name}</p>}
    />,
  );
  expect(screen.getByText("Кабель-Торг", { selector: "strong" })).toBeTruthy();
  fireEvent.click(screen.getByText("Провод МГТФ 0,12"));
  expect(screen.getByText("Карточка Провод МГТФ 0,12")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  fireEvent.click(screen.getByLabelText("Поставщик"));
  unmount();
  render(<DataTable rows={rows} columns={columns} rowKey={(row) => row.id} storageKey="test" />);
  expect(screen.queryByRole("button", { name: "Поставщик" })).toBeNull();
});

it("counts chips over the searched rows when asked (SUPPLYMAP issFilterRender_), over all rows by default", () => {
  const rows = [
    { id: "1", name: "Разъём", status: "Новая" },
    { id: "2", name: "Болт", status: "Новая" },
    { id: "3", name: "Разъём угловой", status: "Готово" },
  ];
  const columns = [
    { key: "name", title: "Наименование", value: (row: typeof rows[number]) => row.name },
    { key: "status", title: "Статус", value: (row: typeof rows[number]) => row.status },
  ];
  const presets = ["Новая", "Готово"].map((label) => ({ label, test: (row: typeof rows[number]) => row.status === label }));
  const { unmount } = render(<DataTable rows={rows} columns={columns} rowKey={(row) => row.id} label="Обычная" presets={presets} />);
  fireEvent.change(screen.getByLabelText("Поиск: Обычная"), { target: { value: "разъём" } });
  expect(screen.getByRole("button", { name: "Новая · 2" })).toBeInTheDocument();
  unmount();

  render(<DataTable rows={rows} columns={columns} rowKey={(row) => row.id} label="Заявки" presets={presets} presetCountsFollowSearch />);
  fireEvent.change(screen.getByLabelText("Поиск: Заявки"), { target: { value: "разъём" } });
  expect(screen.getByRole("button", { name: "Все · 2" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Новая · 1" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Готово · 1" })).toBeInTheDocument();
});

it("selects and clears a whole group from its header checkbox", () => {
  render(
    <DataTable
      rows={rows}
      columns={columns}
      rowKey={(row) => row.id}
      storageKey="group-select"
      groupBy={(row) => row.supplier}
      bulkActions={[{ label: "Действие", run: () => undefined }]}
    />,
  );
  const [groupA, groupB] = screen.getAllByLabelText("Выбрать группу") as HTMLInputElement[];
  fireEvent.click(groupB);
  expect((screen.getByLabelText("Выбрать 2") as HTMLInputElement).checked).toBe(true);
  expect((screen.getByLabelText("Выбрать 3") as HTMLInputElement).checked).toBe(true);
  expect((screen.getByLabelText("Выбрать 1") as HTMLInputElement).checked).toBe(false);
  fireEvent.click(screen.getByLabelText("Выбрать 3"));
  expect(groupB.indeterminate).toBe(true);
  expect(groupB.checked).toBe(false);
  fireEvent.click(groupA);
  expect(screen.getByText("Выбрано: 2")).toBeTruthy();
  fireEvent.click(groupA);
  expect(screen.getByText("Выбрано: 1")).toBeTruthy();
});

it("shows a custom group label with the selected count and hides inapplicable bulk actions", () => {
  render(
    <DataTable
      rows={rows}
      columns={columns}
      rowKey={(row) => row.id}
      storageKey="group-label"
      groupBy={(row) => row.supplier}
      groupLabel={(name, group) => `${name} · ${group[0].name}`}
      bulkActions={[
        { label: "Всегда", run: () => undefined },
        { label: "Только Чип", run: () => undefined, hidden: (picked) => !picked.some((row) => row.supplier === "Чип и Дип") },
      ]}
    />,
  );
  const header = screen.getByText("Кабель-Торг · Провод МГТФ 0,12", { selector: "strong" }).parentElement!;
  expect(header.textContent).toBe("Кабель-Торг · Провод МГТФ 0,12 · 2");
  fireEvent.click(screen.getByLabelText("Выбрать 3"));
  expect(header.textContent).toBe("Кабель-Торг · Провод МГТФ 0,12 · 2 · выбрано 1");
  expect(screen.queryByRole("button", { name: "Только Чип" })).toBeNull();
  expect(screen.getByRole("button", { name: "Всегда" })).toBeTruthy();
  fireEvent.click(screen.getByLabelText("Выбрать 1"));
  expect(screen.getByRole("button", { name: "Только Чип" })).toBeTruthy();
});
