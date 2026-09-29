// Shared "печатный бланк" mechanism: no PDF/Doc generation in this stack (Google exit horizon —
// cheap, temporary mechanisms only). A module's backend builds a `PrintSheet` (title/columns/rows/
// sign), and this file turns it into a plain HTML document opened in a new window and sent straight
// to the browser's print dialog (Ctrl+P / "Save as PDF" is the browser's own job). Several modules
// print through this same tiny contract (fulfillment, warehouse, procurement; techcards — multi-page ЭТК) — keep it
// minimal and stable.
import type { PrintSheet } from "./api";

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

const PRINT_CSS = `
  body { font-family: Arial, Helvetica, sans-serif; margin: 24px; color: #111; }
  h1 { font-size: 18px; margin: 0 0 4px; }
  .subtitle { font-size: 12px; color: #444; margin: 0 0 16px; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { border: 1px solid #333; padding: 4px 6px; text-align: left; vertical-align: top; }
  th { background: #eee; }
  .note { margin-top: 12px; font-size: 12px; font-weight: bold; }
  .sign { margin-top: 32px; font-size: 13px; white-space: pre-wrap; }
`;

const PAGE_CSS = `
  .page { page-break-after: always; }
  .page:last-child { page-break-after: auto; }
  .page h2 { font-size: 14px; margin: 14px 0 6px; }
  .page table { margin-bottom: 10px; }
  .text { font-size: 12px; white-space: pre-wrap; margin: 8px 0; }
  .fig { margin: 8px 0 10px; page-break-inside: avoid; }
  .fig svg { max-width: 100%; height: auto; }
`;

export function buildPrintSheetHtml(sheet: PrintSheet): string {
  const head = sheet.columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("");
  const body = sheet.rows
    .map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`)
    .join("");
  const note = sheet.note ? `<p class="note">${escapeHtml(sheet.note)}</p>` : "";
  return `<!doctype html><html><head><meta charset="utf-8">` +
    `<title>${escapeHtml(sheet.title)}</title><style>${PRINT_CSS}</style></head><body>` +
    `<h1>${escapeHtml(sheet.title)}</h1>` +
    `<p class="subtitle">${escapeHtml(sheet.subtitle)}</p>` +
    `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>` +
    note +
    `<p class="sign">${escapeHtml(sheet.sign)}</p>` +
    `</body></html>`;
}

function openPrintHtml(html: string): void {
  const win = window.open("", "_blank", "noopener,noreferrer,width=900,height=1150");
  if (!win) return;
  win.document.open();
  win.document.write(html);
  win.document.close();
  win.focus();
  win.print();
}

/** Opens the sheet in a new window and triggers the browser print dialog. No-op if popups are blocked. */
export function openPrintSheet(sheet: PrintSheet): void {
  openPrintHtml(buildPrintSheetHtml(sheet));
}

/** A multi-page form (ЭТК: cover + one page per operation, SUPPLYMAP etkPrint_) on the same minimal contract:
 * each page is a title/subtitle, a run of text paragraphs and plain tables, and a sign line. A table with no
 * rows is left out, as SUPPLYMAP does. */
export interface PrintTable { columns: string[]; rows: string[][] }
/** `figure` is ready SVG markup built by our own code (etkDraw.ts escapes every value) — never user HTML. */
export type PrintBlock = { heading: string } | { text: string } | { table: PrintTable } | { figure: string };
export interface PrintPage { title: string; subtitle: string; blocks: PrintBlock[]; sign: string }

function tableHtml(table: PrintTable): string {
  if (!table.rows.length) return "";
  const head = table.columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("");
  const body = table.rows
    .map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`)
    .join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function blockHtml(block: PrintBlock): string {
  if ("heading" in block) return `<h2>${escapeHtml(block.heading)}</h2>`;
  if ("text" in block) return block.text ? `<p class="text">${escapeHtml(block.text)}</p>` : "";
  if ("figure" in block) return block.figure ? `<div class="fig">${block.figure}</div>` : "";
  return tableHtml(block.table);
}

export function buildPrintDocumentHtml(title: string, pages: PrintPage[]): string {
  const body = pages.map((page) =>
    `<section class="page"><h1>${escapeHtml(page.title)}</h1>` +
    (page.subtitle ? `<p class="subtitle">${escapeHtml(page.subtitle)}</p>` : "") +
    page.blocks.map(blockHtml).join("") +
    `<p class="sign">${escapeHtml(page.sign)}</p></section>`,
  ).join("");
  return `<!doctype html><html><head><meta charset="utf-8">` +
    `<title>${escapeHtml(title)}</title><style>${PRINT_CSS}${PAGE_CSS}</style></head><body>${body}</body></html>`;
}

export function openPrintDocument(title: string, pages: PrintPage[]): void {
  openPrintHtml(buildPrintDocumentHtml(title, pages));
}

/** Печать готового файла ERP (документ запроса поставщику): грузится в скрытый iframe того же origin и
 * отправляется в диалог печати браузера; iframe убирается после печати. */
export function printStoredDocument(url: string): void {
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.style.position = "fixed";
  frame.style.width = "0";
  frame.style.height = "0";
  frame.style.border = "0";
  frame.onload = () => {
    const win = frame.contentWindow;
    if (!win) return;
    win.addEventListener("afterprint", () => frame.remove());
    win.focus();
    win.print();
  };
  frame.src = url;
  document.body.appendChild(frame);
}

/** Several sheets in one print job, each on its own page (packing list per box — `shipBuildDocs_`). */
export function buildPrintSheetsHtml(sheets: PrintSheet[]): string {
  const bodies = sheets.map((sheet) => {
    const html = buildPrintSheetHtml(sheet);
    return html.slice(html.indexOf("<body>") + 6, html.lastIndexOf("</body>"));
  });
  const title = sheets[0]?.title ?? "";
  return `<!doctype html><html><head><meta charset="utf-8">` +
    `<title>${escapeHtml(title)}</title><style>${PRINT_CSS} .sheet + .sheet { page-break-before: always; }</style></head><body>` +
    bodies.map((body) => `<div class="sheet">${body}</div>`).join("") +
    `</body></html>`;
}

export function openPrintSheets(sheets: PrintSheet[]): void {
  if (sheets.length === 1) { openPrintSheet(sheets[0]); return; }
  openPrintHtml(buildPrintSheetsHtml(sheets));
}
