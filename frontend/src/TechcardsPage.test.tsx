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

const issuedEtk: ProjectTechcard = { ...card, version: 4, status: "Выдана", revision: 1, document: {
  kit: { project: "P-100", index: "01", name: "Жгут" },
  operations: [
    {
      position: 1, code: "CUT_WIRE_auto", side: "", connector: "1", type: "cutWire",
      card: { name: "Резка", safety: "ВАЖНО! ТРЕБОВАНИЕ БЕЗОПАСНОСТИ:\nОчки", defects: "", steps: [{ number: "1", kind: "Операция", text: "Отрезать провод", tolerance: "" }],
        tooling: [{ section: "Оборудование", number: "1", name: "Автомат резки", inventory_number: "ОБ.001", program: "5", parameters: "", norm: "", unit: "" }] },
      times: { operation_seconds: "3,5", setup_seconds: "180" },
      result: [{ name: "Провод", norm: "1", unit: "Шт." }], time: [{ name: "Провод", norm: "0,5" }],
    },
    {
      position: 2, code: "PRS_TERMINAL_auto", side: "A", connector: "XS1", type: "prsTerm",
      card: { name: "Опрессовка", steps: [{ number: "1", kind: "Контроль", text: "Проверить высоту обжима", tolerance: "1,2 мм" }], tooling: [] },
    },
  ],
  warnings: [],
} };

it("walks the ETK route with ← → like SUPPLYMAP renderEtk_", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/projects") return json({ items: [issuedEtk] });
    if (path.endsWith("/revisions")) return json({ items: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<TechcardsPage principal={engineer} view="projects" />);
  expect(await screen.findByText("Отрезать провод")).toBeInTheDocument();
  expect(screen.getByText(/Операция 1 из 2/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "← Назад" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Далее: Опрессовка →" }));
  expect(await screen.findByText("Проверить высоту обжима")).toBeInTheDocument();
  expect(screen.queryByText("Отрезать провод")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Далее: — →" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "← Назад" }));
  expect(await screen.findByText("Отрезать провод")).toBeInTheDocument();
});

it("edits an issued ETK operation from the UI and shows the new revision (portalEtkSaveOp)", async () => {
  let sent: { path: string; body: { version: number; operation: Record<string, unknown> } } | null = null;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/techcards/projects") return json({ items: [issuedEtk] });
    if (path.endsWith("/revisions")) return json({ items: [] });
    if (path.includes("/operations/") && init?.method === "PUT") {
      sent = { path, body: JSON.parse(String(init.body)) };
      const document = issuedEtk.document as { operations: Array<Record<string, unknown>> };
      return json({ ...issuedEtk, version: 5, revision: 2, document: { ...document, operations: [
        { ...sent.body.operation, edited: { at: "29.09.2026 10:00", by: "Инженер" } }, document.operations[1],
      ] } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<TechcardsPage principal={engineer} view="projects" />);
  fireEvent.click(await screen.findByRole("button", { name: "Править" }));
  expect(screen.getByText(/поднимет ревизию до 2/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Шаг 1"), { target: { value: "Отрезать провод по КД" } });
  fireEvent.click(screen.getByRole("button", { name: "＋ Контроль" }));
  fireEvent.change(screen.getByLabelText("Шаг 2"), { target: { value: "Измерить длину" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

  expect(await screen.findByText("Операция сохранена · ревизия 2")).toBeInTheDocument();
  expect(sent!.path).toBe(`/api/techcards/projects/${card.id}/operations/${encodeURIComponent("CUT_WIRE_auto::1::")}`);
  expect(sent!.body.version).toBe(4);
  const sentCard = sent!.body.operation.card as { safety: string; steps: Array<{ kind: string; text: string }>; tooling: Array<{ inventory_number: string }> };
  expect(sentCard.steps).toEqual([
    { kind: "Операция", text: "Отрезать провод по КД", tolerance: "" },
    { kind: "Контроль", text: "Измерить длину", tolerance: "" },
  ]);
  expect(sentCard.safety).toBe("Очки");
  expect(sentCard.tooling[0].inventory_number).toBe("ОБ.001");
  expect(await screen.findByText(/правлена 29.09.2026 10:00 · Инженер/)).toBeInTheDocument();
});

it("prints the ETK through the shared print contract (etkPrint_)", async () => {
  const written: string[] = [];
  const win = { document: { open: vi.fn(), write: (html: string) => written.push(html), close: vi.fn() }, focus: vi.fn(), print: vi.fn() };
  vi.spyOn(window, "open").mockReturnValue(win as unknown as Window);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/projects") return json({ items: [issuedEtk] });
    if (path.endsWith("/revisions")) return json({ items: [{ id: "r1", revision: 1, reason: "Первая выдача", actor_name: "Инженер", occurred_at: "2026-09-29T10:00:00+03:00", document: {} }] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<TechcardsPage principal={engineer} view="projects" />);
  await screen.findByText("Отрезать провод");
  await waitFor(() => expect(screen.getByText(/Первая выдача/)).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Печать / PDF" }));

  expect(win.print).toHaveBeenCalled();
  const html = written.join("");
  expect(html).toContain("Технологическая карта сборки");
  expect(html).toContain("ТК.P-100.CUT_WIRE_auto");
  expect(html).toContain("Операция 2. Опрессовка — XS1");
  expect(html).toContain("Автомат резки");
  expect(html).toContain("Первая выдача");
  expect(html).toContain("ГОСТ 3.1407-86");
});

const libraryItems = [
  { id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Сборка", steps: 2, controls: 1, tooling: 1,
    first_step: "Отрезать провод", step_text: "Отрезать провод Проверить длину", norms: { operation_seconds: "3,50", setup_seconds: "180" }, norms_known: true,
    changed_at: "2026-09-28T10:00:00+03:00", changed_by: "Технолог" },
  { id: "sdr-a", code: "SDR_TIN", side: "A", title: "SDR_TIN | Лужение (A)", name: "Лужение", category: "Сборка", steps: 1, controls: 0, tooling: 0,
    first_step: "Облудить жилу флюсом", step_text: "Облудить жилу флюсом", norms: null, norms_known: true, changed_at: null, changed_by: "" },
  { id: "sdr-b", code: "SDR_TIN", side: "B", title: "SDR_TIN | Лужение (B)", name: "Лужение", category: "Сборка", steps: 1, controls: 0, tooling: 0,
    first_step: "Облудить жилу флюсом", step_text: "Облудить жилу флюсом", norms: null, norms_known: true, changed_at: null, changed_by: "" },
];

it("groups the library by category chips with counts, catalog meta and step-text search (openTcRecords)", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: libraryItems });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<TechcardsPage principal={engineer} view="templates" />);
  expect(await screen.findByRole("button", { name: "Все · 2" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Резка · 1" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Пайка и лужение · 1" })).toBeInTheDocument();
  expect(screen.getByText(/2 шаг\. · 1 контр\. · 1 оснастки · 3,50 с/)).toBeInTheDocument();
  expect(screen.getByText("правлена 28.09")).toBeInTheDocument();
  expect(screen.getByText("нет норм")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Поиск шаблона"), { target: { value: "флюсом" } });
  expect(screen.queryByText("CUT_WIRE_auto")).not.toBeInTheDocument();
  expect(screen.getByText("SDR_TIN")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Поиск шаблона"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Резка · 1" }));
  expect(screen.queryByText("SDR_TIN")).not.toBeInTheDocument();
});

it("shows a template as a techcard sheet and saves an edit with who/when (portalTcRecordSave)", async () => {
  let sent: Record<string, unknown> | null = null;
  const detail = {
    ...libraryItems[0], changed_at: null, changed_by: "",
    document: {
      safety: "ВАЖНО! ТРЕБОВАНИЕ БЕЗОПАСНОСТИ:\nРаботать в очках", defects: "ТИПОВЫЕ ДЕФЕКТЫ: заусенец", revision: "",
      steps: [{ number: "1", kind: "Операция", text: "Отрезать провод", tolerance: "-" }, { number: "2", kind: "Контроль", text: "Проверить длину", tolerance: "±2 мм" }],
      tooling: [{ section: "Оборудование", number: "1", name: "Автомат резки", inventory_number: "ОБ.001", program: "5", parameters: "", norm: "", unit: "" }],
    },
    raw_rows: [],
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: libraryItems });
    if (path === "/api/techcards/templates/cut" && init?.method === "PUT") {
      sent = JSON.parse(String(init.body));
      return json({ ...detail, changed_at: "2026-09-29T10:00:00+03:00", changed_by: "Инженер",
        document: { ...detail.document, steps: [{ number: "1", kind: "Операция", text: "Отрезать провод в размер", tolerance: "-" }, detail.document.steps[1]] } });
    }
    if (path === "/api/techcards/templates/cut") return json(detail);
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<TechcardsPage principal={engineer} view="templates" />);
  fireEvent.click(await screen.findByText("CUT_WIRE_auto"));
  expect(await screen.findByText("Автомат резки")).toBeInTheDocument();
  expect(screen.getByText("ОБ.001")).toBeInTheDocument();
  expect(screen.getByText("прогр. 5")).toBeInTheDocument();
  expect(screen.getByText("3,50 с")).toBeInTheDocument();
  expect(screen.getByText("180 с")).toBeInTheDocument();
  expect(screen.getByText("Перенесено из шаблона БД.ТК, в ERP не правилась")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Править" }));
  expect(screen.getByLabelText("Требование безопасности")).toHaveValue("Работать в очках");
  fireEvent.change(screen.getByLabelText("Шаг 1"), { target: { value: "Отрезать провод в размер" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

  expect(await screen.findByText("Карточка CUT_WIRE_auto сохранена")).toBeInTheDocument();
  expect(sent).toMatchObject({
    name: "Резка",
    safety: "Работать в очках",
    steps: [{ kind: "Операция", text: "Отрезать провод в размер", tolerance: "-" }, { kind: "Контроль", text: "Проверить длину", tolerance: "±2 мм" }],
    tooling: [{ section: "Оборудование", name: "Автомат резки", inventory_number: "ОБ.001", program: "5" }],
  });
  expect(await screen.findByText(/Изменено .* · Инженер/)).toBeInTheDocument();
  expect(screen.getByText("Отрезать провод в размер")).toBeInTheDocument();
});

it("edits a template sheet through a draft and saves it (portalTcTplDraft → portalTcTplSave)", async () => {
  const sheet = { id: "cut-wire", title: "CUT_WIRE | Резка", category: "Резка", description: "Автомат", size: "12×7", updatedAt: "2026-09-23T12:00:00", savedBy: "" };
  const draft = {
    id: "d1", draftSheet: "Черновик — CUT_WIRE | Резка", existingTemplateId: "cut-wire", title: "CUT_WIRE | Резка",
    category: "Резка", description: "Автомат", createdAt: "2026-09-29T10:00:00", createdBy: "Инженер", updatedAt: "2026-09-29T10:00:00",
    rows: [["CUT_WIRE | Резка"], ["Операция", "1", "Отрезать провод"]],
  };
  const calls: Array<{ path: string; method: string; body: unknown }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [] });
    if (path === "/api/techcards/template-sheets") return json({ list: [sheet], drafts: [] });
    if (path === "/api/techcards/template-sheets/drafts" && method === "POST") return json(draft, 201);
    if (path === "/api/techcards/template-sheets/drafts/d1" && method === "PUT") return json(draft);
    if (path === "/api/techcards/template-sheets/drafts/d1/save") return json({ action: "updated", id: "cut-wire", title: "CUT_WIRE | Резка", sizeLabel: "2 x 3" });
    throw new Error(`Unexpected request: ${method} ${path}`);
  });

  render(<TechcardsPage principal={engineer} view="templates" />);
  fireEvent.click(await screen.findByRole("button", { name: "📄 Шаблоны-листы" }));
  expect(await screen.findByText("Автомат")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Редактировать CUT_WIRE | Резка" }));

  fireEvent.change(await screen.findByLabelText("Ячейка 2:3"), { target: { value: "Отрезать провод в размер" } });
  fireEvent.click(screen.getByRole("button", { name: "💾 Сохранить шаблон" }));
  expect(screen.getByText("Пересохранение существующего шаблона «CUT_WIRE | Резка» (контент заменится).")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

  expect(await screen.findByText("Шаблон обновлён: CUT_WIRE | Резка")).toBeInTheDocument();
  expect(calls.find((call) => call.method === "POST" && call.path.endsWith("/drafts"))?.body).toEqual({ template_id: "cut-wire", title: "" });
  const put = calls.find((call) => call.method === "PUT")?.body as { rows: string[][] };
  expect(put.rows[1].slice(0, 3)).toEqual(["Операция", "1", "Отрезать провод в размер"]);
  expect(put.rows[0]).toHaveLength(7);
  expect(calls.find((call) => call.path.endsWith("/save"))?.body).toEqual({ title: "CUT_WIRE | Резка", category: "Резка", description: "Автомат" });
});

it("creates a new template draft, drops a draft and deletes a template sheet", async () => {
  const sheet = { id: "strip", title: "STRIP | Разделка", category: "", description: "", size: "3×7", updatedAt: "2026-09-23T12:00:00", savedBy: "" };
  const open = { id: "d2", draftSheet: "Черновик — OLD | Черновик", existingTemplateId: "", title: "OLD | Черновик", category: "", description: "", createdAt: "", createdBy: "", updatedAt: "" };
  let sheets = [sheet];
  let drafts = [open];
  const calls: string[] = [];
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    calls.push(`${method} ${path}`);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [] });
    if (path === "/api/techcards/template-sheets") return json({ list: sheets, drafts });
    if (path === "/api/techcards/template-sheets/drafts/d2" && method === "DELETE") { drafts = []; return json({ dropped: true }); }
    if (path === "/api/techcards/template-sheets/strip" && method === "DELETE") { sheets = []; return json({ deleted: true, id: "strip", title: sheet.title }); }
    if (path === "/api/techcards/template-sheets/drafts" && method === "POST") {
      return json({ ...open, id: "d3", draftSheet: "Черновик — NEW | Новый", title: "NEW | Новый", rows: [] }, 201);
    }
    throw new Error(`Unexpected request: ${method} ${path}`);
  });

  render(<TechcardsPage principal={engineer} view="templates" />);
  fireEvent.click(await screen.findByRole("button", { name: "📄 Шаблоны-листы" }));
  fireEvent.click(await screen.findByRole("button", { name: "Удалить черновик OLD | Черновик" }));
  await waitFor(() => expect(screen.queryByText("Открытые черновики:")).not.toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "Удалить STRIP | Разделка" }));
  expect(await screen.findByText("Шаблон удалён: STRIP | Разделка")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "➕ Новый шаблон" }));
  fireEvent.change(screen.getByLabelText("Название нового шаблона"), { target: { value: "NEW | Новый" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать черновик" }));
  expect(await screen.findByText("Черновик — NEW | Новый")).toBeInTheDocument();
  expect(screen.getAllByLabelText(/^Ячейка 12:/)).toHaveLength(7);
  expect(calls).toContain("DELETE /api/techcards/template-sheets/drafts/d2");
  expect(calls).toContain("DELETE /api/techcards/template-sheets/strip");
});
