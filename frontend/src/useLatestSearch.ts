import { useEffect, useRef, useState } from "react";

export type LatestSearch<T> = { data: T | null; loading: boolean; error: string };

/**
 * Search-as-you-type with a debounce and a generation guard: only the answer to the LAST request is applied.
 * A slow answer to an earlier query (A→B→A) never overwrites the answer of the current one; changing the deps
 * or unmounting invalidates everything still in flight. `loading` is true from the change until the answer to
 * the current query arrives, so callers can hold actions on rows that belong to an older query.
 * `data` keeps the last applied answer while the next one loads (no flicker); a failed load keeps it too.
 */
export function useLatestSearch<T>(
  load: () => Promise<T>,
  deps: readonly unknown[],
  { delay = 250, fallback = "Не удалось загрузить" }: { delay?: number; fallback?: string } = {},
): LatestSearch<T> {
  const generation = useRef(0);
  const loader = useRef(load);
  loader.current = load;
  const [state, setState] = useState<LatestSearch<T>>({ data: null, loading: true, error: "" });
  useEffect(() => {
    const mine = ++generation.current;
    setState((current) => ({ ...current, loading: true, error: "" }));
    const timer = window.setTimeout(() => {
      loader.current().then(
        (data) => { if (mine === generation.current) setState({ data, loading: false, error: "" }); },
        (reason) => { if (mine === generation.current) setState((current) => ({ ...current, loading: false, error: reason instanceof Error && reason.message ? reason.message : fallback })); },
      );
    }, delay);
    return () => { window.clearTimeout(timer); generation.current += 1; };
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return state;
}
