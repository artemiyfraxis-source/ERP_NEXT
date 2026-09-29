import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LoginPage } from "./LoginPage";
import type { Principal } from "./api";

const STAFF = [
  { id: "u1", name: "Иванов Иван", department: "Цех" },
  { id: "u2", name: "Петров Пётр", department: "Снабжение" },
];

const PRINCIPAL: Principal = {
  user: { id: "u2", email: "petrov@example.test", name: "Петров Пётр", department: "Снабжение" },
  roles: ["buyer"],
  permissions: {},
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

it("signs in by picking yourself in the list and typing the PIN", async () => {
  const bodies: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/login-staff") return json({ staff: STAFF });
    if (path === "/api/auth/pin-login") {
      bodies.push(JSON.parse(String(init?.body)));
      return json(PRINCIPAL);
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  const onLogin = vi.fn();

  render(<LoginPage onLogin={onLogin} />);
  expect(await screen.findByRole("option", { name: "Петров Пётр · Снабжение" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Поиск сотрудника"), { target: { value: "петр" } });
  expect(screen.queryByRole("option", { name: "Иванов Иван · Цех" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Сотрудник"), { target: { value: "u2" } });
  fireEvent.change(screen.getByLabelText("PIN-код"), { target: { value: "123456" } });
  fireEvent.click(screen.getByRole("button", { name: "Войти" }));

  await waitFor(() => expect(onLogin).toHaveBeenCalledWith(PRINCIPAL));
  expect(bodies).toEqual([{ who: "u2", pin: "123456" }]);
  expect(window.localStorage.getItem("erp.login.who")).toBe("u2");
});

it("asks to pick yourself before signing in", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ staff: STAFF }));
  render(<LoginPage onLogin={vi.fn()} />);
  await screen.findByRole("option", { name: "Иванов Иван · Цех" });
  fireEvent.click(screen.getByRole("button", { name: "Войти" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Выбери себя в списке.");
});

it("shows the PIN once on the first sign-in with an empty PIN and fills it in (portalRevealPin_)", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/login-staff") return json({ staff: STAFF });
    if (path === "/api/auth/pin-login") return json({ reveal: true, name: "Иванов Иван", pin: "482913" });
    throw new Error(`Unexpected request: ${path}`);
  });
  const onLogin = vi.fn();

  render(<LoginPage onLogin={onLogin} />);
  await screen.findByRole("option", { name: "Иванов Иван · Цех" });
  fireEvent.change(screen.getByLabelText("Сотрудник"), { target: { value: "u1" } });
  fireEvent.click(screen.getByRole("button", { name: "Войти" }));

  const card = await screen.findByRole("dialog", { name: "Ваш PIN" });
  expect(card).toHaveTextContent("482913");
  expect(card).toHaveTextContent("второй раз он не покажется");
  fireEvent.click(screen.getByRole("button", { name: "Записал" }));
  expect(screen.getByLabelText("PIN-код")).toHaveValue("482913");
  expect(onLogin).not.toHaveBeenCalled();
});

it("shows the server's refusal as is", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/login-staff") return json({ staff: STAFF });
    return json({ detail: "Неверный сотрудник или PIN." }, 401);
  });
  render(<LoginPage onLogin={vi.fn()} />);
  await screen.findByRole("option", { name: "Иванов Иван · Цех" });
  fireEvent.change(screen.getByLabelText("Сотрудник"), { target: { value: "u1" } });
  fireEvent.change(screen.getByLabelText("PIN-код"), { target: { value: "000000" } });
  fireEvent.click(screen.getByRole("button", { name: "Войти" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Неверный сотрудник или PIN.");
});

it("keeps the login and password form next to the PIN", async () => {
  const bodies: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/login-staff") return json({ staff: STAFF });
    if (path === "/api/auth/login") {
      bodies.push(JSON.parse(String(init?.body)));
      return json(PRINCIPAL);
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  const onLogin = vi.fn();
  render(<LoginPage onLogin={onLogin} />);
  fireEvent.click(await screen.findByRole("button", { name: "Войти по логину и паролю" }));
  fireEvent.change(screen.getByLabelText("Логин"), { target: { value: "petrov" } });
  fireEvent.change(screen.getByLabelText("Пароль"), { target: { value: "secret-password" } });
  fireEvent.click(screen.getByRole("button", { name: "Войти" }));
  await waitFor(() => expect(onLogin).toHaveBeenCalled());
  expect(bodies).toEqual([{ login: "petrov", password: "secret-password" }]);
});
