import { useEffect, useRef, useState } from "react";
import { kitsetsApi, type KitsetCard, type ProjectStatusAction } from "./api";
import { toneOf } from "./ProjectCard";

export const PROJECT_STAGES = ["Новый", "ТК готова", "КБ набрана", "В работе", "КБ на складе", "Отгружено"];
export const PROJECT_TERMINALS = ["Отказ", "Слился", "Отменён"];
export const PROJECT_BLOCKERS = ["ХОЛД", "СТОП", "ОЖИД ИНФ", "ОЖИД КОМПЛ", "ПАУЗА"];

/** Статус проекта с меню смены в один клик — в плитке списка и в шапке карточки.
 *  Этап (Новый → Отгружено) ставится на все КБ разом, «Авто» возвращает этапы по фактам (ТК выдана, набрано,
 *  задания, ГП на складе, отгрузка). Блокер (ХОЛД, СТОП…) — с причиной, как «Поставить блокер» плана SUPPLYMAP.
 *  Отказ / Слился / Отменён закрывают проект; этап или «Авто» возвращают его в работу. «Отгружено» по факту
 *  отгрузки ставится само. */
export function ProjectStatus({ project, status, blocker, canEdit, stages = PROJECT_STAGES, terminals = PROJECT_TERMINALS, blockers = PROJECT_BLOCKERS, onChanged, onError }: {
  project: string;
  status: string;
  blocker: string;
  canEdit: boolean;
  stages?: string[];
  terminals?: string[];
  blockers?: string[];
  onChanged: (card: KitsetCard) => void;
  onError?: (text: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [holdKind, setHoldKind] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const box = useRef<HTMLSpanElement>(null);
  const ladder = [...stages, ...terminals];
  const closed = terminals.includes(status);

  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => { if (box.current && !box.current.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("mousedown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);

  async function run(payload: ProjectStatusAction) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const result = await kitsetsApi.setStatus(project, payload);
      setOpen(false); setHoldKind(""); setReason("");
      onChanged(result.card);
    } catch (reason) {
      const text = reason instanceof Error ? reason.message : "Статус не изменён";
      setError(text); onError?.(text);
    } finally { setBusy(false); }
  }

  const chip = <span className={`kit-status tone-${toneOf(status, ladder)}`}>{status || "—"}</span>;
  const blockChip = blocker ? <span className="kit-fact tone-danger" title="Активный блокер из журнала проекта">⛔ {blocker}</span> : null;
  if (!canEdit) return <span className="proj-status">{chip}{blockChip}</span>;

  return <span className="proj-status" ref={box} onClick={(event) => event.stopPropagation()} onKeyDown={(event) => {
    // Плитка открывает карточку по Enter — клавиши меню до неё не доходят; Escape закрывает меню.
    event.stopPropagation();
    if (event.key === "Escape") setOpen(false);
  }}>
    <button type="button" className={`kit-status tone-${toneOf(status, ladder)} proj-status-btn`} aria-haspopup="menu" aria-expanded={open}
      aria-label={`Статус проекта ${project}: ${status}${blocker ? `, блокер ${blocker}` : ""}`} onClick={() => setOpen(!open)}>{status || "—"} ▾</button>
    {blockChip}
    {open && <div className="proj-status-menu" role="menu" aria-label={`Статус проекта ${project}`}>
      <div className="psm-head">Этап</div>
      <div className="psm-row">
        {stages.map((stage) => <button type="button" key={stage} role="menuitemradio" aria-checked={stage === status} disabled={busy}
          className={`psm-item tone-${toneOf(stage, ladder)}${stage === status ? " on" : ""}`} onClick={() => void run({ action: "status", value: stage })}>{stage}</button>)}
        <button type="button" role="menuitem" className="psm-item" disabled={busy} title="Снять ручные этапы: статус по фактам (ТК, набор, задания, склад, отгрузка)"
          onClick={() => void run({ action: "status", value: "авто" })}>{closed ? "Вернуть в работу" : "Авто"}</button>
      </div>
      {status === "Отгружено" && <div className="psm-note">Отгрузка по факту ставит «Отгружено» и сильнее ручного этапа.</div>}
      <div className="psm-head">Блокер</div>
      <div className="psm-row">
        {blocker
          ? <button type="button" role="menuitem" className="psm-item" disabled={busy} onClick={() => void run({ action: "unblock" })}>Снять {blocker}</button>
          : blockers.map((kind) => <button type="button" key={kind} role="menuitem" disabled={busy}
            className={`psm-item tone-danger${holdKind === kind ? " on" : ""}`} onClick={() => { setHoldKind(kind); setError(""); }}>{kind}</button>)}
      </div>
      {!blocker && holdKind && <form className="psm-reason" onSubmit={(event) => { event.preventDefault(); void run({ action: "block", value: holdKind, text: reason }); }}>
        <input autoFocus aria-label={`Причина ${holdKind}`} placeholder={`Причина ${holdKind}`} value={reason} onChange={(event) => setReason(event.target.value)} />
        <button type="submit" className="primary small" disabled={busy || !reason.trim()}>Поставить</button>
      </form>}
      <div className="psm-head">Закрыть проект</div>
      <div className="psm-row">
        {terminals.map((terminal) => <button type="button" key={terminal} role="menuitemradio" aria-checked={terminal === status} disabled={busy}
          className={`psm-item tone-danger${terminal === status ? " on" : ""}`} onClick={() => void run({ action: "status", value: terminal })}>{terminal}</button>)}
      </div>
      {error && <div className="psm-error" role="alert">{error}</div>}
    </div>}
  </span>;
}
