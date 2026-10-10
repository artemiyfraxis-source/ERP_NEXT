import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { StagePreview } from "./api";
import { AutoTextarea } from "./AutoTextarea";

export const STAGE_AUTO = "авто";

/** Подпись выбранного этапа: «авто» — «Авто (по фактам)». */
export function stageValueLabel(value: string) {
  return value === STAGE_AUTO ? "Авто (по фактам)" : value;
}

function failure(reason: unknown, fallback: string) {
  return reason instanceof Error && reason.message ? reason.message : fallback;
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
  apply: (reason: string) => Promise<void>;
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

  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [onClose]);

  const needReason = Boolean(preview?.reason_required);
  const blocked = !preview || Boolean(preview.error) || preview.direction === "none" || (needReason && !reason.trim());

  async function submit() {
    if (busy || blocked) return;
    setBusy(true); setError("");
    try { await apply(reason.trim()); }
    catch (cause) {
      // Этапы проекта могли измениться (409) — показываем ответ сервера и перечитываем предпросмотр.
      setError(failure(cause, "Этап не изменён"));
      void load();
    } finally { setBusy(false); }
  }

  const warnings = preview ? [...new Set([...preview.warnings, ...preview.kbs.flatMap((item) => item.warnings)])] : [];
  const title = `${current || "—"} → ${stageValueLabel(value)}`;

  return createPortal(<div className="dialog-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <form className="move-dialog stage-dialog panel" role="dialog" aria-modal="true" aria-label="Смена этапа"
      onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <h2>Смена этапа · {project}{kb ? ` · ${kb}` : ""}</h2>
      <div className="stage-dialog-change" aria-label="Что меняется">{title}</div>
      {!preview && !loadError && <div className="empty">Загрузка…</div>}
      {loadError && <div className="error-banner" role="alert">{loadError}</div>}
      {preview?.error && <div className="error-banner" role="alert">{preview.error}</div>}
      {preview && <>
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
        <p className="page-sub">Меняется только этап. Задания цеха, заявки на комплектацию, готовая продукция и отгрузки остаются как есть.</p>
        <label className="wide">{needReason ? "Причина (обязательно: этап назад)" : "Причина (необязательно)"}
          <AutoTextarea aria-label="Причина смены этапа" autoFocus value={reason} onChange={setReason} maxRows={4}
            placeholder={needReason ? "почему возвращаем этап" : "например: сделали вживую, пропустили набор"} />
        </label>
      </>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="move-actions page-actions">
        <button type="submit" className="primary" disabled={busy || blocked}>Сменить этап</button>
        <button type="button" className="secondary" onClick={onClose}>Отмена</button>
      </div>
    </form>
  </div>, document.body);
}
