import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { referencesApi, type SharedColumn } from "./api";

/* Общий набор столбцов таблицы базы (владелец 22.10.2026: «показывать все, но выбрать только нужные»): какие столбцы
   видны всем и в каком порядке. Хранится на сервере, меняет тот, у кого есть правка раздела. Скрытый столбец остаётся
   в записях — генератор ЭТК и склад читают его как раньше. Без провайдера таблица работает как прежде. */

interface ColumnViews {
  views: Record<string, SharedColumn[]>;
  canEdit: boolean;
  save: (view: string, columns: SharedColumn[]) => Promise<void>;
}

const ColumnViewsContext = createContext<ColumnViews | null>(null);

/** `enabled` — у пользователя есть доступ к справочнику; без него общий набор не запрашивается. */
export function ColumnViewsProvider({ children, enabled = true }: { children: ReactNode; enabled?: boolean }) {
  const [views, setViews] = useState<Record<string, SharedColumn[]>>({});
  const [canEdit, setCanEdit] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    referencesApi.columnViews().then((result) => {
      if (!alive) return;
      setViews(result.views && typeof result.views === "object" ? result.views : {});
      setCanEdit(result.can_edit === true);
    }).catch(() => undefined);
    return () => { alive = false; };
  }, [enabled]);
  const save = useCallback(async (view: string, columns: SharedColumn[]) => {
    const saved = await referencesApi.saveColumnView(view, columns);
    setViews((current) => {
      const next = { ...current };
      if (saved.columns.length) next[view] = saved.columns; else delete next[view];
      return next;
    });
  }, []);
  const value = useMemo(() => ({ views, canEdit, save }), [views, canEdit, save]);
  return <ColumnViewsContext.Provider value={value}>{children}</ColumnViewsContext.Provider>;
}

export const useColumnViews = () => useContext(ColumnViewsContext);
