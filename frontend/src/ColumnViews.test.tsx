import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ColumnViewsProvider } from "./ColumnViews";
import { useColumnRemoval } from "./CustomColumn";
import { DataTable, type DataColumn } from "./DataTable";
import { referencesApi, type SharedColumn } from "./api";

interface Row { id: string; name: string; supplier: string; qty: number; status: string }

const rows: Row[] = [
  { id: "1", name: "5D-FB", supplier: "Кабель-Торг", qty: 10, status: "В пути" },
  { id: "2", name: "8D-FB", supplier: "Чип и Дип", qty: 2, status: "Принято" },
];
const columns: DataColumn<Row>[] = [
  { key: "name", title: "Марка", value: (row) => row.name },
  { key: "supplier", title: "Поставщик", value: (row) => row.supplier },
  { key: "qty", title: "Кол-во", value: (row) => row.qty, numeric: true },
  { key: "status", title: "Статус", value: (row) => row.status, hidden: true },
];
// The owner's choice: Марка, Статус (moved up), Кол-во; the supplier is not needed.
const SHARED: SharedColumn[] = [
  { key: "name", visible: true }, { key: "status", visible: true }, { key: "qty", visible: true }, { key: "supplier", visible: false },
];

afterEach(() => { window.localStorage.clear(); vi.restoreAllMocks(); });

function mount(views: Record<string, SharedColumn[]>, canEdit: boolean) {
  vi.spyOn(referencesApi, "columnViews").mockResolvedValue({ views, can_edit: canEdit });
  return render(<ColumnViewsProvider><DataTable rows={rows} columns={columns} rowKey={(row) => row.id} storageKey="t" sharedKey="t" label="Кабели" /></ColumnViewsProvider>);
}

const headers = () => screen.getAllByRole("columnheader").map((header) => header.textContent?.replace(/[↑↓▲▼]/g, "").trim()).filter(Boolean);

describe("общий набор столбцов", () => {
  it("shows only the shared columns in the shared order to everyone", async () => {
    mount({ t: SHARED }, false);

    await waitFor(() => expect(headers()).toEqual(["Марка", "Статус", "Кол-во"]));
  });

  it("opens with the table's own columns while no shared set exists", async () => {
    mount({}, true);

    await waitFor(() => expect(referencesApi.columnViews).toHaveBeenCalled());
    expect(headers()).toEqual(["Марка", "Поставщик", "Кол-во"]);
  });

  it("«Показать все» shows every column, «Только нужные» goes back to the shared set, the first column stays", async () => {
    mount({ t: SHARED }, false);
    await waitFor(() => expect(headers()).toEqual(["Марка", "Статус", "Кол-во"]));

    fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
    expect(document.querySelector(".column-chooser-head")?.textContent).toContain("Показано 3 из 4");
    expect(screen.getByRole("checkbox", { name: "Марка" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Показать все" }));
    expect(headers()).toEqual(["Марка", "Статус", "Кол-во", "Поставщик"]);
    fireEvent.click(screen.getByRole("button", { name: "Только нужные" }));

    expect(headers()).toEqual(["Марка", "Статус", "Кол-во"]);
  });

  it("without a shared set «Только нужные» returns the table's default columns", async () => {
    mount({}, false);
    fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
    fireEvent.click(screen.getByRole("button", { name: "Показать все" }));
    expect(headers()).toHaveLength(4);

    fireEvent.click(screen.getByRole("button", { name: "Только нужные" }));

    expect(headers()).toEqual(["Марка", "Поставщик", "Кол-во"]);
  });

  it("offers «Сохранить для всех» only to those who edit, and sends the order and what is shown", async () => {
    const save = vi.spyOn(referencesApi, "saveColumnView").mockImplementation(async (view, saved) => ({ view, columns: saved }));
    mount({}, true);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Колонки" })).not.toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Поставщик" }));
    fireEvent.click(screen.getByRole("button", { name: "Ниже: Марка" }));
    // the name column is first by data, so the moved one is the second: press «Выше» on the quantity
    fireEvent.click(screen.getByRole("button", { name: "Выше: Кол-во" }));

    fireEvent.click(screen.getByRole("button", { name: "Сохранить для всех" }));

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    const [view, saved] = save.mock.calls[0];
    expect(view).toBe("t");
    expect(saved.find((item) => item.key === "supplier")).toEqual({ key: "supplier", visible: false });
    expect(saved.find((item) => item.key === "name")?.visible).toBe(true);
    expect(saved.find((item) => item.key === "status")?.visible).toBe(false);
    expect(await screen.findByText("Сохранено для всех")).toBeInTheDocument();
  });

  it("a viewer without edit rights has no «Сохранить для всех»", async () => {
    mount({ t: SHARED }, false);
    await waitFor(() => expect(headers()).toEqual(["Марка", "Статус", "Кол-во"]));

    fireEvent.click(screen.getByRole("button", { name: "Колонки" }));

    expect(screen.queryByRole("button", { name: "Сохранить для всех" })).toBeNull();
  });

  it("a new shared set replaces a personal layout once; the same set keeps the personal one", async () => {
    const sig = SHARED.map((item) => `${item.key}:${item.visible ? 1 : 0}`).join("|");
    window.localStorage.setItem("erp-table:t", JSON.stringify({ hidden: ["status", "qty"], known: ["name", "supplier", "qty", "status"], sharedSig: sig }));
    const kept = mount({ t: SHARED }, false);
    await waitFor(() => expect(referencesApi.columnViews).toHaveBeenCalled());
    await waitFor(() => expect(headers()).toEqual(["Марка", "Поставщик"]));
    kept.unmount();

    window.localStorage.setItem("erp-table:t", JSON.stringify({ hidden: ["status", "qty"], known: ["name", "supplier", "qty", "status"], sharedSig: "old" }));
    mount({ t: SHARED }, false);

    await waitFor(() => expect(headers()).toEqual(["Марка", "Статус", "Кол-во"]));
    expect(JSON.parse(window.localStorage.getItem("erp-table:t")!).sharedSig).toBe(sig);
  });

  it("the list is ordered by dragging a row", async () => {
    mount({}, true);
    fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
    const items = screen.getAllByRole("listitem");
    const grip = items[2].querySelector(".column-grip") as HTMLElement;

    fireEvent.dragStart(grip, { dataTransfer: { setData: () => undefined, effectAllowed: "" } });
    fireEvent.drop(items[0], { dataTransfer: {} });

    const order = screen.getAllByRole("listitem").map((item) => within(item).getByRole("checkbox").closest("label")?.textContent);
    expect(order).toEqual(["Кол-во", "Марка", "Поставщик", "Статус"]);
  });
});

describe("удаление своего столбца", () => {
  function Harness({ filled }: { filled: number }) {
    const removal = useColumnRemoval("prov", true, () => filled, () => undefined, "coax");
    return <><button type="button" onClick={removal.removeOf("Полка")}>открыть</button>{removal.dialog}</>;
  }

  it("names how many records hold data, lets it go and sends the removal", async () => {
    const remove = vi.spyOn(referencesApi, "removeColumn").mockResolvedValue({ tab: "prov", name: "Полка", records: 5 });
    render(<Harness filled={3} />);

    fireEvent.click(screen.getByRole("button", { name: "открыть" }));
    const dialog = screen.getByRole("dialog", { name: "Удалить столбец" });
    expect(dialog.textContent).toContain("записей — 3");
    expect(dialog.textContent).toContain("в журнале");
    fireEvent.click(within(dialog).getByRole("button", { name: "Удалить столбец и 3 знач." }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith("prov", "Полка", "coax"));
  });
});
