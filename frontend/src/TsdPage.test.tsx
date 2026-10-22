import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Principal, WarehouseLot } from "./api";
import { App } from "./App";
import { TsdEntry, TsdPage, TsdScreen } from "./TsdPage";

const lot: WarehouseLot = {
  id: "40000000-0000-0000-0000-000000000001",
  version: 4,
  warehouse: "ЦАР",
  grn: "FRX.26.0000001",
  package: "",
  article: "GHR-04V-S",
  series: "S-12",
  name: "Разъём",
  item_type: "Комплектующие",
  purpose: "FRX.001",
  unit: "шт",
  storage_location: "1F4",
  inbound: "10",
  written_off: "2",
  reserved: "0",
  balance: "8",
  available: "8",
  source_receiving_ids: [],
};

const storekeeper: Principal = {
  user: { id: "10000000-0000-0000-0000-000000000009", email: "petrov@example.test", name: "Петров Иван", department: "Склад" },
  roles: ["operator"],
  access_level: "operator",
  permissions: { warehouse: "edit" },
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});

type Call = { method: string; path: string; body?: unknown };
type Route = (call: Call) => Response | Promise<Response>;

function stage(routes: Record<string, Route> = {}) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const call = { method: init?.method ?? "GET", path: String(input), body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    const route = routes[`${call.method} ${call.path}`];
    if (route) return route(call);
    return json({ detail: `Unexpected request: ${call.method} ${call.path}` }, 500);
  });
  return calls;
}

const scanOf = (code: string) => `GET /api/stock/scan?code=${encodeURIComponent(code)}`;

function scan(code: string) {
  const field = screen.getByLabelText("ГРН или место");
  fireEvent.change(field, { target: { value: code } });
  fireEvent.submit(field.closest("form")!);
}

it("a scanned ГРН opens the lot, «Списать» sends the lot's version and the field waits for the next scan", async () => {
  const calls = stage({
    [scanOf("FRX.26.0000001")]: () => json({ kind: "lot", code: "FRX.26.0000001", place: "", lots: [lot] }),
    [`POST /api/stock/batches/${lot.id}/writeoff`]: () => json({ lot: { ...lot, version: 5, written_off: "5", balance: "5", available: "5" } }),
  });
  render(<TsdPage readOnly={false} />);
  const field = screen.getByLabelText("ГРН или место");
  await waitFor(() => expect(field).toHaveFocus());
  expect(field).toHaveAttribute("inputmode", "text"); // no terminal in the test browser: the keyboard is allowed
  scan("FRX.26.0000001");
  const card = await screen.findByRole("region", { name: "Партия FRX.26.0000001" });
  expect(within(card).getByText("Разъём")).toBeInTheDocument();
  expect(card).toHaveTextContent("8 шт");
  expect(card).toHaveTextContent("GHR-04V-S · ЦАР · место 1F4 · серия S-12");
  expect(within(card).getByRole("button", { name: "Этикетка" })).toBeInTheDocument();

  fireEvent.click(within(card).getByRole("button", { name: "Списать" }));
  const ready = within(card).getByRole("button", { name: "Готово" });
  expect(ready).toBeDisabled();
  fireEvent.change(within(card).getByLabelText("Количество"), { target: { value: "3" } });
  expect(ready).toBeDisabled(); // «Куда» is required, as in SUPPLYMAP
  fireEvent.change(within(card).getByLabelText("Куда"), { target: { value: "FRX.001" } });
  fireEvent.click(ready);
  expect(await within(card).findByRole("status")).toHaveTextContent("Списано 3 шт → FRX.001. Остаток 5 шт");
  expect(calls.find((call) => call.path.endsWith("/writeoff"))?.body).toEqual({ version: 4, quantity: "3", purpose: "FRX.001", reason: "" });
  expect(card).toHaveTextContent("5 шт");
  await waitFor(() => expect(field).toHaveFocus());
  expect(within(card).getByRole("button", { name: "Отменить" })).toBeInTheDocument();
});

it("a place code lists what lies there; an empty place says so; an unknown code says it is neither", async () => {
  stage({
    [scanOf("1F4")]: () => json({ kind: "place", code: "1F4", place: "1F4", lots: [lot, { ...lot, id: "40000000-0000-0000-0000-000000000002", package: "П2", balance: "1" }] }),
    [scanOf("9Z9")]: () => json({ kind: "place", code: "9Z9", place: "9Z9", lots: [] }),
    [scanOf("ЧТО-ТО")]: () => json({ detail: "Не найдено: ЧТО-ТО" }, 404),
  });
  render(<TsdPage readOnly={false} />);
  scan("1F4");
  expect(await screen.findByText("Место")).toHaveTextContent("Место 1F4");
  const rows = screen.getAllByRole("button", { name: /FRX\.26\.0000001/ });
  expect(rows).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: /FRX\.26\.0000001 \/ П2/ }));
  const card = await screen.findByRole("region", { name: "Партия FRX.26.0000001 / П2" });
  fireEvent.click(within(card).getByRole("button", { name: "← К списку" }));
  expect(screen.getAllByRole("button", { name: /FRX\.26\.0000001/ })).toHaveLength(2);

  scan("9Z9");
  expect(await screen.findByText("Здесь пусто.")).toBeInTheDocument();
  scan("ЧТО-ТО");
  expect(await screen.findByRole("alert")).toHaveTextContent("Код «ЧТО-ТО» не найден — ни партии, ни места.");
});

it("a complete ГРН with no Enter opens by itself after the scanner's pause", async () => {
  const calls = stage({
    [scanOf("FRX.26.0000001-П2")]: () => json({ kind: "lot", code: "FRX.26.0000001-П2", place: "", lots: [{ ...lot, package: "П2" }] }),
  });
  render(<TsdPage readOnly={false} />);
  fireEvent.change(screen.getByLabelText("ГРН или место"), { target: { value: "FRX.26.0000001-П2" } });
  expect(await screen.findByRole("region", { name: "Партия FRX.26.0000001 / П2" }, { timeout: 4000 })).toBeInTheDocument();
  expect(calls.filter((call) => call.path.startsWith("/api/stock/scan"))).toHaveLength(1);
});

it("keys typed outside any field go into the scan field (a scan after a tap on the screen)", async () => {
  stage();
  render(<TsdPage readOnly={false} />);
  const field = screen.getByLabelText("ГРН или место");
  (document.activeElement as HTMLElement | null)?.blur();
  for (const key of "1F4") fireEvent.keyDown(document.body, { key });
  expect(field).toHaveValue("1F4");
});

it("«Вернуть» offers where the lot went; «Отменить» takes the action back", async () => {
  const calls = stage({
    [scanOf("FRX.26.0000001")]: () => json({ kind: "lot", code: "FRX.26.0000001", place: "", lots: [lot] }),
    [`GET /api/stock/batches/${lot.id}/issued-to`]: () => json({ destinations: [
      { name: "FRX.001", written_off: "2", returned: "0", outstanding: "2", unit: "шт", last_date: "2026-10-19" },
      { name: "Сидоров", written_off: "1", returned: "1", outstanding: "0", unit: "шт", last_date: "2026-10-18" },
    ] }),
    [`POST /api/stock/batches/${lot.id}/return`]: () => json({ lot: { ...lot, version: 5, written_off: "1", balance: "9", available: "9" } }),
    [`POST /api/stock/batches/${lot.id}/undo`]: () => json({ lot: { ...lot, version: 6 }, undone: "Возврат 1 шт" }),
  });
  render(<TsdPage readOnly={false} />);
  scan("FRX.26.0000001");
  const card = await screen.findByRole("region", { name: "Партия FRX.26.0000001" });
  fireEvent.click(within(card).getByRole("button", { name: "Вернуть" }));
  const pick = await within(card).findByRole("button", { name: "FRX.001 · 2 шт" });
  expect(within(card).queryByRole("button", { name: /Сидоров/ })).not.toBeInTheDocument(); // nothing left there
  fireEvent.click(pick);
  fireEvent.change(within(card).getByLabelText("Количество"), { target: { value: "1" } });
  fireEvent.click(within(card).getByRole("button", { name: "Готово" }));
  expect(await within(card).findByRole("status")).toHaveTextContent("Возвращено 1 шт. Остаток 9 шт");
  expect(calls.find((call) => call.path.endsWith("/return"))?.body).toEqual({ version: 4, quantity: "1", source: "FRX.001", reason: "" });
  fireEvent.click(within(card).getByRole("button", { name: "Отменить" }));
  await waitFor(() => expect(within(card).getByRole("status")).toHaveTextContent("Отменено: Возврат 1 шт"));
  expect(calls.find((call) => call.path.endsWith("/undo"))?.body).toEqual({ version: 5 });
});

it("«Переместить» takes the new place; «Этикетка» shows the label", async () => {
  const calls = stage({
    [scanOf("FRX.26.0000001")]: () => json({ kind: "lot", code: "FRX.26.0000001", place: "", lots: [lot] }),
    [`POST /api/stock/batches/${lot.id}/move`]: () => json({ lot: { ...lot, version: 5, storage_location: "12C7" } }),
    [`POST /api/stock/batches/${lot.id}/label`]: () => json({ status: "printed", reason: null, file: "x.prn", copies: 1 }),
  });
  render(<TsdPage readOnly={false} />);
  scan("FRX.26.0000001");
  const card = await screen.findByRole("region", { name: "Партия FRX.26.0000001" });
  fireEvent.click(within(card).getByRole("button", { name: "Переместить" }));
  fireEvent.change(within(card).getByLabelText("Новое место"), { target: { value: "12C7" } });
  fireEvent.click(within(card).getByRole("button", { name: "Готово" }));
  expect(await within(card).findByRole("status")).toHaveTextContent("Перемещено: 1F4 → 12C7");
  expect(calls.find((call) => call.path.endsWith("/move"))?.body).toEqual({ version: 4, storage_location: "12C7", warehouse: "" });

  fireEvent.click(await within(card).findByRole("button", { name: "Этикетка" }));
  expect(within(card).getByAltText("Этикетка партии").getAttribute("src")).toMatch(`/api/stock/batches/${lot.id}/label.png`);
  fireEvent.click(within(card).getByRole("button", { name: "Печать" }));
  await waitFor(() => expect(calls.find((call) => call.path.endsWith("/label"))?.body).toEqual({ copies: 1 }));
});

it("view only: the lot is shown, no actions", async () => {
  stage({
    [scanOf("FRX.26.0000001")]: () => json({ kind: "lot", code: "FRX.26.0000001", place: "", lots: [lot] }),
  });
  render(<TsdPage readOnly />);
  scan("FRX.26.0000001");
  const card = await screen.findByRole("region", { name: "Партия FRX.26.0000001" });
  expect(within(card).queryByRole("button", { name: "Списать" })).not.toBeInTheDocument();
  expect(within(card).queryByRole("button", { name: "Отменить последнее" })).not.toBeInTheDocument();
});

it("the screen hands over to the ordinary shell without the warehouse section", async () => {
  stage();
  const leave = vi.fn();
  render(<TsdScreen principal={{ ...storekeeper, permissions: {} }} onSignOut={() => undefined} onLeave={leave} />);
  await waitFor(() => expect(leave).toHaveBeenCalled());
  cleanup();
  vi.restoreAllMocks();
  stage();
  const stay = vi.fn();
  render(<TsdScreen principal={storekeeper} onSignOut={() => undefined} onLeave={stay} />);
  expect(await screen.findByText("FRAXIS · склад")).toBeInTheDocument();
  expect(screen.getByText("Петров Иван")).toBeInTheDocument();
  expect(stay).not.toHaveBeenCalled();
});

it("where the server has no ТСД entry the ТСД address keeps the ordinary sign-in", async () => {
  stage({ "GET /api/devices/entry": () => json({ detail: "Вход с устройств есть только на стенде." }, 404) });
  render(<TsdEntry onLogin={() => undefined} />);
  expect(await screen.findByRole("heading", { name: "Вход в систему" })).toBeInTheDocument();
  expect(screen.getByLabelText("Логин")).toBeInTheDocument();
});

it("on the ТСД: the employee is found by surname, signs in by PIN and is remembered on the device", async () => {
  const calls = stage({
    "GET /api/devices/entry": () => json({ tsd: true }),
    "GET /api/auth/login-staff": () => json({ staff: [
      { id: "u-1", name: "Иванов Пётр", department: "КБ" },
      { id: storekeeper.user.id, name: "Петров Иван", department: "Склад" },
    ] }),
    "POST /api/auth/pin-login": (call) => (call.body as { pin: string }).pin === "123456"
      ? json(storekeeper) : json({ detail: "Неверный сотрудник или PIN." }, 401),
  });
  const login = vi.fn();
  render(<TsdEntry onLogin={login} />);
  const who = await screen.findByLabelText("Кто вы");
  fireEvent.change(who, { target: { value: "петр" } });
  fireEvent.change(who, { target: { value: "петров" } });
  fireEvent.click(await screen.findByRole("button", { name: /Петров Иван/ }));
  const pin = screen.getByLabelText("PIN");
  await waitFor(() => expect(pin).toHaveFocus());
  fireEvent.change(pin, { target: { value: "12a34" } });
  expect(pin).toHaveValue("1234"); // digits only
  expect(screen.getByRole("button", { name: "Войти" })).toBeDisabled();
  fireEvent.change(pin, { target: { value: "111111" } });
  fireEvent.click(screen.getByRole("button", { name: "Войти" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Неверный сотрудник или PIN.");
  fireEvent.change(pin, { target: { value: "123456" } });
  fireEvent.click(screen.getByRole("button", { name: "Войти" }));
  await waitFor(() => expect(login).toHaveBeenCalledWith(storekeeper));
  expect(calls.filter((call) => call.path === "/api/auth/pin-login").map((call) => call.body))
    .toEqual([{ who: storekeeper.user.id, pin: "111111" }, { who: storekeeper.user.id, pin: "123456" }]);
  cleanup();
  render(<TsdEntry onLogin={login} />);
  expect(await screen.findByText("Петров Иван")).toBeInTheDocument(); // the same device: straight to the PIN
  expect(screen.getByLabelText("PIN")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Другой сотрудник" })).toBeInTheDocument();
});

it("the ТСД address of the whole app: PIN sign-in, then the ТСД screen", async () => {
  window.history.replaceState({}, "", "/?section=tsd");
  let signedIn = false;
  stage({
    "GET /api/auth/me": () => (signedIn ? json(storekeeper) : json({ detail: "Требуется вход в ERP" }, 401)),
    "GET /api/devices/entry": () => json({ tsd: true }),
    "GET /api/auth/login-staff": () => json({ staff: [{ id: storekeeper.user.id, name: "Петров Иван", department: "Склад" }] }),
    "POST /api/auth/pin-login": () => { signedIn = true; return json(storekeeper); },
    "GET /api/site-version": () => json({ version: "test" }),
  });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /Петров Иван/ }));
  fireEvent.change(screen.getByLabelText("PIN"), { target: { value: "123456" } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Войти" })); });
  expect(await screen.findByLabelText("ГРН или место")).toBeInTheDocument();
  expect(screen.getByText("FRAXIS · склад")).toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: "Основная навигация" })).not.toBeInTheDocument();
});
