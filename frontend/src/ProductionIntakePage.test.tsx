import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { Principal } from "./api";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProductionIntakePage } from "./ProductionIntakePage";
import { setRightsPrincipal } from "./rights";
import { forgetPersonalViews } from "./personalViews";

const base = { user: { id: "u1", email: "k@example.test", name: "Сотрудник", department: "Склад" }, roles: ["foreman"], access_level: "foreman" };
const warehouse = { ...base, permissions: { shipping_goods: "edit" as const } };
const manager = { ...base, permissions: { shipping: "edit" as const } };
const viewer = { ...base, permissions: { shipping: "view" as const } };

const row = (over: Record<string, unknown>) => ({
  project: "591К.3", customer: "Заказчик", deadline: "2026-11-12", item_index: "КБ1", name: "Жгут", unit: "шт", stage: "В работе",
  project_stage: "В работе", pack_decided: false, needed: "40", launched: "40", delivered: "26", accepted: "6", packed: "0", sent: "0", remaining: "20",
  good_id: "g1", good_version: 1, grn: "ГП.26.0000001", location: "", tasks: [{ number: "ПЗ-0001", status: "Запущено", site: "ЖУК", quantity: 40, delivered: "26" }], ...over,
});
/** 591К.3 КБ1: цех сдал 20, принято 6 (на складе 6, упаковать можно 6); 600К.1 «Новый» — ждёт запуска. */
const ROWS = [
  row({ pack_permission_remaining: "6", pack_permission_quantity: "6" }),
  row({ project: "600К.1", customer: "Ожидающий", item_index: "КБ7", name: "Шлейф", stage: "Новый", project_stage: "Новый", needed: "8", launched: "0", delivered: "0", accepted: "0", remaining: "8", good_id: null, tasks: [] }),
  row({ project: "710К.2", customer: "Без разрешения", item_index: "КБ2", name: "Кабель", needed: "10", launched: "10", delivered: "10", accepted: "10", remaining: "0", tasks: [] }),
];
const line = (over: Record<string, unknown> = {}) => ({ id: "l1", good_id: "g1", project: "828К.", item_index: "КБ3", article: "", name: "Жгут для ТМЛР", requested: "45", picked: "45", shortage: "0", unit: "шт", grn: "ГП.26.0000126", ...over });
const shipment = (over: Record<string, unknown> = {}) => ({
  id: "s1", number: "ОТГ-20261124-01", version: 3, created_at: "2026-11-05T09:00:00", status: "Упакована", customer: "ООО «НТЦ Альбатрос»", receiver: "", shipping_point: "",
  contact: "", phone: "", terms: null, carrier: "", deadline: "2026-12-02", upd_type: null, upd_number: "", upd_date: null, upd_url: "", pl_number: "PL828К.-1", dn_number: "DN828К.-1",
  track_number: "", packed_at: "2026-11-05", shipped_at: null, manager: "", note: "", tk_note: "", places: 1, weight: "", boxes: [], inherited: [],
  missing_data: ["способ доставки", "адрес"], missing_ship: ["способ доставки", "адрес", "тип УПД"], ready_for_track: false, ready_to_ship: false,
  lines: [line()], ...over,
});
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
let calls: Array<{ url: string; method: string; body: unknown }> = [];
let intakeRows: unknown[] = ROWS;
let shipments: unknown[] = [];

function stub() {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input), method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url === "/api/shipping/intake") return json({ rows: intakeRows });
    if (url === "/api/shipping/shipments" && method === "GET") return json({ shipments });
    if (url.startsWith("/api/shipping/item-addressees?")) return json({ items: [] });
    if (url === "/api/shipping/project-addressees") return json({ addressees: [] });
    if (url.endsWith("/head")) return json({ shipment: shipment({ version: 4, contact: "Петров" }) });
    if (url.endsWith("/issue-document")) return json({ shipment: shipment() });
    if (/\/shipments\/[^/]+\/ship$/.test(url)) return json({ shipment: shipment(), closed: false, why: "" });
    return json({});
  }));
}
beforeEach(() => { vi.useFakeTimers({ now: new Date(2026, 10, 9, 12), toFake: ["Date"] }); window.localStorage.clear(); forgetPersonalViews(); intakeRows = ROWS; shipments = []; setRightsPrincipal(null); stub(); });
afterEach(() => { cleanup(); setRightsPrincipal(null); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); window.history.replaceState({}, "", "/"); });

const column = (name: string) => screen.getByRole("region", { name });
const cardsIn = (name: string) => [...column(name).querySelectorAll(".board-card")] as HTMLElement[];
const writes = () => calls.filter((call) => call.method !== "GET");
const load = async (principal: Principal = warehouse) => { render(<ProductionIntakePage principal={principal} />); await screen.findByRole("region", { name: "Производство" }); };


const SHIPMENTS = () => [
  shipment(),
  shipment({ id: "s2", pl_number: "PL711К.3-1", dn_number: "DN711К.3-1", ready_to_ship: true, missing_data: [], missing_ship: [], carrier: "ПЭК", terms: "ДОСТАВКА ТК", track_number: "10482377165", customer: "АО «Радиоком»",
    lines: [line({ id: "l2", project: "711К.3", item_index: "КБ1" })] }),
  shipment({ id: "s3", pl_number: "PL533К.11-2", status: "Закрыта", shipped_at: "2026-11-05", carrier: "СДЭК", terms: "ДОСТАВКА ТК", customer: "ООО «МЕГАТРОН»",
    lines: [line({ id: "l3", project: "533К.11", item_index: "КБ2", name: "Жгут питания" })], missing_data: [], missing_ship: [] }),
  shipment({ id: "s4", pl_number: "", dn_number: "", status: "Собрана", customer: "Набранный", deadline: null, lines: [line({ id: "l4", project: "690К.1", item_index: "КБ1" })] }),
];

describe("доска по стадиям", () => {
  it("каждая колонка рисует свои карточки: проект · заказчик, КБ с именем, доставка и срок", async () => {
    shipments = SHIPMENTS();
    await load();
    const production = cardsIn("Производство");
    expect(production).toHaveLength(1);
    expect(production[0].textContent).toContain("591К.3");
    expect(production[0].textContent).toContain("КБ1 Жгут");
    expect(production[0].textContent).toContain("срок 12.11");
    expect(production[0].textContent).toContain("сдано цехом 20");
    // «В ожидании» свёрнута по умолчанию: только счётчик, карточки открываются шапкой группы.
    expect(within(column("Производство")).getByRole("button", { name: /В ожидании · 1 проект/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("КБ7 Шлейф")).toBeNull();
    fireEvent.click(within(column("Производство")).getByRole("button", { name: /В ожидании/ }));
    expect(screen.getByText("КБ7 Шлейф")).toBeInTheDocument();
    expect(cardsIn("Принято").map((card) => card.textContent)).toEqual([expect.stringContaining("591К.3"), expect.stringContaining("710К.2"), expect.stringContaining("690К.1")]);
    const packed = cardsIn("Упаковано");
    expect(packed).toHaveLength(1);
    expect(packed[0].textContent).toContain("828К.");
    expect(packed[0].textContent).toContain("ООО «НТЦ Альбатрос»");
    expect(packed[0].textContent).toContain("КБ3 Жгут для ТМЛР");
    expect(packed[0].textContent).toContain("доставка не выбрана · срок 02.12");
    expect(packed[0].textContent).toContain("PL828К.-1");
    expect(cardsIn("На отгрузку")[0].textContent).toContain("ПЭК · трек 10482377165");
    expect(cardsIn("Отгружено")[0].textContent).toContain("СДЭК · отгружено 05.11");
    expect(cardsIn("Отгружено")[0].textContent).toContain("✓ закрыта");
  });

  it("нет общего количества и «ОТГ» ни на доске, ни в окне карточки", async () => {
    shipments = SHIPMENTS();
    await load(manager);
    const text = () => document.body.textContent ?? "";
    expect(text()).not.toMatch(/ОТГ/);
    expect(cardsIn("Упаковано")[0].textContent).not.toMatch(/\d+\s*шт/);
    fireEvent.click(within(cardsIn("Упаковано")[0]).getByRole("button", { name: /^Открыть 828К\./ }));
    expect(await screen.findByRole("dialog", { name: /Карточка 828К\. · КБ3 · PL828К\.-1/ })).toBeInTheDocument();
    expect(text()).not.toMatch(/ОТГ/);
    expect(document.querySelector("[aria-label*='ОТГ']")).toBeNull();
  });

  it("блокер называет, чего нет и кто вносит; кнопка — по первому пробелу", async () => {
    shipments = SHIPMENTS();
    await load(warehouse);
    const packed = cardsIn("Упаковано")[0];
    expect(packed.querySelector(".board-card-blocker")!.textContent).toBe("⚠ нет: способ доставки, адрес, тип УПД — вносит склад или менеджер");
    expect(within(packed).getByRole("button", { name: "Внести УПД…" })).toBeInTheDocument();
    // Принято: нет разрешения упаковки — склад может упаковать лишь разрешённое, а разрешает менеджер.
    const noPermit = cardsIn("Принято").find((card) => card.textContent!.includes("710К.2"))!;
    expect(noPermit.querySelector(".board-card-blocker")!.textContent).toBe("⚠ КБ2: нет разрешения упаковки — даёт менеджер (раздел «Отгрузка»)");
    expect(within(noPermit).queryByRole("button", { name: /Разрешить упаковку/ })).toBeNull();
    const assembled = cardsIn("Принято").find((card) => card.textContent!.includes("690К.1"))!;
    expect(assembled.querySelector(".board-card-blocker")!.textContent).toContain("нет: PL — не сформирован");
  });

  it("кнопки следующей стадии по ролям: склад принимает и упаковывает, менеджер разрешает упаковку", async () => {
    shipments = SHIPMENTS();
    await load(warehouse);
    expect(within(cardsIn("Производство")[0]).getByRole("button", { name: "Принять на склад…" })).toBeInTheDocument();
    expect(within(cardsIn("Принято")[0]).getByRole("button", { name: "Упаковать КБ1…" })).toBeInTheDocument();
    expect(within(cardsIn("На отгрузку")[0]).getByRole("button", { name: "Отгрузить" })).toBeEnabled();
    cleanup();
    await load(manager);
    expect(within(cardsIn("Производство")[0]).queryByRole("button", { name: "Принять на склад…" })).toBeNull();
    const noPermit = cardsIn("Принято").find((card) => card.textContent!.includes("710К.2"))!;
    expect(within(noPermit).getByRole("button", { name: "Разрешить упаковку…" })).toBeInTheDocument();
    expect(within(cardsIn("На отгрузку")[0]).getByRole("button", { name: "Отгрузить" })).toBeDisabled();
    expect(cardsIn("На отгрузку")[0].textContent).toContain("отгружает склад");
    cleanup();
    await load(viewer);
    expect(document.querySelectorAll(".board-card-actions button.primary")).toHaveLength(0);
  });

  it("«Принять на склад…» открывает окно приёмки с КБ карточки", async () => {
    await load(warehouse);
    fireEvent.click(within(cardsIn("Производство")[0]).getByRole("button", { name: "Принять на склад…" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("КБ1");
  });

  it("«Сформировать PL» шлёт issue-document со своей версией; «Отгрузить» спрашивает без ОТГ и шлёт /ship с версией", async () => {
    shipments = SHIPMENTS();
    const ask = vi.spyOn(window, "confirm").mockReturnValue(true);
    await load(warehouse);
    fireEvent.click(within(cardsIn("Принято").find((card) => card.textContent!.includes("690К.1"))!).getByRole("button", { name: "Сформировать PL" }));
    await waitFor(() => expect(writes()).toEqual([expect.objectContaining({ url: "/api/shipping/shipments/s4/issue-document", method: "POST", body: expect.objectContaining({ version: 3 }) })]));
    await screen.findByText(/PL сформирован: 690К\.1/);
    fireEvent.click(within(cardsIn("На отгрузку")[0]).getByRole("button", { name: "Отгрузить" }));
    expect(ask).toHaveBeenCalledWith(expect.stringContaining("711К.3 · КБ1 · PL711К.3-1"));
    expect(ask.mock.calls.at(-1)![0]).not.toMatch(/ОТГ/);
    await waitFor(() => expect(writes().at(-1)).toMatchObject({ url: "/api/shipping/shipments/s2/ship", method: "POST", body: expect.objectContaining({ version: 3 }) }));
  });

  it("поиск по PL, КБ и треку скрывает чужие карточки во всех колонках", async () => {
    shipments = SHIPMENTS();
    await load(warehouse);
    const search = screen.getByRole("searchbox", { name: "Поиск по доске" });
    fireEvent.change(search, { target: { value: "PL711" } });
    expect(cardsIn("На отгрузку")).toHaveLength(1);
    expect(cardsIn("Упаковано")).toHaveLength(0);
    expect(cardsIn("Производство")).toHaveLength(0);
    expect(within(column("На отгрузку")).getByText(/1 из 1/)).toBeInTheDocument();
    fireEvent.change(search, { target: { value: "10482377165" } });
    expect(cardsIn("На отгрузку")).toHaveLength(1);
    fireEvent.change(search, { target: { value: "КБ1 жгут" } });
    expect(cardsIn("Производство")).toHaveLength(1);
    fireEvent.change(search, { target: { value: "ничего-такого" } });
    expect(document.querySelectorAll(".board-card")).toHaveLength(0);
    expect(within(column("Упаковано")).getByText("Ничего не найдено.")).toBeInTheDocument();
  });

  it("чип «Только с пробелами» оставляет карточки с блокером", async () => {
    shipments = SHIPMENTS();
    await load(warehouse);
    fireEvent.click(screen.getByRole("button", { name: "Только с пробелами" }));
    expect(cardsIn("На отгрузку")).toHaveLength(0);
    expect(cardsIn("Упаковано")).toHaveLength(1);
    expect(cardsIn("Отгружено")).toHaveLength(0);
  });

  it("«ещё N»: в колонке 20 карточек, остальные — по кнопке", async () => {
    shipments = Array.from({ length: 25 }, (_, index) => shipment({ id: `d${index}`, pl_number: `PL${index}-1`, status: "Закрыта", shipped_at: "2026-11-05", missing_data: [], missing_ship: [],
      lines: [line({ id: `dl${index}`, project: `${900 + index}К.1` })] }));
    intakeRows = [];
    await load(warehouse);
    expect(cardsIn("Отгружено")).toHaveLength(20);
    fireEvent.click(within(column("Отгружено")).getByRole("button", { name: "ещё 5 · показать" }));
    expect(cardsIn("Отгружено")).toHaveLength(25);
    expect(within(column("Отгружено")).getByRole("button", { name: /Старше 30 дней — в «Журнале»/ })).toBeInTheDocument();
  });

  it("«отгрузка k из n» — только когда у проекта несколько живых отправок", async () => {
    shipments = [shipment({ id: "a", created_at: "2026-11-04T09:00:00" }), shipment({ id: "b", created_at: "2026-11-06T09:00:00", pl_number: "PL828К.-2" }),
      shipment({ id: "c", lines: [line({ id: "lc", project: "999К.1" })], pl_number: "PL999К.1-1" })];
    intakeRows = [];
    await load(warehouse);
    const texts = cardsIn("Упаковано").map((card) => card.textContent!);
    expect(texts.filter((text) => text.includes("отгрузка 1 из 2") && text.includes("PL828К.-1"))).toHaveLength(1);
    expect(texts.filter((text) => text.includes("отгрузка 2 из 2") && text.includes("PL828К.-2"))).toHaveLength(1);
    expect(texts.find((text) => text.includes("999К.1"))).not.toContain("отгрузка");
  });

  it("окно карточки: шаги PL → DN → УПД → трек и правка на месте шапки с версией", async () => {
    shipments = SHIPMENTS();
    await load(warehouse);
    fireEvent.click(within(cardsIn("Упаковано")[0]).getByRole("button", { name: /^Открыть 828К\./ }));
    const dialog = await screen.findByRole("dialog");
    expect([...dialog.querySelectorAll(".board-step")].map((step) => step.getAttribute("data-step"))).toEqual(["PL", "DN", "УПД", "Трек"]);
    expect(within(dialog).getByRole("button", { name: /Позиции \(1\)/ })).toBeInTheDocument();
    expect(within(dialog).getByText("Жгут для ТМЛР")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Изменить: Получатель" }));
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Получатель" }), { target: { value: "Петров" } });
    fireEvent.keyDown(within(dialog).getByRole("textbox", { name: "Получатель" }), { key: "Enter" });
    await waitFor(() => expect(writes()).toEqual([expect.objectContaining({ url: "/api/shipping/shipments/s1/head", method: "PUT", body: { version: 3, contact: "Петров" } })]));
    await waitFor(() => expect(within(dialog).getByText("Петров")).toBeInTheDocument());
  });

  it("главная кнопка «Внести УПД…» открывает окно сразу в правке поля УПД", async () => {
    shipments = SHIPMENTS();
    await load(warehouse);
    fireEvent.click(within(cardsIn("Упаковано")[0]).getByRole("button", { name: "Внести УПД…" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("combobox", { name: "УПД" })).toBeInTheDocument();
  });

  it("?shipment=<id> открывает окно этой отправки, ?tab= подсвечивает колонку", async () => {
    shipments = SHIPMENTS();
    window.history.replaceState({}, "", "/?shipment=s2&tab=ship");
    await load(warehouse);
    expect(await screen.findByRole("dialog", { name: /Карточка 711К\.3 · КБ1 · PL711К\.3-1/ })).toBeInTheDocument();
    expect(column("На отгрузку").className).toContain("is-picked");
  });

  it("«Мои действия» оставляет карточки с кнопкой; «Отгрузить все готовые» — при двух готовых", async () => {
    shipments = [...SHIPMENTS(), shipment({ id: "s5", pl_number: "PL800К.1-1", ready_to_ship: true, missing_data: [], missing_ship: [], lines: [line({ id: "l5", project: "800К.1" })] })];
    await load(warehouse);
    expect(within(column("На отгрузку")).getByRole("button", { name: "Отгрузить все готовые · 2" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Мои действия" }));
    expect(cardsIn("Отгружено")).toHaveLength(0);
    expect(cardsIn("На отгрузку")).toHaveLength(2);
  });
});
