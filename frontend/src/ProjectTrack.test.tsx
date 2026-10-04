import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NO_CONNECTION } from "./api";
import { ProjectTrack, trackWhen } from "./ProjectTrack";

const entries = [
  { at: "2026-10-01T00:00:00", module: "Проекты", kind: "Карточка проекта из расчёта", ref: "708К.4", status: "Создана", item_index: "" },
  { at: "2026-10-04T14:30:00", module: "Техкарты", kind: "Техкарта выдана", ref: "708К.4КБ1", status: "Выдана", item_index: "КБ1" },
  { at: "2026-10-05T09:05:00", module: "Заявки", kind: "Заявка на закупку", ref: "ЗК-0001", status: "Новая", item_index: "КБ2" },
  { at: null, module: "Отгрузка", kind: "Отправка уехала", ref: "ОТ-0001", status: "Запланирована", item_index: "" },
];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function bodyRows() {
  const table = screen.getByRole("table");
  return within(table).getAllByRole("row").slice(1);
}

describe("ProjectTrack", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("loads the track of the project and shows it in time order with Russian dates", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => json({ project: "708К.4", entries }));
    vi.stubGlobal("fetch", fetchMock);
    render(<ProjectTrack project="708К.4" />);
    expect(screen.getByText("Загрузка…")).toBeTruthy();
    expect(await screen.findByText("Техкарта выдана")).toBeTruthy();
    expect(decodeURIComponent(String(fetchMock.mock.calls[0][0]))).toBe("/api/techcards/kitsets/708К.4/track");

    const headers = within(screen.getByRole("table")).getAllByRole("columnheader").map((cell) => cell.textContent?.replace(/[▲▼\s]+$/, ""));
    expect(headers).toEqual(["Когда", "Раздел", "Событие", "Номер", "Статус", "КБ"]);
    const rows = bodyRows().map((row) => within(row).getAllByRole("cell").map((cell) => cell.textContent));
    expect(rows).toEqual([
      ["01.10.2026", "Проекты", "Карточка проекта из расчёта", "708К.4", "Создана", ""],
      ["04.10.2026 14:30", "Техкарты", "Техкарта выдана", "708К.4КБ1", "Выдана", "КБ1"],
      ["05.10.2026 09:05", "Заявки", "Заявка на закупку", "ЗК-0001", "Новая", "КБ2"],
      ["—", "Отгрузка", "Отправка уехала", "ОТ-0001", "Запланирована", ""],
    ]);
    expect(document.body.textContent).not.toMatch(/\d{4}-\d{2}-\d{2}/);
    expect(screen.queryByText("По проекту в разделах ещё ничего нет")).toBeNull();
  });

  it("filters by the section and searches across columns", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ project: "708К.4", entries })));
    render(<ProjectTrack project="708К.4" />);
    await screen.findByText("Техкарта выдана");
    fireEvent.click(screen.getByRole("button", { name: "Фильтры" }));
    fireEvent.change(screen.getByLabelText("Фильтр: Раздел"), { target: { value: "Заявки" } });
    expect(bodyRows()).toHaveLength(1);
    expect(screen.getByText("ЗК-0001")).toBeTruthy();
    expect(screen.queryByText("Техкарта выдана")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Снять фильтр: Раздел" }));
    fireEvent.change(screen.getByLabelText("Поиск: Трек проекта"), { target: { value: "отгрузка уехала" } });
    expect(bodyRows()).toHaveLength(1);
    expect(screen.getByText("ОТ-0001")).toBeTruthy();
  });

  it("says plainly that nothing has been created for the project yet", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ project: "708К.4", entries: [] })));
    render(<ProjectTrack project="708К.4" />);
    expect(await screen.findByText("По проекту в разделах ещё ничего нет")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows the server error and the lost connection in a banner, not an endless loading", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "Нет доступа к разделу" }, 403)));
    const { unmount } = render(<ProjectTrack project="708К.4" />);
    expect((await screen.findByRole("alert")).textContent).toContain("Нет доступа к разделу");
    expect(screen.queryByText("Загрузка…")).toBeNull();
    unmount();

    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    render(<ProjectTrack project="708К.4" />);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(NO_CONNECTION));
  });

  it("formats the moment: day only for midnight, minutes otherwise, a dash for no date", () => {
    expect(trackWhen(null)).toBe("—");
    expect(trackWhen("2026-10-04T00:00:00")).toBe("04.10.2026");
    expect(trackWhen("2026-10-04T14:30:00")).toBe("04.10.2026 14:30");
    expect(trackWhen("2026-10-04T00:00:00.000000")).toBe("04.10.2026");
  });
});
