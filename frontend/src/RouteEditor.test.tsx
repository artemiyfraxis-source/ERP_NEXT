import { useState } from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RouteEditor } from "./RouteEditor";
import { numberRange } from "./RouteDiagram";
import { deriveRoute, type Route, type RouteOp } from "./routeLogic";

/* Жгут с пайкой: резка провода (1) и резка ТУТ (2) сходятся в «Надеть ТУТ» (3), дальше пайка (4) и усадка (5) — изделие.
   Нормы на партию 5 шт: 10, 2, 3, 4 и 1 мин. Схема рисуется карточками полуфабрикатов; операция открывает свой блок ЭТК. */
const OPS: RouteOp[] = [
  { position: 1, key: "cut", type: "cutWire", code: "", label: "Резка провода", minutes: 10 },
  { position: 2, key: "cutTut", type: "cutTut", code: "", label: "Резка ТУТ", minutes: 2 },
  { position: 3, key: "insTut", type: "insTut", code: "", label: "Надеть ТУТ", minutes: 3 },
  { position: 4, key: "solder", type: "solderConn", code: "", label: "Пайка разъёма", minutes: 4 },
  { position: 5, key: "heat", type: "heatTut", code: "", label: "Усадка ТУТ", minutes: 1 },
];

function mount(props: Partial<Parameters<typeof RouteEditor>[0]> = {}) {
  const onChange = vi.fn();
  render(<RouteEditor ops={OPS} finalName="Жгут XH" quantity={5} value={null} onChange={onChange} {...props} />);
  return onChange;
}

const lastRoute = (onChange: ReturnType<typeof vi.fn>): Route => onChange.mock.calls.at(-1)![0];
const block = (key: string, ops: number[], final = false) => ({ key, name: "", ops, final, components: [] });
const manual: Route = {
  blocks: [
    { key: "b1", name: "", ops: [1], final: false, components: [] },
    { key: "b2", name: "", ops: [2], final: false, components: [] },
    { key: "b3", name: "", ops: [3], final: false, components: [] },
    { key: "b4", name: "", ops: [4, 5], final: true, components: [] },
  ],
  edges: [{ from: "b1", to: "b3", per_unit: 1 }, { from: "b2", to: "b3", per_unit: 1 }, { from: "b3", to: "b4", per_unit: 1 }],
};
const canvas = () => screen.getByRole("group", { name: "Карточки схемы" });
const cards = () => [...canvas().querySelectorAll("article.rc-card")] as HTMLElement[];
const op = (name: RegExp) => within(canvas()).getByRole("button", { name });

it("draws the semi-finished cards of the scheme: cut wires and tubes go into the finished product with its operations and times", () => {
  mount();
  expect(cards().map((card) => card.querySelector(".rc-title")?.textContent)).toEqual(["Нарезанные провода", "Нарезанные трубки", "Готовый кабель"]);
  expect(cards().map((card) => card.querySelector(".rc-kind")?.textContent)).toEqual(["ПОЛУФАБРИКАТ", "ПОЛУФАБРИКАТ", "ФИНАЛ"]);
  expect(screen.getByText("автоматическая")).toBeInTheDocument();
  expect(screen.getByText(/3 карточки/)).toHaveTextContent("3 карточки · 5 операций · на изделие 4 мин");
  // Финал: изделие второй строкой, три операции со временем на штуку (норма на партию / 5).
  const final = cards()[2];
  expect(final.querySelector(".rc-sub")).toHaveTextContent("Жгут XH");
  expect(final.querySelector(".rc-meta")).toHaveTextContent("3 оп. · 1,6 мин/шт");
  expect(within(final).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["3Надеть ТУТ0,6 мин", "4Пайка разъёма0,8 мин", "5Усадка ТУТ0,2 мин"]);
  // «берёт 1 шт» на связи не пишется — число только когда не одна штука.
  expect(canvas().querySelectorAll(".rc-link")).toHaveLength(2);
  expect(canvas().querySelector(".rc-qty")).toBeNull();
});

it("shows the number on a link only when the next card takes not one piece", () => {
  mount({ value: { ...manual, edges: manual.edges.map((edge) => (edge.from === "b2" ? { ...edge, per_unit: 2 } : edge)) } });
  expect(canvas().querySelectorAll(".rc-qty")).toHaveLength(1);
  expect(canvas().querySelector(".rc-qty")).toHaveTextContent("2 шт");
});

it("opens the block of an operation and sends the new per-unit quantity of its input", () => {
  const onChange = mount();
  fireEvent.click(op(/Надеть ТУТ/));
  const panel = screen.getByRole("region", { name: "Блок «Надеть ТУТ»" });
  fireEvent.change(within(panel).getByLabelText("Штук из «Резка ТУТ» на один «Надеть ТУТ»"), { target: { value: "2,5" } });
  const route = lastRoute(onChange);
  expect(route.edges.find((edge) => edge.from === "b2" && edge.to === "b3")?.per_unit).toBe(2.5);
  expect(route.edges.filter((edge) => edge.per_unit === 1)).toHaveLength(3);
  expect(route.blocks.map((item) => item.ops)).toEqual([[1], [2], [3], [4], [5]]);
});

it("ignores a quantity that is not a positive number", () => {
  const onChange = mount();
  fireEvent.click(op(/Надеть ТУТ/));
  const input = screen.getByLabelText("Штук из «Резка ТУТ» на один «Надеть ТУТ»");
  fireEvent.change(input, { target: { value: "0" } });
  fireEvent.change(input, { target: { value: "abc" } });
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.blur(input);
  expect(input).toHaveValue("1");
});

it("merges two adjacent blocks into one with both operations and the downstream block's name", () => {
  const onChange = mount();
  fireEvent.click(op(/Пайка разъёма/));
  const panel = screen.getByRole("region", { name: "Блок «Пайка разъёма»" });
  fireEvent.change(within(panel).getByLabelText("Объединить с блоком"), { target: { value: "b5" } });
  fireEvent.click(within(panel).getByRole("button", { name: "Объединить" }));
  const route = lastRoute(onChange);
  expect(route.blocks.map((item) => [item.key, item.ops, item.final])).toEqual([
    ["b1", [1], false], ["b2", [2], false], ["b3", [3], false], ["b5", [4, 5], true],
  ]);
  expect(route.edges.map((edge) => `${edge.from}>${edge.to}`)).toEqual(["b1>b3", "b2>b3", "b3>b5"]);
});

it("splits a block after the chosen operation and gives the inputs to the first part", () => {
  const onChange = mount({ value: manual });
  fireEvent.click(op(/Усадка ТУТ/));
  const panel = screen.getByRole("region", { name: "Блок «Усадка ТУТ»" });
  fireEvent.change(within(panel).getByLabelText("Разделить после операции"), { target: { value: "4" } });
  fireEvent.click(within(panel).getByRole("button", { name: "Разделить" }));
  const route = lastRoute(onChange);
  expect(route.blocks.map((item) => [item.key, item.ops, item.final])).toEqual([
    ["b1", [1], false], ["b2", [2], false], ["b3", [3], false], ["b5", [4], false], ["b4", [5], true],
  ]);
  expect(route.edges.map((edge) => `${edge.from}>${edge.to}`)).toEqual(["b1>b3", "b2>b3", "b3>b5", "b5>b4"]);
});

it("moves an operation to another block and renames a block", () => {
  const onChange = mount({ value: manual, renderOp: (position) => <div>настройка операции {position}</div> });
  fireEvent.click(op(/Пайка разъёма/));
  const panel = screen.getByRole("region", { name: "Блок «Усадка ТУТ»" });
  expect(within(panel).getByText("настройка операции 4")).toBeInTheDocument();
  expect(within(panel).getByText("настройка операции 5")).toBeInTheDocument();
  fireEvent.change(within(panel).getByLabelText("Перенести операцию 4 в блок"), { target: { value: "b3" } });
  expect(lastRoute(onChange).blocks.map((item) => [item.key, item.ops])).toEqual([["b1", [1]], ["b2", [2]], ["b3", [3, 4]], ["b4", [5]]]);
  fireEvent.change(within(panel).getByLabelText("Название блока"), { target: { value: "Готовый жгут" } });
  expect(lastRoute(onChange).blocks.find((item) => item.key === "b4")?.name).toBe("Готовый жгут");
});

it("returns to the automatic scheme with «Сбросить к автоматической» and does not offer it for an automatic one", () => {
  const onChange = mount({ value: manual });
  expect(screen.getByText("правлена вручную")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Сбросить к автоматической" }));
  expect(onChange).toHaveBeenCalledWith(null);
});

it("does not offer the reset for an automatic scheme", () => {
  mount();
  expect(screen.queryByRole("button", { name: "Сбросить к автоматической" })).not.toBeInTheDocument();
});

it("refuses an edit that leaves a block without a way to the product and says why", () => {
  const onChange = mount({ value: manual });
  fireEvent.click(op(/Надеть ТУТ/));
  const panel = screen.getByRole("region", { name: "Блок «Надеть ТУТ»" });
  fireEvent.click(within(panel).getByRole("button", { name: "Убрать вход «Резка ТУТ»" }));
  expect(onChange).not.toHaveBeenCalled();
  expect(screen.getAllByRole("alert").at(-1)).toHaveTextContent("Так нельзя: блок «Резка ТУТ» не доходит до итогового блока");
});

it("adds a second input to a block from a block that is not after it", () => {
  const onChange = mount({ value: manual });
  fireEvent.click(op(/Усадка ТУТ/));
  const panel = screen.getByRole("region", { name: "Блок «Усадка ТУТ»" });
  const select = within(panel).getByLabelText("Добавить вход из блока");
  expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual(["— выберите —", "Резка провода", "Резка ТУТ"]);
  fireEvent.change(select, { target: { value: "b1" } });
  fireEvent.click(within(panel).getByRole("button", { name: "Добавить вход" }));
  expect(lastRoute(onChange).edges.map((edge) => `${edge.from}>${edge.to}`)).toEqual(["b1>b3", "b1>b4", "b2>b3", "b3>b4"]);
});

it("offers no input that would make the scheme loop", () => {
  mount({ value: manual });
  fireEvent.click(op(/Надеть ТУТ/));
  const panel = screen.getByRole("region", { name: "Блок «Надеть ТУТ»" });
  expect(within(panel).queryByLabelText("Добавить вход из блока")).not.toBeInTheDocument();
});

it("collapses and reopens the scheme", () => {
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Свернуть схему" }));
  expect(screen.queryByRole("group", { name: "Карточки схемы" })).not.toBeInTheDocument();
  expect(screen.getByText(/3 карточки/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Показать схему" }));
  expect(canvas()).toBeInTheDocument();
});

it("shows the server's text in an error banner", () => {
  mount({ ops: OPS.slice(0, 3), error: "Схема полуфабрикатов: блок «Резка» не доходит до итогового блока", value: null });
  expect(screen.getByRole("alert")).toHaveTextContent("Схема полуфабрикатов: блок «Резка» не доходит до итогового блока");
  expect(screen.getByRole("alert")).toHaveClass("error-banner");
});

it("shows no time when the card has never been built", () => {
  mount({ ops: OPS.map((item) => ({ ...item, minutes: null })) });
  expect(screen.queryByText(/мин\/шт/)).not.toBeInTheDocument();
  expect(screen.getByText(/3 карточки/)).toHaveTextContent("3 карточки · 5 операций");
  expect(screen.getByText(/3 карточки/)).not.toHaveTextContent("на изделие");
  expect(within(cards()[2]).queryByText("—")).not.toBeInTheDocument();
});

it("derives the same scheme as the ETK does from the operation types", () => {
  const route = deriveRoute(OPS);
  expect(route.edges.map((edge) => `${edge.from}>${edge.to}`)).toEqual(["b1>b3", "b2>b3", "b3>b4", "b4>b5"]);
  expect(route.blocks.find((item) => item.final)?.key).toBe("b5");
});

it("numbers operations the way the generator list does when an operation above has no template", () => {
  const ops = OPS.slice(0, 3).map((item) => ({ ...item, number: item.position + 1 }));
  mount({ ops, value: { blocks: [block("b1", [1]), block("b2", [2, 3], true)], edges: [{ from: "b1", to: "b2", per_unit: 1 }] }, skipped: [1] });
  expect(screen.getByText("Без шаблона, в схему не входят: 1")).toBeInTheDocument();
  fireEvent.click(op(/Надеть ТУТ/));
  const panel = screen.getByRole("region", { name: /Блок/ });
  expect(within(panel).getByLabelText("Перенести операцию 3 в блок")).toBeInTheDocument();
  expect(within(panel).getByLabelText("Перенести операцию 4 в блок")).toBeInTheDocument();
  expect(within(within(panel).getByLabelText("Разделить после операции")).getByRole("option", { name: "3. Резка ТУТ" })).toBeInTheDocument();
});

it("numbers a block by the range of its operations", () => {
  expect(numberRange([3, 1, 2])).toBe("1–3");
  expect(numberRange([4])).toBe("4");
  expect(numberRange([2, 5])).toBe("2, 5");
  expect(numberRange([])).toBe("");
});

const CODED: RouteOp[] = [
  { position: 1, key: "cutWire", type: "cutWireCrimp", code: "CUT_WIRE+1crimp", label: "Резка с обжимом А и В — 4 кабеля", minutes: null, codeNote: "вместо CUT_WIRE+2crimp — нет карты", side: "A" },
  { position: 2, key: "insTermA1", type: "insTerm", code: "INS_terminal_2.50", label: "Монтаж в разъём А1 — XHP-4", minutes: null, side: "A" },
  { position: 3, key: "twist", type: "twist", code: "TWIST", label: "Свивка", minutes: null },
  { position: 4, key: "insTermB1", type: "insTerm", code: "INS_terminal_2.50", label: "Монтаж в разъём В1 — XHP-4", minutes: null, side: "B" },
];

it("names the operations in Russian on the cards, keeps the card codes in the hint and marks a replaced card", () => {
  mount({ ops: CODED, finalName: "Кабель USB" });
  expect(cards().map((card) => card.querySelector(".rc-title")?.textContent)).toEqual(["Нарезанные и обжатые провода", "Сторона А собрана", "Готовый кабель"]);
  // Коды карт на карточке не пишутся — только в подсказке операции.
  expect(canvas().textContent).not.toMatch(/CUT_WIRE|INS_terminal|TWIST/);
  expect(op(/Резка с обжимом/)).toHaveAttribute("title", expect.stringContaining("Карта CUT_WIRE+1crimp"));
  expect(op(/Резка с обжимом/)).toHaveTextContent("заменена");
  // Свивка идёт перед стороной В — в карточке готового кабеля вместе с монтажом В1.
  expect(within(cards()[2]).getAllByRole("listitem").map((item) => item.querySelector(".rc-op-name")?.textContent)).toEqual(["Свивка", "Монтаж в разъём В1 — XHP-4"]);
  expect(cards()[2].querySelector(".rc-sub")).toHaveTextContent("Кабель USB");
});

it("«Изменить схему» adds «Выше/Ниже» to the opened block and hands over the new operation order", () => {
  const onMove = vi.fn();
  mount({ ops: CODED, finalName: "Кабель USB", onMove });
  fireEvent.click(op(/Свивка/));
  expect(screen.queryByRole("button", { name: "↑ Выше" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Изменить схему" }));
  expect(screen.queryByText(/Тяните операцию на другую/)).not.toBeInTheDocument();
  expect(document.querySelectorAll(".rc-card .rc-grip").length).toBeGreaterThan(0);
  fireEvent.click(within(screen.getByRole("region", { name: "Блок «Свивка»" })).getByRole("button", { name: "↑ Выше" }));
  // Свивка встаёт перед монтажом А1; схема автоматическая — её выведут заново.
  expect(onMove).toHaveBeenCalledWith([1, 3, 2, 4], null);
  fireEvent.click(screen.getByRole("button", { name: "Готово" }));
  expect(screen.queryByRole("button", { name: "↑ Выше" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Изменить схему" })).toBeInTheDocument();
});

it("«Сложная сборка»: draws the generator's ПФ graph as it is — a card per block with its level, kits, what comes in", () => {
  // Схема генератора (владелец 16.10): резка провода и резка ТУТ — ПФ-комплекты, «Надеть ТУТ» берёт их, пайка с усадкой — изделие.
  const graph: Route = {
    blocks: [{ ...block("cut", [1]), kit: true }, { ...block("tubes", [2]), kit: true, name: "Нарезанные трубки" },
      { ...block("on", [3]), name: "Ветвь А1" }, block("final", [4, 5], true)],
    edges: [{ from: "cut", to: "on", per_unit: 1, item: "НВ4 0,5 красный ×1; НВ4 0,5 черный ×1" },
      { from: "tubes", to: "on", per_unit: 1, item: "ТУТ 3/1 ×2" }, { from: "on", to: "final", per_unit: 1, item: "Ветвь А1" }],
  };
  const onChange = mount({ blockView: true, derive: () => graph });
  expect(cards().map((card) => card.querySelector(".rc-title")?.textContent)).toEqual(["1.1 · Резка провода", "1.2 · Нарезанные трубки", "2 · Ветвь А1", "3 · Жгут XH"]);
  expect(cards()[0].querySelector(".rc-meta")?.textContent).toContain("комплект на изделие");
  expect([...cards()[2].querySelectorAll(".rc-inputs li")].map((item) => item.textContent)).toEqual(["НВ4 0,5 красный ×1", "НВ4 0,5 черный ×1", "ТУТ 3/1 ×2"]);
  expect(screen.getByText(/4 ПФ · 5 операций/)).toBeInTheDocument();
  // Блок открывается по операции; во входах блока — что идёт по стрелке.
  fireEvent.click(op(/Надеть ТУТ/));
  expect(within(screen.getByRole("region", { name: "Блок «Ветвь А1»" })).getByText(/ТУТ 3\/1 ×2/)).toBeInTheDocument();
  expect(onChange).not.toHaveBeenCalled();
});

it("moves a hand-edited scheme with its blocks: the operations get new numbers, blocks and arrows stay", () => {
  const onMove = vi.fn();
  mount({ value: manual, onMove });
  fireEvent.click(screen.getByRole("button", { name: "Изменить схему" }));
  fireEvent.click(op(/Надеть ТУТ/));
  fireEvent.click(within(screen.getByRole("region", { name: "Блок «Надеть ТУТ»" })).getByRole("button", { name: "↓ Ниже" }));
  const [order, route] = onMove.mock.calls[0];
  expect(order).toEqual([1, 2, 4, 5, 3]);
  expect((route as Route).blocks.map((item) => [item.key, item.ops])).toEqual([["b1", [1]], ["b2", [2]], ["b4", [3, 4]], ["b3", [5]]]);
  expect((route as Route).edges).toEqual(manual.edges);
});

it("offers «Вернуть порядок генератора» when the order is the technologist's own", () => {
  const onResetOrder = vi.fn();
  mount({ ops: CODED, orderChanged: true, onResetOrder });
  expect(screen.getByText("свой порядок операций")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Вернуть порядок генератора" }));
  expect(onResetOrder).toHaveBeenCalled();
});

it("draws the crimp of each side right before it with the wires crimped on that side (owner 14.10)", () => {
  const ops: RouteOp[] = [
    { position: 1, key: "cutWire", type: "cutWire", code: "", label: "Резка — 2 кабеля", minutes: null },
    { position: 2, key: "prsTermA1", type: "prsTerm", code: "", label: "Опрессовка терминалов А1", minutes: null, side: "A" },
    { position: 3, key: "insTermA1", type: "insTerm", code: "", label: "Монтаж в разъём А1", minutes: null, side: "A" },
    { position: 4, key: "prsTermB1", type: "prsTerm", code: "", label: "Опрессовка терминалов В1", minutes: null, side: "B" },
    { position: 5, key: "insTermB1", type: "insTerm", code: "", label: "Монтаж в разъём В1", minutes: null, side: "B" },
  ];
  const wire = { key: "u1", label: "А1:1 → В1:1", title: "1 · НВ4 красный", color: "#d7322d", left: "cut" as const, right: "cut" as const };
  const second = { ...wire, key: "u2", label: "А1:2 → В1:2" };
  mount({ ops, art: {
    wires: [wire, second], tubes: [], crimped: [{ ...wire, left: "terminal", right: "terminal" }, { ...second, left: "terminal", right: "terminal" }],
    crimpedBy: { A: [{ ...wire, left: "terminal" }], B: [{ ...wire, right: "terminal" }, { ...second, right: "terminal" }] },
    faces: { A: [], B: [] },
  } });
  expect(cards().map((card) => card.querySelector(".rc-title")?.textContent)).toEqual([
    "Нарезанные провода", "Обжатые провода, сторона А", "Сторона А собрана", "Обжатые провода, сторона В", "Готовый кабель"]);
  const [, crimpA, , crimpB] = cards();
  expect([...crimpA.querySelectorAll(".rc-op-name")].map((name) => name.textContent)).toEqual(["Опрессовка терминалов А1"]);
  expect([...crimpB.querySelectorAll(".rc-op-name")].map((name) => name.textContent)).toEqual(["Опрессовка терминалов В1"]);
  expect(crimpA.querySelectorAll(".rc-wire svg")).toHaveLength(1);
  expect(crimpA).toHaveTextContent("А1:1 → В1:1");
  expect(crimpB.querySelectorAll(".rc-wire svg")).toHaveLength(2);
});

/* ── Управление мышью («Изменить схему») ─────────────────────────────────────────────────────────────────────── */

afterEach(() => { delete (document as { elementFromPoint?: unknown }).elementFromPoint; });
const edit = () => fireEvent.click(screen.getByRole("button", { name: "Изменить схему" }));
const row = (name: RegExp) => op(name).closest("li") as HTMLElement;
const card = (key: string) => canvas().querySelector(`[data-card="${key}"]`) as HTMLElement;
const view = () => canvas().closest(".rc-view") as HTMLElement;
const title = (key: string) => card(key).querySelector(".rc-title") as HTMLElement;
/** Тянет `source` (операцию или карточку) и бросает на `target`; `at` — где по вертикали (прямоугольник цели 0…100). */
function dragTo(source: Element, target: Element | null, at = 50) {
  document.elementFromPoint = vi.fn(() => target);
  if (target) target.getBoundingClientRect = () => ({ top: 0, height: 100, left: 0, width: 100, right: 100, bottom: 100, x: 0, y: 0, toJSON: () => ({}) });
  fireEvent.pointerDown(source, { button: 0, clientX: 5, clientY: 5 });
  fireEvent.pointerMove(window, { clientX: 40, clientY: at });
  fireEvent.pointerUp(window, { clientX: 40, clientY: at });
}
const blocksOf = (route: Route) => route.blocks.map((item) => `${item.key}:${item.ops.join(",")}${item.final ? "!" : ""}`);
const arrowsOf = (route: Route) => route.edges.map((edge) => `${edge.from}>${edge.to}`);

it("mouse control is on only in «Изменить схему»: before it, dragging an operation does nothing", () => {
  const onChange = mount({ value: manual });
  dragTo(op(/Резка провода/), card("b4"));
  expect(onChange).not.toHaveBeenCalled();
});

it("editing draws a card per block (not per stage), shows the hint about the mouse and a disabled «Отменить»", () => {
  mount();
  edit();
  expect(cards().map((item) => item.querySelector(".rc-title")?.textContent)).toEqual(["Резка провода", "Резка ТУТ", "Надеть ТУТ", "Пайка разъёма", "Усадка ТУТ"]);
  expect(screen.queryByText(/Тяните операцию на другую/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Отменить" })).toBeDisabled();
});

it("drops an operation onto another operation: it joins that block after it, and the scheme turns manual", () => {
  const onChange = mount({ value: manual });
  edit();
  // Операция 2 была одна в блоке: блок b2 сливается с b3 («Надеть ТУТ»). Порядок ЭТК тот же — схема уходит без перенумерации.
  dragTo(op(/Резка ТУТ/), row(/Надеть ТУТ/), 80);
  expect(blocksOf(lastRoute(onChange))).toEqual(["b1:1", "b3:2,3", "b4:4,5!"]);
  expect(arrowsOf(lastRoute(onChange))).toEqual(["b1>b3", "b3>b4"]);
});

it("reorders operations inside a block and hands the new ETK order over", () => {
  const onMove = vi.fn();
  mount({ value: manual, onMove });
  edit();
  dragTo(op(/Усадка ТУТ/), row(/Пайка разъёма/), 20);
  // Усадка встала перед пайкой: порядок ЭТК 1, 2, 3, 5, 4; блок b4 по-прежнему из двух последних операций.
  expect(onMove).toHaveBeenCalledTimes(1);
  expect(onMove.mock.calls[0][0]).toEqual([1, 2, 3, 5, 4]);
  expect(blocksOf(onMove.mock.calls[0][1])).toEqual(["b1:1", "b2:2", "b3:3", "b4:4,5!"]);
});

it("drops an operation onto empty canvas: a new block feeds the one it came from, the ETK is renumbered", () => {
  const onMove = vi.fn();
  mount({ value: manual, onMove });
  edit();
  dragTo(op(/Усадка ТУТ/), view());
  expect(onMove.mock.calls[0][0]).toEqual([1, 2, 3, 5, 4]);
  const route: Route = onMove.mock.calls[0][1];
  expect(blocksOf(route)).toEqual(["b1:1", "b2:2", "b3:3", "b5:4", "b4:5!"]);
  expect(arrowsOf(route)).toContain("b5>b4");
});

it("drops an operation on a block, not on one of its rows: the operation goes to the end of that block", () => {
  const onMove = vi.fn();
  mount({ value: manual, onMove });
  edit();
  // «Надеть ТУТ» была единственной в b3 — блок уходит вместе с ней в b4, в конец; порядок ЭТК: 4, 5 раньше неё.
  dragTo(op(/Надеть ТУТ/), card("b4"));
  expect(onMove.mock.calls[0][0]).toEqual([1, 2, 4, 5, 3]);
  expect(blocksOf(onMove.mock.calls[0][1])).toEqual(["b1:1", "b2:2", "b4:3,4,5!"]);
});

it("drags a block onto another block by its card: the lower part merges, the upper part makes it feed that block", () => {
  const onChange = mount({ value: manual });
  edit();
  dragTo(title("b2"), card("b3"), 80);
  expect(blocksOf(lastRoute(onChange))).toEqual(["b1:1", "b3:2,3", "b4:4,5!"]);
  dragTo(title("b1"), card("b4"), 10);
  expect(arrowsOf(lastRoute(onChange))).toEqual(["b1>b4", "b2>b3", "b3>b4"]);
});

it("refuses a drop the server would refuse and says why: nothing is sent", () => {
  const onChange = mount({ value: manual });
  edit();
  // b3 на верхнюю часть b1: b3 питал бы b1, а b1 уже питает b3 — цикл.
  dragTo(title("b3"), card("b1"), 10);
  expect(onChange).not.toHaveBeenCalled();
  expect(screen.getByRole("alert")).toHaveTextContent(/Так нельзя: схема зациклена/);
});

it("a click right after a drag does not open the block of the operation; a plain click still does", () => {
  mount({ value: manual });
  edit();
  dragTo(op(/Резка провода/), null);
  fireEvent.click(op(/Резка провода/));
  expect(screen.queryByRole("region", { name: /Блок/ })).not.toBeInTheDocument();
  fireEvent.click(op(/Резка провода/));
  expect(screen.getByRole("region", { name: "Блок «Резка провода»" })).toBeInTheDocument();
});

it("Esc cancels a drag; a touch pointer does not drag at all (the block panel controls stay)", () => {
  const onChange = mount({ value: manual });
  edit();
  document.elementFromPoint = vi.fn(() => card("b4"));
  fireEvent.pointerDown(op(/Резка провода/), { button: 0, clientX: 5, clientY: 5 });
  fireEvent.pointerMove(window, { clientX: 50, clientY: 50 });
  expect(document.querySelector(".route-ghost")).toBeInTheDocument();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(document.querySelector(".route-ghost")).not.toBeInTheDocument();
  fireEvent.pointerUp(window, { clientX: 50, clientY: 50 });
  fireEvent.pointerDown(op(/Резка провода/), { button: 0, pointerType: "touch", clientX: 5, clientY: 5 });
  fireEvent.pointerMove(window, { clientX: 50, clientY: 50 });
  expect(document.querySelector(".route-ghost")).not.toBeInTheDocument();
  expect(onChange).not.toHaveBeenCalled();
});

it("shows where the drop lands while dragging: the target card is lit and the ghost says what happens", () => {
  mount({ value: manual });
  edit();
  document.elementFromPoint = vi.fn(() => card("b4"));
  fireEvent.pointerDown(op(/Резка ТУТ/), { button: 0, clientX: 5, clientY: 5 });
  fireEvent.pointerMove(window, { clientX: 50, clientY: 50 });
  expect(card("b4")).toHaveClass("is-drop-join");
  expect(document.querySelector(".route-ghost")).toHaveTextContent("2. Резка ТУТ");
  expect(document.querySelector(".route-ghost")).toHaveTextContent("В блок «Усадка ТУТ»");
  fireEvent.pointerUp(window);
});

/** Редактор с настоящим состоянием: операции перенумеровываются по `onMove`, как это делает генератор. */
function Live() {
  const [ops, setOps] = useState(OPS);
  const [route, setRoute] = useState<Route | null>(manual);
  const onMove = (order: number[], next: Route | null) => {
    setOps((current) => order.map((old, at) => ({ ...current[old - 1], position: at + 1 })));
    setRoute(next);
  };
  return <><output aria-label="порядок">{ops.map((item) => item.key).join(",")}</output>
    <RouteEditor ops={ops} finalName="Жгут XH" quantity={5} value={route} onChange={setRoute} onMove={onMove} /></>;
}

it("undoes the last move with «Отменить» and Ctrl+Z, including the ETK order it changed", () => {
  render(<Live />);
  edit();
  const order = () => screen.getByLabelText("порядок").textContent;
  dragTo(op(/Усадка ТУТ/), view());
  expect(order()).toBe("cut,cutTut,insTut,heat,solder");
  expect(screen.getByRole("button", { name: "Отменить" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
  expect(order()).toBe("cut,cutTut,insTut,solder,heat");
  expect(cards().map((item) => item.dataset.card)).toEqual(["b1", "b2", "b3", "b4"]);
  expect(screen.getByRole("button", { name: "Отменить" })).toBeDisabled();
  // Две правки подряд — Ctrl+Z снимает по одной, и на русской раскладке тоже.
  dragTo(op(/Резка ТУТ/), row(/Надеть ТУТ/), 80);
  expect(cards()).toHaveLength(3);
  dragTo(op(/Усадка ТУТ/), view());
  expect(cards()).toHaveLength(4);
  act(() => { fireEvent.keyDown(window, { key: "z", code: "KeyZ", ctrlKey: true }); });
  expect(cards()).toHaveLength(3);
  act(() => { fireEvent.keyDown(window, { key: "я", code: "KeyZ", ctrlKey: true }); });
  expect(cards().map((item) => item.dataset.card)).toEqual(["b1", "b2", "b3", "b4"]);
  expect(order()).toBe("cut,cutTut,insTut,solder,heat");
});

it("a drag on the automatic scheme makes it manual; undo hands back null (automatic)", () => {
  const onChange = mount();
  edit();
  // Автоматическая схема — блок на операцию: усадка (5, итог) бросается на пайку (4) — блоки сливаются, итог сохраняется.
  dragTo(op(/Усадка ТУТ/), row(/Пайка разъёма/), 80);
  expect(lastRoute(onChange)).not.toBeNull();
  expect(lastRoute(onChange).blocks.filter((item) => item.final).map((item) => item.ops)).toEqual([[4, 5]]);
});
