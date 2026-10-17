import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** Окно поверх страницы (владелец 14.10: «тут все должно окнами открываться»): панель страницы внутри, Esc закрывает.
 *  `dismissable` — закрывать и щелчком мимо окна; у форм с правкой выключено, чтобы промах не стёр набранное. */
export function PageWindow({ label, close, width = 1040, dismissable = false, children }: {
  label: string; close: () => void; width?: number; dismissable?: boolean; children: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { box.current?.focus(); }, []);
  return createPortal(<div className="dialog-overlay" onMouseDown={(event) => { if (dismissable && event.target === event.currentTarget) close(); }}
    onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); close(); } }}>
    <div ref={box} tabIndex={-1} className="page-window" role="dialog" aria-modal="true" aria-label={label} style={{ width: `min(${width}px, 100%)` }}>{children}</div>
  </div>, document.body);
}
