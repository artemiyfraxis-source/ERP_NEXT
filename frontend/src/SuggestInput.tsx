import { useEffect, useId, useLayoutEffect, useRef, useState, type InputHTMLAttributes, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

const LIMIT = 60;

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "list"> & {
  options: readonly string[];
  value: string;
  onChange: (value: string) => void;
  /** Grey second part of an option (e.g. the name next to an article); typed text matches it too. */
  describe?: (option: string) => string;
  /** The list opens only after this many typed characters (long lists like articles). */
  minLength?: number;
};

/** A text field with the ERP's own suggestion list — free text stays allowed. Replaces `<datalist>`, whose popup is
    drawn by the browser (dark in Chrome/Edge) and ignores the app's style. The list is portalled with fixed
    coordinates so a dialog that scrolls (`.move-dialog`) does not clip it. */
export function SuggestInput({ options, value, onChange, describe, minLength = 0, onFocus, onBlur, onKeyDown, ...rest }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [box, setBox] = useState<{ left: number; top: number; width: number; up: boolean } | null>(null);

  const typed = value.trim().toLocaleLowerCase("ru");
  // A value that already is one of the options shows the whole list — the user reopens it to pick another.
  const exact = options.some((option) => option.toLocaleLowerCase("ru") === typed);
  const matches = (option: string) =>
    option.toLocaleLowerCase("ru").includes(typed) || (describe?.(option) ?? "").toLocaleLowerCase("ru").includes(typed);
  const shown = typed.length < minLength ? [] : (typed && !exact ? options.filter(matches) : options).slice(0, LIMIT);
  const visible = open && shown.length > 0;

  useLayoutEffect(() => {
    if (!visible) return;
    const place = () => {
      const rect = inputRef.current?.getBoundingClientRect();
      if (!rect) return;
      const up = rect.bottom + 260 > window.innerHeight && rect.top > 260;
      setBox({ left: rect.left, top: up ? rect.top - 4 : rect.bottom + 4, width: rect.width, up });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [visible]);

  useEffect(() => { setActive(-1); }, [value, open]);

  const pick = (option: string) => {
    onChange(option);
    setOpen(false);
  };

  const keyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    onKeyDown?.(event);
    if (event.defaultPrevented) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) { setOpen(true); return; }
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current) => (current + step + shown.length) % Math.max(shown.length, 1));
    } else if (event.key === "Enter" && visible && active >= 0) {
      event.preventDefault();
      pick(shown[active]);
    } else if (event.key === "Escape" && visible) {
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
    }
  };

  return <>
    <input
      {...rest}
      ref={inputRef}
      role="combobox"
      aria-autocomplete="list"
      aria-expanded={visible}
      aria-controls={visible ? listId : undefined}
      aria-activedescendant={visible && active >= 0 ? `${listId}-${active}` : undefined}
      autoComplete="off"
      value={value}
      onChange={(event) => { onChange(event.target.value); setOpen(true); }}
      onFocus={(event) => { setOpen(true); onFocus?.(event); }}
      onBlur={(event) => { setOpen(false); onBlur?.(event); }}
      onClick={() => setOpen(true)}
      onKeyDown={keyDown}
    />
    {visible && box && createPortal(
      <ul
        id={listId}
        role="listbox"
        aria-label={rest["aria-label"] ? `Варианты: ${rest["aria-label"]}` : "Варианты"}
        className="suggest-list"
        // At least as wide as the field, wider when an option needs it (article + name), never past the window.
        style={{ left: box.left, minWidth: box.width, maxWidth: Math.max(box.width, window.innerWidth - box.left - 16), ...(box.up ? { bottom: window.innerHeight - box.top } : { top: box.top }) }}
      >
        {shown.map((option, index) => <li
          key={option}
          id={`${listId}-${index}`}
          role="option"
          aria-selected={index === active}
          className={index === active ? "active" : undefined}
          // mousedown, not click: the input's blur would close the list before a click lands.
          onMouseDown={(event) => { event.preventDefault(); pick(option); }}
          onMouseEnter={() => setActive(index)}
        >{option}{describe?.(option) ? <span className="suggest-desc"> — {describe(option)}</span> : null}</li>)}
      </ul>,
      document.body,
    )}
  </>;
}
