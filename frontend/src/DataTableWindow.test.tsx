import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DataTable, type DataColumn } from "./DataTable";
import { CHUNK_ROWS, ROW_HEIGHT } from "./blockLayout";

/* «База данных», 26.10.2026 («когда листаешь, фпс падает»): a list in blocks puts only the rows near the screen in the
   page — the rest is empty space of their height — and shows the whole list (no pages of 200, nothing cut). */

type Part = { id: string; name: string; series: string };
const rows: Part[] = [
  ...Array.from({ length: 60 }, (_, index) => ({ id: `a${index}`, name: `МГТФ ${index}`, series: "МГТФ" })),
  ...Array.from({ length: 300 }, (_, index) => ({ id: `b${index}`, name: `НВ-4 ${index}`, series: "НВ-4" })),
];
const columns: DataColumn<Part>[] = [{ key: "name", title: "Марка", value: (row) => row.name }];

/** An IntersectionObserver the test drives: `report` tells the list which of its pieces are near the screen. */
class FakeObserver {
  static last: FakeObserver | null = null;
  nodes = new Set<Element>();
  constructor(private callback: IntersectionObserverCallback) { FakeObserver.last = this; }
  observe(node: Element) { this.nodes.add(node); }
  unobserve(node: Element) { this.nodes.delete(node); }
  disconnect() { this.nodes.clear(); }
  takeRecords() { return []; }
  report(near: (node: HTMLElement) => boolean, top = 0, bounds = { top: -1000, bottom: 1900 }) {
    const entries = [...this.nodes].map((node) => ({
      target: node, isIntersecting: near(node as HTMLElement),
      boundingClientRect: { top } as DOMRectReadOnly, rootBounds: bounds as DOMRectReadOnly,
    })) as unknown as IntersectionObserverEntry[];
    act(() => this.callback(entries, this as unknown as IntersectionObserver));
  }
}

const original = globalThis.IntersectionObserver;
beforeEach(() => { globalThis.IntersectionObserver = FakeObserver as unknown as typeof IntersectionObserver; });
afterEach(() => { globalThis.IntersectionObserver = original; FakeObserver.last = null; });

const bodyRows = () => document.querySelectorAll(".data-table.dt-blocks tbody:not(.dt-gap) > tr").length;

it("puts only the pieces near the screen in the page and keeps the whole list (no pages)", () => {
  render(<DataTable label="Провода" rows={rows} columns={columns} rowKey={(row) => row.id} groupBy={(row) => row.series} blocks />);
  // before the window reports: about the first two screens
  expect(bodyRows()).toBe(80);
  expect(screen.queryByRole("status")).toBeNull(); // no «load more» — the whole list is there
  const headings = [...document.querySelectorAll(".dt-block-title")].map((node) => node.textContent);
  expect(headings).toEqual(["МГТФ 60", "НВ-4 300"]);

  // the screen is at the top: only the first piece is near
  FakeObserver.last!.report((node) => node.dataset.chunk === "МГТФ\u00010");
  expect(bodyRows()).toBe(CHUNK_ROWS);
  const gap = document.querySelector<HTMLElement>(".dt-window-gap[data-window='НВ-4']")!;
  expect(gap).not.toBeNull(); // the far block is one empty space of its height
  expect(parseFloat(gap.style.height)).toBeGreaterThanOrEqual(300 * ROW_HEIGHT);
  expect(document.querySelectorAll(".data-table.dt-blocks tbody.dt-gap")).toHaveLength(2); // the rest of the near block

  // scrolled to the far block: its pieces the screen reaches come in
  FakeObserver.last!.report((node) => node.dataset.window === "НВ-4", 0);
  expect(screen.getByText("НВ-4 0")).toBeInTheDocument();
  expect(screen.queryByText("НВ-4 299")).toBeNull();
  expect(screen.queryByText("МГТФ 0")).toBeNull();
});

/* Prod, 26.10.2026 (PR #705, «Коаксиальные кабели» at 3330×1274 device px, wheel scrolling): blocks further down stayed
   empty boxes on the screen. A block came into the screen's margin by its header alone — no piece reached — and the
   observer, which speaks only when a box starts or stops crossing the margin, never asked again. */
const small: Part[] = [
  ...Array.from({ length: 100 }, (_, index) => ({ id: `a${index}`, name: `GNS ${index}`, series: "GNS-360B" })),
  { id: "h1", name: "HCTAYZ 1/2", series: "HCTAYZ — разные" }, { id: "h2", name: "HCTAYZ 7/8", series: "HCTAYZ — разные" },
  ...Array.from({ length: 3 }, (_, index) => ({ id: `l${index}`, name: `LMR-195 ${index}`, series: "LMR-195" })),
];
const smallBlocks = () => render(<DataTable label="Кабели" rows={small} columns={columns} rowKey={(row) => row.id} groupBy={(row) => row.series} blocks />);
const isGap = (name: string) => (node: HTMLElement) => node.dataset.window === name;

it("shows the rows of a small block that comes into the margin by its header alone", () => {
  smallBlocks();
  FakeObserver.last!.report((node) => node.dataset.chunk === "GNS-360B\u00010"); // the screen is at the top
  expect(document.querySelector(".dt-window-gap[data-window='HCTAYZ — разные']")).not.toBeNull();
  // the block's box is 10 px inside the margin's bottom: its header is in, its first row is not
  FakeObserver.last!.report(isGap("HCTAYZ — разные"), 1890);
  expect(screen.getByText("HCTAYZ 1/2")).toBeInTheDocument();
  expect(screen.getByText("HCTAYZ 7/8")).toBeInTheDocument();
  // its rows now report «not near» on their own (still below the margin): the near block keeps them, no flicker back
  FakeObserver.last!.report((node) => node.dataset.window === "HCTAYZ — разные");
  expect(screen.getByText("HCTAYZ 7/8")).toBeInTheDocument();
  expect(document.querySelector(".dt-window-gap[data-window='HCTAYZ — разные']")).toBeNull();
});

it("shows the rows on fractional geometry (zoom, device pixel ratio 1.67)", () => {
  smallBlocks();
  FakeObserver.last!.report(() => false);
  FakeObserver.last!.report(isGap("LMR-195"), 1899.37, { top: -999.4, bottom: 1899.83 });
  expect(screen.getAllByText(/^LMR-195 \d$/)).toHaveLength(3);
});

it("puts a block no taller than the screen in whole, never a part of its rows", () => {
  const rows24 = Array.from({ length: 24 }, (_, index) => ({ id: `r${index}`, name: `RG-58 ${index}`, series: "RG-58" }));
  render(<DataTable label="Кабели" rows={rows24} columns={columns} rowKey={(row) => row.id} groupBy={(row) => row.series} blocks />);
  expect(24 * ROW_HEIGHT).toBeLessThanOrEqual(window.innerHeight);
  FakeObserver.last!.report((node) => node.dataset.chunk === "RG-58\u00010");
  expect(bodyRows()).toBe(24); // one piece of 24, not 20 + 4
  expect(document.querySelectorAll(".data-table.dt-blocks tbody.dt-gap")).toHaveLength(0);
});

it("fills a block left empty on the screen once a scroll settles, whatever the observer said", () => {
  vi.useFakeTimers();
  try {
    smallBlocks();
    FakeObserver.last!.report(() => false); // the observer missed everything
    const gap = document.querySelector<HTMLElement>(".dt-window-gap[data-window='LMR-195']")!;
    vi.spyOn(gap, "getBoundingClientRect").mockReturnValue({ top: 300.4, bottom: 393.6, height: 93.2 } as DOMRect);
    act(() => { window.dispatchEvent(new Event("scroll")); vi.advanceTimersByTime(200); });
    expect(screen.getAllByText(/^LMR-195 \d$/)).toHaveLength(3);
  } finally {
    vi.useRealTimers();
  }
});