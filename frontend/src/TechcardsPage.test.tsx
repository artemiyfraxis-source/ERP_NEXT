import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TechcardsPage } from "./TechcardsPage";
import type { Principal, ProjectTechcard } from "./api";

const engineer: Principal = {
  user: {
    id: "30000000-0000-0000-0000-000000000001",
    email: "engineer@example.test",
    name: "Инженер",
    department: "Производство",
  },
  roles: ["engineer"],
  permissions: { techcards: "edit" },
};

const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000001",
  version: 1,
  project: "P-100",
  item_index: "01",
  product_name: "Жгут",
  source_calculation: "Расчёт 7",
  deadline: "2026-10-01",
  status: "Черновик",
  revision: 0,
  document: null,
  manual_operation_keys: [],
  created_at: "2026-09-23T10:00:00+03:00",
  updated_at: "2026-09-23T10:00:00+03:00",
  issued_at: null,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.restoreAllMocks());

it("creates a persistent project card", async () => {
  let payload: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/techcards/projects" && init?.method === "POST") {
      payload = JSON.parse(String(init.body));
      return json(card, 201);
    }
    if (path === "/api/techcards/projects") return json({ items: [] });
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [] });
    if (path.endsWith("/revisions")) return json({ items: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<TechcardsPage principal={engineer} />);
  fireEvent.change(screen.getByLabelText("Проект"), { target: { value: "P-100" } });
  fireEvent.change(screen.getByLabelText("Индекс изделия"), { target: { value: "01" } });
  fireEvent.change(screen.getByLabelText("Наименование"), { target: { value: "Жгут" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать карточку" }));

  await waitFor(() => expect(payload).toEqual({
    project: "P-100",
    item_index: "01",
    product_name: "Жгут",
    source_calculation: "",
    deadline: null,
  }));
  expect(await screen.findByText("Карточка проекта создана")).toBeInTheDocument();
  expect(screen.getByText("Черновик")).toBeInTheDocument();
});

it("automatically builds and issues an electronic techcard", async () => {
  let current = card;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/techcards/projects") return json({ items: [current] });
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [] });
    if (path.endsWith("/revisions")) return json({ items: [] });
    if (path.endsWith("/build") && init?.method === "POST") {
      current = { ...current, version: 2, document: { operations: [{ code: "10", connector: "A", result: [{ name: "Жгут", norm: "1", unit: "Шт." }], time: [{ name: "Жгут", norm: "1" }], card: { name: "Сборка", steps: [{ number: "1", kind: "Операция", text: "Собрать" }] } }], warnings: [], build_config: { ops: [{ templateId: "one" }] } } };
      return json(current);
    }
    if (path.endsWith("/issue") && init?.method === "POST") {
      current = { ...current, version: 3, status: "Выдана", revision: 1 };
      return json(current);
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<TechcardsPage principal={engineer} />);
  const editor = await screen.findByLabelText("Конфигурация автоматической сборки");
  fireEvent.change(editor, { target: { value: JSON.stringify({ ops: [{ templateId: "one" }] }) } });
  fireEvent.click(screen.getByRole("button", { name: "Собрать ЭТК автоматически" }));
  expect(await screen.findByText("Черновик ЭТК собран")).toBeInTheDocument();
  expect(screen.getByText("Собрать")).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Выдать ЭТК" }));
  expect(await screen.findByText("ЭТК выдана, ревизия 1 зафиксирована")).toBeInTheDocument();
});

it("shows the imported operation library and opens a structured template", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/projects") return json({ items: [] });
    if (path === "/api/techcards/templates") return json({
      metadata: { source_commit: "df683f87768f98735a87e68bde2be023b48dc03a", imported_at: "2026-09-23T10:00:00Z", template_count: 1 },
      items: [{ id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Резка", steps: 1, tooling: 1 }],
    });
    if (path === "/api/techcards/templates/cut") return json({
      id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Резка", steps: 1, tooling: 1,
      document: { safety: "Работать в очках", defects: "Неверная длина", steps: [{ number: "1", kind: "Операция", text: "Отрезать провод", tolerance: "±2 мм" }], tooling: [] }, raw_rows: [],
    });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<TechcardsPage principal={engineer} />);
  fireEvent.click(await screen.findByText("CUT_WIRE_auto"));
  expect(await screen.findByText("Отрезать провод")).toBeInTheDocument();
  expect(screen.getByText(/Снимок SUPPLYMAP df683f87/)).toBeInTheDocument();
});

it("shows connector housing pin layout on an installation operation", async () => {
  const withHousing: ProjectTechcard = { ...card, document: {
    operations: [{
      position: 1, code: "INS_terminal_2.50", type: "insTerm", connector: "XHP-4",
      card: { name: "Монтаж", steps: [{ number: "1", kind: "Операция", text: "Вставить контакты" }] },
      fields: { wires: [{ art: "красный", pins: [1] }] },
      housing: { title: "JST XH", art: "XHP-4", rows: 1, cols: 4, n: 4, color: "#efe6cf", conf: "medium", grid: [[1, 2, 3, 4]], round: null },
    }], warnings: [],
  } };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/projects") return json({ items: [withHousing] });
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [] });
    if (path.endsWith("/revisions")) return json({ items: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<TechcardsPage principal={engineer} />);
  expect(await screen.findByText("JST XH")).toBeInTheDocument();
  expect(screen.getByTitle("Контакт 1: красный")).toBeInTheDocument();
  expect(screen.getByText(/Со стороны проводов/)).toBeInTheDocument();
});

it("NAV-1: «Шаблоны техкарт» shows only the operation library", async () => {
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    paths.push(path);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Резка", steps: 1, tooling: 1 },
    ] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<TechcardsPage principal={engineer} view="templates" />);

  expect(await screen.findByText("CUT_WIRE_auto")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Шаблоны техкарт", level: 1 })).toBeInTheDocument();
  expect(screen.queryByText("Новая карточка изделия")).not.toBeInTheDocument();
  expect(paths).toEqual(["/api/techcards/templates"]);
});
