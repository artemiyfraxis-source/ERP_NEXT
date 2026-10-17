import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { FacesThumb, RouteCanvas, WireThumbs, spreadLinkTools, type CanvasCard } from "./RouteCanvas";

const CARDS: CanvasCard[] = [
  { id: "wires", kind: "semiFinished", title: "Нарезанные провода", meta: "3 комплектующие · 1 шт · 2 мин", x: 32, y: 32, width: 280, height: 264 },
  { id: "final", kind: "final", title: "Готовый жгут", meta: "6 комплектующих · 1 шт · 5 мин", x: 32, y: 360, width: 280, height: 264, warning: "Нет в чертеже: 1 часть" },
];
const LINK = { id: "l1", from: "wires", to: "final", label: "«Нарезанные провода» → «Готовый жгут»" };

function mount(props: Partial<Parameters<typeof RouteCanvas>[0]> = {}) {
  const handlers = { onSelect: vi.fn(), onMove: vi.fn(), onInsert: vi.fn(), onRemoveLink: vi.fn(), onRemoveCard: vi.fn(), onAutoLayout: vi.fn() };
  render(<RouteCanvas cards={CARDS} links={[LINK]} label="Карточки маршрута" height={600} {...handlers} {...props} />);
  return handlers;
}
const card = (title: string) => screen.getByRole("article", { name: new RegExp(title) });

it("draws each card with its kind, title, meta and warning and a link between them", () => {
  mount();
  const plane = screen.getByRole("group", { name: "Карточки маршрута" });
  expect(within(card("Нарезанные провода")).getByText("ПОЛУФАБРИКАТ")).toBeInTheDocument();
  expect(within(card("Готовый жгут")).getByText("ФИНАЛ")).toBeInTheDocument();
  expect(card("Готовый жгут")).toHaveTextContent("6 комплектующих · 1 шт · 5 мин");
  expect(card("Готовый жгут")).toHaveTextContent("Нет в чертеже: 1 часть");
  expect(card("Готовый жгут")).toHaveStyle({ left: "32px", top: "360px", width: "280px", height: "264px" });
  expect(plane.querySelectorAll("path.rc-link")).toHaveLength(1);
  expect(plane.querySelector(".rc-qty")).toBeNull(); // одна штука на связи не пишется
});

it("writes the number on a link only when it is not one", () => {
  mount({ links: [{ ...LINK, qty: 2 }] });
  expect(screen.getByText("2 шт")).toHaveAttribute("title", "Берёт 2 шт на одну штуку");
});

it("inserts a card or removes the link with the buttons on the link and removes a card with its ×", () => {
  const handlers = mount();
  fireEvent.click(screen.getByRole("button", { name: "Вставить карточку: «Нарезанные провода» → «Готовый жгут»" }));
  expect(handlers.onInsert).toHaveBeenCalledWith("l1");
  fireEvent.click(screen.getByRole("button", { name: "Удалить связь: «Нарезанные провода» → «Готовый жгут»" }));
  expect(handlers.onRemoveLink).toHaveBeenCalledWith("l1");
  fireEvent.click(screen.getByRole("button", { name: "Удалить карточку «Готовый жгут»" }));
  expect(handlers.onRemoveCard).toHaveBeenCalledWith("final");
  fireEvent.click(screen.getByRole("button", { name: "Расставить" }));
  expect(handlers.onAutoLayout).toHaveBeenCalled();
});

it("selects with a click, opens with Enter, moves with arrows (Shift — farther), removes with Delete, Esc clears", () => {
  const handlers = mount({ selected: "wires" });
  const wires = card("Нарезанные провода");
  fireEvent.pointerDown(wires, { button: 0, clientX: 10, clientY: 10, pointerId: 1 });
  fireEvent.pointerUp(wires, { pointerId: 1 });
  expect(handlers.onSelect).toHaveBeenLastCalledWith("wires");
  fireEvent.keyDown(card("Готовый жгут"), { key: "Enter" });
  expect(handlers.onSelect).toHaveBeenLastCalledWith("final");
  fireEvent.keyDown(wires, { key: "ArrowRight" });
  expect(handlers.onMove).toHaveBeenLastCalledWith("wires", 40, 32);
  fireEvent.keyDown(wires, { key: "ArrowDown", shiftKey: true });
  expect(handlers.onMove).toHaveBeenLastCalledWith("wires", 32, 72);
  fireEvent.keyDown(wires, { key: "Delete" });
  expect(handlers.onRemoveCard).toHaveBeenLastCalledWith("wires");
  fireEvent.keyDown(wires, { key: "Escape" });
  expect(handlers.onSelect).toHaveBeenLastCalledWith(null);
});

it("drags a card to a new place on the canvas", () => {
  const handlers = mount();
  const wires = card("Нарезанные провода");
  fireEvent.pointerDown(wires, { button: 0, clientX: 100, clientY: 100, pointerId: 1 });
  fireEvent.pointerMove(wires, { clientX: 160, clientY: 140, pointerId: 1 });
  fireEvent.pointerUp(wires, { pointerId: 1 });
  expect(handlers.onMove).toHaveBeenCalledWith("wires", 92, 72);
  expect(handlers.onSelect).not.toHaveBeenCalled();
});

it("zooms in and out and back to the whole route", () => {
  mount();
  expect(screen.getByText("100 %")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Уменьшить" }));
  expect(screen.getByText("90 %")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Увеличить" }));
  fireEvent.click(screen.getByRole("button", { name: "Увеличить" }));
  expect(screen.getByText("110 %")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "По размеру" }));
  expect(screen.getByText("100 %")).toBeInTheDocument();
});

it("is read-only without handlers: no buttons, cards are not focusable", () => {
  render(<RouteCanvas cards={CARDS} links={[LINK]} label="Карточки маршрута" controls={false} />);
  expect(screen.queryAllByRole("button")).toHaveLength(0);
  expect(card("Готовый жгут")).not.toHaveAttribute("tabindex");
});

it("draws cut wires with their contacts and the faces of the side connectors", () => {
  const wires = Array.from({ length: 6 }, (_, i) => ({ key: `w${i}`, label: `А1:${i + 1} → В1:${i + 1}`, title: `провод ${i + 1}`, color: "#d7322d", left: "copper" as const, right: "terminal" as const }));
  const { container } = render(<><WireThumbs wires={wires} /><FacesThumb faces={[{ key: "А1", label: "А1 · XHP-2", housing: null, byPin: null }]} /></>);
  expect(screen.getByText("А1:1 → В1:1")).toBeInTheDocument();
  expect(screen.queryByText("А1:5 → В1:5")).not.toBeInTheDocument();
  expect(screen.getByText("и ещё 2 проводов")).toBeInTheDocument();
  expect(container.querySelectorAll(".rc-wire svg")).toHaveLength(4);
  // Разъёма нет в базе разъёмов — подпись в рамке вместо торца.
  expect(container.querySelector(".rc-face-none")).toHaveTextContent("А1");
  expect(container.querySelector("figcaption")).toHaveTextContent("А1 · XHP-2");
});

it("keeps the marks of two arrows from one card apart, each button still reachable by keyboard", () => {
  const cards: CanvasCard[] = [
    { ...CARDS[0], x: 100, width: 100 },
    { id: "a", kind: "semiFinished", title: "Сторона А", meta: "1 шт", x: 0, y: 360, width: 100, height: 100 },
    { id: "b", kind: "semiFinished", title: "Сторона В", meta: "1 шт", x: 120, y: 360, width: 100, height: 100 },
  ];
  mount({ cards, links: [{ id: "la", from: "wires", to: "a", label: "к А", qty: 2 }, { id: "lb", from: "wires", to: "b", label: "к В", qty: 3 }] });
  const marks = Array.from(document.querySelectorAll<HTMLElement>(".rc-link-tools")).map((item) => parseFloat(item.style.left));
  expect(marks).toHaveLength(2);
  expect(Math.abs(marks[0] - marks[1])).toBeGreaterThanOrEqual(64);
  const buttons = screen.getAllByRole("button", { name: /Вставить карточку|Удалить связь/ });
  expect(buttons).toHaveLength(4);
  buttons[2].focus();
  expect(document.activeElement).toBe(buttons[2]);
});

it("spreads neighbours of one row, leaves other rows and lone marks alone", () => {
  const spread = spreadLinkTools([{ id: "a", x: 100, y: 50 }, { id: "b", x: 110, y: 50 }, { id: "c", x: 105, y: 400 }]);
  expect(spread.get("b")! - spread.get("a")!).toBe(64);
  expect((spread.get("a")! + spread.get("b")!) / 2).toBeCloseTo(105, 0);
  expect(spread.get("c")).toBe(105);
});
