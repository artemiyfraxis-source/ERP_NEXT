import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { EmployeeCardPage } from "./EmployeeCardPage";
import type { EmployeeCard, Principal } from "./api";

const ME = "10000000-0000-0000-0000-000000000001";
const OTHER = "20000000-0000-0000-0000-000000000002";

// Access levels: the level key travels in `roles` and `access_level`; «Сотрудники» belongs to superadmin / admin alone.
function principal(permissions: Principal["permissions"] = {}, level = "operator"): Principal {
  return { user: { id: ME, email: "me@example.test", name: "Иванов Пётр", department: "Монтаж" }, roles: [level], access_level: level, permissions };
}

const FULL: EmployeeCard = {
  as_of: "2026-10-04",
  header: {
    id: ME, name: "Иванов Пётр Сергеевич", department: "Монтаж",
    roles: [{ key: "master", label: "Мастер" }], site: "ЦАР", site_label: "Москва", active: true,
  },
  subordinates: [{ id: OTHER, name: "Сидоров Олег", site: "ЦАР", department: "Монтаж" }],
  projects: [
    { project: "708К.4", roles: ["Исполнитель", "Инициатор заявок"], deadline: "2026-10-20" },
    { project: "512А.1", roles: ["Инициатор заявок"], deadline: null },
  ],
  skills: [
    { operation: "CUT_WIRE", name: "Резка провода", allowed: true, stars: 3, coefficient: "1.25" },
    { operation: "PRS_TERM", allowed: false, stars: 0, coefficient: "1" },
  ],
  absences: {
    upcoming: [
      { kind: "отпуск", from_date: "2026-10-12", to_date: "2026-10-25" },
      { kind: "больничный", from_date: "2026-11-02", to_date: "2026-11-03" },
    ],
    next_vacation: { kind: "отпуск", from_date: "2026-10-12", to_date: "2026-10-25" },
  },
  hours_this_month: { month: "2026-10", label: "октябрь 2026", hours: 126.5, sessions: 14 },
  requests: { count: 12, items: [{ id: "r1", number: "ЗК-101", name: "Провод МГТФ 0,35", status: "В работе" }] },
  tasks: {
    count: 1,
    items: [{
      id: "t1", project: "708К.4", item_index: null, module: "procurement", module_label: "Закупка", kind: "x",
      title: "Согласовать замену терминала", created_at: "2026-10-01T09:00:00+03:00",
    }],
  },
};

const EMPTY: EmployeeCard = {
  as_of: "2026-10-04",
  header: { id: ME, name: "Петров Иван", department: null, roles: [{ key: "staff", label: "Сотрудник" }], site: null, site_label: null, active: true },
  subordinates: [], projects: [], skills: [],
  absences: { upcoming: [], next_vacation: null },
  hours_this_month: { month: "2026-10", label: "октябрь 2026", hours: null, sessions: null },
  requests: { count: null, items: [] },
  tasks: { count: null, items: [] },
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serve(card: EmployeeCard, seen: string[] = []) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    seen.push(path);
    if (path.startsWith("/api/employee-card/")) return json(card);
    throw new Error(`Unexpected request: ${path}`);
  });
  return seen;
}

afterEach(() => vi.restoreAllMocks());

it("shows the own card: header, the «Обзор» tab and the cards in the concept's order", async () => {
  const seen = serve(FULL);
  render(<EmployeeCardPage principal={principal()} />);

  expect(await screen.findByRole("heading", { level: 1, name: "Иванов Пётр Сергеевич" })).toBeInTheDocument();
  expect(seen[0]).toBe("/api/employee-card/me");
  expect(screen.getByText("ИП")).toBeInTheDocument();
  // Нет должности — серая подсказка, а не роль доступа вместо неё; роль — маленькой серой строкой.
  expect(screen.getByText("должность не указана")).toBeInTheDocument();
  expect(screen.getByText("должность не указана").closest("p")).toHaveTextContent("должность не указана · Монтаж");
  expect(screen.getByText("доступ: Мастер")).toBeInTheDocument();
  expect(screen.getByText("Карточка активна")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Обзор" })).toBeInTheDocument();
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Обзор", "Отпуска и заявления"]);
  expect(screen.getByText("Сведения на 04.10.2026")).toBeInTheDocument();

  const titles = screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.firstChild?.textContent);
  expect(titles).toEqual([
    "Основные данные", "Подчинённость", "Проекты", "Отпуска", "Рабочее время", "Компетенции", "Мои заявки на покупку", "Задачи",
  ]);

  const main = screen.getByRole("region", { name: "Основные данные" });
  expect(within(main).getByText("ЦАР · Москва")).toBeInTheDocument();
  expect(within(main).getByText("Площадка")).toBeInTheDocument();
  expect(within(main).getByText("Роль доступа")).toBeInTheDocument();
  expect(within(main).queryByText("Табельный номер")).toBeNull();

  expect(within(screen.getByRole("region", { name: "Подчинённость" })).getByText("Сидоров Олег")).toBeInTheDocument();

  const projects = screen.getByRole("region", { name: "Проекты" });
  expect(within(projects).getByText("Исполнитель · Инициатор заявок")).toBeInTheDocument();
  expect(within(projects).getByText("срок 20.10.2026")).toBeInTheDocument();
  expect(within(projects).getByText("нет данных")).toBeInTheDocument();

  const vacations = screen.getByRole("region", { name: "Отпуска" });
  expect(within(vacations).getByText(/12\.10\.2026 – 25\.10\.2026/)).toBeInTheDocument();
  expect(within(vacations).getByText("Больничный")).toBeInTheDocument();
  expect(within(vacations).queryByText("Отпуск")).toBeNull();

  const hours = screen.getByRole("region", { name: "Рабочее время" });
  expect(within(hours).getByText(/126,5/)).toBeInTheDocument();
  expect(within(hours).getByText(/за октябрь 2026/)).toBeInTheDocument();

  const requests = screen.getByRole("region", { name: "Мои заявки на покупку" });
  expect(within(requests).getByText("ЗК-101")).toBeInTheDocument();
  expect(within(requests).getByText("Показаны первые 1 из 12")).toBeInTheDocument();

  const tasks = screen.getByRole("region", { name: "Задачи" });
  expect(within(tasks).getByText("Согласовать замену терминала")).toBeInTheDocument();
  expect(within(tasks).getByText("Закупка")).toBeInTheDocument();
});

it("names the competences in Russian, the allowed ones first, with the code as a small second line", async () => {
  const seen = serve(FULL);
  render(<EmployeeCardPage principal={principal()} />);
  const skills = await screen.findByRole("region", { name: "Компетенции" });
  expect(await within(skills).findByText("Резка провода")).toBeInTheDocument();
  expect(seen).not.toContain("/api/production/skills");
  // Код без названия: «операция без названия», код — только в подсказке.
  expect(within(skills).getByText("операция без названия")).toHaveAttribute("title", "PRS_TERM");
  expect(within(skills).queryByText("PRS_TERM")).toBeNull();
  expect(within(skills).getByText("CUT_WIRE")).toBeInTheDocument();
  expect(within(skills).getByLabelText("3 из 5")).toBeInTheDocument();
  expect(within(skills).getByText(/×1,25/)).toBeInTheDocument();
  expect(within(skills).getByText("нет допуска")).toBeInTheDocument();
  const rows = within(skills).getAllByRole("listitem").map((row) => row.textContent);
  expect(rows[0]).toContain("Резка провода");
  expect(rows[1]).toContain("нет допуска");
});

it("says «нет данных» for every section the modules gave nothing for, never a zero", async () => {
  serve(EMPTY);
  render(<EmployeeCardPage principal={principal()} />);
  await screen.findByRole("heading", { level: 1, name: "Петров Иван" });

  for (const title of ["Подчинённость", "Проекты", "Отпуска", "Рабочее время", "Компетенции", "Мои заявки на покупку", "Задачи"]) {
    expect(within(screen.getByRole("region", { name: title })).getByText("нет данных")).toBeInTheDocument();
  }
  // Отдела нет — в шапке и в «Основных данных» тоже «нет данных»; площадки нет — «все площадки».
  const main = screen.getByRole("region", { name: "Основные данные" });
  expect(within(main).getByText("нет данных")).toBeInTheDocument();
  expect(within(main).getByText("все площадки")).toBeInTheDocument();
  expect(screen.queryByText(/^0$/)).toBeNull();
  expect(screen.queryByText(/NaN|undefined|null/)).toBeNull();
});

it("tells a real empty list (zero open requests and tasks) from missing data", async () => {
  serve({ ...EMPTY, requests: { count: 0, items: [] }, tasks: { count: 0, items: [] } });
  render(<EmployeeCardPage principal={principal()} />);
  await screen.findByRole("heading", { level: 1, name: "Петров Иван" });
  expect(screen.getByText("Открытых заявок нет")).toBeInTheDocument();
  expect(screen.getByText("Открытых задач нет")).toBeInTheDocument();
});

it("opens another person's card by id with the staff-directory back link and no «Мои заявки»", async () => {
  const seen = serve({ ...FULL, header: { ...FULL.header, id: OTHER, name: "Сидоров Олег" } });
  const openStaff = vi.fn();
  const openMyRequests = vi.fn();
  render(<EmployeeCardPage principal={principal({}, "admin")} userId={OTHER} openStaff={openStaff} openMyRequests={openMyRequests} />);

  expect(await screen.findByRole("heading", { level: 1, name: "Сидоров Олег" })).toBeInTheDocument();
  expect(seen[0]).toBe(`/api/employee-card/${OTHER}`);
  expect(screen.getByRole("region", { name: "Заявки на покупку" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Открыть «Мои заявки»/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "← Сотрудники" }));
  expect(openStaff).toHaveBeenCalled();
});

it("uses the own endpoint when the id is the signed-in person's", async () => {
  const seen = serve(FULL);
  render(<EmployeeCardPage principal={principal()} userId={ME} />);
  await screen.findByRole("heading", { level: 1, name: "Иванов Пётр Сергеевич" });
  expect(seen[0]).toBe("/api/employee-card/me");
});

it("shows «Редактировать» only to superadmin and admin, never by a grant on the staff directory", async () => {
  serve(FULL);
  for (const level of ["commercial", "tech_lead", "foreman", "operator"]) {
    const view = render(<EmployeeCardPage principal={principal({ staffdir: "edit" }, level)} openStaffEdit={vi.fn()} />);
    await screen.findByRole("heading", { level: 1, name: "Иванов Пётр Сергеевич" });
    expect(screen.queryByRole("button", { name: "Редактировать" })).toBeNull();
    view.unmount();
  }
  for (const level of ["superadmin", "admin"]) {
    const view = render(<EmployeeCardPage principal={principal({}, level)} openStaffEdit={vi.fn()} />);
    expect(await screen.findByRole("button", { name: "Редактировать" })).toBeInTheDocument();
    view.unmount();
  }
  render(<EmployeeCardPage principal={principal({}, "admin")} />);
  await screen.findByRole("heading", { level: 1, name: "Иванов Пётр Сергеевич" });
  // Без обработчика открывать нечего — кнопки нет и у администратора.
  expect(screen.queryByRole("button", { name: "Редактировать" })).toBeNull();
});

it("links a subordinate to their card only for superadmin and admin", async () => {
  serve({ ...FULL, subordinates: [{ id: OTHER, name: "Сидоров Олег", site: "ЦАР", department: "Монтаж" }] });
  const openCard = vi.fn();
  const operator = render(<EmployeeCardPage principal={principal({ staffdir: "view" })} openCard={openCard} />);
  await screen.findByText("Сидоров Олег");
  expect(screen.queryByRole("button", { name: "Сидоров Олег" })).toBeNull();
  operator.unmount();
  render(<EmployeeCardPage principal={principal({}, "superadmin")} openCard={openCard} />);
  fireEvent.click(await screen.findByRole("button", { name: "Сидоров Олег" }));
  expect(openCard).toHaveBeenCalledWith(OTHER);
});

it("opens the staff edit form on «Редактировать»", async () => {
  serve(FULL);
  const openStaffEdit = vi.fn();
  render(<EmployeeCardPage principal={principal({}, "admin")} openStaffEdit={openStaffEdit} />);
  fireEvent.click(await screen.findByRole("button", { name: "Редактировать" }));
  expect(openStaffEdit).toHaveBeenCalledWith(ME);
});

it("marks a person who no longer works", async () => {
  serve({ ...FULL, header: { ...FULL.header, active: false } });
  render(<EmployeeCardPage principal={principal()} />);
  expect(await screen.findByText("Не работает")).toBeInTheDocument();
  expect(screen.queryByText("Карточка активна")).toBeNull();
});

it("links projects, tasks and own requests to their screens", async () => {
  serve(FULL);
  const openProject = vi.fn();
  const openMyRequests = vi.fn();
  render(<EmployeeCardPage principal={principal()} openProject={openProject} openMyRequests={openMyRequests} />);
  const projects = await screen.findByRole("region", { name: "Проекты" });
  fireEvent.click(within(projects).getByRole("button", { name: "Карточка проекта 708К.4" }));
  fireEvent.click(within(screen.getByRole("region", { name: "Задачи" })).getByRole("button", { name: "Карточка проекта 708К.4" }));
  expect(openProject).toHaveBeenCalledTimes(2);
  expect(openProject).toHaveBeenCalledWith("708К.4");
  fireEvent.click(screen.getByRole("button", { name: /Открыть «Мои заявки»/ }));
  expect(openMyRequests).toHaveBeenCalled();
});

it("renders the personnel fields only when the card brings them", async () => {
  serve({
    ...FULL,
    personnel: { tab_number: "0147", position: "Мастер участка", category: "Первая", since: "2024-03-01", manager_name: "Кузнецов А. П." },
  });
  render(<EmployeeCardPage principal={principal()} />);
  expect(await screen.findByText("Мастер участка · Монтаж")).toBeInTheDocument();
  // Должность указана — шапка показывает её, роль доступа остаётся отдельной серой строкой.
  expect(screen.queryByText("должность не указана")).toBeNull();
  expect(screen.getByText("доступ: Мастер")).toBeInTheDocument();
  expect(screen.getByText("Категория:")).toBeInTheDocument();
  expect(screen.getAllByText("01.03.2024")).toHaveLength(2);
  const main = screen.getByRole("region", { name: "Основные данные" });
  expect(within(main).getByText("0147")).toBeInTheDocument();
  expect(within(main).getByText("Кузнецов А. П.")).toBeInTheDocument();
});

it("never shows the access role in place of a missing position, even with a blank department", async () => {
  serve({ ...EMPTY, personnel: { tab_number: null, position: "  ", category: null, since: null, manager_name: null } });
  render(<EmployeeCardPage principal={principal()} />);
  await screen.findByRole("heading", { level: 1, name: "Петров Иван" });
  const sub = screen.getByText("должность не указана");
  expect(sub).toHaveClass("ec-nodata");
  expect(sub.closest("p")).toHaveTextContent(/^должность не указана$/);
  expect(screen.getByText("доступ: Сотрудник")).toBeInTheDocument();
});

it("shows the server's refusal instead of an empty card", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ detail: "Чужая карточка открыта только с доступом к разделу «Сотрудники»." }, 403));
  render(<EmployeeCardPage principal={principal()} userId={OTHER} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Чужая карточка открыта только с доступом");
});

it("opens «Отпуска и заявления» on the own card with the count of requests waiting for a decision", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.startsWith("/api/employee-card/")) return json(FULL);
    if (path === "/api/leave-requests/waiting") return json({ count: 2 });
    if (path === "/api/leave-requests") {
      return json({ today: "2026-10-04", balance: { year: 2026, norm: 28, registered: 0, pending: 0, left: 28 }, own: [], inbox: [], journal: null, hr: false });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<EmployeeCardPage principal={principal()} />);

  const tab = await screen.findByRole("tab", { name: /Отпуска и заявления/ });
  expect(await within(tab).findByLabelText("ждут решения: 2")).toHaveTextContent("2");
  fireEvent.click(tab);
  expect(await screen.findByText("Заявлений нет — нажмите «Новое заявление».")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Проекты" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Обзор" }));
  expect(screen.getByRole("region", { name: "Проекты" })).toBeInTheDocument();
});

it("offers no leave tab on someone else's card", async () => {
  serve({ ...FULL, header: { ...FULL.header, id: OTHER } });
  render(<EmployeeCardPage principal={principal({}, "admin")} userId={OTHER} />);
  await screen.findByRole("heading", { level: 1 });
  expect(screen.queryByRole("tab", { name: /Отпуска/ })).not.toBeInTheDocument();
});
