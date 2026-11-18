import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { AccessMode, Principal } from "./api";
import type { MachineCard, MachineRow } from "./machinesApi";
import { MachinesPage } from "./MachinesPage";
import { visibleNav } from "./navigation";
import { Ob019CardView } from "./Ob019Card";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const OB019: MachineRow = {
  key: "machine:u19", kind: "machine", equipment_id: "u19", inventory: "ОБ.019",
  name: "Автомат резки и зачистки провода с лазерной маркировкой", pc: "PC-108", ip: "192.168.1.108",
  service: "Мост ERP", version: "1.0.0", link: { code: "ok", text: "На связи", tone: "ok" },
  last_seen: "2026-11-18T10:00:00+03:00", device: "ПЛК линии резки", state: { text: "Аварийный стоп", tone: "danger" }, card: true,
};
const GODEX: MachineRow = {
  key: "printer:pc:godex", kind: "printer", equipment_id: "u101", inventory: "ОБ.101", name: "Godex EZ2350i", pc: "Цех",
  ip: "192.168.1.50", service: "Агент печати", version: "2.9.1",
  link: { code: "stale", text: "Нет связи с ПК", tone: "danger" }, last_seen: null, device: "Godex EZ2350i",
  state: { text: "Принтер не найден", tone: "warn" }, card: false,
};

function card(overrides: Partial<MachineCard> = {}): MachineCard {
  return {
    equipment_id: "u19", inventory: "ОБ.019", name: OB019.name, pc: "PC-108", ip: "192.168.1.108",
    service: "Мост ERP", service_version: "mbd 1", bridge_version: "1.0.0",
    link: { code: "ok", text: "На связи", tone: "ok" }, reported_at: "2026-11-18T10:00:00+03:00", age_seconds: 2, error: "",
    live: {
      state: { code: 105, text: "Аварийный стоп", tone: "danger" }, alarm: { code: 0, text: "Аварий нет", tone: "neutral" },
      mode: { code: 2, text: "Автомат" },
      job: { total_set: 300, total_finished: 12, batch_set: 10, batch_finished: 2, uph: 3621, program: 3, order_quantity: null,
        order_finished: null, order_number: "ED F", product: null, order_running: true },
      wire: { length_mm: 325, strip_front_mm: 5, pull_front_mm: 0, strip_back_mm: 5, pull_back_mm: 0, diameter_mm: 0.35, knife_back_mm: 0.1 },
    },
    ...overrides,
  };
}

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

it("lists connected equipment one value per column and opens the live card of ОБ.019 only", async () => {
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    calls.push(String(input));
    return String(input).endsWith("/u19") ? json(card()) : json({ now: "2026-11-18T10:00:02+03:00", rows: [OB019, GODEX] });
  });
  render(<MachinesPage />);
  const table = await screen.findByRole("table");
  for (const title of ["ОБ", "Название", "ПК", "IP", "Служба", "Версия", "Связь", "Последний сигнал", "Устройство", "Состояние"]) {
    expect(within(table).getByRole("columnheader", { name: new RegExp(`^${title}`) })).toBeTruthy();
  }
  expect(within(table).getByText("Аварийный стоп").className).toContain("tone-danger");
  expect(within(table).getByText("Нет связи с ПК").className).toContain("tone-danger");
  fireEvent.click(within(table).getByText("ОБ.101"));
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(within(table).getByText("ОБ.019"));
  const dialog = await screen.findByRole("dialog", { name: /ОБ\.019/ });
  expect(await within(dialog).findByText("325,00 мм")).toBeTruthy();
  const estop = within(dialog).getAllByText("Аварийный стоп");
  expect(estop).toHaveLength(2);  // big in the head line and in «Станок»
  for (const item of estop) expect(item.className).toContain("tone-danger");
  expect(within(dialog).getByText("3 621 шт/ч")).toBeTruthy();
  expect(calls).toContain("/api/production/machines/u19");
});

it("polls every 3 seconds only while the tab is visible", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ now: "", rows: [OB019] }));
  render(<MachinesPage />);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(fetch).toHaveBeenCalledTimes(2);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  document.dispatchEvent(new Event("visibilitychange"));
  await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
  expect(fetch).toHaveBeenCalledTimes(2);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  document.dispatchEvent(new Event("visibilitychange"));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
});

it("shows an honest error and the empty list text", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ now: "", rows: [] }));
  render(<MachinesPage />);
  expect(await screen.findByText(/Ничего не подключено/)).toBeTruthy();
});

it("keeps the last PLC values greyed when the machine PC is gone and says so", () => {
  const { container } = render(<Ob019CardView card={card({
    link: { code: "stale", text: "Нет связи с ПК станка", tone: "danger" }, age_seconds: 75 })} />);
  expect(screen.getByText("Нет связи с ПК станка").closest("p")?.className).toContain("tone-danger");
  expect(screen.getByText(/1 мин назад/)).toBeTruthy();
  expect(container.querySelector(".machine-old")).not.toBeNull();
});

it("says the port is busy while PXS_HMI holds COM8 and there are no PLC words", () => {
  render(<Ob019CardView card={card({ link: { code: "port_busy", text: "Пульт PXS_HMI открыт — порт занят", tone: "warn" },
    error: "COM8 занят", live: null })} />);
  expect(screen.getByText("Пульт PXS_HMI открыт — порт занят")).toBeTruthy();
  expect(screen.getByText("COM8 занят")).toBeTruthy();
  expect(screen.getByText(/Нет данных ПЛК/)).toBeTruthy();
});

it("opens «Станки» for Производство or Задания view", () => {
  const user = { id: "1", email: "u@example.test", name: "Сотрудник", department: "Цех" };
  const as = (permissions: Record<string, AccessMode>): Principal => ({ user, roles: ["operator"], access_level: "operator", permissions });
  expect(visibleNav(as({ myops: "view" })).some((item) => item.id === "machines")).toBe(true);
  expect(visibleNav(as({ production: "view" })).some((item) => item.id === "machines")).toBe(true);
  expect(visibleNav(as({ warehouse: "edit" })).some((item) => item.id === "machines")).toBe(false);
});
