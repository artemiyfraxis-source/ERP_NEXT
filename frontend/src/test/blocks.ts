import { act, render as plainRender, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach } from "vitest";

/* A list shown in blocks (DataTable `blocks`, «База данных» 25.10.2026): a heading and a table per group. The blocks share
   one set of columns, so the first table's header is the list's header. */

/** The tables of the list's blocks, top first. */
export const blockTables = () => [...document.querySelectorAll<HTMLTableElement>(".data-table.dt-blocks > .table-wrap > table")];

/** Queries inside the first block's header row. */
export const listHead = () => within(blockTables()[0].querySelector("thead")!);

/** The list's column headers (of the first block). */
export const listHeads = () => listHead().getAllByRole("columnheader");

/** A block heading as «ПуГВ · 5» (its name and count). */
export function blockLine(heading: Element): string {
  const copy = heading.cloneNode(true) as HTMLElement;
  const count = copy.querySelector(".page-count")?.textContent ?? "";
  copy.querySelectorAll(".page-count, .group-picked, [aria-hidden]").forEach((node) => node.remove());
  return `${(copy.textContent ?? "").trim()} · ${count}`;
}

/** The block headings of the list, top first. */
export const blockHeadings = () => [...document.querySelectorAll<HTMLElement>(".data-table.dt-blocks > .dt-block-title")];

/** The block headings as «name · count». */
export const blockLines = () => blockHeadings().map(blockLine);

/** The body rows of every block in order (no opened-row cards). */
export const blockRows = () => blockTables().flatMap((table) => [...table.querySelectorAll<HTMLTableRowElement>(":scope > tbody > tr:not(.detail)")]);

/* The cable lists open their groups FOLDED (DataTable `foldedByDefault`, owner 26.10.2026). `render` here is the plain
   render plus what a user does first: it presses «Развернуть все» whenever that button shows up (the list loads async), or the
   heading of a list that has a single group. Once the list has been seen open it is left alone, so a test that folds a group afterwards stays folded.
   A test of the folded start itself uses `plainRender`. */
export { plainRender };
const watchers = new Set<MutationObserver>();
afterEach(() => { watchers.forEach((watcher) => watcher.disconnect()); watchers.clear(); });

/* A press counts once the list is seen open: the list may reset its groups when its rows arrive, then it is pressed again. */
function openGroups(seen: WeakMap<Element, { open: boolean; tries: number }>) {
  const buttons = [...document.querySelectorAll<HTMLButtonElement>("button")];
  const toggle = buttons.find((button) => button.textContent === "Развернуть все" || button.textContent === "Свернуть все");
  const heads = [...document.querySelectorAll<HTMLButtonElement>(".data-table.dt-blocks > .dt-block-title button.dt-block-fold")];
  const targets = toggle ? [toggle] : heads.length === 1 ? heads : [];
  for (const target of targets) {
    const state = seen.get(target) ?? { open: false, tries: 0 };
    seen.set(target, state);
    const isOpen = target.textContent === "Свернуть все" || target.getAttribute("aria-expanded") === "true";
    if (isOpen) { state.open = true; continue; }
    if (state.open || state.tries >= 5) continue;
    state.tries += 1;
    act(() => { target.click(); });
  }
}

export function render(ui: ReactNode, options?: Parameters<typeof plainRender>[1]) {
  const result = plainRender(ui, options);
  const seen = new WeakMap<Element, { open: boolean; tries: number }>();
  const watcher = new MutationObserver(() => openGroups(seen));
  watcher.observe(document.body, { childList: true, subtree: true });
  watchers.add(watcher);
  const timer = window.setInterval(() => openGroups(seen), 20);
  watchers.add({ disconnect: () => window.clearInterval(timer) } as MutationObserver);
  openGroups(seen);
  return result;
}
