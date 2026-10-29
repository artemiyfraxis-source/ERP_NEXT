import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApprovalPin, ChangePassword, ProfilePanel } from "./ProfilePanel";

function json(body: unknown, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

const PROFILE = {
  name: "Закупкин", login: "buyer", email: "buyer@example.test", department: "Снабжение",
  role: "buyer", role_label: "Технический руководитель", access_level: "tech_lead", access_level_label: "Технический руководитель",
  job_title: "Закупщик", site: "", site_label: "все площадки",
};

it("«Вход и пароль» on the own card: shows the login and changes the own password", async () => {
  const sent: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/profile") return json(PROFILE);
    if (path === "/api/auth/password" && init?.method === "POST") {
      sent.push(JSON.parse(String(init.body)));
      return json(null, 204);
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ChangePassword />);
  expect(await screen.findByText("buyer")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Текущий пароль"), { target: { value: "old-password" } });
  fireEvent.change(screen.getByLabelText("Новый пароль"), { target: { value: "new-password-1" } });
  fireEvent.change(screen.getByLabelText("Повтор нового пароля"), { target: { value: "new-password-2" } });
  fireEvent.click(screen.getByRole("button", { name: "Сменить пароль" }));
  expect(await screen.findByText("Новый пароль и повтор не совпадают.")).toBeInTheDocument();
  expect(sent).toEqual([]);

  fireEvent.change(screen.getByLabelText("Повтор нового пароля"), { target: { value: "new-password-1" } });
  fireEvent.click(screen.getByRole("button", { name: "Сменить пароль" }));
  await waitFor(() => expect(sent).toEqual([{ current: "old-password", new: "new-password-1" }]));
  expect(await screen.findByText("Пароль изменён.")).toBeInTheDocument();
});

it("the sidebar footer has only «Моя карточка» — no «Профиль» (owner 14.10)", () => {
  const open = vi.fn();
  const view = render(<ProfilePanel />);
  expect(screen.queryByRole("button", { name: "Моя карточка" })).not.toBeInTheDocument();
  view.unmount();

  render(<ProfilePanel openCard={open} />);
  expect(screen.queryByRole("button", { name: "Профиль" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Моя карточка" }));
  expect(open).toHaveBeenCalledTimes(1);
});

it("«Разрешительный ПИН» on the own card: «не задан», then «Задать» with the current password and the PIN twice, masked", async () => {
  const sent: unknown[] = [];
  const answers: Array<[number, unknown]> = [
    [422, { detail: "Разрешительный ПИН должен отличаться от PIN входа." }],
    [200, { set: true, set_at: "2026-10-29T07:00:00+00:00" }],
  ];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/approval-pin" && init?.method === "POST") {
      sent.push(JSON.parse(String(init.body)));
      const [status, body] = answers.shift()!;
      return json(body, status);
    }
    if (path === "/api/auth/approval-pin") return json({ set: false, set_at: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ApprovalPin />);
  expect(await screen.findByText("Не задан")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Задать" }));
  const current = screen.getByLabelText("Текущий пароль или PIN входа");
  const pin = screen.getByLabelText("Новый разрешительный ПИН");
  const repeat = screen.getByLabelText("Повтор разрешительного ПИН");
  for (const field of [current, pin, repeat]) expect(field).toHaveAttribute("type", "password");

  fireEvent.change(current, { target: { value: "my-password-1" } });
  fireEvent.change(pin, { target: { value: "12345" } });
  fireEvent.change(repeat, { target: { value: "12345" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить ПИН" }));
  expect(await screen.findByText("Разрешительный ПИН — 6–8 цифр.")).toBeInTheDocument();
  fireEvent.change(pin, { target: { value: "482913" } });
  fireEvent.change(repeat, { target: { value: "482914" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить ПИН" }));
  expect(await screen.findByText("Новый разрешительный ПИН и повтор не совпадают.")).toBeInTheDocument();
  expect(sent).toEqual([]);

  fireEvent.change(repeat, { target: { value: "482913" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить ПИН" }));
  expect(await screen.findByText("Разрешительный ПИН должен отличаться от PIN входа.")).toBeInTheDocument();
  expect(current).toHaveValue("");  // the current secret is typed again after a refusal

  fireEvent.change(current, { target: { value: "my-password-1" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить ПИН" }));
  expect(await screen.findByText("Разрешительный ПИН задан.")).toBeInTheDocument();
  expect(sent).toEqual([
    { current: "my-password-1", pin: "482913" },
    { current: "my-password-1", pin: "482913" },
  ]);
  expect(screen.getByText("Задан")).toBeInTheDocument();
  expect(screen.getByText(/^· с 29.10.2026/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Новый разрешительный ПИН")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сменить" })).toBeInTheDocument();
});

it("«Разрешительный ПИН»: a load failure is said, not an endless «Загрузка…»", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ detail: "Нет доступа" }, 403));
  render(<ApprovalPin />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Нет доступа");
});
