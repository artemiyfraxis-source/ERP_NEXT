import { useEffect, useRef, useState, type MouseEvent } from "react";
import { createPortal } from "react-dom";

export type RowAction = {
  label: string;
  run: () => void;
  disabled?: boolean;
  /** Необратимое действие: красным, ставится последним. */
  danger?: boolean;
};

const ITEM_HEIGHT = 32;

/** Меню строки «⋯»: действия над одной строкой таблицы, когда сама строка кликается и ведёт в главное место
 *  (аудит дизайна 10.10, М-1). Список вынесен в body и позиционируется по кнопке, поэтому прокрутка таблицы его не
 *  обрезает; Esc, клик мимо и прокрутка закрывают; открытое меню ставит фокус на первое действие. */
export function RowActionMenu({ label, items, disabled }: { label: string; items: RowAction[]; disabled?: boolean }) {
  const [place, setPlace] = useState<{ top?: number; bottom?: number; right: number } | null>(null);
  const root = useRef<HTMLSpanElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!place) return undefined;
    popup.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
    const away = (event: Event) => {
      const target = event.target as Node;
      if (!root.current?.contains(target) && !popup.current?.contains(target)) setPlace(null);
    };
    const close = () => setPlace(null);
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setPlace(null);
      opener.current?.focus();
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", escape);
    document.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", escape);
      document.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [place]);

  function toggle(event: MouseEvent<HTMLButtonElement>) {
    event.stopPropagation();
    if (place) { setPlace(null); return; }
    const box = event.currentTarget.getBoundingClientRect();
    const height = items.length * ITEM_HEIGHT + 12;
    const right = Math.max(window.innerWidth - box.right, 4);
    const fitsBelow = window.innerHeight - box.bottom >= height + 8;
    setPlace(fitsBelow || box.top < height ? { top: box.bottom + 2, right } : { bottom: window.innerHeight - box.top + 2, right });
  }

  return <span className="row-action-menu" ref={root} onClick={(event) => event.stopPropagation()}>
    <button type="button" ref={opener} className="row-action-open" aria-label={label} aria-expanded={Boolean(place)} aria-haspopup="menu" disabled={disabled} onClick={toggle}>⋯</button>
    {place && createPortal(
      <div className="row-action-popup" role="menu" ref={popup} style={{ position: "fixed", ...place }}>
        {items.map((item) => <button key={item.label} type="button" role="menuitem" className={item.danger ? "danger" : undefined} disabled={item.disabled}
          onClick={(event) => { event.stopPropagation(); setPlace(null); opener.current?.focus(); item.run(); }}>{item.label}</button>)}
      </div>,
      document.body,
    )}
  </span>;
}
