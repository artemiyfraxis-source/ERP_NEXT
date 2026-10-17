import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { RouteDiagram } from "./RouteDiagram";
import type { Route, RouteBlock, RouteEdge } from "./routeLogic";

const block = (key: string, ops: number[], final = false): RouteBlock => ({ key, name: `Блок ${key}`, ops, final, components: [] });
/* b1 и b2 сходятся в b3 (итог); b4 без операций — тоже вход итога. */
const route: Route = {
  blocks: [block("b1", [1]), block("b2", [2]), block("b4", []), block("b3", [3], true)],
  edges: [{ from: "b1", to: "b3", per_unit: 2 }, { from: "b2", to: "b3", per_unit: 1 }, { from: "b4", to: "b3", per_unit: 1 }],
};

function mount(extra: Partial<Parameters<typeof RouteDiagram>[0]> = {}) {
  const props = { onSelect: vi.fn(), onPerUnit: vi.fn(), ...extra };
  render(<RouteDiagram route={route} title={(item) => item.name} numbers={(item) => item.ops.join(", ")} totals={new Map()} selected="b1"
    edgeLabel={(edge: RouteEdge) => `Штук ${edge.from} → ${edge.to}`} {...props} />);
  return props;
}

it("draws the empty block as one compact line and has no sideways scroller", () => {
  mount();
  const empty = screen.getByTitle("Блок b4");
  expect(empty).toHaveClass("is-empty");
  expect(within(empty).getByText("нет операций")).toBeInTheDocument();
  expect(document.querySelector(".route-scroll")).toBeNull();
});

it("shows no hooks unless the caller passes them", () => {
  mount();
  expect(screen.queryByRole("button", { name: /Добавить вход/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /Соединить блок/ })).toBeNull();
  expect(document.querySelectorAll("input.route-per").length).toBe(3); // как раньше: число правится прямо на стрелке
});

it("adds an input with the plus on the block", () => {
  const onAddInput = vi.fn();
  const props = mount({ onAddInput });
  fireEvent.click(screen.getByRole("button", { name: "Добавить вход в блок «Блок b3»" }));
  expect(onAddInput).toHaveBeenCalledWith("b3");
  expect(props.onSelect).not.toHaveBeenCalled();
});

it("connects two blocks with two taps and cancels with Esc or a second tap", () => {
  const onConnect = vi.fn();
  const props = mount({ onConnect });
  const arrow = (name: string) => screen.getByRole("button", { name: `Соединить блок «${name}» с другим блоком` });
  fireEvent.click(arrow("Блок b1"));
  expect(screen.getByRole("status")).toHaveTextContent("Esc — отмена");
  fireEvent.click(screen.getByTitle("Блок b2"));
  expect(onConnect).toHaveBeenCalledWith("b1", "b2");
  expect(props.onSelect).not.toHaveBeenCalled(); // нажатие во время соединения не выбирает блок
  expect(screen.queryByRole("status")).toBeNull();

  fireEvent.click(arrow("Блок b1"));
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("status")).toBeNull();
  fireEvent.click(arrow("Блок b1"));
  fireEvent.click(arrow("Блок b1"));
  expect(screen.queryByRole("status")).toBeNull();
  expect(onConnect).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByTitle("Блок b2")); // вне режима — обычный выбор
  expect(props.onSelect).toHaveBeenCalledWith("b2");
});

it("opens a popup on the «N шт» pill to change the number or remove the link", () => {
  const onEdgeEdit = vi.fn(), onEdgeQuantity = vi.fn(), onEdgeRemove = vi.fn();
  mount({ onEdgeEdit, onEdgeQuantity, onEdgeRemove });
  fireEvent.click(screen.getByTitle("Штук b1 → b3"));
  expect(onEdgeEdit).toHaveBeenCalledWith(route.edges[0]);
  const dialog = screen.getByRole("dialog", { name: "Штук b1 → b3" });
  fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "5" } });
  expect(onEdgeQuantity).toHaveBeenCalledWith(route.edges[0], 5);
  fireEvent.click(within(dialog).getByRole("button", { name: "Убрать связь" }));
  expect(onEdgeRemove).toHaveBeenCalledWith(route.edges[0]);
  expect(screen.queryByRole("dialog")).toBeNull();

  fireEvent.click(screen.getByTitle("Штук b1 → b3"));
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("starts the edge and puts the «N шт» pill below the real bottom of an empty block that shares a row with a card", () => {
  const props = mount({ onEdgeEdit: vi.fn() });
  const px = (value: string) => Number.parseFloat(value);
  const cell = (key: string) => screen.getByTitle(`Блок ${key}`).closest(".route-cell") as HTMLElement;
  const bottom = (key: string) => px(cell(key).style.top) + px(cell(key).style.height);
  const empty = cell("b4"), card = cell("b1");
  expect(px(empty.style.height)).toBeLessThan(px(card.style.height));
  expect(px(empty.style.top)).toBeGreaterThan(px(card.style.top)); // пустой блок стоит по центру строки карточек
  const pill = (from: string) => screen.getByTitle(`Штук ${from} → b3`).closest(".route-edge-label") as HTMLElement;
  // значок: его верх (центр минус полвысоты 13 px) не выше низа блока-источника — на блок не наезжает
  expect(px(pill("b4").style.top) - 13).toBeGreaterThanOrEqual(bottom("b4"));
  expect(px(pill("b1").style.top) - 13).toBeGreaterThanOrEqual(bottom("b1"));
  // начало стрелки — от настоящего низа пустого блока, а не от верха строки + высота строки
  const starts = Array.from(document.querySelectorAll("svg line")).map((line) => px(line.getAttribute("y1")!));
  expect(starts).toContain(bottom("b4"));
  // клик по пустому блоку выбирает его
  fireEvent.click(screen.getByTitle("Блок b4"));
  expect(props.onSelect).toHaveBeenCalledWith("b4");
});

it("lists the linked inputs on a block card and shows nothing for a block without inputs", () => {
  mount();
  expect(screen.getByTitle("Блок b3")).toHaveTextContent("входы: Блок b1, Блок b2, Блок b4");
  expect(screen.getByTitle("Блок b1").textContent).not.toMatch(/вход/);
  expect(document.body.textContent).not.toContain("с входами 0");
});

it("marks blocks by kind, quantity and the master's check only when the caller passes marks", () => {
  mount({ marks: (item) => (item.key === "b1" ? { kind: "semiFinished", checked: true } : item.key === "b2" ? { kind: "assembly", quantity: 3 } : { kind: item.final ? "final" : "semiFinished" }) });
  expect(screen.getByTitle("Блок b1")).toHaveTextContent("п/ф");
  expect(within(screen.getByTitle("Блок b1")).getByTitle("Проверено мастером")).toHaveTextContent("✓");
  expect(screen.getByTitle("Блок b1")).toHaveTextContent("✓ 1 оп.");
  expect(screen.getByTitle("Блок b2")).toHaveTextContent("сборка");
  expect(screen.getByTitle("Блок b2")).toHaveTextContent("3 шт · 1 оп.");
  expect(within(screen.getByTitle("Блок b3")).getAllByText("итог")).toHaveLength(1);
  expect(screen.getByTitle("Блок b4")).toHaveTextContent("п/ф"); // пустой блок — тоже с видом
});

it("draws no kind tags without marks", () => {
  mount();
  expect(document.querySelector(".route-block-kind, .route-block-checked")).toBeNull();
});
