import { createContext, useCallback, useContext, useState, type ReactNode, type SyntheticEvent } from "react";

/* Свёрнутые блоки ЭТК (владелец 13.10: «по дефолту всё свёрнуто и по необходимости открывалось»). Каждый блок —
   схема жгута, требование безопасности, разделы операции, журнал ревизий — закрыт, пока сотрудник его не откроет;
   «Оснастка» и «Ход операции» открыты сразу (владелец 16.10: монтажнику они нужны каждый раз). Что сотрудник открыл
   или закрыл не по умолчанию, запоминается в этом браузере для него (localStorage), одно на все операции и ЭТК. */

/** Блоки, открытые, пока сотрудник их не закрыл. */
const OPEN_BY_DEFAULT: ReadonlySet<string> = new Set(["sec:Оснастка", "sec:Ход операции"]);

const FoldUser = createContext("");

/** Чьи открытые блоки помнить: id сотрудника, вошедшего в ERP. */
export function EtkFoldScope({ user, children }: { user: string; children: ReactNode }) {
  return <FoldUser.Provider value={user}>{children}</FoldUser.Provider>;
}

export const foldKey = (user: string, block: string) => `etk-fold:${user || "-"}:${block}`;

function readFold(key: string, byDefault: boolean): boolean {
  try {
    const stored = window.localStorage.getItem(key);
    return stored === "1" ? true : stored === "0" ? false : byDefault;
  } catch { return byDefault; }
}

function writeFold(key: string, open: boolean, byDefault: boolean) {
  try {
    if (open === byDefault) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, open ? "1" : "0");
  } catch { /* приватное окно или запрет хранилища: блок просто откроется как по умолчанию в следующий раз */ }
}

/** Открыт ли блок и обработчик `onToggle` для его `<details>`. */
export function useEtkFold(block: string): { open: boolean; onToggle: (event: SyntheticEvent<HTMLDetailsElement>) => void } {
  const key = foldKey(useContext(FoldUser), block);
  const byDefault = OPEN_BY_DEFAULT.has(block);
  const [state, setState] = useState(() => ({ key, open: readFold(key, byDefault) }));
  const open = state.key === key ? state.open : readFold(key, byDefault);
  const onToggle = useCallback((event: SyntheticEvent<HTMLDetailsElement>) => {
    const next = event.currentTarget.open;
    setState((current) => (current.key === key && current.open === next ? current : { key, open: next }));
    writeFold(key, next, byDefault);
  }, [key, byDefault]);
  return { open, onToggle };
}

/** `<details>` ЭТК, закрытый по умолчанию и помнящий своё состояние. `lazy` — содержимое строится только открытым
 *  (тяжёлая схема жгута грузится по щелчку). */
export function EtkFold({ block, summary, className, label, lazy = false, children }: {
  block: string; summary: ReactNode; className?: string; label?: string; lazy?: boolean; children: ReactNode;
}) {
  const { open, onToggle } = useEtkFold(block);
  return <details className={className} open={open} onToggle={onToggle} aria-label={label}>
    <summary>{summary}</summary>
    {(!lazy || open) && children}
  </details>;
}
