import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { NOTIFICATIONS_CHANGED } from "./NotificationBell";
import { READ_DEADLINE_MS, READ_TIMEOUT_TEXT, type Principal, type RequestRow, type RequestSchema } from "./api";

/* Аудит F07/U01: фоновый 401 не стирает неотправленную пачку заявок, черновик живёт в памяти оболочки по
   пользователю, повтор после неизвестного результата с тем же ключом не создаёт дубль, зависшее чтение кончается. */

const person = (id: string, name: string): Principal => ({
  user: { id, email: `${id}@example.test`, name, department: "Закупки" },
  roles: ["tech_lead"],
  access_level: "tech_lead",
  permissions: { newrequest: "edit", myrequests: "edit" },
});
const USERS: Record<string, Principal> = {
  anna: person("10000000-0000-0000-0000-00000000000a", "Анна"),
  boris: person("10000000-0000-0000-0000-00000000000b", "Борис"),
};

const schema: RequestSchema = {
  required: ["purpose", "delivery_point", "name"],
  purposes: ["RND"],
  delivery_points: ["Москва"],
  units: ["шт"],
  categories: ["Общепром", "ИП", "ОП"],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
const unauthorized = () => json({ detail: "Требуется вход в ERP" }, 401);

/** A fake ERP with sessions and the server's creation-key rule: a repeated key returns what it created before. */
function fakeErp() {
  const state = {
    session: null as string | null,
    created: new Map<string, RequestRow[]>(),
    creations: 0,
    keys: [] as string[],
    /** The next create is applied, then the connection breaks before the answer arrives. */
    loseNextAnswer: false,
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (path === "/api/auth/login") {
      const { login } = JSON.parse(String(init?.body));
      state.session = login;
      return json(USERS[login]);
    }
    if (path === "/api/auth/logout") {
      state.session = null;
      return new Response(null, { status: 204 });
    }
    if (!state.session) return unauthorized();
    const me = USERS[state.session];
    if (path === "/api/auth/me") return json(me);
    if (path === "/api/notifications/unread") return json({ unread: 0 });
    if (path === "/api/requests/schema") return json(schema);
    if (path.startsWith("/api/requests/mine")) return json({ requests: [], total: 0 });
    if (path === "/api/requests" && method === "POST") {
      const key = new Headers(init?.headers).get("Idempotency-Key") ?? "";
      state.keys.push(key);
      const scoped = `${me.user.id}:${key}`;
      let rows = state.created.get(scoped);
      if (!rows) {
        state.creations += 1;
        const body = JSON.parse(String(init?.body)) as { requests: Array<{ name: string }> };
        rows = body.requests.map((item, index) => ({ legacy_id: `REQ-${state.creations}${index ? `-${index}` : ""}`, name: item.name }) as RequestRow);
        state.created.set(scoped, rows);
      }
      if (state.loseNextAnswer) {
        state.loseNextAnswer = false;
        throw new TypeError("network connection lost");
      }
      return json({ requests: rows }, 201);
    }
    throw new Error(`Unexpected request: ${method} ${path}`);
  });
  return state;
}

/** The bell's poll meets the expired session: the real chain bell → api → shell. */
function pollBell() {
  act(() => { window.dispatchEvent(new Event(NOTIFICATIONS_CHANGED)); });
}

async function signIn(login: string, scope: HTMLElement = document.body) {
  const box = within(scope);
  fireEvent.change(box.getByLabelText("Логин"), { target: { value: login } });
  fireEvent.change(box.getByLabelText("Пароль"), { target: { value: "secret" } });
  fireEvent.click(box.getByRole("button", { name: "Войти" }));
}

const nameCell = () => screen.findByLabelText("Наименование, строка 1") as Promise<HTMLInputElement>;

beforeEach(() => {
  window.history.replaceState({}, "", "/?section=newrequest");
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});

describe("background 401 (F07)", () => {
  it("keeps the typed row under the sign-in dialog and the same user goes on with it", async () => {
    const erp = fakeErp();
    erp.session = "anna";
    render(<App />);
    fireEvent.change(await nameCell(), { target: { value: "Разъём" } });

    erp.session = null;
    pollBell();
    const dialog = await screen.findByRole("dialog", { name: "Сессия истекла" });
    // The page is not unmounted: the typed row is still there, hidden under the opaque sign-in screen.
    expect((await nameCell()).value).toBe("Разъём");
    expect(document.querySelector(".app-shell")).toHaveAttribute("inert");

    await signIn("anna", dialog);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect((await nameCell()).value).toBe("Разъём");
    expect(document.querySelector(".app-shell")).not.toHaveAttribute("inert");
  });

  it("drops the user's drafts on «Выйти»", async () => {
    const erp = fakeErp();
    erp.session = "anna";
    render(<App />);
    fireEvent.change(await nameCell(), { target: { value: "Разъём" } });
    fireEvent.click(screen.getByRole("button", { name: "Выйти" }));
    await screen.findByRole("heading", { name: "Вход в систему" });
    await signIn("anna");
    expect((await nameCell()).value).toBe("");
  });
});

describe("hung reads (U01)", () => {
  it("ends a hung «who am I» with the message and an enabled «Повторить»", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let answer = false;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/auth/me") return answer ? json(USERS.anna) : new Promise<Response>(() => undefined);
      if (path === "/api/notifications/unread") return json({ unread: 0 });
      if (path === "/api/requests/schema") return json(schema);
      throw new Error(`Unexpected request: ${path}`);
    });
    window.history.replaceState({}, "", "/");
    render(<App />);
    expect(screen.getByText("Загружаем ERP…")).toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(READ_DEADLINE_MS); });
    expect(await screen.findByRole("alert")).toHaveTextContent(READ_TIMEOUT_TEXT);
    const retry = screen.getByRole("button", { name: "Повторить" });
    expect(retry).toBeEnabled();

    answer = true;
    fireEvent.click(retry);
    expect(await screen.findByText("Добрый день, Анна")).toBeInTheDocument();
  });
});
