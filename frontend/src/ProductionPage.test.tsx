import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProductionPage } from "./ProductionPage";
import type { Principal, ProductionTask } from "./api";

const principal: Principal = {
  user: {
    id: "10000000-0000-0000-0000-000000000001",
    email: "master@example.test",
    name: "Мастер",
    department: "Производство",
  },
  roles: ["master"],
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

afterEach(() => vi.restoreAllMocks());

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
      breaks: [["13:00", "13:30"]], effective_hours: "8",
    },
  }));
});

it("starts the first operation from an issued GRN and package", async () => {
  let payload: unknown;
  const operatorPrincipal: Principal = {
    user: { id: "10000000-0000-0000-0000-000000000002", email: "operator@example.test", name: "Оператор", department: "Производство" },
    roles: ["staff"], permissions: { production: "view" },
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
  fireEvent.click(screen.getByRole("button", { name: "Начать операцию" }));

  await waitFor(() => expect(payload).toEqual({
    inputs: [],
    materials: [{ fulfillment_line_id: "72000000-0000-0000-0000-000000000001", quantity: "3" }],
  }));
});

it("records the master's append-only decision for a QC rejection", async () => {
  let payload: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/production/tasks") return json({ tasks: [], next_cursor: null });
    if (path === "/api/production/operators") return json({ operators: [] });
    if (path === "/api/production/boxes") return json({ boxes: [] });
    if (path === "/api/production/defects") return json({ defects: [] });
    if (path === "/api/production/instruments") return json({ instruments: [] });
    if (path === "/api/production/qc/rejections") return json({ rejections: [{
      id: "73000000-0000-0000-0000-000000000001",
      box_id: "74000000-0000-0000-0000-000000000001",
      box_number: "000002-4", item: "591K.3.КБ1",
      inspected_at: "2026-09-22T09:00:00+03:00", rejected: 1,
      defect_number: "Б-000001-7", defect_character: "Обрыв",
      defect_description: "Нет контакта", status: "Ожидает решения", decision: null,
    }] });
    if (path.endsWith("/qc/rejections/73000000-0000-0000-0000-000000000001/decide") && init?.method === "POST") {
      payload = JSON.parse(String(init.body));
      return json({ decision: {} });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProductionPage principal={principal} />);
  fireEvent.change(await screen.findByLabelText("Акт ОТК для Б-000001-7"), { target: { value: "Акт НС-12" } });
  fireEvent.click(screen.getByRole("button", { name: "Принять решение" }));

  await waitFor(() => expect(payload).toEqual({ disposition: "Доработка", act_reference: "Акт НС-12" }));
});

it("corrects one output of a returned box and keeps reopening as a separate action", async () => {
  const requests: Array<{ path: string; body: unknown }> = [];
  const operatorPrincipal: Principal = {
    user: { id: "10000000-0000-0000-0000-000000000002", email: "operator@example.test", name: "Оператор", department: "Производство" },
    roles: ["staff"], permissions: { production: "view" },
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
  await waitFor(() => expect(requests).toHaveLength(1));
  fireEvent.click(await screen.findByRole("button", { name: "Доложить" }));

  await waitFor(() => expect(requests).toEqual([
    {
      path: `/api/production/boxes/${returned.id}/correct`,
      body: {
        version: 4, output_id: "75000000-0000-0000-0000-000000000001",
        good_quantity: "8", defect_quantity: "0", defect_character: "", defect_description: "",
      },
    },
    { path: `/api/production/boxes/${returned.id}/reopen`, body: { version: 4 } },
  ]));
});

it("groups the operator queue by plan day and hides start for a completed operation", async () => {
  const operatorPrincipal: Principal = {
    user: { id: "10000000-0000-0000-0000-000000000002", email: "operator@example.test", name: "Оператор", department: "Производство" },
    roles: ["staff"], permissions: { production: "view" },
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
});
