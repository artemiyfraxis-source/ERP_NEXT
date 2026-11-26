import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { MachineCommand, MachineControl } from "./machineControlApi";
import type { MachineCard, Ob019Live } from "./machinesApi";
import { Ob019ZeroCounters, zeroedLive } from "./Ob019Card";

afterEach(() => vi.restoreAllMocks());

const LIVE: Ob019Live = {
  state: { code: 107, text: "Тираж готов", tone: "ok" }, alarm: { code: 0, text: "", tone: "neutral" },
  mode: { code: 2, text: "Автомат" },
  job: { total_set: 2058, total_finished: 2056, batch_set: 10, batch_finished: 6, uph: 0, program: 1, order_quantity: 0,
    order_finished: 0, order_number: null, product: null, order_running: false },
  wire: { length_mm: 325, strip_front_mm: 5, pull_front_mm: 5, strip_back_mm: 5, pull_back_mm: 5, diameter_mm: 0.8, knife_back_mm: 0.1 },
};
const CARD = { equipment_id: "e1", inventory: "ОБ.019", name: "ОБ.019", pc: "PC-108", ip: "", service: "", service_version: "",
  bridge_version: "", link: { code: "ok", text: "На связи", tone: "ok" }, reported_at: "2026-11-23T10:00:00+03:00", age_seconds: 1,
  error: "", live: LIVE } as unknown as MachineCard;
const CONTROL: MachineControl = { problem: "", state: 107, commands: [], offsets: [], can_edit: true };
const RESET: MachineCommand = { id: "r1", status: "done", status_text: "Записано и проверено", tone: "ok", purpose: "settings",
  summary: "Обнулить счётчики", fields: [], error: "", created_by: "Слава", created_at: "2026-11-23T10:00:01+03:00",
  finished_at: "2026-11-23T10:00:02+03:00",
  reset_counters: { before: { total: 2056, batch: 6 }, after: { total: 0, batch: 0 } } };

it("asks once with the counters shown and posts exactly them", async () => {
  const posts: { url: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    posts.push({ url: decodeURIComponent(String(input)), body: JSON.parse(String(init?.body)) });
    return new Response(JSON.stringify({ ...RESET, status: "queued" }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  render(<Ob019ZeroCounters card={CARD} live={LIVE} control={CONTROL} />);
  fireEvent.click(screen.getByRole("button", { name: "Обнулить" }));
  const dialog = await screen.findByRole("dialog", { name: "Обнулить счётчик" });
  expect(within(dialog).getByText("Сделано 2056 → 0, пучок 6 → 0")).toBeVisible();
  expect(within(dialog).getByText(/^Кнопка «Пуск» на станке начнёт тираж 2\D?058 шт заново/)).toBeVisible();
  expect(posts).toEqual([]);
  fireEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
  expect(posts).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "Обнулить" }));
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Обнулить счётчик" })).getByRole("button", { name: "Обнулить" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toEqual({ url: "/api/production/machines/card/ОБ.019/counters/reset",
    body: { confirm_state: 107, seen_total: 2056, seen_batch: 6 } });
});

it("offers zeroing only when the batch is done and the machine stands", () => {
  const running = { ...LIVE, state: { code: 103, text: "Режет", tone: "info" as const } };
  const { rerender } = render(<Ob019ZeroCounters card={CARD} live={running} control={CONTROL} />);
  expect(screen.queryByRole("button", { name: "Обнулить" })).toBeNull();
  const half = { ...LIVE, state: { code: 102, text: "Готов", tone: "ok" as const }, job: { ...LIVE.job, total_finished: 100 } };
  rerender(<Ob019ZeroCounters card={CARD} live={half} control={CONTROL} />);
  expect(screen.queryByRole("button", { name: "Обнулить" })).toBeNull();
  rerender(<Ob019ZeroCounters card={CARD} live={LIVE} control={{ ...CONTROL, can_edit: false }} />);
  expect(screen.queryByRole("button", { name: "Обнулить" })).toBeNull();
});

it("shows 0 after a done reset until a newer snapshot arrives", () => {
  expect(zeroedLive(LIVE, [RESET], "2026-11-23T10:00:00+03:00").job).toMatchObject({ total_finished: 0, batch_finished: 0 });
  expect(zeroedLive(LIVE, [RESET], "2026-11-23T10:00:05+03:00").job.total_finished).toBe(2056);
  expect(zeroedLive(LIVE, [{ ...RESET, status: "failed", reset_counters: { ...RESET.reset_counters!, after: null } }],
    "2026-11-23T10:00:00+03:00").job.total_finished).toBe(2056);
});

it("says the result is unknown instead of «не обнулено» when the agent did not confirm", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ ...RESET, status: "queued" }),
    { status: 200, headers: { "Content-Type": "application/json" } }));
  const { rerender } = render(<Ob019ZeroCounters card={CARD} live={LIVE} control={CONTROL} />);
  fireEvent.click(screen.getByRole("button", { name: "Обнулить" }));
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Обнулить счётчик" })).getByRole("button", { name: "Обнулить" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Обнулить счётчик" })).toBeNull());
  const unknown = { ...RESET, status: "unknown" as const, error: "Результат обнуления неизвестен — проверьте счётчик на станке" };
  rerender(<Ob019ZeroCounters card={CARD} live={LIVE} control={{ ...CONTROL, commands: [unknown] }} />);
  expect(screen.getByRole("status")).toHaveTextContent("результат неизвестен — проверьте счётчик на станке");
  expect(screen.queryByText(/не обнулено/)).toBeNull();
});
