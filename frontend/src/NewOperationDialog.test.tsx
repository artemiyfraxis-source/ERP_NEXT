import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { NewOperationDialog, errorField } from "./NewOperationDialog";
import { TechcardsPage } from "./TechcardsPage";
import type { Principal } from "./api";

const operations = [{ code: "CUT_WIRE_auto", name: "Резка" }, { code: "LASER_MARK", name: "Лазерная маркировка" }];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });

function stubCreate(reply: () => Response) {
  const bodies: Array<Record<string, unknown>> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input) === "/api/techcards/operations" && init?.method === "POST") {
      bodies.push(JSON.parse(String(init.body)));
      return reply();
    }
    throw new Error(`Unexpected request: ${String(input)}`);
  });
  return bodies;
}

const created = { code: "NEW_1", name: "Новая", record: { id: "n1", tab: "op", display_text: "Новая", source_sheet: "БД.ОП", fields: {} }, templates: [] };

it("creates a blank operation with code, name and optional section", async () => {
  const bodies = stubCreate(() => json(created, 201));
  const onCreated = vi.fn();
  render(<NewOperationDialog operations={operations} onClose={() => undefined} onCreated={onCreated} />);
  const submit = screen.getByRole("button", { name: "Создать операцию" });
  expect(submit).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Номер"), { target: { value: " NEW_1 " } });
  fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Новая" } });
  fireEvent.change(screen.getByLabelText("Раздел"), { target: { value: "Резка" } });
  fireEvent.click(submit);
  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  expect(bodies).toEqual([{ code: "NEW_1", name: "Новая", category: "Резка" }]);
});

it("copies a picked operation found by search", async () => {
  const bodies = stubCreate(() => json(created, 201));
  const onCreated = vi.fn();
  render(<NewOperationDialog operations={operations} onClose={() => undefined} onCreated={onCreated} />);
  fireEvent.change(screen.getByLabelText("Номер"), { target: { value: "NEW_1" } });
  fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Новая" } });
  fireEvent.click(screen.getByRole("tab", { name: /Копия/ }));
  expect(screen.getByRole("button", { name: "Создать операцию" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Поиск операции"), { target: { value: "лазер" } });
  expect(screen.queryByRole("button", { name: /Резка/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Лазерная маркировка/ }));
  expect(screen.getByRole("tab", { name: "Копия: Лазерная маркировка" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Создать операцию" }));
  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  expect(bodies).toEqual([{ code: "NEW_1", name: "Новая", copy_of: "LASER_MARK" }]);
});

it("shows a taken code under «Номер», not as a banner", async () => {
  const message = "Операция с номером «CUT_WIRE_auto» уже есть — номер операции должен быть уникальным";
  stubCreate(() => json({ detail: message }, 422));
  const onCreated = vi.fn();
  render(<NewOperationDialog operations={operations} onClose={() => undefined} onCreated={onCreated} />);
  fireEvent.change(screen.getByLabelText("Номер"), { target: { value: "CUT_WIRE_auto" } });
  fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Дубль" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать операцию" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(message);
  expect(alert.closest("label")).toHaveTextContent("Номер");
  expect(screen.getByLabelText("Номер")).toHaveAttribute("aria-invalid", "true");
  expect(onCreated).not.toHaveBeenCalled();
});

it("routes error texts to the right field", () => {
  expect(errorField("Укажите номер операции")).toBe("code");
  expect(errorField("Укажите название операции")).toBe("name");
  expect(errorField("Что-то ещё")).toBe("other");
});

const principal: Principal = {
  user: { id: "u1", email: "t@example.test", name: "Технолог", department: "Производство" },
  roles: ["tech_lead"], access_level: "tech_lead",
  permissions: { techcards: "edit", erptctpl: "edit", erpref: "edit", tcgen: "edit" },
};
const opRow = { id: "o1", tab: "op", display_text: "Резка", source_sheet: "БД.ОП", fields: { opNumber: "CUT_WIRE_auto", opName: "Резка", "Номер": "CUT_WIRE_auto", "Название": "Резка", "Машина": "EW-05F" } };

function stubPage(posts: Array<Record<string, unknown>> = []) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [] });
    if (path.startsWith("/api/references/tech-operations?")) return json({ metadata: null, records: [opRow] });
    if (path === "/api/techcards/operations" && init?.method === "POST") {
      posts.push(JSON.parse(String(init.body)));
      return json({ code: "NEW_1", name: "Новая", record: { ...opRow, id: "n1", fields: { opNumber: "NEW_1", opName: "Новая", "Номер": "NEW_1", "Название": "Новая", "Машина": "" } }, templates: [] }, 201);
    }
    throw new Error(`Unexpected request: ${path}`);
  });
}

it("hides «Новая операция» without both rights", async () => {
  stubPage();
  const { rerender } = render(<TechcardsPage principal={{ ...principal, permissions: { ...principal.permissions, erpref: "view" } }} view="templates" />);
  await screen.findByText("CUT_WIRE_auto");
  expect(screen.queryByRole("button", { name: "Новая операция" })).toBeNull();
  rerender(<TechcardsPage principal={{ ...principal, permissions: { ...principal.permissions, erptctpl: "view" } }} view="templates" />);
  expect(screen.queryByRole("button", { name: "Новая операция" })).toBeNull();
});

it("creates an operation from the toolbar and opens its card in edit mode", async () => {
  const posts: Array<Record<string, unknown>> = [];
  stubPage(posts);
  render(<TechcardsPage principal={principal} view="templates" />);
  await screen.findByText("CUT_WIRE_auto");
  fireEvent.click(screen.getByRole("button", { name: "Новая операция" }));
  fireEvent.change(screen.getByLabelText("Номер"), { target: { value: "NEW_1" } });
  fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Новая" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать операцию" }));
  await waitFor(() => expect(posts).toEqual([{ code: "NEW_1", name: "Новая" }]));
  expect(await screen.findByRole("button", { name: "Сохранить" })).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).toBeNull();
});
