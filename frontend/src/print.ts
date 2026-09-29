// Shared "печатный бланк" mechanism: no PDF/Doc generation in this stack (Google exit horizon —
// cheap, temporary mechanisms only). A module's backend builds a `PrintSheet` (title/columns/rows/
// sign), and this file turns it into a plain HTML document opened in a new window and sent straight
// to the browser's print dialog (Ctrl+P / "Save as PDF" is the browser's own job). Several modules
// print through this same tiny contract (fulfillment, warehouse) — keep it minimal and stable.
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

/** Opens the sheet in a new window and triggers the browser print dialog. No-op if popups are blocked. */
export function openPrintSheet(sheet: PrintSheet): void {
  const win = window.open("", "_blank", "noopener,noreferrer,width=900,height=1150");
  if (!win) return;
  win.document.open();
  win.document.write(buildPrintSheetHtml(sheet));
  win.document.close();
  win.focus();
  win.print();
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
  const win = window.open("", "_blank", "noopener,noreferrer,width=900,height=1150");
  if (!win) return;
  win.document.open();
  win.document.write(buildPrintSheetsHtml(sheets));
  win.document.close();
  win.focus();
  win.print();
}
