import { useEffect } from "react";

/** Одна строка пояснений на панель вместо разбросанных «i» (ERP-вид, 13.10.2026).
 *  InfoHint помечает поясняемый элемент (`data-help`) — кнопку перед ним, подпись поля или строку настройки;
 *  панель с `<HelpLine/>` показывает в своей строке текст того, что под указателем или в фокусе. Вне такой панели
 *  InfoHint остаётся прежним значком «i» со всплывающей подсказкой. */

const INTERACTIVE = "button, select, input, textarea, a[href], summary, label";

/** The element a hint explains: its labelled parent row, else the control right before it, else the parent. */
export function helpOwner(hint: Element): HTMLElement | null {
  const parent = hint.parentElement;
  if (!parent) return null;
  if (parent.matches("label, summary, header, .he-thickness-control")) return parent;
  const previous = hint.previousElementSibling;
  if (previous instanceof HTMLElement && previous.matches(INTERACTIVE)) return previous;
  return parent;
}

/** The help line of the nearest panel that has one, or null (the hint keeps its «i»). */
export function helpLineFor(target: Element | null): HTMLElement | null {
  for (let element = target; element; element = element.parentElement) {
    const line = element.querySelector<HTMLElement>(":scope > .help-line-slot > .help-line");
    if (line) return line;
  }
  return null;
}

function hostOf(line: HTMLElement): HTMLElement | null {
  return line.parentElement?.parentElement ?? null;
}

/** Puts the text of the hovered/focused control into its panel's line; anything without a hint clears it. */
export function showHelpFor(target: EventTarget | null) {
  if (!(target instanceof Element)) return;
  const line = helpLineFor(target);
  if (!line) return;
  const owner = target.closest<HTMLElement>("[data-help]");
  const text = owner && hostOf(line)?.contains(owner) ? owner.dataset.help ?? "" : "";
  if (line.textContent !== text) line.textContent = text;
}

function clearWhenLeaving(target: EventTarget | null, next: EventTarget | null) {
  if (!(target instanceof Element)) return;
  const line = helpLineFor(target), host = line && hostOf(line);
  if (line && host && !(next instanceof Node && host.contains(next))) line.textContent = "";
}

const over = (event: Event) => showHelpFor(event.target);
const focusIn = (event: Event) => showHelpFor(event.target);
const out = (event: Event) => clearWhenLeaving(event.target, (event as PointerEvent).relatedTarget);
const focusOut = (event: Event) => clearWhenLeaving(event.target, (event as FocusEvent).relatedTarget);
let mounted = 0;

function listen() {
  if (mounted++ === 0) {
    document.addEventListener("pointerover", over);
    document.addEventListener("pointerout", out);
    document.addEventListener("focusin", focusIn);
    document.addEventListener("focusout", focusOut);
  }
  return () => {
    if (--mounted === 0) {
      document.removeEventListener("pointerover", over);
      document.removeEventListener("pointerout", out);
      document.removeEventListener("focusin", focusIn);
      document.removeEventListener("focusout", focusOut);
    }
  };
}

/** The help line of a panel: put it as a direct child of the panel. `idle` — the grey text while nothing is pointed at;
 *  `inline` — one line inside a header (table windows), empty and invisible while idle. */
export function HelpLine({ idle = "", inline = false }: { readonly idle?: string; readonly inline?: boolean }) {
  useEffect(listen, []);
  // The text is written straight into the paragraph (no React children), so a hover does not re-render the panel.
  return <div className={`help-line-slot${inline ? " inline" : ""}`}><p className="help-line" data-idle={idle} /></div>;
}
