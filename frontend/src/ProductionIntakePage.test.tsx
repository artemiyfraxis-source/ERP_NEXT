import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProductionIntakePage } from "./ProductionIntakePage";
import { isoDay } from "./Gantt";

const principal = {
  user: { id: "u1", email: "k@example.test", name: "Кладовщик", department: "Склад" },
  roles: ["foreman"], access_level: "foreman", permissions: { shipping_goods: "edit" as const },
};
const viewer = { ...principal, permissions: { shipping: "view" as const } };

const row = (over: Record<string, unknown>) => ({
  project: "591К.3", customer: "Заказчик", deadline: "2026-10-25", item_index: "КБ1", name: "Жгут", unit: "шт", stage: "Опрессовка",
  needed: "40", launched: "40", delivered: "26", accepted: "6", packed: "0", sent: "0", remaining: "20",
  good_id: "g1", good_version: 1, grn: "ГП.26.0000001", location: "", tasks: [{ number: "ПЗ-0001", status: "Запущено", site: "ЖУК", quantity: 40, delivered: "26" }], ...over,
});
const ROWS = [
  row({}),
  row({ item_index: "КБ2", name: "Кабель", needed: "10", launched: "10", delivered: "10", accepted: "0", remaining: "10", good_id: null, good_version: null, grn: null }),
  row({ project: "600К.1", customer: "Другой", item_index: "КБ7", name: "Шлейф", needed: "5", launched: "5", delivered: "0", accepted: "0", remaining: "0", tasks: [] }),
];
const dispatchCard = {
  project: "591К.3", version: 2, receiver: "Получатель", shipping_point: "Москва", contact: "", phone: "", terms: "САМОВЫВОЗ", carrier: "",
  upd_type: "ЭДО", updated_by: "", updated_at: null, po: "", contract: "", spec: "", invoice: "", pays: "", customer_address: "", hours: "",
  plan_date: null, note: "",
};

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
let calls: Array<{ url: string; method: string; body: unknown }> = [];
let intakeRows: unknown[] = ROWS;

function stub(acceptStatus = 200, acceptBody: unknown = { goods: [], replayed: false }) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input), method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url === "/api/shipping/intake") return json({ rows: intakeRows });
    if (url === "/api/shipping/goods") return json({ goods: [] });
    if (url === "/api/production/finished-goods-ready") return json({ boxes: [] });
    if (url === "/api/shipping/finished-goods/accept") return json(acceptBody, acceptStatus);
    if (url.startsWith("/api/shipping/project-addressee?")) return json({ addressee: dispatchCard });
    if (url === "/api/shipping/project-addressee" && method === "PUT") return json({ addressee: { ...dispatchCard, version: 3 } });
    return json({});
  }));
}

beforeEach(() => { intakeRows = ROWS; stub(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Приёмка с производства", () => {
  it("принимает кусками: количество строки меняется, уходит дата, место и только отмеченное", async () => {
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    expect(screen.getByRole("heading", { name: "Приёмка с производства" })).toBeInTheDocument();
    expect(screen.queryByText("Шлейф")).not.toBeInTheDocument(); // в «Можно принять» только то, что сдано
    fireEvent.change(screen.getByLabelText("Сколько принять 591К.3 КБ1"), { target: { value: "8" } });
    fireEvent.change(screen.getByLabelText("Дата приёмки"), { target: { value: "2026-10-12" } });
    fireEvent.change(screen.getByLabelText("Место хранения"), { target: { value: "Стеллаж 3" } });
    fireEvent.click(screen.getByRole("button", { name: "Принять на склад · 8 шт" }));
    await screen.findByText("Принято на склад: 8 шт");
    const post = calls.find((call) => call.method === "POST")!;
    expect(post.body).toEqual({ lines: [{ project: "591К.3", item_index: "КБ1", quantity: "8" }], received_at: "2026-10-12", location: "Стеллаж 3" });
    expect(calls.filter((call) => call.url === "/api/shipping/intake")).toHaveLength(2); // перезагрузка после успеха
  });

  it("одно намерение — одно поле: неотмеченная строка без количества, флажок ставит всё сданное, пустое поле снимает отметку", async () => {
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    const amount = screen.getByLabelText("Сколько принять 591К.3 КБ1");
    expect(amount).toHaveValue(null);
    expect(amount).toHaveAttribute("placeholder", "до 20");
    fireEvent.click(screen.getByLabelText("Принять 591К.3 КБ1"));
    expect(amount).toHaveValue(20);
    expect(screen.getByRole("button", { name: "Принять на склад · 20 шт" })).toBeEnabled();
    fireEvent.change(amount, { target: { value: "" } });
    expect(screen.getByLabelText("Принять 591К.3 КБ1")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Принять на склад · 0 шт" })).toBeDisabled();
  });

  it("«все к приёмке» отмечает все КБ проекта разом; своё место строки уходит в строке, общее — в пакете", async () => {
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    expect(screen.getByText(/все к приёмке: 2 КБ · 30 шт/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Отметить все КБ проекта 591К.3"));
    expect(screen.getByLabelText("Принять 591К.3 КБ1")).toBeChecked();
    expect(screen.getByLabelText("Принять 591К.3 КБ2")).toBeChecked();
    fireEvent.change(screen.getByLabelText("Место хранения"), { target: { value: "Стеллаж 3" } });
    fireEvent.change(screen.getByLabelText("Место хранения 591К.3 КБ2"), { target: { value: "Полка 1" } });
    fireEvent.click(screen.getByRole("button", { name: "Принять на склад · 30 шт" }));
    await screen.findByText("Принято на склад: 30 шт");
    expect(calls.find((call) => call.method === "POST")!.body).toMatchObject({
      lines: [{ project: "591К.3", item_index: "КБ1", quantity: "20" }, { project: "591К.3", item_index: "КБ2", quantity: "10", location: "Полка 1" }],
      location: "Стеллаж 3",
    });
  });

  it("пустые колонки не показываются; просроченный срок проекта виден в заголовке", async () => {
    const past = isoDay(new Date(Date.now() - 8 * 86_400_000));
    intakeRows = [row({ deadline: past })];
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    const headers = screen.getAllByRole("columnheader").map((cell) => cell.textContent);
    expect(headers.some((text) => text?.includes("Упаковано"))).toBe(false);
    expect(headers.some((text) => text?.includes("Отгружено"))).toBe(false);
    expect(headers.some((text) => text?.includes("Принято"))).toBe(true);
    expect(screen.getByText(/просрочен на 8 дн\./)).toHaveClass("tone-danger");
  });

  it("строка сверх лимита: ошибка у строки и запрос не уходит", async () => {
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    fireEvent.change(screen.getByLabelText("Сколько принять 591К.3 КБ1"), { target: { value: "21" } });
    fireEvent.click(screen.getByRole("button", { name: "Принять на склад · 21 шт" }));
    expect(await screen.findByText("больше сданного: можно 20")).toBeInTheDocument();
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("без правки «Отгрузка — склад» управления приёмкой нет, а список виден", async () => {
    render(<ProductionIntakePage principal={viewer} />);
    await screen.findByText("Жгут");
    expect(screen.queryByLabelText("Дата приёмки")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Принять 591К.3 КБ1")).not.toBeInTheDocument();
  });

  it("«Все» показывает и КБ, которые цех ещё не сдал; заголовок проекта с заказчиком и сроком", async () => {
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    expect(screen.getByText(/^591К\.3 · Заказчик/)).toBeInTheDocument();
    expect(screen.getByText(/^срок 25\.10/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /^Все/ }));
    expect(await screen.findByText("Шлейф")).toBeInTheDocument();
  });

  it("принимать нечего — открывается «В производстве»: КБ с этапом «В работе» без запущенных заданий, «Новый» — только во «Все»", async () => {
    intakeRows = [
      row({ item_index: "КБ1", name: "Жгут в работе", stage: "В работе", launched: "0", delivered: "0", accepted: "0", remaining: "0", tasks: [] }),
      row({ item_index: "КБ2", name: "Жгут новый", stage: "Новый", launched: "0", delivered: "0", accepted: "0", remaining: "0", tasks: [] }),
    ];
    render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findByText("Жгут в работе")).toBeInTheDocument();
    expect(screen.queryByText("Жгут новый")).toBeNull();
    expect(screen.getByRole("tab", { name: /^В производстве/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /^Можно принять/ })).toHaveTextContent("0");
    expect(screen.getByRole("tab", { name: /^Все/ })).toHaveTextContent("2");
    fireEvent.click(screen.getByRole("tab", { name: /^Все/ }));
    expect(await screen.findByText("Жгут новый")).toBeInTheDocument();
  });

  it("«Данные отправки»: сохраняет с версией только изменённое из четырёх полей", async () => {
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    fireEvent.click(screen.getAllByRole("button", { name: "Данные отправки" })[0]);
    const receiver = await screen.findByLabelText("Получатель");
    await waitFor(() => expect(receiver).toHaveValue("Получатель"));
    fireEvent.change(screen.getByLabelText("УПД"), { target: { value: "БЕЗ УПД" } });
    fireEvent.change(screen.getByLabelText("Телефон"), { target: { value: " +7 900 " } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByText("Данные отправки сохранены");
    const put = calls.find((call) => call.method === "PUT")!;
    expect(put.body).toEqual({ project: "591К.3", version: 2, phone: "+7 900", upd_type: "БЕЗ УПД" });
  });

  it("менеджер с правом только на чтение видит карточку без «Сохранить»", async () => {
    render(<ProductionIntakePage principal={viewer} />);
    await screen.findByText("Жгут");
    fireEvent.click(screen.getAllByRole("button", { name: "Данные отправки" })[0]);
    await screen.findByText("Только просмотр");
    expect(screen.queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
  });

  it("конфликт версии при сохранении просит открыть карточку заново", async () => {
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    fireEvent.click(screen.getAllByRole("button", { name: "Данные отправки" })[0]);
    await waitFor(() => expect(screen.getByLabelText("Получатель")).toHaveValue("Получатель"));
    const base = globalThis.fetch as unknown as (i: RequestInfo | URL, n?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", vi.fn(async (i: RequestInfo | URL, n?: RequestInit) => n?.method === "PUT" ? json({ detail: "version" }, 409) : base(i, n)));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByText(/откройте его заново/)).toBeInTheDocument();
  });

  it("принятое остаётся зелёными строками актов, а остаток — отдельной строкой с флажком", async () => {
    const day = isoDay(new Date());
    intakeRows = [
      row({ item_index: "КБ1", name: "Жгут акты", needed: "1000", launched: "1000", delivered: "1000", accepted: "800", remaining: "200", grn: "ГП.26.0000010",
        acts: [{ grn: "ГП.26.0000010", quantity: "500", received_at: day, location: "А-1" }, { grn: "ГП.26.0000011", quantity: "300", received_at: day, location: "А-2" }] }),
      row({ item_index: "КБ2", name: "Жгут целиком", needed: "10", launched: "10", delivered: "10", accepted: "10", remaining: "0", grn: "ГП.26.0000012",
        acts: [{ grn: "ГП.26.0000012", quantity: "10", received_at: day, location: "" }] }),
      row({ item_index: "КБ3", name: "Жгут старый", needed: "10", launched: "10", delivered: "10", accepted: "10", remaining: "0", grn: "ГП.26.0000013",
        acts: [{ grn: "ГП.26.0000013", quantity: "10", received_at: "2026-01-10", location: "" }] }),
    ];
    render(<ProductionIntakePage principal={principal} />);
    await screen.findAllByText("Жгут акты");
    expect(document.querySelectorAll("tr.intake-act-row")).toHaveLength(3); // два акта КБ1 и акт КБ2; КБ3 принята давно — только во «Все»
    expect(screen.getByText("ГП.26.0000010")).toBeInTheDocument();
    expect(screen.getByText("ГП.26.0000011")).toBeInTheDocument();
    expect(screen.queryByText("Жгут старый")).not.toBeInTheDocument();
    // Остаток КБ1 — одна строка с флажком, «до 200»; у актов и у КБ2 (принята целиком) полей ввода нет.
    expect(screen.getAllByLabelText(/Принять 591К.3/)).toHaveLength(1);
    expect(screen.getByLabelText("Сколько принять 591К.3 КБ1")).toHaveAttribute("placeholder", "до 200");
    fireEvent.click(screen.getByRole("tab", { name: /Все/ }));
    expect((await screen.findAllByText("Жгут старый")).length).toBeGreaterThan(0);
  });
});
