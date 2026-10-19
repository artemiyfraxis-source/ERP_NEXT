import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DataTable, columnClass, compareCells, matchesWords, moveKey, orderColumns, readSaved, searchHint, tableText, type DataColumn } from "./DataTable";

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

describe("columns the user arranges: order, width, caption", () => {
  const heads = () => screen.getAllByRole("columnheader").map((head) => head.textContent);
  const table = (key = "arrange") => <DataTable rows={rows} columns={columns} rowKey={(row) => row.id} storageKey={key} exportName="Поставки" />;

  it("moves, widens and renames columns from «Колонки», remembers them and exports in that order", async () => {
    const { unmount } = render(table());
    fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
    const chooser = screen.getByRole("group", { name: "Показывать колонки" });
    fireEvent.click(within(chooser).getByRole("button", { name: "Ниже: Наименование" }));
    fireEvent.change(within(chooser).getByLabelText("Своя подпись: Кол-во"), { target: { value: "Штук" } });
    fireEvent.click(within(chooser).getByRole("button", { name: "Шире: Поставщик" })); // not measured in jsdom: 120 + 16
    expect(heads()).toEqual(["Поставщик", "Наименование", "Штук"]);
    expect(within(chooser).getByRole("button", { name: /Ширина Поставщик: 136 пикселей/ })).toHaveTextContent(/^136\spx$/);
    expect(JSON.parse(window.localStorage.getItem("erp-table:arrange")!)).toMatchObject({
      order: ["supplier", "name", "qty", "status"], widths: { supplier: 136 }, titles: { qty: "Штук" }, hidden: ["status"],
    });
    unmount();

    render(table());
    expect(heads()).toEqual(["Поставщик", "Наименование", "Штук"]);
    const supplier = screen.getByRole("columnheader", { name: "Поставщик" });
    expect(supplier).toHaveClass("dt-w");
    expect(supplier.style.getPropertyValue("--dt-w")).toBe("136px");
    expect(screen.getByText("Чип и Дип", { selector: "td > div" })).toHaveClass("dt-clip"); // a narrowed column cuts long text
    expect(screen.getByRole("columnheader", { name: "Штук" })).toHaveAttribute("title", "Кол-во"); // the real name on hover
    expect(screen.getByLabelText("Поиск: Таблица")).toHaveAttribute("placeholder", "Поиск: поставщик, наименование, штук…");

    // Excel follows the user's order, visibility and captions.
    let blob: Blob | null = null;
    vi.spyOn(URL, "createObjectURL").mockImplementation((value) => { blob = value as Blob; return "blob:x"; });
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    fireEvent.click(screen.getByRole("button", { name: "Excel" }));
    const text = new TextDecoder("utf-16le").decode(new Uint8Array(await blob!.arrayBuffer()).slice(2));
    expect(text.split("\r\n")[0]).toBe("Поставщик\tНаименование\tШтук");

    fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
    fireEvent.click(screen.getByRole("button", { name: "Сбросить вид" }));
    expect(heads()).toEqual(["Наименование", "Поставщик", "Кол-во"]);
    expect(JSON.parse(window.localStorage.getItem("erp-table:arrange")!)).toEqual({ hidden: ["status"], sort: null, preset: "" });
  });

  it("reads the state saved before columns could move: hidden, sort and the screen's own fields stay", () => {
    window.localStorage.setItem("erp-table:old", JSON.stringify({ hidden: ["supplier", "status"], sort: { key: "qty", direction: -1 }, preset: "", groups: { a: 1 }, widths: { name: "wide" }, order: "name" }));
    expect(readSaved("old")).not.toHaveProperty("widths");
    expect(readSaved("old")).not.toHaveProperty("order");
    render(table("old"));
    expect(heads()).toEqual(["Наименование", "Кол-во ▼"]);
    expect(names().map((line) => line.split("|")[1])).toEqual(["100", "10", "2"]);
    expect(screen.getByRole("columnheader", { name: "Наименование" })).not.toHaveClass("dt-w"); // a broken width is ignored
    expect(readSaved("old")).toMatchObject({ groups: { a: 1 }, hidden: ["supplier", "status"] }); // a screen's own field survives the table's writes
  });

  it("puts a column the saved order does not know after its left neighbour", () => {
    expect(orderColumns(columns, ["qty", "name"]).map((column) => column.key)).toEqual(["qty", "status", "name", "supplier"]);
    expect(orderColumns(columns, undefined)).toBe(columns);
    expect(moveKey(["a", "b", "c"], "a", "c")).toEqual(["b", "c", "a"]);
    expect(moveKey(["a", "b", "c"], "c", "a")).toEqual(["c", "a", "b"]);
  });

  it("moves a column by dragging its header and resizes it by dragging the edge", () => {
    render(table("drag"));
    const head = (title: string) => screen.getByRole("columnheader", { name: title });
    fireEvent.dragStart(head("Кол-во"));
    fireEvent.dragOver(head("Наименование"));
    expect(head("Наименование")).toHaveClass("dt-drop-before");
    fireEvent.drop(head("Наименование"));
    expect(heads()).toEqual(["Кол-во", "Наименование", "Поставщик"]);

    const handle = head("Поставщик").querySelector(".dt-resize")!;
    fireEvent(handle, new MouseEvent("pointerdown", { clientX: 100, bubbles: true }));
    act(() => {
      handle.dispatchEvent(new MouseEvent("pointermove", { clientX: 160 }));
      handle.dispatchEvent(new MouseEvent("pointerup"));
      handle.dispatchEvent(new MouseEvent("pointermove", { clientX: 400 })); // released: no longer follows
    });
    expect(head("Поставщик").style.getPropertyValue("--dt-w")).toBe("180px");
    fireEvent.doubleClick(handle);
    expect(head("Поставщик")).not.toHaveClass("dt-w");
    expect(JSON.parse(window.localStorage.getItem("erp-table:drag")!).order).toEqual(["qty", "name", "supplier", "status"]);
  });
});

describe("cells edited right in the table, like a sheet", () => {
  function setup(save = vi.fn(async (_row: Row, _value: string) => undefined as unknown)) {
    const onEdited = vi.fn(async () => undefined);
    const detail = vi.fn((row: Row) => <p>Карточка {row.name}</p>);
    const editable: DataColumn<Row>[] = [
      { key: "name", title: "Наименование", value: (row) => row.name },
      { key: "supplier", title: "Поставщик", value: (row) => row.supplier, edit: { save } },
      // «Кол-во» of a delivered row is closed: the column decides per row.
      { key: "qty", title: "Кол-во", value: (row) => row.qty, numeric: true, edit: { save, enabled: (row) => row.status !== "Принято" } },
    ];
    render(<DataTable rows={rows} columns={editable} rowKey={(row) => row.id} rowLabel={(row) => row.name} renderDetail={detail} onEdited={onEdited} />);
    const cell = (name: string, key: string) => {
      const line = screen.getByText(name).closest("tr")!;
      return line.querySelectorAll("td")[["name", "supplier", "qty"].indexOf(key)] as HTMLElement;
    };
    return { save, onEdited, detail, cell };
  }

  it("opens a cell by typing, writes it with Enter through the record's save and moves down", async () => {
    const { save, onEdited, detail, cell } = setup();
    const target = cell("Разъём JST XH", "supplier");
    fireEvent.click(target);
    expect(target).toHaveClass("dt-active");
    expect(detail).not.toHaveBeenCalled(); // an editable cell does not open the row
    fireEvent.keyDown(target, { key: "Э" });
    const input = screen.getByLabelText("Поставщик: Разъём JST XH");
    expect(input).toHaveValue("Э");
    fireEvent.change(input, { target: { value: "Элитан " } });
    fireEvent.keyDown(input, { key: "Enter" });
    await vi.waitFor(() => expect(screen.queryByLabelText("Поставщик: Разъём JST XH")).toBeNull());
    expect(save).toHaveBeenCalledWith(rows[0], "Элитан");
    expect(onEdited).toHaveBeenCalledTimes(1);
    expect(cell("Провод МГТФ 0,12", "supplier")).toHaveClass("dt-active");
    expect(document.activeElement).toBe(cell("Провод МГТФ 0,12", "supplier"));
  });

  it("cancels with Esc, moves with Tab past closed cells, and leaves read-only cells to the row", async () => {
    const { save, detail, cell } = setup();
    const target = cell("Провод МГТФ 0,12", "supplier");
    fireEvent.doubleClick(target);
    const input = screen.getByLabelText("Поставщик: Провод МГТФ 0,12");
    expect(input).toHaveValue("Кабель-Торг");
    fireEvent.change(input, { target: { value: "Другой" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByLabelText("Поставщик: Провод МГТФ 0,12")).toBeNull();
    expect(target).toHaveTextContent("Кабель-Торг");
    expect(save).not.toHaveBeenCalled();

    // «Кол-во» of the delivered row is closed: Tab goes on to the next row's first editable cell.
    expect(cell("Провод МГТФ 0,12", "qty")).not.toHaveClass("dt-editable");
    fireEvent.keyDown(target, { key: "Tab" });
    expect(cell("Разъём Molex", "supplier")).toHaveClass("dt-active");
    fireEvent.keyDown(cell("Разъём Molex", "supplier"), { key: "Enter" });
    fireEvent.keyDown(screen.getByLabelText("Поставщик: Разъём Molex"), { key: "Tab" }); // unchanged: nothing to write
    await vi.waitFor(() => expect(cell("Разъём Molex", "qty")).toHaveClass("dt-active"));
    expect(save).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("Разъём Molex"));
    expect(detail).toHaveBeenCalled(); // the key column still opens the record
  });

  it("keeps the typed value and shows the refusal in the cell, then writes over the fresh value", async () => {
    const save = vi.fn(async (_row: Row, _value: string) => undefined as unknown).mockRejectedValueOnce(new Error("Ячейку уже поменяли — в таблице свежее значение."));
    const { onEdited, cell } = setup(save);
    fireEvent.click(cell("Разъём Molex", "qty"));
    fireEvent.keyDown(cell("Разъём Molex", "qty"), { key: "5" });
    const input = screen.getByLabelText("Кол-во: Разъём Molex");
    expect(input).toHaveAttribute("inputmode", "decimal");
    fireEvent.change(input, { target: { value: "50" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByRole("alert")).toHaveTextContent("Ячейку уже поменяли");
    expect(input).toHaveValue("50");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(onEdited).toHaveBeenCalledTimes(1); // the rows are read again under the open cell
    fireEvent.keyDown(input, { key: "Enter" });
    await vi.waitFor(() => expect(screen.queryByLabelText("Кол-во: Разъём Molex")).toBeNull());
    expect(save).toHaveBeenLastCalledWith(rows[2], "50");
  });

  it("pastes one value into a selected range, a copied column downwards, and fills down with Ctrl+D", async () => {
    const { save, cell } = setup();
    fireEvent.click(cell("Разъём JST XH", "supplier"));
    fireEvent.click(cell("Разъём Molex", "supplier"), { shiftKey: true });
    expect(cell("Провод МГТФ 0,12", "supplier")).toHaveClass("dt-in-range");
    fireEvent.paste(cell("Разъём Molex", "supplier"), { clipboardData: { getData: () => "Элитан" } });
    await screen.findByText("Поставщик: записано ячеек — 3");
    expect(save.mock.calls.map(([row, value]) => `${row.id}=${value}`)).toEqual(["1=Элитан", "2=Элитан", "3=Элитан"]);

    save.mockClear();
    fireEvent.click(cell("Провод МГТФ 0,12", "supplier"));
    fireEvent.paste(cell("Провод МГТФ 0,12", "supplier"), { clipboardData: { getData: () => "А\tлишнее\r\nБ\r\nВ\r\n" } });
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(2)); // two rows from the cell down: «В» has no row
    expect(save.mock.calls.map(([row, value]) => `${row.id}=${value}`)).toEqual(["2=А", "3=Б"]);

    save.mockClear();
    fireEvent.keyDown(cell("Провод МГТФ 0,12", "supplier"), { key: "d", code: "KeyD", ctrlKey: true }); // the value from above
    await vi.waitFor(() => expect(save).toHaveBeenCalledWith(rows[1], "Чип и Дип"));
  });

  describe("a choice cell (owner 16.10: «редактирование везде сделать как в экселе»)", () => {
    const STATUSES = [
      { value: "s-way", label: "В пути", aliases: ["едет"] },
      { value: "s-done", label: "Принято" },
      { value: "s-back", label: "Возврат" },
    ];
    const statusOf = (row: Row) => STATUSES.find((item) => item.label === row.status)!.value;
    function choice(save = vi.fn(async (_row: Row, _value: string) => undefined as unknown)) {
      const onEdited = vi.fn(async () => undefined);
      const list: DataColumn<Row>[] = [
        { key: "name", title: "Наименование", value: (row) => row.name },
        { key: "status", title: "Статус", value: (row) => row.status, edit: { save, value: statusOf, options: () => STATUSES } },
        { key: "why", title: "Почему", value: () => "по правилу", computed: true },
      ];
      render(<DataTable rows={rows} columns={list} rowKey={(row) => row.id} rowLabel={(row) => row.name} onEdited={onEdited} />);
      const cell = (name: string, index = 1) => screen.getByText(name).closest("tr")!.querySelectorAll("td")[index] as HTMLElement;
      return { save, cell, onEdited };
    }

    it("opens a list in the cell: a typed letter picks an option, Enter writes its value and moves down", async () => {
      const { save, cell } = choice();
      fireEvent.click(cell("Разъём JST XH"));
      fireEvent.keyDown(cell("Разъём JST XH"), { key: "в" });
      const select = screen.getByRole("combobox", { name: "Статус: Разъём JST XH" });
      expect(select).toHaveValue("s-way"); // «в» — the first option starting with it («В пути», then «Возврат»)
      fireEvent.keyDown(select, { key: "ArrowDown" });
      fireEvent.change(select, { target: { value: "s-done" } }); // arrows in a closed list only move the draft
      expect(save).not.toHaveBeenCalled();
      fireEvent.keyDown(select, { key: "Enter" });
      await vi.waitFor(() => expect(save).toHaveBeenCalledWith(rows[0], "s-done"));
      await vi.waitFor(() => expect(cell("Провод МГТФ 0,12")).toHaveClass("dt-active"));
    });

    it("writes a pick by the mouse at once; Esc closes without writing; the computed cell is read-only", async () => {
      const { save, cell } = choice();
      fireEvent.click(cell("Разъём Molex"));
      fireEvent.click(cell("Разъём Molex")); // the active cell opens
      let select = screen.getByRole("combobox", { name: "Статус: Разъём Molex" });
      expect(select).toHaveValue("s-way");
      expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual(["В пути", "Принято", "Возврат"]);
      fireEvent.keyDown(select, { key: "Escape" });
      expect(screen.queryByRole("combobox")).toBeNull();
      fireEvent.doubleClick(cell("Разъём Molex"));
      select = screen.getByRole("combobox", { name: "Статус: Разъём Molex" });
      fireEvent.pointerDown(select);
      fireEvent.change(select, { target: { value: "s-back" } });
      await vi.waitFor(() => expect(save).toHaveBeenCalledWith(rows[2], "s-back"));
      await vi.waitFor(() => expect(screen.queryByRole("combobox")).toBeNull());
      expect(cell("Разъём Molex", 2)).toHaveClass("dt-computed");
      expect(cell("Разъём Molex", 2)).not.toHaveClass("dt-editable");
    });

    it("shows the server's refusal under the list and keeps the pick", async () => {
      const save = vi.fn(async (_row: Row, _value: string) => undefined as unknown).mockRejectedValueOnce(new Error("Ячейку уже поменяли"));
      const { cell } = choice(save);
      fireEvent.doubleClick(cell("Разъём JST XH"));
      const select = screen.getByRole("combobox", { name: "Статус: Разъём JST XH" });
      fireEvent.change(select, { target: { value: "s-done" } });
      expect(await screen.findByRole("alert")).toHaveTextContent("Ячейку уже поменяли");
      expect(select).toHaveValue("s-done");
      expect(select).toHaveAttribute("aria-invalid", "true");
    });

    it("pastes a text that names an option by its label or another spelling, refuses one that names none, copies what it shows", async () => {
      const { save, cell } = choice();
      fireEvent.click(cell("Разъём JST XH"));
      fireEvent.paste(cell("Разъём JST XH"), { clipboardData: { getData: () => "принято\r\nедет\r\nкуда-то\r\n" } });
      await screen.findByText(/Не записано 1 из 3: Разъём Molex — «куда-то» — нет в списке/);
      expect(save.mock.calls.map(([row, value]) => `${row.id}=${value}`)).toEqual(["1=s-done", "2=s-way"]);
      const data = { setData: vi.fn() };
      fireEvent.copy(cell("Разъём JST XH"), { clipboardData: data });
      expect(data.setData).toHaveBeenCalledWith("text/plain", "В пути");
    });
  });

  it("has no editable cells without an edit on the column", () => {
    const { container } = render(<DataTable rows={rows} columns={columns} rowKey={(row) => row.id} />);
    expect(container.querySelector("td.dt-editable")).toBeNull();
    expect(screen.queryByText(/Ctrl\+D/)).toBeNull();
  });
});

describe("the header sorts inside every group (owner 19.10: «сделай просто вверху столбцов сортировку»)", () => {
  const stock: Row[] = [
    { id: "a1", name: "Б-разъём", supplier: "Чип и Дип", qty: 5, status: "" },
    { id: "a2", name: "А-разъём", supplier: "Чип и Дип", qty: 50, status: "" },
    { id: "a3", name: "В-разъём", supplier: "Чип и Дип", qty: 1, status: "" },
    { id: "b1", name: "Провод 2", supplier: "Кабель-Торг", qty: 7, status: "" },
    { id: "b2", name: "Провод 1", supplier: "Кабель-Торг", qty: 3, status: "" },
  ];
  const table = () => <DataTable rows={stock} columns={columns} rowKey={(row) => row.id} storageKey="grouped" groupBy={(row) => row.supplier} groupSort />;
  const order = () => screen.getAllByRole("row").slice(1).map((row) => row.classList.contains("group-row")
    ? `[${row.querySelector("strong")?.textContent}]` : row.querySelector("td")?.textContent);

  it("a header click sorts the rows of every group up, then down, then back; the order of groups stays", () => {
    const { unmount } = render(table());
    const catalogue = ["[Чип и Дип]", "Б-разъём", "А-разъём", "В-разъём", "[Кабель-Торг]", "Провод 2", "Провод 1"];
    expect(order()).toEqual(catalogue);
    expect(screen.queryByRole("combobox", { name: /Сортировка группы/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Кол-во" }));
    expect(order()).toEqual(["[Чип и Дип]", "В-разъём", "Б-разъём", "А-разъём", "[Кабель-Торг]", "Провод 1", "Провод 2"]);
    fireEvent.click(screen.getByRole("button", { name: /Кол-во/ }));
    expect(order()).toEqual(["[Чип и Дип]", "А-разъём", "Б-разъём", "В-разъём", "[Кабель-Торг]", "Провод 2", "Провод 1"]);
    // remembered with the table's key
    unmount();
    render(table());
    expect(order()).toEqual(["[Чип и Дип]", "А-разъём", "Б-разъём", "В-разъём", "[Кабель-Торг]", "Провод 2", "Провод 1"]);
    fireEvent.click(screen.getByRole("button", { name: /Кол-во/ }));
    expect(order()).toEqual(catalogue);
  });

  it("keeps the header sort of other grouped tables as it was", () => {
    render(<DataTable rows={stock} columns={columns} rowKey={(row) => row.id} groupBy={(row) => row.supplier} />);
    expect(screen.queryByRole("combobox", { name: /Сортировка группы/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Кол-во" }));
    expect(order()[0]).toBe("[Чип и Дип]");
    expect(order()[1]).toBe("В-разъём");
  });
});

it("shows every row at once with an unlimited page (the cable lists: 369 multi-core cables and more)", () => {
  const many: Row[] = Array.from({ length: 450 }, (_, index) => ({ id: String(index), name: `Кабель ${index}`, supplier: index % 2 ? "А" : "Б", qty: index, status: "" }));
  const { container } = render(<DataTable rows={many} columns={columns} rowKey={(row) => row.id} groupBy={(row) => row.supplier} pageSize={Infinity} />);
  expect(container.querySelectorAll("tbody tr:not(.group-row)")).toHaveLength(450);
  expect(screen.getByText("Показано 450 из 450")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Показать ещё" })).toBeNull();
});

it("shows «Удалить» in «Колонки» only for a column that can be removed and calls it", () => {
  const remove = vi.fn();
  const columns: DataColumn<Row>[] = [
    { key: "name", title: "Название", value: (row) => row.name },
    { key: "shelf", title: "Полка", value: () => "", remove },
  ];
  render(<DataTable rows={rows} columns={columns} rowKey={(row) => row.id} />);
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  expect(screen.queryByRole("button", { name: "Удалить столбец: Название" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Удалить столбец: Полка" }));
  expect(remove).toHaveBeenCalledTimes(1);
});
