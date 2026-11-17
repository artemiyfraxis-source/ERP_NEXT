import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ProductionInstrument } from "./api";
import { InstrumentLabelPrint } from "./InstrumentLabelPrint";

const instrument = { id: "adapter", inventory_number: "СИ.034", name: "SMA розетка – SMA розетка", version: 1 } as ProductionInstrument;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const base = "/api/production/instruments";

function stage({ online = true, rows = [instrument], bulk = false }: { online?: boolean; rows?: ProductionInstrument[]; bulk?: boolean } = {}) {
  const requests: { path: string; method: string; body: unknown }[] = [];
  let connected = online;
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const request = { path: String(input), method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null };
    requests.push(request);
    if (request.path === `${base}/label-agent`) return json({ online: connected });
    if (request.method === "POST") return json({ status: "queued", job_ids: ["job"], copies: 1 });
    throw new Error(`Unexpected request ${request.path}`);
  });
  const close = vi.fn();
  render(<InstrumentLabelPrint instruments={rows} bulk={bulk} close={close} />);
  fireEvent.load(screen.getByRole("img", { name: /Предпросмотр/ }));
  return { requests, close, fetch, disconnect: () => { connected = false; } };
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("previews the saved card and queues its selected copies only after confirmation", async () => {
  const { requests } = stage();
  expect(screen.getByRole("img")).toHaveAttribute("src", expect.stringContaining(`${base}/adapter/label.png`));
  const print = await screen.findByRole("button", { name: "Печатать 1 этикетку" });
  await waitFor(() => expect(print).toBeEnabled());
  expect(requests.every((request) => request.method === "GET")).toBe(true);
  fireEvent.change(screen.getByLabelText("Копий каждой этикетки"), { target: { value: "3" } });
  expect(screen.getByText("Этикеток: 3")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Печатать 3 этикетки" }));
  await screen.findByText(/Добавлено в очередь/);
  expect(requests.filter((request) => request.method === "POST")).toEqual([{ path: `${base}/adapter/label`, method: "POST", body: { copies: 3 } }]);
  expect(screen.getByRole("button", { name: "Печатать 3 этикетки" })).toBeDisabled();
});

it("confirms exactly the bulk rows and the physical label count", async () => {
  const rows = [instrument, { ...instrument, id: "second", inventory_number: "СИ.035" }];
  const { requests } = stage({ rows, bulk: true });
  await waitFor(() => expect(screen.getByRole("button", { name: "Печатать 2 этикетки" })).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Копий каждой этикетки"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Печатать 4 этикетки" }));
  await screen.findByText(/Добавлено в очередь/);
  expect(requests.filter((request) => request.method === "POST")).toEqual([{ path: `${base}/labels`, method: "POST", body: { ids: ["adapter", "second"], copies: 2 } }]);
});

it("refuses an offline printer without a print request", async () => {
  const { requests } = stage({ online: false });
  await screen.findByText("Принтер склада не на связи");
  expect(screen.getByRole("button", { name: "Печатать 1 этикетку" })).toBeDisabled();
  expect(requests.some((request) => request.method === "POST")).toBe(false);
});

it("checks the agent again before queueing and refuses a lost connection", async () => {
  const { requests, disconnect } = stage();
  const print = screen.getByRole("button", { name: "Печатать 1 этикетку" });
  await waitFor(() => expect(print).toBeEnabled());
  disconnect(); fireEvent.click(print);
  await screen.findByText("Принтер склада не на связи");
  expect(requests.some((request) => request.method === "POST")).toBe(false);
});

it("bounds copies and caps bulk print at 200 physical labels", async () => {
  stage({ rows: Array.from({ length: 50 }, (_, index) => ({ ...instrument, id: `${index}` })), bulk: true });
  await waitFor(() => expect(screen.getByRole("button", { name: "Печатать 50 этикеток" })).toBeEnabled());
  for (const copies of ["0", "6", "1.5"]) {
    fireEvent.change(screen.getByLabelText("Копий каждой этикетки"), { target: { value: copies } });
    expect(screen.getByRole("button", { name: /^Печатать/ })).toBeDisabled();
  }
  fireEvent.change(screen.getByLabelText("Копий каждой этикетки"), { target: { value: "5" } });
  expect(screen.getByText("Не более 200 этикеток за один раз")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^Печатать/ })).toBeDisabled();
});

it("shows a preview error and allows cancelling without printing", async () => {
  const { requests, close } = stage();
  fireEvent.error(screen.getByRole("img"));
  expect(screen.getByRole("alert")).toHaveTextContent("Не удалось загрузить предпросмотр этикетки");
  expect(screen.getByRole("button", { name: /^Печатать/ })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(close).toHaveBeenCalledOnce();
  expect(requests.some((request) => request.method === "POST")).toBe(false);
});

it("keeps the print action locked when the network loses the enqueue response", async () => {
  const { fetch } = stage();
  await waitFor(() => expect(screen.getByRole("button", { name: "Печатать 1 этикетку" })).toBeEnabled());
  fetch.mockImplementation(async (_input, init) => {
    if (init?.method === "POST") throw new TypeError("Failed to fetch");
    return json({ online: true });
  });
  fireEvent.click(screen.getByRole("button", { name: "Печатать 1 этикетку" }));
  await screen.findByText("Результат отправки неизвестен. Проверьте очередь принтера перед повторной печатью.");
  expect(screen.getByRole("button", { name: "Печатать 1 этикетку" })).toBeDisabled();
});

it("shows a server refusal without claiming that labels were queued", async () => {
  const { fetch } = stage();
  await waitFor(() => expect(screen.getByRole("button", { name: "Печатать 1 этикетку" })).toBeEnabled());
  fetch.mockImplementation(async (_input, init) => init?.method === "POST"
    ? json({ detail: "Принтер склада не на связи" }, 409) : json({ online: true }));
  fireEvent.click(screen.getByRole("button", { name: "Печатать 1 этикетку" }));
  await screen.findByText("Принтер склада не на связи");
  expect(screen.queryByText(/Добавлено в очередь/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Печатать 1 этикетку" })).toBeEnabled();
});

it.each([502, 503, 504])("locks retries after an ambiguous submitted HTTP %s response", async (status) => {
  const { fetch } = stage();
  await waitFor(() => expect(screen.getByRole("button", { name: "Печатать 1 этикетку" })).toBeEnabled());
  fetch.mockImplementation(async (_input, init) => init?.method === "POST"
    ? json({ detail: "Не получен ответ сервера" }, status) : json({ online: true }));
  fireEvent.click(screen.getByRole("button", { name: "Печатать 1 этикетку" }));
  await screen.findByText("Результат отправки неизвестен. Проверьте очередь принтера перед повторной печатью.");
  expect(screen.getByRole("button", { name: "Печатать 1 этикетку" })).toBeDisabled();
  expect(screen.queryByText(/Добавлено в очередь/)).not.toBeInTheDocument();
});
