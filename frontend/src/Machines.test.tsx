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
  service: "Агент ERP · станок", version: "1.0.0", link: { code: "ok", text: "На связи", tone: "ok" },
  last_seen: "2026-11-18T10:00:00+03:00", device: "ПЛК линии резки", state: { text: "Аварийный стоп", tone: "danger" }, card: true,
  others: "",
};
const GODEX: MachineRow = {
  key: "printer:pc:godex", kind: "printer", equipment_id: "u101", inventory: "ОБ.101", name: "Godex EZ2350i", pc: "Цех",
  ip: "192.168.1.50", service: "Агент ERP · печать", version: "2.9.1",
  link: { code: "stale", text: "Нет связи с ПК", tone: "danger" }, last_seen: null, device: "Godex EZ2350i",
  state: { text: "Принтер не найден", tone: "warn" }, card: false, others: "",
};
const PLANAR: MachineRow = {
  key: "device:instrument:si2", kind: "device", equipment_id: null, inventory: "СИ.002", name: "Planar S50180", pc: "DESKTOP-PTA57QF",
  ip: "192.168.1.121", service: "Агент ERP · печать", version: "2.9.4", link: { code: "ok", text: "На связи", tone: "ok" },
  last_seen: "2026-11-18T10:00:00+03:00", device: "Planar, S50180, 26030095, 26.2.2/2", state: { text: "Подключён", tone: "ok" },
  card: false, others: "DESKTOP-5O46RH6 — не найден",
};

function card(overrides: Partial<MachineCard> = {}): MachineCard {
  return {
    equipment_id: "u19", inventory: "ОБ.019", name: OB019.name, pc: "PC-108", ip: "192.168.1.108",
    service: "Агент ERP · станок", service_version: "mbd 1", bridge_version: "1.0.0",
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

const NOT_INSTALLED: MachineRow = {
  key: "machine:u37", kind: "machine", equipment_id: null, inventory: "ОБ.037", name: "Автомат резки BZW-880", pc: "", ip: "",
  service: "Агент ERP · станок", version: "", link: { code: "not_installed", text: "Агент не установлен", tone: "neutral" },
  last_seen: null, device: "ПЛК линии резки", state: { text: "—", tone: "neutral" }, card: false, others: "",
};

function machines(calls: string[] = []) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = decodeURIComponent(String(input));
    calls.push(url);
    if (url.endsWith("/u19")) return json(card());
    if (url.endsWith("/control")) return json({ problem: "", state: 105, commands: [], offsets: [], can_edit: false });
    if (url.endsWith("/transfer")) return json({ choices: [] });
    if (url.endsWith("/card/ОБ.019")) return json({ inventory: "ОБ.019", settings: [{ title: "Провод и зачистка", fields: [
      { label: "Длина провода", value: 325, unit: "мм", read: true, key: "length_mm", kind: "u32" }] }] });
    return json({ now: "2026-11-18T10:00:02+03:00", rows: [OB019, NOT_INSTALLED, GODEX, PLANAR] });
  });
}

it("shows connected equipment as tiles in groups and opens the live card of ОБ.019", async () => {
  const calls: string[] = [];
  machines(calls);
  render(<MachinesPage />);
  const tools = await screen.findByRole("region", { name: "Станки" });
  const printers = screen.getByRole("region", { name: "Принтеры" });
  const devices = screen.getByRole("region", { name: "Приборы и сканеры" });
  expect(within(tools).getByText("ОБ.019")).toBeTruthy();
  expect(within(tools).getByText("ОБ.037")).toBeTruthy();
  expect(within(printers).getByText("ОБ.101")).toBeTruthy();
  // One analyser — one tile with its СИ number; the other PC that reports it is in its details, not a second tile.
  expect(within(devices).getAllByText("СИ.002")).toHaveLength(1);
  expect(screen.queryByText("DESKTOP-5O46RH6 — не найден")).toBeNull();
  expect(within(tools).getByText("Аварийный стоп").className).toContain("tone-danger");
  expect(within(printers).getByText("Нет связи с ПК").className).toContain("tone-danger");
  expect(within(tools).getByText("PC-108 · 192.168.1.108")).toBeTruthy();
  // A machine without the agent is muted but still opens its details.
  const absent = within(tools).getByText("ОБ.037").closest("button")!;
  expect(absent.className).toContain("is-absent");
  fireEvent.click(absent);
  const info = await screen.findByRole("dialog", { name: /ОБ\.037/ });
  expect(within(info).getByText(/Агент ERP на ПК станка не установлен/)).toBeTruthy();
  fireEvent.click(within(info).getByRole("button", { name: "Закрыть" }));
  fireEvent.click(within(devices).getByText("СИ.002"));
  expect(within(await screen.findByRole("dialog", { name: /СИ\.002/ })).getByText("DESKTOP-5O46RH6 — не найден")).toBeTruthy();
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Закрыть" }));
  expect(calls.some((url) => url.endsWith("/u101"))).toBe(false);

  fireEvent.click(within(tools).getByText("ОБ.019"));
  const dialog = await screen.findByRole("dialog", { name: /ОБ\.019/ });
  expect(await within(dialog).findByText("на станке 325,00 мм")).toBeTruthy();
  const estop = within(dialog).getByText("Аварийный стоп");
  expect(estop.className).toContain("tone-danger");
  expect(within(dialog).getByText("3 621 шт/ч")).toBeTruthy();
  expect(calls).toContain("/api/production/machines/u19");
});

it("searches tiles by number, name, PC or IP", async () => {
  machines();
  render(<MachinesPage />);
  const search = await screen.findByRole("searchbox", { name: "Поиск станков" });
  fireEvent.change(search, { target: { value: "pc-108" } });
  expect(screen.getByText("ОБ.019")).toBeTruthy();
  expect(screen.queryByText("ОБ.101")).toBeNull();
  expect(screen.queryByRole("region", { name: "Принтеры" })).toBeNull();
  fireEvent.change(search, { target: { value: "нет такого" } });
  expect(screen.getByText("Ничего не найдено по «нет такого»")).toBeTruthy();
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
  render(<Ob019CardView card={card({ link: { code: "port_busy", text: "Открыт пульт станка на ПК — запись из ERP недоступна", tone: "warn" },
    error: "COM8 занят", live: null })} />);
  expect(screen.getByText("Открыт пульт станка на ПК — запись из ERP недоступна")).toBeTruthy();
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
