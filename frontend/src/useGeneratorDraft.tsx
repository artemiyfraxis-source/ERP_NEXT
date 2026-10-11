import { useEffect, useRef, useState } from "react";

/*
 * Черновик генератора ТК (владелец 10.10: «ввёл данные и переключился — всё слетело»). Форма генератора живёт в
 * состоянии компонента, а вкладки карточки проекта и смена КБ его размонтируют. Рабочее состояние формы пишется в
 * хранилище браузера за пользователем, проектом и КБ: через секунду после последней правки и сразу при уходе
 * (размонтирование, скрытие вкладки, закрытие страницы). При открытии черновик, сделанный поверх текущей сборки ЭТК,
 * возвращается в форму. Пишутся только правки пользователя: засев формы и автоподбор шаблонов черновиком не считаются.
 * Каждая запись несёт номер ревизии: другая вкладка, записавшая более новый черновик, не перезаписывается.
 */

const PREFIX = "erp-tcgen-draft";
const FORMAT = 1;

type Stored<T> = { v: number; rev: number; savedAt: string; built: string; value: T };

export type DraftStatus =
  | { kind: "idle" }
  | { kind: "pending" }
  | { kind: "saved"; at: string }
  | { kind: "error"; text: string };

export function draftKey(user: string, project: string, index: string) {
  return `${PREFIX}:${user}:${project}:${index}`;
}

/** Отпечаток собранной конфигурации карты: черновик действует, пока ЭТК не пересобрали поверх него. */
export function buildMark(document: Record<string, unknown> | null | undefined): string {
  const text = JSON.stringify(document?.build_config ?? null);
  let hash = 5381;
  for (let i = 0; i < text.length; i += 1) hash = ((hash << 5) + hash + text.charCodeAt(i)) | 0;
  return `${text.length}:${(hash >>> 0).toString(36)}`;
}

function read<T>(key: string): Stored<T> | null {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const stored = JSON.parse(raw) as Stored<T>;
    return stored && stored.v === FORMAT && typeof stored.rev === "number" && stored.value ? stored : null;
  } catch { return null; }
}

function remove(key: string) {
  try { window.localStorage.removeItem(key); } catch { /* хранилище недоступно — удалять нечего */ }
}

const isPlain = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);

/** Черновик старой версии формы: недостающие поля берутся из засева, чтобы форма не падала на новом поле. */
function mergeSeed<T>(seed: T, stored: T): T {
  if (!isPlain(seed) || !isPlain(stored)) return stored;
  const out: Record<string, unknown> = { ...seed };
  for (const [key, value] of Object.entries(stored)) {
    out[key] = isPlain(seed[key]) && isPlain(value) ? { ...seed[key], ...value } : value;
  }
  return out as T;
}

export const clockText = (iso: string) => new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
export const stampText = (iso: string) => new Date(iso).toLocaleString("ru-RU", {
  day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
});

export function useGeneratorDraft<T>({ user, project, index, document, value, apply, delay = 1000 }: {
  /** Нет пользователя — черновик не ведётся (чужой черновик на общем компьютере цеха не открывается). */
  user?: string;
  project: string;
  index: string;
  document: Record<string, unknown> | null | undefined;
  value: T;
  apply: (value: T) => void;
  delay?: number;
}) {
  const key = user ? draftKey(user, project, index) : "";
  const [status, setStatus] = useState<DraftStatus>({ kind: "idle" });
  const [restored, setRestored] = useState<{ savedAt: string } | null>(null);
  // known — что лежит в хранилище от этой вкладки: ключ, ревизия, состояние формы (JSON); пока key ≠ known.key, форма
  // ещё не открыта для этой КБ и правки не пишутся.
  const known = useRef({ key: "", rev: 0, json: "", seed: null as T | null, conflict: false });
  const armed = useRef(false);
  const pending = useRef<{ key: string; json: string; value: T; timer: ReturnType<typeof setTimeout> } | null>(null);
  const live = useRef({ value, document, apply });
  live.current = { value, document, apply };

  function write(job: { key: string; json: string; value: T }) {
    const state = known.current;
    if (state.key !== job.key || state.conflict) return;
    try {
      const current = read<T>(job.key);
      if (current && current.rev > state.rev) {
        state.conflict = true;
        setStatus({ kind: "error", text: `Черновик изменён в другой вкладке в ${clockText(current.savedAt)} — оставлен он, правки здесь не сохранены. Откройте генератор заново.` });
        return;
      }
      const record: Stored<T> = { v: FORMAT, rev: state.rev + 1, savedAt: new Date().toISOString(), built: buildMark(live.current.document), value: job.value };
      window.localStorage.setItem(job.key, JSON.stringify(record));
      state.rev = record.rev; state.json = job.json;
      setStatus({ kind: "saved", at: record.savedAt });
    } catch {
      setStatus({ kind: "error", text: "Черновик не сохранён: хранилище браузера недоступно." });
    }
  }

  function flush() {
    const job = pending.current;
    if (!job) return;
    clearTimeout(job.timer);
    pending.current = null;
    write(job);
  }

  function cancel() {
    if (pending.current) clearTimeout(pending.current.timer);
    pending.current = null;
  }

  /** Форма засеяна для этой КБ: вернуть черновик, сделанный поверх текущей сборки, или засев как есть. */
  function open(seed: T): T {
    flush();
    armed.current = false;
    known.current = { key, rev: 0, json: JSON.stringify(seed), seed, conflict: false };
    setStatus({ kind: "idle" });
    setRestored(null);
    if (!key) return seed;
    const stored = read<T>(key);
    if (!stored) return seed;
    if (stored.built !== buildMark(live.current.document)) { remove(key); return seed; }
    const value = mergeSeed(seed, stored.value);
    known.current = { key, rev: stored.rev, json: JSON.stringify(value), seed, conflict: false };
    setRestored({ savedAt: stored.savedAt });
    return value;
  }

  /** Сборка прошла: черновик больше не нужен, форма остаётся как есть. */
  function clear() {
    cancel();
    if (known.current.key) remove(known.current.key);
    known.current = { ...known.current, rev: 0, json: JSON.stringify(live.current.value), conflict: false };
    armed.current = false;
    setRestored(null);
    setStatus({ kind: "idle" });
  }

  /** «Отбросить»: черновик удаляется, форма возвращается к засеву. */
  function discard() {
    cancel();
    const seed = known.current.seed;
    if (known.current.key) remove(known.current.key);
    known.current = { ...known.current, rev: 0, json: seed === null ? "" : JSON.stringify(seed), conflict: false };
    armed.current = false;
    setRestored(null);
    setStatus({ kind: "idle" });
    if (seed !== null) live.current.apply(seed);
  }

  useEffect(() => {
    const state = known.current;
    if (!key || state.key !== key || state.conflict) return;
    const json = JSON.stringify(value);
    // До первой правки пользователя изменения формы — засев и автоподбор: они становятся точкой отсчёта.
    if (!armed.current) { state.json = json; return; }
    if (json === state.json) { if (pending.current) { cancel(); setStatus({ kind: "idle" }); } return; }
    cancel();
    const job = { key, json, value };
    pending.current = { ...job, timer: setTimeout(() => { pending.current = null; write(job); }, delay) };
    setStatus({ kind: "pending" });
  }, [value, key]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const hidden = () => { if (window.document.visibilityState === "hidden") flush(); };
    window.addEventListener("pagehide", flush);
    window.addEventListener("beforeunload", flush);
    window.document.addEventListener("visibilitychange", hidden);
    return () => {
      window.removeEventListener("pagehide", flush);
      window.removeEventListener("beforeunload", flush);
      window.document.removeEventListener("visibilitychange", hidden);
      flush();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const touch = () => { armed.current = true; };
  /** На корень формы: любое действие пользователя внутри неё — правка, которую стоит сохранять. */
  const bind = { onPointerDownCapture: touch, onClickCapture: touch, onKeyDownCapture: touch, onInputCapture: touch, onChangeCapture: touch };
  return { open, clear, discard, bind, status, restored };
}

/** Строка у кнопки «Создать»: восстановленный черновик с «Отбросить» и состояние автосохранения. */
export function GeneratorDraftNote({ draft }: { draft: Pick<ReturnType<typeof useGeneratorDraft>, "status" | "restored" | "discard"> }) {
  const { status, restored } = draft;
  const text = status.kind === "pending" ? "Сохраняю черновик…"
    : status.kind === "saved" ? `Черновик сохранён ${clockText(status.at)}`
      : status.kind === "error" ? status.text : "";
  if (!restored && !text) return null;
  return <div className="gen-draft" role="status">
    {restored && <span>Восстановлен несохранённый черновик от {stampText(restored.savedAt)} · <button type="button" className="link-button" onClick={draft.discard}>Отбросить</button></span>}
    {text && <span className={status.kind === "error" ? "tone-danger" : undefined}>{text}</span>}
  </div>;
}
