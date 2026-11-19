import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { MachineCommand, MachineControl } from "./machineControlApi";
import type { MachineCard } from "./machinesApi";
import { Ob019Controls } from "./Ob019Controls";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function card(state: number, stateText: string, overrides: Partial<MachineCard> = {}, alarm = 0): MachineCard {
  return {
    equipment_id: "u19", inventory: "ОБ.019", name: "Линия резки", pc: "PC-OB019", ip: "192.168.1.71", service: "Мост ERP",
    service_version: "mbd 1", bridge_version: "1.1.0", link: { code: "ok", text: "На связи", tone: "ok" },
    reported_at: "2026-11-19T10:00:00+03:00", age_seconds: 1, error: "",
    live: {
      state: { code: state, text: stateText, tone: "ok" },
      alarm: { code: alarm, text: alarm ? "Нет провода" : "Аварий нет", tone: alarm ? "danger" : "neutral" },
      mode: { code: 1, text: "Один цикл" },
      job: { total_set: 1, total_finished: 0, batch_set: 0, batch_finished: 0, uph: 0, program: 1, order_quantity: null,
        order_finished: null, order_number: null, product: null, order_running: false },
      wire: { length_mm: 100, strip_front_mm: 5, pull_front_mm: 5, strip_back_mm: 5, pull_back_mm: 5, diameter_mm: 0.8, knife_back_mm: 0.1 },
    },
    ...overrides,
  } as MachineCard;
}

const RUNNING: MachineCommand = {
  id: "k1", status: "taken", status_text: "Выполняется", tone: "info", purpose: "control", command: "start",
  state_before: null, state_after: null, summary: "Пуск", fields: [], error: "", created_by: "Слава",
  created_at: "2026-11-19T10:00:05+03:00", finished_at: null,
};

function control(overrides: Partial<MachineControl> = {}): MachineControl {
  return { problem: "", state: 102, commands: [], offsets: [], can_edit: true, can_control: true, ...overrides };
}

function mockApi(answer: MachineControl, send: () => Response = () => json({ ...RUNNING, id: "new", status: "queued" })) {
  const posts: { url: string; body: Record<string, unknown> }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (init?.method === "POST") {
      posts.push({ url: String(input), body: JSON.parse(String(init.body)) });
      return send();
    }
    return json(answer);
  });
  return posts;
}

afterEach(() => { vi.restoreAllMocks(); });

it("Stop is there for everyone who sees the card and goes without a confirmation; the rest needs the right", async () => {
  const posts = mockApi(control({ can_control: false }));
  render(<Ob019Controls card={card(103, "Работает")} />);
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: "Пуск" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Сброс" })).toBeNull();
  expect(screen.getByText(/Аварийная остановка — только кнопкой на станке/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Стоп" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].url).toContain("/api/production/machines/card/%D0%9E%D0%91.019/command");
  expect(posts[0].body).toEqual({ command: "stop" });
});

it("Start asks «руки убраны» showing the current state and sends the state the person saw", async () => {
  const posts = mockApi(control());
  render(<Ob019Controls card={card(102, "Готов, ждёт пуска")} />);
  const start = await screen.findByRole("button", { name: "Пуск" });
  expect((screen.getByRole("button", { name: "Сброс" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Сброс аварии" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(start);
  const confirm = screen.getByRole("group", { name: "Подтверждение: Пуск" });
  expect(within(confirm).getByText("Готов, ждёт пуска")).toBeTruthy();
  const go = within(confirm).getByRole("button", { name: "Подтвердить: Пуск" }) as HTMLButtonElement;
  expect(go.disabled).toBe(true);
  fireEvent.click(within(confirm).getByLabelText(/Руки убраны/));
  expect(go.disabled).toBe(false);
  fireEvent.click(go);
  await waitFor(() => expect(posts.map((post) => post.body)).toEqual([{ command: "start", confirm_state: 102, hands_clear: true }]));
  await waitFor(() => expect(screen.queryByRole("group", { name: "Подтверждение: Пуск" })).toBeNull());
});

it("Reset is offered in «не сброшен»; Clear alarm only with an alarm; no link — only Stop", async () => {
  mockApi(control());
  const { rerender } = render(<Ob019Controls card={card(100, "Не сброшен — нужен сброс", {}, 6)} />);
  const reset = await screen.findByRole("button", { name: "Сброс" }) as HTMLButtonElement;
  expect(reset.disabled).toBe(false);
  expect((screen.getByRole("button", { name: "Пуск" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Сброс аварии" }) as HTMLButtonElement).disabled).toBe(false);
  rerender(<Ob019Controls card={card(100, "Не сброшен — нужен сброс", {
    link: { code: "stale", text: "Нет связи с ПК станка", tone: "danger" } })} />);
  for (const name of ["Сброс", "Пуск", "Сброс аварии"]) {
    expect((screen.getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(true);
  }
  expect((screen.getByRole("button", { name: "Стоп" }) as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getByText(/Нет связи — «Стоп» может не дойти/)).toBeTruthy();
});

it("a refused command shows the server's reason", async () => {
  mockApi(control(), () => json({ detail: "Состояние станка изменилось — проверьте его и подтвердите ещё раз" }, 409));
  render(<Ob019Controls card={card(102, "Готов, ждёт пуска")} />);
  fireEvent.click(await screen.findByRole("button", { name: "Пуск" }));
  fireEvent.click(screen.getByLabelText(/Руки убраны/));
  fireEvent.click(screen.getByRole("button", { name: "Подтвердить: Пуск" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Состояние станка изменилось");
});

it("while a command runs the others wait and its progress is shown; Stop stays", async () => {
  mockApi(control({ commands: [RUNNING] }));
  render(<Ob019Controls card={card(102, "Готов, ждёт пуска")} />);
  expect((await screen.findByRole("status")).textContent).toBe("Пуск: выполняется");
  expect((screen.getByRole("button", { name: "Пуск" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Стоп" }) as HTMLButtonElement).disabled).toBe(false);
});
