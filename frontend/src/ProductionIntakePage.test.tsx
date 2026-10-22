import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  it("число «Можно принять» — кнопка: окно с КБ, заказано/принято/остаток, количество по умолчанию — всё; уходит дата, место и строка", async () => {
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    expect(screen.getByRole("heading", { name: "Приёмка с производства" })).toBeInTheDocument();
    expect(screen.queryByText("Шлейф")).not.toBeInTheDocument(); // в «Можно принять» только то, что сдано
    const headers = screen.getAllByRole("columnheader").map((cell) => cell.textContent ?? "");
    for (const gone of ["Принять, шт", "Этап", "Сдано цехом", "Место хранения"]) expect(headers.some((text) => text.includes(gone))).toBe(false);
    expect(headers.findIndex((text) => text.includes("Остаток"))).toBe(headers.findIndex((text) => text.includes("Заказано")) + 1);
    expect(screen.queryByLabelText("Сколько принять 591К.3 КБ1")).not.toBeInTheDocument(); // поля в строке нет
    fireEvent.click(screen.getByRole("button", { name: "Принять 591К.3 КБ1: можно 20" }));
    const dialog = await screen.findByRole("dialog", { name: "Приёмка 591К.3 КБ1" });
    expect(within(dialog).getByLabelText("Сколько принять 591К.3 КБ1")).toHaveValue(20);
    fireEvent.change(within(dialog).getByLabelText("Сколько принять 591К.3 КБ1"), { target: { value: "8" } });
    fireEvent.change(within(dialog).getByLabelText("Дата приёмки"), { target: { value: "2026-10-12" } });
    fireEvent.change(within(dialog).getByLabelText("Место хранения 591К.3 КБ1"), { target: { value: "Стеллаж 3" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Принять на склад · 8 шт" }));
    await screen.findByText("Принято на склад: 8 шт");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const post = calls.find((call) => call.method === "POST")!;
    expect(post.body).toEqual({ lines: [{ project: "591К.3", item_index: "КБ1", quantity: "8", location: "Стеллаж 3" }], received_at: "2026-10-12" });
    expect(calls.filter((call) => call.url === "/api/shipping/intake")).toHaveLength(2); // перезагрузка после успеха
  });

  it("«Принять всё» проекта открывает то же окно со всеми КБ; место — у каждой своё", async () => {
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    fireEvent.click(screen.getByRole("button", { name: /Принять всё: 2 КБ · 30 шт/ }));
    const dialog = await screen.findByRole("dialog", { name: "Принять всё 591К.3" });
    expect(within(dialog).getByLabelText("Сколько принять 591К.3 КБ1")).toHaveValue(20);
    expect(within(dialog).getByLabelText("Сколько принять 591К.3 КБ2")).toHaveValue(10);
    fireEvent.change(within(dialog).getByLabelText("Место хранения 591К.3 КБ2"), { target: { value: "Полка 1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Принять на склад · 30 шт" }));
    await screen.findByText("Принято на склад: 30 шт");
    expect(calls.find((call) => call.method === "POST")!.body).toMatchObject({
      lines: [{ project: "591К.3", item_index: "КБ1", quantity: "20" }, { project: "591К.3", item_index: "КБ2", quantity: "10", location: "Полка 1" }],
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

  it("количество сверх лимита или нулевое: ошибка у строки в окне и запрос не уходит", async () => {
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    fireEvent.click(screen.getByRole("button", { name: "Принять 591К.3 КБ1: можно 20" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Сколько принять 591К.3 КБ1"), { target: { value: "21" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Принять на склад · 21 шт" }));
    expect(await within(dialog).findByText("больше сданного: можно 20")).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Сколько принять 591К.3 КБ1"), { target: { value: "0" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Принять на склад · 0 шт" }));
    expect(await within(dialog).findByText("укажите количество больше 0")).toBeInTheDocument();
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("без правки «Отгрузка — склад» управления приёмкой нет, а список виден", async () => {
    render(<ProductionIntakePage principal={viewer} />);
    await screen.findByText("Жгут");
    expect(screen.queryByRole("button", { name: /^Принять 591К\.3 КБ1/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Принять всё/ })).not.toBeInTheDocument();
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

  it("принятое — зелёные строки актов с «Заказано»; «Остаток» один на КБ: заказано − принято по всем ГРН", async () => {
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
    expect(screen.queryByText("Жгут старый")).not.toBeInTheDocument();
    const cells = (tr: Element) => [...tr.querySelectorAll("td")].map((td) => td.textContent);
    const ordered = [...document.querySelectorAll("tbody tr")].filter((tr) => tr.textContent?.includes("Жгут акты")).map(cells);
    expect(ordered).toHaveLength(3); // акт, акт, остаток
    for (const line of ordered) expect(line).toContain("1000"); // «Заказано» на каждой строке, и на зелёных тоже
    expect(ordered.filter((line) => line.includes("200")).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /^Принять 591К.3/ })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Принять 591К.3 КБ1: можно 200" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Сколько принять 591К.3 КБ1")).toHaveValue(200);
    expect(within(dialog).getByText("800")).toBeInTheDocument(); // принято по КБ, не по строке остатка
    fireEvent.click(within(dialog).getByRole("button", { name: "Закрыть" }));
    fireEvent.click(screen.getByRole("tab", { name: /Все/ }));
    expect((await screen.findAllByText("Жгут старый")).length).toBeGreaterThan(0);
  });

  it("остаток по КБ при приёмке в несколько ГРН: 100+350+350+173 принято из 1000 — остаток 27 одной строкой", async () => {
    const day = isoDay(new Date());
    intakeRows = [row({ project: "533К.12", item_index: "КБ1", name: "CCW-0,2", needed: "1000", launched: "1000", delivered: "1000", accepted: "973", remaining: "27", grn: "ГП.26.0000020",
      acts: [["ГП.26.0000020", "100"], ["ГП.26.0000021", "350"], ["ГП.26.0000022", "350"], ["ГП.26.0000023", "173"]].map(([grn, quantity]) => ({ grn, quantity, received_at: day, location: "" })) })];
    render(<ProductionIntakePage principal={principal} />);
    await screen.findAllByText("CCW-0,2");
    const lines = [...document.querySelectorAll("tbody tr")].filter((tr) => tr.textContent?.includes("CCW-0,2"));
    expect(lines).toHaveLength(5);
    const left = lines.map((tr) => [...tr.querySelectorAll("td")][3]?.textContent);
    expect(left).toEqual(["—", "—", "—", "—", "27"]);
  });

  it("«Печать этикетки» у ГРН ставит этикетку в очередь печати и показывает статус", async () => {
    const day = isoDay(new Date());
    intakeRows = [row({ item_index: "КБ2", name: "Жгут печать", needed: "10", launched: "10", delivered: "10", accepted: "10", remaining: "0", grn: "ГП.26.0000030",
      acts: [{ grn: "ГП.26.0000030", quantity: "10", received_at: day, location: "А-1" }] })];
    const base = globalThis.fetch as unknown as (i: RequestInfo | URL, n?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (url === "/api/shipping/intake/%D0%93%D0%9F.26.0000030/label") return json({ status: "queued", reason: "В очереди принтера.", file: "j1", copies: 1, job_id: "j1" });
      return base(input, init);
    }));
    render(<ProductionIntakePage principal={principal} />);
    await screen.findAllByText("Жгут печать");
    fireEvent.click(screen.getByRole("button", { name: "Печать этикетки ГП.26.0000030" }));
    expect(await screen.findByText("В очереди принтера.")).toBeInTheDocument();
    expect(calls.find((call) => call.url.endsWith("/label") && call.method === "POST")?.body).toEqual({ copies: 1 });
    expect(calls.some((call) => call.url.startsWith("/api/stock/"))).toBe(false); // не поиск партии склада
  });

  it("печать недоступна без права на склад", async () => {
    intakeRows = [row({ accepted: "10", acts: [{ grn: "ГП.26.0000031", quantity: "10", received_at: isoDay(new Date()), location: "" }] })];
    render(<ProductionIntakePage principal={viewer} />);
    await screen.findAllByText("Жгут");
    expect(screen.queryByRole("button", { name: /Печать этикетки/ })).not.toBeInTheDocument();
  });
});
