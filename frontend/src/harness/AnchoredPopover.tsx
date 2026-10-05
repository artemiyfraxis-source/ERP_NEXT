import { useId, useLayoutEffect, useRef, type ReactNode } from "react";
import { infoHintPosition } from "./InfoHint";
import "./anchored-popover.css";

/** A popup anchored to its sibling control, above clipping and transformed panels. */
export function AnchoredPopover({ open, onClose, className, label, role = "dialog", children }: {
  open: boolean;
  onClose: () => void;
  className: string;
  label: string;
  role?: "dialog" | "listbox";
  children: ReactNode;
}) {
  const id = useId();
  const popupRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  useLayoutEffect(() => { closeRef.current = onClose; });
  useLayoutEffect(() => {
    const popup = popupRef.current;
    const parent = popup?.parentElement;
    const anchor = parent?.querySelector<HTMLElement>(":scope > button, :scope > input, :scope > label input");
    if (!open || !popup || !parent || !anchor) return;
    document.dispatchEvent(new CustomEvent("techmap:popover-open", { detail: id }));
    popup.showPopover();
    const preferredMaxHeight = parseFloat(getComputedStyle(popup).maxHeight) || Infinity;
    const position = () => {
      const rect = anchor.getBoundingClientRect();
      if (!anchor.getClientRects().length || rect.bottom < 0 || rect.top > innerHeight || rect.right < 0 || rect.left > innerWidth) {
        closeRef.current(); return;
      }
      popup.style.maxWidth = `${Math.max(1, innerWidth - 16)}px`;
      popup.style.maxHeight = `${Math.min(preferredMaxHeight, Math.max(1, innerHeight - 16))}px`;
      const next = infoHintPosition(rect, popup.getBoundingClientRect(), { width: innerWidth, height: innerHeight });
      popup.style.left = `${next.left}px`;
      popup.style.top = `${next.top}px`;
      popup.style.maxHeight = `${Math.min(preferredMaxHeight, next.maxHeight)}px`;
    };
    const outside = (event: Event) => {
      if (event.target instanceof Node && !parent.contains(event.target)) closeRef.current();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault(); event.stopPropagation();
      anchor.focus({ preventScroll: true }); closeRef.current();
    };
    const otherPopup = (event: Event) => {
      if ((event as CustomEvent<string>).detail !== id) closeRef.current();
    };
    const observer = new ResizeObserver(position);
    observer.observe(popup); observer.observe(anchor);
    position();
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("focusin", outside);
    document.addEventListener("keydown", escape, true);
    document.addEventListener("techmap:popover-open", otherPopup);
    return () => {
      observer.disconnect();
      if (popup.matches(":popover-open")) popup.hidePopover();
      popup.style.maxHeight = "";
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("focusin", outside);
      document.removeEventListener("keydown", escape, true);
      document.removeEventListener("techmap:popover-open", otherPopup);
    };
  }, [open, id]);
  return <div ref={popupRef} popover="manual" className={`anchored-popover ${className}`} role={role} aria-label={label}>{children}</div>;
}
