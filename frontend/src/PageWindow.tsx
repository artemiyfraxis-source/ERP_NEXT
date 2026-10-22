import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** Окно поверх страницы (владелец 14.10: «тут все должно окнами открываться»): панель страницы внутри, Esc закрывает.
 *  `dismissable` — закрывать и щелчком мимо окна; у форм с правкой выключено, чтобы промах не стёр набранное. */
export function PageWindow({ label, close, width = 1040, dismissable = false, children }: {
  label: string; close: () => void; width?: number; dismissable?: boolean; children: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  // Закрылось окно поверх другого окна — фокус обратно туда, откуда открыли: Esc закроет и нижнее.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    box.current?.focus();
    return () => { if (opener?.isConnected) opener.focus(); };
  }, []);
  return createPortal(<div className="dialog-overlay" onMouseDown={(event) => { if (dismissable && event.target === event.currentTarget) close(); }}
    onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); close(); } }}>
    <div ref={box} tabIndex={-1} className="page-window" role="dialog" aria-modal="true" aria-label={label} style={{ width: `min(${width}px, 100%)` }}>{children}</div>
  </div>, document.body);
}
