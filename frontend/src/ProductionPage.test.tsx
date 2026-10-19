import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProductionPage, clock, etkRevisionNotice, goodPrefill, workedSeconds } from "./ProductionPage";
import { forgetOpenProject } from "./MasterBoard";
import type { Principal, ProductionOperation, ProductionTask } from "./api";

const principal: Principal = {
  user: {
    id: "10000000-0000-0000-0000-000000000001",
    email: "master@example.test",
    name: "Мастер",
    department: "Производство",
  },
  roles: ["foreman"],
  access_level: "foreman",
  permissions: { production: "edit" },
};

const task: ProductionTask = {
  id: "70000000-0000-0000-0000-000000000001",
  number: "ПЗ-0001",
  version: 2,
  created_at: "2026-09-22T09:00:00+03:00",
  project: "591K.3",
  item: "591K.3.КБ1",
  name: "Жгут",
  quantity: 10,
  fulfillment_request_id: null,
  deadline: "2026-10-01",
  status: "Запущено",
  launched_by: "Мастер",
  planned_start: "2026-09-22T09:00:00+03:00",
  planned_finish: "2026-09-22T09:12:00+03:00",
  assigned: 1,
  operations: [{
    id: "71000000-0000-0000-0000-000000000001",
    position: 1,
    code: "OP-01",
    stage: "Разделка",
    description: "Подготовить кабель",
    seconds_per_unit: "60",
    setup_seconds: "120",
    norm_seconds: 720,
    operator_ids: ["10000000-0000-0000-0000-000000000002"],
    planned_start: "2026-09-22T09:00:00+03:00",
    planned_finish: "2026-09-22T09:12:00+03:00",
    assigned_by: "Мастер",
  }],
};

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.restoreAllMocks(); forgetOpenProject(); });

async function openBoardProject(project: string) {
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(`^${project.replace(/\./g, "\\.")}`) }));
}

function masterFetch(extra: (path: string, init?: RequestInit) => Response | null, tasks: ProductionTask[] = []) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const handled = extra(path, init);
    if (handled) return handled;
    if (path === "/api/production/tasks") return json({ tasks, next_cursor: null });
    if (path === "/api/production/operators") return json({ operators: [
      { id: "10000000-0000-0000-0000-000000000002", name: "Оператор", site: "ЦАР" },
      { id: "10000000-0000-0000-0000-000000000003", name: "Новичок", site: "ЦАР" },
    ] });
    if (path === "/api/production/boxes") return json({ boxes: [] });
    if (path === "/api/production/defects") return json({ defects: [] });
    if (path === "/api/production/instruments") return json({ instruments: [] });
    if (path === "/api/production/qc/rejections") return json({ rejections: [] });
    if (path === "/api/production/calendar") return json({ holidays: [], absences: [] });
    if (path === "/api/production/skills") return json({
      skills: [{ operator_id: "10000000-0000-0000-0000-000000000002", operation_code: "OP-01", allowed: true, stars: 3, coefficient: "1.2" }],
      operation_codes: [{ code: "OP-01", name: "Разделка" }, { code: "OP-02", name: "Сборка" }],
    });
    if (path === "/api/issues") return json({ requests: [], next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });
}

it("launches a task only from an issued techcard picked in the launchable list", async () => {
  let payload: unknown;
  let key = "";
  masterFetch((path, init) => {
    if (path === "/api/production/launchable") return json({ items: [{
      techcard_id: "80000000-0000-0000-0000-000000000001", project: "591K.3", index: "КБ1", item: "591K.3КБ1",
      name: "Жгут", customer: "", run: "25", deadline: null, launched: 10, operations: 3,
    }, {
      techcard_id: "80000000-0000-0000-0000-000000000002", project: "708К.4", index: "КБ2", item: "708К.4КБ2",
      name: "Кабель", customer: "", run: "5", deadline: null, launched: 0, operations: 1,
    }] });
    if (path === "/api/production/tasks" && init?.method === "POST") {
      payload = JSON.parse(String(init.body));
      key = new Headers(init.headers).get("Idempotency-Key") ?? "";
      return json({ task });
    }
    return null;
  });

  render(<ProductionPage principal={principal} />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Запустить задание" }));
  expect(await screen.findByText("запущено 10")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Поиск сборки"), { target: { value: "кабель" } });
  expect(screen.queryByText("Жгут")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Поиск сборки"), { target: { value: "591" } });
  fireEvent.click(screen.getByRole("button", { name: /КБ1/ }));
  expect(screen.getByLabelText("Кол-во")).toHaveValue("25");
  fireEvent.change(screen.getByLabelText("Кол-во"), { target: { value: "12" } });
  // Задание встаёт в работу сегодня — дата на кнопке (РП-5).
  fireEvent.click(screen.getByRole("button", { name: /^Запустить сегодня, \d\d\.\d\d$/ }));

  await waitFor(() => expect(payload).toEqual({
    techcard_id: "80000000-0000-0000-0000-000000000001",
    quantity: 12,
    deadline: null,
    fulfillment_request_id: null,
  }));
  expect(key).not.toBe("");
});

it("shows tasks on a Gantt, operation fact, and assigns with a server preview and admission tags", async () => {
  const bodies: unknown[] = [];
  const withFact: ProductionTask = { ...task, operations: [{
    ...task.operations[0], fact: { good: "6", defect: "1", processed: "7" }, state: "в работе", wip: { ok: "4", closed: "2" },
  }] };
  masterFetch((path, init) => {
    if (path.endsWith("/assign") && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      bodies.push(body);
      return json({ task: withFact, warnings: [], dry: body.dry, plan: {
        start: "2026-09-22T09:00:00+03:00", finish: "2026-09-22T10:00:00+03:00",
        warnings: body.operator_ids.length > 1 ? ["Новичок: нет допуска к операции OP-01"] : [],
      } });
    }
    return null;
  }, [withFact]);

  render(<ProductionPage principal={principal} />);
  await openBoardProject("591K.3");
  fireEvent.click(await screen.findByRole("button", { name: /ПЗ-0001/ }));
  expect(await screen.findByText(/сдано 6 из 10, брак 1 · в коробках 4 \+ 2 ждут ОТК/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Разделка/ }));
  expect(await screen.findByText("нет допуска", { exact: false })).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("Назначить Новичок"));
  expect(await screen.findByText("Новичок: нет допуска к операции OP-01", {}, { timeout: 2000 })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Назначить" }));

  await waitFor(() => expect(bodies.at(-1)).toEqual({
    version: 2,
    operator_ids: ["10000000-0000-0000-0000-000000000002", "10000000-0000-0000-0000-000000000003"],
    requested_start: expect.stringMatching(/^2026-09-22T/),
    dry: false,
  }));
  expect(bodies.some((body) => (body as { dry: boolean }).dry)).toBe(true);
});

it("keeps «—» in the competency matrix: only set codes are saved", async () => {
  let payload: unknown;
  masterFetch((path, init) => {
    if (path.startsWith("/api/production/skills/") && init?.method === "PUT") {
      payload = JSON.parse(String(init.body));
      return new Response(null, { status: 204 });
    }
    return null;
  });

  render(<ProductionPage principal={principal} />);
  fireEvent.click(await screen.findByRole("button", { name: "Компетенции" }));
  expect(await screen.findByLabelText("Допуск OP-02")).toHaveValue("");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

  await waitFor(() => expect(payload).toEqual({ skills: [
    { operation_code: "OP-01", allowed: true, stars: 3, coefficient: "1.2" },
  ] }));
});

it("saves shop holidays through the production calendar", async () => {
  let payload: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/production/tasks") return json({ tasks: [], next_cursor: null });
    if (path === "/api/production/operators") return json({ operators: [] });
    if (path === "/api/production/boxes") return json({ boxes: [] });
    if (path === "/api/production/defects") return json({ defects: [] });
    if (path === "/api/production/instruments") return json({ instruments: [] });
    if (path === "/api/production/qc/rejections") return json({ rejections: [] });
    if (path === "/api/production/calendar" && init?.method === "PUT") {
      payload = JSON.parse(String(init.body));
      return new Response(null, { status: 204 });
    }
    if (path === "/api/production/calendar") return json({ holidays: [], absences: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProductionPage principal={principal} />);
  fireEvent.click(await screen.findByRole("button", { name: "Календарь цеха" }));
  fireEvent.change(await screen.findByLabelText("Нерабочий день"), { target: { value: "2026-11-04" } });
  fireEvent.click(screen.getByRole("button", { name: "Добавить праздник" }));
  fireEvent.click(screen.getByRole("button", { name: "Сохранить календарь" }));

  await waitFor(() => expect(payload).toEqual({
    holidays: ["2026-11-04"], absences: [],
    shift: {
      weekdays: [0, 1, 2, 3, 4], start: "09:00", finish: "17:30",
      breaks: [["13:00", "13:30"]], effective_hours: "7",
    },
  }));
});

it("lays the shop calendar out in two blocks and lists what is saved with a way to remove it", async () => {
  let payload: { holidays: string[]; absences: Array<{ operator_id: string; from_date: string; to_date: string; kind: string }>; shift: { weekdays: number[] } } | undefined;
  const absence = { id: "78000000-0000-0000-0000-000000000001", operator_id: "10000000-0000-0000-0000-000000000002", from_date: "2026-10-05", to_date: "2026-10-09", kind: "Отпуск" };
  masterFetch((path, init) => {
    if (path === "/api/production/calendar" && init?.method === "PUT") {
      payload = JSON.parse(String(init.body));
      return new Response(null, { status: 204 });
    }
    return path === "/api/production/calendar" ? json({ holidays: ["2026-11-04", "2026-10-12"], absences: [absence], shift: null }) : null;
  });
  render(<ProductionPage principal={principal} />);
  fireEvent.click(await screen.findByRole("button", { name: "Календарь цеха" }));

  const shiftBlock = await screen.findByRole("region", { name: "Смена" });
  const offDays = screen.getByRole("region", { name: "Нерабочие дни и отсутствия" });
  expect(within(shiftBlock).getByRole("heading", { name: "Смена" })).toBeInTheDocument();
  expect(within(offDays).getByRole("heading", { name: "Нерабочие дни и отсутствия" })).toBeInTheDocument();
  expect(screen.queryByText(/Чистое время — без обеда/)).not.toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Сохранить календарь" })).toHaveLength(1);

  // Сохранённое видно по-русски и по порядку дат, у каждого — своё «убрать».
  await waitFor(() => expect(within(offDays).getByRole("button", { name: "Удалить нерабочий день 12.10.2026" })).toBeInTheDocument());
  const chips = Array.from(offDays.querySelectorAll(".holiday-chip")).map((chip) => chip.textContent);
  expect(chips).toEqual(["12.10.2026×", "04.11.2026×"]);
  const row = within(offDays).getByRole("row", { name: /Оператор.*Отпуск/ });
  expect(within(row).getByText("05.10.2026")).toBeInTheDocument();
  expect(within(row).getByText("09.10.2026")).toBeInTheDocument();

  // «Добавить» активна, только когда поля заполнены.
  const addHoliday = within(offDays).getByRole("button", { name: "Добавить праздник" });
  const addAbsence = within(offDays).getByRole("button", { name: "Добавить отсутствие" });
  expect(addHoliday).toBeDisabled();
  expect(addAbsence).toBeDisabled();
  fireEvent.change(within(offDays).getByLabelText("С"), { target: { value: "2026-10-20" } });
  expect(addAbsence).toBeEnabled();
  fireEvent.click(addAbsence);
  expect(within(offDays).getAllByRole("row")).toHaveLength(3);

  // Рабочие дни — переключатели: суббота включается, понедельник выключается.
  const saturday = within(shiftBlock).getByRole("button", { name: "Сб" });
  expect(saturday).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(saturday);
  fireEvent.click(within(shiftBlock).getByRole("button", { name: "Пн" }));
  expect(saturday).toHaveAttribute("aria-pressed", "true");
  expect(within(shiftBlock).getByRole("button", { name: "Пн" })).toHaveAttribute("aria-pressed", "false");

  fireEvent.click(within(offDays).getByRole("button", { name: "Удалить нерабочий день 12.10.2026" }));
  fireEvent.click(within(offDays).getByRole("button", { name: /Удалить отсутствие: Оператор, 05.10.2026/ }));
  fireEvent.click(screen.getByRole("button", { name: "Сохранить календарь" }));

  await waitFor(() => expect(payload).toBeDefined());
  expect(payload?.holidays).toEqual(["2026-11-04"]);
  expect(payload?.absences).toEqual([{ operator_id: absence.operator_id, from_date: "2026-10-20", to_date: "2026-10-20", kind: "Отпуск" }]);
  expect(payload?.shift.weekdays).toEqual([1, 2, 3, 4, 5]);
});

it("shows a registered leave request in the calendar without a way to remove it and never saves it back", async () => {
  let payload: { absences: unknown[] } | undefined;
  const leave = {
    id: "78000000-0000-0000-0000-000000000002", operator_id: "10000000-0000-0000-0000-000000000002",
    from_date: "2026-10-12", to_date: "2026-10-25", kind: "Отпуск", leave_request_id: "79000000-0000-0000-0000-000000000001",
  };
  masterFetch((path, init) => {
    if (path === "/api/production/calendar" && init?.method === "PUT") {
      payload = JSON.parse(String(init.body));
      return new Response(null, { status: 204 });
    }
    return path === "/api/production/calendar" ? json({ holidays: [], absences: [leave], shift: null }) : null;
  });
  render(<ProductionPage principal={principal} />);
  fireEvent.click(await screen.findByRole("button", { name: "Календарь цеха" }));

  const offDays = await screen.findByRole("region", { name: "Нерабочие дни и отсутствия" });
  const row = await within(offDays).findByRole("row", { name: /Отпуск.*по заявлению/ });
  expect(within(row).queryByRole("button")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Сохранить календарь" }));
  await waitFor(() => expect(payload).toBeDefined());
  expect(payload?.absences).toEqual([]);
});

it("starts the first operation from an issued GRN and package", async () => {
  let payload: unknown;
  const operatorPrincipal: Principal = {
    user: { id: "10000000-0000-0000-0000-000000000002", email: "operator@example.test", name: "Оператор", department: "Производство" },
    roles: ["operator"], access_level: "operator", permissions: { myops: "edit" },
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/production/my") return json({ tasks: [task] });
    if (path === "/api/production/my/execution") return json({ session: null, boxes: [] });
    if (path.endsWith("/inputs")) return json({ boxes: [], materials: [{
      fulfillment_line_id: "72000000-0000-0000-0000-000000000001",
      article: "WIRE-1", name: "Провод", unit: "м", grn: "PPM.26.0000001",
      package: "П1", issued: "10", consumed: "0", reserved: "0", available: "10",
    }] });
    if (path.endsWith("/work/start") && init?.method === "POST") {
      payload = JSON.parse(String(init.body));
      return json({ session: {} });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProductionPage principal={operatorPrincipal} mine />);
  expect(await screen.findByText("норма 0,2 ч на 10 шт (подготовка 2 мин + 1 мин на изделие)")).toBeInTheDocument();
  expect(screen.queryByText("Ещё не сдавали")).not.toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Начать" }));
  fireEvent.change(await screen.findByLabelText("Количество из партии PPM.26.0000001 П1"), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "Начать работу" }));

  await waitFor(() => expect(payload).toEqual({
    inputs: [],
    materials: [{ fulfillment_line_id: "72000000-0000-0000-0000-000000000001", quantity: "3" }],
  }));
});

it("corrects one output of a returned box and shows that it waits for the master again", async () => {
  const requests: Array<{ path: string; body: unknown }> = [];
  const operatorPrincipal: Principal = {
    user: { id: "10000000-0000-0000-0000-000000000002", email: "operator@example.test", name: "Оператор", department: "Производство" },
    roles: ["operator"], access_level: "operator", permissions: { myops: "edit" },
  };
  const returned = {
    id: "74000000-0000-0000-0000-000000000002", number: "000002-6", version: 4,
    task_id: task.id, operation_id: task.operations[0].id, item: task.item, operation_position: 2,
    operator_id: operatorPrincipal.user.id, quantity: "10", consumed: "0", reserved: "0", rejected: "0",
    available: "10", status: "Возвращена", created_at: "2026-09-22T09:00:00+03:00",
    closed_at: null, closed_by: null, confirmed_at: null, confirmed_by: null, return_reason: "Пересчитать",
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/production/my") return json({ tasks: [task] });
    if (path === "/api/production/my/execution") return json({ session: null, boxes: [returned] });
    if (path.endsWith("/inputs")) return json({ boxes: [], materials: [] });
    if (path === `/api/production/boxes/${returned.id}/outputs`) return json({ outputs: [{
      id: "75000000-0000-0000-0000-000000000001", good_quantity: "10", defect_quantity: "0",
      finished_at: "2026-09-22T10:00:00+03:00", correction_of_id: null,
    }] });
    if (init?.method === "POST") {
      requests.push({ path, body: JSON.parse(String(init.body)) });
      return json({ box: returned });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProductionPage principal={operatorPrincipal} mine />);
  fireEvent.click(await screen.findByRole("button", { name: "Исправить" }));
  const good = await screen.findByLabelText("Годные в коробке 000002-6");
  await waitFor(() => expect(good).toHaveValue(10));
  fireEvent.change(good, { target: { value: "8" } });
  fireEvent.click(screen.getByRole("button", { name: "Записать исправление" }));
  await waitFor(() => expect(requests).toEqual([
    {
      path: `/api/production/boxes/${returned.id}/correct`,
      body: {
        version: 4, output_id: "75000000-0000-0000-0000-000000000001",
        good_quantity: "8", defect_quantity: "0", defect_character: "", defect_description: "",
      },
    },
  ]));
  const dialog = await screen.findByRole("dialog", { name: "Коробка исправлена — подпишите заново" });
  expect(dialog).toHaveTextContent("000002-6");
  expect(screen.queryByRole("button", { name: "Доложить" })).not.toBeInTheDocument();
});

it("starts a later stage by typed box numbers and shows the new box code to write on it", async () => {
  const requests: Array<{ path: string; body: unknown }> = [];
  const operatorPrincipal: Principal = {
    user: { id: "10000000-0000-0000-0000-000000000002", email: "operator@example.test", name: "Оператор", department: "Производство" },
    roles: ["operator"], access_level: "operator", permissions: { myops: "edit" },
  };
  const later = { ...task, operations: [{ ...task.operations[0], position: 2 }] };
  const session = {
    id: "76000000-0000-0000-0000-000000000001", task_id: later.id, operation_id: later.operations[0].id,
    operator_id: operatorPrincipal.user.id, status: "В работе", started_at: "2026-09-22T09:00:00+03:00",
    paused_at: null, pause_seconds: 0, finished_at: null, version: 1,
  };
  let current: typeof session | null = null;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/production/my") return json({ tasks: [later] });
    if (path === "/api/production/my/execution") return json({ session: current, boxes: [] });
    if (path.endsWith("/inputs")) return json({ boxes: [], materials: [] });
    if (init?.method === "POST") {
      requests.push({ path, body: JSON.parse(String(init.body)) });
      if (path.endsWith("/work/start")) { current = session; return json({ session }); }
      current = null;
      return json({ session, box: {
        id: "74000000-0000-0000-0000-000000000009", number: "000007-5", code: "180К.11·КБ1·22.09·ОП01·3",
        task_id: later.id, operation_id: later.operations[0].id, item: "КБ1", operation_position: 2, quantity: "5", closed_at: "2026-09-22T10:00:00+03:00",
      }, output_id: "x", defect: null });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProductionPage principal={operatorPrincipal} mine />);
  fireEvent.click(await screen.findByRole("button", { name: "Начать" }));
  fireEvent.change(await screen.findByLabelText("Номера коробок"), { target: { value: "000001-8, 000002-6\n" } });
  fireEvent.click(screen.getByRole("button", { name: "Начать работу" }));
  fireEvent.click(await screen.findByRole("button", { name: "Сдать…" }));
  fireEvent.change(await screen.findByLabelText("Годные изделия"), { target: { value: "5" } });
  fireEvent.click(screen.getByRole("button", { name: "Сдать" }));

  const dialog = await screen.findByRole("dialog", { name: "Подпишите коробку" });
  expect(within(dialog).getByLabelText("Код коробки")).toHaveTextContent("180К.11·КБ1·22.09·ОП01·3");
  expect(dialog).toHaveTextContent("В коробке 5 шт");
  expect(dialog).toHaveTextContent("№ учёта 000007-5");
  expect(within(dialog).getByRole("button", { name: "Печать этикетки" })).toBeEnabled();
  expect(requests[0].body).toEqual({ inputs: ["000001-8", "000002-6"], materials: [] });
  fireEvent.click(screen.getByRole("button", { name: "Подписал" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("groups the operator queue by plan day and hides start for a completed operation", async () => {
  const operatorPrincipal: Principal = {
    user: { id: "10000000-0000-0000-0000-000000000002", email: "operator@example.test", name: "Оператор", department: "Производство" },
    roles: ["operator"], access_level: "operator", permissions: { myops: "edit" },
  };
  const now = new Date();
  const at = (days: number, hour: number) => new Date(now.getFullYear(), now.getMonth(), now.getDate() + days, hour).toISOString();
  const done: ProductionTask = { ...task, id: "70000000-0000-0000-0000-000000000009", number: "ПЗ-0009", operations: [{
    ...task.operations[0], planned_start: at(0, 9), planned_finish: at(0, 23),
    fact: { good: "10", defect: "0", processed: "10" }, state: "выполнена", mates: ["Мастер"],
  }] };
  const next: ProductionTask = { ...task, operations: [{
    ...task.operations[0], planned_start: at(1, 9), planned_finish: at(1, 10), state: "не начата", mates: [],
  }] };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/production/my") return json({ tasks: [done, next] });
    if (path === "/api/production/my/execution") return json({ session: null, boxes: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProductionPage principal={operatorPrincipal} mine />);

  expect(await screen.findByText(/^Сегодня · /)).toBeInTheDocument();
  expect(screen.getByText(/^Завтра · /)).toBeInTheDocument();
  expect(screen.getByText(/вместе с: Мастер/)).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Начать" })).toHaveLength(1);
  // Сделанное свёрнуто под невыполненным: «Начать» не ищут среди готового.
  expect(screen.getByText("Выполнено · 1")).toBeInTheDocument();
  expect(screen.getByText(/^Сегодня · /).closest("details")).not.toBeNull();
  expect(screen.getByText(/^Завтра · /).closest("details")).toBeNull();
});

it("lets the master hand out operations of issued projects to installers and launch a ready card", async () => {
  const bodies: Array<{ path: string; body: unknown }> = [];
  const open: ProductionTask = { ...task, assigned: 0, operations: [
    { ...task.operations[0], operator_ids: [] },
    { ...task.operations[0], id: "71000000-0000-0000-0000-000000000002", position: 2, stage: "Обжим", code: "OP-02", operator_ids: ["10000000-0000-0000-0000-000000000002"],
      planned_start: "2026-09-22T10:00:00", planned_finish: "2026-09-22T14:00:00" },
  ] };
  vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-09-22T08:00:00"));
  masterFetch((path, init) => {
    if (path === "/api/production/operators") return json({ operators: [
      { id: "10000000-0000-0000-0000-000000000002", name: "Рыбкина Ольга", site: "ЦАР", department: "Монтаж" },
      { id: "10000000-0000-0000-0000-000000000003", name: "Попов Игнат", site: "ЦАР", department: "Монтаж" },
    ] });
    if (path === "/api/techcards/kitsets?sort=upd") return json({ statuses: ["Новый", "ТК готова", "В работе", "Отгружено"], items: [{
      project: "591K.3", customer: "ООО «Тест»", calc_source: "", updated_at: "2026-09-28T15:19:00+03:00", updated_by: "",
      assemblies: 1, lines: 7, kbs: ["КБ1"], status: "В работе", blocker: "",
      kb_stages: [{ index: "КБ1", status: "В работе", tc: true, kit: false, review: null }],
    }] });
    if (path === "/api/production/launchable") return json({ items: [{
      techcard_id: "80000000-0000-0000-0000-000000000009", project: "708К.4", index: "КБ2", item: "708К.4КБ2",
      name: "Кабель", customer: "", run: "5", deadline: "2026-10-10", launched: 0, operations: 4,
    }] });
    if (init?.method === "POST") {
      bodies.push({ path, body: JSON.parse(String(init.body)) });
      return json({ task: open, warnings: [], plan: { start: null, finish: null, warnings: [] }, dry: false });
    }
    return null;
  }, [open]);

  render(<ProductionPage principal={principal} />);
  expect(await screen.findByRole("region", { name: "Проекты и операции" })).toBeInTheDocument();
  expect(await screen.findByText("Рыбкина Ольга", { selector: ".mb-week th b" })).toBeInTheDocument();
  // Окно 10:00–14:00 без обеда 13:00–13:30 — 3,5 ч на часах; чистых 7 ч из 8 ч смены: 3,5 × 7 / 8 ≈ 3,1 ч.
  expect(screen.getByTitle("Рыбкина Ольга, 22.09: 3,1 из 7 ч")).toHaveTextContent("3,1/7");
  expect(screen.getByTitle("Попов Игнат, 22.09: 0 из 7 ч")).toBeInTheDocument();
  expect(screen.getByText("без исполнителя: 1")).toBeInTheDocument();
  expect(screen.getByText("без исполнителя 1 из 2")).toBeInTheDocument();
  expect(await screen.findByText("без задания 1")).toBeInTheDocument(); // счётчик запуска приходит отдельным запросом
  expect(screen.getByText("ООО «Тест»")).toBeInTheDocument();
  expect(screen.getByText("ТК на все КБ")).toBeInTheDocument();

  await openBoardProject("591K.3");
  fireEvent.click(screen.getByRole("button", { name: /Разделка/ }));
  expect(screen.getByText("Обжим")).toBeInTheDocument();
  expect(screen.getByText("22.09 10:00–14:00")).toBeInTheDocument();
  // Свободный день — пустая ячейка, как в «Загрузке по людям» плана (РП-4).
  expect(screen.getByTitle("Попов Игнат, 22.09: 0 из 7 ч").textContent).toBe("");
  // Клик по дню — раскладка по часам: у Рыбкиной 10:00–14:00 занято, выбор часа ставит операцию ровно с него.
  fireEvent.click(screen.getByRole("button", { name: "День Рыбкина Ольга 22.09" }));
  expect(screen.getByTitle("11:00–12:00: Обжим · 591K.3 · КБ1")).toBeInTheDocument();
  // Занятый час не принимает вторую операцию: на нём нет кнопки, свободные — есть. «Назначить на день» ставит после
  // работы этого дня (ничего не сдвигая) и показывает, до скольких: 10 шт × 60 с / 1,2 + 120 с на 8/7 → 12 мин.
  expect(screen.queryByRole("button", { name: "Назначить Рыбкина Ольга на 22.09 с 11:00" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Назначить на 22.09 с 14:00" })).toBeInTheDocument();
  expect(screen.getByText("до 14:12")).toBeInTheDocument();
  // Дробление: штук монтажнику — по умолчанию все 10; Попову ставим 4 с обеда — начало сдвигается на конец обеда.
  expect(screen.getByLabelText("Штук монтажнику")).toHaveValue(10);
  fireEvent.change(screen.getByLabelText("Штук монтажнику"), { target: { value: "4" } });
  fireEvent.click(screen.getByRole("button", { name: "День Попов Игнат 23.09" }));
  fireEvent.click(screen.getByRole("button", { name: "Назначить Попов Игнат на 23.09 с 13:30" }));
  await waitFor(() => expect(bodies.at(-1)).toEqual({
    path: `/api/production/tasks/${open.id}/operations/${open.operations[0].id}/share`,
    body: { version: 2, operator_id: "10000000-0000-0000-0000-000000000003", quantity: 4, requested_start: "2026-09-23T13:30" },
  }));
  // Снять — крестиком на исполнителе, без отдельной кнопки у каждого; сначала окно с тем, что изменится (16.10).
  const posted = bodies.length;
  fireEvent.click(screen.getByRole("button", { name: "Снять Рыбкина Ольга" }));
  const confirm = await screen.findByRole("dialog", { name: "Снять Рыбкина Ольга с этапа" });
  expect(bodies).toHaveLength(posted);
  expect(within(confirm).getByText("План Рыбкина Ольга 22.09 10:00–14:00 освободится.")).toBeInTheDocument();
  expect(within(confirm).getByText("Этап останется без исполнителя — его нужно будет назначить заново.")).toBeInTheDocument();
  fireEvent.click(within(confirm).getByRole("button", { name: "Отмена" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(bodies).toHaveLength(posted);
  fireEvent.click(screen.getByRole("button", { name: "Снять Рыбкина Ольга" }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Снять Рыбкина Ольга" }));
  await waitFor(() => expect(bodies.at(-1)).toEqual({
    path: `/api/production/tasks/${open.id}/operations/${open.operations[1].id}/share`,
    body: { version: 2, operator_id: "10000000-0000-0000-0000-000000000002", remove: true },
  }));

  // «Назначить только …» (16.10): весь этап одному монтажнику одним нажатием, ближайший рабочий день и час — на кнопке.
  fireEvent.click(screen.getByRole("button", { name: "Обжим" }));
  const only = screen.getByRole("group", { name: "Весь этап одному монтажнику" });
  expect(within(only).getByLabelText("Монтажник на весь этап")).toHaveValue("10000000-0000-0000-0000-000000000002");
  fireEvent.change(within(only).getByLabelText("Монтажник на весь этап"), { target: { value: "10000000-0000-0000-0000-000000000003" } });
  expect(within(only).getByLabelText("День начала")).toHaveValue("2026-09-22");
  expect(within(only).getByText("Остальные исполнители будут сняты — все 10 шт получит Попов Игнат.")).toBeInTheDocument();
  fireEvent.click(within(only).getByRole("button", { name: "Назначить только Попов Игнат на 22.09 с 09:00" }));
  await waitFor(() => expect(bodies.at(-1)).toEqual({
    path: `/api/production/tasks/${open.id}/operations/${open.operations[1].id}/share`,
    body: { version: 2, operator_id: "10000000-0000-0000-0000-000000000003", quantity: 10, requested_start: "2026-09-22T09:00" },
  }));
  // Другой день — кнопка говорит его.
  fireEvent.change(within(only).getByLabelText("День начала"), { target: { value: "2026-09-24" } });
  expect(within(only).getByRole("button", { name: "Назначить только Попов Игнат на 24.09 с 09:00" })).toBeInTheDocument();

  fireEvent.click(await screen.findByRole("button", { name: "← Все проекты" }));
  await openBoardProject("708К.4");
  // КБ без задания — справкой, без кнопки «Создать задание» (владелец 19.10): задания создаёт автоплан.
  expect(await screen.findByText(/техкарта выдана, задания ещё нет/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Создать задание" })).toBeNull();
  vi.useRealTimers();
});

it("hands a task to ZHUK whole and shows the ZHUK master no hourly grid", async () => {
  // Владелец, 30.09: ЖУК работает не нормированно — задание отдаётся площадке целиком, без людей и часов.
  let sent: unknown;
  masterFetch((path, init) => {
    if (path === "/api/production/operators") return json({ viewer_site: "ЖУК", operators: [] });
    if (path === "/api/production/launchable") return json({ items: [] });
    if (path === `/api/production/tasks/${task.id}/handover`) {
      sent = JSON.parse(String(init?.body));
      return json({ task: { ...task, handed_to: "ЖУК" } });
    }
    return null;
  }, [task]);

  render(<ProductionPage principal={principal} />);
  expect(await screen.findByText(/ЖУК работает без раскладки по людям и часам/)).toBeInTheDocument();
  expect(document.querySelector(".mb-week")).toBeNull();
  await openBoardProject("591K.3");
  fireEvent.click(await screen.findByRole("button", { name: "Отдать в ЖУК" }));
  await waitFor(() => expect(sent).toEqual({ version: task.version, site: "ЖУК" }));
});

// Сетевой график (ARCH-TRACK-2, B4): две нарезки идут в сборку, сборка — финал; вторая нарезка режет по 2 на изделие.
const cutA = "71000000-0000-0000-0000-0000000000a1";
const cutB = "71000000-0000-0000-0000-0000000000b2";
const assembly = "71000000-0000-0000-0000-0000000000c3";
const stageOp = (id: string, position: number, stage: string, extra: Partial<ProductionOperation>): ProductionOperation => ({
  ...task.operations[0], id, position, stage, code: `OP-0${position}`, operator_ids: [], planned_start: null, planned_finish: null,
  ...extra,
});
const branched: ProductionTask = { ...task, assigned: 0, operations: [
  stageOp(cutA, 1, "Нарезка А", { inputs: [], first: true, final: false, required_quantity: "10" }),
  stageOp(cutB, 2, "Нарезка Б", { inputs: [], first: true, final: false, required_quantity: "20", fact: { good: "5", defect: "0", processed: "5" }, state: "в работе" }),
  stageOp(assembly, 3, "Сборка", {
    inputs: [
      { operation_id: cutA, position: 1, per_unit: "1", transfer_batch: null },
      { operation_id: cutB, position: 2, per_unit: "2", transfer_batch: null },
    ],
    first: false, final: true, required_quantity: "10",
  }),
] };
const FEEDS = "из этапов 1 «Нарезка А», 2 «Нарезка Б» ×2";
const operatorPrincipal: Principal = {
  user: { id: "10000000-0000-0000-0000-000000000002", email: "operator@example.test", name: "Оператор", department: "Производство" },
  roles: ["operator"], access_level: "operator", permissions: { myops: "edit" },
};

it("shows on the task Gantt where the assembly takes its parts from, and how many each branch must hand in", async () => {
  masterFetch(() => null, [branched]);
  render(<ProductionPage principal={principal} />);
  await openBoardProject("591K.3");
  fireEvent.click(await screen.findByRole("button", { name: /ПЗ-0001/ }));

  // «Из этапов …» — только на сборке; у нарезок (начало веток) этой строки нет.
  expect(await screen.findAllByText(FEEDS)).toHaveLength(1);
  // Нарезка Б сдаёт 20 по заданию на 10 изделий, а не тираж.
  expect(screen.getByText(/сдано 5 из 20/)).toBeInTheDocument();
  expect(screen.queryByText(/из 10/)).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /^2 Нарезка Б/ }));
  expect(await screen.findByText(/591K\.3 · КБ1 × 10 · сдать 20 шт/)).toBeInTheDocument();
});

it("keeps a chain task as it was: no «из этапов», the second stage reads «с этапа 1»", async () => {
  const chain: ProductionTask = { ...task, operations: [
    stageOp(cutA, 1, "Резка", { inputs: [], first: true, final: false, required_quantity: "10" }),
    stageOp(cutB, 2, "Обжим", {
      operator_ids: ["10000000-0000-0000-0000-000000000002"],
      inputs: [{ operation_id: cutA, position: 1, per_unit: "1", transfer_batch: null }], first: false, final: true, required_quantity: "10",
    }),
  ] };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/production/my") return json({ tasks: [{ ...chain, operations: [chain.operations[1]] }] });
    if (path === "/api/production/my/execution") return json({ session: null, boxes: [] });
    if (path.endsWith("/inputs")) return json({ boxes: [], materials: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<ProductionPage principal={operatorPrincipal} mine />);
  fireEvent.click(await screen.findByRole("button", { name: "Начать" }));
  expect(await screen.findByText(/с этапа 1, сданные/)).toBeInTheDocument();
  expect(screen.queryByText(/из этапов/)).not.toBeInTheDocument();
  expect(screen.queryByText(/сдать/)).not.toBeInTheDocument();
});

it("tells the fitter of the assembly which stages the boxes come from, and lets a branch start take materials", async () => {
  // «Мои операции» отдают задание с одной операцией — названия входов в нём лежат в самих входах (`stage`).
  const named = (operation: ProductionOperation): ProductionOperation => ({
    ...operation,
    inputs: operation.inputs?.map((input) => ({ ...input, stage: branched.operations.find((item) => item.id === input.operation_id)?.stage })),
  });
  const mineTask = (operation: ProductionOperation): ProductionTask => ({ ...branched, operations: [{ ...named(operation), operator_ids: [operatorPrincipal.user.id] }] });
  let mine = mineTask(branched.operations[2]);
  const box = (number: string, position: number, operationId: string) => ({
    id: `74000000-0000-0000-0000-00000000000${position}`, number, version: 1, task_id: branched.id, operation_id: operationId, item: branched.item,
    operation_position: position, operator_id: "x", quantity: "5", consumed: "0", reserved: "0", rejected: "0", available: "5",
    status: "Подтверждена", created_at: "2026-09-22T09:00:00+03:00", closed_at: null, closed_by: null, confirmed_at: null, confirmed_by: null, return_reason: "",
  });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/production/my") return json({ tasks: [mine] });
    if (path === "/api/production/my/execution") return json({ session: null, boxes: [] });
    if (path.endsWith("/inputs")) return json(mine.operations[0].id === assembly
      ? { boxes: [box("000001-8", 1, cutA), box("000002-6", 2, cutB)], materials: [] }
      : { boxes: [], materials: [{ fulfillment_line_id: "72000000-0000-0000-0000-000000000001", article: "WIRE-1", name: "Провод", unit: "м", grn: "PPM.26.0000001", package: "П1", issued: "10", consumed: "0", reserved: "0", available: "10" }] });
    throw new Error(`Unexpected request: ${path}`);
  });

  // Сборка: коробки с нескольких этапов, штуки на изделие — в карточке и в форме старта.
  const { unmount } = render(<ProductionPage principal={operatorPrincipal} mine />);
  expect(await screen.findByText(new RegExp(FEEDS))).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Начать" }));
  expect(await screen.findByText(new RegExp(`${FEEDS}; сданные`))).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: /000001-8.*5 шт · этап 1/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /000002-6.*5 шт · этап 2/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Ввести код вручную" }));
  expect(screen.getByLabelText("Номера коробок")).toBeInTheDocument();
  unmount();

  // Вторая нарезка стоит под номером 2, но входов у неё нет: коробок не просит, берёт материалы комплекта.
  mine = mineTask(branched.operations[1]);
  render(<ProductionPage principal={operatorPrincipal} mine />);
  fireEvent.click(await screen.findByRole("button", { name: "Начать" }));
  expect(await screen.findByLabelText("Количество из партии PPM.26.0000001 П1")).toBeInTheDocument();
  expect(screen.queryByLabelText("Номера коробок")).not.toBeInTheDocument();
  expect(screen.getByText(/сдать 20 шт/)).toBeInTheDocument();
});

// Owner 04.10.2026: «почему эти два блока вообще тут» — очередь ОТК, брак и средства измерения живут в разделе «ОТК».
// Owner 13.10.2026: «ОТК подтверждает и все» — «Коробки на подтверждение» мастера убраны, коробки экран не тянет.
it("keeps only planning on the resource planning screen: no box confirmation, no QC blocks", async () => {
  const adminPrincipal: Principal = { ...principal, roles: ["admin"], access_level: "admin", permissions: { production: "edit" } };
  const box = (id: string, number: string, status: string) => ({
    id, number, version: 1, task_id: task.id, operation_id: task.operations[0].id, item: task.item,
    operation_position: 1, operator_id: "10000000-0000-0000-0000-000000000002", quantity: "5", consumed: "0", reserved: "0", rejected: "0",
    available: "5", status, created_at: "2026-09-22T09:00:00+03:00", closed_at: null, closed_by: null,
    confirmed_at: null, confirmed_by: null, return_reason: "",
  });
  const requested: string[] = [];
  masterFetch((path) => {
    requested.push(path);
    return path === "/api/production/boxes"
      ? json({ boxes: [box("74000000-0000-0000-0000-000000000011", "000011-2", "Закрыта оператором"), box("74000000-0000-0000-0000-000000000012", "000012-0", "Подтверждена")] })
      : null;
  }, [task]);
  render(<ProductionPage principal={adminPrincipal} />);

  expect(await screen.findByRole("button", { name: "Календарь цеха" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Коробки на подтверждение" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Подтвердить" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Вернуть…" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "+ Запустить задание" })).toBeInTheDocument();
  for (const title of ["Очередь ОТК", "Средства измерения", "Брак ОТК", "Брак цеха"]) {
    expect(screen.queryByRole("heading", { name: title })).not.toBeInTheDocument();
  }
  expect(screen.queryByRole("button", { name: "+ Добавить СИ" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Годные ОТК 000012-0")).not.toBeInTheDocument();
  // Данные ОТК и коробки экран планирования больше не тянет.
  expect(requested).not.toContain("/api/production/boxes");
  expect(requested).not.toContain("/api/production/defects");
  expect(requested).not.toContain("/api/production/instruments");
  expect(requested).not.toContain("/api/production/qc/rejections");
});

// Access levels (owner 04.10.2026): the planning actions belong to edit on «Ресурсное планирование» (`production`), whoever
// holds it; a view grant reads the board, and «Задания» (`myops`) is a screen of its own.
it("gives the planning actions by edit on production alone, whatever the level is called", async () => {
  for (const level of ["tech_lead", "foreman", "operator", "commercial"]) {
    masterFetch(() => null, [task]);
    const view = render(<ProductionPage principal={{ ...principal, roles: [level], access_level: level, permissions: { production: "edit" } }} />);
    expect(await screen.findByRole("button", { name: "+ Запустить задание" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Календарь цеха" })).toBeInTheDocument();
    view.unmount();
    vi.restoreAllMocks();
  }
});

it("keeps the planning actions away from a view grant on production", async () => {
  const requested: string[] = [];
  masterFetch((path) => { requested.push(path); return null; }, [task]);
  render(<ProductionPage principal={{ ...principal, roles: ["commercial"], access_level: "commercial", permissions: { production: "view" } }} />);

  await waitFor(() => expect(requested).toContain("/api/production/operators"));
  expect(screen.getByRole("heading", { name: "Ресурсное планирование", level: 1 })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "+ Запустить задание" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Календарь цеха" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Коробки на подтверждение" })).not.toBeInTheDocument();
});

it("asks for nothing of the planning board without a view on production", async () => {
  const requested: string[] = [];
  masterFetch((path) => { requested.push(path); return null; }, [task]);
  render(<ProductionPage principal={{ ...principal, roles: ["operator"], access_level: "operator", permissions: { myops: "edit" } }} />);

  await waitFor(() => expect(requested).toContain("/api/production/tasks"));
  expect(requested).not.toContain("/api/production/operators");
  expect(requested).not.toContain("/api/production/boxes");
  expect(screen.queryByRole("button", { name: "+ Запустить задание" })).not.toBeInTheDocument();
});

// Вид сотрудника: планировщик выбирает монтажника и видит его очередь без кнопок действий; сотрудник выбора не видит.
function viewAsFetch(requested: string[]) {
  const fitterId = "10000000-0000-0000-0000-000000000002";
  const returned = {
    id: "74000000-0000-0000-0000-000000000009", number: "000009-6", version: 1, task_id: task.id, operation_id: task.operations[0].id,
    item: task.item, operation_position: 2, operator_id: fitterId, quantity: "10", consumed: "0", reserved: "0", rejected: "0",
    available: "10", status: "Возвращена", created_at: "2026-09-22T09:00:00+03:00", closed_at: null, closed_by: null,
    confirmed_at: null, confirmed_by: null, return_reason: "Пересчитать",
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    requested.push(path);
    if (path.startsWith("/api/production/my/execution")) return json({ session: null, boxes: path.includes("operator_id") ? [returned] : [] });
    if (path.startsWith("/api/production/my")) return json({ tasks: path.includes("operator_id") ? [task] : [] });
    if (path === "/api/production/operators") return json({ viewer_site: "ЦАР", operators: [
      { id: fitterId, name: "Оператор", site: "ЦАР" }, { id: "x2", name: "Жуковский", site: "ЖУК" },
    ] });
    throw new Error(`Unexpected request: ${path}`);
  });
  return fitterId;
}

it("lets a planner view a fitter's queue read-only", async () => {
  const requested: string[] = [];
  const fitterId = viewAsFetch(requested);
  render(<ProductionPage principal={{ ...principal, roles: ["foreman"], access_level: "foreman", permissions: { production: "view", myops: "view" } }} mine />);

  const select = await screen.findByRole("combobox", { name: "Сотрудник" });
  // Мастер площадки ЦАР видит только монтажников своей площадки.
  expect(within(select).queryByRole("option", { name: "Жуковский" })).not.toBeInTheDocument();
  fireEvent.change(select, { target: { value: fitterId } });

  expect(await screen.findByText("Вид сотрудника Оператор — только просмотр")).toBeInTheDocument();
  expect(await screen.findByText("ПЗ-0001", { exact: false, selector: ".mo-s" })).toBeInTheDocument();
  expect(screen.queryByText("Ещё не сдавали")).not.toBeInTheDocument();
  expect(requested).toContain(`/api/production/my?operator_id=${fitterId}`);
  expect(screen.getByText("000009-6")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Начать" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Исправить" })).not.toBeInTheDocument();
  // Заголовки не говорят «Мои», пока смотрим чужое; кнопка возвращает к своим заданиям.
  expect(screen.getByRole("heading", { level: 1, name: "Задания" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Коробки: Оператор" })).toBeInTheDocument();
  expect(screen.queryByText("Мои коробки")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Вернуться к моим заданиям" }));
  await waitFor(() => expect(screen.queryByText("Вид сотрудника Оператор — только просмотр")).not.toBeInTheDocument());
  expect(select).toHaveValue("");
  expect(screen.getByRole("heading", { level: 1, name: "Мои задания" })).toBeInTheDocument();
});

it("tells a planner with the selector to pick an employee when their own view is empty", async () => {
  viewAsFetch([]);
  render(<ProductionPage principal={{ ...principal, roles: ["foreman"], access_level: "foreman", permissions: { production: "view", myops: "view" } }} mine />);
  expect(await screen.findByText("Вам операции не назначены. Выберите сотрудника, чтобы увидеть его задания.")).toBeInTheDocument();
});

it("keeps the old empty text for staff without the selector", async () => {
  viewAsFetch([]);
  render(<ProductionPage principal={{ ...principal, roles: ["operator"], access_level: "operator", permissions: { myops: "edit" } }} mine />);
  expect(await screen.findByText(/Операции вам пока не назначены/)).toBeInTheDocument();
});

it("shows no employee selector to ordinary staff", async () => {
  const requested: string[] = [];
  viewAsFetch(requested);
  render(<ProductionPage principal={{ ...principal, roles: ["operator"], access_level: "operator", permissions: { myops: "edit" } }} mine />);

  await waitFor(() => expect(requested).toContain("/api/production/my"));
  expect(screen.queryByRole("combobox", { name: "Сотрудник" })).not.toBeInTheDocument();
  expect(requested).not.toContain("/api/production/operators");
});

const fitter: Principal = {
  user: { id: "10000000-0000-0000-0000-000000000002", email: "operator@example.test", name: "Оператор", department: "Производство" },
  roles: ["operator"], access_level: "operator", permissions: { myops: "edit" },
};

function readyTask(readiness: ProductionOperation["readiness"], extra: Partial<ProductionOperation> = {}): ProductionTask {
  return { ...task, operations: [{ ...task.operations[0], position: 2, first: false, readiness, ...extra }] };
}

function fitterFetch(mine: ProductionTask, extra: (path: string, init?: RequestInit) => Response | null = () => null) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const handled = extra(path, init);
    if (handled) return handled;
    if (path === "/api/production/my") return json({ tasks: [mine] });
    if (path === "/api/production/my/execution") return json({ session: null, boxes: [] });
    if (path.endsWith("/inputs")) return json({ boxes: [], materials: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
}

const readyBoxes = [
  { id: "74000000-0000-0000-0000-000000000011", number: "000011-1", quantity: "6", operation_position: 1, stage: "Разделка" },
  { id: "74000000-0000-0000-0000-000000000012", number: "000012-9", quantity: "4", operation_position: 1, stage: "Разделка" },
];

it("shows the input readiness line on the fitter's card", async () => {
  fitterFetch(readyTask({ state: "waiting_master", text: "ждёт: Разделка — коробка ещё у мастера", boxes: [] }));
  render(<ProductionPage principal={fitter} mine />);
  expect(await screen.findByText("ждёт: Разделка — коробка ещё у мастера")).toBeInTheDocument();
});

it("starts by tapping ready box chips and keeps typed numbers as a fallback", async () => {
  let payload: unknown;
  fitterFetch(readyTask({ state: "ready", text: "можно начинать", boxes: readyBoxes }), (path, init) => {
    if (path.endsWith("/work/start") && init?.method === "POST") { payload = JSON.parse(String(init.body)); return json({ session: {} }); }
    return null;
  });
  render(<ProductionPage principal={fitter} mine />);
  fireEvent.click(await screen.findByRole("button", { name: "Начать" }));
  const first = await screen.findByRole("button", { name: /000011-1/ });
  const second = screen.getByRole("button", { name: /000012-9/ });
  fireEvent.click(first);
  fireEvent.click(second);
  expect(first).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(second);
  expect(second).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(second);
  fireEvent.click(screen.getByRole("button", { name: "Ввести код вручную" }));
  fireEvent.change(screen.getByLabelText("Номера коробок"), { target: { value: "000099-4, 000011-1" } });
  fireEvent.click(screen.getByRole("button", { name: "Начать работу" }));
  await waitFor(() => expect(payload).toEqual({ inputs: ["000011-1", "000012-9", "000099-4"], materials: [] }));
});

it("prefills «Годные» with the remainder of the fitter's share when work is running", async () => {
  const session = {
    id: "76000000-0000-0000-0000-000000000002", task_id: task.id, operation_id: task.operations[0].id,
    operator_id: fitter.user.id, status: "В работе", started_at: "2026-09-22T09:00:00+03:00",
    paused_at: null, pause_seconds: 0, finished_at: null, version: 1,
  };
  fitterFetch(readyTask({ state: "ready", text: "можно начинать", boxes: [] }, { remaining: "7" }), (path) => {
    if (path === "/api/production/my/execution") return json({ session, boxes: [] });
    return null;
  });
  render(<ProductionPage principal={fitter} mine />);
  fireEvent.click(await screen.findByRole("button", { name: "Сдать…" }));
  const input = await screen.findByLabelText("Годные изделия");
  await waitFor(() => expect(input).toHaveValue(7));
});

it("opens the operation's ETK read-only on that operation through the production endpoint", async () => {
  const etk = {
    id: "80000000-0000-0000-0000-000000000001", version: 1, project: "591K.3", item_index: "КБ1", product_name: "Жгут",
    source_calculation: "", deadline: null, status: "Выдана", revision: 1, manual_operation_keys: [],
    created_at: "2026-09-20T09:00:00+03:00", updated_at: "2026-09-20T09:00:00+03:00", issued_at: "2026-09-20T09:00:00+03:00",
    document: { operations: [
      { position: 1, code: "OP-01", title: "Разделка кабеля" },
      { position: 2, code: "OP-02", title: "Опрессовка терминалов" },
    ] },
  };
  fitterFetch(readyTask({ state: "ready", text: "можно начинать", boxes: [] }, { code: "OP-02" }), (path) => {
    if (path.endsWith("/etk")) return json({ card: etk, focus: { position: 2, code: "OP-02" } });
    if (path.includes("/api/techcards/projects/")) return json({ items: [] });
    return null;
  });
  render(<ProductionPage principal={fitter} mine />);
  fireEvent.click(await screen.findByRole("button", { name: "ЭТК" }));
  // Только операция задания: без списка операций ЭТК слева и без «← Назад / Далее» к чужим операциям.
  expect(await screen.findByRole("heading", { name: /Опрессовка терминалов/ })).toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: "Маршрут ЭТК" })).not.toBeInTheDocument();
  expect(screen.queryByText("Разделка кабеля")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Назад|Далее/ })).not.toBeInTheDocument();
  expect(document.querySelector(".etk-sheet")).not.toBeNull();
  expect(screen.queryByRole("button", { name: "Выдать ЭТК" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Пересобрать/ })).not.toBeInTheDocument();
});

it("opens the fitter's ETK in a window with «Оснастка» and «Ход операции» expanded, the rest collapsed, and remembers what this user changed", async () => {
  window.localStorage.clear();
  const etk = {
    id: "80000000-0000-0000-0000-000000000001", version: 1, project: "591K.3", item_index: "КБ1", product_name: "Жгут",
    source_calculation: "", deadline: null, status: "Выдана", revision: 1, manual_operation_keys: [],
    created_at: "2026-09-20T09:00:00+03:00", updated_at: "2026-09-20T09:00:00+03:00", issued_at: "2026-09-20T09:00:00+03:00",
    document: {
      operations: [{ position: 2, code: "OP-02", title: "Опрессовка терминалов",
        card: { name: "Опрессовка терминалов", safety: "Работать в очках", steps: [{ number: "1", kind: "Операция", text: "Обжать терминал" }] } }],
      connections: [
        { wire: "НВ4 0,2 белый", from: { end: "A1", conn: "DS1071", pin: 3 }, to: { end: "B1", conn: "XHP-4", pin: 2 } },
        { wire: "НВ4 0,2 красный", from: { end: "A1", conn: "DS1071", pin: 1 }, to: { end: "B1", conn: "XHP-4", pin: 1 } },
      ],
    },
  };
  const seen: string[] = [];
  fitterFetch(readyTask({ state: "ready", text: "можно начинать", boxes: [] }, { code: "OP-02" }), (path) => {
    seen.push(path);
    if (path.endsWith("/etk")) return json({ card: etk, focus: { position: 2, code: "OP-02" } });
    // У монтажника нет раздела «Проекты»: чертёж КБ ему не читается — схема строится по ЭТК.
    if (path.includes("/harness/")) return new Response(JSON.stringify({ detail: "Раздел «Проекты» вам не открыт." }), { status: 403, headers: { "Content-Type": "application/json" } });
    if (path.includes("/api/techcards/projects/")) return json({ items: [] });
    return null;
  });
  const first = render(<ProductionPage principal={fitter} mine />);
  fireEvent.click(await screen.findByRole("button", { name: "ЭТК" }));
  // Окно поверх списка (16.10): ЭТК не раздвигает карточки, закрывается кнопкой.
  const dialog = await screen.findByRole("dialog", { name: "ЭТК: Разделка" });
  const steps = await within(dialog).findByRole("region", { name: "Ход операции" });
  expect(steps.querySelector("details")).toHaveAttribute("open");
  expect(screen.getByRole("region", { name: "Оснастка" }).querySelector("details")).toHaveAttribute("open");
  expect(screen.getByRole("region", { name: "Вход" }).querySelector("details")).not.toHaveAttribute("open");
  expect(screen.getByText("Требование безопасности").closest("details")).not.toHaveAttribute("open");
  const schema = screen.getByText("Схема Э4").closest("details")!;
  expect(schema).not.toHaveAttribute("open");
  // Схема строится только открытой: чертёж не запрошен, пока блок закрыт.
  expect(seen.some((path) => path.includes("/harness/"))).toBe(false);

  fireEvent.click(screen.getByText("Схема Э4"));
  // Чертежа монтажнику не прочитать — схема связей по таблице соединений ЭТК, как в генераторе (владелец 14.10).
  const map = await screen.findByRole("img", { name: "Провода с контакта на контакт, сростки и хвосты" });
  expect([...map.querySelectorAll('[data-end="A1"] .pin-links-row')].map((row) => `${row.getAttribute("data-pin")}:${row.getAttribute("data-wires") ?? ""}`))
    .toEqual(["1:2", "2:", "3:1"]);
  expect([...map.querySelectorAll(".pin-links-line")].map((line) => `${line.getAttribute("data-a")}→${line.getAttribute("data-b")}`)).toEqual(["A1·3→B1·2", "A1·1→B1·1"]);
  expect(screen.queryByText("Таблица соединений · 2")).toBeNull();
  expect(map.closest(".etk-e4")).toBeNull();

  // Закрыл «Ход операции» — запомнилось «закрыт»; открыл «Вход» — «открыт».
  fireEvent.click(within(steps).getByText("Ход операции"));
  await waitFor(() => expect(steps.querySelector("details")).not.toHaveAttribute("open"));
  fireEvent.click(within(screen.getByRole("region", { name: "Вход" })).getByText("Вход"));
  await waitFor(() => expect(window.localStorage.getItem(`etk-fold:${fitter.user.id}:sec:Вход`)).toBe("1"));
  expect(window.localStorage.getItem(`etk-fold:${fitter.user.id}:sec:Ход операции`)).toBe("0");
  expect(window.localStorage.getItem(`etk-fold:${fitter.user.id}:schema`)).toBe("1");
  fireEvent.click(within(dialog).getByRole("button", { name: "Закрыть" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  first.unmount();

  render(<ProductionPage principal={fitter} mine />);
  fireEvent.click(await screen.findByRole("button", { name: "ЭТК" }));
  expect((await screen.findByRole("region", { name: "Ход операции" })).querySelector("details")).not.toHaveAttribute("open");
  expect(screen.getByRole("region", { name: "Вход" }).querySelector("details")).toHaveAttribute("open");
  expect(screen.getByRole("region", { name: "Оснастка" }).querySelector("details")).toHaveAttribute("open");
  window.localStorage.clear();
});

it("loads the ETK sketches of the fitter's operation through the production endpoints, not the techcards ones", async () => {
  const etk = {
    id: "80000000-0000-0000-0000-000000000001", version: 1, project: "591K.3", item_index: "КБ1", product_name: "Жгут",
    source_calculation: "", deadline: null, status: "Выдана", revision: 1, manual_operation_keys: [],
    created_at: "2026-09-20T09:00:00+03:00", updated_at: "2026-09-20T09:00:00+03:00", issued_at: "2026-09-20T09:00:00+03:00",
    document: { operations: [{ position: 1, code: "OP-01", title: "Разделка кабеля" }] },
  };
  const seen: string[] = [];
  fitterFetch(readyTask({ state: "ready", text: "можно начинать", boxes: [] }, { code: "OP-01" }), (path) => {
    seen.push(path);
    if (path.endsWith("/etk")) return json({ card: etk, focus: { position: 1, code: "OP-01" } });
    if (path.endsWith("/etk/sketches")) return json({ items: [{ operation_key: "OP-01::::", file_name: "a.png", size: 3, uploaded_by: "Технолог", uploaded_at: "2026-09-20T10:00:00+03:00" }] });
    return null;
  });
  render(<ProductionPage principal={fitter} mine />);
  fireEvent.click(await screen.findByRole("button", { name: "ЭТК" }));
  await waitFor(() => expect(document.querySelector(".etk-sketch img")).not.toBeNull());
  expect(document.querySelector(".etk-sketch img")?.getAttribute("src")).toMatch(/^\/api\/production\/my\/tasks\/.+\/operations\/.+\/etk\/sketches\/OP-01%3A%3A%3A%3A\?v=/);
  expect(seen.some((path) => path.endsWith("/etk/sketches"))).toBe(true);
  expect(seen.some((path) => path.includes("/api/techcards/") && path.includes("/sketches"))).toBe(false);
});

// Демо «задачи монтажника на этапе 3» (владелец 16.10): ждущий этап не зелёный, счётчик — только активные, код операции
// монтажнику не показывается.
it("greys out «Начать» on a stage waiting for its input with the reason next to it and counts only active cards", async () => {
  const waiting = readyTask({ state: "waiting_stage", text: "ждёт этап 1 «Разделка»: сдано 0 из 10", boxes: [], source_position: 1, handed: "0", need: "10" });
  const finished: ProductionTask = { ...task, id: "70000000-0000-0000-0000-000000000009", operations: [{
    ...task.operations[0], id: "71000000-0000-0000-0000-000000000009", state: "выполнена", fact: { good: "10", defect: "0", processed: "10" },
  }] };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/production/my") return json({ tasks: [waiting, finished] });
    if (path === "/api/production/my/execution") return json({ session: null, boxes: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<ProductionPage principal={fitter} mine />);

  const start = await screen.findByRole("button", { name: "Начать" });
  expect(start).toBeDisabled();
  const reason = screen.getByText("ждёт этап 1 «Разделка»: сдано 0 из 10");
  expect(start).toHaveAttribute("aria-describedby", reason.id);
  fireEvent.click(start);
  expect(screen.queryByRole("button", { name: "Начать работу" })).not.toBeInTheDocument();
  expect(screen.getByText("1", { selector: ".page-count" })).toBeInTheDocument();
  expect(screen.getByText("Выполнено · 1")).toBeInTheDocument();
  expect(screen.queryByText("OP-01")).not.toBeInTheDocument();
  expect(screen.queryByText("Ещё не сдавали")).not.toBeInTheDocument();
});

it("shows the fitter's own share, what was handed of it and how many pieces can be started now", async () => {
  const share = { operator_id: fitter.user.id, quantity: 4, planned_start: "2026-09-22T09:00:00+03:00", planned_finish: "2026-09-22T09:06:00+03:00", started_at: null, finished_at: null };
  fitterFetch(readyTask({ state: "ready", text: "можно начинать", boxes: readyBoxes, available: "10" },
    { my_share: share, remaining: "3", fact: { good: "5", defect: "0", processed: "5" }, state: "в работе" }));
  render(<ProductionPage principal={fitter} mine />);

  expect(await screen.findByText("ваша доля 4 из 10")).toBeInTheDocument();
  expect(screen.getByText("сдано вами 1 из 4 · по этапу 5 из 10")).toBeInTheDocument();
  // Коробки дают 10 шт, своих осталось 3 — начать можно на 3.
  expect(screen.getByText("можно начать: 3 шт · коробок: 2")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Начать" })).toBeEnabled();
  // Норма — на свою долю: 2 мин подготовки + 1 мин × 4 шт.
  expect(screen.getByText("норма 0,1 ч на 4 шт (подготовка 2 мин + 1 мин на изделие)")).toBeInTheDocument();
});

it("says once that the work was started earlier than planned", async () => {
  const share = { operator_id: fitter.user.id, quantity: null, planned_start: "2026-09-22T09:00:00+03:00", planned_finish: "2026-09-22T11:50:00+03:00", started_at: "2026-09-22T06:22:00+03:00", finished_at: null };
  fitterFetch(readyTask({ state: "ready", text: "можно начинать", boxes: [] }, { my_share: share }));
  render(<ProductionPage principal={fitter} mine />);
  expect(await screen.findByText("— начато раньше на 2 ч 38 мин")).toBeInTheDocument();
  expect(screen.getAllByText(/начато раньше/)).toHaveLength(1);
  expect(screen.getByText(/^взял 22.09/)).toBeInTheDocument();
});

it("keeps «Начать» disabled with a reason until a box is chosen, then lets it go", async () => {
  fitterFetch(readyTask({ state: "ready", text: "можно начинать", boxes: readyBoxes }));
  render(<ProductionPage principal={fitter} mine />);
  fireEvent.click(await screen.findByRole("button", { name: "Начать" }));
  const go = await screen.findByRole("button", { name: "Начать работу" });
  expect(go).toBeDisabled();
  expect(screen.getByText("Выберите коробку или введите её код")).toBeInTheDocument();
  expect(screen.queryByLabelText("Номера коробок")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /000011-1/ }));
  expect(go).toBeEnabled();
  expect(screen.queryByText("Выберите коробку или введите её код")).not.toBeInTheDocument();
});

it("limits the «Годные» prefill by the taken input boxes", async () => {
  expect(goodPrefill("7", undefined)).toBe("7");
  expect(goodPrefill("7", null)).toBe("7");
  expect(goodPrefill("7", "4")).toBe("4");
  expect(goodPrefill("7", "12")).toBe("7");
  expect(goodPrefill(undefined, "4")).toBe("");
  const session = {
    id: "76000000-0000-0000-0000-000000000003", task_id: task.id, operation_id: task.operations[0].id,
    operator_id: fitter.user.id, status: "В работе", started_at: "2026-09-22T09:00:00+03:00",
    paused_at: null, pause_seconds: 0, finished_at: null, version: 1, input_limit: "4",
  };
  fitterFetch(readyTask({ state: "ready", text: "можно начинать", boxes: [] }, { remaining: "7" }), (path) => {
    if (path === "/api/production/my/execution") return json({ session, boxes: [] });
    return null;
  });
  render(<ProductionPage principal={fitter} mine />);
  fireEvent.click(await screen.findByRole("button", { name: "Сдать…" }));
  const input = await screen.findByLabelText("Годные изделия");
  await waitFor(() => expect(input).toHaveValue(4));
});

it("titles an overdue operation that was never started «Просрочено · план», a started one «Начато ранее»", async () => {
  const old = { planned_start: "2026-10-04T09:00:00+03:00", planned_finish: "2026-10-04T12:00:00+03:00" };
  const idle = readyTask({ state: "ready", text: "можно начинать", boxes: [] }, old);
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-05T10:00:00+03:00"));
  try {
    fitterFetch(idle);
    const { unmount } = render(<ProductionPage principal={fitter} mine />);
    expect(await screen.findByText(/Просрочено · план 04\.10/)).toBeInTheDocument();
    expect(screen.queryByText(/Начато ранее/)).not.toBeInTheDocument();
    unmount();
    const begun = readyTask({ state: "ready", text: "можно начинать", boxes: [] }, { ...old, fact: { processed: "3", good: "3", defect: "0" } } as Partial<ProductionOperation>);
    vi.restoreAllMocks();
    fitterFetch(begun);
    render(<ProductionPage principal={fitter} mine />);
    expect(await screen.findByText(/Начато ранее · 04\.10/)).toBeInTheDocument();
  } finally { vi.useRealTimers(); }
});

// Работа за сотрудника (12.10.2026): мастер с правкой планирования делает всё, что монтажник, — от его имени.
it("lets a master work for a fitter: banner, start on her behalf with a replay key", async () => {
  const fitterId = "10000000-0000-0000-0000-000000000002";
  const calls: Array<{ path: string; key: string | null }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      calls.push({ path, key: new Headers(init.headers).get("Idempotency-Key") });
      return json({ session: {} });
    }
    calls.push({ path, key: null });
    if (path.startsWith("/api/production/my/execution")) return json({ session: null, boxes: [] });
    if (path.startsWith("/api/production/my")) return path.includes("operator_id")
      ? json({ tasks: [{ ...task, operations: [{ ...task.operations[0], readiness: { state: "ready", text: "можно начинать", boxes: [] } }] }], can_act: true })
      : json({ tasks: [], can_act: true });
    if (path === "/api/production/operators") return json({ viewer_site: "ЦАР", operators: [{ id: fitterId, name: "Оператор", site: "ЦАР" }] });
    if (path.includes("/inputs")) return json({ boxes: [], materials: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<ProductionPage principal={{ ...principal, permissions: { production: "edit", myops: "edit" } }} mine />);

  fireEvent.change(await screen.findByRole("combobox", { name: "Сотрудник" }), { target: { value: fitterId } });
  expect(await screen.findByText("Работаете за сотрудника Оператор")).toBeInTheDocument();
  expect(screen.queryByText(/только просмотр/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Вернуться к моим заданиям" })).toBeInTheDocument();
  expect(await screen.findByText("можно начинать")).toBeInTheDocument();
  expect(screen.getByText(/^план /, { selector: ".mo-window" })).toHaveTextContent(/^план 22\.09 \d\d:00–\d\d:12$/);
  // Мастеру код операции виден (монтажнику — нет).
  expect(screen.getByText("OP-01", { selector: ".op-code" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Начать" }));
  fireEvent.click(await screen.findByRole("button", { name: "Начать работу" }));

  await waitFor(() => expect(calls.some((call) => call.path.endsWith(`/work/start?on_behalf_of=${fitterId}`))).toBe(true));
  expect(calls.find((call) => call.path.includes("/work/start"))?.key).toBeTruthy();
  expect(calls.some((call) => call.path.endsWith(`/inputs?operator_id=${fitterId}`))).toBe(true);
});

it("puts pause and «Сдать…» on the running card with its timer, and the fact against the norm on a finished one", async () => {
  const startedAt = new Date(Date.now() - 42 * 60_000).toISOString();
  const session = {
    id: "76000000-0000-0000-0000-000000000005", task_id: task.id, operation_id: task.operations[0].id,
    operator_id: fitter.user.id, status: "В работе", started_at: startedAt, pause_started_at: null, pause_seconds: 0,
  };
  const finished: ProductionTask = { ...task, id: "70000000-0000-0000-0000-000000000008", operations: [{
    ...task.operations[0], state: "выполнена", fact: { good: "10", defect: "0", processed: "10" },
    time_fact: { actual_seconds: 98 * 60, norm_seconds: 90 * 60, percent: 109, finishes: 1 },
  }] };
  const posted: Array<{ path: string; key: string | null }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") { posted.push({ path, key: new Headers(init.headers).get("Idempotency-Key") }); return json({ session }); }
    if (path === "/api/production/my") return json({ tasks: [finished, task] });
    if (path === "/api/production/my/execution") return json({ session, boxes: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<ProductionPage principal={fitter} mine />);

  // Начало 42 мин назад: после полуночи это вчера — тогда с датой («в работе с 16.10 23:58»).
  expect(await screen.findByText(/^в работе с (\d\d\.\d\d )?\d\d:\d\d · 0:4[12]$/)).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: /Идёт работа/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Начать" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сдать…" })).toBeInTheDocument();
  expect(screen.getByText("факт 1:38 · норма 1:30 · 109 %")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Пауза" }));
  await waitFor(() => expect(posted).toEqual([{ path: `/api/production/work/${session.id}/pause`, key: `${session.id}:pause:0:` }]));
});

it("counts worked time without pauses", () => {
  expect(clock(5880)).toBe("1:38");
  expect(clock(59)).toBe("0:00");
  const base = { id: "s", task_id: "t", operation_id: "o", operator_id: "u", started_at: "2026-10-12T14:00:00+03:00", input_limit: null };
  expect(workedSeconds({ ...base, status: "В работе", pause_started_at: null, pause_seconds: 600 }, new Date("2026-10-12T15:00:00+03:00"))).toBe(3000);
  expect(workedSeconds({ ...base, status: "Пауза", pause_started_at: "2026-10-12T14:30:00+03:00", pause_seconds: 0 }, new Date("2026-10-12T15:00:00+03:00"))).toBe(1800);
});

const pfPrincipal: Principal = {
  user: { id: "10000000-0000-0000-0000-000000000002", email: "operator@example.test", name: "Оператор", department: "Производство" },
  roles: ["operator"], access_level: "operator", permissions: { myops: "edit" },
};

function mockMine(operation: ProductionOperation) {
  const mine: ProductionTask = { ...task, operations: [{ ...operation, operator_ids: [pfPrincipal.user.id] }] };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/production/my") return json({ tasks: [mine] });
    if (path === "/api/production/my/execution") return json({ session: null, boxes: [] });
    if (path.endsWith("/inputs")) return json({ boxes: [{
      id: "74000000-0000-0000-0000-000000000001", number: "000001-8", code: "180К.11·КБ1·13.10·Б07·3", version: 1, task_id: task.id, operation_id: "x", item: task.item,
      operation_position: 2, operator_id: "x", quantity: "5", consumed: "0", reserved: "0", rejected: "0", available: "5",
      status: "Подтверждена", created_at: "2026-09-22T09:00:00+03:00", closed_at: null, closed_by: null, confirmed_at: null, confirmed_by: null, return_reason: "",
    }], materials: [] });
    if (path.endsWith("/etk")) return json({ card: { document: { operations: [
      { position: 5, card: { steps: [{ kind: "КОНТРОЛЬ", text: "Высота обжима", tolerance: "1,2 мм" }], defects: "" } },
    ] } }, focus: { position: 5, positions: [5, 6, 7], code: "OP-01" } });
    throw new Error(`Unexpected request: ${path}`);
  });
}

it("shows a semi-finished task card: ПФ name, operation numbers, inputs and collapsed requirements", async () => {
  mockMine({ ...task.operations[0], position: 3, stage: "Опрессовка", first: false,
    etk_ops: [{ position: 5, code: "OP-5", stage: "Зачистка" }, { position: 6, code: "OP-6", stage: "Опрессовка" }, { position: 7, code: "OP-7", stage: "Контроль" }] });
  render(<ProductionPage principal={pfPrincipal} mine />);
  expect(await screen.findByText("ПФ-3 · Опрессовка")).toBeInTheDocument();
  expect(screen.getByText("оп. 05·06·07")).toBeInTheDocument();
  expect(await screen.findByText(/Получить от предыдущей задачи/)).toBeInTheDocument();
  expect(await screen.findByText(/180К\.11·КБ1·13\.10·Б07·3/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Инструкция" })).toBeInTheDocument();
  fireEvent.click(screen.getByText("Требования"));
  expect(await screen.findByText(/Контроль: Высота обжима \(допуск: 1,2 мм\)/)).toBeInTheDocument();
});

it("keeps an old one-ETK-operation card as it was: stage name, ЭТК button, no ПФ blocks", async () => {
  mockMine({ ...task.operations[0], first: false });
  render(<ProductionPage principal={pfPrincipal} mine />);
  expect(await screen.findByText("Разделка")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "ЭТК" })).toBeInTheDocument();
  expect(screen.queryByText("Требования")).not.toBeInTheDocument();
  expect(screen.queryByText(/ПФ-/)).not.toBeInTheDocument();
});

it("says when the card got a newer revision than the task was launched with", () => {
  expect(etkRevisionNotice({ pinned: 1, current: 2, newer: true, fallback: null }))
    .toBe("Есть новая ревизия ТК 2 — задание идёт по ревизии 1");
  expect(etkRevisionNotice({ pinned: 2, current: 2, newer: false, fallback: null })).toBe("");
  expect(etkRevisionNotice({ pinned: 1, current: 2, newer: false, fallback: "Ревизия 1 не найдена — показана текущая ЭТК" }))
    .toBe("Ревизия 1 не найдена — показана текущая ЭТК");
  expect(etkRevisionNotice(undefined)).toBe("");
});
