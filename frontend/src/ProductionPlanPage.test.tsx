import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProductionPlanPage } from "./ProductionPlanPage";
import { MIN_VISIBLE_DAYS, autoHiddenColumns, groupSheetRows, markText, projectColor, sheetDays, sheetMonths, todayScrollLeft } from "./PlanLoadSheet";
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
    deadline: "2026-09-30", days_left: 8, stage: "В работе", terminal: false, stage_manual: false,
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

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.restoreAllMocks(); window.localStorage.removeItem("erp-zg-columns"); });

/** Журнал проекта — по клику на организацию (№ проекта открывает карточку). */
async function openProject(project: string) {
  const row = (await screen.findByRole("button", { name: project })).closest("tr")!;
  fireEvent.click(row.querySelector(".zg-link")!);
}

function sheetRow(project: string) {
  return document.querySelector(`tr[data-project="${project}"]`) as HTMLTableRowElement;
}

it("shows the shop sheet «ЗАГРУЗ» columns in the sheet order and the project journal", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => String(input) === "/api/production/calendar" ? json({ holidays: [], absences: [], shift: {} }) : json(plan));
  render(<ProductionPlanPage principal={principal} />);

  expect(await screen.findByRole("heading", { name: "Производственный план" })).toBeInTheDocument();
  await screen.findByRole("button", { name: "708К.4" });
  const heads = Array.from(document.querySelectorAll(".zg-table thead th.zg-col-head")).map((item) => item.textContent);
  expect(heads).toEqual([
    "Комментарий", "Статус", "Готов", "План", "№", "Заказчик", "Дав.", "Пров.",
    "Компл", "ЗКП", "Приход", "ТК", "С/Б", "Набр.", "Дн.", "Дедлайн",
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
  // Статус — один чип и «+N», полный список в подсказке.
  expect(Array.from(row.querySelectorAll(".zg-chip")).map((item) => item.textContent)).toEqual(["СТОП"]);
  expect(row.querySelector(".zg-more")?.textContent).toBe("+1");
  expect(row.querySelector(".zg-col-ready")?.textContent).toBe("40%");
  expect(row.querySelector<HTMLElement>(".zg-ready i")?.style.getPropertyValue("--zg-ready")).toBe("40%");
  expect(row.querySelector(".zg-col-kit_arrival")?.textContent).toBe("28.09");
  expect(row.querySelector(".zg-col-days_left")?.className).toContain("ok");
  expect(row.querySelector(".zg-col-deadline")?.textContent).toBe("30.09");
  expect(row.querySelector(".zg-col-deadline")?.getAttribute("title")).toBe("Дедлайн 30.09.2026 · дней до дедлайна: 8");
  expect(row.querySelector(".zg-col-test_sb")?.className).toContain("zg-manual");
  expect(screen.getByText("На паузе · 1")).toBeInTheDocument();
  await openProject("708К.4");
  expect(screen.getAllByText("Ждём разъёмы").length).toBeGreaterThan(0);
  expect(screen.getByText("Всего 5: склад 2, в пути 1, в закупке 1, в заявках 1.")).toBeInTheDocument();
  expect(screen.getByText("1 заданий · назначено 2 из 3 этапов.")).toBeInTheDocument();
  expect(screen.getByText("ТК выдана · не набрано")).toBeInTheDocument();
});

it("keeps the sheet header to short one-line labels and gives the full sheet names in tooltips", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => String(input) === "/api/production/calendar" ? json({ holidays: [], absences: [], shift: {} }) : json(plan));
  render(<ProductionPlanPage principal={principal} />);
  await screen.findByRole("button", { name: "708К.4" });

  const heads = Array.from(document.querySelectorAll<HTMLElement>(".zg-table thead th.zg-col-head"));
  // Подпись — одна короткая строка, без переносов; полное название колонки листа — в подсказке.
  expect(heads.every((head) => head.textContent!.length <= 11 && !head.textContent!.includes("\n"))).toBe(true);
  // Все показанные колонки закреплены; последняя отделена от календаря границей с тенью.
  expect(heads.every((head) => head.classList.contains("zg-freeze"))).toBe(true);
  expect(heads.filter((head) => head.classList.contains("zg-edge")).map((head) => head.textContent)).toEqual(["Дедлайн"]);
  expect(heads.map((head) => head.getAttribute("title")?.split(" — ")[0])).toEqual([
    "Комментарий", "Статус", "% Готов", "В плане", "№ Проекта", "Наименование организации", "Есть давал", "Пров компл",
    "Компл есть", "ЗКП", "Дата прихода компл.", "Тех кар", "Тест С/Б", "Компл набр", "Ост дн", "Дедлайн",
  ]);
  expect(heads[3].getAttribute("title")).toBe("В плане — на проект назначены операции");
  expect(heads[7].getAttribute("title")).toBe("Пров компл — комплектация проверена");
  // Ширина таблицы задана колонками, а не содержимым: длинный комментарий её не раздувает.
  expect(document.querySelector<HTMLElement>(".zg-table")!.style.width).toMatch(/^calc\(\d+px \+ 68 \* var\(--zg-day\)\)$/);

  const row = sheetRow("708К.4");
  expect(row.querySelector(".zg-col-comment")?.getAttribute("title")).toBe("ждём разъёмы");
  expect(row.querySelector(".zg-link")?.getAttribute("title")).toBe("Заказчик — журнал проекта");
  expect(row.querySelector(".zg-col-status")?.getAttribute("title")).toBe("СТОП, В РАБОТЕ — СТОП: Ждём разъёмы");
  expect(row.querySelector(".zg-col-kit_checked")?.getAttribute("title")).toBe("Пров компл — комплектация проверена");
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
    ["В плане", "✓", "true"], ["Пров компл", "—", "false"], ["Компл есть", "—", "false"], ["ЗКП", "✓", "true"],
    ["Тех кар", "✓", "true"], ["Тест С/Б", "✓", "true"], ["Компл набр", "—", "false"],
  ]);
  expect(flags.every((flag) => flag.getAttribute("role") === "checkbox")).toBe(true);
  // Короткая подпись в шапке, полное название и смысл — в подсказке.
  const head = (label: string) => Array.from(document.querySelectorAll(".zg-col-head")).find((item) => item.textContent === label)!;
  expect(head("С/Б").getAttribute("title")).toBe("Тест С/Б");
  expect(head("Компл").getAttribute("title")).toBe("Компл есть — все комплектующие на складе");
  expect(head("План").getAttribute("title")).toBe("В плане — на проект назначены операции");
  // «Давал»: «Ч/Т» коротко в ячейке, полные слова — в списке поверх неё.
  expect(row.querySelector(".zg-col-customer_supplied .zg-supplied")?.textContent).toBe("Ч/Т");
  expect(screen.getByLabelText("Есть давал · 708К.4")).toHaveValue("авто");
});

it("lets the viewer choose the sheet columns, keeps the sheet order and returns to the screen-width default", async () => {
  window.localStorage.removeItem("erp-zg-columns");
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(plan));
  render(<ProductionPlanPage principal={principal} />);
  await screen.findByRole("button", { name: "708К.4" });
  const labels = () => Array.from(document.querySelectorAll(".zg-col-head")).map((item) => item.textContent);

  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  const chooser = screen.getByRole("group", { name: "Показывать колонки" });
  expect(chooser.querySelector<HTMLInputElement>("input[disabled]")?.parentElement?.textContent).toBe("№ Проекта");
  fireEvent.click(screen.getByLabelText("Ост дн"));
  fireEvent.click(screen.getByLabelText("ЗКП"));
  expect(labels()).toEqual(["Комментарий", "Статус", "Готов", "План", "№", "Заказчик", "Дав.", "Пров.", "Компл", "Приход", "ТК", "С/Б", "Набр.", "Дедлайн"]);
  expect(screen.getByRole("button", { name: "Колонки · 14 из 16" })).toBeInTheDocument();
  expect(JSON.parse(window.localStorage.getItem("erp-zg-columns")!)).toEqual(["days_left", "zkp"]);
  // «Дн.» скрыта — светофор дедлайна переезжает в «Дедлайн».
  expect(sheetRow("708К.4").querySelector(".zg-col-deadline")?.className).toContain("zg-days ok");
  expect(sheetRow("708К.4").querySelectorAll("td.zg-freeze")).toHaveLength(14);

  fireEvent.click(screen.getByRole("button", { name: "По ширине экрана" }));
  expect(labels()).toHaveLength(16);
  expect(window.localStorage.getItem("erp-zg-columns")).toBeNull();
});

it("sets the stage of a project without KBs through a journal mark with an empty KB", async () => {
  const bare = { ...plan.rows[0], project: "70К.26.13", stage: "Новый", kits: [], blocker: null, events: [] };
  let payload: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input) === "/api/production/plan/events") {
      payload = JSON.parse(String(init?.body));
      return json({ event: plan.rows[0].events[0] }, 201);
    }
    return json({ ...plan, rows: [bare] });
  });
  render(<ProductionPlanPage principal={principal} />);
  await openProject("70К.26.13");

  expect(screen.queryByText(/набрано/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Этап проекта"), { target: { value: "Отгружено" } });
  await waitFor(() => expect(payload).toMatchObject({
    project: "70К.26.13", kind: "stage", item_index: "", value: "Отгружено",
  }));
});

it("adds a deadline through the native project journal", async () => {
  let payload: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input) === "/api/production/plan/events") {
      payload = JSON.parse(String(init?.body));
      return json({ event: plan.rows[0].events[0] }, 201);
    }
    return json(plan);
  });
  render(<ProductionPlanPage principal={principal} />);
  await openProject("708К.4");
  fireEvent.change(screen.getByLabelText("Запись"), { target: { value: "deadline" } });
  fireEvent.change(screen.getByLabelText("Дата"), { target: { value: "2026-10-03" } });
  fireEvent.click(screen.getByRole("button", { name: "Записать" }));

  await waitFor(() => expect(payload).toEqual({
    project: "708К.4", kind: "deadline", blocker: "ХОЛД", text: "", deadline: "2026-10-03",
  }));
});

it("records manual kit stage and techcard review", async () => {
  const payloads: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input) === "/api/production/plan/events") {
      payloads.push(JSON.parse(String(init?.body)));
      return json({ event: plan.rows[0].events[0] }, 201);
    }
    return json(plan);
  });
  render(<ProductionPlanPage principal={principal} />);
  await openProject("708К.4");

  fireEvent.change(screen.getByLabelText("Этап КБ1"), { target: { value: "Отгружено" } });
  await waitFor(() => expect(payloads[0]).toMatchObject({
    project: "708К.4", kind: "stage", item_index: "КБ1", value: "Отгружено",
  }));
  fireEvent.change(screen.getByLabelText("Проверка ТК КБ1"), { target: { value: "проверена" } });
  await waitFor(() => expect(payloads[1]).toMatchObject({
    project: "708К.4", kind: "tcReview", item_index: "КБ1", value: "проверена",
  }));
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

it("groups the sheet: on hold on top, active by deadline, finished at the bottom, closed on demand; searches", async () => {
  const groupedBoard: ProductionPlanResult = { ...board, rows: [
    row({ project: "701К.1", customer: "Альфа", days_left: null, deadline: null, group: "active" }),
    row({ project: "702К.1", customer: "Бета", days_left: -2, deadline: "2026-09-20", group: "active" }),
    row({ project: "703К.1", customer: "Гамма", days_left: 5, deadline: "2026-09-27", group: "hold" }),
    row({ project: "704К.1", customer: "Дельта", days_left: 1, deadline: "2026-09-23", group: "active" }),
    row({ project: "705К.1", customer: "Эпсилон", terminal: true, stage: "Отказ", group: "closed" }),
    row({ project: "706К.1", customer: "Зета", stage: "Отгружено", group: "done" }),
  ] };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => String(input) === "/api/production/calendar"
    ? json({ holidays: ["2026-09-24"], absences: [], shift: {} }) : json(groupedBoard));
  render(<ProductionPlanPage principal={principal} />);
  await screen.findByText("Альфа");

  expect(projects()).toEqual(["703К.1", "702К.1", "704К.1", "701К.1", "706К.1"]);
  expect(Array.from(document.querySelectorAll(".zg-group-label")).map((item) => item.textContent)).toEqual([
    "На паузе · 1", "В работе · 3", "ОТК, отгрузка, готово · 1",
  ]);
  // A shop holiday (24.09) is grey like a weekend.
  await waitFor(() => expect(document.querySelectorAll(".zg-table thead th.zg-day-head")[9]?.className).toContain("zg-off"));
  fireEvent.click(screen.getByLabelText("Закрытые проекты · 1"));
  expect(projects()).toContain("705К.1");
  fireEvent.change(screen.getByLabelText("Поиск проекта"), { target: { value: "эпс" } });
  expect(projects()).toEqual(["705К.1"]);
  fireEvent.change(screen.getByLabelText("Поиск проекта"), { target: { value: "нет такого" } });
  expect(screen.getByText("Под этот поиск проектов нет.")).toBeInTheDocument();
});

it("edits sheet cells inline: a checkbox, back to the ERP value, the status, a day note and the deadline", async () => {
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

  fireEvent.click(screen.getByLabelText("Пров компл · 708К.4"));
  await waitFor(() => expect(payloads[0]).toMatchObject({ project: "708К.4", kind: "mark", field: "kit_checked", value: "да" }));
  // Тест С/Б marked by hand «да» over «нет»: unticking returns the ERP value.
  await waitFor(() => expect(screen.getByLabelText("Тест С/Б · 708К.4")).not.toBeDisabled());
  fireEvent.click(screen.getByLabelText("Тест С/Б · 708К.4"));
  await waitFor(() => expect(payloads[1]).toMatchObject({ field: "test_sb", value: "авто" }));

  await waitFor(() => expect(screen.getByLabelText("Тест С/Б · 708К.4")).not.toBeDisabled());
  fireEvent.click(sheetRow("708К.4").querySelector(".zg-col-status")!);
  const dialog = await screen.findByRole("dialog", { name: "Правка: 708К.4" });
  fireEvent.click(dialog.querySelector("label.t-pause input")!);
  fireEvent.click(screen.getByRole("button", { name: "Поставить статус" }));
  await waitFor(() => expect(payloads[2]).toMatchObject({ field: "status", value: "СТОП, В РАБОТЕ, ПАУЗА" }));

  await waitFor(() => expect(screen.getByLabelText("Тест С/Б · 708К.4")).not.toBeDisabled());
  const day = Array.from(sheetRow("708К.4").querySelectorAll(".zg-day"))[10];
  expect(day.textContent).toBe("П");
  fireEvent.click(day);
  fireEvent.click(await screen.findByRole("button", { name: "Убрать отметку" }));
  await waitFor(() => expect(payloads[3]).toMatchObject({ field: "day:2026-09-25", value: "" }));

  await waitFor(() => expect(screen.getByLabelText("Тест С/Б · 708К.4")).not.toBeDisabled());
  fireEvent.click(sheetRow("708К.4").querySelector(".zg-col-deadline")!);
  fireEvent.change(await screen.findByLabelText("Дата дедлайна"), { target: { value: "2026-10-03" } });
  fireEvent.click(screen.getByRole("button", { name: "Поставить дедлайн 03.10.2026" }));
  await waitFor(() => expect(payloads[4]).toEqual({ project: "708К.4", kind: "deadline", blocker: "", text: "", deadline: "2026-10-03" }));
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
  expect(MIN_VISIBLE_DAYS).toBeGreaterThanOrEqual(14);
  expect(autoHiddenColumns(2218, 24)).toEqual([]);
  expect(autoHiddenColumns(1458, 24)).toEqual([]);
  expect(autoHiddenColumns(1192, 24)).toEqual(["days_left", "zkp", "in_plan", "customer_supplied"]);
  expect(autoHiddenColumns(600, 24)).toEqual(["days_left", "zkp", "in_plan", "customer_supplied", "test_sb", "kit_checked"]);
  expect(autoHiddenColumns(0, 24)).toEqual([]);
  expect(groupSheetRows([row({ project: "1", terminal: true, group: undefined }), row({ project: "2", group: undefined })]).closed.map((item) => item.project)).toEqual(["1"]);
  expect(markText({ field: "status", value: "ХОЛД", text: "" })).toBe("Статус: ХОЛД");
  expect(markText({ field: "kit_arrival", value: "2026-10-09", text: "" })).toBe("Дата прихода компл.: 09.10.2026");
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
    expect(load.textContent).toContain("21.09–04.10");
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
    expect(detail.textContent).toContain("ПЗ-0001 · 708К.4КБ1");
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

it("opens the project card from the project number, not a panel under the list", async () => {
  const opened: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(plan));
  render(<ProductionPlanPage principal={principal} openProjectCard={(project) => opened.push(project)} />);
  fireEvent.click(await screen.findByRole("button", { name: "708К.4" }));
  expect(opened).toEqual(["708К.4"]);
  expect(screen.queryByRole("dialog")).toBeNull();
});
