import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProductionPage, goodPrefill } from "./ProductionPage";
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
  fireEvent.click(screen.getByRole("button", { name: "Запустить" }));

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
  expect(await screen.findByText(/сдано 6 из 10, брак 1 · в коробках 4 \+ 2 ждут подтверждения/)).toBeInTheDocument();
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
  expect(await screen.findByText("Ещё не сдавали")).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "▶ Начать" }));
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
  const dialog = await screen.findByRole("dialog", { name: "Коробка исправлена" });
  expect(dialog).toHaveTextContent("000002-6");
  expect(screen.queryByRole("button", { name: "Доложить" })).not.toBeInTheDocument();
});

it("starts a later stage by typed box numbers and shows the new box number to write on it", async () => {
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
      return json({ session, box: { id: "74000000-0000-0000-0000-000000000009", number: "000007-5" }, output_id: "x", defect: null });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProductionPage principal={operatorPrincipal} mine />);
  fireEvent.click(await screen.findByRole("button", { name: "▶ Начать" }));
  fireEvent.change(await screen.findByLabelText("Номера коробок"), { target: { value: "000001-8, 000002-6\n" } });
  fireEvent.click(screen.getByRole("button", { name: "Начать работу" }));
  fireEvent.change(await screen.findByLabelText("Годные изделия"), { target: { value: "5" } });
  fireEvent.click(screen.getByRole("button", { name: "Сдать" }));

  const dialog = await screen.findByRole("dialog", { name: "Напиши на коробке" });
  expect(dialog).toHaveTextContent("000007-5");
  expect(requests[0].body).toEqual({ inputs: ["000001-8", "000002-6"], materials: [] });
  fireEvent.click(screen.getByRole("button", { name: "Записал" }));
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
  expect(screen.getAllByRole("button", { name: "▶ Начать" })).toHaveLength(1);
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
  expect(await screen.findByText("к запуску 1")).toBeInTheDocument(); // счётчик запуска приходит отдельным запросом
  expect(screen.getByText("ООО «Тест»")).toBeInTheDocument();
  expect(screen.getByText("ТК на все КБ")).toBeInTheDocument();

  await openBoardProject("591K.3");
  fireEvent.click(screen.getByRole("button", { name: /Разделка/ }));
  expect(screen.getByText("Обжим")).toBeInTheDocument();
  expect(screen.getByText("22.09 10:00–14:00")).toBeInTheDocument();
  expect(screen.getByTitle("Попов Игнат, 22.09: 0 из 7 ч")).toHaveTextContent("0/7");
  // Клик по дню — раскладка по часам: у Рыбкиной 10:00–14:00 занято, выбор часа ставит операцию ровно с него.
  fireEvent.click(screen.getByRole("button", { name: "День Рыбкина Ольга 22.09" }));
  expect(screen.getByTitle("11:00–12:00: Обжим · 591K.3.КБ1")).toBeInTheDocument();
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
  // Снять — крестиком на исполнителе, без отдельной кнопки у каждого.
  fireEvent.click(screen.getByRole("button", { name: "Снять Рыбкина Ольга" }));
  await waitFor(() => expect(bodies.at(-1)).toEqual({
    path: `/api/production/tasks/${open.id}/operations/${open.operations[1].id}/share`,
    body: { version: 2, operator_id: "10000000-0000-0000-0000-000000000002", remove: true },
  }));

  fireEvent.click(await screen.findByRole("button", { name: "← Все проекты" }));
  await openBoardProject("708К.4");
  fireEvent.click(await screen.findByRole("button", { name: "В работу" }));
  await waitFor(() => expect(bodies.at(-1)).toEqual({
    path: "/api/production/tasks",
    body: { techcard_id: "80000000-0000-0000-0000-000000000009", quantity: 5, deadline: "2026-10-10", fulfillment_request_id: null },
  }));
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
  expect(await screen.findByText(/591K.3.КБ1 × 10 · сдать 20 шт/)).toBeInTheDocument();
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
  fireEvent.click(await screen.findByRole("button", { name: "▶ Начать" }));
  expect(await screen.findByText(/с этапа 1, подтверждённые мастером/)).toBeInTheDocument();
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
  fireEvent.click(await screen.findByRole("button", { name: "▶ Начать" }));
  expect(await screen.findByText(new RegExp(`${FEEDS}; подтверждённые мастером`))).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: /000001-8.*5 шт · этап 1/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /000002-6.*5 шт · этап 2/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Ввести номер вручную" }));
  expect(screen.getByLabelText("Номера коробок")).toBeInTheDocument();
  unmount();

  // Вторая нарезка стоит под номером 2, но входов у неё нет: коробок не просит, берёт материалы комплекта.
  mine = mineTask(branched.operations[1]);
  render(<ProductionPage principal={operatorPrincipal} mine />);
  fireEvent.click(await screen.findByRole("button", { name: "▶ Начать" }));
  expect(await screen.findByLabelText("Количество из партии PPM.26.0000001 П1")).toBeInTheDocument();
  expect(screen.queryByLabelText("Номера коробок")).not.toBeInTheDocument();
  expect(screen.getByText(/сдать 20 шт/)).toBeInTheDocument();
});

// Owner 04.10.2026: «почему эти два блока вообще тут» — очередь ОТК, брак и средства измерения живут в разделе «ОТК».
it("keeps only planning and the master's box confirmation on the resource planning screen", async () => {
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

  expect(await screen.findByRole("heading", { name: "Коробки на подтверждение" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Календарь цеха" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "+ Запустить задание" })).toBeInTheDocument();
  for (const title of ["Очередь ОТК", "Средства измерения", "Брак ОТК", "Брак цеха"]) {
    expect(screen.queryByRole("heading", { name: title })).not.toBeInTheDocument();
  }
  expect(screen.queryByRole("button", { name: "+ Добавить СИ" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Годные ОТК 000012-0")).not.toBeInTheDocument();
  // Данные ОТК экран планирования больше не тянет.
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
  expect(await screen.findByText("Ещё не сдавали")).toBeInTheDocument();
  expect(requested).toContain(`/api/production/my?operator_id=${fitterId}`);
  expect(screen.getByText("000009-6")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "▶ Начать" })).not.toBeInTheDocument();
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
  fireEvent.click(await screen.findByRole("button", { name: "▶ Начать" }));
  const first = await screen.findByRole("button", { name: /000011-1/ });
  const second = screen.getByRole("button", { name: /000012-9/ });
  fireEvent.click(first);
  fireEvent.click(second);
  expect(first).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(second);
  expect(second).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(second);
  fireEvent.click(screen.getByRole("button", { name: "Ввести номер вручную" }));
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
  const { container } = render(<ProductionPage principal={fitter} mine />);
  fireEvent.click(await screen.findByRole("button", { name: "ЭТК" }));
  const nav = await screen.findByRole("navigation", { name: "Маршрут ЭТК" });
  expect(nav.querySelector("button.on")).toHaveTextContent("Опрессовка терминалов");
  expect(container.querySelector(".etk-sheet")).not.toBeNull();
  expect(screen.queryByRole("button", { name: "Выдать ЭТК" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Пересобрать/ })).not.toBeInTheDocument();
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
  const { container } = render(<ProductionPage principal={fitter} mine />);
  fireEvent.click(await screen.findByRole("button", { name: "ЭТК" }));
  await waitFor(() => expect(container.querySelector(".etk-sketch img")).not.toBeNull());
  expect(container.querySelector(".etk-sketch img")?.getAttribute("src")).toMatch(/^\/api\/production\/my\/tasks\/.+\/operations\/.+\/etk\/sketches\/OP-01%3A%3A%3A%3A\?v=/);
  expect(seen.some((path) => path.endsWith("/etk/sketches"))).toBe(true);
  expect(seen.some((path) => path.includes("/api/techcards/") && path.includes("/sketches"))).toBe(false);
});

it("confirms every box waiting for the master with one button", async () => {
  const waiting = (id: string, number: string) => ({
    id, number, version: 2, task_id: task.id, operation_id: task.operations[0].id, item: task.item, operation_position: 1,
    operator_id: "10000000-0000-0000-0000-000000000002", quantity: "5", consumed: "0", reserved: "0", rejected: "0",
    available: "5", status: "Закрыта оператором", created_at: "2026-09-22T09:00:00+03:00",
    closed_at: null, closed_by: null, confirmed_at: null, confirmed_by: null, return_reason: null,
  });
  const boxes = [waiting("74000000-0000-0000-0000-000000000021", "000021-2"), waiting("74000000-0000-0000-0000-000000000022", "000022-0")];
  const confirmed: string[] = [];
  masterFetch((path, init) => {
    if (path === "/api/production/boxes") return json({ boxes });
    if (path.endsWith("/confirm") && init?.method === "POST") { confirmed.push(path); return json({ box: boxes[0] }); }
    return null;
  }, [task]);
  render(<ProductionPage principal={principal} />);
  fireEvent.click(await screen.findByRole("button", { name: "Подтвердить все" }));
  await waitFor(() => expect(confirmed).toEqual(boxes.map((box) => `/api/production/boxes/${box.id}/confirm`)));
});

it("keeps «Начать» disabled with a reason until a box is chosen, then lets it go", async () => {
  fitterFetch(readyTask({ state: "ready", text: "можно начинать", boxes: readyBoxes }));
  render(<ProductionPage principal={fitter} mine />);
  fireEvent.click(await screen.findByRole("button", { name: "▶ Начать" }));
  const go = await screen.findByRole("button", { name: "Начать работу" });
  expect(go).toBeDisabled();
  expect(screen.getByText("Выбери коробку или введи её номер")).toBeInTheDocument();
  expect(screen.queryByLabelText("Номера коробок")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /000011-1/ }));
  expect(go).toBeEnabled();
  expect(screen.queryByText("Выбери коробку или введи её номер")).not.toBeInTheDocument();
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

it("folds the return reason behind «Вернуть…» in the boxes waiting for the master", async () => {
  const box = {
    id: "74000000-0000-0000-0000-000000000031", number: "000031-5", version: 2, task_id: task.id, operation_id: task.operations[0].id, item: task.item, operation_position: 1,
    operator_id: "10000000-0000-0000-0000-000000000002", quantity: "5", consumed: "0", reserved: "0", rejected: "0",
    available: "5", status: "Закрыта оператором", created_at: "2026-09-22T09:00:00+03:00",
    closed_at: null, closed_by: null, confirmed_at: null, confirmed_by: null, return_reason: null,
  };
  const returned: unknown[] = [];
  masterFetch((path, init) => {
    if (path === "/api/production/boxes") return json({ boxes: [box] });
    if (path.endsWith("/return") && init?.method === "POST") { returned.push(JSON.parse(String(init.body))); return json({ box }); }
    return null;
  }, [task]);
  render(<ProductionPage principal={principal} />);
  expect(await screen.findByText("000031-5")).toBeInTheDocument();
  expect(screen.queryByLabelText("Причина возврата")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Вернуть оператору" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Вернуть…" }));
  const confirm = screen.getByRole("button", { name: "Вернуть оператору" });
  expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Причина возврата"), { target: { value: "Не хватает 1 шт" } });
  fireEvent.click(confirm);
  await waitFor(() => expect(returned).toHaveLength(1));
});
