import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CommandPalette, SearchEntry } from "./CommandPalette";
import type { Principal } from "./api";

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

const RESULTS = [
  { group: "Строки закупки", title: "SM-0042 · Резистор 10 кОм", subtitle: "591К.3 · ООО Ромашка", section: "procurement", params: { tab: "sheet", q: "SM-0042" } },
  { group: "Склад", title: "FRX.26.0000003-П2", subtitle: "Резистор · 1F4 · 10 шт", section: "stock", params: { q: "FRX.26.0000003-П2" } },
];

function person(permissions: Principal["permissions"], level = "operator"): Principal {
  return { user: { id: "1", email: "a@b.c", name: "Иванов", department: "Склад" }, roles: [level], access_level: level, permissions };
}

const searches: string[] = [];
function serve(results = RESULTS) {
  searches.length = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    searches.push(url);
    return json({ query: "", results, ms: 1 });
  });
}

function press(init: KeyboardEventInit, target: EventTarget = document.body) {
  act(() => { target.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init })); });
}

beforeEach(() => { window.history.replaceState({}, "", "/?section=cabinet"); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

async function mount(principal: Principal) {
  render(<CommandPalette principal={principal} />);
}

it("opens on Ctrl+K and closes on Esc", async () => {
  serve();
  await mount(person({ warehouse: "view" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  press({ key: "k", code: "KeyK", ctrlKey: true });
  expect(await screen.findByRole("dialog", { name: "Поиск" })).toBeInTheDocument();
  expect(screen.getByRole("combobox")).toHaveFocus();
  fireEvent.keyDown(screen.getByRole("combobox"), { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("«/» opens outside inputs only", async () => {
  serve();
  await mount(person({ warehouse: "view" }));
  const field = document.createElement("input");
  document.body.appendChild(field);
  field.focus();
  press({ key: "/", code: "Slash" }, field);
  expect(screen.queryByRole("dialog")).toBeNull();
  press({ key: "/", code: "Slash" });
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  field.remove();
});

it("searches after the debounce, groups the answer and walks it with the arrows", async () => {
  serve();
  await mount(person({ warehouse: "view", procurement: "view" }));
  press({ key: "k", code: "KeyK", ctrlKey: true });
  const input = await screen.findByRole("combobox");
  fireEvent.change(input, { target: { value: "резистор" } });
  fireEvent.change(input, { target: { value: "резистор 10" } });
  await screen.findByText("SM-0042 · Резистор 10 кОм");
  expect(searches).toEqual(["/api/search?q=%D1%80%D0%B5%D0%B7%D0%B8%D1%81%D1%82%D0%BE%D1%80%2010"]);
  expect(screen.getByRole("group", { name: "Строки закупки" })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Склад" })).toBeInTheDocument();
  const options = screen.getAllByRole("option");
  expect(options[0]).toHaveAttribute("aria-selected", "true");
  fireEvent.keyDown(input, { key: "ArrowDown" });
  expect(options[1]).toHaveAttribute("aria-selected", "true");
  fireEvent.keyDown(input, { key: "ArrowUp" });
  fireEvent.keyDown(input, { key: "ArrowUp" });
  expect(screen.getAllByRole("option").at(-1)).toHaveAttribute("aria-selected", "true");
});

it("Enter opens the selected result with its parameters", async () => {
  serve();
  await mount(person({ warehouse: "view", procurement: "view" }));
  press({ key: "k", code: "KeyK", ctrlKey: true });
  const input = await screen.findByRole("combobox");
  fireEvent.change(input, { target: { value: "резистор" } });
  await screen.findByText("FRX.26.0000003-П2");
  fireEvent.keyDown(input, { key: "ArrowDown" });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(window.location.search).toBe("?section=stock&q=FRX.26.0000003-%D0%9F2");
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("a GRN offers the warehouse at once, before the server answers", async () => {
  serve([]);
  await mount(person({ warehouse: "view" }));
  press({ key: "k", code: "KeyK", ctrlKey: true });
  const input = await screen.findByRole("combobox");
  fireEvent.change(input, { target: { value: "PPM.26.0000001" } });
  expect(screen.getAllByRole("option")[0]).toHaveTextContent("PPM.26.0000001");
  fireEvent.keyDown(input, { key: "Enter" });
  expect(window.location.search).toBe("?section=stock&q=PPM.26.0000001");
});

it("commands are filtered by the sections the user can open", async () => {
  serve([]);
  await mount(person({ warehouse: "view" }));
  press({ key: "k", code: "KeyK", ctrlKey: true });
  await screen.findByRole("combobox");
  const names = screen.getAllByRole("option").map((option) => option.textContent);
  expect(names).toEqual(["СкладПерейти"]);
  cleanup();
  serve([]);
  await mount(person({ procurement: "view", receiving: "view", newrequest: "edit", warehouse: "view", projects: "view" }));
  press({ key: "k", code: "KeyK", ctrlKey: true });
  await screen.findByRole("combobox");
  expect(screen.getAllByRole("option").map((option) => option.querySelector(".palette-title")?.textContent)).toEqual([
    "Оформить заявку", "Сегодня", "Запросы КП", "Закупка", "Счета и поставки", "Склад", "Приёмка", "Поставщики", "Номенклатура", "УПД",
    "Внутренние проекты",
  ]);
  const input = screen.getByRole("combobox");
  fireEvent.change(input, { target: { value: "поставщики" } });
  expect(screen.getAllByRole("option").map((option) => option.querySelector(".palette-title")?.textContent)).toEqual(["Поставщики", "Закупка"]);
  fireEvent.keyDown(input, { key: "Enter" });
  // The purchasing reference books left «Снабжение» (Витя's edit 4): «Справочники → Закупка».
  expect(window.location.search).toBe("?section=purchasingrefs&tab=directory");
});

it("«Мои заявки» and the «Заявки» register are one search away now that the menu has no items for them", async () => {
  serve([]);
  await mount(person({ myrequests: "edit", requests: "edit" }));
  press({ key: "k", code: "KeyK", ctrlKey: true });
  const input = await screen.findByRole("combobox");
  fireEvent.change(input, { target: { value: "заявки" } });
  expect(screen.getAllByRole("option").map((option) => option.querySelector(".palette-title")?.textContent))
    .toEqual(expect.arrayContaining(["Мои заявки", "Реестр заявок"]));
});

it("a search result for a moved tab lands in its new section", async () => {
  serve([{ group: "Поставщики", title: "Ромашка", subtitle: "", section: "procurement", params: { tab: "directory", q: "Ромашка" } }]);
  await mount(person({ procurement: "view" }));
  press({ key: "k", code: "KeyK", ctrlKey: true });
  fireEvent.change(await screen.findByRole("combobox"), { target: { value: "ромашка" } });
  fireEvent.click(await screen.findByText("Ромашка"));
  expect(new URL(window.location.href).searchParams.get("section")).toBe("purchasingrefs");
});

it("an admin sees every command", async () => {
  serve([]);
  render(<CommandPalette principal={person({}, "admin")} />);
  press({ key: "k", code: "KeyK", ctrlKey: true });
  await screen.findByRole("combobox");
  expect(screen.getAllByRole("option").length).toBeGreaterThanOrEqual(4);
});

it("the visible entry «Поиск по ERP» with its Ctrl+K hint opens the palette", async () => {
  serve([]);
  render(<><SearchEntry /><CommandPalette principal={person({ warehouse: "view" })} /></>);
  const entry = screen.getByRole("button", { name: "Поиск по ERP" });
  expect(entry).toHaveTextContent("Ctrl+K");
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(entry);
  expect(await screen.findByRole("dialog", { name: "Поиск" })).toBeInTheDocument();
  expect(screen.getByRole("combobox")).toHaveFocus();
});

it("a result of a new group says where it lives and opens that place", async () => {
  serve([{ group: "База данных", title: "Наконечник кольцевой 6 мм", subtitle: "База данных → Наконечники", section: "references", params: { tab: "lug", q: "Наконечник кольцевой 6 мм" } }]);
  await mount(person({ erpref: "view" }));
  press({ key: "k", code: "KeyK", ctrlKey: true });
  fireEvent.change(await screen.findByRole("combobox"), { target: { value: "кольцевой" } });
  expect(await screen.findByText("База данных → Наконечники")).toBeInTheDocument();
  fireEvent.click(screen.getByText("Наконечник кольцевой 6 мм"));
  const url = new URL(window.location.href);
  expect([url.searchParams.get("section"), url.searchParams.get("tab"), url.searchParams.get("q")]).toEqual(["references", "lug", "Наконечник кольцевой 6 мм"]);
});

it("section words find the menu section once something is typed, and an empty field stays short", async () => {
  serve([]);
  await mount(person({ shipping: "view", warehouse: "view", erpref: "view" }));
  press({ key: "k", code: "KeyK", ctrlKey: true });
  const input = await screen.findByRole("combobox");
  const titles = () => screen.getAllByRole("option").map((option) => option.querySelector(".palette-title")?.textContent);
  expect(titles()).toEqual(["Склад"]);
  fireEvent.change(input, { target: { value: "упаковка" } });
  expect(titles()).toEqual(expect.arrayContaining(["Отправки", "Приёмка с производства"]));
  expect(titles()).not.toContain("Отгрузка");
  fireEvent.change(input, { target: { value: "наконечники" } });
  expect(titles()).toEqual(["База данных → Наконечники"]);
  fireEvent.keyDown(input, { key: "Enter" });
  expect(window.location.search).toBe("?section=references&tab=lug");
  fireEvent.keyDown(document.body, { key: "k", code: "KeyK", ctrlKey: true });
  fireEvent.change(await screen.findByRole("combobox"), { target: { value: "оборудование" } });
  expect(titles()).toEqual(expect.arrayContaining(["Склад → Оборудование", "База данных → Оборудование"]));
});
