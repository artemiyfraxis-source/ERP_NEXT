// Карта резки: таблица, построенная из текущего документа жгута (live-cut-list).
// Серверная панель TECHMAP-GRAPHER (HarnessCutListPanel + harness-cut-list-api) не перенесена.

export type HarnessCutListStatus = "limited" | "ready" | "incomplete";
export type HarnessCutListItemStatus = "ready" | "incomplete";

export interface HarnessCutListItem {
  readonly sourceKind?: "wire" | "cable" | "covering";
  readonly wireId: string;
  readonly circuit: string;
  readonly material: string;
  readonly materialSourceKey: string | null;
  readonly materialDisplayName: string | null;
  readonly sourceLengthMm: number | null;
  readonly endCorrectionFromMm: number;
  readonly endCorrectionToMm: number;
  readonly roundingStepMm: number;
  readonly cutLengthMm: number | null;
  readonly pieces: number;
  readonly totalMetres: number | null;
  readonly status: HarnessCutListItemStatus;
  readonly warnings: readonly ("material-missing" | "length-missing")[];
}

export interface HarnessCutList {
  readonly projectId: string;
  readonly harnessId: string;
  readonly harnessQuantity: number;
  readonly status: HarnessCutListStatus;
  readonly warning: string;
  readonly items: readonly HarnessCutListItem[];
}

function formatMillimetres(value: number | null): string {
  return value === null ? "Не задана" : `${value.toLocaleString("ru-RU", { maximumFractionDigits: 3 })} мм`;
}

function formatCorrection(item: HarnessCutListItem): string {
  const format = (value: number) => `${value > 0 ? "+" : ""}${value.toLocaleString("ru-RU", { maximumFractionDigits: 3 })}`;
  return `${format(item.endCorrectionFromMm)} / ${format(item.endCorrectionToMm)} мм`;
}

export function HarnessCutListTable({ cutList, onReveal, highlightedIds = [] }: { readonly cutList: HarnessCutList; readonly onReveal?: (id: string) => void; readonly highlightedIds?: readonly string[] }) {
  return <>
    {cutList.warning && <p className="cut-list-warning" role="note">{cutList.warning}</p>}
    {cutList.items.length === 0 ? <p className="cut-list-empty">В жгуте пока нет проводов.</p> : (
      <div className="cut-list-scroll">
        <table className="cut-list-table">
          <thead><tr>
            <th>Цепь / провод</th>
            <th>Исходная длина</th>
            <th>Поправки</th>
            <th>Длина резки</th>
            <th>Шт.</th>
            <th>Общий метраж</th>
            <th>Статус</th>
          </tr></thead>
          <tbody>{cutList.items.map(item => <tr key={item.wireId} aria-selected={highlightedIds.includes(item.wireId)} className={highlightedIds.includes(item.wireId) ? "is-related" : ""}>
            <td>{onReveal ? <button type="button" className="ui-control" onClick={() => onReveal(item.wireId)} aria-label={`Показать на чертеже ${item.circuit || item.wireId}`}>{item.circuit || "Без цепи"} ↗</button> : <strong>{item.circuit || "Без цепи"}</strong>}<span>{item.wireId}</span><small>{item.materialDisplayName ?? (item.material === "not-pinned" ? "Материал не закреплён" : item.material)}{item.materialSourceKey && item.materialSourceKey !== item.materialDisplayName ? ` · ${item.materialSourceKey}` : ""}</small></td>
            <td>{formatMillimetres(item.sourceLengthMm)}</td>
            <td>{formatCorrection(item)}<small>шаг {formatMillimetres(item.roundingStepMm)}</small></td>
            <td>{formatMillimetres(item.cutLengthMm)}</td>
            <td>{item.pieces.toLocaleString("ru-RU")}</td>
            <td>{item.totalMetres === null ? "Не рассчитан" : `${item.totalMetres.toLocaleString("ru-RU", { maximumFractionDigits: 6 })} м`}</td>
            <td><span className={`cut-list-status ${item.status}`}>{item.warnings.includes("length-missing") ? "Нет длины" : item.warnings.includes("material-missing") ? "Нет материала" : "Готово"}</span></td>
          </tr>)}</tbody>
        </table>
      </div>
    )}
  </>;
}
