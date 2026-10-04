import { useEffect, useRef, useState, type MouseEvent } from "react";
import { createPortal } from "react-dom";

export type MenuItem = { label: string; title?: string; disabled?: boolean; run: () => void };

const MENU_ITEM_HEIGHT = 30;

/** Меню «⋯» строки. Список вынесен в body и позиционируется по кнопке (fixed): ни прокрутка таблицы, ни её
 *  stacking context (раскрытая строка, залипающая шапка соседней таблицы) его не обрезают и не перекрывают. */
export function RowMenu({ label, items, disabled }: { label: string; items: MenuItem[]; disabled?: boolean }) {
  const [place, setPlace] = useState<{ top?: number; bottom?: number; right: number } | null>(null);
  const root = useRef<HTMLSpanElement>(null);
  const popup = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!place) return;
    const away = (event: Event) => {
      const target = event.target as Node;
      if (!root.current?.contains(target) && !popup.current?.contains(target)) setPlace(null);
    };
    const close = () => setPlace(null);
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setPlace(null); };
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
    const height = items.length * MENU_ITEM_HEIGHT + 12;
    const right = Math.max(window.innerWidth - box.right, 4);
    const fitsBelow = window.innerHeight - box.bottom >= height + 8;
    setPlace(fitsBelow || box.top < height ? { top: box.bottom + 2, right } : { bottom: window.innerHeight - box.top + 2, right });
  }

  return (
    <span className="row-menu" ref={root} onClick={(event) => event.stopPropagation()}>
      <button type="button" className="secondary small" aria-label={label} aria-expanded={Boolean(place)} aria-haspopup="menu" disabled={disabled} onClick={toggle}>⋯</button>
      {place && createPortal(
        <div className="row-menu-popup" role="menu" ref={popup} style={{ position: "fixed", ...place }}>
          {items.map((item) => (
            <button key={item.label} type="button" role="menuitem" disabled={item.disabled} title={item.title} onClick={(event) => { event.stopPropagation(); setPlace(null); item.run(); }}>{item.label}</button>
          ))}
        </div>,
        document.body,
      )}
    </span>
  );
}
