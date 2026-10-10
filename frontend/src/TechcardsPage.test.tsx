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
  roles: ["tech_lead"],
  access_level: "tech_lead",
  permissions: { techcards: "edit", erptctpl: "edit", tcgen: "edit" },
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

it("opens only the linked techcard from the project card, without a creation form", async () => {
  window.history.replaceState({}, "", `/?section=techcards&card=${card.id}`);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/projects") return json({ items: [card, { ...card, id: "other", project: "P-200" }] });
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [] });
    if (path.endsWith("/revisions")) return json({ items: [] });
    return json({});
  });

  render(<TechcardsPage principal={engineer} view="projects" />);
  expect(await screen.findByRole("heading", { name: "Техкарта 01" })).toBeInTheDocument();
  expect(screen.queryByText("Новая карточка изделия")).toBeNull();
  expect(screen.queryByText("P-200")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "← P-100" }));
  expect(window.location.search).toBe("?section=projects&project=P-100");
  window.history.replaceState({}, "", "/");
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

  render(<TechcardsPage principal={{ ...engineer, roles: ["admin"], access_level: "admin" }} />);
  const editor = await screen.findByLabelText("Конфигурация автоматической сборки");
  fireEvent.change(editor, { target: { value: JSON.stringify({ ops: [{ templateId: "one" }] }) } });
  // Одна кнопка: сборка ЭТК и выдача сразу, отдельного шага «Выдать» нет (как файл техкарты в SUPPLYMAP).
  fireEvent.click(screen.getByRole("button", { name: "Собрать по JSON" }));
  expect(await screen.findByText("Техкарта создана")).toBeInTheDocument();
  expect(screen.getByText("Собрать")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Выдать ЭТК" })).toBeNull();
  expect(screen.getByText(/ревизия 1/)).toBeInTheDocument();
});

it("keeps the generator's JSON configuration to superadmin and admin, whatever the matrix grants", async () => {
  const open = async (principal: Parameters<typeof TechcardsPage>[0]["principal"]) => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/techcards/projects") return json({ items: [card] });
      if (path === "/api/techcards/templates") return json({ metadata: null, items: [] });
      if (path.endsWith("/revisions")) return json({ items: [] });
      throw new Error(`Unexpected request: ${path}`);
    });
    return render(<TechcardsPage principal={principal} />);
  };

  const lead = await open(engineer);
  expect((await screen.findAllByText("P-100")).length).toBeGreaterThan(0);
  expect(screen.queryByLabelText("Конфигурация автоматической сборки")).not.toBeInTheDocument();
  lead.unmount();
  vi.restoreAllMocks();

  await open({ ...engineer, roles: ["superadmin"], access_level: "superadmin" });
  expect(await screen.findByLabelText("Конфигурация автоматической сборки")).toBeInTheDocument();
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

  const { container } = render(<TechcardsPage principal={engineer} />);
  const sketch = await screen.findByRole("region", { name: "Эскиз" });
  // Торец серии с номерами контактов и легенда «конт. — провод» — SVG, одна и та же разметка на экране и в печати.
  expect(sketch.querySelector("svg")?.textContent).toContain("конт. 1 — красный");
  expect(sketch.textContent).toContain("вид со стороны проводов");
  expect(container.textContent).toContain("Загрузить картинку эскиза");
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
  expect(screen.getByText(/операция 1 из 2/)).toBeInTheDocument();
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
  fireEvent.click(await screen.findByRole("button", { name: /Править/ }));
  expect(screen.getByText("Сохранение — ревизия 2")).toBeInTheDocument();
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

it("shows the operation field chips and the sketch on the ETK screen (renderEtk_ chips / etkDraw*)", async () => {
  const withFields: ProjectTechcard = { ...issuedEtk, document: { ...issuedEtk.document, operations: [{
    position: 1, code: "PRS_TERMINAL_auto", side: "A", connector: "XS1", type: "prsTerm",
    card: { name: "Опрессовка", steps: [{ number: "1", kind: "Операция", text: "Обжать контакт" }], tooling: [] },
    fields: { applicator: "AP-22", crimpHeight: "0,85", pullForce: "40" },
    times: { operation_seconds: "3,5", setup_seconds: "180" },
  }] } };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/projects") return json({ items: [withFields] });
    if (path.endsWith("/revisions")) return json({ items: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<TechcardsPage principal={engineer} view="projects" />);
  const chips = await screen.findByLabelText("Параметры операции");
  expect(chips).toHaveTextContent("АппликаторAP-22");
  expect(chips).toHaveTextContent("Высота обжима0,85 мм");
  expect(chips).toHaveTextContent("Отрыв40 Н");
  expect(chips).toHaveTextContent("Время3,5 с на шт. · подгот. 3 мин");
  expect(screen.getByText("Эскиз")).toBeInTheDocument();
  expect(screen.getByText("Высота обжима по жиле: 0,85 мм")).toBeInTheDocument();
});

it("lays an ETK operation out in production order with the soldering mode and pictured defects", async () => {
  const soldering: ProjectTechcard = { ...issuedEtk, document: { ...issuedEtk.document, operations: [{
    position: 1, code: "SDR_XT60", side: "A", connector: "XS1", type: "sdr", result_label: "Полуфабрикат",
    card: { name: "Пайка", safety: "ВАЖНО! ТРЕБОВАНИЕ БЕЗОПАСНОСТИ:\nЗащитные очки при пайке.",
      defects: "1. Дефекты паяного соединения:\nНедолив припоя: Чашечка заполнена менее чем на 75%.\n«Холодная» пайка: Матовая поверхность.",
      steps: [{ number: "1", kind: "Операция", text: "Паяльник ≥80 Вт / станция с массивным жалом; t° 350–380 °C", tolerance: "(+/-) 10 °C" }],
      tooling: [
        { section: "Расходники", number: "1", name: "Припой Sn63Pb37" },
        { section: "Оборудование", number: "1", name: "T21(R) | Станция паяльная" },
        { section: "Инструмент", number: "1", name: "Микроскоп" },
      ] },
    components: [{ art: "XT60-M", name: "Разъём XT60", norm: "1", unit: "шт" }],
    semis: [{ name: "Провод зачищенный", norm: "2", unit: "шт" }],
    result: [{ name: "Провод с XT60", norm: "1", unit: "шт" }],
    time: [{ name: "Пайка", norm: "1,5", unit: "мин" }],
  }] } };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/projects") return json({ items: [soldering] });
    if (path.endsWith("/revisions")) return json({ items: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<TechcardsPage principal={engineer} view="projects" />);
  const solder = await screen.findByLabelText("Режим пайки");
  expect(solder).toHaveTextContent(/Жало паяльника.*Массивное/);
  expect(solder).toHaveTextContent("Температура жала350–380 °C (±10 °C)");
  const headings = screen.getAllByRole("heading", { level: 4 }).map((item) => item.textContent);
  // «Эскиз» есть у каждой операции для технолога: туда загружают свою картинку.
  expect(headings).toEqual(["1Вход", "2Оснастка", "3Ход операции", "4Типовые дефекты · 2", "5Эскиз", "6Результат"]);
  const sections = [...document.querySelectorAll(".etk-op .tr-sec-h")].map((item) => item.textContent);
  expect(sections.slice(0, 3)).toEqual(["Инструмент", "Оборудование", "Расходники"]);
  expect(screen.getByText("Недолив припоя")).toBeInTheDocument();
  expect(document.querySelectorAll(".etk-def-pic > svg")).toHaveLength(2);
  expect(screen.getByText("Требование безопасности")).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Полуфабрикат" })).toBeInTheDocument();
  expect(screen.getByRole("row", { name: /^Провод с XT60 1\sшт$/ })).toBeInTheDocument();
  // Время — отдельной цифрой, без повтора наименования полуфабриката.
  const time = screen.getByLabelText("Расчётное время");
  expect(time).toHaveTextContent("Расчётное время1,5 мин");
  expect(time).not.toHaveTextContent("Провод с XT60");
});

it("shows per-wire times beside their rows and no «— Шт.» for a missing norm", async () => {
  const cutting: ProjectTechcard = { ...issuedEtk, document: { ...issuedEtk.document, kit: { part_quantity: "100" }, operations: [{
    position: 1, code: "CUT_WIRE+1crimp", side: "A", connector: "обжим", type: "cutWireCrimp", result_label: "Полуфабрикат",
    title: "CUT_WIRE+1crimp | Автомат резка + опрессовка", card: null,
    semis: [{ name: "Сростки", norm: "", unit: "Шт." }],
    result: [{ name: "ПВ3 250мм, обжатый терминал T1", norm: "200", unit: "Шт." }, { name: "ПВ3 300мм, обжатый терминал T1", norm: "100", unit: "Шт." }],
    time: [{ name: "ПВ3 250мм, обжатый терминал T1", norm: "100,5" }, { name: "ПВ3 300мм, обжатый терминал T1", norm: "69,5" }],
  }] } };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/projects") return json({ items: [cutting] });
    if (path.endsWith("/revisions")) return json({ items: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<TechcardsPage principal={engineer} view="projects" />);
  const time = await screen.findByLabelText("Расчётное время");
  expect(time).toHaveTextContent("Расчётное время, итого170 мин2 ч 50 мин · на партию 100 шт");
  expect(screen.getByRole("row", { name: /^ПВ3 250мм, обжатый терминал T1 200\sшт 100,5$/ })).toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent(/^Автомат резка \+ опрессовка/);
  expect(document.querySelector(".etk-op")).not.toHaveTextContent("— Шт.");
  expect(document.querySelector(".etk-op")).not.toHaveTextContent("cutWireCrimp");
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
  fireEvent.click(screen.getByRole("button", { name: /Печать \/ PDF/ }));

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

  fireEvent.change(screen.getByLabelText("Поиск: Операции"), { target: { value: "флюсом" } });
  expect(screen.queryByText("CUT_WIRE_auto")).not.toBeInTheDocument();
  expect(screen.getByText("SDR_TIN")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Поиск: Операции"), { target: { value: "" } });
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

  fireEvent.click(screen.getByRole("button", { name: /Править/ }));
  expect(screen.getByText("Работать в очках")).toHaveClass("tc-preview");
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
  fireEvent.click(await screen.findByRole("button", { name: "Шаблоны-листы" }));
  expect(await screen.findByText("Автомат")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Редактировать CUT_WIRE | Резка" }));

  fireEvent.change(await screen.findByLabelText("Ячейка 2:3"), { target: { value: "Отрезать провод в размер" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить шаблон" }));
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
  fireEvent.click(await screen.findByRole("button", { name: "Шаблоны-листы" }));
  fireEvent.click(await screen.findByRole("button", { name: "Удалить черновик OLD | Черновик" }));
  await waitFor(() => expect(screen.queryByText("Открытые черновики:")).not.toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "Удалить STRIP | Разделка" }));
  expect(await screen.findByText("Шаблон удалён: STRIP | Разделка")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Новый шаблон" }));
  fireEvent.change(screen.getByLabelText("Название нового шаблона"), { target: { value: "NEW | Новый" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать черновик" }));
  expect(await screen.findByText("Черновик — NEW | Новый")).toBeInTheDocument();
  expect(screen.getAllByLabelText(/^Ячейка 12:/)).toHaveLength(7);
  expect(calls).toContain("DELETE /api/techcards/template-sheets/drafts/d2");
  expect(calls).toContain("DELETE /api/techcards/template-sheets/strip");
});

it("merges БД.ОП into the operation cards: op-only codes get a card and every card opens its БД.ОП row", async () => {
  const paths: string[] = [];
  const ops = [
    { id: "o1", tab: "op", display_text: "Резка", source_sheet: "БД.ОП", fields: { opNumber: "CUT_WIRE_auto", opName: "Резка", tOp: "3,50", "Номер": "CUT_WIRE_auto", "Машина": "EW-05F", "Тип операции": "Погонный" } },
    { id: "o2", tab: "op", display_text: "Лазерная маркировка", source_sheet: "БД.ОП", fields: { opNumber: "LASER_MARK", opName: "Лазерная маркировка", tOp: "12", tPrep: "60", "Номер": "LASER_MARK", "Машина": "Лазер", "Программа": "7" } },
  ];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    paths.push(path);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: libraryItems });
    if (path.startsWith("/api/references/tech-operations?")) return json({ metadata: null, records: ops });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<TechcardsPage principal={{ ...engineer, permissions: { ...engineer.permissions, erpref: "view" } }} view="templates" />);
  expect(await screen.findByText("LASER_MARK")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Все · 3" })).toBeInTheDocument();
  expect(screen.getByText("EW-05F · Погонный")).toBeInTheDocument();
  expect(screen.getByText("нет шаблона ТК")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Поиск: Операции"), { target: { value: "лазер" } });
  expect(screen.queryByText("CUT_WIRE_auto")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("LASER_MARK"));
  expect(await screen.findByText(/Шаблона листа \(БД\.ТК\) у этой операции нет/)).toBeInTheDocument();
  expect(screen.getByText("Программа")).toBeInTheDocument();
  expect(screen.getByText("60 с")).toBeInTheDocument();
  expect(paths.some((path) => path.includes("tab=op"))).toBe(true);
});
