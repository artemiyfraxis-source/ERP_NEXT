import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RequestGrid } from "./RequestGrid";
import { GRID_COLUMNS, emptyDraft } from "./requestGridLogic";
import { installTapHint } from "./tapHint";
import { labelStockCells } from "./WarehouseColumns";

function touch(coarse: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: coarse && query.includes("pointer: coarse"), media: query, addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false,
  }));
}

afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

describe("строки заявки на телефоне", () => {
  it("каждое поле позиции несёт подпись колонки для карточки", () => {
    const noop = () => undefined;
    const { container } = render(<RequestGrid positions={[emptyDraft()]} schema={null} errors={null} sendCount={0} busy={false}
      onField={noop} onPaste={noop} onAdd={noop} onRemove={noop} onDuplicate={noop} onSubmit={noop} />);
    const labels = Array.from(container.querySelectorAll("tbody td[data-label]")).map((cell) => cell.getAttribute("data-label"));
    expect(labels).toEqual(GRID_COLUMNS.map((column) => column.label));
  });
});

describe("склад на телефоне", () => {
  it("ячейки партий получают подписи из шапки, без стрелок сортировки; строки групп не трогаются", () => {
    document.body.innerHTML = `<table class="stock-table"><thead><tr><th>Наименование</th><th><button>Остаток ▲</button></th><th>Место</th></tr></thead>
      <tbody><tr class="stock-group-row"><td colspan="3">Группа</td></tr><tr><td>Провод</td><td>10 м</td><td>А-1</td></tr></tbody></table>`;
    labelStockCells(document);
    const cells = Array.from(document.querySelectorAll("tbody tr:not(.stock-group-row) td")).map((cell) => cell.getAttribute("data-label"));
    expect(cells).toEqual(["Наименование", "Остаток", "Место"]);
    expect(document.querySelector(".stock-group-row td")?.hasAttribute("data-label")).toBe(false);
  });
});

describe("подсказка по нажатию", () => {
  it("нажатие на элемент с title показывает текст, повторное и нажатие в стороне — убирает", () => {
    touch(true);
    const stop = installTapHint();
    render(<div><span title="Имя взято из справочника">Провод</span><p>рядом</p></div>);
    fireEvent.click(screen.getByText("Провод"));
    expect(screen.getByRole("tooltip").textContent).toBe("Имя взято из справочника");
    fireEvent.click(screen.getByText("рядом"));
    expect(screen.queryByRole("tooltip")).toBeNull();
    stop();
  });

  it("кнопка с title выполняет своё действие и подсказку не открывает", () => {
    touch(true);
    const stop = installTapHint();
    const onClick = vi.fn();
    render(<button title="Удалить строку" onClick={onClick}>Убрать</button>);
    const button = screen.getByText("Убрать");
    const proceeded = fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(proceeded).toBe(true);
    expect(screen.queryByRole("tooltip")).toBeNull();
    stop();
  });

  it("на мыши (pointer: fine) обработчик не включается", () => {
    touch(false);
    const stop = installTapHint();
    render(<span title="Подсказка">Текст</span>);
    fireEvent.click(screen.getByText("Текст"));
    expect(screen.queryByRole("tooltip")).toBeNull();
    stop();
  });
});
