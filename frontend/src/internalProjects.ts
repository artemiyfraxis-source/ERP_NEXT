import { useCallback, useEffect, useState } from "react";
import { internalProjectsApi, type InternalProject } from "./api";

/* One cache of the internal projects «ВП-001» for the whole screen. Every place that
   shows a project reference asks `projectLabel(ref)` — «ВП-001 · Название» for an internal one, the text as written for
   everything else. */

const INTERNAL = /^ВП-\d{3,}$/i;

let cache: InternalProject[] = [];
let loaded = false;
let loading: Promise<void> | null = null;
let generation = 0;
const listeners = new Set<() => void>();

function publish(next: InternalProject[]) {
  cache = next;
  loaded = true;
  listeners.forEach((listener) => listener());
}

/** Reload the list (after a project was created, closed or reopened). */
export function refreshInternalProjects(): Promise<void> {
  const mine = generation;
  loading = internalProjectsApi.list()
    .then((result) => { if (mine === generation) publish(result.projects ?? []); })
    // No access to the list: the references stay as written.
    .catch(() => { if (mine === generation) publish([]); });
  return loading;
}

/** Test hook: forget the cached list. */
export function resetInternalProjects() {
  generation += 1;
  cache = [];
  loaded = false;
  loading = null;
}

/** «ВП-001 · Название» for a known internal project, otherwise the reference as written. */
export function projectLabel(reference: string | null | undefined): string {
  const text = String(reference ?? "");
  if (!INTERNAL.test(text.trim())) return text;
  const found = cache.find((project) => project.number === text.trim().toUpperCase());
  return found ? `${found.number} · ${found.name}` : text;
}

/** The cached list, loaded on first use; re-renders when it changes. */
export function useInternalProjects(): InternalProject[] {
  const [, setTick] = useState(0);
  useEffect(() => {
    const listener = () => setTick((tick) => tick + 1);
    listeners.add(listener);
    if (!loaded && !loading) void refreshInternalProjects();
    return () => { listeners.delete(listener); };
  }, []);
  return cache;
}

/** Label function for components: subscribes to the cache, so the text updates when the list arrives. */
export function useProjectLabel(): (reference: string | null | undefined) => string {
  const list = useInternalProjects();
  // A new identity per loaded list: memoised column sets that capture the function rebuild when the names arrive.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useCallback((reference: string | null | undefined) => projectLabel(reference), [list]);
}
