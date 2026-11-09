import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProductionIntakePage } from "./ProductionIntakePage";
import { isoDay } from "./Gantt";
import { capturePrintFrames } from "./testPrintFrames";
import { setRightsPrincipal } from "./rights";
import { forgetPersonalViews } from "./personalViews";

const principal = {
  user: { id: "u1", email: "k@example.test", name: "Кладовщик", department: "Склад" },
  roles: ["foreman"], access_level: "foreman", permissions: { shipping_goods: "edit" as const },
};
const viewer = { ...principal, permissions: { shipping: "view" as const } };
const admin = { ...principal, roles: ["admin"], access_level: "admin", permissions: { shipping: "edit" as const, shipping_goods: "edit" as const } };
const deliveryHeaders = ["Получатель", "Способ доставки", "Перевозчик", "Адрес", "Телефон", "Комментарий для склада"];
function showColumns(names: string[]) {
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  for (const name of names) {
    const checkbox = screen.getByLabelText(name) as HTMLInputElement;
    if (!checkbox.checked) fireEvent.click(checkbox);
  }
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
}

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
const act = (grn: string, quantity: string, received_at = "2026-10-30") => ({ grn, quantity, received_at, location: "" });

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
let addressees: unknown[] = [];
/** Ответы сервера на «Разрешить без УПД» по очереди: [status, body]. */
let approvals: Array<[number, unknown]> = [];

function stub() {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input), method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url === "/api/shipping/intake") return json({ rows: intakeRows });
    if (url === "/api/shipping/shipments" && method === "GET") return json({ shipments });
    if (url === "/api/shipping/project-addressees") return json({ addressees });
    if (url === "/api/shipping/finished-goods/accept") return json({ goods: [], replayed: false });
    if (url.startsWith("/api/shipping/intake/") && url.endsWith("/label")) return json({ status: "queued", reason: "В очереди принтера.", file: "j1", copies: 1, job_id: "j1" });
    if (url.startsWith("/api/shipping/project-addressee?")) return json({ addressee: dispatchCard });
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
beforeEach(() => { window.localStorage.clear(); forgetPersonalViews(); intakeRows = ROWS; shipments = []; addressees = []; approvals = []; setRightsPrincipal(null); stub(); });
afterEach(() => { cleanup(); setRightsPrincipal(null); vi.restoreAllMocks(); vi.unstubAllGlobals(); window.history.replaceState({}, "", "/"); });

const tab = (name: RegExp) => screen.getByRole("tab", { name });
const count = (name: RegExp) => tab(name).querySelector(".page-count")?.textContent?.trim();
const headers = () => screen.getAllByRole("columnheader").map((cell) => (cell.textContent ?? "").replace(/[^А-Яа-яЁёA-Za-z /]/g, "").trim()).filter(Boolean);
const bodyRows = (text = "Жгут") => [...document.querySelectorAll("tbody tr")].filter((tr) => tr.querySelector(".intake-kb") && tr.textContent?.includes(text));
const rowButtons = (text = "Жгут") => bodyRows(text).flatMap((tr) => [...tr.querySelectorAll("button")].map((button) => button.getAttribute("aria-label") ?? button.textContent ?? ""));
const cell = (tr: Element, label: string) => (tr.querySelector(`td[data-label="${label}"]`)?.textContent ?? "").replace(/ /g, " ");
const writes = () => calls.filter((call) => call.method !== "GET");

describe("Приёмка с производства: свои колонки и одно действие у каждой вкладки (аудит 06.11)", () => {
  const today = isoDay(new Date());
  // 20 не принято: 6 сдано цехом («Готово»), 14 ещё в работе; принято 20, набрано 10 — 10 на складе; 4 упаковано, 3 к отгрузке, 3 уехало.
  const multi = () => row({ needed: "40", delivered: "26", accepted: "20", packed: "10", sent: "3", remaining: "20", pack_decided: true, pack_permission_remaining: "10000", acts: [] });
  const ready = { ...packedShipment, id: "s2", number: "ОТГ-20261029-02", ready_to_ship: true, ready_for_track: true, track_number: "TRK-1", missing_ship: [], missing_data: [], lines: [shipmentLine({ id: "l2", picked: "3", requested: "3" })] };
  const gone = { ...packedShipment, id: "s3", number: "ОТГ-20261029-03", status: "Отгружена", shipped_at: today, track_number: "TRK-0", missing_ship: [], missing_data: [], lines: [shipmentLine({ id: "l3", picked: "3", requested: "3", grn: "ГП.26.0000003" })] };

  it("у каждой вкладки свои колонки и ровно одно своё действие; кнопка — со штуками своей строки", async () => {
    intakeRows = [multi()];
    shipments = [packedShipment, ready, gone];
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    expect(tab(/^Готово/)).toHaveAttribute("aria-selected", "true");
    expect([/^В ожидании/, /^В производстве/, /^Готово/, /^Принято/, /^Упаковано/, /^На отгрузку/, /^Отгружено/, /^Все/].map(count))
      .toEqual(["0", "1", "1", "1", "1", "1", "1", "1"]);
    const expected: Array<[RegExp, string[], string[]]> = [
      [/^В производстве/, ["КБ", "Наименование", "Заказано", "Запущено", "Остаток", "Задания", "Принять досрочно"], ["Принять досрочно 591К.3 КБ1: 14"]],
      [/^Готово/, ["КБ", "Наименование", "Заказано", "Задания", "Можно принять"], ["Принять 591К.3 КБ1: 6"]],
      [/^Принято/, ["КБ", "Наименование", "ГРН", "Дата приёмки", "Количество", "Упаковать"], ["Упаковать 591К.3 КБ1: 10"]],
      [/^Упаковано/, ["КБ", "Наименование", "Отправка", "Упаковано", "Трек"], ["Трек 591К.3 КБ1"]],
      [/^На отгрузку/, ["КБ", "Наименование", "Отправка", "Упаковано", "Трек", "Отгрузка"], ["Отгрузить 591К.3 КБ1"]],
      [/^Отгружено/, ["КБ", "Наименование", "Отправка", "Отгружено", "Трек", "Дата отгрузки", "ГРН партии"], []],
    ];
    for (const [name, heads, buttons] of expected) {
      fireEvent.click(tab(name));
      await waitFor(() => expect(headers()).toEqual(heads));
      expect(rowButtons()).toEqual(buttons);
    }
    fireEvent.click(tab(/^Принято/));
    expect(cell(bodyRows()[0], "Количество")).toBe("10 шт");
    expect(screen.getByRole("button", { name: "Упаковать 591К.3 КБ1: 10" })).toHaveTextContent("Упаковать · 10");
    fireEvent.click(tab(/^Отгружено/));
    expect(cell(bodyRows()[0], "ГРН партии")).toBe("ГП.26.0000003");
    expect(cell(bodyRows()[0], "Трек")).toBe("TRK-0");
    fireEvent.click(tab(/^В ожидании/));
    expect(await screen.findByText("В ожидании производства ничего нет.")).toBeInTheDocument();
    fireEvent.click(tab(/^Все/));
    await waitFor(() => expect(headers()).toEqual(["КБ", "Наименование", "Этап", "Количество", "Заказано", "Запущено", "Остаток", "ГРН", "Дата приёмки",
      "Отправка", "Трек", "Дата отгрузки", "Действие"]));
    expect(rowButtons()).toEqual(["Принять досрочно 591К.3 КБ1: 14", "Принять 591К.3 КБ1: 6", "Упаковать 591К.3 КБ1: 10", "Трек 591К.3 КБ1", "Отгрузить 591К.3 КБ1"]);
    expect(screen.queryByText(/Счётчики:/)).not.toBeInTheDocument();
    expect(writes()).toEqual([]);
  });

  it("«Принято»: строка на каждый акт приёмки со своим ГРН, датой и штуками; набранное уходит со старых актов", async () => {
    intakeRows = [
      row({ needed: "1000", accepted: "973", packed: "100", remaining: "27", pack_decided: true, pack_permission_remaining: "450",
        acts: [act("ГП.26.0000200", "500", "2026-10-28"), act("ГП.26.0000201", "473", "2026-10-30")] }),
      row({ item_index: "КБ2", name: "Жгут три партии", needed: "500", accepted: "500", packed: "286", sent: "286", remaining: "0", pack_allowed: true, pack_permission_remaining: "214",
        acts: [act("ГП.ДЕМО.0", "100"), act("ГП.ДЕМО.1", "186"), act("ГП.ДЕМО.2", "214", "2026-11-03")] }),
    ];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Принято/ }));
    expect(count(/^Принято/)).toBe("2");
    const kb1 = bodyRows("КБ1");
    expect(kb1.map((tr) => [cell(tr, "ГРН"), cell(tr, "Дата приёмки"), cell(tr, "Количество")])).toEqual([
      ["ГП.26.0000200Печать", "28.10.2026", "400 шт"], ["ГП.26.0000201Печать", "30.10.2026", "473 шт"]]);
    // Разрешение КБ (450) делится по строкам: старый акт — первым; сумма кнопок не больше разрешения.
    expect(rowButtons("КБ1").filter((name) => name.startsWith("Упаковать"))).toEqual(["Упаковать 591К.3 КБ1: 400", "Упаковать 591К.3 КБ1: 50"]);
    const kb2 = bodyRows("Жгут три партии");
    expect(kb2.map((tr) => [cell(tr, "ГРН"), cell(tr, "Количество")])).toEqual([["ГП.ДЕМО.2Печать", "214 шт"]]);
    expect(rowButtons("Жгут три партии")).toEqual(["Печать этикетки ГП.ДЕМО.2", "Упаковать 591К.3 КБ2: 214"]);
    fireEvent.click(screen.getByRole("button", { name: "Печать этикетки ГП.ДЕМО.2" }));
    expect(await screen.findByText("В очереди принтера.")).toBeInTheDocument();
    expect(calls.find((call) => call.url.endsWith("/label"))).toMatchObject({ method: "POST", body: { copies: 1 }, url: `/api/shipping/intake/${encodeURIComponent("ГП.ДЕМО.2")}/label` });
  });

  it("строка отправки, которая называет свои партии (`grns`), списывает именно их", async () => {
    intakeRows = [row({ needed: "30", accepted: "30", packed: "10", remaining: "0", pack_decided: true, pack_permission_remaining: "100",
      acts: [act("ГП.СТАРЫЙ", "10", "2026-10-01"), act("ГП.НОВЫЙ", "20", "2026-10-20")] })];
    shipments = [{ ...packedShipment, lines: [shipmentLine({ picked: "10", requested: "10", grn: "ГП.НОВЫЙ", grns: ["ГП.НОВЫЙ"] })] }];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Принято/ }));
    expect(bodyRows().map((tr) => [cell(tr, "ГРН"), cell(tr, "Количество")])).toEqual([["ГП.СТАРЫЙПечать", "10 шт"], ["ГП.НОВЫЙПечать", "10 шт"]]);
  });

  it("набранное в отправку и остаток на складе — каждый со своими штуками, без «Упаковать · 20» на обоих", async () => {
    intakeRows = [row({ needed: "20", accepted: "20", packed: "5", remaining: "0", pack_decided: true, pack_permission_remaining: "100", acts: [act("ГП.26.0000500", "20")] })];
    shipments = [{ ...packedShipment, status: "Собрана", pl_number: "", dn_number: "", lines: [shipmentLine({ picked: "5", requested: "5", grn: "ГП.26.0000500" })] }];
    render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findByRole("tab", { name: /^Принято/ })).toHaveAttribute("aria-selected", "true");
    expect(bodyRows().map((tr) => cell(tr, "Количество"))).toEqual(["15 шт", "5 шт"]);
    expect(rowButtons().filter((name) => name.startsWith("Упаковать"))).toEqual(["Упаковать 591К.3 КБ1: 15", "Упаковать 591К.3 КБ1: 5"]);
    expect(screen.queryByRole("button", { name: /Упаковать.*20/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Упаковать 591К.3 КБ1: 5" }));
    const dialog = await screen.findByRole("dialog", { name: "Упаковка 591К.3" });
    expect(within(dialog).getByRole("article", { name: "Отправка ОТГ-20261029-01" })).toBeInTheDocument();
  });

  it("без разрешения — «Нужно разрешение» без кнопки; с разрешением — окно упаковки, количество вводят явно", async () => {
    intakeRows = [row({ accepted: "6", remaining: "0", delivered: "6", tasks: [] })];
    const { unmount } = render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Принято/ }));
    expect(await screen.findByText("Нужно разрешение")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Упаковать / })).not.toBeInTheDocument();
    unmount();

    intakeRows = [row({ accepted: "6", remaining: "0", delivered: "6", tasks: [], pack_allowed: true, pack_permission_remaining: "10000", acts: [act("ГП.26.0000001", "6", today)] })];
    render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findByRole("tab", { name: /^Принято/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(await screen.findByRole("button", { name: "Упаковать 591К.3 КБ1: 6" }));
    const dialog = await screen.findByRole("dialog", { name: "Упаковка 591К.3" });
    fireEvent.change(within(dialog).getByLabelText("Сколько упаковать КБ1"), { target: { value: "6" } });
    const pack = within(dialog).getByRole("button", { name: "Упаковать · 6 шт" });
    await waitFor(() => expect(pack).toBeEnabled());
    fireEvent.click(pack);
    expect(await within(dialog).findByText("Упаковано: ОТГ-9 · PL591К.3-9")).toBeInTheDocument();
    expect(calls.find((call) => call.url === "/api/shipping/project-ship")?.body).toEqual({
      project: "591К.3", addressee_version: 2, ship: false, lines: [{ good_id: "g1", version: 1, quantity: "6" }],
    });
  });

  it("«Готово»: «Принять · 6» предлагает штуки строки; «В производстве» — «Принять досрочно» с предупреждением", async () => {
    intakeRows = [multi()];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: "Принять 591К.3 КБ1: 6" }));
    let dialog = await screen.findByRole("dialog", { name: "Приёмка 591К.3 КБ1" });
    expect(within(dialog).getByLabelText("Сколько принять 591К.3 КБ1")).toHaveValue(6);
    fireEvent.change(within(dialog).getByLabelText("Сколько принять 591К.3 КБ1"), { target: { value: "21" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Принять на склад · 21 шт" }));
    expect(await within(dialog).findByText("больше сданного: можно 20")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Закрыть" }));

    fireEvent.click(tab(/^В производстве/));
    const early = await screen.findByRole("button", { name: "Принять досрочно 591К.3 КБ1: 14" });
    expect(early).toHaveAttribute("title", "Ещё в производстве — принять досрочно");
    expect(early).toHaveClass("intake-early");
    expect(screen.queryByRole("button", { name: /Принять всё/ })).not.toBeInTheDocument();
    fireEvent.click(early);
    dialog = await screen.findByRole("dialog", { name: "Приёмка 591К.3 КБ1" });
    expect(within(dialog).getByLabelText("Сколько принять 591К.3 КБ1")).toHaveValue(14);
    fireEvent.change(within(dialog).getByLabelText("Дата приёмки"), { target: { value: "2026-10-12" } });
    fireEvent.change(within(dialog).getByLabelText("Место хранения 591К.3 КБ1"), { target: { value: "Стеллаж 3" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Принять на склад · 14 шт" }));
    await screen.findByText("Принято на склад: 14 шт");
    expect(calls.find((call) => call.method === "POST")!.body).toEqual({ lines: [{ project: "591К.3", item_index: "КБ1", quantity: "14", location: "Стеллаж 3" }], received_at: "2026-10-12" });
  });

  it("«Принять всё» — только в «Готово» (готовое) и «Все» (всё, что можно принять)", async () => {
    intakeRows = [row({ needed: "100", accepted: "0", delivered: "20", remaining: "100" }),
      row({ item_index: "КБ2", name: "Кабель", needed: "10", accepted: "0", delivered: "10", remaining: "10" })];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: "Принять всё: 2 КБ · 30 шт" }));
    let dialog = await screen.findByRole("dialog", { name: "Принять всё 591К.3" });
    expect(within(dialog).getByLabelText("Сколько принять 591К.3 КБ1")).toHaveValue(20);
    expect(within(dialog).getByLabelText("Сколько принять 591К.3 КБ2")).toHaveValue(10);
    fireEvent.click(within(dialog).getByRole("button", { name: "Закрыть" }));
    fireEvent.click(tab(/^В производстве/));
    await waitFor(() => expect(headers()).toContain("Принять досрочно"));
    expect(screen.queryByRole("button", { name: /Принять всё/ })).not.toBeInTheDocument();
    fireEvent.click(tab(/^Все/));
    fireEvent.click(await screen.findByRole("button", { name: "Принять всё: 2 КБ · 110 шт" }));
    dialog = await screen.findByRole("dialog", { name: "Принять всё 591К.3" });
    expect(within(dialog).getAllByLabelText("Сколько принять 591К.3 КБ1")).toHaveLength(1);
    fireEvent.click(within(dialog).getByRole("button", { name: "Принять на склад · 110 шт" }));
    await screen.findByText("Принято на склад: 110 шт");
    expect(calls.find((call) => call.method === "POST")!.body).toMatchObject({ lines: [
      { project: "591К.3", item_index: "КБ1", quantity: "100" }, { project: "591К.3", item_index: "КБ2", quantity: "10" },
    ] });
  });

  it("по умолчанию — первая вкладка с настоящим действием склада; «В ожидании» — только когда больше ничего нет", async () => {
    intakeRows = [row({ delivered: "0", accepted: "10", remaining: "30", pack_decided: true, pack_permission_remaining: "10" })];
    const first = render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findByRole("tab", { name: /^Принято/ })).toHaveAttribute("aria-selected", "true");
    first.unmount();
    intakeRows = [row({ item_index: "КБ2", name: "Жгут новый", stage: "Новый", project_stage: "Новый", launched: "0", delivered: "0", accepted: "0", remaining: "0", tasks: [] })];
    render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findByText("Жгут новый")).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((item) => item.textContent?.replace(/\s*\d+$/, ""))).toEqual(
      ["В ожидании", "В производстве", "Готово", "Принято", "Упаковано", "На отгрузку", "Отгружено", "Все"]);
    expect(tab(/^В ожидании/)).toHaveAttribute("aria-selected", "true");
    expect(headers()).toEqual(["КБ", "Наименование", "Заказано"]);
    expect(rowButtons()).toEqual([]);
  });

  it.each(["Готово", "ОТК", "ОТК пройден"])("КБ на этапе «%s» — во вкладке «Готово», не «В производстве»", async (stage) => {
    intakeRows = [row({ stage, project_stage: stage, delivered: "0", accepted: "6", remaining: "34", tasks: [] })];
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    expect(tab(/^Готово/)).toHaveAttribute("aria-selected", "true");
    expect([/^В производстве/, /^Готово/].map(count)).toEqual(["0", "1"]);
    expect(rowButtons()).toEqual(["Принять 591К.3 КБ1: 34"]);
  });

  it("«Упаковано»: ЭДО без номера — выключенный «Трек…» с причиной, без «Без УПД…»; УПД нет совсем — «Без УПД…» с ПИН", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", delivered: "4", tasks: [] })];
    shipments = [packedShipment];
    const first = render(<ProductionIntakePage principal={principal} />);
    const off = await screen.findByRole("button", { name: "Трек 591К.3 КБ1" });
    expect(tab(/^Упаковано/)).toHaveAttribute("aria-selected", "true");
    expect(off).toBeDisabled();
    expect(off.getAttribute("title")).toContain("не хватает: № УПД");
    expect(screen.queryByRole("button", { name: "Без УПД 591К.3 КБ1" })).not.toBeInTheDocument();
    expect(rowButtons()).toEqual(["Трек 591К.3 КБ1"]);
    first.unmount();

    // Сервер решает сам (`no_upd_offered`): ЭДО без номера — не предлагает, даже если слова пробелов похожи.
    shipments = [{ ...packedShipment, missing_ship_codes: ["upd_number", "track_number"], no_upd_offered: false }];
    const second = render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findByRole("button", { name: "Трек 591К.3 КБ1" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Без УПД 591К.3 КБ1" })).not.toBeInTheDocument();
    second.unmount();

    shipments = [{ ...packedShipment, upd_type: null, missing_ship: ["тип УПД", "трек-номер"], missing_ship_codes: ["upd_type", "track_number"], no_upd_offered: true }];
    approvals = [[403, { detail: "Неверный разрешительный ПИН." }], [200, { shipment: packedShipment }]];
    render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findByRole("button", { name: "Без УПД 591К.3 КБ1" })).toBeInTheDocument();
    expect(rowButtons()).toEqual(["Без УПД 591К.3 КБ1"]);
    fireEvent.click(screen.getByRole("button", { name: "Без УПД 591К.3 КБ1" }));
    const pinWindow = await screen.findByRole("dialog", { name: "Отгрузка без УПД · ОТГ-20261029-01" });
    const who = within(pinWindow).getByLabelText("Кто разрешает");
    await waitFor(() => expect(within(who).getAllByRole("option").map((option) => option.textContent)).toEqual(["выберите", "Артемий", "Зингер", "Касаточкин"]));
    fireEvent.change(who, { target: { value: "p2" } });
    fireEvent.change(within(pinWindow).getByLabelText("Разрешительный ПИН"), { target: { value: "1111" } });
    fireEvent.click(within(pinWindow).getByRole("button", { name: "Разрешить отгрузку без УПД" }));
    expect(await within(pinWindow).findByText("Неверный разрешительный ПИН.")).toBeInTheDocument();
    fireEvent.change(within(pinWindow).getByLabelText("Разрешительный ПИН"), { target: { value: "3333" } });
    fireEvent.click(within(pinWindow).getByRole("button", { name: "Разрешить отгрузку без УПД" }));
    await waitFor(() => expect(calls.filter((call) => call.url.endsWith("/approve-no-upd")).map((call) => call.body))
      .toEqual([{ version: 3, approver_id: "p2", pin: "1111" }, { version: 3, approver_id: "p2", pin: "3333" }]));
  });

  it("«Трек…» привязан к своей отправке и сохраняет трек правкой документов с версией", async () => {
    intakeRows = [row({ accepted: "8", packed: "8", remaining: "0", delivered: "8" })];
    shipments = [packedShipment, { ...packedShipment, id: "s2", number: "ОТГ-20261029-02" }].map((shipment) => ({
      ...shipment, upd_number: "У-7", missing_data: [], missing_ship: ["трек-номер"], ready_for_track: true,
    }));
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("ОТГ-20261029-02");
    const party = bodyRows().find((tr) => tr.textContent?.includes("ОТГ-20261029-02"))!;
    fireEvent.click(within(party as HTMLElement).getByRole("button", { name: "Трек 591К.3 КБ1" }));
    const dialog = await screen.findByRole("dialog", { name: "Трек 591К.3 КБ1" });
    expect(within(dialog).queryByLabelText("Трек-номер ОТГ-20261029-01")).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Трек-номер ОТГ-20261029-02"), { target: { value: " 1234567890 " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить трек" }));
    expect(await within(dialog).findByText("Трек сохранён: ОТГ-20261029-02 · 1234567890")).toBeInTheDocument();
    expect(calls.find((call) => call.url === "/api/shipping/shipments/s2/documents")?.body).toEqual({
      version: 3, upd_type: "ЭДО", upd_number: "У-7", upd_date: null, upd_url: "", track_number: "1234567890",
    });
  });

  it("короба, PL/DN и этикетки — в окне упаковки из заголовка проекта, не кнопками в строке", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", delivered: "4", tasks: [] })];
    shipments = [{ ...packedShipment, missing_data: [] }];
    const printed = capturePrintFrames();
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByRole("button", { name: "Трек 591К.3 КБ1" });
    expect(screen.queryByRole("button", { name: /^(Документы|Сформировать отгрузочные|Печать PL|Печать DN) / })).not.toBeInTheDocument();
    showColumns(["PL/DN"]);
    expect(cell(bodyRows()[0], "PL/DN")).toBe("PL591К.3-1 · DN591К.3-1");
    fireEvent.click(screen.getByRole("button", { name: "Короба и PL/DN" }));
    const dialog = await screen.findByRole("dialog", { name: "Упаковка 591К.3" });
    const card = within(within(dialog).getByRole("article", { name: "Отправка ОТГ-20261029-01" }));
    fireEvent.click(card.getByRole("button", { name: "Печать PL" }));
    await waitFor(() => expect(printed.print).toHaveBeenCalledTimes(1));
    fireEvent.click(card.getByRole("button", { name: "Печать этикеток" }));
    await waitFor(() => expect(printed.print).toHaveBeenCalledTimes(2));
    expect(card.getByRole("button", { name: "Перевыпустить" })).toBeInTheDocument();
    printed.restore();
  });

  it("данные отправки складу — только для чтения: колонки, карточка проекта и карточка отправки без правки", async () => {
    intakeRows = [row({ accepted: "20", packed: "8", remaining: "0", tasks: [] })];
    addressees = [{ ...dispatchCard, receiver: "Общий получатель", note: "Склад проекта" }];
    shipments = [
      { ...packedShipment, receiver: "Получатель А", shipping_point: "Адрес А", phone: "+7 А", track_number: "TRK-A", note: "На рампу А", lines: [shipmentLine({ picked: "4" })] },
      { ...packedShipment, id: "s2", number: "ОТГ-2", receiver: "Получатель Б", shipping_point: "Адрес Б", carrier: "Яндекс Доставка", phone: "+7 Б", track_number: "TRK-B", note: "На рампу Б", lines: [shipmentLine({ id: "l2", picked: "4" })] },
    ];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    showColumns([...deliveryHeaders, "УПД"]);
    const [a, b] = ["ОТГ-20261029-01", "ОТГ-2"].map((number) => bodyRows().find((tr) => cell(tr, "Отправка") === number)!);
    expect([cell(a, "Получатель"), cell(a, "Адрес"), cell(a, "Перевозчик"), cell(a, "Комментарий для склада")]).toEqual(["Получатель А", "Адрес А", "СДЭК", "На рампу А"]);
    expect(cell(a, "Трек")).toContain("TRK-A");
    expect([cell(b, "Получатель"), cell(b, "Адрес"), cell(b, "Перевозчик")]).toEqual(["Получатель Б", "Адрес Б", "Яндекс Доставка"]);
    expect(cell(a, "УПД")).toBe("УПД · ЭДО");
    expect(screen.queryByText("Общий получатель")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^(Внести УПД|Данные отправки ОТГ)/ })).not.toBeInTheDocument();

    fireEvent.click(tab(/^Принято/));
    fireEvent.click(await screen.findByRole("button", { name: "Данные отправки" }));
    const card = await screen.findByRole("dialog", { name: "Данные отправки 591К.3" });
    expect(await within(card).findByLabelText("Получатель")).toBeDisabled();
    expect(within(card).queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
    fireEvent.click(within(card).getByRole("button", { name: "Закрыть" }));

    fireEvent.click(tab(/^Упаковано/));
    fireEvent.click(await screen.findByRole("button", { name: "Короба и PL/DN" }));
    const pack = await screen.findByRole("dialog", { name: "Упаковка 591К.3" });
    const shipCard = within(within(pack).getByRole("article", { name: "Отправка ОТГ-20261029-01" }));
    expect(shipCard.queryByRole("button", { name: "Сохранить документы" })).not.toBeInTheDocument();
    expect(shipCard.queryByLabelText("Загрузить УПД ОТГ-20261029-01")).not.toBeInTheDocument();
    expect(shipCard.queryByText("Получатель и адрес этой отправки")).not.toBeInTheDocument();
    fireEvent.click(shipCard.getByRole("button", { name: "Данные отправки" }));
    const data = await screen.findByRole("dialog", { name: "Данные отправки ОТГ-20261029-01" });
    expect(within(data).queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
    expect(writes()).toEqual([]);
  });

  it("«На отгрузку»: «Отгрузить» открывает отправки проекта; отгрузка — по готовой отправке", async () => {
    intakeRows = [multi()];
    shipments = [packedShipment, ready, gone];
    render(<ProductionIntakePage principal={admin} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^На отгрузку/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Отгрузить 591К.3 КБ1" }));
    const dialog = await screen.findByRole("dialog", { name: "Отгрузка 591К.3" });
    expect(within(within(dialog).getByRole("article", { name: "Отправка ОТГ-20261029-01" })).getByRole("button", { name: "Отгрузить" })).toBeDisabled();
    fireEvent.click(within(within(dialog).getByRole("article", { name: "Отправка ОТГ-20261029-02" })).getByRole("button", { name: "Отгрузить" }));
    expect(await within(dialog).findByText(/^Отгружено: ОТГ-20261029-02\. Не закрыта — не хватает: файл УПД/)).toBeInTheDocument();
    expect(calls.find((call) => call.url === "/api/shipping/shipments/s2/ship")?.body).toEqual({ version: 3 });
  });

  it("уехавшая целиком КБ видна в «Отгружено» из отправок за 30 дней — без кнопок", async () => {
    intakeRows = [];
    shipments = [{ ...gone, lines: [shipmentLine({ id: "l9", item_index: "КБ9", name: "Уехавший жгут", picked: "5", requested: "5" })] }];
    render(<ProductionIntakePage principal={admin} />);
    expect(await screen.findByText("Уехавший жгут")).toBeInTheDocument();
    expect(tab(/^Отгружено/)).toHaveAttribute("aria-selected", "true");
    const line = bodyRows("Уехавший жгут")[0];
    expect(line.querySelectorAll("button")).toHaveLength(0);
    expect([cell(line, "Отгружено"), cell(line, "Отправка"), cell(line, "ГРН партии")]).toEqual(["5 шт", "ОТГ-20261029-03", "ГП.26.0000001"]);
  });

  it("одинаковые КБ в одной отправке не склеиваются, порядок КБ — естественный", async () => {
    intakeRows = [];
    shipments = [{ ...packedShipment, status: "Отгружена", shipped_at: today, lines: [
      shipmentLine({ id: "ten", item_index: "КБ10", picked: "10" }), shipmentLine({ id: "two-a", item_index: "КБ2", picked: "1000" }),
      shipmentLine({ id: "one", item_index: "КБ1", picked: "1" }), shipmentLine({ id: "two-b", item_index: "КБ2", picked: "1000" }),
    ] }];
    render(<ProductionIntakePage principal={viewer} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Отгружено/ }));
    expect([...document.querySelectorAll('td[data-label="КБ"]')].map((td) => td.textContent)).toEqual(["КБ1", "КБ2", "КБ2", "КБ10"]);
    expect(count(/^Отгружено/)).toBe("3");
  });

  it("упакованная КБ, которой нет в приёмке, встаёт в свой шаг без записей; зрителю — без кнопок", async () => {
    intakeRows = [];
    shipments = [{ ...packedShipment, ready_to_ship: true, missing_data: [], missing_ship: [], lines: [shipmentLine({ picked: "38", requested: "100" })] }];
    const first = render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findByRole("tab", { name: /^На отгрузку/ })).toHaveAttribute("aria-selected", "true");
    expect(rowButtons()).toEqual(["Отгрузить 591К.3 КБ1"]);
    first.unmount();
    render(<ProductionIntakePage principal={viewer} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^На отгрузку/ }));
    expect(rowButtons()).toEqual([]);
    expect(writes()).toEqual([]);
  });

  it("поиск и Excel находят ГРН партий строки", async () => {
    intakeRows = [row({ accepted: "12", packed: "12", remaining: "0", tasks: [] })];
    shipments = [
      { ...packedShipment, lines: [shipmentLine({ grn: "ГП.26.0000042", grns: ["ГП.26.0000042"] })] },
      { ...packedShipment, id: "s2", number: "ОТГ-2", lines: [shipmentLine({ id: "l3", grn: "ГП.26.0000043", grns: ["ГП.26.0000043", "ГП.26.0000044"] })] },
    ];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    showColumns(["ГРН"]);
    fireEvent.change(screen.getByRole("searchbox", { name: "Поиск: Приёмка с производства" }), { target: { value: "ГП.26.0000044" } });
    expect(bodyRows().map((tr) => cell(tr, "Отправка"))).toEqual(["ОТГ-2"]);
    let blob: Blob | null = null;
    const create = vi.spyOn(URL, "createObjectURL").mockImplementation((value) => { blob = value as Blob; return "blob:intake"; });
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    try {
      fireEvent.click(screen.getByRole("button", { name: "Excel" }));
      const text = new TextDecoder("utf-16le").decode(new Uint8Array(await blob!.arrayBuffer()).slice(2));
      const [heads, values] = text.split("\r\n").map((line) => line.split("\t"));
      expect(values[heads.indexOf("ГРН")]).toBe("ГП.26.0000043, ГП.26.0000044");
    } finally { create.mockRestore(); revoke.mockRestore(); click.mockRestore(); }
  });

  it("без права на склад — список виден, действий и печати нет; заголовок проекта с заказчиком", async () => {
    intakeRows = [...ROWS, row({ item_index: "КБ3", name: "Жгут принятый", accepted: "10", remaining: "0", acts: [act("ГП.26.0000031", "10")] })];
    render(<ProductionIntakePage principal={viewer} />);
    await screen.findByText("Кабель");
    expect(screen.getByText(/^591К\.3 · Заказчик/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Принять/ })).not.toBeInTheDocument();
    fireEvent.click(tab(/^Принято/));
    await screen.findByText("Жгут принятый");
    expect(screen.queryByRole("button", { name: /Печать этикетки|^Упаковать /i })).not.toBeInTheDocument();
    expect(headers()).not.toContain("Упаковать");
  });

  it("журнал, «+ Позиция ГП» и «+ Заявка на отгрузку» — окнами из шапки; `?tab=journal` открывает журнал", async () => {
    shipments = [gone];
    const first = render(<ProductionIntakePage principal={admin} />);
    await screen.findByText("Жгут");
    fireEvent.click(screen.getByRole("button", { name: "Журнал" }));
    const journal = await screen.findByRole("dialog", { name: "Журнал отгрузок" });
    expect(within(journal).getByText("PL591К.3-1")).toBeInTheDocument();
    fireEvent.click(within(journal).getByRole("button", { name: "Закрыть" }));
    fireEvent.click(screen.getByRole("button", { name: "+ Позиция ГП" }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: "Позиция готовой продукции" })).getByRole("button", { name: "Закрыть" }));
    fireEvent.click(screen.getByRole("button", { name: "+ Заявка на отгрузку" }));
    expect(await screen.findByRole("dialog", { name: "Заявка на отгрузку" })).toBeInTheDocument();
    first.unmount();
    window.history.replaceState({}, "", "/?section=intake&tab=journal");
    render(<ProductionIntakePage principal={viewer} />);
    expect(await screen.findByRole("dialog", { name: "Журнал отгрузок" })).toBeInTheDocument();
  });
});
