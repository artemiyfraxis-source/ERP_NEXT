import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { MachineRow, Ob099MachineCard, UnitSetup } from "./machinesApi";
import { MachinesPage } from "./MachinesPage";
import { Ob099CardView, pressStatus } from "./Ob099Card";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const PRESS: MachineRow = {
  key: "machine:u99", kind: "machine", equipment_id: "u99", inventory: "ОБ.099.1",
  name: "Пресс сервоприводный для опрессовки контактов", pc: "DESKTOP-K6M7O0U", ip: "192.168.1.82",
  service: "Агент ERP · станок", version: "1.0.0", link: { code: "ok", text: "На связи", tone: "ok" },
  last_seen: "2026-11-19T10:00:00+03:00", device: "ПЛК пресса", state: { text: "Работа", tone: "ok" }, card: true, others: "",
  article: "BZW-SF4040T", site: "ЦАР", operations: ["Опрессовка терминалов"], family: "Опрессовка", controlled: true,
};

function card(overrides: Partial<Ob099MachineCard> = {}, live: Partial<NonNullable<Ob099MachineCard["live"]>> = {}): Ob099MachineCard {
  return {
    kind: "ob099_fins", equipment_id: "u99", inventory: "ОБ.099.1", name: PRESS.name, pc: PRESS.pc, ip: PRESS.ip,
    service: PRESS.service, service_version: "FINS/UDP", bridge_version: "1.0.3",
    link: { code: "ok", text: "На связи", tone: "ok" }, reported_at: "2026-11-19T10:00:00+03:00", age_seconds: 2, error: "",
    live: {
      state: { text: "Готов", tone: "ok" },
      alarms: [{ code: "W83.06", text: "Ресурс ножа отсечки отходов исчерпан", tone: "danger" }],
      job: { product: "61117-4", wire: "A", terminal: "61117-4", applicator: "A-12", equipment_no: "260122-A2" },
      crimp: { height_mm: 2.5, tolerance_mm: 0.05, height_min_mm: 0.5, height_max_mm: 4, cfm_on: false, work_mode: true },
      counters: { total_set: 1400, batch_set: 700, total_done: 156, batch_done: 156 },
      tools: [
        { name: "Нож обжима", life: 99999, used: 3406, left: 96593, used_pct: 3.4, tone: "ok" },
        { name: "Нож отсечки отходов", life: 99999, used: 99999, left: 0, used_pct: 100, tone: "danger" },
      ],
      ...live,
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

const SETUP: UnitSetup = {
  unit_id: "u99", inventory: "ОБ.099.1", name: PRESS.name, model: "BZW-SF4040T", requires_setup: { code: "full", label: "полная" },
  reason: "", parameters: [
    { key: "applicator", label: "Аппликатор", unit: "", source: "оснастка", source_field: "", register: "" },
    { key: "crimp_height_mm", label: "Высота обжима жилы", unit: "мм", source: "контакт", source_field: "", register: "" },
  ],
  configuration: null, references: [], open_tasks: [], online: true,
  history: [{ at: "2026-11-18T15:00:00+03:00", by: "Иван", operation: "", reason: "смена аппликатора", values: { crimp_height_mm: "1.15" } }],
};

/** Ответы сервера карточки пресса: наладка, журнал работ; всё прочее — список оборудования. */
function serve(setup: UnitSetup = SETUP, press = card()) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = decodeURIComponent(String(input));
    if (url.includes("/setup-model/")) return json(setup);
    if (url.endsWith("/u99")) return json(press);
    if (url.includes("manual-jobs")) return json({ date_from: "2026-11-13", date_to: "2026-11-19", rows: [], can_edit: false });
    return json({ now: "2026-11-19T10:00:02+03:00", rows: [PRESS] });
  });
}

afterEach(() => { vi.restoreAllMocks(); window.history.replaceState({}, "", "/"); });

it("puts the press state, link age and next step in one strip, counters and blade wear in one line", async () => {
  serve();
  render(<Ob099CardView card={card()} header={{ title: "ОБ.099.1", close: () => undefined }} />);
  const strip = screen.getByRole("status", { name: "Состояние пресса" });
  expect(within(strip).getByText("Готов")).toBeTruthy();
  expect(strip.textContent).toMatch(/На связи · сигнал .*2 с назад/);
  expect(strip.textContent).toContain("Аварийная остановка — кнопкой на прессе");
  expect(screen.getByRole("list", { name: "Предупреждения пресса" }).textContent).toContain("Ресурс ножа отсечки отходов исчерпан");
  const line = screen.getByLabelText("Счётчики и ресурс");
  expect(within(line).getByText("156 из 700 шт")).toBeTruthy();
  expect(within(line).getByText("100,0 % ресурса").className).toContain("tone-danger");
  expect(within(line).getByText("выключен — брак не будет замечен").className).toContain("tone-warn");
  expect(within(line).getByText("52 обжимов · брак 8 (15,4 %)").className).toContain("tone-danger");
  // The header holds only «Журнал» and «Закрыть»: no «Наладка», no «Ручная работа».
  const head = screen.getByRole("heading", { name: "ОБ.099.1" }).parentElement!;
  expect(within(head).getAllByRole("button").map((button) => button.textContent)).toEqual(["Журнал", "Закрыть"]);
  expect(await screen.findByRole("table", { name: "Параметры пресса" })).toBeTruthy();
});

it("names the state, alarm and running work in words with the next step", () => {
  expect(pressStatus(card({}, { state: { text: "Авария", tone: "danger" },
    alarms: [{ code: "W80.03", text: "Брак обжима (CFM)", tone: "danger" }] }))).toMatchObject(
    { title: "Авария: Брак обжима (CFM)", tone: "danger", hint: "Устраните причину и сбросьте аварию на пульте пресса" });
  expect(pressStatus(card({}, { state: { text: "Работа", tone: "ok" } }))).toMatchObject(
    { title: "Обжимает · партия 156 из 700", tone: "neutral", hint: "Идёт обжим — наладку меняйте после остановки" });
  expect(pressStatus(card({}, { state: { text: "Нужен сброс", tone: "warn" } }))).toMatchObject({ tone: "warn", hint: "Сбросьте пресс на его пульте" });
  expect(pressStatus(card(), "Сверьте пресс с ЭТК")).toMatchObject({ title: "Готов", hint: "Сверьте пресс с ЭТК" });
  expect(pressStatus(card())).toMatchObject({ hint: "Пресс готов — можно обжимать" });
  expect(pressStatus(card({ live: null }))).toMatchObject({ title: "Агент на связи, пресс ещё не ответил", tone: "warn" });
  const gone = pressStatus(card({ link: { code: "stale", text: "Нет связи с ПК станка", tone: "danger" }, age_seconds: 75 }));
  expect(gone).toMatchObject({ title: "Нет связи с ПК станка", tone: "danger" });
  expect(gone.link).toMatch(/^Последний сигнал .*1 мин назад$/);
});

it("greys the last press values once when the PC is gone and keeps no alarm list from stale words", async () => {
  serve();
  const { container } = render(<Ob099CardView card={card({ link: { code: "stale", text: "Нет связи с ПК станка", tone: "danger" }, age_seconds: 75 })} />);
  expect(screen.getByText("Нет связи с ПК станка")).toBeTruthy();
  expect(screen.queryByRole("list", { name: "Предупреждения пресса" })).toBeNull();
  expect(container.querySelector(".machine-summary.machine-old")).not.toBeNull();
  const table = await screen.findByRole("table", { name: "Параметры пресса" });
  expect(within(table).getByRole("columnheader", { name: "На прессе · последние" })).toBeTruthy();
  // Stale words never fill «Настроено».
  expect((within(table).getByLabelText("Высота обжима жилы, настроено") as HTMLInputElement).value).toBe("");
});

it("has no «На прессе» column when the press never answered", async () => {
  serve();
  render(<Ob099CardView card={card({ live: null })} />);
  const table = await screen.findByRole("table", { name: "Параметры пресса" });
  expect(within(table).queryByRole("columnheader", { name: /На прессе/ })).toBeNull();
  expect(screen.getByText("Агент на связи, пресс ещё не ответил")).toBeTruthy();
});

it("opens the day's crimps, NG reasons, alarms, setup journal and connection behind «Журнал»", async () => {
  serve();
  render(<Ob099CardView card={card()} header={{ title: "ОБ.099.1", close: () => undefined }} />);
  await screen.findByRole("table", { name: "Параметры пресса" });
  fireEvent.click(screen.getByRole("button", { name: "Журнал" }));
  const dialog = await screen.findByRole("dialog", { name: "Журнал · ОБ.099.1" });
  const crimps = within(dialog).getByRole("region", { name: "Обжимы за 19.11" });
  expect(within(crimps).getByText("8 · 15,4 %").className).toContain("tone-danger");
  expect(within(crimps).getByText(/^\d\d\.11 \d\d:58 · брак · пик 611$/)).toBeTruthy();
  expect(within(within(dialog).getByRole("region", { name: "Причины брака за 19.11" })).getByText("Отклонение кривой усилия (CFM)")).toBeTruthy();
  expect(within(within(dialog).getByRole("region", { name: "Аварии за 19.11" })).getByText(/^8 · последняя \d\d\.11 \d\d:58$/)).toBeTruthy();
  expect(within(dialog).getByRole("region", { name: "Журнал наладки" }).textContent).toContain("без задания: смена аппликатора");
  expect(within(dialog).getByRole("region", { name: "Журнал наладки" }).textContent).toContain("Высота обжима жилы 1,15");
  expect(within(within(dialog).getByRole("region", { name: "Подключение" })).getByText("260122-A2")).toBeTruthy();
});

it("opens the press card from «Оборудование» with the header of «Журнал» and «Закрыть» only", async () => {
  const fetch = serve();
  render(<MachinesPage canWrite />);
  fireEvent.click(within(await screen.findByRole("region", { name: "Опрессовка" })).getByText("ОБ.099.1"));
  const dialog = await screen.findByRole("dialog", { name: /ОБ\.099\.1/ });
  await within(dialog).findByRole("table", { name: "Параметры пресса" });
  const head = within(dialog).getByRole("heading", { level: 2 }).parentElement!;
  expect(within(head).getAllByRole("button").map((button) => button.textContent)).toEqual(["Журнал", "Закрыть"]);
  expect(within(dialog).queryByText(/Ручная работа|Наладка · ждёт/)).toBeNull();
  await waitFor(() => expect(fetch.mock.calls.some(([input]) => decodeURIComponent(String(input)).includes("/setup-model/ОБ.099.1"))).toBe(true));
});

it("incoming instruction context offers the ETK operation as the source, never a load or start command", async () => {
  serve();
  render(<Ob099CardView card={card()} incoming={{ taskId: "t1", operationId: "o1", loadRequested: true, action: {
    etk_position: 3, equipment_id: "u99", inventory_number: "ОБ.099.1", display_name: "Опрессовка", model: null, card_kind: "ob099",
    connectivity: { state: "ok", fresh: true, last_seen: null }, open_permitted: true, load_permitted: false, can_open: true, can_load: false,
    source: null, blocker: "Загрузка задания на пресс не поддерживается" } }} />);
  expect((await screen.findByRole("radio", { name: /Операция ЭТК 3 · из инструкции/ }) as HTMLInputElement).checked).toBe(true);
  expect(screen.queryByRole("button", { name: /Загрузить|Пуск|Записать в пресс/ })).not.toBeInTheDocument();
});
