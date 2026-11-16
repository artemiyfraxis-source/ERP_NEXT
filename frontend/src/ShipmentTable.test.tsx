import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Shipment } from "./api";
import type { DataPreset } from "./DataTable";
import { forgetPersonalViews } from "./personalViews";
import { setRightsPrincipal } from "./rights";
import { ShipmentTable } from "./ShipmentTable";

const shipment: Shipment = {
  id: "s1", number: "ОТГ-1", version: 1, created_at: "2026-11-10T12:00:00", shipped_at: "2026-11-02", deadline: "2026-11-30",
  status: "Отгружена", customer: "Заказчик", receiver: "Получатель", shipping_point: "Тула", phone: "+7 123", contact: "Контакт",
  terms: "ДОСТАВКА ТК", carrier: "СДЭК", track_number: "12345", upd_type: "ЭДО", upd_number: "5", upd_date: "2026-11-01", upd_url: "/file",
  pl_number: "PL-1", dn_number: "DN-1", packed_at: "2026-11-01", manager: "Менеджер", note: "Примечание", tk_note: "Осторожно", places: 1, weight: "3",
  boxes: [{ place: 1, kind: "Короб", dims: "20×30×40", net: "2", gross: "3", seal: "Пломба-77", items: [{ item_index: "КБ1", quantity: "2" }] }],
  lines: [
    { id: "l1", good_id: "g1", project: "100К.1", item_index: "КБ1", article: "АРТ-1", name: "Жгут особый", requested: "4", picked: "2", shortage: "2", unit: "шт", grn: "ГП-1", grns: ["ГП-1", "ГП-2"], lots: [{ grn: "ГП-2", quantity: "1", act_id: null }] },
    { id: "l2", good_id: "g2", project: "200К.1", item_index: "КБ2", article: "", name: "Кабель", requested: "5", picked: "5", shortage: "0", unit: "м", grn: "ГП-3" },
  ], inherited: [], missing_data: [], missing_ship: [],
};
beforeEach(() => { localStorage.clear(); forgetPersonalViews(); vi.stubGlobal("fetch", vi.fn(async () => new Response("{}"))); setRightsPrincipal(null); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function table(shipments = [shipment], presets?: DataPreset<Shipment>[]) {
  const open = vi.fn(), error = vi.fn();
  render(<ShipmentTable shipments={shipments} label="Отправки" storageKey="test-ships" onOpenShipment={open} onError={error}
    positions={shipments.flatMap((item) => item.lines)} positionKey={(line) => line.id}
    positionShipment={(line) => shipments.find((item) => item.lines.includes(line))!}
    positionLine={(line) => line} presets={presets}
    positionColumns={[{ key: "kb", title: "КБ", value: (line) => line.item_index }, { key: "name", title: "Наименование", value: (line) => line.name }]} />);
  return { open, error };
}

it("renders one shipment row, all projects and separate unit totals; opens details by keyboard", () => {
  table();
  const rows = screen.getAllByRole("row").filter((row) => row.getAttribute("aria-expanded") !== null);
  expect(rows).toHaveLength(1);
  expect(rows[0]).toHaveTextContent("100К.1"); expect(rows[0]).toHaveTextContent("200К.1");
  expect(rows[0]).toHaveTextContent("2 шт"); expect(rows[0]).toHaveTextContent("5 м");
  expect(rows[0]).toHaveTextContent("02.11.2026"); expect(rows[0]).toHaveTextContent("факт");
  expect(screen.queryByText("Жгут особый")).not.toBeInTheDocument();
  fireEvent.keyDown(rows[0], { key: "Enter" });
  expect(screen.getByText("Жгут особый")).toBeInTheDocument();
  for (const text of ["Запрошено", "Набрано", "Уехало", "Недобор", "ГП-1", "ГП-2", "Пломба-77", "Осторожно"]) expect(screen.getAllByText(text).length).toBeGreaterThan(0);
});

it.each(["Жгут особый", "ГП-2", "Пломба-77", "Примечание", "АРТ-1"])("searches collapsed details: %s", (query) => {
  table();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: query } });
  expect(screen.getByRole("button", { name: "Данные отправки ОТГ-1" })).toBeInTheDocument();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "несуществующий" } });
  expect(screen.queryByRole("button", { name: "Данные отправки ОТГ-1" })).not.toBeInTheDocument();
});

it("opening data does not expand the row and print links respect permissions", () => {
  setRightsPrincipal({ user: { id: "u", name: "Склад", email: "u@test", department: "Склад" }, roles: [], permissions: { shipping_goods: "view" } });
  const { open } = table();
  fireEvent.click(screen.getByRole("button", { name: "Данные отправки ОТГ-1" }));
  expect(open).toHaveBeenCalledWith(shipment);
  expect(screen.queryByText("Жгут особый")).not.toBeInTheDocument();
  expect(screen.queryAllByRole("button", { name: /Печать/ })).toHaveLength(0);
});

it("retains a position view with all lines and Excel", () => {
  table();
  fireEvent.click(screen.getByRole("button", { name: "Excel по позициям" }));
  const view = within(screen.getByRole("dialog", { name: "Отправки — по позициям" }));
  expect(view.getByText("КБ1")).toBeInTheDocument(); expect(view.getByText("КБ2")).toBeInTheDocument();
  expect(view.getByRole("button", { name: "Excel" })).toBeInTheDocument();
});

it("shows empty shipments and uses date order before status", () => {
  table([{ ...shipment, id: "empty", number: "ОТГ-2", lines: [], status: "Новая", shipped_at: null, deadline: "2026-12-01" }, shipment]);
  const rows = screen.getAllByRole("row").filter((row) => row.getAttribute("aria-expanded") !== null);
  expect(rows).toHaveLength(2); expect(rows[0]).toHaveTextContent("ОТГ-2"); expect(rows[0]).toHaveTextContent("план");
});

it.each(["АРТ-1", "ГП-2", "Жгут особый"])("position export inherits detail query and retains only matching lines: %s", (query) => {
  table();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: query } });
  fireEvent.click(screen.getByRole("button", { name: "Excel по позициям" }));
  const view = within(screen.getByRole("dialog", { name: "Отправки — по позициям" }));
  expect(view.getByRole("searchbox")).toHaveValue(query);
  expect(view.getByText("КБ1")).toBeInTheDocument();
  expect(view.queryByText("КБ2")).not.toBeInTheDocument();
});

it.each(["status", "preset"])("position export keeps the current shipment set: %s", (mode) => {
  const complete = { ...shipment, id: "s2", number: "ОТГ-2", status: "Закрыта", lines: [{ ...shipment.lines[1], id: "other", item_index: "КБ3" }] };
  table([shipment, complete], [{ label: "Частичные", test: (item) => item.lines.some((line) => Number(line.picked) < Number(line.requested)) }]);
  if (mode === "status") fireEvent.change(screen.getByRole("combobox", { name: "Статус отправки" }), { target: { value: "Отгружена" } });
  else fireEvent.click(screen.getByRole("button", { name: "Частичные" }));
  fireEvent.click(screen.getByRole("button", { name: "Excel по позициям" }));
  const view = within(screen.getByRole("dialog", { name: "Отправки — по позициям" }));
  expect(view.getByText("КБ1")).toBeInTheDocument();
  expect(view.queryByText("КБ3")).not.toBeInTheDocument();
});

it("blocks incomplete active DN while retaining allowed PL printing", () => {
  table([{ ...shipment, status: "Упакована", missing_data: ["файл УПД"] }]);
  expect(screen.getByRole("button", { name: "Печать DN DN-1" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Печать PL PL-1" })).toBeEnabled();
});

it("retains user-selected column sorts over default date ordering", () => {
  table([shipment, { ...shipment, id: "s2", number: "ОТГ-2", shipped_at: "2026-11-15" }]);
  const rows = () => screen.getAllByRole("row").filter((row) => row.getAttribute("aria-expanded") !== null);
  expect(rows()[0]).toHaveTextContent("ОТГ-2");
  fireEvent.click(screen.getByRole("button", { name: "Отправка" }));
  expect(rows()[0]).toHaveTextContent("ОТГ-1");
});
