import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Shipment } from "./api";
import { forgetPersonalViews } from "./personalViews";
import { setRightsPrincipal } from "./rights";
import { JournalWindow, journalRows } from "./ShipmentJournal";

const shipment: Shipment = {
  id: "s1", number: "ОТГ-1", version: 1, created_at: "2026-11-01T12:00:00", shipped_at: "2026-11-10", deadline: "2026-11-08",
  status: "Отгружена", customer: "Заказчик", receiver: "Получатель", shipping_point: "Тула", phone: "+7 123", contact: "Контакт",
  terms: "ДОСТАВКА ТК", carrier: "СДЭК", track_number: "12345", upd_type: "ЭДО", upd_number: "5", upd_date: "2026-11-01", upd_url: "/file",
  pl_number: "PL-1", dn_number: "DN-1", packed_at: "2026-11-01", manager: "Менеджер", note: "Примечание", tk_note: "Осторожно", places: 1, weight: "7.75",
  boxes: [{ place: 1, kind: "Короб", dims: "20×30×40", net: "2", gross: "3", seal: "Пломба-77", items: [{ item_index: "КБ1", quantity: "2" }] }],
  lines: [
    { id: "l1", good_id: "g1", project: "100К.1", item_index: "КБ1", article: "АРТ-1", name: "Жгут", requested: "4", picked: "2", shortage: "2", unit: "шт", grn: "ГП-1", grns: ["ГП-1", "ГП-2"] },
    { id: "l2", good_id: "g2", project: "100К.1", item_index: "КБ2", article: "АРТ-2", name: "Кабель", requested: "5", picked: "5", shortage: "0", unit: "м", grn: "ГП-3" },
  ], inherited: [], missing_data: [], missing_ship: [],
};

beforeEach(() => {
  localStorage.clear(); forgetPersonalViews(); setRightsPrincipal(null);
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}")));
});
afterEach(() => { cleanup(); setRightsPrincipal(null); vi.unstubAllGlobals(); });

const positions = () => screen.getAllByRole("row").filter((row) => row.querySelector('td[data-label="КБ"]'));
const cell = (row: HTMLElement, title: string) => row.querySelector(`td[data-label="${title}"]`)!;
const show = (shipments = [shipment]) => render(<JournalWindow shipments={shipments} close={() => undefined} />);

it("shows one immediately visible row per position with separate shipping values and the normal Excel export", () => {
  show();
  const rows = positions();
  expect(rows).toHaveLength(2);
  expect(cell(rows[0], "КБ")).toHaveTextContent("КБ1");
  expect(cell(rows[1], "КБ")).toHaveTextContent("КБ2");
  for (const [label, text] of Object.entries({ Получатель: "Получатель", Телефон: "+7 123", Адрес: "Тула", ТК: "СДЭК", Трек: "12345", "№ УПД": "5", PL: "PL-1", DN: "DN-1" })) {
    expect(cell(rows[0], label).textContent).toBe(text);
    expect(cell(rows[0], label).querySelector("small")).toBeNull();
  }
  expect(cell(rows[0], "Отгружено").textContent).toBe("2");
  expect(cell(rows[0], "Недогруз").textContent).toBe("2");
  expect(rows.every((row) => !row.hasAttribute("aria-expanded"))).toBe(true);
  expect(screen.queryByRole("button", { name: /раскрыть|Excel по позициям/i })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Excel" })).toBeInTheDocument();
  expect(within(rows[0]).getByRole("button", { name: "Открыть 100К.1 КБ1" })).toBeInTheDocument();
});

it("filters partial shipments per position and keeps separate waiting and shipped presets", () => {
  const waiting = { ...shipment, id: "s2", number: "ОТГ-2", status: "Упакована", shipped_at: null,
    lines: [{ ...shipment.lines[0], id: "wait", item_index: "КБ3" }] };
  show([shipment, waiting]);
  fireEvent.click(screen.getByRole("button", { name: /Частичные/ }));
  expect(positions()).toHaveLength(1);
  expect(cell(positions()[0], "КБ")).toHaveTextContent("КБ1");
  fireEvent.click(screen.getByRole("button", { name: /Не уехали/ }));
  expect(positions()).toHaveLength(1);
  expect(cell(positions()[0], "КБ")).toHaveTextContent("КБ3");
  expect(cell(positions()[0], "Отгружено").textContent).toBe("—");
  fireEvent.click(screen.getByRole("button", { name: /Отгружены/ }));
  expect(positions()).toHaveLength(2);
});

it("keeps newest groups first and chronological sequence after search and preset filtering", () => {
  const latest = { ...shipment, id: "s2", number: "ОТГ-2", shipped_at: "2026-11-12", lines: [shipment.lines[1]], note: "Особая заметка" };
  show([shipment, latest]);
  expect(cell(positions()[0], "Отправка")).toHaveTextContent("ОТГ-2");
  expect(cell(positions()[0], "Отгрузка")).toHaveTextContent("2 из 2");
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Особая заметка" } });
  expect(positions()).toHaveLength(1);
  expect(cell(positions()[0], "Отгрузка")).toHaveTextContent("2 из 2");
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Пломба-77" } });
  expect(positions()).toHaveLength(3);
  fireEvent.click(screen.getByRole("button", { name: /Частичные/ }));
  expect(positions()).toHaveLength(1);
  expect(cell(positions()[0], "Отгрузка")).toHaveTextContent("1 из 2");
});

it("searches a position article without retaining its sibling position", () => {
  show();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "АРТ-1" } });
  expect(positions()).toHaveLength(1);
  expect(cell(positions()[0], "КБ")).toHaveTextContent("КБ1");
});

it("shows missing required tracking only in its own cell and keeps all shipping columns visible by default", () => {
  show([{ ...shipment, track_number: "" }]);
  expect(cell(positions()[0], "Трек")).toHaveTextContent("не заполнен");
  expect(cell(positions()[0], "Трек").querySelector(".tone-warn")).not.toBeNull();
  const headers = screen.getAllByRole("columnheader").map((header) => header.textContent);
  for (const title of ["Создана", "Срок", "Отгружена", "Способ доставки", "Получатель", "Телефон", "Адрес", "УПД", "№ УПД", "Дата УПД", "Файл УПД", "PL", "DN"]) {
    expect(headers).toContain(title);
  }
});

it("opens shipment data from its number with no editing or history correction authority", () => {
  show();
  fireEvent.click(within(positions()[0]).getByRole("button", { name: "Данные отправки ОТГ-1" }));
  const data = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-1" }));
  expect(data.getByRole("textbox", { name: "№ УПД" })).toHaveAttribute("readonly");
  expect(data.queryByRole("button", { name: /Сохранить|Исправить историю/ })).not.toBeInTheDocument();
});

it("retains empty selected shipment identity and status without inventing a position", () => {
  const empty = { ...shipment, status: "Отменена", lines: [] };
  render(<JournalWindow shipments={[empty]} selectedShipment={empty} close={() => undefined} />);
  expect(screen.getByText(empty.number)).toBeInTheDocument();
  expect(screen.getByText("Отменена", { selector: ".status-chip" })).toBeInTheDocument();
  expect(screen.getByText(/В отгрузке нет позиций/)).toBeInTheDocument();
  expect(screen.queryByText("КБ1")).not.toBeInTheDocument();
});

it("disables incomplete active DN and respects print permissions", () => {
  const active = { ...shipment, status: "Упакована", shipped_at: null, missing_data: ["файл УПД"] };
  const rendered = show([active]);
  expect(within(positions()[0]).getByRole("button", { name: "Печать DN DN-1" })).toBeDisabled();
  expect(within(positions()[0]).getByRole("button", { name: "Печать PL PL-1" })).toBeEnabled();
  rendered.unmount();
  setRightsPrincipal({ user: { id: "u", name: "Склад", email: "u@test", department: "Склад" }, roles: [], permissions: { shipping_goods: "view" } });
  show();
  expect(screen.queryByRole("button", { name: /Печать/ })).not.toBeInTheDocument();
  expect(cell(positions()[0], "PL")).toHaveTextContent("PL-1");
});

it("keeps journal row quantities and box totals without counting unshipped goods as shipped", () => {
  const [partial, complete] = journalRows([shipment]);
  expect(partial).toMatchObject({ project: "100К.1", ordered: 4, shipped: 2, partial: true, weight: "3", dims: "20×30×40" });
  expect(complete).toMatchObject({ ordered: 5, shipped: 5, partial: false });
  expect(journalRows([{ ...shipment, status: "Упакована", shipped_at: null }])[0]).toMatchObject({ shipped: "", partial: false });
});
