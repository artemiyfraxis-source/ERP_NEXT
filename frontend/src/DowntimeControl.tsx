import { useEffect, useState } from "react";
import { failureText } from "./api";
import { DOWNTIME_REASONS, downtimeApi, downtimeDuration, refreshOpenDowntimes, useOpenDowntimes, type DowntimeReason } from "./downtimeApi";
import { formatMoment } from "./format";
import "./downtime.css";

/** Простой по заданию: «Простой…» → причина (+ комментарий, у «Другое» обязателен) → «Начать простой»; открытый
 *  простой задания — полосой с причиной и временем и «Закончить простой». У задания открыт не больше одного простоя.
 *  Монтажник отмечает свой простой по операции; мастер на экране задания — простой задания целиком. */
export function DowntimeControl({ taskId, operationId = null, primary = true, performerId, wholeTask = false, master = false, readOnly = false, onBehalfOf = "" }: {
  taskId: string;
  operationId?: string | null;
  /** Первая карточка задания в очереди: на ней — полоса открытого простоя; на остальных — короткая строка. */
  primary?: boolean;
  /** Чей простой: монтажник карточки (или тот, за кого работают). */
  performerId: string;
  /** Мастер на экране задания: простой задания целиком. */
  wholeTask?: boolean;
  /** Мастер закрывает любой простой задания, монтажник — только свой. */
  master?: boolean;
  readOnly?: boolean;
  onBehalfOf?: string;
}) {
  const { items } = useOpenDowntimes();
  const [form, setForm] = useState(false);
  const [reason, setReason] = useState<DowntimeReason | "">("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const open = items?.find((item) => item.task_id === taskId) ?? null;

  useEffect(() => {
    if (!open) return;
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, [open]);

  async function act(operation: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await operation();
      setForm(false);
      setReason("");
      setComment("");
    } catch (reason) {
      setError(failureText(reason, "Простой не записан"));
    } finally {
      await refreshOpenDowntimes();
      setNow(Date.now());
      setBusy(false);
    }
  }

  const needComment = reason === "other" && !comment.trim();
  const canStop = !readOnly && open && (master || open.operator_id === performerId);

  if (open && !primary) {
    return <div className="dwn-bar dwn-elsewhere">Задание стоит: {open.reason_name.toLowerCase()} с {formatMoment(open.started_at)}{open.stage ? ` (${open.stage})` : ""}</div>;
  }
  if (open) {
    const elapsed = Math.max(open.seconds, Math.round((now - Date.parse(open.started_at)) / 1000));
    const who = open.whole_task ? "задание целиком" : open.operator_name;
    return <div className="dwn-bar dwn-open" role="status">
      <span className="dwn-mark">Простой</span>
      <span className="dwn-text"><b>{open.reason_name}</b> · с {formatMoment(open.started_at)} · <b className="dwn-time">{downtimeDuration(elapsed)}</b> · {who}{open.stage ? ` · ${open.stage}` : ""}{open.comment ? ` — ${open.comment}` : ""}</span>
      {canStop && <button type="button" className="primary small" disabled={busy} onClick={() => void act(() => downtimeApi.stop(open.id, onBehalfOf))}>Закончить простой</button>}
      {error && <span className="dwn-error" role="alert">{error}</span>}
    </div>;
  }
  if (readOnly) return null;
  return <div className="dwn-bar">
    {!form && <button type="button" className="secondary small" aria-expanded={false} onClick={() => setForm(true)}>{wholeTask ? "Простой задания…" : "Простой…"}</button>}
    {form && <div className="dwn-form" role="group" aria-label={wholeTask ? "Простой задания" : "Простой"}>
      <div className="seg-tabs dwn-reasons" role="radiogroup" aria-label="Причина простоя">
        {DOWNTIME_REASONS.map((item) => <button key={item.key} type="button" role="radio" aria-checked={reason === item.key}
          className={reason === item.key ? "on" : undefined} onClick={() => setReason(item.key)}>{item.name}</button>)}
      </div>
      <input className="dwn-comment" aria-label="Комментарий к простою" maxLength={500} value={comment}
        placeholder={reason === "other" ? "Что случилось — обязательно" : "Комментарий"} onChange={(event) => setComment(event.target.value)} />
      <button type="button" className="primary small" disabled={busy || !reason || needComment}
        title={!reason ? "Выберите причину" : needComment ? "Напишите, что случилось" : undefined}
        onClick={() => reason && void act(() => downtimeApi.start({ task_id: taskId, operation_id: operationId, reason, comment: comment.trim(), whole_task: wholeTask }, onBehalfOf))}>
        Начать простой
      </button>
      <button type="button" className="secondary small" disabled={busy} onClick={() => { setForm(false); setError(""); }}>Отмена</button>
    </div>}
    {error && <span className="dwn-error" role="alert">{error}</span>}
  </div>;
}
