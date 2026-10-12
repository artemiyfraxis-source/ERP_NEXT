import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProcurementPage } from "./ProcurementPage";
import { ProjectsPage } from "./ProjectsPage";
import { RequestsPage } from "./RequestsPage";
import type { InternalProject, Principal, RequestSchema } from "./api";
import { projectLabel, resetInternalProjects } from "./internalProjects";
import { chooseProject, emptyHeader, internalProjectKey, projectKind } from "./requestFormV2Logic";
import { resetStageOptions } from "./StageOptions";

const principal: Principal = {
  user: { id: "u1", email: "b@example.test", name: "Закупщик", department: "Закупки" },
  roles: ["tech_lead"],
  access_level: "tech_lead",
  permissions: { projects: "edit", newrequest: "edit", myrequests: "edit", requests: "edit", procurement: "edit" },
};

const project = (patch: Partial<InternalProject> = {}): InternalProject => ({
  id: "p1", number: "ВП-001", name: "Стенд испытаний", purpose: "RND", responsible_id: "s1", responsible: "Иванов",
  note: null, closed: false, closed_at: null, version: 1, lines: { "В работе": 2, "Жду счет": 1 }, open_requests: 3, ...patch,
});

const schema: RequestSchema = {
  required: ["purpose", "name", "quantity", "unit"],
  purposes: ["RND", "FRX", "Хоз деятельность", "Обеспечение производства"],
  delivery_points: ["г. Москва, ул. 6-я Радиальная, д. 17"],
  units: ["шт"],
  categories: ["Общепром", "ИП", "ОП"],
  projects: ["591К.3", "ВП-001"],
  internal_projects: [{ number: "ВП-001", name: "Стенд испытаний", purpose: "RND" }],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const option = (key: string, enabled: boolean) => ({ key, block: "b", title: "t", before: "", after: "", enabled });

function stub(internalOn: boolean, extra: (path: string, init?: RequestInit) => Response | undefined = () => undefined, state = { rows: [project()] }) {
  const calls: Array<{ path: string; init?: RequestInit }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = decodeURIComponent(String(input));
    calls.push({ path, init });
    const custom = extra(path, init);
    if (custom) return custom;
    if (path === "/api/stage-options") {
      return json({ available: true, options: [option("projects.internal", internalOn), option("requests.form", true)] });
    }
    if (path === "/api/purchasing/internal-projects" && init?.method === "POST") {
      const created = project({ id: "p2", number: "ВП-002", name: JSON.parse(String(init.body)).name, lines: {}, open_requests: 0 });
      state.rows = [...state.rows, created];
      return json(created, 201);
    }
    if (path === "/api/purchasing/internal-projects") return json({ projects: state.rows });
    if (path.endsWith("/close") && init?.method === "POST") {
      state.rows = state.rows.map((row) => ({ ...row, closed: true }));
      return json(state.rows[0]);
    }
    if (path === "/api/auth/login-staff") return json({ staff: [{ id: "s1", name: "Иванов", department: "РНД" }] });
    if (path === "/api/requests/schema") return json(schema);
    if (path.startsWith("/api/techcards/kitsets?sort=")) return json({ items: [], statuses: [] });
    if (path === "/api/requests") return json({ requests: [] });
    return json({ lines: [], buyers: [], stages: [], next_cursor: null });
  });
  return calls;
}

beforeEach(() => {
  window.history.replaceState({}, "", "/?section=projects");
});

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  cleanup(); // unmount before the reset: a live tree would refetch without the mock
  resetStageOptions();
  resetInternalProjects();
});

describe("«Проекты»: Коммерческие · Внутренние", () => {
  it("has no tabs while the option is off", async () => {
    stub(false);
    render(<ProjectsPage principal={principal} />);
    await screen.findByRole("heading", { name: "Проекты" });
    expect(screen.queryByRole("tab", { name: "Внутренние" })).not.toBeInTheDocument();
  });

  it("shows the internal projects as a table with full-word purpose, stages and the status", async () => {
    stub(true);
    render(<ProjectsPage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Внутренние" }));
    const table = await screen.findByRole("table");
    const row = within(table).getByText("ВП-001").closest("tr")!;
    expect(within(row).getByText("Стенд испытаний")).toBeInTheDocument();
    expect(within(row).getByText("НИОКР (опытный образец)")).toBeInTheDocument();
    expect(within(row).getByText("Иванов")).toBeInTheDocument();
    expect(within(row).getByText("В работе 2 · Жду счет 1")).toBeInTheDocument();
    expect(within(row).getByText("Открыт")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Коммерческие" })).toBeInTheDocument();
  });

  it("creates a project from the form and closes one", async () => {
    const calls = stub(true);
    render(<ProjectsPage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Внутренние" }));
    fireEvent.click(await screen.findByRole("button", { name: "+ Внутренний проект" }));
    fireEvent.click(screen.getByRole("button", { name: "Создать проект" }));
    expect(await screen.findByText("Название обязательно")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Ремонт цеха" } });
    fireEvent.click(screen.getByRole("button", { name: "Создать проект" }));
    expect(await screen.findByText("Выберите назначение")).toBeInTheDocument();
    // the purposes come from the directory (request schema), shown in full words
    const choices = within(screen.getByLabelText("Назначение")).getAllByRole("option").map((o) => o.textContent);
    expect(choices).toEqual(expect.arrayContaining(["НИОКР (опытный образец)", "Хозяйственная деятельность"]));
    fireEvent.change(screen.getByLabelText("Назначение"), { target: { value: "Хоз деятельность" } });
    fireEvent.change(screen.getByLabelText("Ответственный"), { target: { value: "s1" } });
    fireEvent.click(screen.getByRole("button", { name: "Создать проект" }));
    expect(await screen.findByText("Создан проект ВП-002 · Ремонт цеха")).toBeInTheDocument();
    const post = calls.find((call) => call.init?.method === "POST")!;
    expect(JSON.parse(String(post.init!.body))).toMatchObject({ name: "Ремонт цеха", purpose: "Хоз деятельность", responsible_id: "s1" });
    fireEvent.click((await screen.findAllByRole("button", { name: "Закрыть" }))[0]);
    await waitFor(() => expect(calls.some((call) => call.path.endsWith("/close"))).toBe(true));
  });
});

describe("request form: internal projects group", () => {
  async function openForm(internalOn: boolean, withProjects = true) {
    stub(internalOn, (path) => (path === "/api/requests/schema"
      ? json({
        ...schema, projects: internalOn ? schema.projects : ["591К.3"],
        internal_projects: internalOn && withProjects ? schema.internal_projects : [],
      }) : undefined));
    render(<RequestsPage principal={principal} view="create" />);
    return screen.findByLabelText("Проект");
  }

  it("lists open internal projects and locks the purpose to the chosen one", async () => {
    const field = await openForm(true);
    fireEvent.focus(field);
    const list = await screen.findByRole("listbox");
    expect(within(list).getByText("Внутренние проекты")).toBeInTheDocument();
    expect(within(list).queryByRole("option", { name: "НИОКР (опытный образец)" })).not.toBeInTheDocument();
    fireEvent.mouseDown(within(list).getByRole("option", { name: "ВП-001 · Стенд испытаний" }));
    // the purpose is the project's, shown read-only: there is no control for it
    expect(screen.queryByLabelText("Назначение")).not.toBeInTheDocument();
    expect(screen.getByText("Назначение: НИОКР (опытный образец)")).toBeInTheDocument();
  });

  it("offers «Создать внутренний проект…» when there is none and opens the Projects section", async () => {
    const field = await openForm(true, false);
    fireEvent.focus(field);
    fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: "Создать внутренний проект…" }));
    expect(new URL(window.location.href).searchParams.get("section")).toBe("projects");
    expect(new URL(window.location.href).searchParams.get("tab")).toBe("internal");
  });

  it("keeps the purposes as the internal choice while the option is off", async () => {
    const field = await openForm(false);
    fireEvent.focus(field);
    const list = await screen.findByRole("listbox");
    expect(within(list).getByRole("option", { name: "НИОКР (опытный образец)" })).toBeInTheDocument();
    expect(within(list).queryByRole("option", { name: /ВП-001/ })).not.toBeInTheDocument();
  });

  it("chooseProject takes the number and the purpose of an internal project", () => {
    const header = chooseProject(emptyHeader(), internalProjectKey("ВП-001"), schema.internal_projects);
    expect(header).toMatchObject({ project_reference: "ВП-001", purpose: "RND" });
    expect(projectKind(header)).toBe("internal");
  });
});

describe("«ВП-001 · Название» wherever a reference is shown", () => {
  it("labels the cards of the «Сегодня» board", async () => {
    stub(true, (path) => {
      if (path === "/api/stage-options") {
        return json({ available: true, options: [option("projects.internal", true), option("procurement.today", true)] });
      }
      if (path === "/api/procurement/today") {
        return json({ sections: [{ key: "to_pay", title: "Ждёт оплаты", count: 1, items: [], projects: [{ project: "ВП-001", count: 1, days: 2, date: null, suppliers: [] }, { project: "591К.3", count: 1, days: 1, date: null, suppliers: [] }] }] });
      }
      return undefined;
    });
    render(<ProcurementPage />);
    expect(await screen.findByRole("button", { name: /ВП-001 · Стенд испытаний/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /591К\.3/ })).toBeInTheDocument();
  });

  it("projectLabel leaves anything else as written", async () => {
    stub(true);
    const { useProjectLabel } = await import("./internalProjects");
    let label: (value: string | null | undefined) => string = projectLabel;
    function Probe() { label = useProjectLabel(); return null; }
    render(<Probe />);
    await waitFor(() => expect(label("ВП-001")).toBe("ВП-001 · Стенд испытаний"));
    expect(label("вп-001")).toBe("ВП-001 · Стенд испытаний");
    expect(label("591К.3")).toBe("591К.3");
    expect(label("ВП-777")).toBe("ВП-777");
    expect(label(null)).toBe("");
  });
});
