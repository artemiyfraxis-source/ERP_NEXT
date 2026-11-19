import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { MachineRow, Ob099MachineCard } from "./machinesApi";
import { MachinesPage } from "./MachinesPage";
import { Ob099CardView } from "./Ob099Card";

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

const PRESS: MachineRow = {
  key: "machine:u99", kind: "machine", equipment_id: "u99", inventory: "ОБ.099.1",
  name: "Пресс сервоприводный для опрессовки контактов", pc: "DESKTOP-K6M7O0U", ip: "192.168.1.82",
  service: "Агент ERP · станок", version: "1.0.0", link: { code: "ok", text: "На связи", tone: "ok" },
  last_seen: "2026-11-19T10:00:00+03:00", device: "ПЛК пресса", state: { text: "Работа", tone: "ok" }, card: true, others: "",
  article: "BZW-SF4040T", site: "ЦАР", operations: ["Опрессовка терминалов"], family: "Опрессовка", controlled: true,
};

function card(overrides: Partial<Ob099MachineCard> = {}): Ob099MachineCard {
  return {
    kind: "ob099_fins", equipment_id: "u99", inventory: "ОБ.099.1", name: PRESS.name, pc: PRESS.pc, ip: PRESS.ip,
    service: PRESS.service, service_version: "FINS/UDP", bridge_version: "1.0.0",
    link: { code: "ok", text: "На связи", tone: "ok" }, reported_at: "2026-11-19T10:00:00+03:00", age_seconds: 2, error: "",
    live: {
      state: { text: "Работа", tone: "ok" },
      alarms: [{ code: "W83.06", text: "Ресурс ножа отсечки отходов исчерпан", tone: "danger" }],
      job: { product: "61117-4", wire: "A", terminal: "61117-4", applicator: "A-12", equipment_no: "260122-A2" },
      crimp: { height_mm: 2.5, tolerance_mm: 0.05, height_min_mm: 0.5, height_max_mm: 4, cfm_on: false, work_mode: true },
      counters: { total_set: 1400, batch_set: 700, total_done: 156, batch_done: 156 },
      tools: [
        { name: "Нож обжима", life: 99999, used: 3406, left: 96593, used_pct: 3.4, tone: "ok" },
        { name: "Нож отсечки отходов", life: 99999, used: 99999, left: 0, used_pct: 100, tone: "danger" },
      ],
    },
    today: {
      day: "2026-11-19", total: 52, ok: 44, ng: 8, ng_pct: 15.4,
      ng_reasons: [{ text: "Отклонение кривой усилия (CFM)", count: 8 }],
      last: { at: "2026-11-19T09:58:12+03:00", result: "NG", peak: 611, terminal: "61117-4", applicator: "A-12" },
      alarms: [{ text: "Брак обжима (CFM)", count: 8, last_at: "2026-11-19T09:58:13+03:00" }],
    },
    ...overrides,
  };
}

afterEach(() => { vi.restoreAllMocks(); });

it("shows the press state, job, crimp height, blade wear and the day's crimps with NG reasons and alarms", () => {
  render(<Ob099CardView card={card()} />);
  expect(screen.getByRole("list", { name: "Аварии сейчас" }).textContent).toContain("Ресурс ножа отсечки отходов исчерпан");
  const press = screen.getByRole("region", { name: "Пресс" });
  expect(within(press).getByText("Выключен").className).toContain("tone-warn");  // CFM off: NG goes unseen
  expect(within(press).getByText("156 из 700")).toBeTruthy();
  const job = screen.getByRole("region", { name: "Задание" });
  expect(within(job).getByText("2,500 мм ± 0,050")).toBeTruthy();
  expect(within(job).getByText("A-12")).toBeTruthy();
  const blades = screen.getByRole("region", { name: "Ресурс ножей" });
  expect(within(blades).getByText("3,4 %").className).toContain("tone-ok");
  expect(within(blades).getByText("100,0 %").className).toContain("tone-danger");
  const crimps = screen.getByRole("region", { name: "Обжимы за 19.11" });
  expect(within(crimps).getByText("8 · 15,4 %").className).toContain("tone-danger");
  expect(within(crimps).getByText(/^\d\d\.11 \d\d:58 · брак · пик 611$/)).toBeTruthy();
  expect(within(screen.getByRole("region", { name: "Причины брака за 19.11" })).getByText("Отклонение кривой усилия (CFM)")).toBeTruthy();
  expect(within(screen.getByRole("region", { name: "Аварии за 19.11" })).getByText(/^8 · последняя \d\d\.11 \d\d:58$/)).toBeTruthy();
});

it("says there was no NG and no alarm on a quiet day, and greys the last words when the PC is gone", () => {
  const { container } = render(<Ob099CardView card={card({
    link: { code: "stale", text: "Нет связи с ПК станка", tone: "danger" }, age_seconds: 75,
    today: { day: "2026-11-19", total: 0, ok: 0, ng: 0, ng_pct: null, ng_reasons: [], last: null, alarms: [] } })} />);
  expect(screen.getByText("Брака нет")).toBeTruthy();
  expect(screen.getByText("Аварий нет")).toBeTruthy();
  expect(screen.queryByRole("list", { name: "Аварии сейчас" })).toBeNull();
  expect(container.querySelector(".machine-old")).not.toBeNull();
  expect(screen.getByText(/1 мин назад/)).toBeTruthy();
});

it("opens the press card from «Оборудование»", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
    String(input).endsWith("/u99") ? json(card()) : json({ now: "2026-11-19T10:00:02+03:00", rows: [PRESS] }));
  render(<MachinesPage />);
  fireEvent.click(within(await screen.findByRole("region", { name: "Опрессовка" })).getByText("ОБ.099.1"));
  const dialog = await screen.findByRole("dialog", { name: /ОБ\.099\.1/ });
  expect(await within(dialog).findByText("2,500 мм ± 0,050")).toBeTruthy();
});
