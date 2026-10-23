import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Principal } from "./api";
import { EquipmentPage } from "./EquipmentPage";
import { FALLBACK_SCALE, LabelDesigner, PREVIEW_DELAY_MS, newElement, snap, textShown, textStored, type TemplateElement } from "./LabelDesigner";
import { ProductionIntakePage } from "./ProductionIntakePage";
import { WarehousePage } from "./WarehousePage";

const FIELDS = [{ key: "grn", label: "ГРН" }, { key: "article", label: "Артикул" }, { key: "name", label: "Наименование" }];
const GRN: TemplateElement = {
  id: "el1", kind: "field", field: "grn", x_mm: 2, y_mm: 2, w_mm: 30, h_mm: 5, font_mm: 4, bold: true, align: "left", max_lines: 1, fit: "shrink", mono: true,
};
const TEMPLATE = { elements: [GRN] };
const DEFAULT = { template: { elements: [{ ...GRN, x_mm: 1 }] }, is_default: true, version: null };
const answer = (kind: string) => ({
  kind, fields: FIELDS,
  formats: { "58x30": DEFAULT, "58x40": { template: TEMPLATE, is_default: false, version: 3 }, "100x50": DEFAULT },
});
const SETTINGS = {
  printer_name: "", gap_mm: 2, density: 12, speed: 2, width_mm: 58, height_mm: 40, dpi: 300, code_type: "both", offset_x_mm: 0, offset_y_mm: 0,
};
const AGENT = { last_seen: new Date().toISOString(), host: "SKLAD-PC", printer: "TSC MB341", queue: 0, last_error: "", last_job: null };

type Call = { method: string; path: string; body?: Record<string, unknown> };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
let calls: Call[] = [];

function stage(routes: Record<string, (call: Call) => Response> = {}) {
  calls = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const call: Call = { method: init?.method ?? "GET", path: String(input), body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    const route = routes[`${call.method} ${call.path}`];
    if (route) return route(call);
    const template = call.path.match(/^\/api\/stock\/labels\/templates\/(\w+)(?:\/([\dx]+))?(\/preview)?$/);
    if (template && call.method === "GET" && !template[2]) return json(answer(template[1]));
    if (template?.[3] && call.method === "POST") return new Response(new TextEncoder().encode("png"), { headers: { "Content-Type": "image/png" } });
    if (template && call.method === "PUT") return json({ template: call.body?.template, is_default: false, version: 4 });
    if (template && call.method === "DELETE") return json(DEFAULT);
    if (call.path === "/api/stock/labels/settings") return json({ settings: SETTINGS, code_types: { qr: "QR-код", code128: "Штрихкод Code128", both: "QR и Code128" }, copies_max: 50 });
    if (call.path === "/api/stock/labels/agent/status") return json(AGENT);
    if (call.path.split("?")[0] === "/api/stock") return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
    if (call.path === "/api/shipping/intake") return json({ rows: [] });
    return json({ detail: `Unexpected request: ${call.path}` }, 500);
  });
}

const previews = () => calls.filter((call) => call.path.endsWith("/preview"));

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

async function designer(props: Partial<Parameters<typeof LabelDesigner>[0]> = {}) {
  stage();
  render(<LabelDesigner initialKind="lot" usedFormat="58x40" {...props} />);
  return screen.findByRole("button", { name: "Поле: ГРН" });
}
const props = () => within(screen.getByRole("complementary", { name: "Свойства элемента" }));

it("snaps to 0.5 mm and makes new elements inside the label", () => {
  expect(snap(3.26)).toBe(3.5);
  expect(snap(3.24)).toBe(3);
  const qr = newElement("qr", [GRN], [58, 30], "grn");
  expect(qr).toMatchObject({ id: "el2", kind: "qr", field: "grn", x_mm: 2, y_mm: 2 });
  expect(qr.y_mm + qr.h_mm).toBeLessThanOrEqual(30);
  expect(newElement("text", [], [58, 40]).text).toBe("Текст");
});

it("opens on the printer's format, marks it «используется», adds a field from the palette", async () => {
  await designer();
  const formats = screen.getByRole("tablist", { name: "Формат" });
  expect(within(formats).getByRole("tab", { name: /58 × 40/ })).toHaveAttribute("aria-selected", "true");
  expect(within(formats).getByRole("tab", { name: /58 × 40/ })).toHaveTextContent("используется");
  expect(screen.getByText(/Свой шаблон/)).toBeInTheDocument();
  const add = screen.getByLabelText("Добавить поле");
  expect(within(add).getAllByRole("option").map((option) => option.textContent)).toEqual(["+ Поле…", "ГРН", "Артикул", "Наименование"]);
  fireEvent.change(add, { target: { value: "article" } });
  const box = screen.getByRole("button", { name: "Поле: Артикул" });
  expect(box).toHaveAttribute("aria-pressed", "true");
  expect(props().getByLabelText("Поле")).toHaveValue("article");
  expect(screen.getByText(/не сохранено/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "+ QR" }));
  expect(screen.getByRole("button", { name: "QR-код: ГРН" })).toBeInTheDocument();
});

it("drags an element on the 0.5 mm grid, resizes it by the corner, nudges with arrows and removes with Delete", async () => {
  const box = await designer();
  fireEvent.pointerDown(box, { button: 0, clientX: 100, clientY: 100, pointerId: 1 });
  fireEvent.pointerMove(box, { clientX: 100 + 5 * FALLBACK_SCALE + 1, clientY: 100 + 2 * FALLBACK_SCALE, pointerId: 1 });
  fireEvent.pointerUp(box, { pointerId: 1 });
  expect(props().getByLabelText("X, мм")).toHaveValue("7");
  expect(props().getByLabelText("Y, мм")).toHaveValue("4");

  const handle = box.querySelector(".ld-handle")!;
  fireEvent.pointerDown(handle, { button: 0, clientX: 0, clientY: 0, pointerId: 2 });
  fireEvent.pointerMove(handle, { clientX: 4 * FALLBACK_SCALE, clientY: 1.5 * FALLBACK_SCALE, pointerId: 2 });
  fireEvent.pointerUp(handle, { pointerId: 2 });
  expect(props().getByLabelText("Ширина, мм")).toHaveValue("34");
  expect(props().getByLabelText("Высота, мм")).toHaveValue("6,5");

  // Dragged past the edge — stays inside the 58 × 40 label.
  fireEvent.pointerDown(box, { button: 0, clientX: 0, clientY: 0, pointerId: 3 });
  fireEvent.pointerMove(box, { clientX: 1000, clientY: 1000, pointerId: 3 });
  fireEvent.pointerUp(box, { pointerId: 3 });
  expect(props().getByLabelText("X, мм")).toHaveValue("24");
  expect(props().getByLabelText("Y, мм")).toHaveValue("33,5");

  const canvas = screen.getByRole("group", { name: /Макет этикетки 58 × 40/ });
  fireEvent.keyDown(canvas, { key: "ArrowLeft" });
  expect(props().getByLabelText("X, мм")).toHaveValue("23,5");
  fireEvent.keyDown(canvas, { key: "ArrowUp", shiftKey: true });
  expect(props().getByLabelText("Y, мм")).toHaveValue("31,5");
  fireEvent.keyDown(canvas, { key: "Delete" });
  expect(screen.queryByRole("button", { name: "Поле: ГРН" })).not.toBeInTheDocument();
  expect(screen.getByText("Этикетка пустая — добавьте поле.")).toBeInTheDocument();
});

it("edits properties and «Сохранить» sends the template with its version", async () => {
  await designer();
  fireEvent.click(screen.getByRole("button", { name: "Поле: ГРН" }));
  fireEvent.change(props().getByLabelText("Кегль, мм"), { target: { value: "6,5" } });
  fireEvent.change(props().getByLabelText("Выравнивание"), { target: { value: "center" } });
  fireEvent.click(props().getByLabelText("жирный"));
  fireEvent.change(props().getByLabelText("Не влезает"), { target: { value: "ellipsis" } });
  fireEvent.change(props().getByLabelText("Строк"), { target: { value: "9" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  expect(await screen.findByText("Шаблон сохранён")).toBeInTheDocument();
  const put = calls.find((call) => call.method === "PUT")!;
  expect(put.path).toBe("/api/stock/labels/templates/lot/58x40");
  expect(put.body).toEqual({ template: { elements: [{ ...GRN, font_mm: 6.5, align: "center", bold: false, fit: "ellipsis", max_lines: 4 }] }, version: 3 });
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();

  // The next save carries the version the server returned.
  fireEvent.change(props().getByLabelText("Кегль, мм"), { target: { value: "5" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(calls.filter((call) => call.method === "PUT")).toHaveLength(2));
  expect(calls.filter((call) => call.method === "PUT")[1].body?.version).toBe(4);
});

it("a text shows and takes its fields by Russian name, and stores their keys", async () => {
  expect(textShown("Приход {received_at} · {x}", [{ key: "received_at", label: "Дата прихода" }])).toBe("Приход {Дата прихода} · {x}");
  expect(textStored("Приход {дата прихода}", [{ key: "received_at", label: "Дата прихода" }])).toBe("Приход {received_at}");
  await designer();
  fireEvent.click(screen.getByRole("button", { name: "+ Текст" }));
  fireEvent.change(props().getByLabelText("Текст"), { target: { value: "Партия {ГРН} · " } });
  fireEvent.change(props().getByLabelText("Вставить поле в текст"), { target: { value: "article" } });
  expect(props().getByLabelText("Текст")).toHaveValue("Партия {ГРН} · {Артикул}");
  expect(screen.getByRole("button", { name: "Текст: Партия {ГРН} · {Артикул}" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(calls.some((call) => call.method === "PUT")).toBe(true));
  const saved = (calls.find((call) => call.method === "PUT")!.body?.template as { elements: TemplateElement[] }).elements;
  expect(saved[1]).toMatchObject({ kind: "text", text: "Партия {grn} · {article}" });
});

it("a stale version answers «Шаблон изменили — обновите»; «Обновить» takes the server's template", async () => {
  stage({ "PUT /api/stock/labels/templates/lot/58x40": () => json({ detail: "Шаблон изменили, обновите" }, 409) });
  render(<LabelDesigner initialKind="lot" usedFormat="58x40" />);
  fireEvent.click(await screen.findByRole("button", { name: "Поле: ГРН" }));
  fireEvent.change(props().getByLabelText("Кегль, мм"), { target: { value: "8" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Шаблон изменили — обновите");
  const loads = calls.filter((call) => call.path === "/api/stock/labels/templates/lot").length;
  fireEvent.click(within(alert).getByRole("button", { name: "Обновить" }));
  await waitFor(() => expect(calls.filter((call) => call.path === "/api/stock/labels/templates/lot")).toHaveLength(loads + 1));
  await waitFor(() => expect(screen.queryByText(/не сохранено/)).not.toBeInTheDocument());
});

it("«Сбросить к стандартному» asks first, then sends DELETE and shows the default", async () => {
  await designer();
  fireEvent.click(screen.getByRole("button", { name: "Сбросить к стандартному" }));
  const confirm = within(await screen.findByRole("dialog", { name: "Сбросить шаблон" }));
  expect(calls.some((call) => call.method === "DELETE")).toBe(false);
  fireEvent.click(confirm.getByRole("button", { name: "Сбросить" }));
  expect(await screen.findByText("Шаблон сброшен к стандартному")).toBeInTheDocument();
  expect(calls.filter((call) => call.method === "DELETE").map((call) => call.path)).toEqual(["/api/stock/labels/templates/lot/58x40"]);
  expect(screen.getByText(/Стандартный шаблон/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сбросить к стандартному" })).toBeDisabled();
});

it("«Отменить изменения» brings the saved template back", async () => {
  await designer();
  fireEvent.click(screen.getByRole("button", { name: "+ Рамка" }));
  expect(within(screen.getByRole("group", { name: /Макет/ })).getByRole("button", { name: "Рамка" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Отменить изменения" }));
  expect(within(screen.getByRole("group", { name: /Макет/ })).queryByRole("button", { name: "Рамка" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Отменить изменения" })).toBeDisabled();
});

it("the preview is asked once after a burst of edits, with the latest template", async () => {
  await designer();
  await waitFor(() => expect(previews()).toHaveLength(1));
  expect(previews()[0]).toMatchObject({ method: "POST", path: "/api/stock/labels/templates/lot/58x40/preview", body: { template: TEMPLATE, sample: null } });
  expect(await screen.findByRole("img", { name: "Предпросмотр этикетки 58 × 40" })).toHaveAttribute("src", `data:image/png;base64,${btoa("png")}`);
  fireEvent.click(screen.getByRole("button", { name: "Поле: ГРН" }));
  for (const size of ["5", "6", "7"]) fireEvent.change(props().getByLabelText("Кегль, мм"), { target: { value: size } });
  expect(previews()).toHaveLength(1);
  await waitFor(() => expect(previews()).toHaveLength(2));
  expect((previews()[1].body?.template as { elements: TemplateElement[] }).elements[0].font_mm).toBe(7);
  await act(() => new Promise((resolve) => setTimeout(resolve, PREVIEW_DELAY_MS + 200)));
  expect(previews()).toHaveLength(2);
});

it("switches the kind: «Приёмка с производства» loads its own templates", async () => {
  await designer();
  fireEvent.click(screen.getByRole("tab", { name: "Приёмка с производства" }));
  await waitFor(() => expect(calls.some((call) => call.path === "/api/stock/labels/templates/intake")).toBe(true));
  expect(screen.getByRole("tab", { name: "Приёмка с производства" })).toHaveAttribute("aria-selected", "true");
});

it("a viewer sees the template but no palette, «Сохранить» or «Сбросить»", async () => {
  await designer({ readOnly: true });
  expect(screen.queryByLabelText("Добавить поле")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Сбросить к стандартному" })).not.toBeInTheDocument();
  // The server draws a preview only for those who may change the warehouse: a viewer never asks for it.
  expect(screen.getByText(/предпросмотр и правка шаблона — у тех, кто может менять склад/)).toBeInTheDocument();
  await new Promise((resolve) => setTimeout(resolve, 700));
  expect(calls.some((call) => call.path.endsWith("/preview"))).toBe(false);
});

async function openTemplateTab() {
  fireEvent.click(await screen.findByRole("button", { name: "Настройка печати" }));
  const dialog = within(await screen.findByRole("dialog", { name: "Настройка печати этикеток" }));
  expect(await dialog.findByRole("region", { name: "Печать этикеток ГРН" })).toBeInTheDocument();
  expect(await dialog.findByRole("status", { name: "Агент печати" })).toHaveTextContent("принтер TSC MB341");
  fireEvent.click(dialog.getByRole("tab", { name: "Шаблон этикетки" }));
  await dialog.findByRole("button", { name: "Поле: ГРН" });
  return dialog;
}

it("«Склад» → «Настройка печати»: the printer and the template of a lot's label", async () => {
  stage();
  render(<WarehousePage />);
  const dialog = await openTemplateTab();
  expect(dialog.getByRole("tab", { name: "Партия склада" })).toHaveAttribute("aria-selected", "true");
  expect(dialog.getByRole("tab", { name: /58 × 40/ })).toHaveTextContent("используется");
});

const keeper = (permissions: Principal["permissions"]) => ({
  user: { id: "u1", email: "k@example.test", name: "Кладовщик", department: "Склад" },
  roles: ["foreman"], access_level: "foreman", permissions,
}) as unknown as Principal;

it("«Приёмка с производства» → «Настройка печати» opens on the intake label; no warehouse access — no button", async () => {
  stage();
  const { unmount } = render(<ProductionIntakePage principal={keeper({ shipping_goods: "edit", warehouse: "edit" })} />);
  const dialog = await openTemplateTab();
  expect(dialog.getByRole("tab", { name: "Приёмка с производства" })).toHaveAttribute("aria-selected", "true");
  expect(calls.some((call) => call.path === "/api/stock/labels/templates/intake")).toBe(true);
  unmount();

  render(<ProductionIntakePage principal={keeper({ shipping_goods: "edit" })} />);
  await screen.findByRole("heading", { name: "Приёмка с производства" });
  expect(screen.queryByRole("button", { name: "Настройка печати" })).not.toBeInTheDocument();
});

it("closing with unsaved template edits asks first", async () => {
  stage();
  render(<WarehousePage />);
  const dialog = await openTemplateTab();
  fireEvent.click(dialog.getByRole("button", { name: "+ Линия" }));
  fireEvent.click(dialog.getByRole("button", { name: "Закрыть" }));
  const ask = within(await screen.findByRole("dialog", { name: "Несохранённый шаблон" }));
  fireEvent.click(ask.getByRole("button", { name: "Вернуться к шаблону" }));
  expect(screen.getByRole("dialog", { name: "Настройка печати этикеток" })).toBeInTheDocument();
  fireEvent.click(dialog.getByRole("button", { name: "Закрыть" }));
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Несохранённый шаблон" })).getByRole("button", { name: "Закрыть без сохранения" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Настройка печати этикеток" })).not.toBeInTheDocument());
});

it("«Оборудование» keeps the ТСД card; the label printer is gone from it", async () => {
  stage({
    "GET /api/devices/scanner-log": () => json({ lines: [] }),
    "GET /api/devices/client-errors": () => json({ errors: [] }),
  });
  render(<EquipmentPage admin />);
  expect(await screen.findByRole("region", { name: "ТСД" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Печать этикеток ГРН" })).not.toBeInTheDocument();
  expect(calls.some((call) => call.path.startsWith("/api/stock/labels"))).toBe(false);
});
