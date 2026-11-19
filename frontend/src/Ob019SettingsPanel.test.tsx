import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { MachineControl, MachineWindow, TransferPlan } from "./machineControlApi";
import { Ob019SettingsPanel } from "./Ob019SettingsPanel";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const WINDOW: MachineWindow = {
  inventory: "ОБ.019",
  settings: [
    { title: "Задание", fields: [
      { label: "Режим", value: 2, unit: "", read: true, key: "mode", kind: "mode", text: "Автомат" },
      { label: "Тираж", value: 300, unit: "шт", read: true, key: "total_set", kind: "u32" },
    ] },
    { title: "Провод и зачистка", fields: [
      { label: "Длина провода", value: 325, unit: "мм", read: true, key: "length_mm", kind: "u32" },
    ] },
    { title: "Скорости", fields: [{ label: "Нож", value: 5, unit: "", read: true, key: null, kind: "u16" }] },
  ],
};
const OFFSETS = ["length_mm", "strip_front_mm", "strip_back_mm"].map((key) => ({
  key, label: { length_mm: "Длина провода", strip_front_mm: "Зачистка начала", strip_back_mm: "Зачистка конца" }[key]!,
  offset: "0,00", reason: "", updated_by: "", updated_at: null, etk: key !== "length_mm",
}));
const CONTROL: MachineControl = { problem: "", state: 102, commands: [], offsets: OFFSETS, can_edit: true };
const PLAN: TransferPlan = {
  group_id: "g1", number: "ГР-0007", wire: "ПВАМ 0,35", length_mm: "325,00", quantity: 12,
  rows: [
    { key: "length_mm", label: "Длина провода", unit: "мм", nominal: "325,00", source: "Задание", offset: "0,00", value: "325,00", live: "325,00" },
    { key: "strip_front_mm", label: "Зачистка начала", unit: "мм", nominal: "5,00", source: "ЭТК", offset: "0,40", value: "5,40", live: "5,00" },
    { key: "knife_back_mm", label: "Отвод ножа", unit: "мм", nominal: null, source: "Нет данных", offset: "0,00", value: null, live: "0,10" },
  ],
  job: { total_set: 12, order_quantity: 12, order_number: "GR-0007", product: "PVAM 0,35 L325,00" }, missing: ["Отвод ножа"], blocking: [],
};

function server(control: MachineControl = CONTROL) {
  const posts: { url: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (init?.method === "POST") {
      posts.push({ url, body: JSON.parse(String(init.body)) });
      if (url.endsWith("/correction")) return json({ offset: "0,40", etk: [] });
      return json({ id: "c1", status: "queued", status_text: "Ждёт агента станка", tone: "info", purpose: "settings",
        summary: "Запись", fields: [], error: "", created_by: "Слава", created_at: "2026-11-19T10:00:00+03:00", finished_at: null });
    }
    if (url.endsWith("/control")) return json(control);
    if (url.includes("/transfer?")) return json(PLAN);
    if (url.endsWith("/transfer")) return json({ choices: [{ key: "g1:325", group_id: "g1", number: "ГР-0007", project: "708К.",
      wire: "ПВАМ 0,35", length_mm: "325", quantity: "12" }] });
    return json(WINDOW);
  });
  return posts;
}

afterEach(() => { vi.restoreAllMocks(); });

it("shows every HMI page and writes a changed value only after the confirmation with the machine state", async () => {
  const posts = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  expect(await screen.findByRole("tab", { name: "Скорости" })).toBeInTheDocument();
  expect(screen.getByText("Можно записывать: готов, ждёт пуска, пульт PXS_HMI закрыт")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Скорости" }));
  expect(screen.getByText("только просмотр")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Провод и зачистка" }));
  expect(screen.getByText("325,00 мм")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Длина провода: новое значение"), { target: { value: "326" } });
  fireEvent.click(screen.getByRole("button", { name: "Записать на станок (1)" }));
  const dialog = screen.getByRole("alertdialog");
  expect(within(dialog).getByText("Станок: Готов, ждёт пуска.")).toBeInTheDocument();
  expect(within(dialog).getByText("Длина провода: 325,00 мм → 326 мм")).toBeInTheDocument();
  expect(posts).toEqual([]);
  fireEvent.click(within(dialog).getByRole("button", { name: "Подтвердить запись" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toEqual({ url: "/api/production/machines/card/%D0%9E%D0%91.019/write",
    body: { values: { length_mm: "326" }, confirm_state: 102 } });
  expect(await screen.findByText("Запись настроек: ждёт агента станка")).toBeInTheDocument();
});

it("transfers the ETK job with the machine correction and refuses while the machine runs", async () => {
  const posts = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const pick = await screen.findByLabelText("Задание резки");
  await waitFor(() => expect(within(pick).getAllByRole("option")).toHaveLength(2));
  fireEvent.change(pick, { target: { value: "g1:325" } });
  expect(await screen.findByText("5,40")).toBeInTheDocument();
  expect(screen.getByText("остаётся как на станке")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Передать на станок" }));
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Подтвердить запись" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toEqual({ group_id: "g1", length_mm: "325", confirm_state: 102 });
});

it("blocks writing with the reason when the machine is not idle", async () => {
  server({ ...CONTROL, problem: "Записывать можно, когда станок готов и ждёт пуска или тираж готов; сейчас: Работает", state: 103 });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  fireEvent.change(await screen.findByLabelText("Тираж: новое значение"), { target: { value: "50" } });
  expect(await screen.findByText(/сейчас: Работает/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Записать на станок (1)" })).toBeDisabled();
});

it("records «станок режет не так» with the offset suggested from the measured size", async () => {
  const posts = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const pick = await screen.findByLabelText("Задание резки");
  await waitFor(() => expect(within(pick).getAllByRole("option")).toHaveLength(2));
  fireEvent.change(pick, { target: { value: "g1:325" } });
  await screen.findByText("5,40");
  fireEvent.change(screen.getByLabelText("Получилось, мм"), { target: { value: "4,6" } });
  expect(screen.getByLabelText("Поправка станка, мм")).toHaveValue("0,40");
  fireEvent.click(screen.getByRole("button", { name: "Записать правку" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toEqual({ parameter: "strip_front_mm", case: "machine", measured: "4,6", offset: "0,40" });
  expect(await screen.findByText("Правка записана: поправка станка 0,40 мм")).toBeInTheDocument();
});
