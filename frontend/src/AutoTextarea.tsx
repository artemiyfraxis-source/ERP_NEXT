import { forwardRef, useCallback, useLayoutEffect, useRef, type ChangeEvent, type KeyboardEvent, type TextareaHTMLAttributes } from "react";

type Props = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "rows" | "value" | "onChange"> & {
  value: string;
  onChange: (value: string) => void;
  /** Строк до прокрутки (по умолчанию 6). */
  maxRows?: number;
  /** Enter ставит перенос строки. По умолчанию нет: значение однострочное, текст лишь переносится визуально. */
  multiline?: boolean;
};

/** Поле длинного текста: выглядит как обычный input, стартует с одной строки, растёт по содержимому
 *  до maxRows и дальше прокручивается. Заменяет узкий однострочный input там, где текста много. */
export const AutoTextarea = forwardRef<HTMLTextAreaElement, Props>(function AutoTextarea(
  { value, onChange, maxRows = 6, multiline = false, className, onKeyDown, ...rest }, forwarded,
) {
  const inner = useRef<HTMLTextAreaElement | null>(null);
  const setRef = useCallback((node: HTMLTextAreaElement | null) => {
    inner.current = node;
    if (typeof forwarded === "function") forwarded(node);
    else if (forwarded) forwarded.current = node;
  }, [forwarded]);

  const fit = useCallback(() => {
    const el = inner.current;
    if (!el) return;
    el.style.height = "auto";
    const style = window.getComputedStyle(el);
    const line = parseFloat(style.lineHeight) || 18;
    const border = (parseFloat(style.borderTopWidth) || 0) + (parseFloat(style.borderBottomWidth) || 0);
    const pad = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0) + border;
    const cap = line * maxRows + pad;
    const want = el.scrollHeight + border;
    if (want > 0) el.style.height = `${Math.min(want, cap)}px`;
    el.style.overflowY = want > cap ? "auto" : "hidden";
  }, [maxRows]);

  useLayoutEffect(fit, [fit, value]);
  useLayoutEffect(() => {
    const el = inner.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    let width = el.clientWidth;
    const observer = new ResizeObserver(() => {
      if (el.clientWidth !== width) { width = el.clientWidth; fit(); }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [fit]);

  const handleKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    onKeyDown?.(event);
    if (!multiline && event.key === "Enter" && !event.defaultPrevented) event.preventDefault();
  };
  const handleChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    const next = event.target.value;
    onChange(multiline ? next : next.replace(/\s*[\r\n]+\s*/g, " "));
  };

  return <textarea {...rest} ref={setRef} rows={1} value={value} onChange={handleChange} onKeyDown={handleKey}
    className={className ? `auto-textarea ${className}` : "auto-textarea"} />;
});
