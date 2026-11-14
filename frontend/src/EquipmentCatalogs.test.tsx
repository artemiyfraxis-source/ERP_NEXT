import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReferencesPage } from "./ReferencesPage";
import { EquipmentForm } from "./EquipmentForm";
import { EQUIPMENT_TABS } from "./referenceTabs";
import type { Principal, TechOperationRecord } from "./api";

const principal: Principal = {
  user: { id: "tester", email: "test@example.test", name: "Технолог", department: "ERP" },
  roles: ["admin"], access_level: "admin", permissions: { erpref: "edit" },
};
const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); history.replaceState({}, "", "/"); });

it("shows bound verification fields from the registry and never from stale catalog copies", () => {
  render(<EquipmentForm values={{ "Тип": "Средства измерений", "Наименование": "Прибор", "Заводской номер": "OLD",
    "Поверка": "Старое свидетельство", "Дата поверки": "01.01.2000" }} records={[]} isNew={false} onChange={vi.fn()}
    instrumentBound instrumentPassport={{ serial_number: "SERIAL", status: "Действует", verification_number: "NEW",
      verified_at: "2026-11-14T00:00:00", verification_due: "2027-11-14T00:00:00" }} />);
  expect(screen.getByLabelText("Поле Заводской номер")).toHaveValue("SERIAL");
  expect(screen.getByLabelText("Поле Поверка")).toHaveValue("NEW");
  expect(screen.getByLabelText("Поле Дата поверки")).toHaveValue("14.11.2026");
  expect(screen.getByLabelText("Поле Дата поверки")).toHaveAttribute("readonly");
  expect(screen.queryByDisplayValue("Старое свидетельство")).toBeNull();
});

it("six catalogs keep columns and counts separate, add/remove columns in the selected tab and delete the selected unit", async () => {
  const records: TechOperationRecord[] = Object.keys(EQUIPMENT_TABS).map((tab, index) => ({
    id: `unit-${index}`, tab: tab as TechOperationRecord["tab"], display_text: `Прибор ${index}`,
    source_sheet: "", equipment_model_id: `model-${index}`, fields: {
      "Наименование": `Прибор ${index}`, "Инв. номер": `ОБ.${index}`, "Тип": "Оборудование", "Свой столбец": "Да",
    },
  }));
  const calls: Array<{ path: string; method: string; body: unknown }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method && init.method !== "GET") {
      calls.push({ path, method: init.method, body: init.body ? JSON.parse(String(init.body)) : null });
      return json({ name: "Новый столбец" });
    }
    if (path.includes("tech-operations?")) {
      const params = new URL(path, "http://localhost").searchParams;
      const tab = params.get("tab");
      return json({ metadata: null, records: params.get("family") === "true" ? records : records.filter((row) => row.tab === tab),
        columns: ["Инв. номер", "Наименование", `Поле ${tab}`, "Свой столбец"], custom_columns: ["Свой столбец"] });
    }
    if (path.endsWith("/equipment-models")) return json({ models: [] });
    if (path.endsWith("/maps")) return json({ suppliers: [], countries: [], gost_awg: [] });
    return json({ bases: [], rows: [], codes: [], missing: [], views: {} });
  });
  render(<ReferencesPage principal={principal} initialTab="ob" />);
  for (const [tab, label] of Object.entries(EQUIPMENT_TABS)) {
    fireEvent.click(await screen.findByRole("button", { name: new RegExp(`^${label}(?: |$)`) }));
    expect(await screen.findByRole("columnheader", { name: `Поле ${tab}` })).toBeInTheDocument();
    for (const other of Object.keys(EQUIPMENT_TABS).filter((key) => key !== tab)) {
      expect(screen.queryByRole("columnheader", { name: `Поле ${other}` })).toBeNull();
    }
    expect(screen.getAllByText("1 моделей · 1 шт.").length).toBeGreaterThan(0);
  }
  fireEvent.click(screen.getByRole("button", { name: "+ Столбец" }));
  fireEvent.change(screen.getByLabelText("Название столбца"), { target: { value: "Новый столбец" } });
  fireEvent.click(screen.getByRole("button", { name: "Добавить столбец" }));
  await waitFor(() => expect(calls).toContainEqual({ path: "/api/references/tech-operations/columns", method: "POST", body: { tab: "ob_tools", name: "Новый столбец" } }));
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  fireEvent.click(screen.getByRole("button", { name: "Удалить столбец: Свой столбец" }));
  fireEvent.click(within(screen.getByRole("dialog", { name: "Удалить столбец" })).getByRole("button", { name: /Удалить/ }));
  await waitFor(() => expect(calls).toContainEqual({ path: "/api/references/tech-operations/columns/remove", method: "POST", body: { tab: "ob_tools", name: "Свой столбец" } }));
  fireEvent.click(screen.getByText("ОБ.5"));
  fireEvent.click(screen.getByRole("button", { name: "Удалить ОБ.5" }));
  expect(calls.some((call) => call.path === "/api/references/equipment/unit-5")).toBe(false);
  fireEvent.click(within(screen.getByRole("dialog", { name: "Удалить экземпляр" })).getByRole("button", { name: "Удалить" }));
  await waitFor(() => expect(calls).toContainEqual({ path: "/api/references/equipment/unit-5", method: "DELETE", body: null }));
});

it("hides the previous catalog while the next catalog is loading", async () => {
  let resolveMeasuring: ((response: Response) => void) | undefined;
  const record = { id: "one", tab: "ob", display_text: "Станок", source_sheet: "", fields: { "Наименование": "Станок", "Инв. номер": "ОБ.1" } };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.includes("tab=ob_measuring")) return new Promise<Response>((resolve) => { resolveMeasuring = resolve; });
    if (path.includes("tab=ob")) return json({ metadata: null, records: [record] });
    if (path.includes("tech-operations?")) return json({ metadata: null, records: [] });
    if (path.endsWith("/maps")) return json({ suppliers: [], countries: [], gost_awg: [] });
    return json({ bases: [], rows: [], models: [], codes: [], missing: [] });
  });
  render(<ReferencesPage principal={principal} initialTab="ob" />);
  expect(await screen.findByText("ОБ.1")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^Средства измерений/ }));
  expect(screen.queryByText("ОБ.1")).toBeNull();
  expect(screen.queryByRole("button", { name: "+ Новая запись" })).toBeNull();
  resolveMeasuring!(json({ metadata: null, records: [], columns: ["Заводской номер"] }));
  expect(await screen.findByRole("columnheader", { name: "Заводской номер" })).toBeInTheDocument();
});

it.each(["Наменование", "Наименование"])("preserves a name stored only under %s when another field changes", async (nameKey) => {
  const alias = nameKey === "Наменование" ? "Наименование" : "Наменование";
  const record = { id: "machine", tab: "ob", display_text: "Станок", source_sheet: "", fields: {
    "Тип": "Оборудование", [nameKey]: "Станок", "Инв. номер": "ОБ.001", "Артикул": "A1",
  } };
  const writes: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "PATCH") { writes.push(JSON.parse(String(init.body))); return json(record); }
    if (path.includes("tech-operations?")) {
      const params = new URL(path, "http://localhost").searchParams;
      return json({ metadata: null, columns: ["Инв. номер", alias, "Артикул"],
        records: params.get("tab") === "ob" ? [record] : [] });
    }
    if (path.endsWith("/maps")) return json({ suppliers: [], countries: [], gost_awg: [] });
    return json({ bases: [], rows: [], models: [], codes: [], missing: [], owners: {} });
  });
  render(<ReferencesPage principal={principal} initialTab="ob" />);
  fireEvent.click(await screen.findByText("ОБ.001"));
  fireEvent.click(screen.getByRole("button", { name: "Править запись" }));
  expect(screen.getByLabelText("Наименование")).toHaveValue("Станок");
  fireEvent.change(screen.getByLabelText("Артикул"), { target: { value: "A2" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить запись" }));
  await waitFor(() => expect(writes).toEqual([{ edits: [{ field: "Артикул", old: "A1", value: "A2" }] }]));
});

it("edits a linked matrix from another catalog, including its historical name field", async () => {
  const tool = { id: "tool", tab: "ob_tools", display_text: "Пресс", source_sheet: "", fields: {
    "Тип": "Инструмент", "Подтип": "Обжимной инструмент", "Наменование": "Пресс", "Инв. номер": "ОБ.1",
    "Код в базе терминалов": "M8", "Обжимает (серии разъёмов)": '[{"kind":"series","key":"zh","label":"JST ZH"}]',
  } };
  const die = { id: "die", tab: "ob_dies", display_text: "Матрица", source_sheet: "", fields: {
    "Тип": "Матрицы", "Наменование": "Матрица", "Инв. номер": "ОСН.1", "Для инструмента": "ОБ.1",
  } };
  const writes: Array<{ path: string; body: unknown }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "PATCH") { writes.push({ path, body: JSON.parse(String(init.body)) }); return json(die); }
    if (path.endsWith("/crimps")) return json({ code: "M8", terminals: [], targets: [] });
    if (path.includes("tech-operations?")) {
      const params = new URL(path, "http://localhost").searchParams;
      const records = [tool, die];
      return json({ metadata: null, columns: ["Инв. номер", "Наименование", "Код в базе терминалов", "Обжимает (серии разъёмов)"],
        records: params.get("family") === "true" ? records : records.filter((row) => row.tab === params.get("tab")) });
    }
    if (path.endsWith("/maps")) return json({ suppliers: [], countries: [], gost_awg: [] });
    return json({ bases: [], rows: [], models: [], codes: [], missing: [], owners: {} });
  });
  render(<ReferencesPage principal={principal} initialTab="ob_tools" />);
  expect(await screen.findByRole("columnheader", { name: "Код" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Обжимает (серии разъёмов)" })).toBeInTheDocument();
  expect(screen.getByText("M8")).toBeInTheDocument();
  expect(screen.getByText("JST ZH")).toBeInTheDocument();
  fireEvent.click(await screen.findByText("ОБ.1"));
  expect(await screen.findByRole("heading", { name: /Что обжимает/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /ОСН.1 Матрица/ }));
  fireEvent.click(screen.getByRole("button", { name: "Править запись" }));
  expect(screen.getByLabelText("Тип")).toHaveValue("Матрицы");
  expect(screen.getByLabelText("Наименование")).toHaveValue("Матрица");
  fireEvent.change(screen.getByLabelText("Наименование"), { target: { value: "Матрица новая" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить запись" }));
  await waitFor(() => expect(writes).toContainEqual({ path: "/api/references/tech-operations/die", body: {
    edits: expect.arrayContaining([
      { field: "Наменование", old: "Матрица", value: "Матрица новая" },
      { field: "Наименование", old: "", value: "Матрица новая" },
    ]),
  } }));
});
