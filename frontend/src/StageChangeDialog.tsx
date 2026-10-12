import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { StageCloseItem, StageKeepItem, StagePreview } from "./api";
import { AutoTextarea } from "./AutoTextarea";

export const STAGE_AUTO = "авто";

/** Подпись выбранного этапа: «авто» — «Авто (по фактам)». */
export function stageValueLabel(value: string) {
  return value === STAGE_AUTO ? "Авто (по фактам)" : value;
}

function failure(reason: unknown, fallback: string) {
  return reason instanceof Error && reason.message ? reason.message : fallback;
}

const CLOSE_GROUPS: Array<{ kind: string; title: string }> = [
  { kind: "kit_request", title: "Заявки на комплектацию" },
  { kind: "task", title: "Задания цеха" },
  { kind: "reserve", title: "Резервы техкарты" },
];
const RESERVE_POSITIONS = /\s*·\s*снимается резерв с (\d+) поз\./;
const OPEN_LIMIT = 4;

/** Подпись закрываемой заявки приходит с хвостом «· снимается резерв с N поз.» — хвост уходит в итог группы. */
function closeGroup(items: StageCloseItem[], kind: string) {
  const rows = items.filter((item) => item.kind === kind).map((item) => {
    const match = RESERVE_POSITIONS.exec(item.label);
    return { id: item.id, label: item.label.replace(RESERVE_POSITIONS, ""), positions: match ? Number(match[1]) : 0 };
  });
  return { rows, positions: rows.reduce((sum, row) => sum + row.positions, 0) };
}

function ClosePart({ items }: { items: StageCloseItem[] }) {
  return <ul className="stage-cascade-list" aria-label="Будет закрыто">
    {CLOSE_GROUPS.map(({ kind, title }) => {
      const { rows, positions } = closeGroup(items, kind);
      if (rows.length === 0) return null;
      const head = <><b>{title}: {rows.length}</b>{positions > 0 && <span className="muted"> (вернётся резерв {positions} поз.)</span>}</>;
      return <li key={kind}>
        <details open={rows.length <= OPEN_LIMIT}>
          <summary>{head}</summary>
          <ul>{rows.map((row) => <li key={row.id}>{row.label}</li>)}</ul>
        </details>
      </li>;
    })}
  </ul>;
}

function KeepPart({ items }: { items: StageKeepItem[] }) {
  return <details className="stage-cascade-keep" open={items.length <= OPEN_LIMIT}>
    <summary>Остаётся: {items.length}</summary>
    <ul>{items.map((item, index) => <li key={`${item.label}-${index}`}>{item.label}<span className="muted"> — {item.reason}</span></li>)}</ul>
  </details>;
}

/** Подтверждение ручной смены этапа — одно окно для карточки проекта, «Состава» и «Производственного плана».
 *  Сначала предпросмотр с сервера (по каждой КБ «было → станет», пропущенные этапы, что смена не трогает),
 *  потом запись; назад — только с причиной. */
export function StageChangeDialog({ project, kb = "", current, value, loadPreview, apply, onClose }: {
  project: string;
  /** Пусто — весь проект, иначе одна КБ. */
  kb?: string;
  /** Этап, с которого меняем (то, что видно на экране сейчас). */
  current: string;
  value: string;
  loadPreview: () => Promise<StagePreview>;
  /** `previewToken` — из предпросмотра: изменилось закрываемое с тех пор → сервер ответит 409. */
  apply: (reason: string, previewToken: string) => Promise<void>;
  onClose: () => void;
}) {
  const [preview, setPreview] = useState<StagePreview | null>(null);
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const loader = useRef(loadPreview);
  loader.current = loadPreview;

  const load = useCallback(async () => {
    setPreview(null); setLoadError("");
    try { setPreview(await loader.current()); }
    catch (cause) { setLoadError(failure(cause, "Предпросмотр не получен")); }
  }, []);
  useEffect(() => { void load(); }, [load, project, kb, value]);

  const needReason = Boolean(preview?.reason_required);
  const blocked = !preview || Boolean(preview.error) || preview.direction === "none" || (needReason && !reason.trim());

  async function submit() {
    if (busy || blocked) return;
    setBusy(true); setError("");
    try { await apply(reason.trim(), preview?.preview_token ?? ""); }
    catch (cause) {
      // Этапы проекта могли измениться (409) — показываем ответ сервера и перечитываем предпросмотр.
      setError(failure(cause, "Этап не изменён"));
      void load();
    } finally { setBusy(false); }
  }

  // Закрываемое и остающееся показывают свои разделы; построчные тексты КБ — только от сервера без них.
  const note = preview?.cascade_note ?? "";
  const warnings = preview ? [...new Set([
    ...preview.warnings.filter((text) => text !== note),
    ...preview.kbs.filter((item) => item.close === undefined && item.keep === undefined).flatMap((item) => item.warnings),
  ])] : [];
  const closing = preview?.close ?? [];
  const keeping = preview?.keep ?? [];
  const closeCount = preview?.totals?.close ?? closing.length;
  const forward = preview?.direction === "forward";
  const title = `${current || "—"} → ${stageValueLabel(value)}`;

  return createPortal(<div className="dialog-overlay" onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); onClose(); } }} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <form className="move-dialog stage-dialog panel" role="dialog" aria-modal="true" aria-label="Смена этапа"
      onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <h2>Смена этапа · {project}{kb ? ` · ${kb}` : ""}</h2>
      <div className="stage-dialog-change" aria-label="Что меняется">{title}</div>
      {!preview && !loadError && <div className="empty">Загрузка…</div>}
      {loadError && <div className="error-banner" role="alert">{loadError}</div>}
      {preview?.error && <div className="error-banner" role="alert">{preview.error}</div>}
      {preview && <div className="stage-dialog-body">
        {preview.kbs.length > 0 && <table className="stage-dialog-kbs" aria-label="Что станет с КБ"><thead><tr><th>КБ</th><th>Было</th><th>Станет</th><th>Примечание</th></tr></thead>
          <tbody>{preview.kbs.map((item) => <tr key={item.kb} className={item.change ? "" : "stage-keep"}>
            <th scope="row">{item.label || item.kb}</th>
            <td>{item.from || "—"}</td>
            <td>{item.change ? <b>{item.to}</b> : <span className="muted">{item.to || "—"}</span>}</td>
            <td>{item.skipped.length > 0 && <span className="stage-skipped">пропущены: {item.skipped.join(", ")}</span>}
              {item.note && <span className="muted">{item.note}</span>}</td>
          </tr>)}</tbody></table>}
        {preview.direction === "none" && !preview.error && <div className="page-sub">{preview.message || "Без изменений."}</div>}
        {warnings.length > 0 && <div className="warning-inline stage-dialog-warn" role="status"><ul>{warnings.map((text) => <li key={text}>{text}</li>)}</ul></div>}
        {(closing.length > 0 || keeping.length > 0 || note || (forward && closing.length === 0)) && <div className="stage-cascade">
          {closing.length > 0 && <section><h3>Будет закрыто</h3><ClosePart items={closing} /></section>}
          {forward && closing.length === 0 && <div className="page-sub">Закрывать нечего — меняется только этап</div>}
          {note && <div className="page-sub">{note}</div>}
          {keeping.length > 0 && <section className="muted"><KeepPart items={keeping} /></section>}
        </div>}
        <label className="wide">{needReason ? "Причина (обязательно: этап назад)" : "Причина (необязательно)"}
          <AutoTextarea aria-label="Причина смены этапа" autoFocus value={reason} onChange={setReason} maxRows={4}
            placeholder={needReason ? "почему возвращаем этап" : "например: сделали вживую, пропустили набор"} />
        </label>
      </div>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="move-actions page-actions">
        <button type="submit" className="primary" disabled={busy || blocked}>{forward && closeCount > 0 ? `Сменить этап и закрыть ${closeCount}` : "Сменить этап"}</button>
        <button type="button" className="secondary" onClick={onClose}>Отмена</button>
      </div>
    </form>
  </div>, document.body);
}
