import { within } from "@testing-library/react";

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
