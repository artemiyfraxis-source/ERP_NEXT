import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiTokens } from "./ApiTokens";
import { AccessPage } from "./AccessPage";
import type { Principal } from "./api";
import { hasFullAccess, visibleNav } from "./navigation";

const ROW = {
  scope_labels: [], created_by: "Директоров", revoked_by: "", revoked_at: null,
  created_at: "2026-10-01T10:00:00+03:00", last_used_at: null,
};
const TOKENS = [
  { ...ROW, id: "t1", name: "Генератор на ноутбуке", scopes: ["tcgen", "assembly"], status: "active", expires_at: "2026-12-30T10:00:00+03:00", last_used_at: "2026-10-03T09:15:00+03:00" },
  { ...ROW, id: "t2", name: "Старый", scopes: ["tcgen"], status: "expired", expires_at: "2026-09-01T10:00:00+03:00" },
  { ...ROW, id: "t3", name: "Потерян", scopes: ["assembly"], status: "revoked", expires_at: "2026-12-30T10:00:00+03:00", revoked_at: "2026-10-02T10:00:00+03:00" },
];
const SECRET = "erp_abcdef0123456789";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** The server as the tests need it: the list, `issue` and `revoke`; every call is recorded. */
function serve() {
  const calls: Array<{ path: string; method: string; body: unknown }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (path === "/api/auth/api-tokens" && method === "GET") return json({ tokens: TOKENS, scopes: {} });
    if (path === "/api/auth/api-tokens" && method === "POST") return json({ ...TOKENS[0], id: "t4", name: "Новый", token: SECRET }, 201);
    if (path === "/api/auth/api-tokens/t1/revoke") return json({ ...TOKENS[0], status: "revoked" });
    throw new Error(`Unexpected request: ${method} ${path}`);
  });
  return calls;
}

afterEach(() => vi.restoreAllMocks());

it("lists the tokens with Russian scope names, dates and statuses", async () => {
  serve();
  render(<ApiTokens />);
  const row = (await screen.findByText("Генератор на ноутбуке")).closest("tr") as HTMLElement;
  expect(within(row).getByText("Генератор техкарт, Черновики сборки")).toBeInTheDocument();
  expect(within(row).getByText("30.12.2026")).toBeInTheDocument();
  expect(within(row).getByText("03.10.2026 09:15")).toBeInTheDocument();
  expect(within(row).getByText("действует")).toHaveClass("tone-ok");
  expect(within(screen.getByText("Старый").closest("tr") as HTMLElement).getByText("истёк")).toHaveClass("tone-warn");
  const revoked = screen.getByText("Потерян").closest("tr") as HTMLElement;
  expect(within(revoked).getByText("отозван")).toHaveClass("tone-danger");
  expect(within(revoked).queryByRole("button", { name: "Отозвать" })).toBeNull();
  expect(screen.queryByText("tcgen")).toBeNull();
});

it("shows the secret once after issuing and forgets it on close", async () => {
  const calls = serve();
  render(<ApiTokens />);
  await screen.findByText("Генератор на ноутбуке");
  const issue = screen.getByRole("button", { name: "Выпустить токен" });
  expect(issue).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Новый" } });
  fireEvent.click(screen.getByLabelText("Генератор техкарт"));
  expect(screen.getByLabelText("Срок, дней")).toHaveValue(90);
  fireEvent.click(issue);

  const secret = await screen.findByLabelText("Токен");
  expect(secret).toHaveValue(SECRET);
  expect(secret).toHaveAttribute("readonly");
  expect(screen.getByText(/Токен показывается один раз — сохраните его сейчас\./)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Скопировать" })).toBeInTheDocument();
  expect(calls.find((call) => call.method === "POST")?.body).toEqual({ name: "Новый", scopes: ["tcgen"], days: 90 });

  fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));
  expect(screen.queryByLabelText("Токен")).toBeNull();
  expect(document.body.textContent).not.toContain(SECRET);
  expect(JSON.stringify(Object.entries(localStorage))).not.toContain(SECRET);
});

it("revokes only after the confirmation", async () => {
  const calls = serve();
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  render(<ApiTokens />);
  const row = (await screen.findByText("Генератор на ноутбуке")).closest("tr") as HTMLElement;
  fireEvent.click(within(row).getByRole("button", { name: "Отозвать" }));
  expect(calls.some((call) => call.path.endsWith("/revoke"))).toBe(false);
  fireEvent.click(within(row).getByRole("button", { name: "Отозвать" }));
  await waitFor(() => expect(calls.some((call) => call.path === "/api/auth/api-tokens/t1/revoke" && call.method === "POST")).toBe(true));
  expect(confirm).toHaveBeenCalledTimes(2);
});

it("puts the server's text into the error banner", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) =>
    (init?.method ?? "GET") === "GET" ? json({ tokens: [], scopes: {} }) : json({ detail: "Выберите хотя бы одну область токена." }, 422));
  render(<ApiTokens />);
  fireEvent.change(await screen.findByLabelText("Название"), { target: { value: "X" } });
  fireEvent.click(screen.getByLabelText("Черновики сборки"));
  fireEvent.click(screen.getByRole("button", { name: "Выпустить токен" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Выберите хотя бы одну область токена.");
  expect(screen.queryByLabelText("Токен")).toBeNull();
});

it("is a tab of «Доступы» for full access only", async () => {
  serve();
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input) === "/api/auth/access") return json({ sections: [], levels: [], defaults: {}, seed: {}, matrix: {}, users: [] });
    return json({ tokens: TOKENS, scopes: {} });
  });
  render(<AccessPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Токены API" }));
  expect(await screen.findByText("Генератор на ноутбуке")).toBeInTheDocument();

  // The page itself is a full-access-only nav item, so the tab never reaches anyone else.
  const user = { id: "1", email: "u@example.test", name: "Сотрудник", department: "КБ" };
  const as = (level: string): Principal => ({ user, roles: [level], access_level: level, permissions: {} });
  expect(hasFullAccess(as("admin"))).toBe(true);
  expect(visibleNav(as("admin")).some((item) => item.id === "access")).toBe(true);
  expect(visibleNav(as("operator")).some((item) => item.id === "access")).toBe(false);
});

it("explains why the issue button is disabled and has its own heading", async () => {
  serve();
  render(<ApiTokens />);
  await screen.findByText("Генератор на ноутбуке");
  expect(screen.getByRole("heading", { name: "Выпустить токен" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Выпустить токен" })).toBeDisabled();
  expect(screen.getByText("Укажите название и хотя бы одну область")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Новый" } });
  fireEvent.click(screen.getByLabelText("Генератор техкарт"));
  expect(screen.queryByText("Укажите название и хотя бы одну область")).toBeNull();
});

it("closes the open one-time secret panel after a revoke", async () => {
  serve();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<ApiTokens />);
  await screen.findByText("Генератор на ноутбуке");
  fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Новый" } });
  fireEvent.click(screen.getByLabelText("Генератор техкарт"));
  fireEvent.click(screen.getByRole("button", { name: "Выпустить токен" }));
  await screen.findByLabelText("Токен");
  fireEvent.click(screen.getAllByRole("button", { name: "Отозвать" })[0]);
  await waitFor(() => expect(screen.queryByLabelText("Токен")).toBeNull());
});
