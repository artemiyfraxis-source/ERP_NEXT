import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Ob099Live, UnitSetup } from "./machinesApi";
import { etkPressRequired, heightFits, Ob099Setup, PRESS_READ_ONLY } from "./Ob099Setup";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const LIVE: Ob099Live = {
  state: { text: "Готов", tone: "ok" }, alarms: [],
  job: { product: "61117-4", wire: "A", terminal: "61117-4", applicator: "A-12", equipment_no: "260122-A2" },
  crimp: { height_mm: 1.25, tolerance_mm: 0.05, height_min_mm: 0.5, height_max_mm: 4, cfm_on: true, work_mode: true },
  counters: { total_set: 1400, batch_set: 700, total_done: 156, batch_done: 156 }, tools: [],
};

const TASK = { id: "s1", version: 1, status: "Назначена", operation: "П-118 · Жгут 1 · Опрессовка терминалов · ЭТК 3",
  planned_start: "2026-11-23T08:00:00+03:00", planned_finish: null, mine: true, can_confirm: true,
  source: { task_id: "t1", operation_id: "o1", etk_position: 3 } };

const SETUP: UnitSetup = {
  unit_id: "u99", inventory: "ОБ.099.1", name: "Пресс", model: "BZW-SF4040T", requires_setup: { code: "full", label: "полная" }, reason: "",
  parameters: [
    { key: "applicator", label: "Аппликатор", unit: "", source: "оснастка", source_field: "", register: "" },
    { key: "crimp_height_mm", label: "Высота обжима жилы", unit: "мм", source: "контакт", source_field: "", register: "" },
    { key: "ins_crimp_height_mm", label: "Высота обжима изоляции", unit: "мм", source: "контакт", source_field: "", register: "" },
  ],
  configuration: { values: { crimp_height_mm: "1.20" }, wire: "", contact: "61117-4", set_by: "Иван", set_at: "2026-11-22T12:00:00+03:00", version: 4 },
  references: [{ id: "r1", wire: "МГТФ 0,35", contact: "61117-4", version: 1, values: { ins_crimp_height_mm: "1.9" }, created_by: "Иван", created_at: "2026-11-18T10:00:00+03:00" }],
  open_tasks: [TASK], history: [], online: true,
};

const ETK = { card: { document: { operations: [{ position: 3, code: "PRS", fields: { crimpEnds: [
  { ends: ["A"], art: "61117-4", wire: "МГТФ 0,35", section: "0,35", applicator: "A-12", crimpHeight: "1,10-1,20" },
] } }] } }, focus: { position: 3, positions: [3], code: "PRS" } };

type Call = { url: string; method: string; body: unknown };

function serve({ etk = json(ETK) as Response | (() => Response) } = {}) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = decodeURIComponent(String(input));
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null });
    if (url.endsWith("/etk")) return typeof etk === "function" ? etk() : etk.clone();
    if (url.endsWith("/configuration")) {
      return json({ ok: true, version: 5, values: {}, ...(JSON.parse(String(init?.body)).task_id ? { task_status: "Ready" } : {}) });
    }
    return json(SETUP);
  });
  return calls;
}

function mount(props: Partial<Parameters<typeof Ob099Setup>[0]> = {}, setup: UnitSetup = SETUP) {
  const reload = vi.fn(async () => undefined);
  const hint = vi.fn();
  render(<Ob099Setup inventory="ОБ.099.1" live={LIVE} fresh canWrite setup={setup} error="" reload={reload} onHint={hint} {...props} />);
  return { reload, hint };
}

const row = (name: string) => screen.getByRole("rowheader", { name: new RegExp(`^${name}`) }).closest("tr")!;

afterEach(() => { vi.restoreAllMocks(); });

it("reads what the ETK operation requires: terminal, wire, applicator and crimp height, per section when they differ", () => {
  expect(etkPressRequired(ETK.card.document, 3)).toEqual({ terminal: "61117-4", wire: "МГТФ 0,35", sections: ["0,35"], applicator: "A-12", crimpHeight: "1,10–1,20" });
  expect(etkPressRequired(ETK.card.document, 4)).toBeNull();
  expect(etkPressRequired({ operations: [{ position: 1, fields: { crimpEnds: [
    { ends: ["A"], art: "T1", wire: "W", section: "0,35", applicator: "A-1", crimpHeight: "0,80" },
    { ends: ["B"], art: "T1", wire: "W", section: "0,5", applicator: "A-1", crimpHeight: "0,90" }] } }] }, 1))
    .toMatchObject({ applicator: "A-1", crimpHeight: "0,35: 0,80; 0,5: 0,90" });
  expect(heightFits("1,10–1,20", 1.15)).toBe(true);
  expect(heightFits("1,10–1,20", 1.25)).toBe(false);
  expect(heightFits("1,15 ± 0,05", 1.2)).toBe(true);
  // One number is compared only with a tolerance (the press's own); without one — not at all.
  expect(heightFits("1,15", 1.15)).toBeNull();
  expect(heightFits("1,15", 1.17, 0.03)).toBe(true);
  expect(heightFits("1,15", 1.2, 0.03)).toBe(false);
  expect(heightFits("0,35: 0,80; 0,5: 0,90", 0.8)).toBeNull();
});

it("compares the ETK with the live press, highlights only the difference and confirms the setup by task without any press write", async () => {
  const calls = serve();
  const { reload, hint } = mount();
  expect(screen.getByText(PRESS_READ_ONLY)).toBeTruthy();
  expect(screen.getByText(/На прессе: клемма 61117-4 · аппликатор A-12 · провод A · высота 1,250 мм/)).toBeTruthy();
  expect((screen.getByRole("radio", { name: /Опрессовка терминалов · ЭТК 3/ }) as HTMLInputElement).checked).toBe(true);
  await waitFor(() => expect(within(row("Высота обжима жилы")).getByText("1,10–1,20")).toBeTruthy());
  expect(row("Высота обжима жилы").className).toBe("is-diff");
  expect(within(row("Высота обжима жилы")).getByText("1,250 мм ± 0,050")).toBeTruthy();
  expect(within(row("Высота обжима жилы")).getByText("мм · пределы пресса 0,500–4,000")).toBeTruthy();
  expect(row("Аппликатор").className).toBe("");
  expect(row("Клемма").className).toBe("");
  // Not in the ETK: the newest эталон of the same terminal says it, with its date.
  expect(within(row("Высота обжима изоляции")).getAllByRole("cell")[0].textContent).toBe("1,9 · эталон 18.11");
  // The press differs from the ETK: its value is not copied into «Настроено».
  expect((within(row("Высота обжима жилы")).getByRole("textbox") as HTMLInputElement).value).toBe("");
  await waitFor(() => expect(hint).toHaveBeenLastCalledWith(
    "Пресс не как требуется: высота обжима жилы — выставьте на пульте пресса и нажмите «Подтвердить наладку»"));

  fireEvent.change(within(row("Высота обжима жилы")).getByRole("textbox"), { target: { value: "1,15" } });
  fireEvent.click(screen.getByRole("button", { name: "Подтвердить наладку" }));
  // The press still differs: an explicit second confirmation that names what differs.
  const confirm = screen.getByRole("alertdialog", { name: "Не как требуется" });
  expect(confirm.textContent).toContain("Подтвердить, как есть: 1 параметр не как требуется — высота обжима жилы");
  expect(calls.some((call) => call.method === "POST")).toBe(false);
  fireEvent.click(within(confirm).getByRole("button", { name: "Подтвердить, как есть" }));
  expect(await screen.findByText(/^Записано в ERP \d\d:\d\d · задание наладки готово$/)).toBeTruthy();
  const posted = calls.filter((call) => call.method !== "GET");
  expect(posted).toEqual([{ url: "/api/production/setup-model/ОБ.099.1/configuration", method: "POST", body: {
    expected_version: 4, task_id: "s1", task_version: 1, values: { applicator: "A-12", crimp_height_mm: "1,15", ins_crimp_height_mm: "1,9" },
    wire: "A", contact: "61117-4", reason: "" } }]);
  expect(calls.some((call) => /\/machines\/card\/|\/command|\/write|\/run/.test(call.url))).toBe(false);
  expect(reload).toHaveBeenCalled();
});

it("records a setup without a task only with a reason and fills from the press, not from stale words", async () => {
  const calls = serve();
  mount({ live: { ...LIVE, crimp: { ...LIVE.crimp, height_mm: 1.15 } } }, { ...SETUP, open_tasks: [] });
  const button = screen.getByRole("button", { name: "Записать наладку" });
  expect(button).toBeDisabled();
  expect(screen.getByText("Укажите причину наладки без задания")).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox", { name: "Причина" }), { target: { value: "смена аппликатора" } });
  expect(button).not.toBeDisabled();
  fireEvent.click(button);
  await screen.findByText(/^Записано в ERP \d\d:\d\d$/);
  expect(calls.find((call) => call.method === "POST")?.body).toMatchObject({ task_id: null, reason: "смена аппликатора",
    contact: "61117-4", values: { applicator: "A-12", crimp_height_mm: "1,150" } });
  expect(calls.some((call) => call.url.endsWith("/etk"))).toBe(false);
});

it("does not prefill «Настроено» from stale press words and greys the press column", async () => {
  serve();
  mount({ fresh: false }, { ...SETUP, open_tasks: [], configuration: null, references: [] });
  expect((within(row("Высота обжима жилы")).getByRole("textbox") as HTMLInputElement).value).toBe("");
  expect(screen.getByRole("columnheader", { name: "На прессе · последние" })).toBeTruthy();
  expect(within(row("Высота обжима жилы")).getByText("1,250 мм ± 0,050").className).toContain("machine-muted");
});

it("says why the command waits: no right, or a task of another technician", () => {
  serve();
  mount({ canWrite: false });
  expect(screen.getByRole("button", { name: "Подтвердить наладку" })).toBeDisabled();
  expect(screen.getByText("Нет права записывать наладку")).toBeTruthy();
});

it("a task assigned to another technician cannot be confirmed here", () => {
  serve();
  mount({}, { ...SETUP, open_tasks: [{ ...TASK, mine: false, can_confirm: false }] });
  fireEvent.click(screen.getByRole("radio", { name: /Опрессовка терминалов · ЭТК 3/ }));
  expect(screen.getByRole("button", { name: "Подтвердить наладку" })).toBeDisabled();
  expect(screen.getByText("Задание наладки назначено другому наладчику")).toBeTruthy();
});

it("an unreadable ETK is said once and the эталон still gives what it knows", async () => {
  serve({ etk: () => json({ detail: "Операция не назначена сотруднику" }, 403) });
  mount({ live: { ...LIVE, job: { ...LIVE.job, wire: "МГТФ 0,35" } } });
  expect(await screen.findByText(/ЭТК задания не прочитана: Операция не назначена сотруднику/)).toBeTruthy();
  expect(within(row("Высота обжима изоляции")).getByText("1,9")).toBeTruthy();
});

it("without a setup model shows the press rows and saves them by their names", async () => {
  const calls = serve();
  mount({}, { ...SETUP, parameters: [], requires_setup: { code: null, label: "не задано" }, open_tasks: [] });
  for (const name of ["Клемма", "Провод", "Аппликатор", "Высота обжима жилы", "Высота обжима изоляции", "Окно усилия обжима", "Программа пресса"]) {
    expect(row(name)).toBeTruthy();
  }
  fireEvent.change(screen.getByRole("textbox", { name: "Причина" }), { target: { value: "проверка" } });
  fireEvent.click(screen.getByRole("button", { name: "Записать наладку" }));
  await screen.findByText(/^Записано в ERP/);
  expect(calls.find((call) => call.method === "POST")?.body).toMatchObject({ values: { "Аппликатор": "A-12", "Высота обжима жилы, мм": "1,250", "Программа пресса": "61117-4" } });
});

const REF = SETUP.references[0];

it("several эталоны of one terminal, wire and applicator are never picked silently", async () => {
  serve();
  mount({}, { ...SETUP, references: [REF, { ...REF, id: "r2", values: { ins_crimp_height_mm: "1.8" }, created_at: "2026-11-20T10:00:00+03:00" },
    { ...REF, id: "r4", values: { applicator: "A-99", ins_crimp_height_mm: "2.2" }, created_at: "2026-11-21T10:00:00+03:00" }] });
  const pick = await screen.findByRole("combobox", { name: "Несколько эталонов — выберите" });
  await waitFor(() => expect(within(row("Высота обжима жилы")).getByText("1,10–1,20")).toBeTruthy());
  expect(within(row("Высота обжима изоляции")).getAllByRole("cell")[0].textContent).toBe("—");
  expect((within(row("Высота обжима изоляции")).getByRole("textbox") as HTMLInputElement).value).toBe("");
  fireEvent.change(pick, { target: { value: "r2" } });
  expect(within(row("Высота обжима изоляции")).getAllByRole("cell")[0].textContent).toBe("1,8 · эталон 20.11");
  // An эталон of another applicator is not a candidate at all.
  expect(within(pick).getAllByRole("option").map((option) => option.textContent)).toEqual(["не брать эталон", "20.11 · МГТФ 0,35", "18.11 · МГТФ 0,35"]);
});

it("an эталон for another wire size (AWG while the ETK gives mm²) is not used", async () => {
  serve();
  mount({}, { ...SETUP, references: [{ ...REF, wire: "AWG22" }, { ...REF, id: "r3", values: { applicator: "A-99", ins_crimp_height_mm: "2.2" } }] });
  await waitFor(() => expect(within(row("Высота обжима жилы")).getByText("1,10–1,20")).toBeTruthy());
  expect(within(row("Высота обжима изоляции")).getAllByRole("cell")[0].textContent).toBe("—");
  expect(screen.queryByRole("combobox", { name: /эталон/i })).toBeNull();
});

it("a double click on the command sends one request", async () => {
  const calls: string[] = [];
  let answer: (value: Response) => void = () => undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if ((init?.method ?? "GET") === "POST") { calls.push(String(input)); return new Promise<Response>((resolve) => { answer = resolve; }); }
    return json(ETK);
  });
  mount({ live: { ...LIVE, crimp: { ...LIVE.crimp, height_mm: 1.15 } } });
  await waitFor(() => expect(within(row("Высота обжима жилы")).getByText("1,10–1,20")).toBeTruthy());
  const button = screen.getByRole("button", { name: "Подтвердить наладку" });
  fireEvent.click(button);
  fireEvent.click(button);
  await waitFor(() => expect(calls).toHaveLength(1));
  answer(json({ ok: true, version: 5, values: {}, task_status: "Ready" }));
  expect(await screen.findByText(/^Записано в ERP/)).toBeTruthy();
  expect(calls).toHaveLength(1);
});

it("a single-number norm without any tolerance is neutral: no highlight, no «выставьте» hint", async () => {
  serve({ etk: () => json({ ...ETK, card: { document: { operations: [{ position: 3, fields: { crimpEnds: [
    { ends: ["A"], art: "61117-4", wire: "МГТФ 0,35", section: "0,35", applicator: "A-12", crimpHeight: "1,15" }] } }] } } }) });
  const { hint } = mount({ live: { ...LIVE, crimp: { ...LIVE.crimp, tolerance_mm: null } } });
  await waitFor(() => expect(within(row("Высота обжима жилы")).getByText("1,15")).toBeTruthy());
  expect(row("Высота обжима жилы").className).toBe("");
  await waitFor(() => expect(hint).toHaveBeenLastCalledWith("Сверьте пресс с ЭТК и нажмите «Подтвердить наладку»"));
});
