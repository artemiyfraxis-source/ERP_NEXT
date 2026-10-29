import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProductionIntakePage } from "./ProductionIntakePage";
import { isoDay } from "./Gantt";
import { capturePrintFrames } from "./testPrintFrames";

const principal = {
  user: { id: "u1", email: "k@example.test", name: "Кладовщик", department: "Склад" },
  roles: ["foreman"], access_level: "foreman", permissions: { shipping_goods: "edit" as const },
};
const viewer = { ...principal, permissions: { shipping: "view" as const } };
const admin = { ...principal, roles: ["admin"], access_level: "admin", permissions: { shipping: "edit" as const, shipping_goods: "edit" as const } };

const row = (over: Record<string, unknown>) => ({
  project: "591К.3", customer: "Заказчик", deadline: "2026-10-25", item_index: "КБ1", name: "Жгут", unit: "шт", stage: "В работе",
  project_stage: "В работе", pack_decided: false, needed: "40", launched: "40", delivered: "26", accepted: "6", packed: "0", sent: "0", remaining: "20",
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

const shipmentLine = (over: Record<string, unknown> = {}) => ({ id: "l1", good_id: "g1", project: "591К.3", item_index: "КБ1", article: "", name: "Жгут", requested: "4", picked: "4", shortage: "0", unit: "шт", grn: "ГП.26.0000001", ...over });
const packedShipment = {
  id: "s1", number: "ОТГ-20261029-01", version: 3, created_at: "2026-10-29T09:00:00", status: "Упакована",
  customer: "Заказчик", receiver: "Получатель", shipping_point: "Москва", contact: "", phone: "+7 900", terms: "ДОСТАВКА ТК", carrier: "СДЭК",
  deadline: null, upd_type: "ЭДО", upd_number: "", upd_date: null, upd_url: "", pl_number: "PL591К.3-1", dn_number: "DN591К.3-1", track_number: "",
  packed_at: "2026-10-29", shipped_at: null, manager: "", note: "", tk_note: "", places: 1, weight: "", boxes: [], inherited: [],
  missing_data: ["№ УПД"], missing_ship: ["№ УПД", "трек-номер"], ready_for_track: false, ready_to_ship: false, no_upd_approved_by: null, no_upd_approved_at: null,
  lines: [shipmentLine()],
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
let calls: Array<{ url: string; method: string; body: unknown }> = [];
let intakeRows: unknown[] = ROWS;
let shipments: unknown[] = [];
/** Ответы сервера на «Разрешить без УПД» по очереди: [status, body]. */
let approvals: Array<[number, unknown]> = [];

function stub(acceptStatus = 200, acceptBody: unknown = { goods: [], replayed: false }) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input), method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url === "/api/shipping/intake") return json({ rows: intakeRows });
    if (url === "/api/shipping/shipments" && method === "GET") return json({ shipments });
    if (url === "/api/shipping/goods") return json({ goods: [] });
    if (url === "/api/production/finished-goods-ready") return json({ boxes: [] });
    if (url === "/api/shipping/finished-goods/accept") return json(acceptBody, acceptStatus);
    if (url.startsWith("/api/shipping/project-addressee?")) return json({ addressee: dispatchCard });
    if (url === "/api/shipping/project-addressee" && method === "PUT") return json({ addressee: { ...dispatchCard, version: 3 } });
    if (url === "/api/shipping/project-ship") return json({ shipment: { ...packedShipment, id: "s9", number: "ОТГ-9", pl_number: "PL591К.3-9" }, shipped: false, closed: false, why: "", replayed: false });
    if (url === "/api/shipping/no-upd-approvers") return json({ approvers: [{ id: "p1", name: "Артемий" }, { id: "p2", name: "Зингер" }, { id: "p3", name: "Касаточкин" }] });
    if (url.endsWith("/approve-no-upd")) { const [status, body] = approvals.shift() ?? [200, { shipment: packedShipment }]; return json(body, status); }
    if (/\/print\?document=/.test(url)) return json({ forms: [{ kind: "pl", title: "PL1-1", number: ["PL", "1", "-", "1"], place: "1", places: "1", package: "Короб", dims: "", net: "", gross: "", seal: "",
      rows: [{ part: "КБ1", name: "", quantity: "1", unit: "шт", batch: "", pl: "", boxes: "" }], packed_at: "", shipped_at: "", packer: "", shipper: "", consignee: "", address: "", project: "591К.3" }] });
    if (url.endsWith("/labels")) return json({ labels: [{ shipper: "Фраксис", consignee: "Получатель", address: "Москва", project: "591К.3", pl_number: "PL591К.3-1", place: "Упаковка 1/1", items: [{ item_index: "КБ1", quantity: "4", unit: "шт" }], net: "", dims: "" }] });
    if (url.endsWith("/documents") && method === "PUT") return json({ shipment: packedShipment });
    if (/\/shipments\/[^/]+\/(ship|close|unship)$/.test(url)) return json({ shipment: packedShipment, closed: false, why: "Нельзя закрыть: не хватает — файл УПД" });
    return json({});
  }));
}
beforeEach(() => { intakeRows = ROWS; shipments = []; approvals = []; stub(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Приёмка с производства", () => {
  it("число «Можно принять» — кнопка: окно с КБ, заказано/принято/остаток, количество по умолчанию — всё; уходит дата, место и строка", async () => {
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    expect(screen.getByRole("heading", { name: "Приёмка с производства" })).toBeInTheDocument();
    expect(screen.queryByText("Шлейф")).not.toBeInTheDocument(); // в «Можно принять» только то, что сдано
    const headers = screen.getAllByRole("columnheader").map((cell) => cell.textContent ?? "");
    for (const gone of ["Принять, шт", "Этап", "Сдано цехом", "Место хранения"]) expect(headers.some((text) => text.includes(gone))).toBe(false);
    expect(headers.map((text) => text.replace(/[^А-Яа-яЁё ]/g, "").trim())).toEqual(["КБ", "Наименование", "Заказано", "Можно принять", "Задания"]);
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
    expect(headers.some((text) => text?.includes("Отгружено"))).toBe(false);
    expect(headers.some((text) => text?.includes("Трек"))).toBe(false);
    expect(headers.some((text) => text?.includes("Можно принять"))).toBe(true);
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

  it("вкладки — шаги пути с числами; принимать нечего — открывается первый непустой шаг", async () => {
    intakeRows = [
      row({ item_index: "КБ2", name: "Жгут новый", stage: "Новый", project_stage: "Новый", launched: "0", delivered: "0", accepted: "0", remaining: "0", tasks: [] }),
    ];
    render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findByText("Жгут новый")).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent?.replace(/\s*\d+$/, ""))).toEqual(
      ["В ожидании", "В производстве", "Готово", "Принято", "Упаковано", "На отгрузку", "Отгружено", "Все"]);
    expect(screen.getByRole("tab", { name: /^В ожидании/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /^Готово/ })).toHaveTextContent("0");
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
    expect(screen.getByRole("tab", { name: /^Готово/ })).toHaveAttribute("aria-selected", "true");
    expect(document.querySelectorAll("tr.intake-act-row")).toHaveLength(0); // акты — только в «Принято»
    expect(screen.queryByText("Жгут старый")).not.toBeInTheDocument();
    const kb1 = [...document.querySelectorAll("tbody tr")].filter((tr) => tr.textContent?.includes("Жгут акты"));
    expect(kb1).toHaveLength(1);
    expect(kb1[0].textContent).toContain("1000");
    expect(kb1[0].textContent).toContain("200");
    expect(screen.getAllByRole("button", { name: /^Принять 591К.3/ })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Принять 591К.3 КБ1: можно 200" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Сколько принять 591К.3 КБ1")).toHaveValue(200);
    expect(within(dialog).getByText("800")).toBeInTheDocument(); // принято по КБ, не по строке остатка
    fireEvent.click(within(dialog).getByRole("button", { name: "Закрыть" }));
    fireEvent.click(screen.getByRole("tab", { name: /^Принято/ }));
    expect((await screen.findAllByText("Жгут старый")).length).toBeGreaterThan(0);
    expect(document.querySelectorAll("tr.intake-act-row")).toHaveLength(4);
    const cells = (tr: Element) => [...tr.querySelectorAll("td")].map((td) => td.textContent);
    const ordered = [...document.querySelectorAll("tbody tr")].filter((tr) => tr.textContent?.includes("Жгут акты")).map(cells);
    expect(ordered).toHaveLength(3); // акт, акт, строка КБ
  });

  it("остаток по КБ при приёмке в несколько ГРН: 100+350+350+173 принято из 1000 — остаток 27 одной строкой", async () => {
    const day = isoDay(new Date());
    intakeRows = [row({ project: "533К.12", item_index: "КБ1", name: "CCW-0,2", needed: "1000", launched: "1000", delivered: "1000", accepted: "973", remaining: "27", grn: "ГП.26.0000020",
      acts: [["ГП.26.0000020", "100"], ["ГП.26.0000021", "350"], ["ГП.26.0000022", "350"], ["ГП.26.0000023", "173"]].map(([grn, quantity]) => ({ grn, quantity, received_at: day, location: "" })) })];
    render(<ProductionIntakePage principal={principal} />);
    await screen.findAllByText("CCW-0,2");
    const rowsIn = () => [...document.querySelectorAll("tbody tr")].filter((tr) => tr.textContent?.includes("CCW-0,2"));
    expect(rowsIn()).toHaveLength(1); // «Готово»: одна строка, «Можно принять» 27, без актов
    expect([...rowsIn()[0].querySelectorAll("td")][3]?.textContent).toBe("27");
    fireEvent.click(screen.getByRole("tab", { name: /^Принято/ }));
    await waitFor(() => expect(rowsIn()).toHaveLength(5)); // четыре акта и строка КБ
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
    expect(calls.some((call) => call.url.startsWith("/api/stock/labels/lot") || (call.url.startsWith("/api/stock/labels") && call.method === "POST"))).toBe(false); // не поиск партии склада и не печать партии
  });

  it("печать недоступна без права на склад", async () => {
    intakeRows = [row({ accepted: "10", acts: [{ grn: "ГП.26.0000031", quantity: "10", received_at: isoDay(new Date()), location: "" }] })];
    render(<ProductionIntakePage principal={viewer} />);
    await screen.findAllByText("Жгут");
    expect(screen.queryByRole("button", { name: /Печать этикетки/ })).not.toBeInTheDocument();
  });
});

describe("Приёмка с производства: упаковка и отгрузка на этом же экране (владелец 29.10)", () => {
  const today = isoDay(new Date());
  const multi = () => row({ needed: "40", delivered: "26", accepted: "20", packed: "10", sent: "3", remaining: "20", pack_decided: true, acts: [] });
  const ready = { ...packedShipment, id: "s2", number: "ОТГ-20261029-02", ready_to_ship: true, ready_for_track: true, track_number: "TRK-1", missing_ship: [], missing_data: [], lines: [shipmentLine({ id: "l2", picked: "3", requested: "3" })] };
  const gone = { ...packedShipment, id: "s3", number: "ОТГ-20261029-03", status: "Отгружена", shipped_at: today, track_number: "TRK-0", missing_ship: [], missing_data: [], lines: [shipmentLine({ id: "l3", picked: "3", requested: "3" })] };
  const tab = (name: RegExp) => screen.getByRole("tab", { name });
  const count = (name: RegExp) => tab(name).querySelector(".page-count")?.textContent?.trim();

  it("КБ, у которой штуки в нескольких шагах, видна в каждом; числа на вкладках; по умолчанию — первый шаг с работой", async () => {
    intakeRows = [multi()];
    shipments = [packedShipment, ready, gone];
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    // 20 не принято: 6 сдано цехом («Готово»), 14 ещё в работе — их тоже можно принять («В производстве», 28.10).
    expect(tab(/^В производстве/)).toHaveAttribute("aria-selected", "true");
    expect([/^В ожидании/, /^В производстве/, /^Готово/, /^Принято/, /^Упаковано/, /^На отгрузку/, /^Отгружено/, /^Все/].map(count))
      .toEqual(["0", "1", "1", "1", "1", "1", "1", "1"]);
    for (const name of [/^Готово/, /^Принято/, /^Упаковано/, /^На отгрузку/, /^Отгружено/]) {
      fireEvent.click(tab(name));
      expect(await screen.findByText("Жгут")).toBeInTheDocument();
    }
    fireEvent.click(tab(/^В ожидании/));
    expect(await screen.findByText("В ожидании производства ничего нет.")).toBeInTheDocument();
  });

  it("уехавшая целиком КБ (её нет в приёмке) видна в «Отгружено» из отправок за 30 дней", async () => {
    intakeRows = [];
    shipments = [{ ...gone, lines: [shipmentLine({ id: "l9", item_index: "КБ9", name: "Уехавший жгут", picked: "5", requested: "5" })] }];
    render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findByText("Уехавший жгут")).toBeInTheDocument();
    expect(tab(/^Отгружено/)).toHaveAttribute("aria-selected", "true");
    const line = [...document.querySelectorAll("tbody tr")].filter((tr) => tr.textContent?.includes("Уехавший жгут"))[0];
    expect(line.querySelectorAll("button")).toHaveLength(0); // в «Отгружено» у строки нет кнопок
    expect(line.textContent).toContain("5");
  });

  it("«Упаковано»: без решения менеджера кнопка выключена; с решением — окно, количество по умолчанию — всё принятое", async () => {
    intakeRows = [row({ accepted: "6", remaining: "0", delivered: "6", tasks: [] })];
    const { unmount } = render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Принято/ }));
    const waiting = await screen.findByRole("button", { name: "Упаковать 591К.3 КБ1: ждёт решения менеджера" });
    expect(waiting).toBeDisabled();
    expect(waiting).toHaveAttribute("title", "Упаковать можно после решения менеджера «упаковывать»");
    unmount();

    // Под зелёной строкой акта у строки КБ «Принято» — прочерк, а упаковать всё равно можно всё принятое.
    intakeRows = [row({ accepted: "6", remaining: "0", delivered: "6", tasks: [], pack_decided: true, acts: [{ grn: "ГП.26.0000001", quantity: "6", received_at: isoDay(new Date()), location: "" }] })];
    render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findByRole("tab", { name: /^Принято/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(await screen.findByRole("button", { name: "Упаковать 591К.3 КБ1: упаковано 0, можно 6" }));
    const dialog = await screen.findByRole("dialog", { name: "Упаковка 591К.3" });
    expect(within(dialog).getByLabelText("Сколько упаковать КБ1")).toHaveValue(6);
    const pack = within(dialog).getByRole("button", { name: "Упаковать · 6 шт" });
    await waitFor(() => expect(pack).toBeEnabled()); // версия карточки отправки пришла
    fireEvent.click(pack);
    expect(await within(dialog).findByText("Упаковано: ОТГ-9 · PL591К.3-9")).toBeInTheDocument();
    expect(calls.find((call) => call.url === "/api/shipping/project-ship")?.body).toEqual({
      project: "591К.3", addressee_version: 2, ship: false, lines: [{ good_id: "g1", version: 1, quantity: "6" }],
    });
  });

  it("окно упаковки печатает PL, DN и этикетки коробов, перевыпускает документы и возвращает набранное на склад", async () => {
    intakeRows = [row({ accepted: "6", packed: "4", remaining: "0", delivered: "6", tasks: [] })];
    shipments = [{ ...packedShipment, missing_data: [] }, { ...packedShipment, id: "s5", number: "ОТГ-20261029-05", status: "Собрана", pl_number: "", dn_number: "", lines: [shipmentLine({ id: "l5", picked: "2" })] }];
    const printed = capturePrintFrames();
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Принято/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Упаковка 591К.3 КБ1: упаковано 4" }));
    const dialog = await screen.findByRole("dialog", { name: "Упаковка 591К.3" });
    const card = within(within(dialog).getByRole("article", { name: "Отправка ОТГ-20261029-01" }));
    fireEvent.click(card.getByRole("button", { name: "Печать PL" }));
    await waitFor(() => expect(printed.print).toHaveBeenCalledTimes(1));
    fireEvent.click(card.getByRole("button", { name: "Печать DN" }));
    await waitFor(() => expect(printed.print).toHaveBeenCalledTimes(2));
    fireEvent.click(card.getByRole("button", { name: "Печать этикеток" }));
    await waitFor(() => expect(printed.print).toHaveBeenCalledTimes(3));
    expect(printed.html[2]).toContain("Упаковка 1/1");
    const urls = calls.map((call) => call.url);
    expect(urls).toEqual(expect.arrayContaining(["/api/shipping/shipments/s1/print?document=pl", "/api/shipping/shipments/s1/print?document=dn", "/api/shipping/shipments/s1/labels"]));
    expect(card.getByRole("button", { name: "Перевыпустить" })).toBeInTheDocument();
    const other = within(within(dialog).getByRole("article", { name: "Отправка ОТГ-20261029-05" }));
    expect(other.getByRole("button", { name: "Сформировать отгрузочные" })).toBeInTheDocument();
    expect(other.getByRole("button", { name: "Вернуть на склад" })).toBeInTheDocument();
    printed.restore();
  });

  it("«Трек…» выключен, пока нет УПД и ТК; когда можно — окно сохраняет трек правкой документов с версией", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", delivered: "4", tasks: [] })];
    shipments = [packedShipment];
    const { unmount } = render(<ProductionIntakePage principal={principal} />);
    // Держит только УПД — единственная кнопка строки «Без УПД…» открывает окно с ПИН.
    fireEvent.click(await screen.findByRole("button", { name: "Без УПД 591К.3 КБ1" }));
    expect(await screen.findByRole("dialog", { name: "Отгрузка без УПД · ОТГ-20261029-01" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Трек 591К.3 КБ1" })).not.toBeInTheDocument();
    unmount();
    // Держит ещё и другое (адрес) — выключенный «Трек…» с причиной.
    shipments = [{ ...packedShipment, missing_ship: ["№ УПД", "адрес доставки", "трек-номер"] }];
    const second = render(<ProductionIntakePage principal={principal} />);
    const off = await screen.findByRole("button", { name: "Трек 591К.3 КБ1" });
    expect(off).toBeDisabled();
    expect(off.getAttribute("title")).toContain("не хватает: № УПД, адрес доставки, трек-номер");
    expect(screen.queryByRole("button", { name: "Без УПД 591К.3 КБ1" })).not.toBeInTheDocument();
    second.unmount();
    shipments = [packedShipment];

    shipments = [{ ...packedShipment, upd_number: "У-7", missing_data: [], missing_ship: ["трек-номер"], ready_for_track: true }];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: "Трек 591К.3 КБ1" }));
    const dialog = await screen.findByRole("dialog", { name: "Трек 591К.3 КБ1" });
    fireEvent.change(within(dialog).getByLabelText("Трек-номер ОТГ-20261029-01"), { target: { value: " 1234567890 " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить трек" }));
    expect(await within(dialog).findByText("Трек сохранён: ОТГ-20261029-01 · 1234567890")).toBeInTheDocument();
    expect(calls.find((call) => call.url === "/api/shipping/shipments/s1/documents")?.body).toEqual({
      version: 3, upd_type: "ЭДО", upd_number: "У-7", upd_date: null, upd_url: "", track_number: "1234567890",
    });
  });

  it("«Отгрузить» — только когда упаковано и есть УПД, ТК и трек; «Отгружено» открывает отправки проекта", async () => {
    intakeRows = [multi()];
    shipments = [packedShipment, ready, gone];
    render(<ProductionIntakePage principal={admin} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Все/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Отгрузка 591К.3 КБ1: отгружено 3, к отгрузке 3" }));
    const dialog = await screen.findByRole("dialog", { name: "Отгрузка 591К.3" });
    const blocked = within(within(dialog).getByRole("article", { name: "Отправка ОТГ-20261029-01" }));
    expect(blocked.getByRole("button", { name: "Отгрузить" })).toBeDisabled();
    const shippable = within(within(dialog).getByRole("article", { name: "Отправка ОТГ-20261029-02" }));
    fireEvent.click(shippable.getByRole("button", { name: "Отгрузить" }));
    expect(await within(dialog).findByText(/^Отгружено: ОТГ-20261029-02\. Не закрыта — не хватает: файл УПД/)).toBeInTheDocument();
    expect(calls.find((call) => call.url === "/api/shipping/shipments/s2/ship")?.body).toEqual({ version: 3 });
    // Уехавшая: «Закрыть» и откат «Отгружено» администратором.
    const shipped = within(within(dialog).getByRole("article", { name: "Отправка ОТГ-20261029-03" }));
    expect(shipped.getByRole("button", { name: "Закрыть" })).toBeInTheDocument();
    fireEvent.click(shipped.getByRole("button", { name: "Откатить отгрузку" }));
    fireEvent.change(shipped.getByLabelText("Причина отката ОТГ-20261029-03"), { target: { value: "по ошибке" } });
    fireEvent.click(shipped.getByRole("button", { name: "Откатить в «Упакована»" }));
    await waitFor(() => expect(calls.find((call) => call.url === "/api/shipping/shipments/s3/unship")?.body).toEqual({ version: 3, reason: "по ошибке" }));
  });

  it("без УПД: уполномоченный выбирает себя и вводит ПИН; неверный ПИН и блокировку объясняет сервер", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", delivered: "4", tasks: [] })];
    shipments = [{ ...packedShipment, missing_ship: ["№ УПД", "файл УПД", "трек-номер"] }];
    approvals = [[403, { detail: "Неверный PIN." }], [429, { detail: "Слишком много попыток. Подождите 10 минут." }], [200, { shipment: packedShipment }]];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Все/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Отгрузка 591К.3 КБ1: отгружено 0" }));
    const ships = await screen.findByRole("dialog", { name: "Отгрузка 591К.3" });
    fireEvent.click(within(ships).getByRole("button", { name: "Разрешить без УПД" }));
    const pinWindow = await screen.findByRole("dialog", { name: "Отгрузка без УПД · ОТГ-20261029-01" });
    const who = within(pinWindow).getByLabelText("Кто разрешает");
    await waitFor(() => expect(within(who).getAllByRole("option").map((option) => option.textContent)).toEqual(["выберите", "Артемий", "Зингер", "Касаточкин"]));
    const pin = within(pinWindow).getByLabelText("ПИН");
    expect(pin).toHaveAttribute("type", "password");
    const submit = within(pinWindow).getByRole("button", { name: "Разрешить отгрузку без УПД" });
    expect(submit).toBeDisabled();
    fireEvent.change(who, { target: { value: "p2" } });
    fireEvent.change(pin, { target: { value: "1111" } });
    fireEvent.click(submit);
    expect(await within(pinWindow).findByText("Неверный PIN.")).toBeInTheDocument();
    expect(pin).toHaveValue("");
    fireEvent.change(pin, { target: { value: "2222" } });
    fireEvent.click(submit);
    expect(await within(pinWindow).findByText("Слишком много попыток. Подождите 10 минут.")).toBeInTheDocument();
    fireEvent.change(pin, { target: { value: "3333" } });
    fireEvent.click(submit);
    expect(await within(ships).findByText("Отгрузка без УПД разрешена: ОТГ-20261029-01 — Зингер")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: /Отгрузка без УПД/ })).not.toBeInTheDocument();
    const posts = calls.filter((call) => call.url.endsWith("/approve-no-upd"));
    expect(posts.map((call) => call.body)).toEqual([1111, 2222, 3333].map((value) => ({ version: 3, approver_id: "p2", pin: String(value) })));
  });

  it("отправка «Без УПД» ждёт разрешения: сервер пишет «разрешение на отгрузку без УПД» — кнопка есть, «Отгрузить» выключена", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", delivered: "4", tasks: [] })];
    shipments = [{ ...packedShipment, upd_type: "БЕЗ УПД", terms: "САМОВЫВОЗ", carrier: "", missing_data: [], missing_ship: ["разрешение на отгрузку без УПД"] }];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Все/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Отгрузка 591К.3 КБ1: отгружено 0" }));
    const ships = await screen.findByRole("dialog", { name: "Отгрузка 591К.3" });
    expect(within(ships).getByRole("button", { name: "Отгрузить" })).toBeDisabled();
    expect(within(ships).getByRole("button", { name: "Разрешить без УПД" })).toBeInTheDocument();
  });

  it("собранную отправку самовывоза с набранным можно отгрузить, когда сервер говорит «готова»", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", delivered: "4", tasks: [] })];
    shipments = [{ ...packedShipment, status: "Собрана", terms: "САМОВЫВОЗ", carrier: "", pl_number: "", dn_number: "", missing_data: [], missing_ship: [], ready_to_ship: true }];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Все/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Отгрузка 591К.3 КБ1: отгружено 0" }));
    const ships = await screen.findByRole("dialog", { name: "Отгрузка 591К.3" });
    expect(within(ships).getByRole("button", { name: "Отгрузить" })).toBeEnabled();
  });

  it("«Разрешить без УПД» не предлагается, когда держит не только УПД", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", delivered: "4", tasks: [] })];
    shipments = [{ ...packedShipment, missing_ship: ["№ УПД", "адрес доставки"] }];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Все/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Отгрузка 591К.3 КБ1: отгружено 0" }));
    const ships = await screen.findByRole("dialog", { name: "Отгрузка 591К.3" });
    expect(within(ships).queryByRole("button", { name: "Разрешить без УПД" })).not.toBeInTheDocument();
  });

  it("у каждой вкладки свои колонки и одно своё действие; чужих действий нет", async () => {
    intakeRows = [multi()];
    shipments = [packedShipment, ready, gone];
    render(<ProductionIntakePage principal={admin} />);
    await screen.findByText("Жгут");
    const headers = () => screen.getAllByRole("columnheader").map((cell) => (cell.textContent ?? "").replace(/[^А-Яа-яЁё ]/g, "").trim());
    const lineButtons = () => [...document.querySelectorAll("tbody tr")].filter((tr) => tr.textContent?.includes("Жгут") && !tr.textContent.includes("Данные отправки"))
      .flatMap((tr) => [...tr.querySelectorAll("button")].map((button) => button.getAttribute("aria-label") ?? button.textContent ?? ""));
    const expected: Array<[RegExp, string[], RegExp | null]> = [
      [/^В ожидании/, ["КБ", "Наименование", "Заказано"], null],
      [/^В производстве/, ["КБ", "Наименование", "Заказано", "Запущено", "Остаток", "Задания"], null],
      [/^Готово/, ["КБ", "Наименование", "Заказано", "Можно принять", "Задания"], /^Принять 591К\.3 КБ1/],
      [/^Принято/, ["КБ", "Наименование", "Принято", "Дата приёмки", "ГРН", "Упаковано"], /^Упаковать|^Упаковка|^Печать этикетки/],
      [/^Упаковано/, ["КБ", "Наименование", "Упаковано", "Трек"], /^(Трек|Без УПД) 591К\.3 КБ1/],
      [/^На отгрузку/, ["КБ", "Наименование", "Упаковано", "Трек", "Отгрузка"], /^Отгрузить 591К\.3 КБ1/],
      [/^Отгружено/, ["КБ", "Наименование", "Отгружено", "Трек", "Дата отгрузки"], null],
    ];
    for (const [name, heads, action] of expected) {
      fireEvent.click(tab(name));
      if (name.source === "^В ожидании") { await screen.findByText("В ожидании производства ничего нет."); continue; }
      await waitFor(() => expect(headers()).toEqual(heads));
      const buttons = lineButtons();
      if (action) { expect(buttons.length).toBeGreaterThan(0); for (const label of buttons) expect(label).toMatch(action); }
      else expect(buttons).toEqual([]);
    }
    // «Принять всё» — только в «Готово» и «Все»; «Принять» не бывает под «Отгружено» и «Упаковано».
    fireEvent.click(tab(/^Все/));
    await waitFor(() => expect(headers()).toContain("Можно принять"));
  });

  it("журнал, «+ Позиция ГП» и «+ Заявка на отгрузку» — окнами из шапки", async () => {
    shipments = [gone];
    render(<ProductionIntakePage principal={admin} />);
    await screen.findByText("Жгут");
    fireEvent.click(screen.getByRole("button", { name: "Журнал" }));
    const journal = await screen.findByRole("dialog", { name: "Журнал отгрузок" });
    expect(within(journal).getByText("PL591К.3-1")).toBeInTheDocument();
    fireEvent.click(within(journal).getByRole("button", { name: "Закрыть" }));
    fireEvent.click(screen.getByRole("button", { name: "+ Позиция ГП" }));
    const good = await screen.findByRole("dialog", { name: "Позиция готовой продукции" });
    fireEvent.click(within(good).getByRole("button", { name: "Закрыть" }));
    fireEvent.click(screen.getByRole("button", { name: "+ Заявка на отгрузку" }));
    expect(await screen.findByRole("dialog", { name: "Заявка на отгрузку" })).toBeInTheDocument();
  });

  it("ссылка `?tab=journal` (бывшая «Отгрузка → Журнал») открывает журнал; без права на правку — числа без кнопок", async () => {
    window.history.replaceState({}, "", "/?section=intake&tab=journal");
    intakeRows = [multi()];
    shipments = [packedShipment, ready, gone];
    render(<ProductionIntakePage principal={viewer} />);
    expect(await screen.findByRole("dialog", { name: "Журнал отгрузок" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^(Упаковать|Упаковка|Отгрузка|Трек) 591К/ })).not.toBeInTheDocument();
    window.history.replaceState({}, "", "/");
  });
});
