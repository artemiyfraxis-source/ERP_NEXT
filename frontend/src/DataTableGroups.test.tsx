import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DataTable, type DataColumn } from "./DataTable";

interface Row { id: string; project: string; shipment: string; kb: string; quantity: number; carrier: string }
const rows: Row[] = [
  { id: "a2", project: "663К.1", shipment: "ОТГ-2", kb: "02", quantity: 20, carrier: "СДЭК" },
  { id: "b1", project: "700К.1", shipment: "ОТГ-3", kb: "01", quantity: 3, carrier: "ПЭК" },
  { id: "a1", project: "663К.1", shipment: "ОТГ-2", kb: "01", quantity: 10, carrier: "СДЭК" },
  { id: "a3", project: "663К.1", shipment: "ОТГ-1", kb: "03", quantity: 5, carrier: "Деловые линии" },
  { id: "a4", project: "663К.1", shipment: "", kb: "04", quantity: 7, carrier: "Самовывоз" },
];
const columns: DataColumn<Row>[] = [
  { key: "kb", title: "КБ", value: (row) => row.kb },
  { key: "quantity", title: "Количество", value: (row) => row.quantity, numeric: true },
  { key: "shipment", title: "Отправка", value: (row) => row.shipment, render: () => null },
  { key: "carrier", title: "ТК", value: (row) => row.carrier, filter: true, render: (row) => row.shipment ? null : row.carrier },
  { key: "project", title: "Проект", value: (row) => row.project, hidden: true, export: true },
];
const groupBy = (row: Row) => `${row.project}:${row.shipment}`;
const groupParent = (row: Row) => row.project;
const groupCells = (_key: string, group: Row[]) => group[0].shipment ? {
  shipment: <button type="button">{group[0].shipment}</button>,
  quantity: group.reduce((sum, row) => sum + row.quantity, 0),
  carrier: group[0].carrier,
} : null;
const props = { rows, columns, rowKey: (row: Row) => row.id, groupBy, groupParent, groupCells, groupSort: true };

afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });

it("renders a single parent per project with aligned subgroup cells and direct rows without a shipment", () => {
  const { container } = render(<DataTable {...props} groupParentLabel={(project) => <strong>{project}</strong>} />);
  const body = container.querySelector("tbody")!;
  expect([...body.querySelectorAll(".dt-parent-row")].map((row) => row.textContent)).toEqual(["663К.1", "700К.1"]);
  expect([...body.children].map((row) => row.textContent)).toEqual([
    "663К.1", "30ОТГ-2СДЭК", "0220", "0110", "5ОТГ-1Деловые линии", "035", "047Самовывоз",
    "700К.1", "3ОТГ-3ПЭК", "013",
  ]);
  const group = body.querySelector(".dt-group-cells")!;
  expect(within(group as HTMLElement).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["", "30", "ОТГ-2", "СДЭК"]);
  expect(group.querySelectorAll("td")[1]).toHaveClass("num");
  expect(body.querySelectorAll(".dt-group-cells")).toHaveLength(3);
  expect(body.querySelector(".dt-parent-row td")).toHaveAttribute("colspan", "5");
});

it("sorts only child rows inside each subgroup while preserving parent and shipment order", () => {
  const { container } = render(<DataTable {...props} />);
  const ids = () => [...container.querySelectorAll("tbody tr:not(.group-row) td:first-child")].map((cell) => cell.textContent);
  fireEvent.click(screen.getByRole("button", { name: "Количество" }));
  expect(ids()).toEqual(["01", "02", "03", "04", "01"]);
  fireEvent.click(screen.getByRole("button", { name: "Количество ▲" }));
  expect(ids()).toEqual(["02", "01", "03", "04", "01"]);
  expect([...container.querySelectorAll(".dt-parent-row")].map((row) => row.textContent)).toEqual(["663К.1", "700К.1"]);
});

it("keeps identically named subgroups independent under different parents", () => {
  const records = rows.map((row) => row.id === "b1" ? { ...row, shipment: "ОТГ-2" } : row);
  const { container } = render(<DataTable {...props} rows={records} groupBy={(row) => row.shipment} />);
  fireEvent.click(screen.getByRole("button", { name: "Количество" }));
  expect([...container.querySelectorAll(".dt-group-cells")].map((row) => row.textContent)).toEqual([
    "30ОТГ-2СДЭК", "5ОТГ-1Деловые линии", "3ОТГ-2ПЭК",
  ]);
});

it("gives parent and subgroup callbacks filtered data and matches shipment values despite blank child cells", () => {
  const parentLabel = vi.fn((project: string, _parent: Row[], _filtered: Row[]) => project);
  const cells = vi.fn(groupCells);
  const { container } = render(<DataTable {...props} groupParentLabel={parentLabel} groupCells={cells} />);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "ОТГ-2" } });
  expect(container.querySelectorAll("tbody tr:not(.group-row)")).toHaveLength(2);
  expect(container.querySelectorAll(".dt-parent-row")).toHaveLength(1);
  expect(parentLabel).toHaveBeenLastCalledWith("663К.1", [rows[0], rows[2]], [rows[0], rows[2]]);
  expect(cells).toHaveBeenLastCalledWith(groupBy(rows[0]), [rows[0], rows[2]], [rows[0], rows[2]], columns.filter((column) => !column.hidden));
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Фильтры" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Фильтр: ТК" }), { target: { value: "ПЭК" } });
  expect(parentLabel).toHaveBeenLastCalledWith("700К.1", [rows[1]], [rows[1]]);
});

it("keeps parent headers without a checkbox and group selection scoped to that shipment", async () => {
  const run = vi.fn();
  const { container } = render(<DataTable {...props} bulkActions={[{ label: "Обработать", run }]} />);
  expect(container.querySelector(".dt-parent-row input")).toBeNull();
  expect(container.querySelector(".dt-parent-row td")).toHaveAttribute("colspan", "6");
  const [shipment] = screen.getAllByRole("checkbox", { name: "Выбрать группу" });
  fireEvent.click(shipment);
  expect(screen.getByRole("checkbox", { name: "Выбрать a1" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Выбрать a2" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Выбрать a3" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("checkbox", { name: "Выбрать a1" }));
  expect((shipment as HTMLInputElement).indeterminate).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Обработать" }));
  expect(run).toHaveBeenCalledWith([rows[0]]);
  await act(async () => {});
});

it("aligns group cells with a saved visible column order including filler cells", () => {
  localStorage.setItem("erp-table:tree", JSON.stringify({ order: ["kb", "carrier", "quantity", "shipment", "project"], hidden: ["project", "shipment"] }));
  const { container } = render(<DataTable {...props} storageKey="tree" columns={columns.map((column) => ({ ...column, width: 80 }))} />);
  const group = container.querySelector(".dt-group-cells")!;
  expect([...group.querySelectorAll("td")].map((cell) => cell.textContent)).toEqual(["", "СДЭК", "30", ""]);
  expect(group.querySelector("td:last-child")).toHaveClass("dt-fill");
  expect(container.querySelector(".dt-parent-row td")).toHaveAttribute("colspan", "4");
});

it("exports child records with raw shipment and project values without synthetic header records", async () => {
  let blob: Blob | undefined;
  vi.spyOn(URL, "createObjectURL").mockImplementation((value) => { blob = value as Blob; return "blob:tree"; });
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  render(<DataTable {...props} exportName="Отправки" />);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "ОТГ-2" } });
  fireEvent.click(screen.getByRole("button", { name: "Excel" }));
  const text = new TextDecoder("utf-16le").decode(await blob!.arrayBuffer());
  expect(text).toBe("КБ\tКоличество\tОтправка\tТК\tПроект\r\n02\t20\tОТГ-2\tСДЭК\t663К.1\r\n01\t10\tОТГ-2\tСДЭК\t663К.1\r\n");
});

it("optional group checkbox label, the group's own selection in its block and a bulk button with live text and hint", () => {
  const block = vi.fn((_name: string, group: Row[], _filtered: Row[], fold: { selected: Row[] }) => group[0].shipment
    ? { head: <span>{group[0].shipment}: выбрано {fold.selected.length} из {group.length}</span> } : null);
  render(<DataTable {...props} groupCells={undefined} groupBlock={block}
    groupSelectLabel={(_name, group) => `Выбрать отправку ${group[0].shipment || "без отправки"}`}
    bulkActions={[{ label: "Отгрузить выбранные", run: vi.fn(), text: (selected) => `Отгрузить выбранные (${selected.length})`,
      hint: (selected) => selected.length > 1 ? "Только по одной" : undefined, disabled: (selected) => selected.length > 1 }]} />);
  fireEvent.click(screen.getByRole("checkbox", { name: "Выбрать отправку ОТГ-2" }));
  expect(screen.getByText("ОТГ-2: выбрано 2 из 2")).toBeInTheDocument();
  expect(screen.getByText("ОТГ-1: выбрано 0 из 1")).toBeInTheDocument();
  const button = screen.getByRole("button", { name: "Отгрузить выбранные (2)" });
  expect(button).toBeDisabled();
  expect(button).toHaveAttribute("title", "Только по одной");
});

it("without the new options the group checkbox and the bulk button keep their old label", () => {
  render(<DataTable {...props} bulkActions={[{ label: "Сделать", run: vi.fn() }]} />);
  fireEvent.click(screen.getAllByRole("checkbox", { name: "Выбрать группу" })[0]);
  expect(screen.getByRole("button", { name: "Сделать" })).not.toHaveAttribute("title");
});
