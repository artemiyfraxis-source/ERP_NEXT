import { Fragment, useEffect, useId, useLayoutEffect, useRef, useState, type InputHTMLAttributes, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

export type ComboOption = { value: string; label: string; group?: string; desc?: string };

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "list"> & {
  options: readonly ComboOption[];
  /** strict: the selected option's value; free: the text itself */
  value: string;
  /** free: every keystroke and every pick; strict: a pick only */
  onChange: (value: string, option?: ComboOption) => void;
  /** only list values are accepted: typed text that is not an option is dropped on leaving the field */
  strict?: boolean;
  /** strict: an emptied field clears the choice */
  onClear?: () => void;
  /** options already narrowed by the caller (server search) — no filtering here */
  prefiltered?: boolean;
  limit?: number;
};

/** Searchable select / autocomplete with groups; shares the look of SuggestInput (`.suggest-list`). Enter and the
 *  arrows act on the list only while it is open and an option is highlighted — otherwise the key goes on to the
 *  caller (the grid moves between cells). */
export function ComboBox({
  options, value, onChange, strict = false, onClear, prefiltered = false, limit = 80,
  onFocus, onBlur, onKeyDown, ...rest
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [typedText, setTypedText] = useState<string | null>(null);
  const [box, setBox] = useState<{ left: number; top: number; width: number; up: boolean } | null>(null);

  const selectedLabel = strict ? (options.find((option) => option.value === value)?.label ?? value) : value;
  const text = strict && typedText !== null ? typedText : selectedLabel;
  const typed = (strict ? (typedText ?? "") : value).trim().toLocaleLowerCase("ru");
  const exactOption = typed ? options.find((option) => option.label.toLocaleLowerCase("ru") === typed) : undefined;
  const exact = Boolean(exactOption);
  const matches = (option: ComboOption) =>
    option.label.toLocaleLowerCase("ru").includes(typed) || (option.desc ?? "").toLocaleLowerCase("ru").includes(typed);
  const shown = (prefiltered || !typed || exact ? [...options] : options.filter(matches)).slice(0, limit);
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

  useEffect(() => { setActive(-1); }, [text, open]);

  const pick = (option: ComboOption) => {
    setTypedText(null);
    onChange(option.value, option);
    setOpen(false);
  };

  const keyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (visible && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current) => (current < 0 ? (step > 0 ? 0 : shown.length - 1) : (current + step + shown.length) % shown.length));
      return;
    }
    if (event.key === "Enter" && visible && active >= 0) {
      event.preventDefault();
      pick(shown[active]);
      return;
    }
    if (event.key === "Escape" && visible) {
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      return;
    }
    // An exact match wins over the list order: «м» typed is «м», not the first unit «шт».
    if (event.key === "Enter" && strict && typedText?.trim() && shown.length > 0) pick(exactOption ?? shown[0]);
    onKeyDown?.(event);
  };

  const leave = () => {
    setOpen(false);
    if (strict && typedText !== null) {
      if (!typedText.trim()) onClear?.();
      else if (exactOption && exactOption.value !== value) onChange(exactOption.value, exactOption); // Tab after «м» keeps «м»
      setTypedText(null);
    }
  };

  const grouped = shown.some((option) => option.group);
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
      value={text}
      onChange={(event) => {
        if (strict) setTypedText(event.target.value);
        else onChange(event.target.value);
        setOpen(true);
      }}
      onFocus={(event) => { setOpen(true); onFocus?.(event); }}
      onBlur={(event) => { leave(); onBlur?.(event); }}
      onClick={() => setOpen(true)}
      onKeyDown={keyDown}
    />
    {visible && box && createPortal(
      <ul
        id={listId}
        role="listbox"
        aria-label={rest["aria-label"] ? `Варианты: ${rest["aria-label"]}` : "Варианты"}
        className="suggest-list"
        style={{ left: box.left, minWidth: box.width, maxWidth: Math.max(box.width, window.innerWidth - box.left - 16), ...(box.up ? { bottom: window.innerHeight - box.top } : { top: box.top }) }}
      >
        {shown.map((option, index) => <Fragment key={`${option.group ?? ""}|${option.value}`}>
          {grouped && option.group && (index === 0 || shown[index - 1].group !== option.group) && <li role="presentation" className="suggest-group">{option.group}</li>}
          <li
            id={`${listId}-${index}`}
            role="option"
            aria-selected={index === active}
            className={index === active ? "active" : undefined}
            onMouseDown={(event) => { event.preventDefault(); pick(option); }}
            onMouseEnter={() => setActive(index)}
          >{option.label}{option.desc ? <span className="suggest-desc"> — {option.desc}</span> : null}</li>
        </Fragment>)}
      </ul>,
      document.body,
    )}
  </>;
}
