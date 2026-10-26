import { identityApi } from "./api";

/* Личные настройки экранов (владелец 26.10.2026: «настройка показываемых столбцов у каждого своя»): какие столбцы,
   в каком порядке, ширины — у каждого свои и хранятся на сервере, так что идут за человеком с компьютера на компьютер.
   Браузер держит свою копию (localStorage) — без связи с сервером экран открывается по ней. Прежняя настройка этого
   браузера один раз переезжает на сервер, если там для вида ещё ничего нет. Общие наборы «Базы данных»
   (ColumnViews.tsx) — отдельная вещь, их это не касается. */

type Views = Record<string, unknown>;

let cache: Views | null = null;
let loading: Promise<Views | null> | null = null;
/** view → the JSON the server holds (as far as this page knows): an identical layout is not sent again. */
const onServer = new Map<string, string>();
const timers = new Map<string, ReturnType<typeof setTimeout>>();
const MIGRATED_KEY = "erp-personal-views:migrated";
export const SAVE_DELAY = 600;

/** The person's layouts, read once per page; null — the server did not answer (the screen keeps the local copy). */
export function loadPersonalViews(): Promise<Views | null> {
  if (!loading) {
    loading = identityApi.uiSettings().then((answer) => {
      const views = answer?.views && typeof answer.views === "object" ? answer.views : {};
      cache = { ...views };
      for (const [view, value] of Object.entries(cache)) onServer.set(view, JSON.stringify(value));
      return cache;
    }, () => {
      loading = null; // the next screen asks again
      return null;
    });
  }
  return loading;
}

/** Saves the layout of a view after a short pause (several clicks in «Колонки» — one request). Fire and forget: a failed
 *  write leaves the local copy, which the next change sends again. */
export function savePersonalView(view: string, value: object, delay = SAVE_DELAY) {
  const text = JSON.stringify(value);
  if (cache) cache[view] = value;
  const pending = timers.get(view);
  if (pending !== undefined) clearTimeout(pending);
  timers.delete(view);
  if (onServer.get(view) === text) return;
  timers.set(view, setTimeout(() => {
    timers.delete(view);
    identityApi.saveUiSetting(view, value).then(() => { onServer.set(view, text); }, () => undefined);
  }, delay));
}

/** «По умолчанию»: the person's layout of the view is dropped on the server. */
export function resetPersonalView(view: string) {
  const pending = timers.get(view);
  if (pending !== undefined) clearTimeout(pending);
  timers.delete(view);
  if (cache) delete cache[view];
  onServer.delete(view);
  identityApi.resetUiSetting(view).catch(() => undefined);
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
  const done = migrated();
  if (done.includes(view)) return;
  try {
    window.localStorage.setItem(MIGRATED_KEY, JSON.stringify([...done, view]));
  } catch { /* storage unavailable: nothing to migrate either */ }
  if (local && Object.keys(local).length && !(cache && view in cache)) savePersonalView(view, local, 0);
}

/** Tests: a fresh page. */
export function forgetPersonalViews() {
  cache = null;
  loading = null;
  onServer.clear();
  timers.forEach((timer) => clearTimeout(timer));
  timers.clear();
}
