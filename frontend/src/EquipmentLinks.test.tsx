import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReferencesPage } from "./ReferencesPage";
import type { Principal } from "./api";

const admin: Principal = {
  user: { id: "10000000-0000-0000-0000-000000000001", email: "admin@example.test", name: "Администратор", department: "ERP" },
  roles: ["admin"], access_level: "admin", permissions: { erpref: "edit", procurement: "edit" },
};

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); window.history.replaceState({}, "", "/"); });

type Call = { path: string; method: string; body: unknown };
const modelInfo = (id: string, role: string, confirmed: boolean, extra: Record<string, unknown> = {}) =>
  ({ id, name: "x", article: "", maker: "", role, role_confirmed: confirmed, display: "x", unit_ids: [], inventory_numbers: [], operations: 0, ...extra });
const emptyReport = {
  counts: { operations_unresolved: 0, tools_unresolved: 0, templates_unresolved: 0, models_without_units: 0, models_units_disagree: 0, roles_unconfirmed: 0 },
  operations: [], tools: [], templates: [], models_without_units: [], models_units_disagree: [], roles_unconfirmed: [],
};

function equipmentApi(calls: Call[], report: unknown = emptyReport) {
  const unit = (id: string, inv: string, name: string, model: string, role: string, extra: Record<string, unknown> = {}) => ({
    id, tab: "ob", display_text: name, source_sheet: "БД.ОБ", equipment_model_id: model, equipment_role: role,
    fields: { "Тип": "Оборудование", "Инв. номер": inv, "Наменование": name }, ...extra,
  });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (method !== "GET") { calls.push({ path, method, body: JSON.parse(String(init?.body ?? "null")) }); return json({}); }
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    if (path === "/api/references/equipment-models") {
      return json({ models: [modelInfo("m1", "operational", true, { name: "Станок", unit_ids: ["u1"] }), modelInfo("m2", "supporting", false, { name: "Компрессор", unit_ids: ["c1"] })] });
    }
    if (path === "/api/references/equipment-links/report") return json(report);
    if (path.includes("tab=ob")) {
      return json({ metadata: null, records: [unit("u1", "ОБ.010", "Станок", "m1", "operational"), unit("c1", "ОБ.200", "Компрессор", "m2", "supporting", { serves_unit_ids: ["u1"] })] });
    }
    if (path.includes("tab=op")) {
      return json({ metadata: null, records: [{ id: "o1", tab: "op", display_text: "o", source_sheet: "БД.ОП",
        fields: { "Номер": "10", "Название": "Резка", "Машина": "иначе названо" }, equipment_links: [{ column: "Машина", position: 0, model_id: "m1" }] }] });
    }
    if (path.includes("crimp-codes")) return json({ codes: [], missing: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
}

it("БД.ОБ: the card shows the kind, a supporting unit lists what it serves, tabs split the kinds", async () => {
  equipmentApi([]);
  render(<ReferencesPage principal={admin} initialTab="ob" />);
  fireEvent.click(await screen.findByText("ОБ.010"));
  let card = screen.getByRole("dialog", { name: "Карточка оборудования" });
  expect(within(card).getByLabelText("Вид оборудования")).toHaveValue("operational");
  // the operation is found through its id link although the «Машина» text differs
  expect(await within(card).findByText(/Резка/)).toBeInTheDocument();
  expect(within(card).getByText("Обслуживают")).toBeInTheDocument();
  fireEvent.click(within(card).getByRole("button", { name: "Закрыть карточку" }));

  fireEvent.click(screen.getByRole("tab", { name: /Обеспечивающее/ }));
  expect(screen.queryByText("ОБ.010")).toBeNull();
  fireEvent.click(screen.getByText("ОБ.200"));
  card = screen.getByRole("dialog", { name: "Карточка оборудования" });
  expect(await within(card).findByText("не подтверждён")).toBeInTheDocument();
  expect(within(card).getByRole("heading", { name: "Обслуживает" })).toBeInTheDocument();
  expect(within(card).getByText(/Станок/, { selector: ".eq-op" })).toBeInTheDocument();
});

it("БД.ОБ: the kind and «Обслуживает» are written by id", async () => {
  const calls: Call[] = [];
  equipmentApi(calls);
  render(<ReferencesPage principal={admin} initialTab="ob" />);
  fireEvent.click(await screen.findByText("ОБ.010"));
  fireEvent.change(within(screen.getByRole("dialog", { name: "Карточка оборудования" })).getByLabelText("Вид оборудования"), { target: { value: "supporting" } });
  await waitFor(() => expect(calls).toContainEqual({ path: "/api/references/equipment-models/m1/role", method: "PUT", body: { role: "supporting" } }));
  fireEvent.click(within(screen.getByRole("dialog", { name: "Карточка оборудования" })).getByRole("button", { name: "Закрыть карточку" }));

  fireEvent.click(screen.getByText("ОБ.200"));
  const card = screen.getByRole("dialog", { name: "Карточка оборудования" });
  fireEvent.click(within(card).getByRole("button", { name: "Выбрать машины" }));
  fireEvent.click(within(card).getByRole("checkbox")); // the one machine, already picked: off
  fireEvent.click(within(card).getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(calls).toContainEqual({ path: "/api/references/tech-operations/c1/serves", method: "PUT", body: { unit_ids: [] } }));
});

it("БД.ОБ: «Без привязки» lists the unresolved cells and the unconfirmed kinds, and confirms a kind", async () => {
  const calls: Call[] = [];
  equipmentApi(calls, {
    ...emptyReport,
    counts: { ...emptyReport.counts, operations_unresolved: 1, roles_unconfirmed: 1 },
    operations: [{ operation_id: "o9", number: "77", column: "Машина", position: 0, text: "Неведомый станок", reason: "unmatched", candidates: [] }],
    roles_unconfirmed: [modelInfo("m2", "supporting", false, { name: "Компрессор", display: "Компрессор", unit_ids: ["c1"], inventory_numbers: ["ОБ.200"] })],
  });
  render(<ReferencesPage principal={admin} initialTab="ob" />);
  const tab = await screen.findByRole("tab", { name: /Без привязки/ });
  await waitFor(() => expect(tab).toHaveTextContent("2"));
  fireEvent.click(tab);
  expect(screen.getByText("Неведомый станок")).toBeInTheDocument();
  expect(screen.getByText("нет такой модели")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Подтвердить «обеспечивающее»/ }));
  await waitFor(() => expect(calls).toContainEqual({ path: "/api/references/equipment-models/m2/role", method: "PUT", body: { role: "supporting" } }));
});
