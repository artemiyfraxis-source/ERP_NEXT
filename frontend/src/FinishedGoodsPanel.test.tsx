import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FinishedGoodsPanel } from "./FinishedGoodsPanel";
import { WarehousePage } from "./WarehousePage";
import { goodsPieces } from "./FinishedGoodsStates";

const good = {
  id: "g1", version: 3, project: "591К.3", item_index: "КБ1", customer: "Заказчик", article: "A-1", name: "Жгут питания",
  ordered: "40", unit: "шт", accepted: "30", shipped: "12", remaining: "18", sent: "5", before_erp: "0",
  received_at: "2026-10-10", grn: "ГП.26.0000001", note: "",
};
const other = { ...good, id: "g2", project: "600К.1", item_index: "КБ7", customer: "Другой", name: "Кабель", ordered: "10", accepted: "0", shipped: "0", remaining: "0", sent: "0", grn: null, received_at: null };
const incoming = [{ project: "600К.1", item_index: "КБ7", name: "Кабель", unit: "шт", needed: "10", delivered: "4", accepted: "0", remaining: "4", in_production: "10", good_id: "g2", good_version: 1, tasks: [] }];

function json(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status }); }

describe("Склад → Готовая продукция", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.history.replaceState({}, "", "/?section=stock");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/shipping/goods") return json({ goods: [good, other] });
      if (url === "/api/shipping/finished-goods/from-production") return json({ rows: incoming });
      if (url.startsWith("/api/stock")) return json({ lots: [], total: 0, truncated: 0, next_cursor: null, groups: [], group: "series" });
      return json({});
    }));
  });
  afterEach(() => vi.restoreAllMocks());

  it("derives the pieces of each state from the finished-goods counters", () => {
    expect(goodsPieces(good)).toEqual({ production: 10, accepted: 18, packed: 7, shipped: 5 });
    expect(goodsPieces({ ordered: "10", accepted: "12", shipped: "0", sent: "0" })).toEqual({ production: 0, accepted: 12, packed: 0, shipped: 0 });
  });

  it("shows pieces per state by project and КБ, read-only, with what waits for acceptance", async () => {
    render(<FinishedGoodsPanel />);
    expect(await screen.findByText("591К.3 · Заказчик — в пр-ве 10 · принято 18 · упак. 7 · отгр. 5")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "в производстве 10 шт, принято складом 18 шт, упаковано 7 шт, отгружено 5 шт" })).toBeInTheDocument();
    expect(screen.getByText("ГП.26.0000001")).toBeInTheDocument();
    expect(screen.getByText("4", { selector: ".tone-warn" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Принять|Упаковать|Отгрузить/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Ждёт приёмки · 1" }));
    expect(screen.queryByText("ГП.26.0000001")).not.toBeInTheDocument();
    expect(screen.getByText("КБ7", { selector: "strong" })).toBeInTheDocument();
  });

  it("links to the «Отгрузка» tabs", async () => {
    const moved = vi.fn();
    window.addEventListener("popstate", moved);
    render(<FinishedGoodsPanel />);
    fireEvent.click(await screen.findByRole("link", { name: "на складе" }));
    expect(window.location.search).toBe("?section=shipping&tab=stock");
    expect(moved).toHaveBeenCalled();
    window.removeEventListener("popstate", moved);
  });

  it("says when it could not load", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "Раздел закрыт" }, 403)));
    render(<FinishedGoodsPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Раздел закрыт");
  });

  it("is a tab of the warehouse only for those who read «Отгрузка»", async () => {
    const { unmount } = render(<WarehousePage />);
    expect(await screen.findByRole("heading", { name: "Склад" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Готовая продукция" })).not.toBeInTheDocument();
    unmount();

    render(<WarehousePage finishedGoods />);
    fireEvent.click(await screen.findByRole("tab", { name: "Готовая продукция" }));
    expect(await screen.findByText("ГП.26.0000001")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Материалы и комплектующие" }));
    expect(await screen.findByRole("button", { name: /Внести позиции/ })).toBeInTheDocument();
  });
});
