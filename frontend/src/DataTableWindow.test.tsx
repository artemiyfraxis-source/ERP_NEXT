import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";
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
  report(near: (node: HTMLElement) => boolean, top = 0) {
    const entries = [...this.nodes].map((node) => ({
      target: node, isIntersecting: near(node as HTMLElement),
      boundingClientRect: { top } as DOMRectReadOnly, rootBounds: { top: -1000, bottom: 1900 } as DOMRectReadOnly,
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
