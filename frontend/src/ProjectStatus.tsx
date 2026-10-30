import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { kitsetsApi, productionPlanApi, type KitsetCard, type ProjectMarkerField, type ProjectMarkerManual, type ProjectMarkers, type ProjectMarkerValue, type ProjectStatusAction, type StagePreview } from "./api";
import { AutoTextarea } from "./AutoTextarea";
import { STAGE_AUTO, StageChangeDialog } from "./StageChangeDialog";
import { StatusChip } from "./StatusChip";
import { statusTone } from "./statusTone";

/** Запасной список этапов — только когда экран не передал свой (порядок этапов ведёт сервер: `stages` плана и карточки). */
const FALLBACK_STAGES = ["Новый", "В работе", "Готово", "ОТК", "ОТК пройден", "Принято складом", "Упаковано", "Отгружено"];
export const PROJECT_TERMINALS = ["Отменён"];
export const PROJECT_BLOCKERS = ["СТОП", "ОЖИД ИНФ", "ОЖИД КОМПЛ"];

/** Откуда меню берёт предпросмотр этапа и куда пишет смену: экран «Проекты» — карточка набора, «Загрузка цеха» — журнал
 *  плана (проекту без карточки тоже можно). Вернул карточку — её получает `onChanged`; null — менять нечего, кроме списка. */
export interface ProjectStatusApi {
  stagePreview: (project: string, value: string) => Promise<StagePreview>;
  setStatus: (project: string, payload: ProjectStatusAction) => Promise<KitsetCard | null>;
}

export const kitsetStatusApi: ProjectStatusApi = {
  stagePreview: (project, value) => kitsetsApi.stagePreview(project, value),
  setStatus: async (project, payload) => (payload.action === "marker"
    ? await kitsetsApi.setMarker(project, payload.field, payload.value)
    : await kitsetsApi.setStatus(project, payload)).card,
};

/** Журнал плана: этап — запись «Этап КБ» на весь проект, блокер и закрытие — записи тех же видов, что в карточке. */
export const planStatusApi: ProjectStatusApi = {
  stagePreview: (project, value) => productionPlanApi.stagePreview(project, value),
  setStatus: async (project, payload) => {
    if (payload.action === "marker") await kitsetsApi.setMarker(project, payload.field, payload.value);
    else if (payload.action === "block") await productionPlanApi.addEvent({ project, kind: "block", blocker: payload.value, text: payload.text });
    else if (payload.action === "unblock") await productionPlanApi.addEvent({ project, kind: "unblock", text: payload.text ?? "" });
    else if (PROJECT_TERMINALS.includes(payload.value)) await productionPlanApi.addEvent({ project, kind: "status", value: payload.value, text: payload.text ?? "" });
    else await productionPlanApi.addEvent({ project, kind: "stage", item_index: "", value: payload.value, reason: payload.reason ?? "", preview_token: payload.preview_token ?? "" });
    return null;
  },
};

/** Две отметки проекта рядом с этапом: «ТК готова / не готова», «КБ набрана / не набрана». Да — зелёная, нет — серая;
 *  поставленная вручную помечена точкой и подсказкой «вручную». Без значений (экран их не передал) — ничего. */
export function ProjectMarks({ tcReady, kitPicked, tcManual = null, kitManual = null }: {
  tcReady?: boolean; kitPicked?: boolean; tcManual?: ProjectMarkerManual; kitManual?: ProjectMarkerManual;
}) {
  const mark = (value: boolean | undefined, manual: ProjectMarkerManual, yes: string, no: string) => value === undefined ? null
    : <StatusChip key={yes} status={value ? yes : no} tone={value ? "success" : "neutral"} className={`proj-mark${manual ? " manual" : ""}`}
      title={manual ? `${value ? yes : no} — вручную` : undefined} />;
  return <>{mark(tcReady, tcManual, "ТК готова", "ТК не готова")}{mark(kitPicked, kitManual, "КБ набрана", "КБ не набрана")}</>;
}

const MARKER_ROWS: Array<[ProjectMarkerField, string]> = [["tc_ready", "ТК"], ["kit_picked", "КБ"]];
const MARKER_VALUES: ProjectMarkerValue[] = ["да", "нет", "авто"];

/** Статус проекта с меню смены в один клик — в плитке списка и в шапке карточки.
 *  Этап (Новый → Отгружено) ставится на все КБ разом, «Авто» возвращает этапы по фактам (ТК выдана, набрано,
 *  задания, ГП на складе, отгрузка). Блокер (ХОЛД, СТОП…) — с причиной, как «Поставить блокер» плана SUPPLYMAP.
 *  Отметки «ТК готова» и «КБ набрана» — отдельно от этапа: по фактам или вручную (да / нет / авто).
 *  «Отменён» закрывает проект; этап или «Авто» возвращают его в работу. «Отгружено» по факту
 *  отгрузки ставится само. */
export function ProjectStatus({ project, status, blocker, canEdit, tcReady, kitPicked, tcManual, kitManual, stages = FALLBACK_STAGES, terminals = PROJECT_TERMINALS, blockers = PROJECT_BLOCKERS, api = kitsetStatusApi, floating = false, onChanged, onError }: {
  project: string;
  status: string;
  blocker: string;
  canEdit: boolean;
  /** Отметки проекта (сервер: `tc_ready`, `kit_picked`, `tc_manual`, `kit_manual`); не переданы — отметок нет. */
  tcReady?: boolean;
  kitPicked?: boolean;
  tcManual?: ProjectMarkerManual;
  kitManual?: ProjectMarkerManual;
  stages?: string[];
  terminals?: string[];
  blockers?: string[];
  /** Адаптер записи; по умолчанию — «Проекты». */
  api?: ProjectStatusApi;
  /** Меню поверх таблицы с прокруткой (лист «Загрузка цеха»): позиция по кнопке, а не внутри ячейки, чтобы не обрезалось. */
  floating?: boolean;
  /** Получает карточку проекта, если адаптер её вернул; без неё (план) список перечитывает сам экран. */
  onChanged: (card: KitsetCard | null) => void;
  onError?: (text: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [holdKind, setHoldKind] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [pendingStage, setPendingStage] = useState<string | null>(null);
  const box = useRef<HTMLSpanElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<CSSProperties | undefined>(undefined);
  const closed = terminals.includes(status);

  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => { if (box.current && !box.current.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("mousedown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);

  // Плавающее меню: под кнопкой, у нижнего края окна — над ней; прокрутка и смена размера закрывают его.
  useLayoutEffect(() => {
    if (!floating || !open || !box.current || !menu.current) { setPlace(undefined); return; }
    const anchor = box.current.getBoundingClientRect();
    const height = menu.current.offsetHeight;
    const width = menu.current.offsetWidth;
    const below = anchor.bottom + 6;
    const top = below + height > window.innerHeight - 8 ? Math.max(8, anchor.top - 6 - height) : below;
    setPlace({ position: "fixed", top, left: Math.max(8, Math.min(anchor.left, window.innerWidth - width - 8)) });
  }, [floating, open, holdKind, error]);
  useEffect(() => {
    if (!floating || !open) return;
    const resize = () => setOpen(false);
    const scroll = (event: Event) => { if (!menu.current?.contains(event.target as Node)) setOpen(false); };
    window.addEventListener("resize", resize);
    window.addEventListener("scroll", scroll, true);
    return () => { window.removeEventListener("resize", resize); window.removeEventListener("scroll", scroll, true); };
  }, [floating, open]);

  async function run(payload: ProjectStatusAction) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const card = await api.setStatus(project, payload);
      setOpen(false); setHoldKind(""); setReason("");
      onChanged(card);
    } catch (reason) {
      const text = reason instanceof Error ? reason.message : "Статус не изменён";
      setError(text); onError?.(text);
    } finally { setBusy(false); }
  }

  /** Этап проекта — через окно подтверждения с предпросмотром (пропущенные этапы, предупреждения, причина назад). */
  async function applyStage(value: string, reason: string, previewToken: string) {
    const card = await api.setStatus(project, { action: "status", value, reason, preview_token: previewToken });
    setPendingStage(null);
    onChanged(card);
  }
  function pickStage(value: string) { setOpen(false); setError(""); setPendingStage(value); }

  const marks = <ProjectMarks tcReady={tcReady} kitPicked={kitPicked} tcManual={tcManual} kitManual={kitManual} />;
  const manualOf = (field: ProjectMarkerField) => (field === "tc_ready" ? tcManual : kitManual) ?? "авто";
  const blockChip = blocker ? <StatusChip status={blocker} title="Активный блокер из журнала проекта" /> : null;
  if (!canEdit) return <span className="proj-status"><StatusChip status={status} />{marks}{blockChip}</span>;

  return <span className="proj-status" ref={box} onClick={(event) => event.stopPropagation()} onKeyDown={(event) => {
    // Плитка открывает карточку по Enter — клавиши меню до неё не доходят; Escape закрывает меню.
    event.stopPropagation();
    if (event.key === "Escape") setOpen(false);
  }}>
    <button type="button" className="proj-status-btn" aria-haspopup="menu" aria-expanded={open}
      aria-label={`Статус проекта ${project}: ${status}${blocker ? `, блокер ${blocker}` : ""}`} onClick={() => setOpen(!open)}>
      <StatusChip status={status}>{status || "—"}<span className="proj-status-caret" aria-hidden="true">▾</span></StatusChip>
    </button>
    {marks}
    {blockChip}
    {open && <div className="proj-status-menu" ref={menu} style={floating ? place ?? { visibility: "hidden" } : undefined} role="menu" aria-label={`Статус проекта ${project}`}>
      <div className="psm-head">Этап</div>
      <div className="psm-row">
        {stages.map((stage) => <button type="button" key={stage} role="menuitemradio" aria-checked={stage === status} disabled={busy}
          className={`psm-item${stage === status ? " on" : ""}`} data-tone={statusTone(stage)} onClick={() => pickStage(stage)}>{stage}</button>)}
        <button type="button" role="menuitem" className="psm-item" disabled={busy} title="Снять ручные этапы: статус по фактам (задания, склад, отгрузка)"
          onClick={() => pickStage(STAGE_AUTO)}>{closed ? "Вернуть в работу" : "Авто"}</button>
      </div>
      {status === "Отгружено" && <div className="psm-note">Отгрузка по факту ставит «Отгружено» и сильнее ручного этапа.</div>}
      <div className="psm-head">Отметки</div>
      {MARKER_ROWS.map(([field, label]) => <div className="psm-row psm-marks" key={field} role="group" aria-label={`Отметка ${label}`}>
        <span className="psm-mark-name">{label === "ТК" ? "ТК готова" : "КБ набрана"}</span>
        {MARKER_VALUES.map((value) => <button type="button" key={value} role="menuitemradio" aria-checked={manualOf(field) === value} disabled={busy}
          aria-label={`${label}: ${value}`} className={`psm-item${manualOf(field) === value ? " on" : ""}`}
          title={value === "авто" ? "По фактам" : `Поставить вручную: ${value}`} onClick={() => void run({ action: "marker", field, value })}>{value}</button>)}
      </div>)}
      <div className="psm-head">Блокер</div>
      <div className="psm-row">
        {blocker
          ? <button type="button" role="menuitem" className="psm-item" disabled={busy} onClick={() => void run({ action: "unblock" })}>Снять {blocker}</button>
          : blockers.map((kind) => <button type="button" key={kind} role="menuitem" disabled={busy}
            className={`psm-item${holdKind === kind ? " on" : ""}`} data-tone={statusTone(kind)} onClick={() => { setHoldKind(kind); setError(""); }}>{kind}</button>)}
      </div>
      {!blocker && holdKind && <form className="psm-reason" onSubmit={(event) => { event.preventDefault(); void run({ action: "block", value: holdKind, text: reason }); }}>
        <AutoTextarea autoFocus aria-label={`Причина ${holdKind}`} placeholder={`Причина ${holdKind}`} value={reason} onChange={setReason} />
        <button type="submit" className="primary small" disabled={busy || !reason.trim()}>Поставить</button>
      </form>}
      <div className="psm-head">Закрыть проект</div>
      <div className="psm-row">
        {terminals.map((terminal) => <button type="button" key={terminal} role="menuitemradio" aria-checked={terminal === status} disabled={busy}
          className={`psm-item${terminal === status ? " on" : ""}`} data-tone={statusTone(terminal)} onClick={() => void run({ action: "status", value: terminal })}>{terminal}</button>)}
      </div>
      {error && <div className="psm-error" role="alert">{error}</div>}
    </div>}
    {pendingStage !== null && <StageChangeDialog project={project} current={status} value={pendingStage}
      loadPreview={() => api.stagePreview(project, pendingStage)}
      apply={(why, token) => applyStage(pendingStage, why, token)} onClose={() => setPendingStage(null)} />}
  </span>;
}
