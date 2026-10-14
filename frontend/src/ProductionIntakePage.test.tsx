import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProductionIntakePage } from "./ProductionIntakePage";

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
    expect(screen.getByText(/591К\.3 · Заказчик · срок 25\.10/)).toBeInTheDocument();
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

  it("«Данные отправки»: сохраняет с версией, плановой датой, комментарием и УПД", async () => {
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    fireEvent.click(screen.getAllByRole("button", { name: "Данные отправки" })[0]);
    const receiver = await screen.findByLabelText("Получатель");
    await waitFor(() => expect(receiver).toHaveValue("Получатель"));
    fireEvent.change(screen.getByLabelText("УПД"), { target: { value: "БЕЗ УПД" } });
    fireEvent.change(screen.getByLabelText("Плановая дата отгрузки"), { target: { value: "2026-10-30" } });
    fireEvent.change(screen.getByLabelText("Комментарий"), { target: { value: "Звонить за день" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByText("Данные отправки сохранены");
    const put = calls.find((call) => call.method === "PUT")!;
    expect(put.body).toMatchObject({ project: "591К.3", version: 2, receiver: "Получатель", terms: "САМОВЫВОЗ", upd_type: "БЕЗ УПД", plan_date: "2026-10-30", note: "Звонить за день" });
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
});
