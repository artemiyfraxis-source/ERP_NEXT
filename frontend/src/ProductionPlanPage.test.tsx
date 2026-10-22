import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProductionPlanPage } from "./ProductionPlanPage";
import {
  MIN_VISIBLE_DAYS, MIN_VISIBLE_WEEKS, autoHiddenColumns, deadlineSpan, groupSheetRows, markText, orderStatuses, periodWords, projectColor, sheetDays,
  sheetMonths, sheetWeeks, startLabels, statusColumnWidth, todayScrollLeft, weekMarks,
} from "./PlanLoadSheet";
import type { Principal, ProductionPlanResult } from "./api";

const principal: Principal = {
  user: { id: "1", email: "master@example.test", name: "Мастер", department: "Производство" },
  roles: ["foreman"],
  access_level: "foreman",
  permissions: { production: "edit" },
};

const cell = (value: string, manual = false, auto = value) => ({ value, auto, manual });
const sheet = {
  comment: cell("ждём разъёмы"), status: cell("СТОП, В РАБОТЕ"), ready: cell("40"), in_plan: cell("да"),
  customer_supplied: cell("Ч/Т"), kit_checked: cell("нет"), kit_present: cell("нет"), zkp: cell("да"),
  kit_arrival: cell("2026-09-28"), tc_ready: cell("да"), test_sb: cell("да", true, "нет"), kit_picked: cell("нет"),
};

const plan: ProductionPlanResult = {
  today: "2026-09-22", calendar_from: "2026-09-15", calendar_to: "2026-11-21",
  shop_statuses: ["ХОЛД", "СТОП", "ПАУЗА", "ПРВ КОМПЛ", "К ЗАПУСК", "В РАБОТЕ", "ОЖИД КОМПЛ", "ОЖИД ИНФ", "ОТК", "НА ОТГР", "ГОТОВО", "ОТГРУЖЕН"],
  supplied_values: ["НЕТ", "ДА", "Ч/Т"], arrival_values: ["Склад", "Ждем", "???"],
  blockers: ["ХОЛД", "СТОП"],
  stages: ["Новый", "ТК готова", "КБ набрана", "В работе", "КБ на складе", "Отгружено"],
  review_values: ["не проверена", "проверена", "с погрешностями"],
  rows: [{
    project: "708К.4", customer: "Заказчик", opened_on: "2026-09-01",
    deadline: "2026-09-30", days_left: 8, work_days_left: 6, stage: "В работе", terminal: false, stage_manual: false,
    kits: [{ item_index: "КБ1", status: "В работе", manual: false, tc: true, kit: false, review: { value: "не проверена", text: "", who: "генератор", occurred_at: "2026-09-22T08:00:00+03:00" } }],
    supply: { total: 5, stock: 2, transit: 1, buying: 1, request: 1, customer_supplied: 1, customer_supplied_stock: 1, eta: "28.09.2026" },
    plan: { tasks: 1, assigned: 2, total: 3, start: "2026-09-22T09:00:00+03:00", finish: "2026-09-23T12:00:00+03:00" },
    blocker: { type: "СТОП", text: "Ждём разъёмы", since: "2026-09-22T09:00:00+03:00", who: "Мастер" },
    last: { occurred_at: "2026-09-22T09:00:00+03:00", who: "Мастер", text: "Ждём разъёмы" },
    events: [{ id: "e1", project: "708К.4", occurred_at: "2026-09-22T09:00:00+03:00", who: "Мастер", kind: "Блокер", blocker: "СТОП", text: "Ждём разъёмы", deadline: null, item_index: "", value: "" }],
    group: "hold", sheet,
    calendar: { work: ["2026-09-22", "2026-09-23"], starts: { "2026-09-22": 10 }, notes: { "2026-09-25": "П" } },
  }],
};

const stagePreview = (over: Record<string, unknown> = {}) => ({
  project: "70К.26.13", value: "Отгружено", kb: "", direction: "forward", reason_required: false, error: "", message: "",
  terminal: "", blocker: "", warnings: [], preview_token: "tok-plan",
  kbs: [{ kb: "КБ1", label: "КБ1", from: "Новый", to: "Отгружено", skipped: [], change: true, note: "", warnings: [] }],
  ...over,
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.restoreAllMocks(); window.localStorage.removeItem("erp-zg-columns"); window.history.replaceState({}, "", "/"); });

function sheetRow(project: string) {
  return document.querySelector(`tr[data-project="${project}"]`) as HTMLTableRowElement;
}

it("shows the shop sheet «ЗАГРУЗ» columns in the sheet order", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => String(input) === "/api/production/calendar" ? json({ holidays: [], absences: [], shift: {} }) : json(plan));
  render(<ProductionPlanPage principal={principal} />);

  expect(await screen.findByRole("heading", { name: "Производственный план" })).toBeInTheDocument();
  await screen.findByRole("button", { name: "708К.4" });
  const heads = Array.from(document.querySelectorAll(".zg-table thead th.zg-col-head")).map((item) => item.textContent);
  expect(heads).toEqual([
    "Проект", "Заказчик", "Статус", "Готовность, %", "В плане", "Давальческое", "Закуплено", "Приход комплектующих", "Комплект на складе", "Комплект проверен", "Техкарта выдана", "Комплект набран", "Тестовая сборка", "Дедлайн", "Рабочих дней до дедлайна", "Комментарий",
  ]);
  // Календарь: сегодня −7 … +60 дней; над днями — строка месяцев, в шапке дня — число и день недели.
  const days = Array.from(document.querySelectorAll(".zg-table thead th.zg-day-head")).map((item) => item.textContent);
  expect(days[0]).toBe("15вт");
  expect(days.length).toBe(68);
  expect(Array.from(document.querySelectorAll(".zg-month-head")).map((item) => [item.textContent, item.getAttribute("colspan")])).toEqual([
    ["сентябрь 2026", "16"], ["октябрь", "31"], ["ноябрь", "21"],
  ]);
  expect(screen.getByText("1", { selector: ".page-count" })).toBeInTheDocument();
  const row = sheetRow("708К.4");
  // Статус — тот же чип проекта, что в «Проектах»: этап со стрелкой меню и активный блокер рядом.
  expect(Array.from(row.querySelectorAll(".zg-col-status .status-chip")).map((item) => item.textContent)).toEqual(["В работе▾", "СТОП"]);
  expect(row.querySelector(".zg-col-status .proj-status-btn")).not.toBeNull();
  expect(row.querySelector(".zg-col-ready")?.textContent).toBe("40%");
  expect(row.querySelector<HTMLElement>(".zg-ready i")?.style.getPropertyValue("--zg-ready")).toBe("40%");
  expect(row.querySelector(".zg-col-kit_arrival")?.textContent).toBe("28.09");
  expect(row.querySelector(".zg-col-days_left")?.className).toContain("ok");
  expect(row.querySelector(".zg-col-days_left")?.textContent).toBe("6");
  expect(row.querySelector(".zg-col-deadline")?.textContent).toBe("30.09");
  expect(row.querySelector(".zg-col-deadline")?.getAttribute("title")).toBe("Дедлайн 30.09.2026 · рабочих дней до дедлайна: 6");
  expect(row.querySelector(".zg-col-test_sb")?.className).toContain("zg-manual");
  expect(screen.getByText("В работе · 1")).toBeInTheDocument();
  // Боковой панели нет: проект открывается в «Ресурсном планировании».
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("writes the sheet header in full words (no abbreviations) and keeps the meaning in tooltips", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => String(input) === "/api/production/calendar" ? json({ holidays: [], absences: [], shift: {} }) : json(plan));
  render(<ProductionPlanPage principal={principal} />);
  await screen.findByRole("button", { name: "708К.4" });

  const heads = Array.from(document.querySelectorAll<HTMLElement>(".zg-table thead th.zg-col-head"));
  // Владелец 14.10: «без сокращений» — подпись в шапке и есть полное название колонки.
  expect(heads.every((head) => head.getAttribute("title")!.split(" — ")[0] === head.textContent)).toBe(true);
  // Все показанные колонки закреплены; последняя отделена от календаря границей с тенью.
  expect(heads.every((head) => head.classList.contains("zg-freeze"))).toBe(true);
  expect(heads.filter((head) => head.classList.contains("zg-edge")).map((head) => head.textContent)).toEqual(["Комментарий"]);
  // Полные названия — словами ERP, а не сокращениями листа (ПП-11).
  expect(heads.map((head) => head.getAttribute("title")?.split(" — ")[0])).toEqual([
    "Проект", "Заказчик", "Статус", "Готовность, %", "В плане", "Давальческое", "Закуплено", "Приход комплектующих", "Комплект на складе", "Комплект проверен", "Техкарта выдана", "Комплект набран", "Тестовая сборка", "Дедлайн", "Рабочих дней до дедлайна", "Комментарий",
  ]);
  expect(heads[4].getAttribute("title")).toBe("В плане — на проект назначены операции");
  expect(heads[9].getAttribute("title")).toBe("Комплект проверен");
  // Ширина таблицы задана колонками, а не содержимым: длинный комментарий её не раздувает.
  expect(document.querySelector<HTMLElement>(".zg-table")!.style.width).toMatch(/^calc\(\d+px \+ 68 \* var\(--zg-day\)\)$/);

  const row = sheetRow("708К.4");
  expect(row.querySelector(".zg-col-comment")?.getAttribute("title")).toBe("ждём разъёмы");
  expect(row.querySelector(".zg-link")?.getAttribute("title")).toBe("Заказчик — журнал проекта");
  expect(row.querySelector(".zg-col-status")?.getAttribute("title")).toBe("СТОП, В работе — СТОП: Ждём разъёмы");
  expect(row.querySelector(".zg-col-kit_checked")?.getAttribute("title")).toBe("Комплект проверен");
  // Сегодня подсвечено и в шапке, и в строках.
  const today = document.querySelector(".zg-table thead th.zg-today");
  expect(today?.getAttribute("title")).toBe("вт 22.09.2026 · сегодня");
  expect(row.querySelectorAll("td.zg-today")).toHaveLength(1);
});

it("shows an empty plan as the calendar grid with one hint row, no sideways scroll and no «0» counter", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ ...plan, rows: [] }));
  render(<ProductionPlanPage principal={principal} />);
  const text = await screen.findByText("Проектов в плане нет — заведите проект в «Проектах».");

  expect(text.closest("td")?.className).toBe("zg-empty");
  expect(text.closest("td")?.getAttribute("colspan")).toBe(String(16 + 68));
  // Шапка календаря на месте: месяцы, 68 дней, сегодня подсвечено.
  expect(document.querySelectorAll(".zg-table thead th.zg-day-head")).toHaveLength(68);
  expect(document.querySelectorAll(".zg-table thead th.zg-month-head")).toHaveLength(3);
  expect(document.querySelector(".zg-table thead th.zg-today")?.textContent).toBe("22вт");
  expect(document.querySelector(".zg-wrap")?.className).toContain("zg-wrap-empty");
  expect(document.querySelector(".page-count")).toBeNull();
});

it("draws yes/no columns as narrow ✓ / — toggles with the full sheet name in the tooltip", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(plan));
  render(<ProductionPlanPage principal={principal} />);
  await screen.findByRole("button", { name: "708К.4" });
  const row = sheetRow("708К.4");

  const flags = Array.from(row.querySelectorAll<HTMLButtonElement>("button.zg-flag"));
  expect(flags.map((flag) => [flag.getAttribute("aria-label")?.split(" · ")[0], flag.textContent, flag.getAttribute("aria-checked")])).toEqual([
    ["В плане", "✓", "true"], ["Закуплено", "✓", "true"], ["Комплект на складе", "—", "false"], ["Комплект проверен", "—", "false"],
    ["Техкарта выдана", "✓", "true"], ["Комплект набран", "—", "false"], ["Тестовая сборка", "✓", "true"],
  ]);
  expect(flags.every((flag) => flag.getAttribute("role") === "checkbox")).toBe(true);
  // Короткая подпись в шапке, полное название и смысл — в подсказке.
  const head = (label: string) => Array.from(document.querySelectorAll(".zg-col-head")).find((item) => item.textContent === label)!;
  expect(head("Тестовая сборка").getAttribute("title")).toBe("Тестовая сборка");
  expect(head("Комплект на складе").getAttribute("title")).toBe("Комплект на складе — все комплектующие на складе");
  expect(head("Закуплено").getAttribute("title")).toBe("Закуплено — авто: все покупные позиции оплачены — в пути или уже пришли (давальческие не в счёт)");
  // «Давал»: «Ч/Т» коротко в ячейке, полные слова — в списке поверх неё.
  expect(row.querySelector(".zg-col-customer_supplied .zg-supplied")?.textContent).toBe("Ч/Т");
  expect(screen.getByLabelText("Давальческое · 708К.4")).toHaveValue("авто");
});

it("lets the viewer choose the sheet columns, keeps the sheet order and returns to the screen-width default", async () => {
  window.localStorage.removeItem("erp-zg-columns");
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(plan));
  render(<ProductionPlanPage principal={principal} />);
  await screen.findByRole("button", { name: "708К.4" });
  const labels = () => Array.from(document.querySelectorAll(".zg-col-head")).map((item) => item.textContent);

  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  const chooser = screen.getByRole("group", { name: "Показывать колонки" });
  expect(chooser.querySelector<HTMLInputElement>("input[disabled]")?.parentElement?.textContent).toBe("Проект");
  fireEvent.click(screen.getByLabelText("Рабочих дней до дедлайна"));
  fireEvent.click(screen.getByLabelText("Закуплено"));
  expect(labels()).toEqual(["Проект", "Заказчик", "Статус", "Готовность, %", "В плане", "Давальческое", "Приход комплектующих", "Комплект на складе", "Комплект проверен", "Техкарта выдана", "Комплект набран", "Тестовая сборка", "Дедлайн", "Комментарий"]);
  expect(screen.getByRole("button", { name: "Колонки · 14 из 16" })).toBeInTheDocument();
  expect(JSON.parse(window.localStorage.getItem("erp-zg-columns")!)).toEqual(["days_left", "zkp"]);
  // «Дн.» скрыта — светофор дедлайна переезжает в «Дедлайн».
  expect(sheetRow("708К.4").querySelector(".zg-col-deadline")?.className).toContain("zg-days ok");
  expect(sheetRow("708К.4").querySelectorAll("td.zg-freeze")).toHaveLength(14);

  fireEvent.click(screen.getByRole("button", { name: "По ширине экрана" }));
  expect(labels()).toHaveLength(16);
  expect(window.localStorage.getItem("erp-zg-columns")).toBeNull();
});

function row(overrides: Partial<ProductionPlanResult["rows"][number]>) {
  return { ...plan.rows[0], blocker: null, events: [], last: null, ...overrides };
}

const board: ProductionPlanResult = {
  ...plan,
  rows: [
    row({ project: "701К.1", customer: "Альфа", days_left: null, deadline: null }),
    row({ project: "702К.1", customer: "Бета", days_left: -2, deadline: "2026-09-20" }),
    row({ project: "703К.1", customer: "Гамма", days_left: 5, deadline: "2026-09-27", blocker: { type: "ХОЛД", text: "оплата", since: "2026-09-21T09:00:00+03:00", who: "Мастер" } }),
    row({ project: "704К.1", customer: "Дельта", days_left: 1, deadline: "2026-09-23", plan: null, stage: "ТК готова" }),
    row({ project: "705К.1", customer: "Эпсилон", terminal: true, stage: "Закрыт" }),
    row({ project: "706К.1", customer: "Зета", stage: "Отгружено" }),
  ],
};

function projects() {
  return Array.from(document.querySelectorAll(".zg-row")).map((item) => item.getAttribute("data-project"));
}

it("groups the sheet by stage in the plan order, overdue first, closed last on demand; collapses per stage; searches", async () => {
  const groupedBoard: ProductionPlanResult = { ...board, rows: [
    row({ project: "701К.1", customer: "Альфа", days_left: null, deadline: null, stage: "В работе" }),
    row({ project: "702К.1", customer: "Бета", days_left: -2, deadline: "2026-09-20", stage: "В работе" }),
    row({ project: "703К.1", customer: "Гамма", days_left: 5, deadline: "2026-09-27", stage: "Новый", blocker: { type: "ХОЛД", text: "Ждём", since: "2026-09-22", who: "Мастер" } }),
    row({ project: "704К.1", customer: "Дельта", days_left: 1, deadline: "2026-09-23", stage: "В работе" }),
    row({ project: "705К.1", customer: "Эпсилон", terminal: true, stage: "Отменён" }),
    row({ project: "706К.1", customer: "Зета", stage: "Отгружено" }),
  ] };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => String(input) === "/api/production/calendar"
    ? json({ holidays: ["2026-09-24"], absences: [], shift: {} }) : json(groupedBoard));
  render(<ProductionPlanPage principal={principal} />);
  await screen.findByText("Альфа");

  // Отгруженные скрыты по умолчанию (владелец 12.10), счётчик в шапке — видимые проекты.
  expect(projects()).toEqual(["703К.1", "702К.1", "704К.1", "701К.1"]);
  expect(Array.from(document.querySelectorAll(".zg-group-label")).map((item) => item.textContent)).toEqual([
    "Новый · 1", "В работе · 3",
  ]);
  expect(document.querySelector(".page-count")?.textContent).toBe("4");
  // The blocked project keeps its stage group and its chip.
  expect(within(sheetRow("703К.1")).getByText("ХОЛД")).toBeInTheDocument();
  // A collapsed stage hides its rows and stays collapsed after a remount.
  fireEvent.click(screen.getByRole("button", { name: "В работе · 3" }));
  expect(projects()).toEqual(["703К.1"]);
  expect(JSON.parse(window.localStorage.getItem("erp-zg-collapsed") ?? "null")).toEqual(["В работе"]);
  fireEvent.click(screen.getByRole("button", { name: "В работе · 3" }));
  expect(projects()).toEqual(["703К.1", "702К.1", "704К.1", "701К.1"]);
  window.localStorage.removeItem("erp-zg-collapsed");
  // «Показать отгруженные» adds the last group, folded.
  fireEvent.click(screen.getByLabelText("Показать отгруженные · 1"));
  expect(screen.getByRole("button", { name: "Отгружено · 1" })).toHaveAttribute("aria-expanded", "false");
  expect(projects()).not.toContain("706К.1");
  fireEvent.click(screen.getByRole("button", { name: "Отгружено · 1" }));
  expect(projects()).toContain("706К.1");
  // A shop holiday (24.09) is grey like a weekend.
  await waitFor(() => expect(document.querySelectorAll(".zg-table thead th.zg-day-head")[9]?.className).toContain("zg-off"));
  fireEvent.click(screen.getByLabelText("Закрытые проекты · 1"));
  expect(projects()).toContain("705К.1");
  fireEvent.change(screen.getByLabelText("Поиск проекта"), { target: { value: "эпс" } });
  expect(projects()).toEqual(["705К.1"]);
  fireEvent.change(screen.getByLabelText("Поиск проекта"), { target: { value: "нет такого" } });
  expect(screen.getByText("Под этот поиск проектов нет.")).toBeInTheDocument();
});

const STAGES = ["Новый", "ТК готова", "КБ набрана", "Распланировано", "В работе", "ОТК", "Принято складом", "Упаковано", "Отгружено"];
const names = (groups: ReturnType<typeof groupSheetRows>) => groups.map((group) => [group.label, group.rows.map((item) => item.project)]);

it("groupSheetRows: groups follow the stage list, empty ones are hidden, closed go last", () => {
  const rows = [
    row({ project: "A", stage: "Отгружено" }), row({ project: "B", stage: "Новый" }), row({ project: "C", stage: "ОТК" }),
    row({ project: "D", stage: "Отменён", terminal: true }), row({ project: "E", stage: "В работе" }),
  ];
  expect(names(groupSheetRows(rows, STAGES))).toEqual([["Новый", ["B"]], ["В работе", ["E"]], ["ОТК", ["C"]], ["Отгружено", ["A"]], ["Закрытые", ["D"]]]);
  // The order comes from the passed list, not from the code.
  expect(groupSheetRows(rows, ["Отгружено", "Новый"]).map((group) => group.label)).toEqual(["Отгружено", "Новый", "ОТК", "В работе", "Закрытые"]);
  expect(groupSheetRows(rows, STAGES, "нет такого")).toEqual([]);
});

it("groupSheetRows: overdue first, no deadline last, project numbers compare numerically", () => {
  const rows = [
    row({ project: "КБ10", stage: "В работе", days_left: 3 }), row({ project: "КБ9", stage: "В работе", days_left: 3 }),
    row({ project: "97К.10", stage: "В работе", days_left: null }), row({ project: "97К.9", stage: "В работе", days_left: null }),
    row({ project: "Z", stage: "В работе", days_left: -5 }), row({ project: "Y", stage: "В работе", days_left: 0 }),
  ];
  expect(groupSheetRows(rows, STAGES)[0].rows.map((item) => item.project)).toEqual(["Z", "Y", "КБ9", "КБ10", "97К.9", "97К.10"]);
});

it("groupSheetRows: a blocked project stays in its stage; an unknown stage goes after the known ones", () => {
  const blocked = row({ project: "H", stage: "ОТК", blocker: { type: "ХОЛД", text: "", since: "2026-09-22", who: "" } });
  const groups = groupSheetRows([blocked, row({ project: "X", stage: "Новый" }), row({ project: "W", stage: "Неизвестный" })], STAGES);
  expect(names(groups)).toEqual([["Новый", ["X"]], ["ОТК", ["H"]], ["Неизвестный", ["W"]]]);
});

it("edits sheet cells inline: a checkbox, back to the ERP value, a day note and the deadline", async () => {
  const payloads: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input) === "/api/production/plan/events") {
      payloads.push(JSON.parse(String(init?.body)));
      return json({ event: plan.rows[0].events[0] }, 201);
    }
    return json(plan);
  });
  render(<ProductionPlanPage principal={principal} />);
  await screen.findByRole("button", { name: "708К.4" });

  fireEvent.click(screen.getByLabelText("Комплект проверен · 708К.4"));
  await waitFor(() => expect(payloads[0]).toMatchObject({ project: "708К.4", kind: "mark", field: "kit_checked", value: "да" }));
  // Тест С/Б marked by hand «да» over «нет»: unticking returns the ERP value.
  await waitFor(() => expect(screen.getByLabelText("Тестовая сборка · 708К.4")).not.toBeDisabled());
  fireEvent.click(screen.getByLabelText("Тестовая сборка · 708К.4"));
  await waitFor(() => expect(payloads[1]).toMatchObject({ field: "test_sb", value: "авто" }));

  await waitFor(() => expect(screen.getByLabelText("Тестовая сборка · 708К.4")).not.toBeDisabled());
  await waitFor(() => expect(screen.getByLabelText("Тестовая сборка · 708К.4")).not.toBeDisabled());
  const day = Array.from(sheetRow("708К.4").querySelectorAll(".zg-day"))[10];
  expect(day.textContent).toBe("П");
  fireEvent.click(day);
  fireEvent.click(await screen.findByRole("button", { name: "Убрать отметку" }));
  await waitFor(() => expect(payloads[2]).toMatchObject({ field: "day:2026-09-25", value: "" }));

  await waitFor(() => expect(screen.getByLabelText("Тестовая сборка · 708К.4")).not.toBeDisabled());
  fireEvent.click(sheetRow("708К.4").querySelector(".zg-col-deadline")!);
  fireEvent.change(await screen.findByLabelText("Дата дедлайна"), { target: { value: "2026-10-03" } });
  fireEvent.click(screen.getByRole("button", { name: "Поставить дедлайн 03.10.2026" }));
  await waitFor(() => expect(payloads[3]).toEqual({ project: "708К.4", kind: "deadline", blocker: "", text: "", deadline: "2026-10-03" }));
});

it("draws the calendar: work days in the project colour, quantity on the first day, deadline and arrival marks", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(plan));
  render(<ProductionPlanPage principal={{ ...principal, roles: ["tech_lead"], access_level: "tech_lead", permissions: { production: "view" } }} />);
  await screen.findByRole("button", { name: "708К.4" });
  const days = Array.from(sheetRow("708К.4").querySelectorAll(".zg-day"));

  expect(days[7].className).toContain("zg-work-start");
  expect(days[7].className).toContain("zg-today");
  expect(days[7].textContent).toBe("10");
  expect(days[8].className).toContain("zg-work-end");
  expect(days[13].className).toContain("zg-arrival");
  expect(days[15].className).toContain("zg-deadline");
  // Read-only viewer: no inline editing.
  expect(screen.getByLabelText("В плане · 708К.4")).toBeDisabled();
  fireEvent.click(days[10]);
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("keeps sheet helpers stable", () => {
  expect(projectColor("708К.4")).toBe(projectColor("708К.4"));
  expect(projectColor("708К.4")).toBeGreaterThanOrEqual(0);
  expect(projectColor("708К.4")).toBeLessThan(12);
  expect(sheetDays("2026-10-02", "2026-10-05").map((day) => [day.label, day.weekday, day.off])).toEqual([
    ["02.10", "пт", false], ["03.10", "сб", true], ["04.10", "вс", true], ["05.10", "пн", false],
  ]);
  expect(sheetDays("2026-09-30", "2026-10-01")[1].monthStart).toBe(true);
  expect(sheetMonths(sheetDays("2026-12-30", "2027-01-02")).map((month) => [month.label, month.span])).toEqual([["декабрь 2026", 2], ["январь 2027", 2]]);
  // Сегодня — третьим днём слева: два дня до него остаются видны; в начале листа сдвига нет.
  const days = sheetDays("2026-09-27", "2026-12-03");
  expect(todayScrollLeft(days, "2026-10-04", 24)).toBe(5 * 24);
  expect(todayScrollLeft(days, "2026-09-28", 24)).toBe(0);
  expect(todayScrollLeft(days, "2027-01-01", 24)).toBe(0);
  expect(todayScrollLeft(days, "2026-10-04", 0)).toBe(0);
  // Узкий экран прячет реже нужные колонки, пока календарь не покажет MIN_VISIBLE_DAYS дней; широкий — ничего.
  // Комментарий скрывается первым (ПП-8), на 16" (лист 1192 px, день 16 px) календарь показывает пять недель (ПП-2).
  expect(MIN_VISIBLE_DAYS).toBe(35);
  expect(autoHiddenColumns(2218, 24)).toEqual([]);
  const laptop = autoHiddenColumns(1192, 16);
  expect(laptop).toEqual(["comment", "days_left", "zkp", "in_plan", "customer_supplied", "test_sb", "kit_checked", "ready", "tc_ready"]);
  const frozenLeft = 1276 - [160, 60, 66, 48, 82, 60, 62, 76, 60].reduce((sum, width) => sum + width, 0);
  expect(Math.floor((1192 - frozenLeft) / 16)).toBeGreaterThanOrEqual(35);
  expect(autoHiddenColumns(1192, 16, false, 104, MIN_VISIBLE_WEEKS)).toEqual(["comment", "days_left"]);
  expect(autoHiddenColumns(1192, 46, false, 104, MIN_VISIBLE_WEEKS)).toEqual(["comment", "days_left", "zkp", "in_plan", "customer_supplied", "test_sb"]);
  expect(autoHiddenColumns(600, 24)).toEqual(["comment", "days_left", "zkp", "in_plan", "customer_supplied", "test_sb", "kit_checked", "ready", "tc_ready"]);
  expect(autoHiddenColumns(0, 24)).toEqual([]);
  expect(markText({ field: "status", value: "ХОЛД", text: "" })).toBe("Статус: ХОЛД");
  expect(markText({ field: "kit_arrival", value: "2026-10-09", text: "" })).toBe("Приход комплектующих: 09.10.2026");
  expect(markText({ field: "status", value: "К ЗАПУСК, ОЖИД ИНФ", text: "" })).toBe("Статус: КБ набрана, ОЖИД ИНФ");
  expect(markText({ field: "in_plan", value: "авто", text: "" })).toBe("В плане: расчёт ERP");
  expect(markText({ field: "day:2026-10-06", value: "П", text: "" })).toBe("Отметка 06.10.2026: П");
  expect(markText({ field: "comment", value: "", text: "" })).toBe("Комментарий: очищено");
});

const loadOperation = (id: string, position: number, stage: string, operators: string[], start: string | null, finish: string | null, state = "не начата") => ({
  id, position, code: "", stage, description: "", seconds_per_unit: 60, setup_seconds: 0,
  norm_seconds: 7200, operator_ids: operators, planned_start: start, planned_finish: finish, state,
});
const loadTask = (operations: unknown[], deadline: string | null = "2026-09-22") => ({
  id: "t1", number: "ПЗ-0001", version: 1, created_at: "2026-09-21T09:00:00", project: "708К.4", item: "КБ1", name: "Жгут",
  quantity: 10, fulfillment_request_id: null, deadline, status: "В работе", launched_by: "Мастер",
  planned_start: null, planned_finish: null, assigned: 2, operations,
});
function mockLoad(tasks: unknown[], operators: unknown[], absences: unknown[] = []) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/production/calendar") return json({ holidays: [], absences, shift: {} });
    if (path === "/api/production/tasks") return json({ tasks, next_cursor: null });
    if (path === "/api/production/operators") return json({ operators });
    return json(plan);
  });
}

it("shows people load as hours per shift: tone by load, late marker, absence, free people compact, unassigned", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 8, 22, 10));
  try {
    mockLoad([loadTask([
      loadOperation("o1", 1, "Резка", ["a"], "2026-09-22T09:00:00", "2026-09-22T10:30:00", "выполнена"),
      loadOperation("o2", 2, "Опрессовка", ["a"], "2026-09-23T09:00:00", "2026-09-23T17:30:00"),
      loadOperation("o3", 3, "Сборка", [], null, null),
    ])], [{ id: "b", name: "Борисов Б.", site: "ЦАР" }, { id: "a", name: "Алексеева А.", site: "ЦАР" }],
    [{ operator_id: "b", from_date: "2026-09-24", to_date: "2026-09-25", kind: "Отпуск" }]);
    render(<ProductionPlanPage principal={principal} />);
    const load = await screen.findByRole("region", { name: "Загрузка по людям" });

    await waitFor(() => expect(load.querySelector(".pl-table")).not.toBeNull());
    expect(load.textContent).toContain("21 сентября – 4 октября 2026 · 2 нед.");
    const rows = Array.from(load.querySelectorAll(".pl-table tbody tr"));
    expect(rows.map((row) => row.querySelector("th b")?.textContent)).toEqual(["Алексеева А.", "Борисов Б."]);
    const mon = rows[0].querySelectorAll("td");
    expect(mon[1].textContent).toBe("1,3/7");
    expect(mon[1].className).toContain("low");
    expect(mon[1].className).not.toContain("late");
    expect(mon[2].textContent).toBe("7/7!");
    expect(mon[2].className).toContain("full");
    expect(mon[2].className).toContain("late");
    expect(mon[5].className).toContain("off");
    expect(rows[1].querySelectorAll("td")[3].className).toContain("absent");
    expect(rows[1].querySelectorAll("td")[3].textContent).toBe("отпуск");
    expect(rows[0].textContent).toContain("8,3 ч за 2 нед.");
    expect(rows[1].textContent).toContain("свободен");
    expect(screen.getByText("Без исполнителя: 1 опер. в 1 зад. — назначить в «Ресурсном планировании»")).toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

it("marks overload and lists the day's operations with ПЗ, hours and deadline on click", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 8, 22, 10));
  try {
    mockLoad([loadTask([
      loadOperation("o1", 1, "Нарезка А", ["a"], "2026-09-22T09:00:00", "2026-09-22T17:30:00"),
      loadOperation("o2", 2, "Сборка", ["a"], "2026-09-22T09:00:00", "2026-09-22T17:30:00"),
    ], "2026-09-30")], [{ id: "a", name: "Алексеева А.", site: "" }]);
    render(<ProductionPlanPage principal={principal} />);
    const cellButton = await screen.findByRole("button", { name: "Загрузка Алексеева А. 22.09" });
    expect(cellButton.closest("td")!.className).toContain("over");
    fireEvent.click(cellButton);
    const detail = screen.getByRole("group", { name: "Что в дне: Алексеева А., 22.09" });
    expect(detail.textContent).toContain("ПЗ-0001 · 708К.4 · КБ1");
    expect(detail.textContent).toContain("1. Нарезка А");
    expect(detail.textContent).toContain("дедлайн 30.09");
    fireEvent.click(screen.getByRole("button", { name: "Закрыть день" }));
    expect(screen.queryByRole("group", { name: /Что в дне/ })).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

it("«Только занятые» hides free people", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 8, 22, 10));
  try {
    mockLoad([loadTask([loadOperation("o1", 1, "Резка", ["a"], "2026-09-22T09:00:00", "2026-09-22T12:00:00")])],
      [{ id: "a", name: "Алексеева А.", site: "" }, { id: "b", name: "Борисов Б.", site: "" }]);
    render(<ProductionPlanPage principal={principal} />);
    await screen.findByText("Борисов Б.");
    fireEvent.click(screen.getByLabelText("Только занятые"));
    expect(screen.queryByText("Борисов Б.")).toBeNull();
    expect(screen.getByText("Алексеева А.")).toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

it("shows the project stage in the shared chip with the blocker beside it and sizes the column to the longest pair", async () => {
  const mixed: ProductionPlanResult = { ...plan, rows: [
    row({ project: "621К.2", group: "active", stage: "КБ на складе", blocker: { type: "ОЖИД КОМПЛ", text: "Ждём", since: "2026-09-22T09:00:00+03:00", who: "Мастер" } }),
    row({ project: "708К.2", group: "active", stage: "Новый", blocker: null }),
    row({ project: "614К.1", group: "active", stage: "В работе", blocker: null }),
  ] };
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(mixed));
  render(<ProductionPlanPage principal={principal} />);
  await screen.findByRole("button", { name: "621К.2" });

  const chips = (project: string) => Array.from(sheetRow(project).querySelectorAll(".zg-col-status .status-chip"));
  expect(chips("621К.2").map((item) => item.textContent)).toEqual(["КБ на складе▾", "ОЖИД КОМПЛ"]);
  expect(chips("621К.2")[1].getAttribute("data-tone")).toBe("warning");
  expect(chips("614К.1").map((item) => item.textContent)).toEqual(["В работе▾"]);
  expect(chips("708К.2").map((item) => item.textContent)).toEqual(["Новый▾"]);
  const width = Number.parseInt(document.querySelector<HTMLElement>(".zg-table col:nth-child(3)")!.style.width, 10);
  expect(width).toBe(statusColumnWidth(mixed.rows));
  expect(width).toBeGreaterThan(statusColumnWidth([mixed.rows[1]]));
});

it("sizes the status column by the stage chip and the blocker chip beside it", () => {
  expect(orderStatuses("В РАБОТЕ, СТОП")).toEqual(["СТОП", "В РАБОТЕ"]);
  expect(orderStatuses("К ЗАПУСК, ОЖИД КОМПЛ, ХОЛД")).toEqual(["ОЖИД КОМПЛ", "ХОЛД", "К ЗАПУСК"]);
  const blocker = { type: "ОЖИД КОМПЛ", text: "", since: "", who: "" };
  const plain = statusColumnWidth([{ stage: "КБ на складе", blocker: null }]);
  const held = statusColumnWidth([{ stage: "КБ на складе", blocker }]);
  expect(plain).toBeGreaterThanOrEqual(84);
  expect(held).toBeGreaterThan(plain);
  expect(statusColumnWidth([])).toBe(84);
  expect(held).toBeLessThanOrEqual(260);
});

it("changes the stage, blocks and closes a project without a card from the status chip, through the plan journal (one status everywhere)", async () => {
  const bare = row({ project: "709К.1", group: "active", stage: "Новый", blocker: null });
  const payloads: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.startsWith("/api/production/plan/stage-preview")) return json(stagePreview({ project: "709К.1", value: "В работе" }));
    if (url === "/api/production/plan/events") { payloads.push(JSON.parse(String(init?.body))); return json({ event: plan.rows[0].events[0] }, 201); }
    return json({ ...plan, rows: [bare] });
  });
  render(<ProductionPlanPage principal={principal} />);
  await screen.findByRole("button", { name: "709К.1" });
  const menu = async () => { fireEvent.click(sheetRow("709К.1").querySelector(".proj-status-btn")!); return screen.findByRole("menu", { name: "Статус проекта 709К.1" }); };

  // Блокер — с причиной, записью «Блокер» плана.
  let box = await menu();
  fireEvent.click(within(box).getByRole("menuitem", { name: "ХОЛД" }));
  fireEvent.change(screen.getByLabelText("Причина ХОЛД"), { target: { value: "ждём разъёмы" } });
  fireEvent.click(screen.getByRole("button", { name: "Поставить" }));
  await waitFor(() => expect(payloads[0]).toMatchObject({ project: "709К.1", kind: "block", blocker: "ХОЛД", text: "ждём разъёмы" }));

  // Закрытие проекта — запись «Статус проекта».
  box = await menu();
  fireEvent.click(within(box).getByRole("menuitemradio", { name: "Отменён" }));
  await waitFor(() => expect(payloads[1]).toMatchObject({ project: "709К.1", kind: "status", value: "Отменён" }));

  // Этап — то же окно подтверждения с предпросмотром плана, запись «Этап КБ» на весь проект.
  box = await menu();
  fireEvent.click(within(box).getByRole("menuitemradio", { name: "В работе" }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Сменить этап" }));
  await waitFor(() => expect(payloads[2]).toMatchObject({ project: "709К.1", kind: "stage", item_index: "", value: "В работе" }));
});

it("opens the project in «Ресурсное планирование» from the number and from the customer, with no side drawer", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(plan));
  const places: string[] = [];
  const listener = () => places.push(window.location.search);
  window.addEventListener("popstate", listener);
  window.history.replaceState({}, "", "/?section=prodplan");
  render(<ProductionPlanPage principal={principal} />);
  const number = await screen.findByRole("button", { name: "708К.4" });
  fireEvent.click(number);
  expect(places).toEqual(["?section=production&project=708%D0%9A.4"]);
  fireEvent.click(sheetRow("708К.4").querySelector(".zg-link")!);
  window.removeEventListener("popstate", listener);
  expect(places).toHaveLength(2);
  expect(places[1]).toBe(places[0]);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.querySelector(".plan-drawer")).toBeNull();
});

it("falls back to the project card when the master board is closed to the viewer", async () => {
  const opened: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => String(input).startsWith("/api/techcards/kitsets")
    ? json({ items: [{ project: "708К.4" }], statuses: [] }) : json(plan));
  const limited: Principal = { ...principal, roles: ["viewer"], access_level: "viewer", permissions: { prodplan: "view" } };
  render(<ProductionPlanPage principal={limited} openProjectCard={(project) => opened.push(project)} />);
  const number = await screen.findByRole("button", { name: "708К.4" });
  await waitFor(() => expect(window.location.search).toBe(""));
  fireEvent.click(number);
  expect(opened).toEqual(["708К.4"]);
  expect(window.location.search).toBe("");
});

it("hides the comment column first and shows a comment as a mark at the project number", async () => {
  window.localStorage.setItem("erp-zg-columns", JSON.stringify(["comment"]));
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(plan));
  render(<ProductionPlanPage principal={principal} />);
  const number = await screen.findByRole("button", { name: "708К.4" });
  expect(number.querySelector(".zg-note-mark")).not.toBeNull();
  expect(number.getAttribute("title")).toContain("Комментарий: ждём разъёмы");
  expect(screen.getByText("комментарий", { selector: ".zg-legend span" })).toBeInTheDocument();
});

it("switches the calendar to weeks: one column a week, bar, launch and deadline inside the week, choice remembered", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(plan));
  render(<ProductionPlanPage principal={principal} />);
  await screen.findByRole("button", { name: "708К.4" });
  fireEvent.click(screen.getByRole("button", { name: "Недели" }));

  const heads = Array.from(document.querySelectorAll(".zg-table thead th.zg-week-head"));
  // 15.09 (вт) … 21.11 (сб): первая и последняя неделя неполные.
  expect(heads).toHaveLength(10);
  expect(heads[0].textContent).toBe("15.09–20.09");
  expect(heads[1].className).toContain("zg-today");
  const weeks = Array.from(sheetRow("708К.4").querySelectorAll("td.zg-week"));
  expect(weeks).toHaveLength(10);
  expect(weeks[1].className).toContain("zg-work-start");
  expect(weeks[1].textContent).toBe("10");
  expect(weeks[2].querySelector(".zg-week-deadline")).not.toBeNull();
  expect(window.localStorage.getItem("erp-zg-scale")).toBe(JSON.stringify("week"));
  expect(screen.getByText("эта неделя")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Дни" }));
  expect(document.querySelectorAll(".zg-table thead th.zg-week-head")).toHaveLength(0);
  expect(window.localStorage.getItem("erp-zg-scale")).toBeNull();
});

it("opens a sheet cell editor from the keyboard and shows «—» in empty cells", async () => {
  const bare = row({ project: "709К.1", deadline: null, days_left: null, group: "active", sheet: { ...sheet, ready: cell(""), kit_arrival: cell("") } });
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ ...plan, rows: [bare] }));
  render(<ProductionPlanPage principal={principal} />);
  await screen.findByRole("button", { name: "709К.1" });
  const ready = sheetRow("709К.1").querySelector<HTMLElement>(".zg-col-ready")!;
  expect(ready.tabIndex).toBe(0);
  fireEvent.keyDown(ready, { key: "Enter" });
  expect(await screen.findByRole("dialog", { name: "Правка: 709К.1" })).toBeInTheDocument();
  expect(["ready", "kit_arrival", "deadline"].map((key) => sheetRow("709К.1").querySelector(`.zg-col-${key}`)?.textContent)).toEqual(["—", "—", "—"]);
});

it("keeps calendar helpers: period in words, launch quantity on the first working day, weeks from Monday", () => {
  expect(periodWords("2026-10-03", "2026-12-09")).toBe("3 октября – 9 декабря 2026");
  expect(periodWords("2026-10-05", "2026-10-18")).toBe("5–18 октября 2026");
  expect(periodWords("2026-12-28", "2027-01-10")).toBe("28 декабря 2026 – 10 января 2027");
  // Запуск в субботу на полосе сб–пн: число встаёт на понедельник (ПП-4); одиночный выходной — остаётся на месте.
  const days = sheetDays("2026-10-09", "2026-10-13");
  expect(startLabels(days, new Set(["2026-10-10", "2026-10-11", "2026-10-12"]), { "2026-10-10": 140 })).toEqual({ "2026-10-12": 140 });
  expect(startLabels(days, new Set(["2026-10-10"]), { "2026-10-10": 140 })).toEqual({ "2026-10-10": 140 });
  expect(startLabels(days, new Set(["2026-10-09"]), { "2026-10-09": 5 })).toEqual({ "2026-10-09": 5 });
  const weeks = sheetWeeks(sheetDays("2026-10-03", "2026-10-20"));
  expect(weeks.map((week) => [week.from, week.to])).toEqual([
    ["2026-10-03", "2026-10-04"], ["2026-10-05", "2026-10-11"], ["2026-10-12", "2026-10-18"], ["2026-10-19", "2026-10-20"],
  ]);
  const marks = weekMarks(weeks[1], { deadline: "2026-10-09", sheet: { kit_arrival: cell("2026-10-05") },
    calendar: { work: ["2026-10-07", "2026-10-08"], starts: { "2026-10-07": 12 }, notes: { "2026-10-08": "П" } } });
  expect(marks.bar).toEqual({ from: 2 / 7, to: 4 / 7 });
  expect(marks.quantity).toBe(12);
  expect(marks.deadline).toBe(5 / 7);
  expect(marks.arrival).toBe(0);
  expect(marks.notes).toEqual(["08.10: П"]);
});

it("полоса «срок»: сегодня → дедлайн, просрочка — от дедлайна до сегодня, без дедлайна нет", () => {
  expect(deadlineSpan({ deadline: "2026-10-16", calendar: { work: [], starts: {}, notes: {} } }, "2026-10-12")).toEqual({ from: "2026-10-12", to: "2026-10-16", late: false });
  expect(deadlineSpan({ deadline: "2026-10-16", calendar: { work: ["2026-10-09", "2026-10-13"], starts: {}, notes: {} } }, "2026-10-12")).toEqual({ from: "2026-10-09", to: "2026-10-16", late: false });
  expect(deadlineSpan({ deadline: "2026-10-12", calendar: undefined }, "2026-10-12")).toEqual({ from: "2026-10-12", to: "2026-10-12", late: false });
  expect(deadlineSpan({ deadline: "2026-10-08", calendar: { work: ["2026-10-13"], starts: {}, notes: {} } }, "2026-10-12")).toEqual({ from: "2026-10-08", to: "2026-10-12", late: true });
  expect(deadlineSpan({ deadline: null, calendar: { work: ["2026-10-13"], starts: {}, notes: {} } }, "2026-10-12")).toBeNull();
  // Недели: доли недели 12–18.10 и 19–20.10 для полосы 12.10–21.10 не нужны; проверяем частичную неделю и пустую.
  const weeks = sheetWeeks(sheetDays("2026-10-03", "2026-10-20"));
  const row = { deadline: "2026-10-14", sheet: undefined as never, calendar: { work: [], starts: {}, notes: {} } };
  const span = deadlineSpan(row, "2026-10-08");
  expect(weekMarks(weeks[0], row, span).span).toBeNull();
  expect(weekMarks(weeks[1], row, span).span).toEqual({ from: 3 / 7, to: 1, late: false });
  expect(weekMarks(weeks[2], row, span).span).toEqual({ from: 0, to: 3 / 7, late: false });
  expect(weekMarks(weeks[3], row, span).span).toBeNull();
  expect(weekMarks(weeks[1], row).span).toBeNull();
  const late = deadlineSpan({ ...row, deadline: "2026-10-06" }, "2026-10-14");
  expect(weekMarks(weeks[1], { ...row, deadline: "2026-10-06" }, late).span).toEqual({ from: 1 / 7, to: 1, late: true });
});
