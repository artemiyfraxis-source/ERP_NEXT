import { useEffect, useState } from "react";
import { api, failureText } from "./api";
import { countLabel, formatNumber, kbLabel } from "./format";
import "./semiLeftovers.css";

/** Остаток полуфабриката одной операции задания (backend production/leftovers.py): в коробках сверх того, что
 *  ещё возьмут следующие этапы этого задания. Только видимость — коробки и количества не меняются. */
export interface SemiLeftover {
  task_id: string;
  task_number: string;
  task_status: string;
  project: string;
  item: string;
  operation_id: string;
  position: number;
  code: string;
  stage: string;
  produced: string;
  consumed: string;
  in_boxes: string;
  needed: string;
  surplus: string;
  boxes: Array<{ id: string; number: string | null; status: string; available: string }>;
}

/** Что планируется сделать: операция задания (у будущего задания — проект и КБ техкарты). */
export interface PlannedOperation {
  task_id: string;
  project: string;
  item: string;
  position: number;
  code: string;
  stage: string;
}

const same = (left: string, right: string) => left.trim().toLocaleLowerCase("ru") === right.trim().toLocaleLowerCase("ru");

/** Остатки других заданий того же полуфабриката: та же КБ проекта, тот же этап (номер, операция, название). */
export function leftoverMatches(rows: readonly SemiLeftover[], planned: PlannedOperation): SemiLeftover[] {
  if (!planned.item) return [];
  return rows.filter((row) => row.task_id !== planned.task_id && same(row.project, planned.project) && same(row.item, planned.item)
    && row.position === planned.position && row.code === planned.code && same(row.stage, planned.stage));
}

export function useSemiLeftovers() {
  const [rows, setRows] = useState<SemiLeftover[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api<{ leftovers: SemiLeftover[] }>("/api/production/leftovers")
      .then((value) => { if (active) setRows(value.leftovers); })
      .catch((reason) => { if (active) setError(failureText(reason, "Остатки полуфабрикатов не загружены")); });
    return () => { active = false; };
  }, []);
  return { rows, error };
}

const pieces = (value: number | string) => `${formatNumber(value)} шт`;
const total = (rows: readonly SemiLeftover[]) => rows.reduce((sum, row) => sum + Number(row.surplus), 0);
const boxNumbers = (row: SemiLeftover) => row.boxes.map((box) => box.number || "без номера").join(", ");

/** Подсказка в строке автоплана: «из остатков 6 шт · ПЗ-0001». */
export function LeftoverHint({ matches }: { matches: readonly SemiLeftover[] }) {
  if (!matches.length) return null;
  const title = matches.map((row) => `${row.task_number} (${row.task_status.toLocaleLowerCase("ru")}): ${pieces(row.surplus)}, коробки ${boxNumbers(row)}`).join("\n");
  return <em className="semi-leftover-hint" title={title}>из остатков {pieces(total(matches))} · {matches.map((row) => row.task_number).join(", ")}</em>;
}

/** Список остатков полуфабрикатов — свёрнутый раздел в окне автоплана. */
export function SemiLeftoversList({ rows, error }: { rows: readonly SemiLeftover[] | null; error: string }) {
  if (error) return <div className="error-banner" role="alert">{error}</div>;
  if (!rows) return <div className="semi-leftovers-empty">Остатки полуфабрикатов: загрузка…</div>;
  if (!rows.length) return <div className="semi-leftovers-empty">Остатков полуфабрикатов нет.</div>;
  return <details className="semi-leftovers">
    <summary>Остатки полуфабрикатов · {countLabel(rows.length, ["позиция", "позиции", "позиций"])}, {pieces(total(rows))}</summary>
    <div className="semi-leftovers-scroll">
      <table>
        <thead><tr>
          <th scope="col">Изделие</th><th scope="col">Этап</th><th scope="col">Задание</th>
          <th scope="col" className="num">Сдано</th><th scope="col" className="num">Взято дальше</th>
          <th scope="col" className="num">Ещё нужно</th><th scope="col" className="num">Остаток</th><th scope="col">Коробки</th>
        </tr></thead>
        <tbody>
          {rows.map((row) => <tr key={row.operation_id}>
            <td><b>{kbLabel(row.project, row.item)}</b></td>
            <td>{row.position} · {row.stage}{row.code && <small translate="no">{row.code}</small>}</td>
            <td>{row.task_number}<small>{row.task_status}</small></td>
            <td className="num">{formatNumber(row.produced)}</td>
            <td className="num">{formatNumber(row.consumed)}</td>
            <td className="num">{formatNumber(row.needed)}</td>
            <td className="num"><b>{formatNumber(row.surplus)}</b></td>
            <td className="semi-leftovers-boxes">{boxNumbers(row)}</td>
          </tr>)}
        </tbody>
      </table>
    </div>
  </details>;
}
