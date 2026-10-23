import { useEffect, useState } from "react";
import { AutoTextarea } from "./AutoTextarea";
import { productionPlanApi, type ProductionPlanResult, type ProductionPlanRow } from "./api";
import { StageChangeDialog } from "./StageChangeDialog";
import { EMPTY, formatDate, formatMoment } from "./format";
import { markText } from "./PlanLoadSheet";
import { statusTone } from "./statusTone";

const shortDate = (value: string | null) => formatDate(value) || EMPTY;
const moment = (value: string | null) => formatMoment(value) || EMPTY;

const OPEN_KEY = "erp-mb-project-block";
// По умолчанию свёрнут: окно проекта — про операции, блок раскрывают, когда нужен.
function readOpen() {
  try { return localStorage.getItem(OPEN_KEY) === "1"; } catch { return false; }
}

/** Одна строка свёрнутого блока: этап, КБ с проверкой ТК, снабжение, последняя запись журнала. */
function summaryText(row: ProductionPlanRow) {
  const parts = [row.stage];
  for (const item of row.kits) parts.push(`${item.item_index}: ${item.status}, ТК ${item.review?.value ?? "не проверена"}`);
  if (row.supply) parts.push(`Снабжение: склад ${row.supply.stock}, в пути ${row.supply.transit}, в закупке ${row.supply.buying}`);
  const last = row.events.reduce<ProductionPlanRow["events"][number] | null>((best, event) => !best || event.occurred_at > best.occurred_at ? event : best, null);
  if (last) parts.push(`последняя запись: ${moment(last.occurred_at)} ${last.kind}`);
  return parts.join(" · ");
}

/** Блок «Проект» окна проекта в «Ресурсном планировании» (владелец 22.10: боковая панель плана убрана, всё из неё — сюда):
 *  заказчик, КБ с этапом и проверкой ТК, снабжение, журнал и история. Данные и записи — те же, что у плана (`productionPlanApi`). */
export function ProjectPlanBlock({ project, canEdit, openProjectCard }: {
  project: string;
  canEdit: boolean;
  /** Карточка проекта в «Проектах»; без доступа к разделу — не передаётся. */
  openProjectCard?: (project: string) => void;
}) {
  const [plan, setPlan] = useState<ProductionPlanResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpenState] = useState(readOpen);
  function setOpen(next: boolean) {
    setOpenState(next);
    try { localStorage.setItem(OPEN_KEY, next ? "1" : "0"); } catch { /* без хранилища выбор просто не запомнится */ }
  }

  async function reload() {
    try {
      setPlan(await productionPlanApi.load());
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Данные проекта не загружены");
    }
  }
  useEffect(() => { void reload(); }, [project]); // eslint-disable-line react-hooks/exhaustive-deps

  const row = plan?.rows?.find((item) => item.project.trim().toUpperCase() === project.trim().toUpperCase()) ?? null;

  async function addEvent(payload: Parameters<typeof productionPlanApi.addEvent>[0]) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await productionPlanApi.addEvent(payload);
      await reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Запись в журнал не добавлена");
    } finally {
      setBusy(false);
    }
  }

  /** Ручной этап — после подтверждения в окне смены этапа; ошибка уходит в само окно (409, причина назад). */
  async function applyStage(name: string, kb: string, value: string, reason: string, previewToken: string) {
    await productionPlanApi.addEvent({ project: name, kind: "stage", blocker: "", text: "", deadline: null, item_index: kb, value, reason, preview_token: previewToken });
    await reload();
  }

  return <section className="mb-project-block" aria-label="Проект">
    <button type="button" className="mb-block-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
      <span aria-hidden>{open ? "▾" : "▸"}</span> <b>Проект</b>
      {row && <span className="mb-block-summary" title={summaryText(row)}>{summaryText(row)}</span>}
    </button>
    {open && <>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {!row && !error && <div className="empty">{plan ? `Проекта ${project} нет в производственном плане.` : "Загрузка…"}</div>}
      {row && <PlanDetails row={row} blockers={plan?.blockers ?? []} stages={plan?.stages ?? []} reviewValues={plan?.review_values ?? []}
        canEdit={canEdit} busy={busy} addEvent={addEvent} applyStage={applyStage} openProjectCard={openProjectCard ? () => openProjectCard(row.project) : undefined} />}
    </>}
  </section>;
}

function PlanDetails({ row, blockers, stages, reviewValues, canEdit, busy, addEvent, applyStage, openProjectCard }: {
  row: ProductionPlanRow;
  openProjectCard?: () => void;
  blockers: string[];
  stages: string[];
  reviewValues: string[];
  canEdit: boolean;
  busy: boolean;
  addEvent: (payload: Parameters<typeof productionPlanApi.addEvent>[0]) => Promise<void>;
  applyStage: (project: string, kb: string, value: string, reason: string, previewToken: string) => Promise<void>;
}) {
  const [stagePick, setStagePick] = useState<{ kb: string; current: string; value: string } | null>(null);
  const [kind, setKind] = useState<"note" | "block" | "unblock" | "deadline">("note");
  const [blocker, setBlocker] = useState(blockers[0] ?? "");
  const [text, setText] = useState("");
  const [deadline, setDeadline] = useState(row.deadline ?? "");

  // Сброс при смене проекта или срока — прямо при рендере, а не эффектом: эффект после первого показа мог стереть то, что
  // уже набрали (запись исчезала, кнопка «Записать» гасла).
  const [formFor, setFormFor] = useState(`${row.project}|${row.deadline ?? ""}`);
  if (formFor !== `${row.project}|${row.deadline ?? ""}`) {
    setFormFor(`${row.project}|${row.deadline ?? ""}`);
    setDeadline(row.deadline ?? "");
    setText("");
  }

  async function submit() {
    await addEvent({ project: row.project, kind, blocker, text, deadline: deadline || null });
    setText("");
    if (kind === "unblock") setKind("note");
  }

  const valid = kind === "unblock" || (kind === "deadline" ? Boolean(deadline) : Boolean(text.trim()) && (kind !== "block" || Boolean(blocker)));
  return <div className="production-plan-details">
    {openProjectCard && <div className="plan-acts"><button className="secondary" onClick={openProjectCard}>Открыть карточку проекта</button></div>}
    {row.blocker && <p className="plan-blocker" data-tone={statusTone(row.blocker.type)}><b>{row.blocker.type}</b> с {moment(row.blocker.since)} · {row.blocker.text} — {row.blocker.who}</p>}
    <p>Заказчик: {row.customer || "—"} · дедлайн {shortDate(row.deadline)} · заведён {shortDate(row.opened_on)}</p>
    <div className="plan-kit-stages">{row.kits.map((item) => <KitMarkEditor
      key={item.item_index} project={row.project} item={item} stages={stages}
      reviewValues={reviewValues} canEdit={canEdit} busy={busy} addEvent={addEvent}
      pickStage={(value) => setStagePick({ kb: item.item_index, current: item.status, value })}
    />)}
      {/* A project without KBs takes its stage from a journal stage mark with an empty KB. */}
      {!row.kits.length && canEdit && stages.includes(row.stage) && <div className="plan-kit-mark">
        <strong>Без КБ</strong>
        <label>Этап проекта<select aria-label="Этап проекта" value={row.stage_manual ? row.stage : "авто"} disabled={busy}
          onChange={(event) => setStagePick({ kb: "", current: row.stage, value: event.target.value })}>
          <option value="авто">Авто · {row.stage}</option>{stages.map((stage) => <option key={stage}>{stage}</option>)}
        </select></label>
      </div>}
    </div>
    {stagePick && <StageChangeDialog project={row.project} kb={stagePick.kb} current={stagePick.current} value={stagePick.value}
      loadPreview={() => productionPlanApi.stagePreview(row.project, stagePick.value, stagePick.kb)}
      apply={async (reason, token) => { await applyStage(row.project, stagePick.kb, stagePick.value, reason, token); setStagePick(null); }}
      onClose={() => setStagePick(null)} />}
    {row.supply ? <section><h3>Снабжение</h3><p>Всего {row.supply.total}: склад {row.supply.stock}, в пути {row.supply.transit}, в закупке {row.supply.buying}, в заявках {row.supply.request}.</p>
      <p>Давальческое: {row.supply.customer_supplied ? `${row.supply.customer_supplied_stock} из ${row.supply.customer_supplied} пришло` : "нет"}</p>
      {row.supply.eta && row.supply.stock < row.supply.total && <small>Всё будет к {row.supply.eta}</small>}</section>
      : <section><h3>Снабжение</h3><p>Заявок с этим проектом в НАЗНАЧЕНИИ нет.</p></section>}
    {row.plan && <section><h3>Загрузка цеха</h3><p>{row.plan.tasks} заданий · назначено {row.plan.assigned} из {row.plan.total} этапов.</p>{row.plan.start && <small>{moment(row.plan.start)} — {moment(row.plan.finish)}</small>}</section>}
    {canEdit && <section className="plan-note-form"><h3>Журнал проекта</h3><div className="plan-note-grid">
      <label>Запись<select value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}><option value="note">Комментарий</option><option value="block">Блокер</option>{row.blocker && <option value="unblock">Снять блокер</option>}<option value="deadline">Дедлайн</option></select></label>
      {kind === "block" && <label>Вид<select value={blocker} onChange={(event) => setBlocker(event.target.value)}>{blockers.map((item) => <option key={item}>{item}</option>)}</select></label>}
      {kind === "deadline" && <label>Дата<input type="date" value={deadline} onChange={(event) => setDeadline(event.target.value)} /></label>}
      {kind !== "unblock" && <label className="plan-note-text">Комментарий<AutoTextarea value={text} multiline maxRows={8} onChange={setText} /></label>}
      <button className="primary" disabled={busy || !valid} onClick={() => void submit()}>Записать</button>
    </div></section>}
    <section className="plan-journal"><h3>История</h3>{row.events.map((event) => <article key={event.id}><div><strong>{event.kind}{event.blocker ? ` · ${event.blocker}` : ""}</strong><time>{moment(event.occurred_at)}</time></div><p>{event.field ? markText(event) : event.text || "Без комментария"}</p><small>{event.who}</small></article>)}{!row.events.length && <div className="empty">Записей пока нет.</div>}</section>
  </div>;
}

function KitMarkEditor({ project, item, stages, reviewValues, canEdit, busy, addEvent, pickStage }: {
  project: string;
  item: ProductionPlanRow["kits"][number];
  stages: string[];
  reviewValues: string[];
  canEdit: boolean;
  busy: boolean;
  addEvent: (payload: Parameters<typeof productionPlanApi.addEvent>[0]) => Promise<void>;
  pickStage: (value: string) => void;
}) {
  const currentReview = item.review?.value ?? "не проверена";
  const [review, setReview] = useState(currentReview);
  const [reviewText, setReviewText] = useState(item.review?.text ?? "");
  useEffect(() => {
    setReview(currentReview);
    setReviewText(item.review?.text ?? "");
  }, [project, item.item_index, currentReview, item.review?.text]);

  function payload(kind: "tcReview", value: string, text = "") {
    return { project, kind, blocker: "", text, deadline: null, item_index: item.item_index, value } as const;
  }

  return <div className="plan-kit-mark">
    <strong>{item.item_index}</strong>
    <span>{item.tc ? "ТК выдана" : "ТК нет"} · {item.kit ? "набрано" : "не набрано"}</span>
    {canEdit ? <>
      <label>Этап<select aria-label={`Этап ${item.item_index}`} value={item.manual ? item.status : "авто"} disabled={busy}
        onChange={(event) => pickStage(event.target.value)}>
        <option value="авто">Авто · {item.status}</option>{stages.map((stage) => <option key={stage}>{stage}</option>)}
      </select></label>
      <label>Проверка ТК<select aria-label={`Проверка ТК ${item.item_index}`} value={review} disabled={busy}
        onChange={(event) => {
          const value = event.target.value;
          setReview(value);
          if (value !== "с погрешностями") void addEvent(payload("tcReview", value));
        }}>
        {reviewValues.map((value) => <option key={value}>{value}</option>)}
      </select></label>
      {review === "с погрешностями" && <label>Погрешности<AutoTextarea aria-label={`Погрешности ${item.item_index}`} value={reviewText} multiline onChange={setReviewText} /></label>}
      {review === "с погрешностями" && <button className="secondary small" disabled={busy || !reviewText.trim()} onClick={() => void addEvent(payload("tcReview", review, reviewText))}>Сохранить проверку</button>}
    </> : <>
      <span>{item.status}{item.manual ? " · вручную" : ""}</span>
      <span>{currentReview}{item.review?.text ? ` · ${item.review.text}` : ""}</span>
    </>}
  </div>;
}
