import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WarehousePage } from "./WarehousePage";
import type { WarehouseLot } from "./api";
import { resetStageOptions } from "./StageOptions";

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

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.localStorage.clear(); resetStageOptions(); });

type Call = { method: string; path: string; body?: unknown };

function stage(on: boolean, routes: Record<string, (call: Call) => Response> = {}) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const call = { method: init?.method ?? "GET", path: String(input), body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    if (call.path === "/api/stage-options") {
      return json({ available: true, options: [{ key: "warehouse.scan", block: "Склад", title: "t", before: "", after: "", enabled: on }] });
    }
    if (call.path === "/api/stock" && call.method === "GET") return json({ lots: [lot, other], total: 2, truncated: 0, next_cursor: null });
    const route = routes[`${call.method} ${call.path}`];
    if (route) return route(call);
    return json({ detail: `Unexpected request: ${call.path}` }, 500);
  });
  return calls;
}

async function scan(code: string) {
  const input = await screen.findByRole("textbox", { name: "Скан / ГРН / место" });
  // The page must have loaded the stock: actions are refused while it is loading.
  await screen.findAllByRole("button", { name: /Показать партии/ });
  fireEvent.change(input, { target: { value: code } });
  fireEvent.submit(input.closest("form")!);
  return input;
}

it("has no scan field while the option is off", async () => {
  stage(false);
  render(<WarehousePage />);
  await screen.findAllByRole("button", { name: /Показать партии/ });
  expect(screen.queryByRole("textbox", { name: "Скан / ГРН / место" })).not.toBeInTheDocument();
});

it("a scanned ГРН opens its card; Enter in the quantity writes off through the existing endpoint", async () => {
  const calls = stage(true, {
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
  const calls = stage(true, {
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
  const calls = stage(true, {
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

it("an unknown code shows the server's «Не найдено»", async () => {
  stage(true, { "GET /api/stock/scan?code=zzz": () => json({ detail: "Не найдено: zzz" }, 404) });
  render(<WarehousePage />);
  await scan("zzz");
  expect(await screen.findByRole("alert")).toHaveTextContent("Не найдено: zzz");
});
