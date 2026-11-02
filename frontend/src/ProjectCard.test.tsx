import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { KitsetTile } from "./api";
import { KitTile } from "./ProjectCard";

const statuses = ["Новый", "ТК готова", "КБ набрана", "В работе", "ОТК", "Отгружено", "Отменён"];
const kb = (index: number, changes: Partial<KitsetTile["kb_stages"][number]> = {}) => ({
  index: `КБ${index}`, status: "Новый", manual: false, tc: false, kit: false, review: null, ...changes,
});
const tile = (changes: Partial<KitsetTile> = {}): KitsetTile => ({
  project: "708К.4", customer: "ООО Заказчик с длинным полным наименованием", calc_source: "Расчёт.xlsx",
  updated_at: "2026-09-29T10:00:00", updated_by: "Технолог", assemblies: 2, lines: 3,
  status: "Новый", blocker: "", kbs: ["КБ1", "КБ2"], kb_stages: [kb(1), kb(2)], ...changes,
});
const review = (value: string) => ({ value, text: "", who: "" });

describe("KitTile compact project card", () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it("summarizes forty KBs in two fixed problem lines while preserving full accessible details", () => {
    const item = tile({
      kbs: Array.from({ length: 40 }, (_, index) => `КБ${index + 1}`),
      kb_stages: Array.from({ length: 40 }, (_, index) => kb(index + 1, { tc: true, kit: true, status: index ? "ТК готова" : "В работе" })),
    });
    const { container } = render(<KitTile item={item} statuses={statuses} canEdit={false} open={vi.fn()} />);
    const customer = screen.getByText(item.customer);
    expect(customer).toHaveAttribute("title", item.customer);
    expect(customer.compareDocumentPosition(screen.getByText("Новый")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.querySelectorAll(".kit-tile-problem-line")).toHaveLength(2);
    expect(container.querySelector(".kit-tile-problems")).toHaveTextContent("ТК: не проверена 40");
    const card = screen.getByRole("button", { name: "Открыть проект 708К.4" });
    expect(card).toHaveAccessibleDescription(/КБ40 — ТК готова/);
    expect(card).toHaveAccessibleDescription(/не проверена — КБ1, КБ2/);
    expect(card).toHaveAccessibleDescription(/КБ39, КБ40/);
    expect(card).toHaveAccessibleDescription(/сборок 2 · позиций 3/);
    expect(card).toHaveAccessibleDescription(/обновлено 29\.09\.2026 10:00/);
    expect(container.querySelector(".kit-review")).toBeNull();
  });

  it("keeps every nonzero issue count in two grouped lines and all review lists in details", () => {
    const item = tile({ kb_stages: [
      kb(1), kb(2, { tc: true }), kb(3, { tc: true, kit: true, review: review("с погрешностями") }),
      kb(4, { tc: true, kit: true, review: review("проверена") }),
    ] });
    const { container } = render(<KitTile item={item} statuses={statuses} canEdit={false} open={vi.fn()} />);
    const lines = container.querySelectorAll(".kit-tile-problem-line");
    expect(lines).toHaveLength(2);
    expect(lines[0]).toHaveTextContent("ТК: не выдана 1 · не проверена 1");
    expect(lines[1]).toHaveTextContent("С погрешностями 1 · КБ не набрана 2");
    const card = screen.getByRole("button", { name: "Открыть проект 708К.4" });
    expect(card).toHaveAccessibleDescription(/ТК не выдана: 1 КБ — КБ1/);
    expect(card).toHaveAccessibleDescription(/не проверена — КБ2/);
    expect(card).toHaveAccessibleDescription(/с погрешностями — КБ3/);
    expect(card).toHaveAccessibleDescription(/проверена — КБ4/);
  });

  it("does not invent readiness, customer, dates or issue counts when no KB facts exist", () => {
    const { container } = render(<KitTile item={tile({ customer: "", updated_at: "", kb_stages: [], kbs: [] })}
      statuses={statuses} canEdit={false} open={vi.fn()} />);
    expect(container.querySelector(".kit-tile-customer")).toHaveTextContent("—");
    expect(container.querySelector(".kit-tile-problems")).toBeEmptyDOMElement();
    expect(container.querySelectorAll(".proj-mark")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Открыть проект 708К.4" })).toHaveAccessibleDescription(/КБ: —[\s\S]*обновлено —/);
    expect(container.textContent).not.toMatch(/undefined|NaN|Invalid Date/);
  });

  it("keeps manual readiness marks and respects positive overrides without fabricating missing KBs", () => {
    const item = tile({ tc_ready: true, kit_picked: true, tc_manual: "да", kit_manual: "да" });
    const { container, rerender } = render(<KitTile item={item} statuses={statuses} canEdit={false} open={vi.fn()} />);
    expect(screen.getByText("ТК готова")).toHaveAttribute("title", "ТК готова — вручную");
    expect(screen.getByText("КБ набрана")).toHaveAttribute("title", "КБ набрана — вручную");
    expect(container.querySelectorAll(".proj-mark.manual")).toHaveLength(2);
    expect(container.querySelector(".kit-tile-problems")).toBeEmptyDOMElement();
    rerender(<KitTile item={{ ...item, tc_ready: false, tc_manual: "нет", kb_stages: [kb(1, { tc: true, kit: true, review: review("проверена") })] }}
      statuses={statuses} canEdit={false} open={vi.fn()} />);
    expect(screen.getByText("ТК не готова")).toHaveAttribute("title", "ТК не готова — вручную");
    expect(container.querySelector(".kit-tile-problems")).toBeEmptyDOMElement();
  });

  it("announces customer, status, blocker and manual marks in read-only cards", () => {
    render(<KitTile item={tile({ status: "В работе", blocker: "СТОП", tc_ready: false, tc_manual: "нет", kit_picked: true, kit_manual: "да" })}
      statuses={statuses} canEdit={false} open={vi.fn()} />);
    const card = screen.getByRole("button", { name: "Открыть проект 708К.4" });
    expect(card).toHaveAccessibleDescription(/Заказчик с длинным полным наименованием/);
    expect(card).toHaveAccessibleDescription(/Статус проекта: В работе/);
    expect(card).toHaveAccessibleDescription(/Блокер: СТОП/);
    expect(card).toHaveAccessibleDescription(/ТК: нет — вручную/);
    expect(card).toHaveAccessibleDescription(/КБ: да — вручную/);
  });

  it("opens once on click, Enter or Space and ignores repeated or descendant keys", () => {
    const open = vi.fn();
    render(<KitTile item={tile()} statuses={statuses} canEdit open={open} statusChanged={vi.fn()} />);
    const card = screen.getByRole("button", { name: "Открыть проект 708К.4" });
    fireEvent.click(card);
    fireEvent.keyDown(card, { key: "Enter" });
    fireEvent.keyDown(card, { key: " " });
    expect(open).toHaveBeenCalledTimes(3);
    fireEvent.keyDown(card, { key: "Enter", repeat: true });
    fireEvent.keyDown(screen.getByRole("button", { name: "Статус проекта 708К.4: Новый" }), { key: "Enter" });
    fireEvent.keyDown(screen.getByRole("button", { name: "Статус проекта 708К.4: Новый" }), { key: " " });
    expect(open).toHaveBeenCalledTimes(3);
  });

  it("retains real blocker clearing, supplied menus and manual mark actions without opening the project", async () => {
    const open = vi.fn(), changed = vi.fn();
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ events: 1, card: null }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<KitTile item={tile({ blocker: "СТОП", tc_ready: false, tc_manual: "нет" })} statuses={statuses} canEdit open={open}
      statusChanged={changed} lists={{ stages: ["Новый", "В работе"], terminals: ["Отменён"], blockers: ["СТОП"] }} />);
    const status = screen.getByRole("button", { name: "Статус проекта 708К.4: Новый, блокер СТОП" });
    expect(screen.getByText("СТОП")).toHaveAttribute("title", "Активный блокер из журнала проекта");
    fireEvent.click(status);
    let menu = screen.getByRole("menu", { name: "Статус проекта 708К.4" });
    expect(within(menu).getByRole("menuitemradio", { name: "В работе" })).toBeInTheDocument();
    expect(within(menu).queryByRole("menuitemradio", { name: "ОТК" })).toBeNull();
    expect(within(menu).getByRole("menuitemradio", { name: "ТК: нет" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Снять СТОП" }));
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ action: "unblock" });
    fireEvent.click(status);
    menu = screen.getByRole("menu", { name: "Статус проекта 708К.4" });
    fireEvent.click(within(menu).getByRole("menuitemradio", { name: "КБ: да" }));
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(2));
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({ action: "marker", field: "kit_picked", value: "да" });
    expect(open).not.toHaveBeenCalled();
  });
});
