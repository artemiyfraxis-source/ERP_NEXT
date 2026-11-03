import { identityApi } from "./api";

/* Личные настройки экранов (владелец 26.10.2026: «настройка показываемых столбцов у каждого своя»): какие столбцы,
   в каком порядке, ширины — у каждого свои и хранятся на сервере, так что идут за человеком с компьютера на компьютер.
   Браузер держит свою копию (localStorage) — без связи с сервером экран открывается по ней. Прежняя настройка этого
   браузера один раз переезжает на сервер, если там для вида ещё ничего нет. Общие наборы «Базы данных»
   (ColumnViews.tsx) — отдельная вещь, их это не касается. */

type Views = Record<string, unknown>;

let cache: Views | null = null;
let loading: Promise<Views | null> | null = null;
// undefined keeps standalone/legacy callers working; null suspends requests while auth cookies change.
let user: string | null | undefined;
let epoch = 0;
/** view → the JSON the server holds (as far as this page knows): an identical layout is not sent again. */
const onServer = new Map<string, string>();
const timers = new Map<string, ReturnType<typeof setTimeout>>();
const writes = new Map<string, Promise<void>>();
const changed = new Set<string>();
const MIGRATED_KEY = "erp-personal-views:migrated";
export const SAVE_DELAY = 600;

/** Bind settings to the authenticated account before rendering its screens or changing its session. */
export function setPersonalViewsUser(next: string | null) {
  if (user === next) return;
  clearPersonalViews();
  user = next;
}

const allowed = (expected?: string) => user !== null && (expected === undefined || expected === user);
const writeKey = (view: string) => JSON.stringify([user, view]);

/** Keep writes to one account/view in order; a queued operation must recheck auth before using its cookie. */
function writeInOrder(view: string, started: number, operation: () => Promise<unknown>, saved: () => void) {
  const key = writeKey(view);
  const run = async () => {
    if (started !== epoch || !allowed()) return;
    try {
      await operation();
      if (started === epoch) saved();
    } catch { /* The next edit retries a failed save. */ }
  };
  const previous = writes.get(key);
  const next = previous ? previous.then(run) : run();
  writes.set(key, next);
  void next.then(() => { if (writes.get(key) === next) writes.delete(key); });
}

/** The person's layouts, read once per page; null — the server did not answer (the screen keeps the local copy). */
export function loadPersonalViews(expectedUser?: string): Promise<Views | null> {
  if (!allowed(expectedUser)) return Promise.resolve(null);
  if (!loading) {
    const started = epoch;
    loading = identityApi.uiSettings().then((answer) => {
      if (started !== epoch) return null;
      const views = answer?.views && typeof answer.views === "object" ? answer.views : {};
      const restored = { ...views };
      for (const view of changed) {
        if (cache && view in cache) restored[view] = cache[view];
        else delete restored[view];
      }
      cache = restored;
      for (const [view, value] of Object.entries(views)) {
        if (!changed.has(view)) onServer.set(view, JSON.stringify(value));
      }
      return cache;
    }, () => {
      if (started === epoch) loading = null; // the next screen asks again
      return null;
    });
  }
  return loading;
}

/** Saves the layout of a view after a short pause (several clicks in «Колонки» — one request). Fire and forget: a failed
 *  write leaves the local copy, which the next change sends again. */
export function savePersonalView(view: string, value: object, delay = SAVE_DELAY, expectedUser?: string) {
  if (!allowed(expectedUser)) return;
  const started = epoch;
  const text = JSON.stringify(value);
  changed.add(view);
  (cache ??= {})[view] = value;
  const pending = timers.get(view);
  if (pending !== undefined) clearTimeout(pending);
  timers.delete(view);
  if (onServer.get(view) === text && !writes.has(writeKey(view))) return;
  timers.set(view, setTimeout(() => {
    if (started !== epoch || !allowed(expectedUser)) return;
    timers.delete(view);
    writeInOrder(view, started, () => identityApi.saveUiSetting(view, value), () => { onServer.set(view, text); });
  }, delay));
}

/** «По умолчанию»: the person's layout of the view is dropped on the server. */
export function resetPersonalView(view: string, expectedUser?: string) {
  if (!allowed(expectedUser)) return;
  const started = epoch;
  changed.add(view);
  const pending = timers.get(view);
  if (pending !== undefined) clearTimeout(pending);
  timers.delete(view);
  if (cache) delete cache[view];
  onServer.delete(view);
  writeInOrder(view, started, () => identityApi.resetUiSetting(view), () => { onServer.delete(view); });
}

function migrated(): string[] {
  try {
    const value = JSON.parse(window.localStorage.getItem(MIGRATED_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

/** The layout this browser kept before the server store goes to the server — once per view and browser, and only when
 *  the server has none for the view (the server's layout wins). */
export function migrateLocalView(view: string, local: object | null | undefined) {
  if (!allowed()) return;
  const done = migrated();
  if (done.includes(view)) return;
  try {
    window.localStorage.setItem(MIGRATED_KEY, JSON.stringify([...done, view]));
  } catch { /* storage unavailable: nothing to migrate either */ }
  if (local && Object.keys(local).length && !(cache && view in cache)) savePersonalView(view, local, 0);
}

function clearPersonalViews() {
  epoch += 1;
  user = undefined;
  cache = null;
  loading = null;
  onServer.clear();
  changed.clear();
  timers.forEach((timer) => clearTimeout(timer));
  timers.clear();
}

/** Tests: a fresh page, including any in-flight write chains from the previous test. */
export function forgetPersonalViews() {
  clearPersonalViews();
  writes.clear();
}
