import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProductionIntakePage } from "./ProductionIntakePage";
import { ShipmentCard } from "./ShipmentCard";
import type { DispatchShipment } from "./productionIntakeApi";
import { capturePrintFrames } from "./testPrintFrames";
import { setRightsPrincipal } from "./rights";
import { forgetPersonalViews } from "./personalViews";

const principal = {
  user: { id: "u1", email: "k@example.test", name: "Кладовщик", department: "Склад" },
  roles: ["foreman"], access_level: "foreman", permissions: { shipping_goods: "edit" as const },
};
const viewer = { ...principal, permissions: { shipping: "view" as const } };
const admin = { ...principal, roles: ["admin"], access_level: "admin", permissions: { shipping: "edit" as const, shipping_goods: "edit" as const } };
const shippingHeaders = ["Статус", "Организация", "Получатель", "Телефон", "Адрес", "Способ доставки", "ТК", "Комментарий для склада", "Для перевозчика", "Трек", "Ждём для трека", "УПД", "№ УПД", "Дата УПД", "Файл УПД", "PL", "DN", "Доставка КБ"];

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
  project: "591К.3", version: 2, receiver: "Организация", shipping_point: "Москва", contact: "", phone: "", terms: "САМОВЫВОЗ", carrier: "",
  upd_type: "ЭДО", updated_by: "", updated_at: null, po: "", contract: "", spec: "", invoice: "", pays: "", customer_address: "", hours: "",
  plan_date: null, note: "",
};
const act = (grn: string, quantity: string, received_at = "2026-10-30") => ({ grn, quantity, received_at, location: "" });

const shipmentLine = (over: Record<string, unknown> = {}) => ({ id: "l1", good_id: "g1", project: "591К.3", item_index: "КБ1", article: "", name: "Жгут", requested: "4", picked: "4", shortage: "0", unit: "шт", grn: "ГП.26.0000001", ...over });
const packedShipment = {
  id: "s1", number: "ОТГ-20261029-01", version: 3, created_at: "2026-10-29T09:00:00", status: "Упакована",
  customer: "Заказчик", receiver: "Организация", shipping_point: "Москва", contact: "", phone: "+7 900", terms: "ДОСТАВКА ТК", carrier: "СДЭК",
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
let itemDeliveries: unknown[] = [];
/** Ответы сервера на «Разрешить без УПД» по очереди: [status, body]. */
let approvals: Array<[number, unknown]> = [];

function stub() {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input), method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url === "/api/shipping/intake") return json({ rows: intakeRows });
    if (url === "/api/shipping/shipments" && method === "GET") return json({ shipments });
    if (url.startsWith("/api/shipping/item-addressees?")) return json({ items: itemDeliveries });
    if (url === "/api/shipping/project-addressees") return json({ addressees });
    if (url === "/api/shipping/finished-goods/accept") return json({ goods: [], replayed: false });
    if (url.startsWith("/api/shipping/intake/") && url.endsWith("/label")) return json({ status: "queued", reason: "В очереди принтера.", file: "j1", copies: 1, job_id: "j1" });
    if (url.startsWith("/api/shipping/project-addressee?")) return json({ addressee: dispatchCard });
    if (url === "/api/shipping/project-ship") return json({ shipment: { ...packedShipment, id: "s9", number: "ОТГ-9", pl_number: "PL591К.3-9" }, shipped: false, closed: false, why: "", replayed: false });
    if (url === "/api/shipping/no-upd-approvers") return json({ approvers: [{ id: "p1", name: "Артемий" }, { id: "p2", name: "Зингер" }, { id: "p3", name: "Касаточкин" }] });
    if (url.endsWith("/approve-no-upd")) { const [status, body] = approvals.shift() ?? [200, { shipment: packedShipment }]; return json(body, status); }
    if (/\/print\?document=/.test(url)) return json({ forms: [{ kind: "pl", title: "PL1-1", number: ["PL", "1", "-", "1"], place: "1", places: "1", package: "Короб", dims: "", net: "", gross: "", seal: "",
      rows: [{ part: "КБ1", name: "", quantity: "1", unit: "шт", batch: "", pl: "", boxes: "" }], packed_at: "", shipped_at: "", packer: "", shipper: "", consignee: "", address: "", project: "591К.3" }] });
    if (url.endsWith("/labels")) return json({ labels: [{ shipper: "Фраксис", consignee: "Организация", address: "Москва", project: "591К.3", pl_number: "PL591К.3-1", place: "Упаковка 1/1", items: [{ item_index: "КБ1", quantity: "4", unit: "шт" }], net: "", dims: "" }] });
    if (url.endsWith("/issue-document")) return json({ shipment: packedShipment });
    if (url.endsWith("/documents") && method === "PUT") return json({ shipment: packedShipment });
    if (/\/shipments\/[^/]+\/(ship|close|unship)$/.test(url)) return json({ shipment: packedShipment, closed: false, why: "Нельзя закрыть: не хватает — файл УПД" });
    return json({});
  }));
}
beforeEach(() => { vi.useFakeTimers({ now: new Date(2026, 10, 9, 12), toFake: ["Date"] }); window.localStorage.clear(); forgetPersonalViews(); intakeRows = ROWS; shipments = []; addressees = []; itemDeliveries = []; approvals = []; setRightsPrincipal(null); stub(); });
afterEach(() => { cleanup(); setRightsPrincipal(null); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); window.history.replaceState({}, "", "/"); });

const tab = (name: RegExp) => screen.getByRole("tab", { name });
const count = (name: RegExp) => tab(name).querySelector(".page-count")?.textContent?.trim();
const headers = () => screen.getAllByRole("columnheader").map((cell) => (cell.textContent ?? "").replace(/[^А-Яа-яЁёA-Za-z№ /]/g, "").trim()).filter(Boolean);
const bodyRows = (text = "Жгут") => [...document.querySelectorAll("tbody tr")].filter((tr) => tr.querySelector(".intake-kb") && tr.textContent?.includes(text));
const shipmentRows = () => [...document.querySelectorAll("tr.dt-group-cells")];
const rowButtons = (text = "Жгут") => bodyRows(text).flatMap((tr) => [...tr.querySelectorAll("button[aria-label]")]
  .map((button) => button.getAttribute("aria-label")!)
  .filter((label) => !label.startsWith("Данные отправки") && !label.startsWith("Доставка ") && !/^Печать (PL|DN) /.test(label)));
const cell = (tr: Element, label: string) => (tr.querySelector(`td[data-label="${label}"]`)?.textContent ?? "").replace(/ /g, " ");
const writes = () => calls.filter((call) => call.method !== "GET");

const kbDelivery = (item_index: string, version = 0) => ({
  project: "591К.3", item_index, version, name: "Жгут", overrides: {}, updated_by: "", updated_at: null,
  effective: { receiver: "Получатель КБ", shipping_point: "Адрес КБ", phone: "123", contact: "", terms: "ДОСТАВКА ТК", carrier: "СДЭК", upd_type: "ЭДО" },
});

it("warehouse can set delivery for the whole catalog including a KB before finished goods exist", async () => {
  window.history.replaceState({}, "", "/?tab=accepted");
  intakeRows = [row({ remaining: "0", accepted: "6", delivered: "6", acts: [] })];
  itemDeliveries = [kbDelivery("КБ1", 2), kbDelivery("КБ2")];
  render(<ProductionIntakePage principal={principal} />);
  await screen.findByText("Адрес КБ");
  fireEvent.click(screen.getByRole("button", { name: "Всем КБ проекта 591К.3" }));
  await screen.findByText("Доставка · 591К.3 · 2 КБ");
  fireEvent.change(screen.getByRole("textbox", { name: "Телефон" }), { target: { value: "456" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(writes()).toEqual([expect.objectContaining({ url: "/api/shipping/item-addressees/bulk", body: {
    project: "591К.3", items: [
      { item_index: "КБ1", version: 2, fields: { phone: "456" } },
      { item_index: "КБ2", version: 0, fields: { phone: "456" } },
    ],
  } })]));
});

it("selected receipt rows of the same KB produce one versioned delivery update", async () => {
  window.history.replaceState({}, "", "/?tab=accepted");
  intakeRows = [row({ remaining: "0", accepted: "6", delivered: "6", acts: [act("ГП.1", "2"), act("ГП.2", "4")] })];
  itemDeliveries = [kbDelivery("КБ1", 2)];
  render(<ProductionIntakePage principal={principal} />);
  await screen.findAllByText("Адрес КБ");
  for (const tr of bodyRows()) fireEvent.click(tr.querySelector('input[type="checkbox"]')!);
  fireEvent.click(screen.getByRole("button", { name: "Задать доставку выбранным КБ" }));
  await screen.findByText("Доставка · 591К.3 · КБ1");
  fireEvent.change(screen.getByRole("textbox", { name: "Телефон" }), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(writes()).toEqual([expect.objectContaining({ url: "/api/shipping/item-addressee", body: {
    project: "591К.3", item_index: "КБ1", version: 2, fields: { phone: "" },
  } })]));
});

it.each(["Новая", "Собрана", "Упакована", "Отгружена", "Закрыта", "Отменена"])("shipment head editing follows status %s; documents retain their rules", (status) => {
  const shipment = { ...packedShipment, status, receiver: "Иванов", phone: "+7 123", contact: "Контакт", note: "Позвонить", deadline: "2026-11-12" } as DispatchShipment;
  render(<ShipmentCard shipment={shipment} rights={{ canManage: true, canWarehouse: true, canEdit: true, canUnship: false }}
    busy="" mutate={vi.fn()} openData={vi.fn()} />);
  const details = screen.getByText("Организация, получатель и адрес этой отправки").closest("details")!;
  const active = ["Новая", "Собрана", "Упакована"].includes(status);
  expect(details.querySelectorAll("input").length > 0).toBe(active);
  if (!active) {
    expect(details).toHaveTextContent("Иванов");
    expect(details).toHaveTextContent("+7 123");
    expect(details).toHaveTextContent("Контакт");
    expect(details).toHaveTextContent("Позвонить");
    expect(details).toHaveTextContent("12.11.2026");
  }
  expect(Boolean(screen.queryByRole("button", { name: "Сохранить документы" }))).toBe(!["Закрыта", "Отменена"].includes(status));
});

it("one project contains aligned shipment rows with blank shipment cells on its KB rows", async () => {
  intakeRows = [];
  shipments = [
    { ...packedShipment, id: "later", number: "ОТГ-2", status: "Закрыта", shipped_at: "2026-11-08", receiver: "Иванов", phone: "+7 222", lines: [shipmentLine({ picked: "250" })] },
    { ...packedShipment, id: "earlier", number: "ОТГ-1", status: "Отгружена", shipped_at: "2026-11-03", receiver: "Петров", phone: "+7 111", lines: [shipmentLine({ picked: "100" }), shipmentLine({ id: "l2", item_index: "КБ2", picked: "50" })] },
  ];
  render(<ProductionIntakePage principal={principal} />);
  fireEvent.click(await screen.findByRole("tab", { name: /^Отгружено/ }));
  expect(document.querySelectorAll("tr.dt-parent-row")).toHaveLength(1);
  expect(document.querySelector("tr.dt-parent-row")).toHaveTextContent("591К.3 · Заказчик");
  expect(shipmentRows()).toHaveLength(2);
  expect(shipmentRows().map((tr) => [cell(tr, "Наименование"), cell(tr, "КБ"), cell(tr, "Отгружено"), cell(tr, "Отгружена"), cell(tr, "Организация"), cell(tr, "Телефон")])).toEqual([
    ["ОТГ-2", "Отгрузка 2 из 2", "250 шт", "08.11.2026", "Иванов", "+7 222"],
    ["ОТГ-1", "Отгрузка 1 из 2", "150 шт", "03.11.2026", "Петров", "+7 111"],
  ]);
  expect(bodyRows()).toHaveLength(3);
  for (const position of bodyRows()) for (const title of ["Статус", "Отгружена", "Организация", "Телефон", "Трек", "PL", "DN"])
    expect(cell(position, title)).toBe("");
  for (const title of ["Создана", "Срок", "Этап позиции", "Данные", "Проект", "Заказчик", "Отправка", "Отгрузка"])
    expect(headers()).not.toContain(title);
  fireEvent.click(screen.getByRole("button", { name: /^КБ$/ }));
  expect(shipmentRows().map((tr) => cell(tr, "Наименование"))).toEqual(["ОТГ-2", "ОТГ-1"]);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "ОТГ-2" } });
  expect(shipmentRows()).toHaveLength(1);
  expect(screen.getByText("Отгрузка 2 из 2")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Петров" } });
  expect(bodyRows()).toHaveLength(2);
  expect(shipmentRows()).toHaveLength(1);
  expect(cell(shipmentRows()[0], "Наименование")).toBe("ОТГ-1");
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Фильтры" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Фильтр: Статус" }), { target: { value: "Отгружена" } });
  expect(bodyRows()).toHaveLength(2);
  expect(shipmentRows()).toHaveLength(1);
});

it("all shows unshipped KB directly under its project and keeps its own effective delivery", async () => {
  window.history.replaceState({}, "", "/?tab=all");
  intakeRows = [row({ item_index: "КБ3", needed: "6", delivered: "6", accepted: "6", packed: "0", remaining: "0", tasks: [] })];
  itemDeliveries = [kbDelivery("КБ3")];
  shipments = [packedShipment, { ...packedShipment, id: "s2", number: "ОТГ-2", created_at: "2026-11-01", lines: [shipmentLine({ id: "l2", item_index: "КБ2" })] }];
  render(<ProductionIntakePage principal={principal} />);
  await screen.findByRole("button", { name: "Всем КБ проекта 591К.3" });
  const parent = document.querySelector("tr.dt-parent-row")!;
  expect(document.querySelectorAll("tr.dt-parent-row")).toHaveLength(1);
  expect(shipmentRows()).toHaveLength(2);
  expect(parent.nextElementSibling).toHaveTextContent("КБ3");
  expect(cell(parent.nextElementSibling!, "Организация")).toBe("Получатель КБ");
  expect(cell(parent.nextElementSibling!, "Адрес")).toBe("Адрес КБ");
  expect(bodyRows()).toHaveLength(3);
  expect(screen.queryByText("Данные", { selector: "th" })).not.toBeInTheDocument();
});

it("shipment columns can be enabled without repeating values in the fallback or KB cells", async () => {
  window.history.replaceState({}, "", "/?tab=packed");
  intakeRows = [];
  shipments = [packedShipment];
  render(<ProductionIntakePage principal={principal} />);
  await screen.findByText(packedShipment.number);
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  fireEvent.click(screen.getByRole("checkbox", { name: /^Отправка$/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: /^Отгрузка$/ }));
  expect(cell(shipmentRows()[0], "Отправка")).toBe(packedShipment.number);
  expect(cell(shipmentRows()[0], "Отгрузка")).toBe("Отгрузка 1 из 1");
  expect(cell(shipmentRows()[0], "КБ")).toBe("");
  expect(cell(shipmentRows()[0], "Наименование")).toBe("");
  expect(cell(bodyRows()[0], "Отправка")).toBe("");
  expect(cell(bodyRows()[0], "Отгрузка")).toBe("");
});

it("shipment permission and unknown blockers remain visible once, including pickup shipments", async () => {
  window.history.replaceState({}, "", "/?tab=packed");
  intakeRows = [];
  shipments = [{ ...packedShipment, terms: "САМОВЫВОЗ", carrier: "", missing_data: ["№ УПД"],
    missing_ship: ["№ УПД", "разрешение на отгрузку без УПД", "согласование склада"] }];
  render(<ProductionIntakePage principal={principal} />);
  await screen.findByText(packedShipment.number);
  expect(cell(shipmentRows()[0], "Ждём для трека")).toBe("разрешение на отгрузку без УПД, согласование склада");
  expect(cell(bodyRows()[0], "Ждём для трека")).toBe("");
  expect(screen.getAllByText(/разрешение на отгрузку без УПД, согласование склада/)).toHaveLength(1);
});

it.each(["БУМАГА", "ЭДО", "БЕЗ УПД", null])("a missing UPD file is highlighted only when required by UPD type %s", async (upd_type) => {
  window.history.replaceState({}, "", "/?tab=packed");
  intakeRows = [];
  shipments = [{ ...packedShipment, upd_type, upd_url: "" }];
  render(<ProductionIntakePage principal={principal} />);
  await screen.findByText(packedShipment.number);
  const file = shipmentRows()[0].querySelector('td[data-label="Файл УПД"]')!;
  expect(file).toHaveTextContent(upd_type === "БУМАГА" ? "не заполнено" : "—");
  expect(Boolean(file.querySelector(".tone-warn"))).toBe(upd_type === "БУМАГА");
  expect(cell(bodyRows()[0], "Файл УПД")).toBe("");
});

it("normal Excel exports all position rows beyond the rendered page and follows search", async () => {
  intakeRows = [];
  shipments = [{ ...packedShipment, status: "Отгружена", shipped_at: "2026-11-08",
    lines: Array.from({ length: 201 }, (_, index) => shipmentLine({ id: `l${index}`, item_index: `КБ${index}`, name: index === 200 ? "Особый" : "Жгут", picked: "1" })) }];
  let blob: Blob | null = null;
  vi.spyOn(URL, "createObjectURL").mockImplementation((value) => { blob = value as Blob; return "blob:x"; });
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  render(<ProductionIntakePage principal={principal} />);
  fireEvent.click(await screen.findByRole("tab", { name: /^Отгружено/ }));
  expect(bodyRows()).toHaveLength(200);
  expect(screen.queryByRole("button", { name: "Excel по позициям" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^Excel$/ }));
  let text = new TextDecoder("utf-16le").decode(await blob!.arrayBuffer());
  expect(text.split(packedShipment.number)).toHaveLength(202);
  const [exportHeaders, firstExport] = text.replace(/^\uFEFF/, "").split("\r\n").map((line) => line.split("\t"));
  for (const [title, value] of [["Проект", "591К.3"], ["Заказчик", "Заказчик"], ["Отправка", packedShipment.number], ["Организация", "Организация"],
    ["Телефон", "+7 900"], ["Адрес", "Москва"], ["Создана", "2026-10-29T09:00:00"], ["Отгружена", "2026-11-08"]])
    expect(firstExport[exportHeaders.indexOf(title)]).toBe(value);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Особый" } });
  expect(bodyRows("")).toHaveLength(1);
  expect(bodyRows("")[0]).toHaveTextContent("КБ200");
  expect(cell(shipmentRows()[0], "Отгружено")).toBe("201 шт");
  fireEvent.click(screen.getByRole("button", { name: /^Excel$/ }));
  text = new TextDecoder("utf-16le").decode(await blob!.arrayBuffer());
  expect(text.split(packedShipment.number)).toHaveLength(2);
});

it("project delivery values use the table columns, missing fields are highlighted and address has a tooltip", async () => {
  const address = "Москва, улица Орджоникидзе, дом 11, строение 13, склад готовой продукции";
  intakeRows = [row({}), row({ project: "600К.1", name: "Шлейф" })];
  addressees = [{ ...dispatchCard, terms: "ДОСТАВКА ТК", shipping_point: address, phone: "", carrier: "СДЭК" }];
  render(<ProductionIntakePage principal={principal} />);
  fireEvent.click(await screen.findByRole("tab", { name: /^Принято/ }));
  expect(document.querySelector(".intake-shipping-grid")).toBeNull();
  const positions = bodyRows("");
  expect(positions).toHaveLength(2);
  const phone = positions[0].querySelector('td[data-label="Телефон"] .tone-warn');
  expect(phone).toHaveTextContent("не заполнено");
  expect(positions[0].querySelector('td[data-label="Адрес"] [title]')).toHaveAttribute("title", address);
  for (const tr of positions) for (const label of ["Организация", "Получатель", "Телефон", "Адрес", "Способ доставки", "ТК", "УПД"]) expect(tr.querySelector(`td[data-label="${label}"]`)).not.toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Изменить данные отправки 591К.3" }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
});

it.each([".", "_", "--", "—–−", "самовывоз"])("заполнитель %s не считается телефоном получателя", async (phone) => {
  intakeRows = [row({})];
  addressees = [{ ...dispatchCard, phone }];
  render(<ProductionIntakePage principal={principal} />);
  fireEvent.click(await screen.findByRole("tab", { name: /^Принято/ }));
  const cell = screen.getByRole("group", { name: "Телефон" });
  expect(cell).toHaveClass("tone-warn");
  expect(cell).toHaveTextContent("не заполнено");
});

it.each([false, true])("весь заказ принят: упаковка без разрешения, право менеджера=%s", async (manager) => {
  intakeRows = [row({ needed: "40", accepted: "40", packed: "10", sent: "10", remaining: "0", tasks: [],
    pack_auto_allowed: true, pack_allowed: true, pack_permission_quantity: "40", pack_permission_remaining: "30" })];
  render(<ProductionIntakePage principal={manager ? admin : principal} />);
  fireEvent.click(await screen.findByRole("tab", { name: /^Принято/ }));
  expect(screen.getByText("Весь заказ принят — разрешение не нужно")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Упаковать 591К.3 КБ1: 30" })).toBeEnabled();
  expect(screen.queryByText("Нужно разрешение")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Разрешить упаковку/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Упаковать 591К.3 КБ1: 30" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByRole("checkbox", { name: "Упаковать КБ1" })).toBeEnabled();
  expect(within(dialog).getByRole("spinbutton", { name: "Сколько упаковать КБ1" })).toHaveAttribute("max", "30");
});

it.each([false, true])("исчерпанное разрешение: количества видны, право менеджера=%s", async (manager) => {
  intakeRows = [row({ needed: "2500", accepted: "2500", packed: "1743", sent: "1743", remaining: "0", tasks: [], pack_permission_quantity: "1743", pack_permission_remaining: "0" })];
  render(<ProductionIntakePage principal={manager ? admin : principal} />);
  fireEvent.click(await screen.findByRole("tab", { name: /^Принято/ }));
  expect(screen.getByText("Нужно разрешение")).toBeInTheDocument();
  const position = bodyRows()[0];
  expect(cell(position, "Разрешено по КБ")).toBe("1 743");
  expect(cell(position, "Упаковано по КБ")).toBe("1 743");
  expect(cell(position, "Количество")).toBe("757 шт");
  const button = screen.queryByRole("button", { name: "Разрешить упаковку 591К.3 КБ1" });
  expect(Boolean(button)).toBe(manager);
  if (button) {
    fireEvent.click(button);
    expect(await screen.findByRole("dialog", { name: "Разрешения упаковки 591К.3" })).toBeInTheDocument();
  }
});

it.each(["В ожидании", "В производстве", "Готово", "Принято", "Все"])("%s: склад видит данные проекта и открывает редактирование", async (label) => {
  intakeRows = [row({}), row({ project: "600К.1", stage: "Новый", project_stage: "Новый", accepted: "0", delivered: "0", remaining: "40", tasks: [] })];
  addressees = [{ ...dispatchCard, phone: "+7 999", receiver: "Получатель проекта" }];
  render(<ProductionIntakePage principal={principal} />);
  fireEvent.click(await screen.findByRole("tab", { name: new RegExp(`^${label}`) }));
  const buttons = await screen.findAllByRole("button", { name: /^Изменить данные отправки/ });
  expect(buttons[0]).toBeEnabled();
  fireEvent.click(buttons[0]);
  const dialog = within(await screen.findByRole("dialog"));
  expect(await dialog.findByLabelText("Организация")).toBeEnabled();
});

it.each([
  ["Новый", "В ожидании", false], ["В работе", "В производстве", false], ["ОТК", "Готово", true],
])("%s: подсветка УПД зависит от этапа строки", async (stage, tab, required) => {
  intakeRows = [row({ stage, project_stage: stage, accepted: "0", delivered: "0", remaining: "40", tasks: [] })];
  addressees = [{ ...dispatchCard, upd_type: null }];
  render(<ProductionIntakePage principal={principal} />);
  fireEvent.click(await screen.findByRole("tab", { name: new RegExp(`^${tab}`) }));
  const edit = await screen.findByRole("button", { name: "Изменить данные отправки 591К.3" });
  expect(edit).toBeInTheDocument();
  const upd = bodyRows()[0].querySelector<HTMLElement>('td[data-label="УПД"]')!;
  if (required) expect(within(upd).getByText("не заполнено")).toHaveClass("tone-warn");
  else {
    expect(upd).toHaveTextContent("—");
    expect(upd.querySelector(".tone-warn")).toBeNull();
  }
});

it("у отгруженной отправки нет ручного закрытия; склад редактирует документы", async () => {
  intakeRows = [];
  shipments = [{ ...packedShipment, status: "Отгружена", shipped_at: "2026-11-08", missing_data: ["файл УПД"] }];
  render(<ProductionIntakePage principal={principal} />);
  fireEvent.click(await screen.findByRole("tab", { name: /^Отгружено/ }));
  fireEvent.click(screen.getByRole("button", { name: "Данные отправки ОТГ-20261029-01" }));
  const dialog = within(await screen.findByRole("dialog"));
  expect(dialog.getByRole("combobox", { name: "Тип УПД" })).toBeEnabled();
  expect(dialog.getByRole("button", { name: "Сохранить" })).toBeEnabled();
});

describe("Приёмка с производства: свои колонки и одно действие у каждой вкладки (аудит 06.11)", () => {
  const today = "2026-11-09";
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
      [/^В производстве/, ["КБ", "Наименование", "Заказано", "Запущено", "Остаток", "Принять досрочно", "Задания"], ["Принять досрочно 591К.3 КБ1: 14"]],
      [/^Готово/, ["КБ", "Наименование", "Заказано", "Можно принять", "Задания"], ["Принять 591К.3 КБ1: 6"]],
      [/^Принято/, ["КБ", "Наименование", "Количество", "Упаковать", "ГРН", "Дата приёмки", "Этикетка", "Разрешение на упаковку", "Разрешено по КБ", "Упаковано по КБ"], ["Упаковать 591К.3 КБ1: 10"]],
      [/^Упаковано/, ["КБ", "Наименование", "Упаковано", "Действие", "ГРН"], []],
      [/^На отгрузку/, ["КБ", "Наименование", "Упаковано", "Отгрузка", "ГРН"], ["Отгрузить 591К.3 КБ1"]],
      [/^Отгружено/, ["КБ", "Наименование", "Отгружено", "ГРН", "Действие"], []],
    ];
    for (const [name, heads, buttons] of expected) {
      fireEvent.click(tab(name));
      await waitFor(() => expect(headers()).toEqual([...heads, ...(name.source === "^Отгружено" ? ["Статус", "Отгружена", ...shippingHeaders.slice(1)] : shippingHeaders)]));
      expect(rowButtons()).toEqual(buttons);
    }
    fireEvent.click(tab(/^Принято/));
    expect(cell(bodyRows()[0], "Количество")).toBe("10 шт");
    expect(screen.getByRole("button", { name: "Упаковать 591К.3 КБ1: 10" })).toHaveTextContent("Упаковать · 10");
    fireEvent.click(tab(/^Отгружено/));
    expect(screen.getByText("ГП.26.0000003")).toBeInTheDocument();
    expect(screen.getByText("TRK-0")).toBeInTheDocument();
    fireEvent.click(tab(/^В ожидании/));
    expect(await screen.findByText("В ожидании производства ничего нет.")).toBeInTheDocument();
    fireEvent.click(tab(/^Все/));
    await waitFor(() => expect(headers()).toEqual(["КБ", "Наименование", "Количество", "Заказано", "Запущено", "Остаток", "Действие", "ГРН", "Дата приёмки", "Этикетка", "Задания", "Разрешение на упаковку", "Разрешено по КБ", "Упаковано по КБ", ...shippingHeaders]));
    expect(rowButtons().sort()).toEqual(["Принять досрочно 591К.3 КБ1: 14", "Принять 591К.3 КБ1: 6", "Упаковать 591К.3 КБ1: 10", "Отгрузить 591К.3 КБ1"].sort());
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
      ["ГП.26.0000200", "28.10.2026", "400 шт"], ["ГП.26.0000201", "30.10.2026", "473 шт"]]);
    // Разрешение КБ (450) делится по строкам: старый акт — первым; сумма кнопок не больше разрешения.
    expect(rowButtons("КБ1").filter((name) => name.startsWith("Упаковать"))).toEqual(["Упаковать 591К.3 КБ1: 400", "Упаковать 591К.3 КБ1: 50"]);
    const kb2 = bodyRows("Жгут три партии");
    expect(kb2.map((tr) => [cell(tr, "ГРН"), cell(tr, "Количество")])).toEqual([["ГП.ДЕМО.2", "214 шт"]]);
    expect(rowButtons("Жгут три партии")).toEqual(["Упаковать 591К.3 КБ2: 214", "Печать этикетки ГП.ДЕМО.2"]);
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
    expect(bodyRows().map((tr) => [cell(tr, "ГРН"), cell(tr, "Количество")])).toEqual([["ГП.СТАРЫЙ", "10 шт"], ["ГП.НОВЫЙ", "10 шт"]]);
  });

  it("a shipment of two KB has two immediate pack buttons and one aligned set of shipping cells", async () => {
    window.history.replaceState({}, "", "/?tab=accepted");
    intakeRows = [
      row({ needed: "20", accepted: "20", packed: "20", remaining: "0", pack_decided: true, pack_permission_remaining: "100", acts: [act("ГП.26.0000501", "20")] }),
      row({ item_index: "КБ2", good_id: "g2", needed: "20", accepted: "20", packed: "20", remaining: "0", pack_decided: true, pack_permission_remaining: "100", acts: [act("ГП.26.0000502", "20")] }),
    ];
    shipments = [{ ...packedShipment, status: "Собрана", upd_number: "У-7", lines: [
      shipmentLine({ picked: "20", requested: "20", grn: "ГП.26.0000501" }),
      shipmentLine({ id: "l2", good_id: "g2", item_index: "КБ2", picked: "20", requested: "20", grn: "ГП.26.0000502" }),
    ] }];
    render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findByRole("button", { name: "Упаковать 591К.3 КБ1: 20" })).toHaveTextContent("Упаковать · 20");
    expect(screen.getByRole("button", { name: "Упаковать 591К.3 КБ2: 20" })).toHaveTextContent("Упаковать · 20");
    expect(bodyRows()).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /Раскрыть/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/· раскрыть/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Excel по позициям" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Короба и PL/DN" })).toBeInTheDocument();
    for (const position of bodyRows()) {
      expect(["Организация", "Получатель", "Телефон", "Адрес", "ТК", "Трек", "№ УПД", "PL", "DN"].map((label) => cell(position, label))).toEqual(Array(9).fill(""));
      expect(position.querySelector("small")).toBeNull();
    }
    expect(["Организация", "Получатель", "Телефон", "Адрес", "ТК", "Трек", "№ УПД", "PL", "DN"].map((label) => cell(shipmentRows()[0], label))).toEqual([
      "Организация", "—", "+7 900", "Москва", "СДЭК", "не заполнено", "У-7", "PL591К.3-1", "DN591К.3-1",
    ]);
    expect(cell(shipmentRows()[0], "Количество")).toBe("40 шт");
    expect(document.querySelector("tr[aria-expanded]")).toBeNull();
  });

  it("набранное в отправку и остаток на складе — каждый со своими штуками, без «Упаковать · 20» на обоих", async () => {
    intakeRows = [row({ needed: "20", accepted: "20", packed: "5", remaining: "0", pack_decided: true, pack_permission_remaining: "100", acts: [act("ГП.26.0000500", "20")] })];
    shipments = [{ ...packedShipment, status: "Собрана", pl_number: "", dn_number: "", lines: [shipmentLine({ picked: "5", requested: "5", grn: "ГП.26.0000500" })] }];
    render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findByRole("tab", { name: /^Принято/ })).toHaveAttribute("aria-selected", "true");
    expect(bodyRows().map((tr) => cell(tr, "Количество"))).toEqual(["15 шт", "5 шт"]);
    expect(shipmentRows()[0]).toHaveTextContent("5 шт");
    expect(rowButtons().filter((name) => name.startsWith("Упаковать"))).toEqual(["Упаковать 591К.3 КБ1: 15", "Упаковать 591К.3 КБ1: 5"]);
    expect(screen.queryByRole("button", { name: /Упаковать.*: 20$/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Короба и PL/DN" })).toBeInTheDocument();
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
    expect(headers()).toEqual(["КБ", "Наименование", "Заказано", ...shippingHeaders]);
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

  it("«Упаковано»: ответ без `no_upd_offered` у ЭДО без номера — выключенный «Трек…»; сервер предлагает «Без УПД…» — кнопка с ПИН", async () => {
    intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", delivered: "4", tasks: [] })];
    shipments = [packedShipment];
    const first = render(<ProductionIntakePage principal={principal} />);
    const off = await screen.findByRole("button", { name: "Трек ОТГ-20261029-01" });
    expect(tab(/^Упаковано/)).toHaveAttribute("aria-selected", "true");
    expect(off).toBeDisabled();
    expect(off.getAttribute("title")).toContain("не хватает: № УПД");
    expect(screen.queryByRole("button", { name: "Без УПД ОТГ-20261029-01" })).not.toBeInTheDocument();
    expect(rowButtons()).toEqual([]);
    first.unmount();

    // Сервер решает сам (`no_upd_offered`): не предлагает — выключенный «Трек…», даже если слова пробелов похожи.
    shipments = [{ ...packedShipment, missing_ship_codes: ["upd_number", "track_number"], no_upd_offered: false }];
    const second = render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findByRole("button", { name: "Трек ОТГ-20261029-01" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Без УПД ОТГ-20261029-01" })).not.toBeInTheDocument();
    // Пропуск УПД виден в своей ячейке и не дублируется текстовым перечнем.
    expect(cell(shipmentRows()[0], "№ УПД")).toBe("не заполнено");
    expect(cell(shipmentRows()[0], "Ждём для трека")).toBe("—");
    second.unmount();

    shipments = [{ ...packedShipment, upd_type: null, missing_ship: ["тип УПД", "трек-номер"], missing_ship_codes: ["upd_type", "track_number"], no_upd_offered: true }];
    approvals = [[403, { detail: "Неверный разрешительный ПИН." }], [200, { shipment: packedShipment }]];
    render(<ProductionIntakePage principal={principal} />);
    expect(await screen.findByRole("button", { name: "Без УПД ОТГ-20261029-01" })).toBeInTheDocument();
    expect(rowButtons()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Без УПД ОТГ-20261029-01" }));
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
    // «Трек…» — в строке самой отправки; у КБ-строк кнопки нет.
    expect(rowButtons()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Трек ОТГ-20261029-02" }));
    const dialog = await screen.findByRole("dialog", { name: "Трек ОТГ-20261029-02" });
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
    await screen.findByRole("button", { name: "Трек ОТГ-20261029-01" });
    expect(screen.queryByRole("button", { name: /^(Документы|Сформировать отгрузочные) / })).not.toBeInTheDocument();
    expect(cell(shipmentRows()[0], "PL")).toContain("PL591К.3-1");
    expect(cell(shipmentRows()[0], "DN")).toContain("DN591К.3-1");
    // PL печатается прямо из строки, как раньше из шапки отправки.
    fireEvent.click(screen.getByRole("button", { name: "Печать PL PL591К.3-1" }));
    await waitFor(() => expect(calls.some((call) => /\/shipments\/s1\/print\?document=pl/.test(call.url))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Короба и PL/DN" }));
    const dialog = await screen.findByRole("dialog", { name: "Упаковка 591К.3" });
    const card = within(within(dialog).getByRole("article", { name: "Отправка ОТГ-20261029-01" }));
    fireEvent.click(card.getByRole("button", { name: "Печать PL" }));
    await waitFor(() => expect(printed.print).toHaveBeenCalledTimes(2));
    fireEvent.click(card.getByRole("button", { name: "Печать этикеток" }));
    await waitFor(() => expect(printed.print).toHaveBeenCalledTimes(3));
    expect(card.getByRole("button", { name: "Перевыпустить" })).toBeInTheDocument();
    printed.restore();
  });

  it("отправка без трека: «не нужен», на «На отгрузку» сама; «Трек…» только у ТК с треком и только в строке отправки", async () => {
  intakeRows = [row({ accepted: "8", packed: "8", remaining: "0", delivered: "8" })];
  const ready = { ...packedShipment, id: "y1", number: "ОТГ-Я", carrier: "Яндекс Доставка", track_required: false, upd_number: "У-1", missing_data: [], missing_ship: [], ready_for_track: true, ready_to_ship: true };
  const needing = { ...packedShipment, id: "c1", number: "ОТГ-С", upd_number: "У-2", track_required: true, missing_data: [], missing_ship: ["трек-номер"], ready_for_track: true };
  shipments = [ready, needing];
  render(<ProductionIntakePage principal={principal} />);
  fireEvent.click(await screen.findByRole("tab", { name: /^На отгрузку/ }));
  const yandex = (await screen.findByText("ОТГ-Я")).closest("tr")!;
  expect(cell(yandex, "Трек")).toBe("не нужен");
  expect(within(yandex).queryByRole("button", { name: /^Трек / })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Отгрузить 591К.3 КБ1" })).toBeInTheDocument();
  fireEvent.click(tab(/^Упаковано/));
  const sdek = (await screen.findByText("ОТГ-С")).closest("tr")!;
  expect(cell(sdek, "Трек").trim()).toBe("не заполнено");
  expect(within(sdek).getByRole("button", { name: "Трек ОТГ-С" })).toBeEnabled();
  expect(rowButtons()).toEqual([]);
});

it("PL и DN по отдельности: без номера — «Сформировать» своего документа через API, с номером — ссылка", async () => {
  intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", delivered: "4", tasks: [] })];
  shipments = [{ ...packedShipment, status: "Собрана", pl_number: "", dn_number: "DN591К.3-1", ready_to_ship: false }];
  render(<ProductionIntakePage principal={principal} />);
  fireEvent.click(await screen.findByRole("tab", { name: /^Все/ }));
  const head = (await screen.findByText("ОТГ-20261029-01")).closest("tr")!;
  expect(screen.queryByRole("button", { name: "Сформировать DN ОТГ-20261029-01" })).not.toBeInTheDocument();
  expect(cell(head, "DN")).toContain("DN591К.3-1");
  fireEvent.click(screen.getByRole("button", { name: "Сформировать PL ОТГ-20261029-01" }));
  await waitFor(() => expect(writes().map((call) => [call.url, call.body])).toContainEqual(["/api/shipping/shipments/s1/issue-document", { version: 3, document: "pl" }]));
});

it("ЭДО без № (владелец 20.11): сервер предлагает «Без УПД…» и у упакованной, и у набранной с DN; без DN — нет", async () => {
  intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", delivered: "4", tasks: [] })];
  const offered = { ...packedShipment, missing_data: ["№ УПД"], missing_ship_codes: ["upd_number", "track_number"], no_upd_offered: true };
  shipments = [offered];
  const packed = render(<ProductionIntakePage principal={principal} />);
  expect(await screen.findByRole("button", { name: "Без УПД ОТГ-20261029-01" })).toBeInTheDocument();
  packed.unmount();

  shipments = [{ ...offered, status: "Собрана", pl_number: "", dn_number: "DN591К.3-1" }];
  const assembled = render(<ProductionIntakePage principal={principal} />);
  fireEvent.click(await screen.findByRole("tab", { name: /^Все/ }));
  expect(await screen.findByRole("button", { name: "Без УПД ОТГ-20261029-01" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Трек ОТГ-20261029-01" })).not.toBeInTheDocument();
  assembled.unmount();

  shipments = [{ ...offered, status: "Собрана", pl_number: "", dn_number: "" }];
  render(<ProductionIntakePage principal={principal} />);
  fireEvent.click(await screen.findByRole("tab", { name: /^Все/ }));
  await screen.findByText("ОТГ-20261029-01");
  expect(screen.queryByRole("button", { name: "Без УПД ОТГ-20261029-01" })).not.toBeInTheDocument();
});

it("DN «Сформировать» зовёт выдачу DN у упакованной отправки без DN", async () => {
  intakeRows = [row({ accepted: "4", packed: "4", remaining: "0", delivered: "4", tasks: [] })];
  shipments = [{ ...packedShipment, dn_number: "" }];
  render(<ProductionIntakePage principal={principal} />);
  fireEvent.click(await screen.findByRole("button", { name: "Сформировать DN ОТГ-20261029-01" }));
  await waitFor(() => expect(writes().map((call) => [call.url, call.body])).toContainEqual(["/api/shipping/shipments/s1/issue-document", { version: 3, document: "dn" }]));
});

it("склад видит общие данные и редактирует карточки проекта и отправки", async () => {
    intakeRows = [row({ accepted: "20", packed: "8", remaining: "0", tasks: [] })];
    addressees = [{ ...dispatchCard, receiver: "Общий получатель", note: "Склад проекта" }];
    shipments = [
      { ...packedShipment, receiver: "Получатель А", shipping_point: "Адрес А", phone: "+7 А", track_number: "TRK-A", note: "На рампу А", lines: [shipmentLine({ picked: "4" })] },
      { ...packedShipment, id: "s2", number: "ОТГ-2", receiver: "Получатель Б", shipping_point: "Адрес Б", carrier: "Яндекс Доставка", phone: "+7 Б", track_number: "TRK-B", note: "На рампу Б", lines: [shipmentLine({ id: "l2", picked: "4" })] },
    ];
    render(<ProductionIntakePage principal={principal} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Упаковано/ }));
    const [a, b] = ["ОТГ-20261029-01", "ОТГ-2"].map((number) => screen.getByText(number).closest("tr")!);
    for (const text of ["Получатель А", "Адрес А", "СДЭК", "TRK-A"]) expect(a).toHaveTextContent(text);
    for (const text of ["Получатель Б", "Адрес Б", "Яндекс Доставка", "TRK-B"]) expect(b).toHaveTextContent(text);
    expect(cell(a, "УПД")).toContain("ЭДО");
    expect(cell(a, "Комментарий для склада")).toBe("На рампу А");
    expect(screen.queryByText("Общий получатель")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Данные отправки ОТГ/ })).toHaveLength(2);

    fireEvent.click(tab(/^Принято/));
    fireEvent.click(await screen.findByRole("button", { name: "Изменить данные отправки 591К.3" }));
    const card = await screen.findByRole("dialog", { name: "Данные отправки 591К.3" });
    expect(await within(card).findByLabelText("Организация")).toBeEnabled();
    expect(within(card).getByRole("button", { name: "Сохранить" })).toBeInTheDocument();
    fireEvent.click(within(card).getByRole("button", { name: "Закрыть" }));

    fireEvent.click(tab(/^Упаковано/));
    fireEvent.click((await screen.findAllByRole("button", { name: "Короба и PL/DN" }))[0]);
    const pack = await screen.findByRole("dialog", { name: "Упаковка 591К.3" });
    const shipCard = within(within(pack).getByRole("article", { name: "Отправка ОТГ-20261029-01" }));
    expect(shipCard.getByRole("button", { name: "Сохранить документы" })).toBeInTheDocument();
    expect(shipCard.getByLabelText("Загрузить УПД ОТГ-20261029-01")).toBeEnabled();
    expect(shipCard.getByText("Организация, получатель и адрес этой отправки")).toBeInTheDocument();
    fireEvent.click(shipCard.getByRole("button", { name: "Данные отправки" }));
    const data = await screen.findByRole("dialog", { name: "Данные отправки ОТГ-20261029-01" });
    expect(within(data).getByRole("button", { name: "Сохранить" })).toBeInTheDocument();
    expect(writes()).toEqual([]);
  });

  it("«На отгрузку»: «Отгрузить» открывает отправки проекта; отгрузка — по готовой отправке", async () => {
    intakeRows = [multi()];
    shipments = [packedShipment, ready, gone];
    render(<ProductionIntakePage principal={admin} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^На отгрузку/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Отгрузить 591К.3 КБ1" }));
    const dialog = await screen.findByRole("dialog", { name: "Отгрузка 591К.3" });
    expect(within(within(dialog).getByRole("article", { name: "Отправка ОТГ-20261029-03" })).queryByRole("button", { name: "Закрыть" })).not.toBeInTheDocument();
    expect(within(within(dialog).getByRole("article", { name: "Отправка ОТГ-20261029-01" })).getByRole("button", { name: "Отгрузить" })).toBeDisabled();
    fireEvent.click(within(within(dialog).getByRole("article", { name: "Отправка ОТГ-20261029-02" })).getByRole("button", { name: "Отгрузить" }));
    expect(await within(dialog).findByText(/^Отгружено: ОТГ-20261029-02\. Не закрыта — не хватает: файл УПД/)).toBeInTheDocument();
    expect(calls.find((call) => call.url === "/api/shipping/shipments/s2/ship")?.body).toEqual({ version: 3 });
  });

  it("уехавшая целиком КБ видна в «Отгружено» из отправок за 30 дней — без кнопок", async () => {
    intakeRows = [];
    shipments = [{ ...gone, lines: [shipmentLine({ id: "l9", item_index: "КБ9", name: "Уехавший жгут", picked: "5", requested: "5" })] }];
    render(<ProductionIntakePage principal={admin} />);
    await screen.findByText("ОТГ-20261029-03");
    expect(tab(/^Отгружено/)).toHaveAttribute("aria-selected", "true");
    expect(rowButtons()).toEqual([]);
    expect(shipmentRows()[0]).toHaveTextContent("5 шт");
    const line = screen.getByText("Уехавший жгут").closest("tr")!;
    expect(line.querySelectorAll('td[data-label="Действие"] button')).toHaveLength(0);
    expect(line).toHaveTextContent("5 шт");
    expect(line).toHaveTextContent("ГП.26.0000001");
    expect(screen.getByText("ОТГ-20261029-03")).toBeInTheDocument();
  });

  it("одинаковые КБ в одной отправке не склеиваются, порядок КБ — естественный", async () => {
    intakeRows = [];
    shipments = [{ ...packedShipment, status: "Отгружена", shipped_at: today, lines: [
      shipmentLine({ id: "ten", item_index: "КБ10", picked: "10" }), shipmentLine({ id: "two-a", item_index: "КБ2", picked: "1000" }),
      shipmentLine({ id: "one", item_index: "КБ1", picked: "1" }), shipmentLine({ id: "two-b", item_index: "КБ2", picked: "1000" }),
    ] }];
    render(<ProductionIntakePage principal={viewer} />);
    fireEvent.click(await screen.findByRole("tab", { name: /^Отгружено/ }));
    expect(bodyRows().map((tr) => cell(tr, "КБ"))).toEqual(["КБ1", "КБ2", "КБ2", "КБ10"]);
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
    fireEvent.change(screen.getByRole("searchbox", { name: "Поиск: Приёмка с производства" }), { target: { value: "ГП.26.0000044" } });
    expect(shipmentRows()).toHaveLength(1);
    expect(shipmentRows()[0]).toHaveTextContent("ОТГ-2");
    expect(bodyRows().map((tr) => cell(tr, "ТК"))).toEqual([""]);
    let blob: Blob | null = null;
    const create = vi.spyOn(URL, "createObjectURL").mockImplementation((value) => { blob = value as Blob; return "blob:intake"; });
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    try {
      fireEvent.click(screen.getByRole("button", { name: /^Excel$/ }));
      const text = new TextDecoder("utf-16le").decode(new Uint8Array(await blob!.arrayBuffer()).slice(2));
      const [heads, values] = text.split("\r\n").map((line) => line.split("\t"));
      expect(values[heads.indexOf("ГРН")]).toBe("ГП.26.0000043, ГП.26.0000044");
    } finally { create.mockRestore(); revoke.mockRestore(); click.mockRestore(); }
  });

  it("без права на склад — список виден, действий и печати нет; заголовок проекта с заказчиком", async () => {
    intakeRows = [...ROWS, row({ item_index: "КБ3", name: "Жгут принятый", accepted: "10", remaining: "0", acts: [act("ГП.26.0000031", "10")] })];
    render(<ProductionIntakePage principal={viewer} />);
    await screen.findByText("Кабель");
    expect(screen.getByRole("button", { name: "Изменить данные отправки 591К.3" }).closest(".ship-group-head")).toHaveTextContent("Заказчик");
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


it("a shipment search link opens the journal filtered by shipment number", async () => {
  shipments = [packedShipment, { ...packedShipment, id: "s2", number: "ОТГ-20261029-02" }];
  window.history.replaceState({}, "", `/?section=intake&tab=journal&q=${encodeURIComponent(packedShipment.number)}`);
  render(<ProductionIntakePage principal={viewer} />);
  const journal = await screen.findByRole("dialog", { name: "Журнал отгрузок" });
  expect((await within(journal).findAllByText(packedShipment.number)).length).toBeGreaterThan(0);
  expect(within(journal).queryByText("ОТГ-20261029-02")).toBeNull();
});

it("a finished goods search link seeds the intake list on the all tab", async () => {
  window.history.replaceState({}, "", "/?section=intake&tab=all&q=600К.1");
  render(<ProductionIntakePage principal={viewer} />);
  expect(await screen.findByText("Шлейф")).toBeInTheDocument();
  expect(screen.queryByText("Жгут")).toBeNull();
});


it("loads a linked older shipment outside the journal list, including cancelled empty ones", async () => {
  window.history.replaceState({}, "", "/?section=intake&tab=journal&shipment=old&q=ОТГ-СТАРАЯ");
  const old = { ...packedShipment, id: "old", number: "ОТГ-СТАРАЯ", status: "Отменена", lines: [] };
  const baseFetch = vi.mocked(globalThis.fetch).getMockImplementation()!;
  vi.stubGlobal("fetch", vi.fn((input, init) => String(input).includes("?shipment=old")
    ? Promise.resolve(json({ shipments: [old] })) : baseFetch(input, init)));
  render(<ProductionIntakePage principal={viewer} />);
  const journal = await screen.findByRole("dialog", { name: "Журнал отгрузок" });
  expect(await within(journal).findByText("ОТГ-СТАРАЯ")).toBeInTheDocument();
  expect(within(journal).getByText("Отменена", { selector: ".status-chip" })).toBeInTheDocument();
  expect(within(journal).getByText(/В отгрузке нет позиций/)).toBeInTheDocument();
});
