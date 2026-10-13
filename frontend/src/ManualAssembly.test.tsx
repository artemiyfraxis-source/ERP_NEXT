import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { expect, it, vi } from "vitest";
import { ManualAssembly } from "./ManualAssembly";
import { addBlock, addCustomOp, emptyManual, selectBlock, setBlockChecked, setBlockQuantity, setPerUnit, updateOp, type ManualState } from "./manualLogic";

/* «Ручная сборка» карточками (владелец 13.10): блоки — карточки холста маршрута, как «Маршрут КБn». */

/** Итог «Жгут» (своя «Контроль») ← «Провод» (резка, опрессовка; проверен мастером) и «Трубка» (×4, берёт 2 шт). */
function scheme(): ManualState {
  let state = addCustomOp(emptyManual(), "b1");
  state = updateOp(state, 1, { label: "Контроль", opSec: "30" });
  state = addBlock(state, "Провод"); // b2 → b1
  state = addCustomOp(state); // seq 2
  state = updateOp(state, 2, { label: "Резка", prepMin: "2", opSec: "6" });
  state = addCustomOp(state); // seq 3
  state = updateOp(state, 3, { label: "Опрессовка", opSec: "12" });
  state = setBlockChecked(state, "b2", true);
  state = selectBlock(state, "b1");
  state = addBlock(state, "Трубка"); // b3 → b1
  state = addCustomOp(state); // seq 4
  state = updateOp(state, 4, { label: "Резка ТУТ", opSec: "3" });
  state = setBlockQuantity(state, "b3", 4);
  return setPerUnit(selectBlock(state, "b1"), "b3", "b1", 2);
}

function Host({ initial, spy }: { initial: ManualState; spy: (state: ManualState) => void }) {
  const [state, setState] = useState(initial);
  return <ManualAssembly state={state} library={[]} opRecs={[]} components={[]} partQty={10} finalName="Жгут"
    onChange={(next) => setState((current) => { const value = next(current); spy(value); return value; })} />;
}

function mount(initial = scheme()) {
  const spy = vi.fn();
  render(<Host initial={initial} spy={spy} />);
  return spy;
}

const canvas = () => screen.getByRole("group", { name: "Блоки схемы" });
const card = (title: string) => {
  const found = within(canvas()).getAllByRole("article").filter((item) => item.querySelector(".rc-title")?.textContent === title);
  expect(found).toHaveLength(1);
  return found[0];
};

it("draws every block as a route card: kind, title, count, operations with numbers and times, links with pieces", () => {
  mount();
  const cards = within(canvas()).getAllByRole("article");
  // Порядок схемы: входы раньше итога; итог — ФИНАЛ внизу, со строкой изделия.
  expect(cards.map((item) => item.querySelector(".rc-title")?.textContent)).toEqual(["Провод", "Трубка", "Контроль"]);
  expect(card("Провод")).toHaveClass("rc-card", "is-semiFinished");
  expect(card("Провод").querySelector(".rc-kind")).toHaveTextContent("ПОЛУФАБРИКАТ");
  expect(card("Контроль")).toHaveClass("is-final");
  expect(card("Контроль").querySelector(".rc-kind")).toHaveTextContent("ФИНАЛ");
  expect(card("Контроль").querySelector(".rc-sub")).toHaveTextContent("итог: Жгут");
  expect(card("Провод").querySelector(".rc-meta")).toHaveTextContent("2 оп. · 5 мин");
  expect(card("Трубка").querySelector(".rc-meta")).toHaveTextContent("4 шт · 1 оп. · 0,5 мин");
  // Операции — строками с номером по схеме и нормой на партию.
  const rows = [...card("Провод").querySelectorAll(".rc-op")].map((row) => row.textContent);
  expect(rows).toEqual(["1Резка3 мин", "2Опрессовка2 мин"]);
  expect(card("Трубка").querySelector(".rc-op-no")).toHaveTextContent("3");
  expect(within(card("Провод")).getByTitle("Проверено мастером")).toBeInTheDocument();
  expect(within(card("Трубка")).queryByTitle("Проверено мастером")).not.toBeInTheDocument();
  // Стрелки: число штук — только где не одна; «+» и «×» на каждой.
  expect(within(canvas()).getByTitle("Берёт 2 шт на одну штуку")).toHaveTextContent("2 шт");
  expect(within(canvas()).getAllByText(/шт$/, { selector: ".rc-qty" })).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Вставить карточку: «Провод» → «Контроль»" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Удалить связь: «Трубка» → «Контроль»" })).toBeInTheDocument();
  // Собранная схема открывается без панели блока.
  expect(screen.queryByRole("region", { name: /^Блок «/ })).not.toBeInTheDocument();
});

it("an empty block says so on its card, and a new assembly opens with the product block pane", () => {
  mount(emptyManual());
  const product = card("Жгут");
  expect(product.querySelector(".rc-meta")).toHaveTextContent("нет операций");
  expect(product.querySelector(".rc-ops")).toBeNull();
  expect(product).toHaveAttribute("aria-current", "true");
  expect(screen.getByRole("region", { name: "Блок «Жгут»" })).toBeInTheDocument();
});

it("edits an operation from its card row: the pane opens on it and the card follows the new norm and name", () => {
  const spy = mount();
  fireEvent.click(within(card("Провод")).getByRole("button", { name: /Резка/ }));
  const pane = screen.getByRole("region", { name: "Блок «Провод»" });
  expect(card("Провод")).toHaveAttribute("aria-current", "true");
  expect(card("Провод").querySelector(".rc-op")).toHaveClass("is-selected");
  expect(within(pane).getByLabelText("Подготовка, мин: Резка")).toHaveValue("2");
  fireEvent.change(within(pane).getByLabelText("Подготовка, мин: Резка"), { target: { value: "10" } });
  fireEvent.change(within(pane).getByLabelText("Название операции 1"), { target: { value: "Резка в размер" } });
  expect(spy.mock.calls.at(-1)![0].ops.find((op: { seq: number }) => op.seq === 2)).toMatchObject({ label: "Резка в размер", prepMin: "10" });
  // 10 мин + 6 с × 10 шт = 11 мин; блок — 11 + 2 = 13 мин.
  expect(card("Провод").querySelector(".rc-op")).toHaveTextContent("1Резка в размер11 мин");
  expect(card("Провод").querySelector(".rc-meta")).toHaveTextContent("2 оп. · 13 мин");
});

it("a card opens its block pane by Enter, Esc closes it; «+ Вход» on a card adds an input block to it", () => {
  mount();
  fireEvent.keyDown(card("Трубка"), { key: "Enter" });
  expect(screen.getByRole("region", { name: "Блок «Трубка»" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Блок «Трубка»" }).querySelector(".ma-kind")).toHaveTextContent("ПОЛУФАБРИКАТ");
  fireEvent.keyDown(card("Трубка"), { key: "Escape" });
  expect(screen.queryByRole("region", { name: /^Блок «/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Добавить вход в блок «Трубка»" }));
  expect(within(canvas()).getAllByRole("article")).toHaveLength(4);
  expect(card("Блок 4")).toHaveAttribute("aria-current", "true");
  expect(screen.getByRole("button", { name: "Вставить карточку: «Блок 4» → «Трубка»" })).toBeInTheDocument();
});

it("the pane sets how many pieces an input takes; the arrow shows it", () => {
  mount();
  fireEvent.keyDown(card("Контроль"), { key: "Enter" });
  fireEvent.change(screen.getByLabelText("Штук из «Провод» на один «Контроль»"), { target: { value: "3" } });
  expect(within(canvas()).getByTitle("Берёт 3 шт на одну штуку")).toBeInTheDocument();
});
