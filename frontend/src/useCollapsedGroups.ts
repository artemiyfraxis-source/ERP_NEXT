import { useMemo, useState } from "react";

/** Local presentation preferences, scoped to the authenticated viewer and surface. */
export function useCollapsedGroups(storageKey: string | null) {
  const saved = useMemo<ReadonlySet<string>>(() => {
    try {
      const value: unknown = storageKey ? JSON.parse(window.localStorage.getItem(storageKey) ?? "[]") : [];
      return new Set(Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []);
    } catch {
      return new Set();
    }
  }, [storageKey]);
  const [state, setState] = useState({ key: storageKey, collapsed: saved });
  const collapsed = state.key === storageKey ? state.collapsed : saved;

  function toggle(name: string) {
    const next = new Set(collapsed);
    if (!next.delete(name)) next.add(name);
    setState({ key: storageKey, collapsed: next });
    try {
      if (storageKey) window.localStorage.setItem(storageKey, JSON.stringify([...next]));
    } catch {
      // Keep the control usable when browser storage is unavailable.
    }
  }

  return { collapsed, toggle };
}
