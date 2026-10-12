import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DataTable, columnClass, compareCells, matchesWords, searchHint, tableText, type DataColumn } from "./DataTable";

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

it("hideSearch removes only the search box: filters, columns and the table stay", () => {
  const props = { label: "Тест", rows, columns, rowKey: (row: Row) => row.id, storageKey: "t-hide" };
  const { rerender } = render(<DataTable {...props} />);
  expect(screen.getByRole("searchbox")).toBeInTheDocument();
  rerender(<DataTable {...props} hideSearch />);
  expect(screen.queryByRole("searchbox")).toBeNull();
  expect(screen.getByRole("button", { name: "Фильтры" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Колонки" })).toBeInTheDocument();
  expect(names()).toHaveLength(3);
});

describe("column sizing and number cells", () => {
  interface Line { id: string; name: string; note: string; qty: number | string | null; code: string }
  const lines: Line[] = [
    { id: "1", name: "Разъём Molex Micro-Fit 3.0, 12 pin, корпус розетки", note: "", qty: "12345.500", code: "A-1" },
    { id: "2", name: "Провод МГТФ", note: "срочно", qty: null, code: "" },
  ];
  const sized: DataColumn<Line>[] = [
    { key: "code", title: "Код", value: (line) => line.code },
    { key: "name", title: "Наименование", value: (line) => line.name, wrap: true, maxWidth: 420 },
    { key: "qty", title: "Кол-во", value: (line) => line.qty, numeric: true },
    { key: "note", title: "Примечание", value: (line) => line.note, width: 120 },
  ];

  it("sizes columns by content and gives the spare width to an empty last column", () => {
    const { container } = render(<DataTable rows={lines} columns={sized} rowKey={(line) => line.id} label="Позиции" />);
    const table = container.querySelector("table")!;
    expect(table).toHaveClass("sized");
    const heads = [...table.querySelectorAll("thead th")];
    expect(heads.map((th) => th.className)).toEqual(["fit key", "text fit", "num fit", "", "dt-fill"]);
    expect(heads[3]).toHaveStyle({ width: "120px" });
    expect(heads[4]).toHaveAttribute("aria-hidden", "true");
    expect(screen.getAllByRole("columnheader")).toHaveLength(4); // the filler is not a column for a screen reader
    for (const row of table.querySelectorAll("tbody tr")) expect(row.lastElementChild).toHaveClass("dt-fill");
    const wrap = table.querySelector("td.text .cell-wrap") as HTMLElement;
    expect(wrap.style.getPropertyValue("--cell-max")).toBe("420px");
  });

  it("lets a grow column take the spare width instead of the filler", () => {
    const columns = sized.map((column) => (column.key === "name" ? { ...column, grow: true, minWidth: 300 } : column));
    const { container } = render(<DataTable rows={lines} columns={columns} rowKey={(line) => line.id} />);
    expect(container.querySelector(".dt-fill")).toBeNull();
    expect(container.querySelector("thead th.grow")).toHaveTextContent("Наименование");
    expect((container.querySelector("td.grow .cell-wrap") as HTMLElement).style.getPropertyValue("--cell-min")).toBe("300px");
  });

  it("spans the empty row and the group header over the filler too", () => {
    const { container, rerender } = render(<DataTable rows={[]} columns={sized} rowKey={(line) => line.id} />);
    expect(container.querySelector("td.empty")).toHaveAttribute("colspan", "5");
    rerender(<DataTable rows={lines} columns={sized} rowKey={(line) => line.id} groupBy={() => "Все"} />);
    expect(container.querySelector("tr.group-row td")).toHaveAttribute("colspan", "5");
  });

  it("keeps the screen's widths when its table has a fixed layout", () => {
    const real = window.getComputedStyle.bind(window);
    vi.spyOn(window, "getComputedStyle").mockImplementation((element, pseudo) => {
      const style = real(element, pseudo);
      return element instanceof HTMLTableElement ? { ...style, tableLayout: "fixed" } as CSSStyleDeclaration : style;
    });
    const { container } = render(<DataTable rows={lines} columns={sized} rowKey={(line) => line.id} />);
    expect(container.querySelector("table")).not.toHaveClass("sized");
    expect(container.querySelector(".fit, .dt-fill")).toBeNull();
  });

  it("writes numbers the Russian way, right-aligned, and an empty cell as a dash", () => {
    const { container } = render(<DataTable rows={lines} columns={sized} rowKey={(line) => line.id} />);
    const qty = [...container.querySelectorAll("tbody td.num")].map((td) => td.textContent);
    expect(qty).toEqual(["12\u202f345,5", "—"]);
    expect(container.querySelectorAll("tbody .cell-empty")).toHaveLength(3); // пустые кол-во, код и примечание
    // Sorting and the Excel export still use the raw value.
    expect(tableText(lines.slice(0, 1), sized.slice(2, 3))).toBe("Кол-во\r\n12345.500\r\n");
  });

  it("aligns a column on request and says what the search looks in", () => {
    const column: DataColumn<Line> = { key: "code", title: "Код", value: (line) => line.code, align: "center" };
    expect(columnClass(column, 1, true)).toBe("align-center fit");
    expect(columnClass({ ...column, numeric: true, align: "right" }, 0, false)).toBe("num key");
    expect(searchHint(sized)).toBe("Поиск: код, наименование, кол-во…");
    expect(searchHint([{ key: "grn", title: "ГРН", value: () => "" }, { key: "n", title: "№", value: () => "" }])).toBe("Поиск: ГРН, №…");
    render(<DataTable rows={lines} columns={sized} rowKey={(line) => line.id} label="Позиции" />);
    expect(screen.getByLabelText("Поиск: Позиции")).toHaveAttribute("placeholder", "Поиск: код, наименование, кол-во…");
  });
});

it("opens a clickable row from the keyboard", () => {
  const onRowClick = vi.fn();
  render(<DataTable rows={rows} columns={columns} rowKey={(row) => row.id} onRowClick={onRowClick} />);
  const row = screen.getByText("Разъём Molex").closest("tr")!;
  expect(row).toHaveAttribute("tabindex", "0");
  fireEvent.keyDown(row, { key: "Enter" });
  expect(onRowClick).toHaveBeenCalledWith(rows[2]);
  fireEvent.keyDown(row, { key: "a" });
  expect(onRowClick).toHaveBeenCalledTimes(1);
});

it("labels every cell with its column for the phone card layout, unless the screen keeps a table", () => {
  const { container, unmount } = render(<DataTable rows={rows} columns={columns} rowKey={(row) => row.id} label="Склад" />);
  expect(screen.getByRole("region", { name: "Склад" })).toHaveClass("data-table", "dt-cards");
  const cells = within(screen.getByText("Разъём Molex").closest("tr")!).getAllByRole("cell").filter((cell) => !cell.classList.contains("dt-fill"));
  expect(cells.map((cell) => cell.getAttribute("data-label"))).toEqual(["Наименование", "Поставщик", "Кол-во"]);
  expect(container.querySelector("td.dt-fill")).not.toHaveAttribute("data-label");
  unmount();
  render(<DataTable rows={rows} columns={columns} rowKey={(row) => row.id} label="Лист" phoneCards={false} />);
  expect(screen.getByRole("region", { name: "Лист" })).not.toHaveClass("dt-cards");
});
