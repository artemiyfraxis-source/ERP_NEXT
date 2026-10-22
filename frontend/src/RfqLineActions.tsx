import { useState } from "react";
import { failureText } from "./api";
import { formatDay } from "./format";
import { hasPrices, rfqApi, type RfqLine, type RfqMatrix } from "./purchasingRfqApi";
import { pickable } from "./RfqMatrix";
import { quantity } from "./rfqUi";

/** The request's lines in bulk (Roller_Buyer RfqPage selection bar): pick lines — one by one, all, or a whole type —
 *  then «В новые» (out of the request as if it never was), «Перезапрос» (out with a note of who had nothing),
 *  «Отменить» (the procurement lines are cancelled with a reason) or «Разделить» (part of the quantity into a new line,
 *  the quotes stay with both parts). A line with a chosen supplier is not picked. */

export const TYPE_UNKNOWN = "Тип не указан";

/** Lines still pickable by their ТИП: «Выбрать по типу…» ticks all of one, e.g. all «Маркировка» to cancel at once. */
export function linesByType(lines: RfqLine[]): [string, string[]][] {
  const types = new Map<string, string[]>();
  for (const line of lines.filter(pickable)) {
    const key = line.item_type?.trim() || TYPE_UNKNOWN;
    types.set(key, [...(types.get(key) ?? []), line.id]);
  }
  return [...types.entries()].sort(([a], [b]) => a.localeCompare(b, "ru"));
}

/** «1 500,5» → "1500.5"; anything else → "". */
export function quantityInput(text: string): string {
  const clean = text.replace(/\s/g, "").replace(",", ".");
  return /^\d+(\.\d+)?$/.test(clean) && Number(clean) > 0 ? clean : "";
}

export function RfqLineToolbar({ matrix, picked, onPick, showRemoved, onShowRemoved }: {
  matrix: RfqMatrix; picked: Set<string>; onPick: (ids: string[], on: boolean) => void; showRemoved: boolean;
  onShowRemoved: (on: boolean) => void;
}) {
  const types = linesByType(matrix.lines);
  const hidden = matrix.lines.filter((line) => line.cancelled).length + (matrix.removed?.length ?? 0);
  return (
    <div className="rfq-line-toolbar">
      {types.length > 0 && (
        <select
          aria-label="Выбрать строки по типу" value=""
          title="Отметить все строки одного типа — например, всю «Маркировку», чтобы отменить её разом"
          onChange={(event) => { const found = types.find(([type]) => type === event.target.value); if (found) onPick(found[1], true); }}
        >
          <option value="">Выбрать по типу…</option>
          {types.map(([type, ids]) => (
            <option key={type} value={type}>{type} ({ids.length})</option>
          ))}
        </select>
      )}
      <span className="spacer" />
      {hidden > 0 && (
        <button type="button" className="link-button" onClick={() => onShowRemoved(!showRemoved)}>
          {showRemoved ? "скрыть отменённые и убранные" : `показать отменённые и убранные (${hidden})`}
        </button>
      )}
    </div>
  );
}

export function RfqSelectionBar({ matrix, picked, onClear, onDone, onError }: {
  matrix: RfqMatrix; picked: Set<string>; onClear: () => void; onDone: (message: string) => void; onError: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  // «Отменить» and «Разделить» are the requests slice's commands.
  const lines = matrix.lines.filter((line) => picked.has(line.id));

  async function run(task: () => Promise<string>, fallback: string) {
    setBusy(true);
    try {
      onDone(await task());
    } catch (reason) {
      onError(failureText(reason, fallback));
    } finally {
      setBusy(false);
    }
  }

  const withdraw = (to: "new" | "requote") => run(async () => {
    const body = { rfq_line_ids: lines.map((line) => line.id), to, drop_prices: false, rfq_version: matrix.rfq.version };
    try {
      await rfqApi.withdraw(matrix.rfq.id, body);
    } catch (error) {
      const priced = hasPrices(error);
      if (!priced || !window.confirm(`${priced.message}\n\nЦены сохранятся текстом в истории строки.`)) throw error;
      await rfqApi.withdraw(matrix.rfq.id, { ...body, drop_prices: true });
    }
    return to === "new" ? `Вернули в новые: ${lines.length}` : `В «Перезапрос»: ${lines.length} — в строке записано, кто не смог`;
  }, "Строки не убраны");

  const cancel = () => {
    const reason = window.prompt(`Отменить ${lines.length} строк заявки. Причина:`, "не закупаем");
    if (!reason?.trim()) return;
    void run(async () => {
      const result = await rfqApi.cancelMany(lines.map((line) => ({ id: line.procurement_line_id, version: line.version ?? 0 })), reason.trim());
      const skipped = result.skipped.map((item) => {
        const line = lines.find((candidate) => candidate.procurement_line_id === item.id);
        return `${line?.legacy_id ?? line?.name ?? item.id} — ${item.reason}`;
      });
      return `Отменено строк: ${result.cancelled.length}${skipped.length ? `; не отменены: ${skipped.join("; ")}` : ""}`;
    }, "Строки не отменены");
  };

  return (
    <div className="bulk-bar rfq-selection-bar" role="toolbar" aria-label="Выбранные строки запроса">
      <span>Выбрано строк: {picked.size}</span>
      <span className="spacer" />
      <button type="button" className="secondary small" onClick={onClear}>Снять выбор</button>
      <button type="button" className="secondary small" disabled={busy} title="Убрать из этого запроса и вернуть в свободные строки — как будто его и не было" onClick={() => void withdraw("new")}>В новые</button>
      <button type="button" className="secondary small" disabled={busy} title="Убрать из этого запроса как «Перезапрос»: в строке останется, кто из поставщиков не смог" onClick={() => void withdraw("requote")}>Перезапрос</button>
      <button type="button" className="secondary small" disabled={busy} title="Отменить строки заявки с причиной — закупать их не будем" onClick={cancel}>Отменить</button>
      {lines.length === 1 && (
        <button
          type="button" className="secondary small" disabled={busy}
          title="Разделить количество, чтобы части взять у разных поставщиков — КП останутся у обеих частей"
          onClick={() => { const task = askSplit(lines[0]); if (task) void run(task, "Строка не разделена"); }}
        >Разделить</button>
      )}
    </div>
  );
}

/** «Разделить»: asks how much goes into a new procurement line (to buy the parts from different suppliers); null — the
 *  buyer changed his mind or typed nonsense. */
export function askSplit(line: RfqLine): (() => Promise<string>) | null {
  const text = window.prompt(`Сколько отделить в новую строку (из ${quantity(line.quantity)} ${line.unit})?`);
  if (text === null) return null;
  const value = quantityInput(text);
  if (!value || Number(value) >= Number(line.quantity)) {
    window.alert("Нужно число больше нуля и меньше количества строки");
    return null;
  }
  return async () => {
    await rfqApi.splitQty(line.procurement_line_id, line.version ?? 0, value);
    return `${line.legacy_id ?? line.name}: ${value} ${line.unit} отделено в новую строку — КП остались у обеих частей`;
  };
}

/** «показать … убранные»: the lines taken out of the request, why and who answered what. */
export function RemovedLines({ matrix }: { matrix: RfqMatrix }) {
  const removed = matrix.removed ?? [];
  if (removed.length === 0) return null;
  return (
    <div className="data-table rfq-removed" aria-label="Убранные из запроса строки">
      <table className="grid">
        <thead><tr><th>Строка</th><th className="num">Кол-во</th><th>Убрана</th><th>Куда</th><th className="grow">Кто что ответил</th></tr></thead>
        <tbody>
          {removed.map((line) => (
            <tr key={line.id}>
              <td className="text"><strong>{line.name}</strong><small className="muted"> {line.legacy_id ?? ""}</small></td>
              <td className="num">{quantity(line.quantity)} {line.unit}</td>
              <td>{formatDay(line.removed_at)}</td>
              <td>{line.reason_label}</td>
              <td className="text">{line.note ?? "решения не было"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
