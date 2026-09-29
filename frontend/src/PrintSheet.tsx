import type { ReactNode } from "react";

/** One column of a printable sheet: how to render its header and each row's cell. */
export interface PrintSheetColumn<T> {
  key: string;
  label: string;
  align?: "right";
  render: (row: T, index: number) => ReactNode;
}

/**
 * A printable, fillable-by-hand sheet — the browser-print equivalent of SUPPLYMAP's `buildPrintDoc_`
 * (Warehouse.gs `buildTransferSheet`, Issue.gs `buildIssueDoc_`/`buildIssueReturnDoc`): a numbered table
 * plus one or more signature lines. Renders inline like the app's other side panels; `@media print`
 * hides everything else so only the sheet itself goes to paper.
 *
 * Shared by warehouse (WH-6 «Лист перемещения на склад») and fulfillment (FF-8 issue/return forms) —
 * keep this component's surface small so both stay easy to extend without colliding.
 */
export function PrintSheet<T>({
  title,
  subtitle,
  columns,
  rows,
  signLabels,
  onClose,
}: {
  title: string;
  subtitle: string;
  columns: PrintSheetColumn<T>[];
  rows: T[];
  signLabels: string[];
  onClose: () => void;
}) {
  return (
    <section className="panel print-sheet" aria-label={title}>
      <style>{`
        @media print {
          body * { visibility: hidden; }
          .print-sheet, .print-sheet * { visibility: visible; }
          .print-sheet { position: absolute; top: 0; left: 0; width: 100%; }
          .no-print { display: none !important; }
        }
      `}</style>
      <div className="manual-stock-head no-print">
        <h2>{title}</h2>
        <div>
          <button type="button" className="primary small" onClick={() => window.print()}>Печать</button>
          <button type="button" className="link-button" onClick={onClose}>Закрыть</button>
        </div>
      </div>
      <h1 className="print-sheet-title">{title}</h1>
      <p className="page-sub">{subtitle}</p>
      <table className="grid compact print-sheet-table">
        <thead>
          <tr>{columns.map((column) => (
            <th key={column.key} className={column.align === "right" ? "num" : undefined}>{column.label}</th>
          ))}</tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>{columns.map((column) => (
              <td key={column.key} className={column.align === "right" ? "num" : undefined}>{column.render(row, index)}</td>
            ))}</tr>
          ))}
        </tbody>
      </table>
      <p className="print-sheet-sign">
        {signLabels.map((label) => `${label}: ______________________`).join("        ")}
        {"        Дата: ______________"}
      </p>
    </section>
  );
}
