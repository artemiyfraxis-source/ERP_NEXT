import { useEffect, useRef, type ReactNode } from "react";

/** «Ещё» в шапке чертежа на телефоне: второстепенные действия уходят в меню, шапка остаётся одной строкой.
 *  Закрывается по Escape, касанию мимо и после выбора пункта (закрытие — в самом пункте). */
export function PhoneMoreMenu({ open, onOpenChange, children }: { open: boolean; onOpenChange: (open: boolean) => void; children: ReactNode }) {
  const root = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) onOpenChange(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onOpenChange(false); };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open, onOpenChange]);
  return <span className="harness-more" ref={root}>
    <button type="button" className="secondary small harness-more-button" aria-haspopup="menu" aria-expanded={open} title="Ещё"
      onClick={() => onOpenChange(!open)}>Ещё</button>
    {open && <div className="harness-more-menu" role="menu" aria-label="Ещё">{children}</div>}
  </span>;
}
