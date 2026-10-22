import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WarehousePage } from "./WarehousePage";
import type { WarehouseLot } from "./api";

const lot: WarehouseLot = {
  id: "40000000-0000-0000-0000-000000000001",
  version: 4,
  warehouse: "ЦАР",
  grn: "FRX.26.0000001",
  package: "",
  article: "GHR-04V-S",
  series: "",
  name: "Разъём",
  item_type: "Комплектующие",
  purpose: "FRX.001",
  unit: "шт",
  storage_location: "1F4",
  inbound: "10",
  written_off: "0",
  reserved: "0",
  balance: "10",
  available: "10",
  source_receiving_ids: [],
};
const other: WarehouseLot = { ...lot, id: "40000000-0000-0000-0000-000000000002", grn: "OLD.26.0000005", storage_location: "1F4A", name: "Клемма" };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.localStorage.clear(); });

type Call = { method: string; path: string; body?: unknown };

function stage(routes: Record<string, (call: Call) => Response> = {}) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const call = { method: init?.method ?? "GET", path: String(input), body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    if (call.path.split("?")[0] === "/api/stock" && call.method === "GET") return json({ lots: [lot, other], total: 2, truncated: 0, next_cursor: null });
    const route = routes[`${call.method} ${call.path}`];
    if (route) return route(call);
    return json({ detail: `Unexpected request: ${call.path}` }, 500);
  });
  return calls;
}

async function scan(code: string) {
  const input = await screen.findByRole("textbox", { name: "Поиск по складу" });
  // The page must have loaded the stock: actions are refused while it is loading.
  await screen.findAllByRole("button", { name: /Показать партии/ });
  fireEvent.change(input, { target: { value: code } });
  fireEvent.submit(input.closest("form")!);
  return input;
}

it("a scanned ГРН opens its card; Enter in the quantity writes off through the existing endpoint", async () => {
  const calls = stage({
    "GET /api/stock/scan?code=frx.26.0000001": () => json({ kind: "lot", code: "frx.26.0000001", place: "", lots: [lot] }),
    [`POST /api/stock/batches/${lot.id}/writeoff`]: () => json({ lot: { ...lot, version: 5, written_off: "2", balance: "8", available: "8" } }),
  });
  render(<WarehousePage />);
  const input = await scan("frx.26.0000001");

  const card = within(await screen.findByRole("region", { name: "Карточка FRX.26.0000001" }));
  const quantity = card.getByLabelText("Количество");
  await waitFor(() => expect(quantity).toHaveFocus());
  fireEvent.change(quantity, { target: { value: "2" } });
  fireEvent.change(card.getByLabelText("Назначение или откуда"), { target: { value: "FRX.001" } });
  fireEvent.submit(quantity.closest("form")!);

  expect(await card.findByRole("status")).toHaveTextContent("Списано 2 шт → остаток 8 шт");
  expect(calls.find((call) => call.method === "POST")).toEqual({
    method: "POST", path: `/api/stock/batches/${lot.id}/writeoff`, body: { version: 4, quantity: "2", purpose: "FRX.001", reason: "" },
  });
  expect(card.getByText("8 шт")).toBeInTheDocument();
  await waitFor(() => expect(input).toHaveFocus());
});

it("a write-off without a purpose is stopped in the card, nothing is sent", async () => {
  const calls = stage({
    "GET /api/stock/scan?code=FRX.26.0000001": () => json({ kind: "lot", code: "FRX.26.0000001", place: "", lots: [lot] }),
  });
  render(<WarehousePage />);
  await scan("FRX.26.0000001");
  const card = within(await screen.findByRole("region", { name: "Карточка FRX.26.0000001" }));
  fireEvent.change(card.getByLabelText("Количество"), { target: { value: "1" } });
  fireEvent.click(card.getByRole("button", { name: "Списать" }));
  expect(await card.findByRole("alert")).toHaveTextContent("Укажи назначение");
  expect(calls.some((call) => call.method === "POST")).toBe(false);
});

it("a place lists its lots, a lot opens, «Переместить» moves it, Esc closes and gives the focus back", async () => {
  const calls = stage({
    "GET /api/stock/scan?code=1F4": () => json({ kind: "place", code: "1F4", place: "1F4", lots: [lot, other] }),
    [`POST /api/stock/batches/${other.id}/move`]: () => json({ lot: { ...other, version: 5, storage_location: "2A1" } }),
  });
  render(<WarehousePage />);
  const input = await scan("1F4");

  const list = within(await screen.findByRole("region", { name: "Место 1F4" }));
  expect(list.getByText("Место 1F4 — партий: 2")).toBeInTheDocument();
  fireEvent.click(list.getByRole("button", { name: "OLD.26.0000005" }));
  const card = within(await screen.findByRole("region", { name: "Карточка OLD.26.0000005" }));
  const place = card.getByLabelText("Новое место");
  fireEvent.change(place, { target: { value: "2a1" } });
  fireEvent.submit(place.closest("form")!);
  expect(await card.findByRole("status")).toHaveTextContent("Перемещено → 2A1");
  expect(calls.find((call) => call.method === "POST")?.body).toEqual({ version: 4, storage_location: "2a1", warehouse: "" });

  fireEvent.keyDown(card.getByLabelText("Количество"), { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("region", { name: "Карточка OLD.26.0000005" })).not.toBeInTheDocument());
  await waitFor(() => expect(input).toHaveFocus());
});

it("words that are no ГРН or place stay a filter of the table: the scan's «Не найдено» is not shown", async () => {
  const calls = stage({ "GET /api/stock/scan?code=%D1%80%D0%B5%D0%B7%D0%B8%D1%81%D1%82%D0%BE%D1%80": () => json({ detail: "Не найдено: резистор" }, 404) });
  render(<WarehousePage />);
  const input = await scan("резистор");
  await waitFor(() => expect(calls.some((call) => call.path.startsWith("/api/stock/scan"))).toBe(true));
  await waitFor(() => expect(calls.some((call) => call.path.includes("q=%D1%80%D0%B5%D0%B7%D0%B8%D1%81%D1%82%D0%BE%D1%80"))).toBe(true));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Скан" })).not.toBeInTheDocument();
  expect(input).toHaveValue("резистор");
});

it("a failed scan other than «not found» says so", async () => {
  stage({ "GET /api/stock/scan?code=FRX.26.0000001": () => json({ detail: "Склад недоступен" }, 503) });
  render(<WarehousePage />);
  await scan("FRX.26.0000001");
  expect(await screen.findByRole("alert")).toHaveTextContent("Склад недоступен");
});

it("«Отменить последнее» in the scan card shows what it takes back and undoes it", async () => {
  let undone = false;
  const calls = stage({
    "GET /api/stock/scan?code=FRX.26.0000001": () => json({ kind: "lot", code: "FRX.26.0000001", place: "", lots: [lot] }),
    [`GET /api/stock/batches/${lot.id}/undo`]: () => json(undone
      ? { undo: null, reason: "Последнее движение — уже отмена. Если нужно, сделай действие заново." }
      : { undo: { label: "Списание 2 шт · FRX.001", occurred_at: "2026-09-21T12:00:00" }, reason: "" }),
    [`POST /api/stock/batches/${lot.id}/undo`]: () => {
      undone = true;
      return json({ lot: { ...lot, version: 5, written_off: "0", balance: "10" }, undone: "Списание 2 шт · FRX.001" });
    },
  });
  render(<WarehousePage />);
  await scan("FRX.26.0000001");
  const card = within(await screen.findByRole("region", { name: "Карточка FRX.26.0000001" }));
  const line = within(await card.findByRole("group", { name: "Отмена последнего движения" }));
  expect(await line.findByText("Списание 2 шт · FRX.001")).toBeInTheDocument();

  fireEvent.click(line.getByRole("button", { name: "Отменить последнее" }));
  expect(await card.findByRole("status")).toHaveTextContent("Отменено: Списание 2 шт · FRX.001");
  expect(calls.find((call) => call.method === "POST")).toEqual({
    method: "POST", path: `/api/stock/batches/${lot.id}/undo`, body: { version: 4 },
  });
  expect(await card.findByText("Последнее движение — уже отмена. Если нужно, сделай действие заново.")).toBeInTheDocument();
});

it("a lot card in the table reads the undo line only on the first click", async () => {
  const calls = stage({
    [`GET /api/stock/batches/${lot.id}/undo`]: () => json({ undo: null, reason: "По этой партии нечего отменять." }),
  });
  render(<WarehousePage />);
  fireEvent.click((await screen.findAllByRole("button", { name: /Показать партии/ }))[0]);
  const cards = await screen.findAllByRole("button", { name: "Отменить последнее…" });
  expect(calls.some((call) => call.path.endsWith("/undo"))).toBe(false);
  fireEvent.click(cards[0]);
  expect(await screen.findByText("По этой партии нечего отменять.")).toBeInTheDocument();
});
