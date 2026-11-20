import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { pultResult, type MachineCommand, type MachineControl, type MachineRun, type MachineWindow } from "./machineControlApi";
import type { MachineCard } from "./machinesApi";
import { FORCE_WINDOW_MS, Ob019Controls, Ob019Pult, PULT_CONFIRM } from "./Ob019Controls";

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

function rollers(lifted: boolean): MachineWindow {
  return { inventory: "ОБ.019", settings: [{ title: "Скорости", fields: [
    { label: "Подъём подающих роликов", value: lifted, unit: "", read: true, key: "feed_wheel", kind: "bit0" }] }] };
}

function mockApi(answer: MachineControl, send: () => Response = () => json({ ...RUNNING, id: "new", status: "queued" }),
  window: MachineWindow = rollers(false)) {
  const posts: { url: string; body: Record<string, unknown> }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (init?.method === "POST") {
      posts.push({ url: decodeURIComponent(String(input)), body: JSON.parse(String(init.body)) });
      return send();
    }
    return json(String(input).endsWith("/control") ? answer : window);
  });
  return posts;
}

afterEach(() => { vi.restoreAllMocks(); });

const button = (name: string | RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;
const noteOf = (name: string | RegExp) => button(name).parentElement!.querySelector(".machine-cmd-note")!.textContent;

it("Stop is there for everyone who sees the card and goes in one click; the rest needs the right", async () => {
  const posts = mockApi(control({ can_control: false }));
  render(<Ob019Controls card={card(103, "Работает")} />);
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: "Пуск" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Сброс" })).toBeNull();
  fireEvent.click(button("Стоп"));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].url).toBe("/api/production/machines/card/ОБ.019/command");
  expect(posts[0].body).toEqual({ command: "stop" });
});

it("ОТК reads the card but gets no Stop or any other command (review of #999)", async () => {
  mockApi(control({ can_control: false, can_stop: false, can_edit: false }));
  render(<Ob019Controls card={card(103, "Работает")} />);
  await waitFor(() => expect(screen.queryByRole("button", { name: "Стоп" })).toBeNull());
  for (const name of ["Пуск", "Сброс", "Сброс аварии"]) expect(screen.queryByRole("button", { name })).toBeNull();
});

it("Start goes in one click with the state the card saw, no «руки убраны» dialog, and its result shows under it", async () => {
  const posts = mockApi(control());
  render(<Ob019Controls card={card(102, "Готов, ждёт пуска")} />);
  await waitFor(() => expect(button("Пуск").disabled).toBe(false));
  expect(button("Сброс").disabled).toBe(true);
  expect(noteOf("Сброс")).toBe("уже сброшен");
  expect(noteOf("Сброс аварии")).toBe("аварии нет");
  fireEvent.click(button("Пуск"));
  await waitFor(() => expect(posts.map((post) => post.body)).toEqual([{ command: "start", confirm_state: 102, hands_clear: true }]));
  expect(screen.queryByRole("group")).toBeNull();
  expect(screen.queryByLabelText(/Руки убраны/)).toBeNull();
  await waitFor(() => expect(noteOf("Пуск")).toBe("отправлено…"));
});

it("a disabled command says why on the row; no link — only Stop", async () => {
  mockApi(control());
  const { rerender } = render(<Ob019Controls card={card(100, "Не сброшен — нужен сброс", {}, 6)} />);
  await waitFor(() => expect(button("Сброс").disabled).toBe(false));
  expect(button("Пуск").disabled).toBe(true);
  expect(noteOf("Пуск")).toBe("сначала «Сброс»");
  expect(button("Сброс аварии").disabled).toBe(false);
  rerender(<Ob019Controls card={card(100, "Не сброшен — нужен сброс", {
    link: { code: "stale", text: "Нет связи с ПК станка", tone: "danger" } })} />);
  for (const name of ["Сброс", "Пуск", "Сброс аварии"]) expect(button(name).disabled).toBe(true);
  expect(button("Стоп").disabled).toBe(false);
  expect(noteOf("Стоп")).toBe("без связи может не дойти");
});

it("a refused command shows the server's reason under its button", async () => {
  mockApi(control(), () => json({ detail: "Состояние станка изменилось — проверьте его и подтвердите ещё раз" }, 409));
  render(<Ob019Controls card={card(102, "Готов, ждёт пуска")} />);
  await waitFor(() => expect(button("Пуск").disabled).toBe(false));
  fireEvent.click(button("Пуск"));
  await waitFor(() => expect(noteOf("Пуск")).toBe("отказ: Состояние станка изменилось — проверьте его и подтвердите ещё раз"));
});

it("while a command runs the others wait and its progress is under it; a done one shows the time", async () => {
  mockApi(control({ commands: [RUNNING] }));
  const { unmount } = render(<Ob019Controls card={card(102, "Готов, ждёт пуска")} />);
  await waitFor(() => expect(noteOf("Пуск")).toBe("отправлено…"));
  expect(button("Пуск").disabled).toBe(true);
  expect(button("Стоп").disabled).toBe(false);
  unmount();
  vi.restoreAllMocks();
  const posts = mockApi(control({ commands: [{ ...RUNNING, id: "new", status: "done", status_text: "Выполнено",
    finished_at: "2026-11-19T14:05:00+01:00" }] }));
  render(<Ob019Controls card={card(102, "Готов, ждёт пуска")} />);
  await waitFor(() => expect(button("Пуск").disabled).toBe(false));
  fireEvent.click(button("Пуск"));
  await waitFor(() => expect(posts).toHaveLength(1));
  await waitFor(() => expect(noteOf("Пуск")).toMatch(/^выполнено \d\d:\d\d$/));
});

it("the feed rollers are a toggle read back from the machine; a click writes feed_wheel", async () => {
  const posts = mockApi(control(), () => json({ ...RUNNING, id: "w1", purpose: "settings", command: null, status: "queued" }));
  render(<Ob019Controls card={card(102, "Готов, ждёт пуска")} />);
  const toggle = await screen.findByRole("button", { name: "Ролики опущены · поднять" });
  await waitFor(() => expect((toggle as HTMLButtonElement).disabled).toBe(false));
  expect(toggle).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(toggle);
  await waitFor(() => expect(posts).toEqual([{ url: "/api/production/machines/card/ОБ.019/write",
    body: { values: { feed_wheel: "1" }, confirm_state: 102 } }]));
  await waitFor(() => expect(noteOf(/Ролики/)).toBe("отправлено…"));
});

it("an old writer on the machine PC refuses the rollers inline; a running machine disables them with the reason", async () => {
  mockApi(control(), () => json({ detail: "Служба записи на ПК станка устарела — обновите (нужна 1.3.0)" }, 409), rollers(true));
  const { unmount } = render(<Ob019Controls card={card(107, "Тираж готов")} />);
  const toggle = await screen.findByRole("button", { name: "Ролики подняты · опустить" });
  await waitFor(() => expect((toggle as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(toggle);
  await waitFor(() => expect(noteOf(/Ролики/)).toBe("отказ: Служба записи на ПК станка устарела — обновите (нужна 1.3.0)"));
  unmount();
  vi.restoreAllMocks();
  mockApi(control({ state: 103 }));
  render(<Ob019Controls card={card(103, "Работает")} />);
  await waitFor(() => expect(noteOf(/Ролики/)).toBe("только когда станок готов или тираж готов"));
  expect(button(/Ролики/).disabled).toBe(true);
});

it("Start is closed while the feed rollers are lifted and says so on the row", async () => {
  const posts = mockApi(control(), undefined, rollers(true));
  render(<Ob019Controls card={card(102, "Готов, ждёт пуска")} />);
  await waitFor(() => expect(button(/Ролики подняты/)).toBeEnabled());
  expect(button("Пуск")).toBeDisabled();
  expect(noteOf("Пуск")).toBe("опустите ролики");
  fireEvent.click(button("Пуск"));
  expect(posts).toEqual([]);
});

it("Start opens again once the rollers read back lowered", async () => {
  mockApi(control(), undefined, rollers(false));
  render(<Ob019Controls card={card(102, "Готов, ждёт пуска")} />);
  await waitFor(() => expect(button(/Ролики опущены/)).toBeEnabled());
  expect(button("Пуск")).toBeEnabled();
});

it("Start says in advance what the server would refuse: no open job, its write not checked yet; the reason goes up", async () => {
  const posts = mockApi(control());
  const why: (string | undefined)[] = [];
  const run = { id: "w1", kind: "cutting", label: "ГР-0007", stage: "writing" } as MachineRun;
  const { rerender } = render(<Ob019Controls card={card(102, "Готов, ждёт пуска")} run={null} onStartWhy={(text) => why.push(text)} />);
  await waitFor(() => expect(noteOf("Пуск")).toBe("выберите задание"));
  expect(button("Пуск")).toBeDisabled();
  rerender(<Ob019Controls card={card(102, "Готов, ждёт пуска")} run={run} onStartWhy={(text) => why.push(text)} />);
  await waitFor(() => expect(noteOf("Пуск")).toBe("ждём записи задания"));
  rerender(<Ob019Controls card={card(102, "Готов, ждёт пуска")} run={{ ...run, stage: "ready" }} onStartWhy={(text) => why.push(text)} />);
  await waitFor(() => expect(button("Пуск")).toBeEnabled());
  expect(why).toEqual(expect.arrayContaining(["выберите задание", "ждём записи задания", ""]));
  expect(posts).toEqual([]);
});

const PULT_TEXT = "Открыт пульт станка на ПК — закройте его, чтобы ERP могла управлять станком";
const pultCard = () => card(102, "Готов, ждёт пуска", { link: { code: "port_busy", text: PULT_TEXT, tone: "warn" }, live: null });

it("the open panel on the PC gets «Закрыть пульт на ПК» after a question; the server's refusal shows as it is", async () => {
  const posts = mockApi(control(), () => json({ detail: "Станок в цикле — пульт закрывать нельзя, дождитесь остановки" }, 409));
  const ask = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<Ob019Pult card={pultCard()} control={control()} />);
  fireEvent.click(button("Закрыть пульт на ПК"));
  expect(ask).toHaveBeenCalledWith(PULT_CONFIRM.hmi_close);
  expect(PULT_CONFIRM.hmi_close).not.toMatch(/PXS|HMI/);
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toEqual({ command: "hmi_close", hands_clear: true });
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("отказ: Станок в цикле — пульт закрывать нельзя, дождитесь остановки"));
});

it("a «no» to the question sends nothing; without the right there is no panel button", async () => {
  const posts = mockApi(control());
  vi.spyOn(window, "confirm").mockReturnValue(false);
  const { rerender } = render(<Ob019Pult card={pultCard()} control={control()} />);
  fireEvent.click(button("Закрыть пульт на ПК"));
  expect(posts).toHaveLength(0);
  rerender(<Ob019Pult card={pultCard()} control={control({ can_control: false })} />);
  expect(screen.queryByRole("button", { name: /пульт/ })).toBeNull();
  rerender(<Ob019Pult card={card(102, "Готов", { link: { code: "stale", text: "Нет связи с ПК станка", tone: "danger" } })} control={control()} />);
  expect(screen.queryByRole("button", { name: /пульт/ })).toBeNull();
});

it("a live machine offers the quieter «Открыть пульт на ПК»; the result says how the panel went", async () => {
  const posts = mockApi(control(), () => json({ ...RUNNING, id: "p1", command: "hmi_open", status: "queued", summary: "Открыть пульт на ПК" }));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const { rerender } = render(<Ob019Pult card={card(102, "Готов, ждёт пуска")} control={control()} />);
  expect(button("Открыть пульт на ПК").className).toContain("small");
  fireEvent.click(button("Открыть пульт на ПК"));
  await waitFor(() => expect(posts.map((post) => post.body)).toEqual([{ command: "hmi_open", hands_clear: true }]));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("открывается…"));
  rerender(<Ob019Pult card={card(102, "Готов, ждёт пуска")} control={control({ commands: [{ ...RUNNING, id: "p1", command: "hmi_open",
    status: "done", finished_at: "2026-11-20T14:05:00", summary: "Открыть пульт на ПК" }] })} />);
  expect(screen.getByRole("status")).toHaveTextContent(/^открыт \d\d:\d\d$/);
  const forced = { ...RUNNING, command: "hmi_close" as const, status: "done" as const, method: "forced" as const, finished_at: null };
  expect(pultResult(forced).text).toBe("закрыт принудительно");
  expect(pultResult({ ...forced, method: "none" }).text).toBe("пульт уже был закрыт");
});

it("a close the panel did not answer offers «Закрыть принудительно» with its own question; the progress follows the sent command", async () => {
  const posts = mockApi(control(), () => json({ ...RUNNING, id: "f1", command: "hmi_close", status: "queued", force: true,
    summary: "Закрыть пульт на ПК принудительно" }));
  const ask = vi.spyOn(window, "confirm").mockReturnValue(true);
  const stuck: MachineCommand = { ...RUNNING, id: "s1", command: "hmi_close", status: "failed", reason: "still_open",
    error: "Пульт не закрылся (возможно, ждёт ответа оператора)", summary: "Закрыть пульт на ПК", finished_at: new Date().toISOString() };
  render(<Ob019Pult card={pultCard()} control={control({ commands: [stuck] })} />);
  expect(screen.getByRole("status")).toHaveTextContent("Пульт не закрылся (возможно, ждёт ответа оператора)");
  fireEvent.click(button("Закрыть принудительно"));
  expect(ask).toHaveBeenCalledWith("ERP не видит, режет ли станок, пока открыт пульт. Убедитесь, что станок стоит. Закрыть пульт принудительно?");
  await waitFor(() => expect(posts.map((post) => post.body)).toEqual([{ command: "hmi_close", hands_clear: true, force: true }]));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("закрывается принудительно…"));
  expect(PULT_CONFIRM.hmi_close).toContain("ERP не видит станок");
});

it("no forced close is offered unless the last close stayed open; a missing helper is said as the server says it", async () => {
  mockApi(control());
  const failed: MachineCommand = { ...RUNNING, id: "n1", command: "hmi_close", status: "failed", reason: "no_helper",
    error: "На ПК станка нет помощника пульта — обновите службу", summary: "Закрыть пульт на ПК" };
  render(<Ob019Pult card={pultCard()} control={control({ commands: [failed] })} />);
  expect(screen.queryByRole("button", { name: "Закрыть принудительно" })).toBeNull();
  expect(screen.getByRole("status")).toHaveTextContent("отказ: На ПК станка нет помощника пульта — обновите службу");
});

it("«Закрыть принудительно» is offered only for a close that stayed open in the last 5 minutes", () => {
  mockApi(control());
  const old: MachineCommand = { ...RUNNING, id: "s2", command: "hmi_close", status: "failed", reason: "still_open",
    error: "Пульт не закрылся (возможно, ждёт ответа оператора)", summary: "Закрыть пульт на ПК",
    finished_at: new Date(Date.now() - FORCE_WINDOW_MS - 1000).toISOString() };
  const { rerender } = render(<Ob019Pult card={pultCard()} control={control({ commands: [old] })} />);
  expect(screen.queryByRole("button", { name: "Закрыть принудительно" })).toBeNull();
  rerender(<Ob019Pult card={pultCard()} control={control({ commands: [{ ...old, finished_at: new Date(Date.now() - 60000).toISOString() }] })} />);
  expect(screen.getByRole("button", { name: "Закрыть принудительно" })).toBeTruthy();
});
