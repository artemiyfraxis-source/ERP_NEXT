import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProductionIntakePage } from "./ProductionIntakePage";
import { isoDay } from "./Gantt";
import { capturePrintFrames } from "./testPrintFrames";
import { setRightsPrincipal } from "./rights";

const principal = {
  user: { id: "u1", email: "k@example.test", name: "Кладовщик", department: "Склад" },
  roles: ["foreman"], access_level: "foreman", permissions: { shipping_goods: "edit" as const },
};
const viewer = { ...principal, permissions: { shipping: "view" as const } };
const admin = { ...principal, roles: ["admin"], access_level: "admin", permissions: { shipping: "edit" as const, shipping_goods: "edit" as const } };
const deliveryHeaders = ["Получатель", "Способ доставки", "Перевозчик", "Адрес", "Телефон", "Комментарий для склада"];

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
let addressees: unknown[] = [];
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
    if (url === "/api/shipping/project-addressees") return json({ addressees });
    if (url === "/api/production/finished-goods-ready") return json({ boxes: [
      { id: "old-box", number: "000044-8", quantity: "20", project: "591К.3", task_item: "КБ1", status: "Подтверждена", version: 1 },
    ] });
    if (url === "/api/shipping/finished-goods/accept") return json(acceptBody, acceptStatus);
    if (url.startsWith("/api/shipping/intake/") && url.endsWith("/label")) return json({ status: "queued", reason: "В очереди принтера.", file: "j1", copies: 1, job_id: "j1" });
    if (url.startsWith("/api/shipping/project-addressee?")) return json({ addressee: dispatchCard });
    if (url === "/api/shipping/project-addressee" && method === "PUT") return json({ addressee: { ...dispatchCard, version: 3 } });
    if (url === "/api/shipping/project-ship") return json({ shipment: { ...packedShipment, id: "s9", number: "ОТГ-9", pl_number: "PL591К.3-9" }, shipped: false, closed: false, why: "", replayed: false });
    if (url === "/api/shipping/no-upd-approvers") return json({ approvers: [{ id: "p1", name: "Артемий" }, { id: "p2", name: "Зингер" }, { id: "p3", name: "Касаточкин" }] });
    if (url.endsWith("/approve-no-upd")) { const [status, body] = approvals.shift() ?? [200, { shipment: packedShipment }]; return json(body, status); }
    if (/\/print\?document=/.test(url)) return json({ forms: [{ kind: "pl", title: "PL1-1", number: ["PL", "1", "-", "1"], place: "1", places: "1", package: "Короб", dims: "", net: "", gross: "", seal: "",
      rows: [{ part: "КБ1", name: "", quantity: "1", unit: "шт", batch: "", pl: "", boxes: "" }], packed_at: "", shipped_at: "", packer: "", shipper: "", consignee: "", address: "", project: "591К.3" }] });
    if (url.endsWith("/labels")) return json({ labels: [{ shipper: "Фраксис", consignee: "Получатель", address: "Москва", project: "591К.3", pl_number: "PL591К.3-1", place: "Упаковка 1/1", items: [{ item_index: "КБ1", quantity: "4", unit: "шт" }], net: "", dims: "" }] });
    if (url.endsWith("/head") && method === "PUT") return json({ shipment: { ...packedShipment, ...calls.at(-1)!.body as object, version: 4 } });
    if (url.endsWith("/documents") && method === "PUT") return json({ shipment: packedShipment });
    if (/\/shipments\/[^/]+\/(ship|close|unship)$/.test(url)) return json({ shipment: packedShipment, closed: false, why: "Нельзя закрыть: не хватает — файл УПД" });
    return json({});
  }));
}
beforeEach(() => { intakeRows = ROWS; shipments = []; addressees = []; approvals = []; setRightsPrincipal(null); stub(); });
afterEach(() => { cleanup(); setRightsPrincipal(null); vi.unstubAllGlobals(); });

describe("Приёмка с производства", () => {
  it.each([[24, 476], [100, 186, 214]])("отделяет общий итог и действия КБ от актов приёмки %j", async (...amounts) => {
    const sent = amounts.length === 2 ? 24 : 286;
    intakeRows = [row({ accepted: "500", packed: String(sent), sent: String(sent), needed: "500", remaining: "0", pack_permission_remaining: String(500 - sent),
      acts: amounts.map((amount, index) => ({ grn: `ГП.ДЕМО.${index}`, quantity: String(amount), received_at: "2026-11-03", location: "" })) })];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Принято/ }));
    const total = (await screen.findByText("Итого по КБ")).closest("tr")!;
    expect(total).toHaveTextContent("500");
    expect(total).toHaveTextContent(String(sent));
    expect(within(total).getByRole("button", { name: /^Упаковать / })).toHaveTextContent(`Упаковать остаток · ${500 - sent}`);
    for (const act of document.querySelectorAll("tr.intake-act-row")) {
      expect(within(act as HTMLElement).queryByRole("button", { name: /^Упаковать|^Отгрузка / })).not.toBeInTheDocument();
      expect(act).not.toHaveTextContent("Итого по КБ");
    }
    expect(calls.every(({ method }) => method === "GET")).toBe(true);
  });
  it("принятое открывает данные проекта до упаковки без создания отправки", async () => {
    intakeRows = [row({ accepted: "20", packed: "0", remaining: "0", tasks: [] })];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Принято/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Данные отправки" }));
    const dialog = within(await screen.findByRole("dialog", { name: "Данные отправки 591К.3" }));
    expect(await dialog.findByLabelText("Получатель")).toBeEnabled();
    fireEvent.change(dialog.getByLabelText("Тип доставки"), { target: { value: "ДОСТАВКА ТК" } });
    expect(dialog.getByLabelText("Адрес доставки")).toBeEnabled();
    fireEvent.change(dialog.getByLabelText("УПД"), { target: { value: "ЭДО" } });
    expect(dialog.getByLabelText("№ УПД")).toBeEnabled();
    expect(dialog.getByLabelText("Приложить УПД")).toBeEnabled();
    expect(calls.every(({ method }) => method === "GET")).toBe(true);
  });
  it("не загружает и не показывает старую приёмку коробок после ОТК", async () => {
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    expect(screen.queryByText(/Коробки из цеха после ОТК/)).not.toBeInTheDocument();
    expect(screen.queryByText("000044-8")).not.toBeInTheDocument();
    expect(calls.some(({ url }) => url === "/api/production/finished-goods-ready" || url === "/api/shipping/goods")).toBe(false);
    expect(calls.filter(({ method }) => method !== "GET")).toEqual([]);
  });

  it("показывает данные доставки и комментарий из одной пакетной загрузки без записи и срока проекта", async () => {
    addressees = [{ ...dispatchCard, receiver: "Получатель проекта", contact: "Анна", phone: "+7 111", carrier: "Самовывоз", note: "Позвонить до приезда" }];
    render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findAllByText("Получатель проекта")).toHaveLength(2);
    expect(screen.getAllByText("Анна")).toHaveLength(2);
    expect(screen.getAllByText("+7 111")).toHaveLength(2);
    expect(screen.getAllByText("Позвонить до приезда")).toHaveLength(2);
    expect(screen.queryByText(/^срок /)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Данные отправки" })).not.toBeInTheDocument();
    expect(calls.filter((call) => call.url === "/api/shipping/project-addressees")).toHaveLength(1);
    expect(calls.filter((call) => call.method !== "GET")).toEqual([]);
  });

  it("упакованное показывает полный ГРН кнопкой и раздельные документы без автоматического формирования", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", tasks: [] })];
    shipments = [{ ...packedShipment, upd_number: "485", upd_url: "/api/shipping/files/upd485", missing_data: [], lines: [shipmentLine({ grn: "ГП.26.0000042" })] }];
    const printed = capturePrintFrames();
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    const grn = await screen.findByRole("button", { name: "Печать этикетки ГП.26.0000042" });
    expect(grn).toHaveTextContent("ГП.26.0000042");
    expect(grn).not.toHaveTextContent("Печать");
    for (const title of ["PL", "DN", "УПД", "Короба"]) expect(screen.getByRole("columnheader", { name: new RegExp(`^${title}( |$)`) })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: /^Документы/ })).not.toBeInTheDocument();
    expect(document.querySelector('td[data-label="PL"]')).toContainElement(screen.getByRole("button", { name: "Печать PL ОТГ-20261029-01" }));
    expect(document.querySelector('td[data-label="DN"]')).toContainElement(screen.getByRole("button", { name: "Печать DN ОТГ-20261029-01" }));
    expect(document.querySelector('td[data-label="УПД"]')).toContainElement(screen.getByRole("link", { name: /УПД.*485/ }));
    expect(document.querySelector('td[data-label="Короба"]')).toContainElement(screen.getByRole("button", { name: "Документы ОТГ-20261029-01" }));
    expect(screen.getByRole("button", { name: "Печать PL ОТГ-20261029-01" })).toHaveTextContent("PL");
    expect(screen.getByRole("button", { name: "Печать DN ОТГ-20261029-01" })).toHaveTextContent("DN");
    expect(screen.getByRole("link", { name: /УПД.*485/ })).toHaveAttribute("href", "/api/shipping/files/upd485");
    expect(calls.filter((call) => call.method !== "GET")).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Печать PL ОТГ-20261029-01" }));
    await waitFor(() => expect(printed.print).toHaveBeenCalledTimes(1));
    expect(calls.some((call) => call.url === "/api/shipping/shipments/s1/print?document=pl")).toBe(true);
    fireEvent.click(grn);
    await screen.findByText("В очереди принтера.");
    expect(calls.find((call) => call.url.endsWith("/label"))?.url).toBe("/api/shipping/intake/%D0%93%D0%9F.26.0000042/label");
    expect(screen.queryByText("ГП.26.0000001")).not.toBeInTheDocument();
    printed.restore();
  });

  it("сохраняет пары адресат-перевозчик-трек каждой отправки и оба комментария", async () => {
    intakeRows = [row({ accepted: "8", packed: "8", remaining: "0", tasks: [] })];
    addressees = [{ ...dispatchCard, receiver: "Общий получатель", note: "Склад проекта" }];
    shipments = [
      { ...packedShipment, receiver: "Получатель А", shipping_point: "Адрес А", carrier: "СДЭК", phone: "+7 А", track_number: "Самовывоз", note: "На рампу А" },
      { ...packedShipment, id: "s2", number: "ОТГ-2", receiver: "Получатель Б", shipping_point: "Адрес Б", carrier: "Яндекс Доставка", phone: "+7 Б", track_number: "Доставка Яндекс", note: "На рампу Б" },
    ];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    for (const text of ["Получатель А", "Получатель Б", "Адрес А", "Адрес Б", "СДЭК", "Яндекс Доставка", "Самовывоз", "Доставка Яндекс", "Склад проекта", "На рампу А", "На рампу Б"]) {
      expect((await screen.findAllByText(text, { exact: true })).length).toBeGreaterThan(0);
    }
    expect(screen.queryByText("Общий получатель")).not.toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /^Перевозчик/ })).toBeInTheDocument();
    const address = document.querySelector('td[data-label="Адрес"]')!;
    expect(address.querySelector('[data-shipment="s1"]')).toHaveTextContent("Адрес А");
    expect(address.querySelector('[data-shipment="s2"]')).toHaveTextContent("Адрес Б");
    const tracks = document.querySelector('td[data-label="Трек"]')!;
    expect(tracks.querySelector('[data-shipment="s1"]')).toHaveTextContent("Самовывоз");
    expect(tracks.querySelector('[data-shipment="s2"]')).toHaveTextContent("Доставка Яндекс");
    expect(calls.filter((call) => call.method !== "GET")).toEqual([]);
  });

  it("вносит УПД именно выбранной отправки, а не карточки проекта", async () => {
    intakeRows = [row({ accepted: "8", packed: "8", remaining: "0", tasks: [] })];
    shipments = [packedShipment, { ...packedShipment, id: "s2", number: "ОТГ-2" }];
    render(<ProductionIntakePage principal={admin} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Внести УПД ОТГ-2" }));
    const dialog = await screen.findByRole("dialog", { name: "Данные отправки ОТГ-2" });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "№ УПД" }), { target: { value: "519" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(calls.some((call) => call.url === "/api/shipping/shipments/s2/documents" && call.method === "PUT")).toBe(true));
    expect(calls.find((call) => call.url === "/api/shipping/shipments/s2/documents")?.body).toMatchObject({ version: 3, upd_number: "519" });
    expect(calls.some((call) => call.url === "/api/shipping/project-addressee" && call.method === "PUT")).toBe(false);
  });

  it("сохраняет связь ГРН с каждой отправкой даже при совпадающих номерах партий", async () => {
    intakeRows = [row({ accepted: "12", packed: "12", remaining: "0", tasks: [] })];
    shipments = [
      { ...packedShipment, receiver: "Адресат А" },
      { ...packedShipment, id: "s2", number: "ОТГ-2", receiver: "Адресат Б" },
      { ...packedShipment, id: "s3", number: "ОТГ-3", receiver: "Адресат В", lines: [shipmentLine({ grn: "ГП.26.0000002" })] },
    ];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    const grns = document.querySelector<HTMLElement>('td[data-label="ГРН"]')!;
    expect(grns.querySelector('[data-shipment="s1"]')).toHaveTextContent("1ГП.26.0000001");
    expect(grns.querySelector('[data-shipment="s2"]')).toHaveTextContent("2ГП.26.0000001");
    expect(grns.querySelector('[data-shipment="s3"]')).toHaveTextContent("3ГП.26.0000002");
    expect(within(grns).getAllByRole("button", { name: "Печать этикетки ГП.26.0000001" })).toHaveLength(2);
    expect(calls.filter((call) => call.method !== "GET")).toEqual([]);
  });

  it("ищет и выгружает показанные ГРН отправок, убирая повторы только внутри одной отправки", async () => {
    intakeRows = [row({ accepted: "12", packed: "12", remaining: "0", tasks: [] })];
    shipments = [
      { ...packedShipment, lines: [shipmentLine({ grn: "ГП.26.0000042" }), shipmentLine({ id: "l2", grn: "ГП.26.0000042" })] },
      { ...packedShipment, id: "s2", number: "ОТГ-2", lines: [shipmentLine({ grn: "ГП.26.0000042" }), shipmentLine({ id: "l3", grn: "ГП.26.0000043" })] },
    ];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    const search = screen.getByRole("searchbox", { name: "Поиск: Приёмка с производства" });
    fireEvent.change(search, { target: { value: "ГП.26.0000043" } });
    expect(screen.getByText("Жгут")).toBeInTheDocument();
    const grns = document.querySelector<HTMLElement>('td[data-label="ГРН"]')!;
    expect(within(grns).getAllByRole("button", { name: "Печать этикетки ГП.26.0000042" })).toHaveLength(2);

    let blob: Blob | null = null;
    const create = vi.spyOn(URL, "createObjectURL").mockImplementation((value) => { blob = value as Blob; return "blob:intake"; });
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    try {
      fireEvent.click(screen.getByRole("button", { name: "Excel" }));
      const text = new TextDecoder("utf-16le").decode(new Uint8Array(await blob!.arrayBuffer()).slice(2));
      const [headers, values] = text.split("\r\n").map((line) => line.split("\t"));
      expect(values[headers.indexOf("ГРН")]).toBe("ГП.26.0000042; ГП.26.0000042, ГП.26.0000043");
      expect(text).not.toContain("ГП.26.0000001");
    } finally { create.mockRestore(); revoke.mockRestore(); click.mockRestore(); }
    fireEvent.change(search, { target: { value: "ГП.26.0000001" } });
    expect(screen.queryByText("Жгут")).not.toBeInTheDocument();
  });

  it("не показывает печать PL/DN без права печати и оставляет данные и ГРН читаемыми", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", tasks: [] })];
    shipments = [packedShipment];
    setRightsPrincipal(viewer);
    render(<ProductionIntakePage principal={viewer} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    expect(await screen.findByText("ГП.26.0000001")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Печать/ })).not.toBeInTheDocument();
    expect(screen.getByText("Получатель", { selector: "td span span" })).toBeInTheDocument();
  });

  it("самовывоз показывает отсутствие необходимости трека, не предлагает ввод и не теряет комментарий", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", tasks: [] })];
    addressees = [{ ...dispatchCard, note: "Приезд после 15:00" }];
    shipments = [{ ...packedShipment, terms: "САМОВЫВОЗ", carrier: "Самовывоз", track_number: "", note: "Приезд после 15:00", missing_data: [] }];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    expect(document.querySelector('td[data-label="Трек"]')).toHaveTextContent("Не требуется");
    expect(screen.queryByRole("button", { name: /^Трек / })).not.toBeInTheDocument();
    expect(screen.getAllByText("Приезд после 15:00")).toHaveLength(1);
  });

  it("самовывоз по перевозчику без условий показывает способ и скрывает старый адрес проекта и отправки", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", tasks: [] })];
    addressees = [{ ...dispatchCard, terms: null, carrier: "Самовывоз", shipping_point: "Старый адрес проекта" }];
    shipments = [{ ...packedShipment, terms: null, carrier: "Самовывоз", shipping_point: "Старый адрес отправки" }];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^В производстве/ }));
    for (const cell of document.querySelectorAll('td[data-label="Способ доставки"]')) expect(cell).toHaveTextContent("Самовывоз");
    for (const cell of document.querySelectorAll('td[data-label="Адрес"]')) expect(cell).toHaveTextContent("Не требуется");
    expect(document.querySelector('td[data-label="Адрес"]')).toBeInTheDocument();
    expect(screen.queryByText("Старый адрес проекта")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /^Упаковано/ }));
    expect(document.querySelector('td[data-label="Способ доставки"]')).toHaveTextContent("Самовывоз");
    expect(document.querySelector('td[data-label="Адрес"]')).toHaveTextContent("Не требуется");
    expect(screen.queryByText("Старый адрес отправки")).not.toBeInTheDocument();
    expect(calls.filter((call) => call.method !== "GET")).toEqual([]);
  });

  it("780К.1: four fully accepted KBs show four real GRN rows and one packing control per KB", async () => {
    intakeRows = ["15", "15", "50", "15"].map((quantity, index) => row({ project: "780К.1", item_index: `КБ${index + 1}`, stage: "Упаковано", project_stage: "Упаковано",
      needed: quantity, accepted: quantity, packed: "0", sent: "0", remaining: "0", tasks: [],
      acts: [{ grn: `ГП.26.000010${index}`, quantity, received_at: "2026-10-30", location: "" }] }));
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("ГП.26.0000100");
    const acceptedTab = screen.getByRole("tab", { name: /^Принято/ });
    expect(acceptedTab).toHaveTextContent("Принято 4");
    fireEvent.click(acceptedTab);
    const lines = [...document.querySelectorAll("tbody tr")].filter((tr) => tr.querySelector(".intake-kb"));
    expect(lines).toHaveLength(8);
    expect(lines.filter((tr) => tr.classList.contains("intake-act-row"))).toHaveLength(4);
    for (let index = 1; index <= 4; index++) {
      const button = screen.getByRole("button", { name: `Упаковать 780К.1 КБ${index}: ждёт решения менеджера` });
      expect(button).toBeDisabled();
      expect(button.closest("tr")).toHaveTextContent("Итого по КБ");
    }
  });

  it("multiple GRN lots keep one packing action on the KB total and a real remainder of 27", async () => {
    intakeRows = [row({ needed: "1000", accepted: "973", packed: "100", remaining: "27", pack_decided: true, pack_permission_remaining: "10000",
      acts: [["ГП.26.0000200", "500"], ["ГП.26.0000201", "473"]].map(([grn, quantity]) => ({ grn, quantity, received_at: "2026-10-30", location: "" })) })];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Все/ }));
    const packing = screen.getAllByRole("button", { name: "Упаковать 591К.3 КБ1: упаковано 100, можно 873" });
    expect(packing).toHaveLength(1);
    expect(packing[0].closest("tr")).toHaveTextContent("Итого по КБ");
    expect(screen.getByRole("button", { name: "Принять 591К.3 КБ1: можно 27" })).toBeInTheDocument();
    fireEvent.click(packing[0]);
    expect(await screen.findByRole("dialog", { name: "Упаковка 591К.3" })).toBeInTheDocument();
  });

  it("actual 780К.1 and 713К.4 count five accepted KBs and display exactly five GRN rows", async () => {
    const act = (grn: string, quantity: string) => [{ grn, quantity, received_at: "2026-10-30", location: "" }];
    intakeRows = [
      ...["15", "15", "50", "15"].map((quantity, index) => row({ project: "780К.1", item_index: `КБ${index + 1}`, stage: "Упаковано", project_stage: "Упаковано",
        needed: quantity, accepted: quantity, remaining: "0", acts: act(`ГП.26.000030${index}`, quantity) })),
      row({ project: "713К.4", needed: "500", accepted: "24", remaining: "476", pack_decided: true, pack_permission_remaining: "10000", acts: act("ГП.26.0000037", "24") }),
      row({ project: "713К.4", item_index: "КБ2", stage: "Упаковано", needed: "500", accepted: "500", packed: "500", remaining: "0" }),
    ];
    shipments = [{ ...packedShipment, lines: [shipmentLine({ project: "713К.4", item_index: "КБ2", requested: "500", picked: "500" })] }];
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByRole("button", { name: "Принять 713К.4 КБ1: можно 476" });
    const accepted = screen.getByRole("tab", { name: /^Принято/ });
    expect(accepted).toHaveTextContent("Принято 5");
    fireEvent.click(accepted);
    const lines = [...document.querySelectorAll("tbody tr")].filter((tr) => tr.querySelector(".intake-kb"));
    expect(lines).toHaveLength(10);
    expect(lines.filter((tr) => tr.classList.contains("intake-act-row"))).toHaveLength(5);
    expect(screen.getAllByRole("button", { name: /^Упаковать 713К.4 КБ1/ })).toHaveLength(1);
  });

  it("KB total retains track and shipment actions once; documents remain in «Упаковано»", async () => {
    intakeRows = [row({ needed: "4", accepted: "4", packed: "4", remaining: "0", acts: [
      { grn: "ГП.26.0000400", quantity: "2", received_at: "2026-10-30", location: "" },
      { grn: "ГП.26.0000401", quantity: "2", received_at: "2026-10-30", location: "" },
    ] })];
    shipments = [{ ...packedShipment, missing_data: [], missing_ship: ["трек-номер"], ready_for_track: true }];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Все/ }));
    await screen.findByText("ГП.26.0000400");
    for (const name of ["Трек 591К.3 КБ1", "Отгрузка 591К.3 КБ1: отгружено 0"]) {
      const buttons = screen.getAllByRole("button", { name });
      expect(buttons).toHaveLength(1);
      expect(buttons[0].closest("tr")).toHaveTextContent("Итого по КБ");
    }
    expect(screen.getByRole("button", { name: "Трек 591К.3 КБ1" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Трек 591К.3 КБ1" }));
    const dialog = await screen.findByRole("dialog", { name: "Трек 591К.3 КБ1" });
    expect(within(dialog).getByRole("textbox", { name: "Трек-номер ОТГ-20261029-01" })).toBeEnabled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Закрыть" }));
    fireEvent.click(screen.getByRole("tab", { name: /^Упаковано/ }));
    fireEvent.click(screen.getByRole("button", { name: "Документы ОТГ-20261029-01" }));
    expect(await screen.findByRole("dialog", { name: "Документы отправки ОТГ-20261029-01" })).toBeInTheDocument();
  });

  it("число «Можно принять» — кнопка: окно с КБ, заказано/принято/остаток, количество по умолчанию — всё; уходит дата, место и строка", async () => {
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    expect(screen.getByRole("heading", { name: "Приёмка с производства" })).toBeInTheDocument();
    expect(screen.queryByText("Шлейф")).not.toBeInTheDocument(); // в «Можно принять» только то, что сдано
    const headers = screen.getAllByRole("columnheader").map((cell) => cell.textContent ?? "");
    for (const gone of ["Принять, шт", "Этап", "Сдано цехом", "Место хранения"]) expect(headers.some((text) => text.includes(gone))).toBe(false);
    expect(headers.map((text) => text.replace(/[^А-Яа-яЁё ]/g, "").trim())).toEqual(["КБ", "Наименование", "Заказано", "Можно принять", ...deliveryHeaders]);
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

  it("пустые колонки не показываются; срок проекта не отвлекает склад", async () => {
    const past = isoDay(new Date(Date.now() - 8 * 86_400_000));
    intakeRows = [row({ deadline: past })];
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    const headers = screen.getAllByRole("columnheader").map((cell) => cell.textContent);
    expect(headers.some((text) => text?.includes("Отгружено"))).toBe(false);
    expect(headers.some((text) => text?.includes("Трек"))).toBe(false);
    expect(headers.some((text) => text?.includes("Можно принять"))).toBe(true);
    expect(screen.queryByText(/просрочен/)).not.toBeInTheDocument();
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

  it("«Все» показывает и КБ, которые цех ещё не сдал; заголовок проекта с заказчиком", async () => {
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    expect(screen.getByText(/^591К\.3 · Заказчик/)).toBeInTheDocument();
    expect(screen.queryByText(/^срок 25\.10/)).not.toBeInTheDocument();
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

  it("кладовщику без права на отправки «Данные отправки» и задания цеха не показываются", async () => {
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    expect(screen.queryByRole("button", { name: "Данные отправки" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: /Задания/ })).not.toBeInTheDocument();
  });

  it("«Данные отправки»: сохраняет изменённую шапку выбранной отправки и документы с новой версией", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", tasks: [] })];
    shipments = [packedShipment];
    render(<ProductionIntakePage principal={admin} />);
    await screen.findByText("Жгут");
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    fireEvent.click(screen.getByRole("button", { name: "Внести УПД ОТГ-20261029-01" }));
    const dialog = await screen.findByRole("dialog", { name: "Данные отправки ОТГ-20261029-01" });
    expect(within(dialog).getByLabelText("Получатель")).toHaveValue("Получатель");
    fireEvent.change(within(dialog).getByLabelText("Получатель"), { target: { value: " Новый получатель " } });
    fireEvent.change(within(dialog).getByLabelText("Тип УПД"), { target: { value: "БЕЗ УПД" } });
    fireEvent.change(within(dialog).getByLabelText("Телефон получателя"), { target: { value: " +7 901 " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(dialog).not.toBeInTheDocument());
    expect(calls.filter((call) => call.method === "PUT")).toEqual([
      { url: "/api/shipping/shipments/s1/head", method: "PUT", body: { version: 3, receiver: "Новый получатель", phone: "+7 901" } },
      { url: "/api/shipping/shipments/s1/documents", method: "PUT", body: { version: 4, upd_type: "БЕЗ УПД", upd_number: "", upd_date: null, upd_url: "", track_number: "" } },
    ]);
  });

  it("менеджер с правом только на чтение видит карточку без «Сохранить»", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", tasks: [] })];
    shipments = [packedShipment];
    render(<ProductionIntakePage principal={viewer} />);
    await screen.findByText("Жгут");
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    fireEvent.click(screen.getByRole("button", { name: "Внести УПД ОТГ-20261029-01" }));
    const dialog = await screen.findByRole("dialog", { name: "Данные отправки ОТГ-20261029-01" });
    const fields = dialog.querySelectorAll("input, select, textarea");
    expect(fields.length).toBeGreaterThan(0);
    for (const field of fields) expect(field.matches(":disabled, [readonly]")).toBe(true);
    expect(within(dialog).queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Получатель"), { target: { value: "Попытка правки" } });
    fireEvent.submit(dialog.querySelector("form")!);
    await waitFor(() => expect(calls.filter((call) => call.method !== "GET")).toEqual([]));
    expect(dialog).toBeInTheDocument();
  });

  it("конфликт версии документов показывает ответ сервера и сохраняет открытый ввод УПД", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", tasks: [] })];
    shipments = [packedShipment];
    render(<ProductionIntakePage principal={admin} />);
    await screen.findByText("Жгут");
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    fireEvent.click(screen.getByRole("button", { name: "Внести УПД ОТГ-20261029-01" }));
    const dialog = await screen.findByRole("dialog", { name: "Данные отправки ОТГ-20261029-01" });
    fireEvent.change(within(dialog).getByLabelText("№ УПД"), { target: { value: "519" } });
    const base = globalThis.fetch as unknown as (i: RequestInfo | URL, n?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", vi.fn(async (i: RequestInfo | URL, n?: RequestInit) => {
      if (String(i) === "/api/shipping/shipments/s1/documents" && n?.method === "PUT") {
        calls.push({ url: String(i), method: n.method, body: JSON.parse(String(n.body)) });
        return json({ detail: "Конкурентное изменение: обновите данные" }, 409);
      }
      return base(i, n);
    }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Конкурентное изменение: обновите данные");
    expect(calls.filter((call) => call.method === "PUT")).toEqual([
      { url: "/api/shipping/shipments/s1/documents", method: "PUT", body: { version: 3, upd_type: "ЭДО", upd_number: "519", upd_date: null, upd_url: "", track_number: "" } },
    ]);
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByLabelText("№ УПД")).toHaveValue("519");
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
    expect(ordered).toHaveLength(3); // итог КБ и два акта
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
    await waitFor(() => expect(rowsIn()).toHaveLength(5)); // итог КБ и четыре акта
    fireEvent.click(screen.getByRole("tab", { name: /^Все/ }));
    await waitFor(() => expect(rowsIn()).toHaveLength(5)); // во «Все» итог также показывает фактический остаток
    expect(screen.getByRole("button", { name: "Принять 533К.12 КБ1: можно 27" })).toBeInTheDocument();
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
  const multi = () => row({ needed: "40", delivered: "26", accepted: "20", packed: "10", sent: "3", remaining: "20", pack_decided: true, pack_permission_remaining: "10000", acts: [] });
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

  it.each(["Готово", "ОТК", "ОТК пройден"])("КБ на этапе «%s» (сделано, ждёт приёмки) — во вкладке «Готово», не «В производстве»", async (stage) => {
    // Владелец 29.10: «Готово» — производство закончено, «ОТК» — на проверке, «ОТК пройден» — заглушка; всё принимается.
    intakeRows = [row({ stage, project_stage: stage, delivered: "0", accepted: "6", remaining: "34", tasks: [] })];
    render(<ProductionIntakePage principal={principal} />);
    await screen.findByText("Жгут");
    expect(tab(/^Готово/)).toHaveAttribute("aria-selected", "true");
    expect([/^В производстве/, /^Готово/].map(count)).toEqual(["0", "1"]);
  });

  it("уехавшая целиком КБ (её нет в приёмке) видна в «Отгружено» из отправок за 30 дней", async () => {
    intakeRows = [];
    shipments = [{ ...gone, lines: [shipmentLine({ id: "l9", item_index: "КБ9", name: "Уехавший жгут", picked: "5", requested: "5" })] }];
    render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findByText("Уехавший жгут")).toBeInTheDocument();
    expect(tab(/^Отгружено/)).toHaveAttribute("aria-selected", "true");
    const line = [...document.querySelectorAll("tbody tr")].filter((tr) => tr.textContent?.includes("Уехавший жгут"))[0];
    expect([...line.querySelectorAll("button")].map((button) => button.getAttribute("aria-label"))).toEqual(["Печать этикетки ГП.26.0000001"]);
    expect(line.textContent).toContain("5");
  });

  it("administrator opens the exact historical shipment from its date", async () => {
    intakeRows = [];
    shipments = [gone];
    render(<ProductionIntakePage principal={admin} />);
    fireEvent.click(await screen.findByRole("button", { name: `Данные отправки ${gone.number}` }));
    const dialog = await screen.findByRole("dialog", { name: `Данные отправки ${gone.number}` });
    fireEvent.click(within(dialog).getByRole("button", { name: "Исправить историю" }));
    expect(screen.getByRole("dialog", { name: `Исправление истории ${gone.number}` })).toBeInTheDocument();
  });

  it("«Упаковано»: без решения менеджера кнопка выключена; с решением — окно, количество по умолчанию — всё принятое", async () => {
    intakeRows = [row({ accepted: "6", remaining: "0", delivered: "6", tasks: [] })];
    const { unmount } = render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Принято/ }));
    const waiting = await screen.findByRole("button", { name: "Упаковать 591К.3 КБ1: ждёт решения менеджера" });
    expect(waiting).toBeDisabled();
    expect(waiting).toHaveAttribute("title", "Упаковать можно после решения менеджера «упаковывать»");
    unmount();

    // На строке акта упаковка использует всё принятое по КБ.
    intakeRows = [row({ accepted: "6", remaining: "0", delivered: "6", tasks: [], pack_decided: true, pack_permission_remaining: "10000", acts: [{ grn: "ГП.26.0000001", quantity: "6", received_at: isoDay(new Date()), location: "" }] })];
    render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findByRole("tab", { name: /^Принято/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(await screen.findByRole("button", { name: "Упаковать 591К.3 КБ1: упаковано 0, можно 6" }));
    const dialog = await screen.findByRole("dialog", { name: "Упаковка 591К.3" });
    expect(within(dialog).getByLabelText("Сколько упаковать КБ1")).toHaveValue(null);
    expect(within(dialog).getByRole("button", { name: "Упаковать · 0 шт" })).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("Сколько упаковать КБ1"), { target: { value: "6" } });
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

  it("без УПД: уполномоченный выбирает себя и вводит разрешительный ПИН; неверный ПИН и блокировку объясняет сервер", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", delivered: "4", tasks: [] })];
    shipments = [{ ...packedShipment, missing_ship: ["№ УПД", "файл УПД", "трек-номер"] }];
    approvals = [[403, { detail: "Неверный разрешительный ПИН." }], [429, { detail: "Слишком много попыток. Подождите 10 минут." }], [200, { shipment: packedShipment }]];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Все/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Отгрузка 591К.3 КБ1: отгружено 0" }));
    const ships = await screen.findByRole("dialog", { name: "Отгрузка 591К.3" });
    fireEvent.click(within(ships).getByRole("button", { name: "Разрешить без УПД" }));
    const pinWindow = await screen.findByRole("dialog", { name: "Отгрузка без УПД · ОТГ-20261029-01" });
    const who = within(pinWindow).getByLabelText("Кто разрешает");
    await waitFor(() => expect(within(who).getAllByRole("option").map((option) => option.textContent)).toEqual(["выберите", "Артемий", "Зингер", "Касаточкин"]));
    const pin = within(pinWindow).getByLabelText("Разрешительный ПИН");
    expect(pin).toHaveAttribute("type", "password");
    const submit = within(pinWindow).getByRole("button", { name: "Разрешить отгрузку без УПД" });
    expect(submit).toBeDisabled();
    fireEvent.change(who, { target: { value: "p2" } });
    fireEvent.change(pin, { target: { value: "1111" } });
    fireEvent.click(submit);
    expect(await within(pinWindow).findByText("Неверный разрешительный ПИН.")).toBeInTheDocument();
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
    const headers = () => screen.getAllByRole("columnheader").map((cell) => (cell.textContent ?? "").replace(/[^А-Яа-яЁёA-Za-z ]/g, "").trim());
    const lineButtons = () => [...document.querySelectorAll("tbody tr")].filter((tr) => tr.textContent?.includes("Жгут") && !tr.textContent.includes("Данные отправки"))
      .flatMap((tr) => [...tr.querySelectorAll("button")].map((button) => button.getAttribute("aria-label") ?? button.textContent ?? ""));
    const expected: Array<[RegExp, string[], RegExp | null]> = [
      [/^В ожидании/, ["КБ", "Наименование", "Заказано"], null],
      [/^В производстве/, ["КБ", "Наименование", "Заказано", "Запущено", "Можно принять", ...deliveryHeaders], /^Принять 591К\.3 КБ1/],
      [/^Готово/, ["КБ", "Наименование", "Заказано", "Можно принять", ...deliveryHeaders], /^Принять 591К\.3 КБ1/],
      [/^Принято/, ["КБ", "Наименование", "Принято", "Дата приёмки", "ГРН", "Упаковано", "Отгружено", "Не упаковано", "УПД", ...deliveryHeaders], /^Упаковать|^Упаковка|^Печать этикетки|^Данные отправки|^Отгрузка /],
      [/^Упаковано/, ["КБ", "Наименование", "ГРН", "Упаковано", ...deliveryHeaders, "Трек", "PL", "DN", "УПД", "Короба"], /^(Трек|Без УПД) 591К\.3 КБ1|^Документы ОТГ-|^Печать (PL|DN|этикетки)|^Внести УПД/],
      [/^На отгрузку/, ["КБ", "Наименование", "ГРН", "Упаковано", ...deliveryHeaders, "Трек", "PL", "DN", "УПД", "Короба", "Отгрузка"], /^Отгрузить 591К\.3 КБ1|^Документы ОТГ-|^Печать (PL|DN|этикетки)|^Внести УПД/],
      [/^Отгружено/, ["КБ", "Наименование", "ГРН", "Отгружено", ...deliveryHeaders, "Трек", "Дата отгрузки"], /^(Печать этикетки|Данные отправки)/],
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

  it("«Документы»: отправка без PL/DN — только «Сформировать отгрузочные», окно упаковки; печати в строке нет", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", delivered: "4", tasks: [] })];
    shipments = [{ ...packedShipment, pl_number: "", dn_number: "" }];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    const form = await screen.findByRole("button", { name: "Сформировать отгрузочные ОТГ-20261029-01" });
    expect(screen.queryByRole("button", { name: /^Документы ОТГ/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Печать (PL|DN)/ })).not.toBeInTheDocument();
    expect(document.querySelector('td[data-label="PL"]')).toHaveTextContent("не сформирован");
    expect(document.querySelector('td[data-label="DN"]')).toHaveTextContent("не сформирован");
    expect(document.querySelector('td[data-label="Короба"]')).toContainElement(form);
    fireEvent.click(form);
    const dialog = await screen.findByRole("dialog", { name: "Упаковка 591К.3" });
    expect(within(dialog).getByRole("button", { name: "Сформировать отгрузочные" })).toBeInTheDocument();
  });

  it("separate document columns retain shipment identity, UPD links before PL creation and print restrictions", async () => {
    intakeRows = [row({ accepted: "8", packed: "8", remaining: "0", delivered: "8", tasks: [] })];
    shipments = [
      { ...packedShipment, upd_number: "111", upd_url: "/api/shipping/files/one" },
      { ...packedShipment, id: "s2", number: "ОТГ-2", pl_number: "", dn_number: "", upd_number: "222", upd_url: "/api/shipping/files/two" },
    ];
    setRightsPrincipal({ ...viewer, permissions: { shipping: "view", print: "view" } });
    render(<ProductionIntakePage principal={viewer} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    const pl = document.querySelector('td[data-label="PL"]')!;
    const upd = document.querySelector('td[data-label="УПД"]')!;
    expect(pl.querySelector('[data-shipment="s1"]')).toHaveTextContent("ОТГ-20261029-01");
    expect(pl.querySelector('[data-shipment="s2"]')).toHaveTextContent("не сформирован");
    expect(within(upd.querySelector('[data-shipment="s1"]') as HTMLElement).getByRole("link")).toHaveAttribute("href", "/api/shipping/files/one");
    expect(within(upd.querySelector('[data-shipment="s2"]') as HTMLElement).getByRole("link")).toHaveAttribute("href", "/api/shipping/files/two");
    expect(screen.queryByRole("button", { name: /^Печать (PL|DN)/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Сформировать отгрузочные/ })).not.toBeInTheDocument();
    expect(calls.filter((call) => call.method !== "GET")).toEqual([]);
  });

  it("«Документы»: сформировано — кнопка открывает окно, PL и DN печатаются там, УПД — тип, № и ссылка на файл", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", delivered: "4", tasks: [] })];
    shipments = [{ ...packedShipment, upd_type: "БУМАГА", upd_number: "485", upd_url: "https://files/upd-485.pdf", missing_data: [] }];
    const printed = capturePrintFrames();
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    const open = await screen.findByRole("button", { name: "Документы ОТГ-20261029-01" });
    expect(open).toHaveTextContent("Короба…");
    expect(screen.getByRole("button", { name: "Печать PL ОТГ-20261029-01" })).toBeInTheDocument();
    fireEvent.click(open);
    const dialog = await screen.findByRole("dialog", { name: "Документы отправки ОТГ-20261029-01" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Печать PL" }));
    await waitFor(() => expect(printed.print).toHaveBeenCalledTimes(1));
    fireEvent.click(within(dialog).getByRole("button", { name: "Печать DN" }));
    await waitFor(() => expect(printed.print).toHaveBeenCalledTimes(2));
    expect(calls.map((call) => call.url)).toEqual(expect.arrayContaining(["/api/shipping/shipments/s1/print?document=pl", "/api/shipping/shipments/s1/print?document=dn"]));
    expect(within(dialog).getByText("УПД · Бумага № 485")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Открыть / печать" })).toHaveAttribute("href", "https://files/upd-485.pdf");
    expect(within(dialog).getByRole("button", { name: "Перевыпустить" })).toBeInTheDocument();
    printed.restore();
  });

  it("«Документы»: DN выключен, пока не внесены данные; бумажная УПД без файла и УПД без типа — предупреждения", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", delivered: "4", tasks: [] })];
    shipments = [{ ...packedShipment, upd_type: "БУМАГА", upd_number: "", upd_url: "" }];
    const { unmount } = render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    expect(await screen.findByText("нет файла УПД")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Документы ОТГ-20261029-01" }));
    const dialog = await screen.findByRole("dialog", { name: "Документы отправки ОТГ-20261029-01" });
    expect(within(dialog).getByRole("button", { name: "Печать DN" })).toBeDisabled();
    expect(within(dialog).getByText("УПД · Бумага — нет файла")).toBeInTheDocument();
    expect(within(dialog).queryByRole("link", { name: "Открыть / печать" })).not.toBeInTheDocument();
    unmount(); cleanup();
    shipments = [{ ...packedShipment, upd_type: null, missing_data: [] }];
    stub();
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    expect(await screen.findByText("тип УПД не выбран")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Документы ОТГ-20261029-01" }));
    expect(within(await screen.findByRole("dialog", { name: "Документы отправки ОТГ-20261029-01" })).getByText("УПД — тип не выбран")).toBeInTheDocument();
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
