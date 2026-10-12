import { useEffect, useRef, useState } from "react";
import { ApiError, generatorDraftApi, type GeneratorDraftRecord } from "./api";

/*
 * Черновик генератора ТК (владелец 10.10: «ввёл данные и переключился — всё слетело»; 11.10 — и на другом компьютере).
 * Форма генератора живёт в состоянии компонента, а вкладки карточки проекта и смена КБ его размонтируют. Рабочее
 * состояние формы пишется в хранилище браузера за пользователем, проектом и КБ: через секунду после последней правки и
 * сразу при уходе (размонтирование, скрытие вкладки, закрытие страницы). Каждая запись несёт номер ревизии: другая
 * вкладка, записавшая более новый черновик, не перезаписывается. Пишутся только правки пользователя: засев формы и
 * автоподбор шаблонов черновиком не считаются.
 *
 * Сервер — второй слой: через пару секунд после записи в браузер черновик уходит на сервер (свой у каждого человека)
 * поверх версии, которую этот компьютер видел последней; при уходе — сразу. При открытии берётся более новый из двух:
 * копия этого компьютера или серверная (по версии, а если менялись оба — по времени). Сервер недоступен — черновик
 * остаётся в браузере и уходит на сервер со следующей правкой или открытием. Черновик, сделанный не поверх текущей
 * сборки ЭТК, не открывается. «Создать» и «Отбросить» убирают черновик и здесь, и на сервере.
 */

const PREFIX = "erp-tcgen-draft";
const FORMAT = 1;
export const OFFLINE_TEXT = "Черновик сохранён на этом компьютере (сервер недоступен)";

/** server — версия серверного черновика, поверх которой сделана запись (0 — на сервере его не было); synced — запись
 *  уже на сервере. У записей до 11.10 этих полей нет: они считаются ещё не отправленными. */
type Stored<T> = { v: number; rev: number; savedAt: string; built: string; value: T; server?: number; synced?: boolean };

export type DraftServer = {
  get: (project: string, index: string) => Promise<{ draft: GeneratorDraftRecord | null }>;
  save: (project: string, index: string, body: { payload: unknown; build_fingerprint: string; base_version: number }, keepalive?: boolean) => Promise<GeneratorDraftRecord>;
  remove: (project: string, index: string) => Promise<unknown>;
};

export type DraftStatus =
  | { kind: "idle" }
  | { kind: "pending" }
  | { kind: "saved"; at: string }
  /** Сохранён только в этом браузере (сервер недоступен или отказал). */
  | { kind: "local"; text: string }
  /** Выбор при открытии между этим компьютером и сервером. */
  | { kind: "note"; text: string }
  | { kind: "error"; text: string };

type Target = { key: string; project: string; index: string };

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

function store<T>(key: string, record: Stored<T>) {
  try { window.localStorage.setItem(key, JSON.stringify(record)); } catch { /* хранилище недоступно — останется серверная копия */ }
}

function remove(key: string) {
  try { window.localStorage.removeItem(key); } catch { /* хранилище недоступно — удалять нечего */ }
}

const isPlain = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const isRecord = (value: unknown): value is GeneratorDraftRecord =>
  isPlain(value) && typeof value.version === "number" && typeof value.updated_at === "string" && isPlain(value.payload);

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

const serverConflictText = (at: string | null | undefined) =>
  `Черновик изменён на другом устройстве${at ? ` в ${clockText(at)}` : ""} — на сервере оставлен он, правки здесь сохраняются только на этом компьютере. Откройте генератор заново: откроется более новый.`;

export function useGeneratorDraft<T>({ user, project, index, document, value, apply, delay = 1000, server = generatorDraftApi, serverDelay = 2000 }: {
  /** Нет пользователя — черновик не ведётся (чужой черновик на общем компьютере цеха не открывается). */
  user?: string;
  project: string;
  index: string;
  document: Record<string, unknown> | null | undefined;
  value: T;
  apply: (value: T) => void;
  delay?: number;
  /** Серверный слой; null — черновик только в браузере. */
  server?: DraftServer | null;
  /** Пауза после записи в браузер до отправки на сервер. */
  serverDelay?: number;
}) {
  const key = user ? draftKey(user, project, index) : "";
  const [status, setStatus] = useState<DraftStatus>({ kind: "idle" });
  const [restored, setRestored] = useState<{ savedAt: string } | null>(null);
  // known — что лежит в хранилище от этой вкладки: ключ, ревизия, состояние формы (JSON); пока key ≠ known.key, форма
  // ещё не открыта для этой КБ и правки не пишутся. base — версия серверного черновика, известная этой вкладке;
  // conflict — другая вкладка записала новее; blocked — сервер держит черновик новее (с другого устройства);
  // offline — последний обмен с сервером не удался; epoch — номер открытия (ответ на старое открытие не применяется).
  const known = useRef({
    key: "", project: "", index: "", rev: 0, json: "", seed: null as T | null, base: 0,
    conflict: false, blocked: false, offline: false, epoch: 0,
  });
  const armed = useRef(false);
  const pending = useRef<{ key: string; json: string; value: T; timer: ReturnType<typeof setTimeout> } | null>(null);
  const pushTimer = useRef<{ target: Target; timer: ReturnType<typeof setTimeout> } | null>(null);
  // Одна отправка за раз: следующая ждёт ответа на предыдущую (версия, поверх которой писать, приходит в ответе).
  const inflight = useRef<{ done: Promise<void>; next: { target: Target; keepalive: boolean } | null } | null>(null);
  // Отправленное без ответа (связь оборвалась), по ключу черновика.
  const lost = useRef(new Map<string, string>());
  const live = useRef({ value, document, apply, server });
  live.current = { value, document, apply, server };

  const here = (target: Target) => known.current.key === target.key;
  const quiet = () => known.current.conflict || known.current.blocked;

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
      const record: Stored<T> = {
        v: FORMAT, rev: state.rev + 1, savedAt: new Date().toISOString(), built: buildMark(live.current.document), value: job.value,
        server: state.base, synced: false,
      };
      window.localStorage.setItem(job.key, JSON.stringify(record));
      state.rev = record.rev; state.json = job.json;
      if (!state.blocked) setStatus(state.offline ? { kind: "local", text: OFFLINE_TEXT } : { kind: "saved", at: record.savedAt });
      schedulePush({ key: state.key, project: state.project, index: state.index });
    } catch {
      setStatus({ kind: "error", text: "Черновик не сохранён: хранилище браузера недоступно." });
    }
  }

  function flush(keepalive = false) {
    const job = pending.current;
    if (job) {
      clearTimeout(job.timer);
      pending.current = null;
      write(job);
    }
    const queued = pushTimer.current;
    if (queued) {
      clearTimeout(queued.timer);
      pushTimer.current = null;
      push(queued.target, keepalive);
    }
  }

  function cancel() {
    if (pending.current) clearTimeout(pending.current.timer);
    pending.current = null;
    if (pushTimer.current) clearTimeout(pushTimer.current.timer);
    pushTimer.current = null;
  }

  function schedulePush(target: Target) {
    if (!live.current.server) return;
    if (pushTimer.current) clearTimeout(pushTimer.current.timer);
    pushTimer.current = { target, timer: setTimeout(() => { pushTimer.current = null; push(target); }, serverDelay) };
  }

  /** Отправить на сервер то, что лежит в браузере за этой КБ, если оно ещё не там. */
  function push(target: Target, keepalive = false) {
    const api = live.current.server;
    if (!api) return;
    if (inflight.current) { inflight.current.next = { target, keepalive }; return; }
    const record = read<T>(target.key);
    if (!record || record.synced || (here(target) && quiet())) return;
    const sent = { rev: record.rev, json: JSON.stringify(record.value) };
    const slot: { done: Promise<void>; next: { target: Target; keepalive: boolean } | null } = { done: Promise.resolve(), next: null };
    inflight.current = slot;
    slot.done = api.save(target.project, target.index, { payload: record.value, build_fingerprint: record.built, base_version: record.server ?? 0 }, keepalive)
      .then((saved) => {
        if (!isRecord(saved)) throw new Error("bad answer");
        adopt(target, saved, sent.rev);
      })
      .catch((reason) => refused(target, reason, sent))
      .finally(() => {
        inflight.current = null;
        if (slot.next) push(slot.next.target, slot.next.keepalive);
      });
  }

  /** Сервер принял черновик: запись в браузере помнит его версию (и что она уже там, если правок с тех пор не было). */
  function adopt(target: Target, saved: GeneratorDraftRecord, rev: number) {
    lost.current.delete(target.key);
    const current = read<T>(target.key);
    if (current) store(target.key, { ...current, server: saved.version, synced: current.rev === rev });
    if (!here(target)) return;
    const state = known.current;
    state.base = saved.version; state.offline = false;
    // Идущая правка и выбор при открытии остаются на виду; иначе — время сохранения.
    if (!quiet()) setStatus((now) => (now.kind === "pending" || now.kind === "note" ? now : { kind: "saved", at: current?.savedAt ?? saved.updated_at }));
    if (current && current.rev !== rev) schedulePush(target);
  }

  async function refused(target: Target, reason: unknown, sent: { rev: number; json: string }) {
    const api = live.current.server;
    if (reason instanceof ApiError && reason.status === 409 && api) {
      // Свой же прошлый ответ мог не дойти, а черновик — исчезнуть между чтением и записью: смотрим, что на сервере.
      let draft: GeneratorDraftRecord | null = null;
      try {
        const answer = await api.get(target.project, target.index);
        draft = isRecord(answer?.draft) ? answer.draft : null;
      } catch {
        if (here(target)) { known.current.offline = true; if (!quiet()) setStatus({ kind: "local", text: OFFLINE_TEXT }); }
        return;
      }
      const current = read<T>(target.key);
      const held = draft ? JSON.stringify(draft.payload) : "";
      const sameAsSent = !!draft && held === sent.json;
      if (!draft || sameAsSent || held === lost.current.get(target.key)) {
        lost.current.delete(target.key);
        const version = draft ? draft.version : 0;
        const synced = sameAsSent && current?.rev === sent.rev;
        if (current) store(target.key, { ...current, server: version, synced });
        // Другая КБ уже открыта — её черновик уйдёт на сервер при следующем открытии.
        if (here(target)) { known.current.base = version; if (current && !synced) schedulePush(target); }
        return;
      }
      if (here(target)) {
        known.current.blocked = true;
        setStatus({ kind: "error", text: serverConflictText(draft.updated_at) });
      }
      return;
    }
    // Запись без ответа могла дойти: если сервер потом скажет «изменён», а там именно она — это не чужая правка.
    if (!(reason instanceof ApiError)) lost.current.set(target.key, sent.json);
    if (!here(target)) return;
    known.current.offline = true;
    if (quiet()) return;
    const text = reason instanceof ApiError && reason.status === 413 ? reason.message : OFFLINE_TEXT;
    setStatus({ kind: "local", text });
  }

  /** Форма засеяна для этой КБ: вернуть черновик, сделанный поверх текущей сборки, или засев как есть. Серверная копия
   *  сверяется следом: более новая заменяет форму, пока пользователь её не трогал. */
  function open(seed: T): T {
    flush();
    armed.current = false;
    const epoch = known.current.epoch + 1;
    known.current = {
      key, project, index, rev: 0, json: JSON.stringify(seed), seed, base: 0,
      conflict: false, blocked: false, offline: false, epoch,
    };
    setStatus({ kind: "idle" });
    setRestored(null);
    if (!key) return seed;
    let result = seed;
    let stored = read<T>(key);
    if (stored && stored.built !== buildMark(live.current.document)) { remove(key); stored = null; }
    if (stored) {
      result = mergeSeed(seed, stored.value);
      Object.assign(known.current, { rev: stored.rev, json: JSON.stringify(result), base: stored.server ?? 0 });
      setRestored({ savedAt: stored.savedAt });
    }
    reconcile({ key, project, index }, epoch);
    return result;
  }

  function reconcile(target: Target, epoch: number) {
    const api = live.current.server;
    if (!api) return;
    api.get(target.project, target.index).then((answer) => {
      const state = known.current;
      if (state.epoch !== epoch || state.conflict) return;
      const mark = buildMark(live.current.document);
      const seen = isRecord(answer?.draft) ? answer.draft : null;
      // Серверный черновик поверх другой сборки ЭТК не открывается; его версия — та, поверх которой писать.
      const remote = seen && seen.build_fingerprint === mark ? seen : null;
      const local = read<T>(target.key);
      if (!remote) {
        if (seen) {
          state.base = seen.version;
          if (local && !local.synced) { store(target.key, { ...local, server: seen.version }); push(target); }
          return;
        }
        if (!local) return;
        if (local.synced && (local.server ?? 0) > 0 && !armed.current) {
          // Был на сервере и исчез: собран или отброшен на другом устройстве — здесь он тоже не нужен.
          remove(target.key);
          Object.assign(state, { rev: 0, base: 0 });
          if (state.seed !== null) { state.json = JSON.stringify(state.seed); live.current.apply(state.seed); }
          setRestored(null);
          return;
        }
        state.base = 0;
        if (local.server) store(target.key, { ...local, server: 0, synced: false });
        if (!local.synced) push(target);
        return;
      }
      if (local && (local.server ?? 0) === remote.version) {
        state.base = remote.version;
        if (!local.synced) push(target);
        return;
      }
      // Сервер ушёл вперёд с тех пор, как этот компьютер видел его (или здесь копии нет).
      const localNewer = !!local && !local.synced && Date.parse(local.savedAt) > Date.parse(remote.updated_at);
      if (armed.current || localNewer) {
        // Форма здесь новее (или её уже правят): она уходит на сервер поверх только что прочитанной версии.
        state.base = remote.version;
        if (local) store(target.key, { ...local, server: remote.version, synced: false });
        if (localNewer) setStatus({ kind: "note", text: `Черновик на другом устройстве (${clockText(remote.updated_at)}) старее этого — оставлен этот.` });
        if (local) push(target);
        return;
      }
      if (local && !local.synced) {
        setStatus({ kind: "note", text: `Черновик изменён на другом устройстве в ${clockText(remote.updated_at)} — открыт он; правки этого компьютера от ${clockText(local.savedAt)} старее.` });
      }
      const seed = state.seed as T;
      const value = mergeSeed(seed, remote.payload as T);
      const record: Stored<T> = {
        v: FORMAT, rev: Math.max(local?.rev ?? 0, state.rev) + 1, savedAt: remote.updated_at, built: mark, value,
        server: remote.version, synced: true,
      };
      store(target.key, record);
      Object.assign(state, { rev: record.rev, base: remote.version, json: JSON.stringify(value) });
      live.current.apply(value);
      setRestored({ savedAt: remote.updated_at });
    }).catch(() => {
      const state = known.current;
      if (state.epoch !== epoch) return;
      state.offline = true;
      const local = read<T>(target.key);
      if (local && !local.synced && !quiet()) setStatus({ kind: "local", text: OFFLINE_TEXT });
    });
  }

  /** Убрать черновик и здесь, и на сервере (после отправки, которая уже в пути, — иначе она бы его вернула). */
  function drop() {
    cancel();
    const state = known.current;
    const api = live.current.server;
    if (state.key) {
      remove(state.key);
      if (api) {
        const target = { project: state.project, index: state.index };
        const before = inflight.current?.done ?? Promise.resolve();
        void before.then(() => api.remove(target.project, target.index)).catch(() => { /* останется на сервере до следующего открытия */ });
      }
    }
    armed.current = false;
    setRestored(null);
    setStatus({ kind: "idle" });
  }

  /** Сборка прошла: черновик больше не нужен, форма остаётся как есть. */
  function clear() {
    drop();
    Object.assign(known.current, { rev: 0, base: 0, json: JSON.stringify(live.current.value), conflict: false, blocked: false });
  }

  /** «Отбросить»: черновик удаляется, форма возвращается к засеву. */
  function discard() {
    drop();
    const seed = known.current.seed;
    Object.assign(known.current, { rev: 0, base: 0, json: seed === null ? "" : JSON.stringify(seed), conflict: false, blocked: false });
    if (seed !== null) live.current.apply(seed);
  }

  useEffect(() => {
    const state = known.current;
    if (!key || state.key !== key || state.conflict) return;
    const json = JSON.stringify(value);
    // До первой правки пользователя изменения формы — засев и автоподбор: они становятся точкой отсчёта.
    if (!armed.current) { state.json = json; return; }
    if (json === state.json) {
      if (pending.current) { clearTimeout(pending.current.timer); pending.current = null; setStatus({ kind: "idle" }); }
      return;
    }
    if (pending.current) clearTimeout(pending.current.timer);
    const job = { key, json, value };
    pending.current = { ...job, timer: setTimeout(() => { pending.current = null; write(job); }, delay) };
    if (!state.blocked) setStatus({ kind: "pending" });
  }, [value, key]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const leave = () => flush(true);
    const hidden = () => { if (window.document.visibilityState === "hidden") flush(); };
    window.addEventListener("pagehide", leave);
    window.addEventListener("beforeunload", leave);
    window.document.addEventListener("visibilitychange", hidden);
    return () => {
      window.removeEventListener("pagehide", leave);
      window.removeEventListener("beforeunload", leave);
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
      : status.kind === "local" || status.kind === "note" || status.kind === "error" ? status.text : "";
  if (!restored && !text) return null;
  const tone = status.kind === "error" ? "tone-danger" : status.kind === "local" ? "tone-warn" : undefined;
  return <div className="gen-draft" role="status">
    {restored && <span>Восстановлен несохранённый черновик от {stampText(restored.savedAt)} · <button type="button" className="link-button" onClick={draft.discard}>Отбросить</button></span>}
    {text && <span className={tone}>{text}</span>}
  </div>;
}
