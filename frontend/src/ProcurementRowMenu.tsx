import { useEffect, useRef, useState, type MouseEvent } from "react";
import { createPortal } from "react-dom";

export type MenuItem = { label: string; /** Короткая надпись на кнопке (узкая колонка действий); полное название — в подсказке и имени кнопки. */ short?: string; title?: string; disabled?: boolean; run: () => void };

const MENU_ITEM_HEIGHT = 30;

/** Меню «⋯» (осталось только в карточке техкарты; «Закупка» его не использует). Список вынесен в body и позиционируется по кнопке (fixed): ни прокрутка таблицы, ни её
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

/** Сколько действий строки видны сразу кнопками; остальные — текстовыми ссылками второй строкой. */
const PRIMARY_ACTIONS = 2;

/** Действия строки — всё на виду, без меню «⋯» (владелец, 05.10: «не нужно вот эти скрытые кнопки»).
 *  Первые два — кнопки, остальные — текстовые ссылки под ними: на десктопе раскрываются при наведении или фокусе
 *  на строке, на планшете (pointer: coarse) видны всегда. Колонка узкая (~150 px): две кнопки в одну строку, у длинных
 *  действий есть `short` (полное название — в подсказке и имени кнопки). Все лежат в DOM и доступны с клавиатуры. */
export function RowActions({ label, items, disabled }: { label: string; items: MenuItem[]; disabled?: boolean }) {
  const primary = items.slice(0, PRIMARY_ACTIONS);
  const rest = items.slice(PRIMARY_ACTIONS);
  return (
    <span className="row-actions" role="group" aria-label={label} onClick={(event) => event.stopPropagation()}>
      <span className="row-actions-main">
        {primary.map((item) => (
          <button key={item.label} type="button" className="secondary small" disabled={disabled || item.disabled} aria-label={item.short ? item.label : undefined} title={item.title ?? (item.short ? item.label : undefined)} onClick={() => item.run()}>{item.short ?? item.label}</button>
        ))}
      </span>
      {rest.length > 0 && (
        <span className="row-actions-more">
          {rest.map((item) => (
            <button key={item.label} type="button" className="link-button" disabled={disabled || item.disabled} title={item.title} onClick={() => item.run()}>{item.label}</button>
          ))}
        </span>
      )}
    </span>
  );
}
