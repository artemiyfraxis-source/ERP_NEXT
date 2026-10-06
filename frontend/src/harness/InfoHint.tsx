import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";

/** Coordinates belong to the viewport, never to a clipping/stacking ancestor. */
export function infoHintPosition(anchor: { left: number; top: number; bottom: number }, size: { width: number; height: number }, viewport: { width: number; height: number }) {
  const margin = 8, gap = 6;
  const below = Math.max(0, viewport.height - anchor.bottom - gap - margin);
  const above = Math.max(0, anchor.top - gap - margin);
  const placeBelow = size.height <= below || below >= above;
  const maxHeight = Math.max(1, placeBelow ? below : above);
  return {
    left: Math.max(margin, Math.min(anchor.left, viewport.width - size.width - margin)),
    top: Math.max(margin, placeBelow ? anchor.bottom + gap : anchor.top - gap - Math.min(size.height, maxHeight)),
    maxHeight,
  };
}

export function InfoHint({ children }: { readonly children: ReactNode }) {
  const id = useId();
  const root = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const tooltip = useRef<HTMLSpanElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const cancelClose = () => clearTimeout(closeTimer.current);
  const show = () => { cancelClose(); setOpen(true); };
  const leave = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => {
      if (!root.current?.contains(document.activeElement)) setOpen(false);
    }, 120);
  };

  useLayoutEffect(() => () => clearTimeout(closeTimer.current), []);
  useLayoutEffect(() => {
    const tip = tooltip.current, trigger = button.current;
    if (!open || !tip || !trigger) return;
    // Native top layer escapes overflow, transforms, isolation, and modal dialogs.
    // A larger z-index within the original container cannot provide this guarantee.
    document.dispatchEvent(new CustomEvent("techmap:hint-open", { detail: id }));
    tip.showPopover();
    const position = () => {
      const anchor = trigger.getBoundingClientRect();
      if (!trigger.getClientRects().length || anchor.bottom < 0 || anchor.top > innerHeight) { setOpen(false); return; }
      tip.style.maxHeight = `${Math.max(1, innerHeight - 16)}px`;
      const next = infoHintPosition(anchor, tip.getBoundingClientRect(), { width: innerWidth, height: innerHeight });
      tip.style.left = `${next.left}px`;
      tip.style.top = `${next.top}px`;
      tip.style.maxHeight = `${next.maxHeight}px`;
    };
    const outside = (event: Event) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); setOpen(false); } };
    const otherHint = (event: Event) => { if ((event as CustomEvent<string>).detail !== id) setOpen(false); };
    position();
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    document.addEventListener("pointerdown", outside);
    document.addEventListener("focusin", outside);
    document.addEventListener("keydown", escape, true);
    document.addEventListener("techmap:hint-open", otherHint);
    return () => {
      if (tip.matches(":popover-open")) tip.hidePopover();
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("focusin", outside);
      document.removeEventListener("keydown", escape, true);
      document.removeEventListener("techmap:hint-open", otherHint);
    };
  }, [open, id, children]);

  return <span className="info-hint" ref={root} onPointerEnter={show} onPointerLeave={leave}>
    <button ref={button} type="button" aria-label="Справка" aria-describedby={id}
      onFocus={show} onBlur={leave} onClick={event => { event.stopPropagation(); show(); }}>i</button>
    <span ref={tooltip} role="tooltip" id={id} popover="manual" className="info-hint-popup"
      onPointerEnter={cancelClose} onPointerLeave={leave}>{children}</span>
  </span>;
}
