import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProfilePanel } from "./ProfilePanel";

function json(body: unknown, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

const PROFILE = {
  name: "Закупкин", login: "buyer", email: "buyer@example.test", department: "Снабжение",
  role: "buyer", role_label: "Технический руководитель", access_level: "tech_lead", access_level_label: "Технический руководитель",
  job_title: "Закупщик", site: "", site_label: "все площадки",
};

it("shows access level, position, department and site and changes the own password", async () => {
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

  render(<ProfilePanel />);
  fireEvent.click(screen.getByRole("button", { name: "Профиль" }));
  expect(await screen.findByText("Технический руководитель")).toBeInTheDocument();
  expect(screen.getByText("Уровень доступа")).toBeInTheDocument();
  expect(screen.getByText("Закупщик", { selector: "dd" })).toBeInTheDocument();
  expect(screen.getByText("Снабжение")).toBeInTheDocument();
  expect(screen.getByText("все площадки")).toBeInTheDocument();

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

it("names the level from its key when the payload has no label, and shows no empty position", async () => {
  const { access_level_label: _label, job_title: _title, ...bare } = PROFILE;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input) === "/api/auth/profile") return json({ ...bare, access_level: "foreman", role_label: "legacy" });
    throw new Error(`Unexpected request: ${String(input)}`);
  });

  render(<ProfilePanel />);
  fireEvent.click(screen.getByRole("button", { name: "Профиль" }));
  expect(await screen.findByText("Бригадир")).toBeInTheDocument();
  expect(screen.queryByText("Должность")).not.toBeInTheDocument();
});

it("offers «Моя карточка» only where the screen can open it", () => {
  const open = vi.fn();
  const view = render(<ProfilePanel />);
  expect(screen.queryByRole("button", { name: "Моя карточка" })).not.toBeInTheDocument();
  view.unmount();

  render(<ProfilePanel openCard={open} />);
  fireEvent.click(screen.getByRole("button", { name: "Моя карточка" }));
  expect(open).toHaveBeenCalledTimes(1);
});
