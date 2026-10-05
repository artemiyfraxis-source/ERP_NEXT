import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { RouteEditor } from "./RouteEditor";
import { numberRange } from "./RouteDiagram";
import { deriveRoute, type Route, type RouteOp } from "./routeLogic";

/* Жгут с пайкой: резка провода (1) и резка ТУТ (2) сходятся в «Надеть ТУТ» (3), дальше пайка (4) и усадка (5) — изделие.
   Нормы на партию 5 шт: 10, 2, 3, 4 и 1 мин. */
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

it("draws the blocks and arrows of a branched scheme with time and inputs", () => {
  mount();
  const blocks = within(screen.getByRole("group", { name: "Блоки схемы" })).getAllByRole("button");
  expect(blocks).toHaveLength(5);
  expect(screen.getByText("автоматическая")).toBeInTheDocument();
  expect(screen.getByText(/5 блоков/)).toHaveTextContent("5 блоков · 5 оп. · на изделие 4 мин");
  // «Надеть ТУТ» берёт два входа: 0,6 мин на штуку, со входами 3 мин (0,6 + 2 + 0,4).
  const insTut = screen.getByRole("button", { name: /Надеть ТУТ/ });
  expect(insTut).toHaveTextContent("1 оп. · 0,6 мин/шт · с входами 3");
  expect(insTut.querySelector(".route-block-no")).toHaveTextContent("3");
  expect(screen.getByRole("button", { name: /Жгут XH/ })).toHaveTextContent("итог");
  // стрелки: резка → надеть, резка ТУТ → надеть, надеть → пайка, пайка → усадка
  expect(screen.getAllByRole("textbox")).toHaveLength(4);
  expect(screen.getByLabelText("Штук из «Резка ТУТ» на один «Надеть ТУТ»")).toHaveValue("1");
});

it("sends the scheme with the new per-unit quantity once an arrow is edited", () => {
  const onChange = mount();
  fireEvent.change(screen.getByLabelText("Штук из «Резка ТУТ» на один «Надеть ТУТ»"), { target: { value: "2,5" } });
  const route = lastRoute(onChange);
  expect(route.edges.find((edge) => edge.from === "b2" && edge.to === "b3")?.per_unit).toBe(2.5);
  expect(route.edges.filter((edge) => edge.per_unit === 1)).toHaveLength(3);
  expect(route.blocks.map((block) => block.ops)).toEqual([[1], [2], [3], [4], [5]]);
  expect(route.blocks.every((block) => block.name === "")).toBe(true);
});

it("ignores a quantity that is not a positive number", () => {
  const onChange = mount();
  const input = screen.getByLabelText("Штук из «Резка ТУТ» на один «Надеть ТУТ»");
  fireEvent.change(input, { target: { value: "0" } });
  fireEvent.change(input, { target: { value: "abc" } });
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.blur(input);
  expect(input).toHaveValue("1");
});

it("merges two adjacent blocks into one with both operations and the downstream block's name", () => {
  const onChange = mount();
  fireEvent.click(screen.getByRole("button", { name: /Пайка разъёма/ }));
  const panel = screen.getByRole("region", { name: "Блок «Пайка разъёма»" });
  fireEvent.change(within(panel).getByLabelText("Объединить с блоком"), { target: { value: "b5" } });
  fireEvent.click(within(panel).getByRole("button", { name: "Объединить" }));
  const route = lastRoute(onChange);
  expect(route.blocks.map((block) => [block.key, block.ops, block.final])).toEqual([
    ["b1", [1], false], ["b2", [2], false], ["b3", [3], false], ["b5", [4, 5], true],
  ]);
  expect(route.edges.map((edge) => `${edge.from}>${edge.to}`)).toEqual(["b1>b3", "b2>b3", "b3>b5"]);
});

it("splits a block after the chosen operation and gives the inputs to the first part", () => {
  const onChange = mount({ value: manual });
  fireEvent.click(screen.getByRole("button", { name: /Жгут XH/ }));
  const panel = screen.getByRole("region", { name: "Блок «Жгут XH»" });
  fireEvent.change(within(panel).getByLabelText("Разделить после операции"), { target: { value: "4" } });
  fireEvent.click(within(panel).getByRole("button", { name: "Разделить" }));
  const route = lastRoute(onChange);
  expect(route.blocks.map((block) => [block.key, block.ops, block.final])).toEqual([
    ["b1", [1], false], ["b2", [2], false], ["b3", [3], false], ["b5", [4], false], ["b4", [5], true],
  ]);
  expect(route.edges.map((edge) => `${edge.from}>${edge.to}`)).toEqual(["b1>b3", "b2>b3", "b3>b5", "b5>b4"]);
});

it("moves an operation to another block and renames a block", () => {
  const onChange = mount({ value: manual, renderOp: (position) => <div>настройка операции {position}</div> });
  fireEvent.click(screen.getByRole("button", { name: /Жгут XH/ }));
  const panel = screen.getByRole("region", { name: "Блок «Жгут XH»" });
  expect(within(panel).getByText("настройка операции 4")).toBeInTheDocument();
  expect(within(panel).getByText("настройка операции 5")).toBeInTheDocument();
  fireEvent.change(within(panel).getByLabelText("Перенести операцию 4 в блок"), { target: { value: "b3" } });
  const moved = lastRoute(onChange);
  expect(moved.blocks.map((block) => [block.key, block.ops])).toEqual([["b1", [1]], ["b2", [2]], ["b3", [3, 4]], ["b4", [5]]]);
  fireEvent.change(within(panel).getByLabelText("Название блока"), { target: { value: "Готовый жгут" } });
  expect(lastRoute(onChange).blocks.find((block) => block.key === "b4")?.name).toBe("Готовый жгут");
});

it("returns to the automatic scheme with «Сбросить к автоматической»", () => {
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
  fireEvent.click(screen.getByRole("button", { name: /Надеть ТУТ/ }));
  const panel = screen.getByRole("region", { name: "Блок «Надеть ТУТ»" });
  fireEvent.click(within(panel).getByRole("button", { name: "Убрать вход «Резка ТУТ»" }));
  expect(onChange).not.toHaveBeenCalled();
  expect(screen.getAllByRole("alert").at(-1)).toHaveTextContent("Так нельзя: блок «Резка ТУТ» не доходит до итогового блока");
});

it("adds a second input to a block from a block that is not after it", () => {
  const onChange = mount({ value: manual });
  fireEvent.click(screen.getByRole("button", { name: /Жгут XH/ }));
  const panel = screen.getByRole("region", { name: "Блок «Жгут XH»" });
  const select = within(panel).getByLabelText("Добавить вход из блока");
  expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual(["— выберите —", "Резка провода", "Резка ТУТ"]);
  fireEvent.change(select, { target: { value: "b1" } });
  fireEvent.click(within(panel).getByRole("button", { name: "Добавить вход" }));
  expect(lastRoute(onChange).edges.map((edge) => `${edge.from}>${edge.to}`)).toEqual(["b1>b3", "b1>b4", "b2>b3", "b3>b4"]);
});

it("offers no input that would make the scheme loop", () => {
  mount({ value: manual });
  fireEvent.click(screen.getByRole("button", { name: /Надеть ТУТ/ }));
  const panel = screen.getByRole("region", { name: "Блок «Надеть ТУТ»" });
  // Оба источника уже подключены, а блоки после «Надеть ТУТ» дали бы цикл.
  expect(within(panel).queryByLabelText("Добавить вход из блока")).not.toBeInTheDocument();
});

it("shows a linear scheme as a single collapsed chain until it is opened", () => {
  const ops = OPS.slice(0, 1).concat({ ...OPS[3], position: 2 }, { ...OPS[4], position: 3 });
  const onChange = mount({ ops });
  expect(screen.queryByRole("button", { name: /Пайка/ })).not.toBeInTheDocument();
  expect(screen.getByText(/3 блока/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Показать схему" }));
  const chain = screen.getByRole("list", { name: "Блоки схемы" });
  expect(within(chain).getAllByRole("listitem")).toHaveLength(3);
  expect(within(chain).getAllByRole("textbox")).toHaveLength(2);
  fireEvent.change(within(chain).getByLabelText("Штук из «Резка провода» на один «Пайка разъёма»"), { target: { value: "3" } });
  expect(lastRoute(onChange).edges[0]).toEqual({ from: "b1", to: "b2", per_unit: 3 });
  fireEvent.click(screen.getByRole("button", { name: "Свернуть схему" }));
  expect(screen.queryByRole("list", { name: "Блоки схемы" })).not.toBeInTheDocument();
});

it("shows the server's text in an error banner and opens the scheme", () => {
  mount({ ops: OPS.slice(0, 3), error: "Схема полуфабрикатов: блок «Резка» не доходит до итогового блока", value: null });
  expect(screen.getByRole("alert")).toHaveTextContent("Схема полуфабрикатов: блок «Резка» не доходит до итогового блока");
  expect(screen.getByRole("alert")).toHaveClass("error-banner");
});

it("shows no time when the card has never been built", () => {
  mount({ ops: OPS.map((op) => ({ ...op, minutes: null })) });
  expect(screen.queryByText(/мин\/шт/)).not.toBeInTheDocument();
  expect(screen.getByText(/5 блоков/)).toHaveTextContent("5 блоков · 5 оп.");
  expect(screen.getByText(/5 блоков/)).not.toHaveTextContent("на изделие");
});

it("derives the same scheme as the ETK does from the operation types", () => {
  const route = deriveRoute(OPS);
  expect(route.edges.map((edge) => `${edge.from}>${edge.to}`)).toEqual(["b1>b3", "b2>b3", "b3>b4", "b4>b5"]);
  expect(route.blocks.find((block) => block.final)?.key).toBe("b5");
});

it("numbers operations the way the generator list does when an operation above has no template", () => {
  const ops = OPS.slice(0, 3).map((op) => ({ ...op, number: op.position + 1 }));
  mount({ ops, value: { blocks: [block("b1", [1]), block("b2", [2, 3], true)], edges: [{ from: "b1", to: "b2", per_unit: 1 }] }, skipped: [1] });
  expect(screen.getByText("Без шаблона, в схему не входят: 1")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Жгут XH/ }));
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
