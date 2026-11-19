import { afterEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { InstrumentMatings } from "./InstrumentMatings";
import type { InstrumentMatingsSnapshot } from "./api";

const snapshot: InstrumentMatingsSnapshot = {
  instrument_id: "adapter", resource: 3000, status: "warning", has_connectors: true,
  ends: [
    { end: "A", total: 2400, version: 1, percent_used: 80, status: "warning", initial_recorded: true, can_initialize: false },
    { end: "B", total: 0, version: 0, percent_used: 0, status: "ok", initial_recorded: false, can_initialize: true },
  ],
  events: [{ id: "first", instrument_id: "adapter", end: "A", delta: 2400, total_after: 2400, kind: "initial", note: "Журнал", source: null, actor: "Иванов", created_at: "2026-11-17T09:00:00+03:00" }],
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
type Write = { end: "A" | "B"; delta: number; kind: string; note: string; version?: number };
function setup(initial = snapshot, writeStatus = 200) {
  let current = structuredClone(initial);
  const writes: Write[] = [];
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => {
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as Write;
      writes.push(body);
      if (writeStatus !== 200) return json({ detail: "Сумма сочленений не может быть отрицательной" }, writeStatus);
      current = { ...current, ends: current.ends.map((end) => end.end === body.end
        ? { ...end, total: end.total + body.delta, version: end.version + 1, initial_recorded: end.initial_recorded || body.kind === "initial", can_initialize: false } : end) };
      return json(current);
    }
    return json(current);
  });
  const onChanged = vi.fn(async () => {});
  const onDirty = vi.fn();
  const onBusy = vi.fn();
  const rendered = render(<InstrumentMatings instrumentId="adapter" locked={false} refreshKey={0} onChanged={onChanged} onDirty={onDirty} onBusy={onBusy} />);
  return { writes, fetch, onChanged, onDirty, onBusy, ...rendered };
}
afterEach(() => vi.restoreAllMocks());

it("shows independent totals and Russian warnings without disabling increments at exhaustion", async () => {
  setup({ ...snapshot, status: "exhausted", ends: [{ ...snapshot.ends[0], total: 3000, percent_used: 100, status: "exhausted" }, snapshot.ends[1]] });
  expect(await screen.findByText("Ресурс исчерпан — заменить")).toHaveClass("tone-danger");
  expect(screen.getByText(/A: 3.*000 \/ 3.*000 \(100 %\)/)).toBeInTheDocument();
  expect(screen.getByText(/B: 0 \/ 3.*000 \(0 %\)/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "+1 сочленение стороны A" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Задать начальное значение стороны A" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Задать начальное значение стороны B" })).toBeEnabled();
});

it("shows the next control by date or matings and marks it overdue", async () => {
  const control = { measured_on: "2026-11-16", due_on: "2027-11-16", matings_since: 13, matings_left: 87, overdue: false,
    line: "следующий контроль до 16.11.2027 или ещё 87 сочленений из 100 — что раньше" };
  const { unmount } = setup({ ...snapshot, control });
  const fresh = await screen.findByText(control.line);
  expect(fresh.parentElement).toHaveTextContent("Замер 16.11.2026 · следующий контроль до 16.11.2027");
  expect(fresh.parentElement).not.toHaveClass("tone-danger");
  unmount();
  vi.restoreAllMocks();
  setup({ ...snapshot, control: { ...control, matings_since: 100, matings_left: 0, overdue: true,
    line: "контроль просрочен: 100 сочленений с замера 16.11.2026 (норма 100)" } });
  expect((await screen.findByText(/контроль просрочен/)).parentElement).toHaveClass("tone-danger");
});

it("shows no control line for an item never measured", async () => {
  setup({ ...snapshot, control: null });
  await screen.findByText(/A: 2.*400/);
  expect(screen.queryByText(/контроль/)).not.toBeInTheDocument();
});

it("posts exactly one increment for the chosen side and refreshes the registry", async () => {
  const { writes, onChanged, onBusy } = setup();
  fireEvent.click(await screen.findByRole("button", { name: "+1 сочленение стороны A" }));
  await screen.findByRole("status");
  expect(writes).toEqual([{ end: "A", delta: 1, kind: "manual", note: "" }]);
  expect(screen.getByText(/A: 2.*401/)).toBeInTheDocument();
  expect(onChanged).toHaveBeenCalledOnce();
  expect(onBusy).toHaveBeenLastCalledWith(false);
});

it("records a noted initial value once for a fresh end and warns the card about unsaved input", async () => {
  const { writes, onDirty } = setup();
  fireEvent.click(await screen.findByRole("button", { name: "Задать начальное значение стороны B" }));
  fireEvent.change(screen.getByLabelText("Начальное число сочленений"), { target: { value: "142" } });
  fireEvent.change(screen.getByLabelText("Источник начального значения"), { target: { value: " Журнал смены " } });
  expect(onDirty).toHaveBeenLastCalledWith(true);
  fireEvent.submit(screen.getByRole("form", { name: "Сочленения стороны B" }));
  await screen.findByRole("status");
  expect(writes).toEqual([{ end: "B", delta: 142, kind: "initial", note: "Журнал смены", version: 0 }]);
  expect(screen.queryByRole("button", { name: "Задать начальное значение стороны B" })).not.toBeInTheDocument();
  expect(onDirty).toHaveBeenLastCalledWith(false);
});

it("requires a correction reason and prevents a negative total before sending", async () => {
  const { writes } = setup();
  fireEvent.click(await screen.findByRole("button", { name: "Исправить сочленения стороны A" }));
  fireEvent.change(screen.getByLabelText("Изменение числа сочленений"), { target: { value: "-2500" } });
  fireEvent.submit(screen.getByRole("form", { name: "Сочленения стороны A" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Укажите причину");
  expect(screen.getByLabelText("Причина исправления")).toHaveFocus();
  fireEvent.change(screen.getByLabelText("Причина исправления"), { target: { value: "Ошибка журнала" } });
  fireEvent.submit(screen.getByRole("form", { name: "Сочленения стороны A" }));
  expect(screen.getByRole("alert")).toHaveTextContent("не может быть отрицательным");
  expect(writes).toHaveLength(0);
  fireEvent.change(screen.getByLabelText("Изменение числа сочленений"), { target: { value: "-2" } });
  fireEvent.submit(screen.getByRole("form", { name: "Сочленения стороны A" }));
  await screen.findByRole("status");
  expect(writes).toEqual([{ end: "A", delta: -2, kind: "correction", note: "Ошибка журнала", version: 1 }]);
});

it("keeps a failed correction draft and shows the server error", async () => {
  const { writes } = setup(snapshot, 422);
  fireEvent.click(await screen.findByRole("button", { name: "Исправить сочленения стороны A" }));
  fireEvent.change(screen.getByLabelText("Изменение числа сочленений"), { target: { value: "-2" } });
  fireEvent.change(screen.getByLabelText("Причина исправления"), { target: { value: "Ошибка" } });
  fireEvent.submit(screen.getByRole("form", { name: "Сочленения стороны A" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("не может быть отрицательной");
  expect(screen.getByLabelText("Изменение числа сочленений")).toHaveValue(-2);
  expect(writes).toHaveLength(1);
});

it("shows one-end unknown-resource items honestly and does not expose raw event kind codes", async () => {
  setup({ ...snapshot, resource: null, status: "unknown", ends: [{ ...snapshot.ends[0], percent_used: null, status: "unknown" }] });
  expect(await screen.findByText("Ресурс не указан")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /стороны B/ })).not.toBeInTheDocument();
  const history = screen.getByText("История сочленений · последние 1").closest("details")!;
  fireEvent.click(within(history).getByText("История сочленений · последние 1"));
  expect(within(history).getByText("Начальное значение")).toBeInTheDocument();
  expect(within(history).queryByText("initial")).not.toBeInTheDocument();
});

it("does not offer initialization for an end whose prior events were corrected to zero", async () => {
  setup({ ...snapshot, ends: [{ ...snapshot.ends[1], can_initialize: false }], events: [] });
  await screen.findByText("Ресурс в норме");
  expect(screen.queryByRole("button", { name: /Задать начальное значение/ })).not.toBeInTheDocument();
});

it("reloads after RF resource changes and exposes an honest load error with retry", async () => {
  const { fetch, rerender, onChanged, onDirty, onBusy } = setup();
  await screen.findByText("Ресурс почти исчерпан");
  fetch.mockRejectedValueOnce(new Error("Нет связи с ERP"));
  rerender(<InstrumentMatings instrumentId="adapter" locked={false} refreshKey={1} onChanged={onChanged} onDirty={onDirty} onBusy={onBusy} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи с ERP");
  fireEvent.click(screen.getByRole("button", { name: "Обновить сочленения" }));
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  expect(fetch).toHaveBeenCalledTimes(3);
});

it("opens initial and correction input in a modal window and closes it with Esc", async () => {
  setup();
  fireEvent.click(await screen.findByRole("button", { name: "Исправить сочленения стороны A" }));
  const dialog = screen.getByRole("dialog", { name: "Исправление сочленений · сторона A" });
  expect(within(dialog).getByLabelText("Причина исправления")).toBeInTheDocument();
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Исправить сочленения стороны B" })).not.toBeInTheDocument();
});

it("reloads the counter after a concurrent change instead of writing twice", async () => {
  const { writes, fetch } = setup(snapshot, 409);
  fireEvent.click(await screen.findByRole("button", { name: "Исправить сочленения стороны A" }));
  fireEvent.change(screen.getByLabelText("Изменение числа сочленений"), { target: { value: "-1" } });
  fireEvent.change(screen.getByLabelText("Причина исправления"), { target: { value: "Ошибка" } });
  fireEvent.submit(screen.getByRole("form", { name: "Сочленения стороны A" }));
  expect(await screen.findByRole("alert")).toBeInTheDocument();
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
  expect(writes).toEqual([{ end: "A", delta: -1, kind: "correction", note: "Ошибка", version: 1 }]);
});

it("shows nothing for an item without RF connectors and without history", async () => {
  const { fetch, container } = setup({ ...snapshot, has_connectors: false, events: [] });
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  await waitFor(() => expect(container.querySelector(".instrument-matings")).toBeNull());
});
