import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LoginPage } from "./LoginPage";
import type { Principal } from "./api";

const PRINCIPAL: Principal = {
  user: { id: "u2", email: "petrov@example.test", name: "Петров Пётр", department: "Снабжение" },
  roles: ["tech_lead"],
  access_level: "tech_lead",
  permissions: {},
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  vi.restoreAllMocks();
});

it("signs in with a login and a password — no staff list, no PIN (owner 01.10.2026)", async () => {
  const bodies: unknown[] = [];
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/login") {
      bodies.push(JSON.parse(String(init?.body)));
      return json(PRINCIPAL);
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  const onLogin = vi.fn();
  render(<LoginPage onLogin={onLogin} />);
  expect(screen.queryByLabelText("Сотрудник")).toBeNull();
  expect(screen.queryByLabelText("PIN-код")).toBeNull();
  fireEvent.change(screen.getByLabelText("Логин"), { target: { value: "petrov" } });
  fireEvent.change(screen.getByLabelText("Пароль"), { target: { value: "secret-password" } });
  fireEvent.click(screen.getByRole("button", { name: "Войти" }));
  await waitFor(() => expect(onLogin).toHaveBeenCalledWith(PRINCIPAL));
  expect(bodies).toEqual([{ login: "petrov", password: "secret-password" }]);
  expect(fetchMock).not.toHaveBeenCalledWith("/api/auth/login-staff", expect.anything());
});

it("shows the server's refusal as is", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ detail: "Неверный логин или пароль" }, 401));
  render(<LoginPage onLogin={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Логин"), { target: { value: "petrov" } });
  fireEvent.change(screen.getByLabelText("Пароль"), { target: { value: "wrong-password" } });
  fireEvent.click(screen.getByRole("button", { name: "Войти" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Неверный логин или пароль");
});

it("«Показать» reveals the typed password and «Скрыть» hides it again (owner 22.10.2026)", () => {
  render(<LoginPage onLogin={vi.fn()} />);
  const password = screen.getByLabelText("Пароль");
  fireEvent.change(password, { target: { value: "secret-password" } });
  expect(password).toHaveAttribute("type", "password");
  fireEvent.click(screen.getByRole("button", { name: "Показать пароль" }));
  expect(password).toHaveAttribute("type", "text");
  expect(password).toHaveValue("secret-password");
  fireEvent.click(screen.getByRole("button", { name: "Скрыть пароль" }));
  expect(password).toHaveAttribute("type", "password");
});
