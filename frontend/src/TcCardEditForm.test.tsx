import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { OperationRecordEdit } from "./api";
import { TcCardEditForm } from "./TcCardEditForm";

const DEFECTS = "ТИПОВЫЕ ДЕФЕКТЫ (NG) для резки:\n1. Дефекты жилы:\nПодрезка жил: Надрезы на проволочках.\nРаспушение жил: Потеря скрутки.\n" +
  "2. Дефекты изоляции:\nСмятие изоляции: Профиль сплющен.";

/** Карточка CUT_WIRE+1crimp+tin, как её отдаёт editDraft (ЭТК 719К.2 КБ1, операция 1), сокращённо. */
function card(): OperationRecordEdit {
  return {
    name: "Автомат резка + опрессовка + лужение",
    safety: "Работы проводить в обесточенном состоянии.",
    defects: DEFECTS,
    revision: "",
    steps: [
      { kind: "Операция", text: "Включить станок.", tolerance: "" },
      { kind: "Операция", text: "Произвести тестовый рез.", tolerance: "-" },
      { kind: "Контроль", text: "Измерить высоту опрессовки.", tolerance: "1,14 – 1,24 мм" },
    ],
    tooling: [
      { section: "Инструмент", name: "Линейка металлическая ГОСТ 427-75", inventory_number: "", program: "", parameters: "", norm: "", unit: "" },
      { section: "Оборудование", name: "BZW-5.0+Z | Станок резки/ обжим+лужение", inventory_number: "ОБ.098", program: "A8", parameters: "", norm: "", unit: "" },
      { section: "Инструмент", name: "EW-2550 | Микрометр", inventory_number: "ОБ.034.2", program: "", parameters: "", norm: "", unit: "" },
      { section: "Расходники", name: "Припой Sn63Pb37", inventory_number: "", program: "", parameters: "от 190 °С", norm: "0,5", unit: "Гр." },
    ],
  };
}

function mount(initial = card()) {
  const onSave = vi.fn(async () => undefined);
  render(<TcCardEditForm code="CUT_WIRE+1crimp+tin" number={1} nameEditable={false} initial={initial} saveNote="Сохранение — ревизия 2"
    onCancel={vi.fn()} onSave={onSave} />);
  const saved = (): OperationRecordEdit => {
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    return (onSave.mock.calls.at(-1) as unknown as [OperationRecordEdit])[0];
  };
  return { onSave, saved };
}

function menu(label: string, item: string) {
  fireEvent.click(screen.getByRole("button", { name: label }));
  fireEvent.click(screen.getByRole("menuitem", { name: item }));
}

it("shows the Russian name as the title, the card code under it and a short save line instead of the service text", () => {
  mount();
  expect(screen.getByRole("heading", { level: 3, name: "Автомат резка + опрессовка + лужение" })).toBeInTheDocument();
  expect(screen.getByText("CUT_WIRE+1crimp+tin")).toBeInTheDocument();
  expect(screen.getByText("Сохранение — ревизия 2")).toBeInTheDocument();
  expect(screen.queryByText(/запишется в журнал|Карточка операции в библиотеке/)).not.toBeInTheDocument();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
});

it("lays tooling out as one table with headers, grouped by the ETK sections with their own columns", () => {
  mount();
  const table = screen.getByRole("table", { name: "Оснастка" });
  expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent))
    .toEqual(["№", "Наименование", "Инв. №", "Программа / параметры", "Норма", "Ед.", ""]);
  expect(within(table).getAllByRole("rowgroup").slice(1).map((group) => group.getAttribute("aria-label")))
    .toEqual(["Инструмент", "Оборудование", "Расходники"]);
  const tools = within(table).getByRole("rowgroup", { name: "Инструмент" });
  expect(within(tools).getByLabelText("Инструмент 2: наименование")).toHaveValue("EW-2550 | Микрометр");
  expect(within(tools).getByLabelText("Инструмент 2: инв. №")).toHaveValue("ОБ.034.2");
  // У инструмента нет программы и нормы — пустых безымянных полей тоже нет.
  expect(within(tools).queryByLabelText(/Инструмент 1: (программа|параметры|норма|ед\.)/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("Оборудование 1: программа")).toHaveValue("A8");
  expect(screen.getByLabelText("Расходники 1: параметры")).toHaveValue("от 190 °С");
  expect(screen.getByLabelText("Расходники 1: норма")).toHaveValue("0,5");
  expect(screen.getByLabelText("Расходники 1: ед.")).toHaveValue("Гр.");
  expect(screen.queryByLabelText("Расходники 1: инв. №")).not.toBeInTheDocument();
});

it("saves an unedited card exactly as it came", () => {
  const initial = card();
  const { saved } = mount(initial);
  expect(saved()).toEqual(initial);
});

it("switches a step between Шаг and Контроль with the toggle", () => {
  const { saved } = mount();
  const kind = screen.getByRole("group", { name: "Тип шага 1" });
  expect(within(kind).getByRole("button", { name: "Шаг" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(within(kind).getByRole("button", { name: "Контроль" }));
  expect(screen.getByLabelText("Шаг 1").closest("li")).toHaveClass("ctl");
  expect(saved().steps.map((step) => step.kind)).toEqual(["Контроль", "Операция", "Контроль"]);
});

it("reorders and deletes steps from the row menu, adds a control step at the bottom", () => {
  const { saved } = mount();
  menu("Действия: шаг 3", "Выше");
  expect(screen.getByLabelText("Шаг 2")).toHaveValue("Измерить высоту опрессовки.");
  menu("Действия: шаг 1", "Удалить");
  fireEvent.click(screen.getByRole("button", { name: "＋ Контроль" }));
  fireEvent.change(screen.getByLabelText("Шаг 3"), { target: { value: "Проверить вылет жил." } });
  expect(saved().steps).toEqual([
    { kind: "Контроль", text: "Измерить высоту опрессовки.", tolerance: "1,14 – 1,24 мм" },
    { kind: "Операция", text: "Произвести тестовый рез.", tolerance: "-" },
    { kind: "Контроль", text: "Проверить вылет жил.", tolerance: "" },
  ]);
});

it("moves tooling rows inside their section and to another section from the row menu", () => {
  const { saved } = mount();
  menu("Действия: Инструмент 2", "Выше");
  expect(screen.getByLabelText("Инструмент 1: наименование")).toHaveValue("EW-2550 | Микрометр");
  menu("Действия: Оборудование 1", "Перенести в инструмент");
  expect(screen.getByLabelText("Инструмент 3: наименование")).toHaveValue("BZW-5.0+Z | Станок резки/ обжим+лужение");
  fireEvent.click(within(screen.getByRole("rowgroup", { name: "Расходники" })).getByRole("button", { name: "＋ Позиция" }));
  fireEvent.change(screen.getByLabelText("Расходники 2: наименование"), { target: { value: "Флюс ФВ 529-3.1" } });
  expect(saved().tooling.map((tool) => [tool.section, tool.name])).toEqual([
    ["Инструмент", "EW-2550 | Микрометр"],
    ["Инструмент", "Линейка металлическая ГОСТ 427-75"],
    ["Расходники", "Припой Sn63Pb37"],
    ["Инструмент", "BZW-5.0+Z | Станок резки/ обжим+лужение"],
    ["Расходники", "Флюс ФВ 529-3.1"],
  ]);
});

it("edits typical defects as a list and saves the same text format with only the edited line changed", () => {
  const { saved } = mount();
  expect(screen.getByRole("group", { name: "Дефекты жилы" })).toBeInTheDocument();
  expect(screen.getByLabelText("Группа 2")).toHaveValue("Дефекты изоляции");
  expect(screen.getByLabelText("Дефект 1.2")).toHaveValue("Распушение жил");
  expect(screen.getByLabelText("Признак 1.1")).toHaveValue("Надрезы на проволочках.");
  // Рисунок «Норма | Брак» к каждому дефекту — из общей библиотеки ЭТК.
  expect(document.querySelectorAll(".tc-def-pic > svg")).toHaveLength(3);
  fireEvent.change(screen.getByLabelText("Признак 2.1"), { target: { value: "Профиль сплющен роликами." } });
  menu("Действия: дефект 1.2", "Выше");
  expect(saved().defects).toBe("ТИПОВЫЕ ДЕФЕКТЫ (NG) для резки:\n1. Дефекты жилы:\nРаспушение жил: Потеря скрутки.\nПодрезка жил: Надрезы на проволочках.\n" +
    "2. Дефекты изоляции:\nСмятие изоляции: Профиль сплющен роликами.");
});

it("adds a defect group to a card without defects", () => {
  const { saved } = mount({ ...card(), defects: "" });
  fireEvent.click(screen.getByRole("button", { name: "＋ Группа" }));
  fireEvent.change(screen.getByLabelText("Группа 1"), { target: { value: "Дефекты обжима" } });
  fireEvent.change(screen.getByLabelText("Дефект 1.1"), { target: { value: "Эффект «банана»" } });
  fireEvent.change(screen.getByLabelText("Признак 1.1"), { target: { value: "Изгиб терминала." } });
  expect(saved().defects).toBe("1. Дефекты обжима:\nЭффект «банана»: Изгиб терминала.");
});

it("keeps a defects text that does not parse into defects as plain text", () => {
  mount({ ...card(), defects: "Типовые дефекты (NG):" });
  expect(screen.getByRole("textbox", { name: "Типовые дефекты" })).toHaveValue("Типовые дефекты (NG):");
});

it("shows the generator's sections read-only in the ETK order", () => {
  render(<TcCardEditForm code="CUT_WIRE+1crimp" nameEditable={false} initial={card()} onCancel={vi.fn()} onSave={vi.fn()}
    context={{ input: <span>Провод ПуГВ 0,5</span>, sketch: <span>рисунок</span>, result: <span>Провод с терминалом</span> }} />);
  const titles = screen.getAllByRole("heading", { level: 4 }).map((heading) => heading.textContent);
  expect(titles).toEqual([
    "1Входиз генератора", "2Инструмент, оборудование, расходники", "3Ход операции", "4Типовые дефекты · 3",
    "5Эскизиз генератора", "6Результатиз генератора",
  ]);
});

it("asks before dropping edits on «Отмена»", () => {
  const onCancel = vi.fn();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<TcCardEditForm code="X" initial={card()} onCancel={onCancel} onSave={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Наименование операции"), { target: { value: "Резка" } });
  fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(confirm).toHaveBeenCalled();
  expect(onCancel).not.toHaveBeenCalled();
  confirm.mockRestore();
});
