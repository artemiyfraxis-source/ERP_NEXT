import { useEffect, useRef, useState } from "react";
import { devicesApi } from "./devicesApi";

/** «Сканер не вводит код?» (Roller_Buyer ScannerCheck): shows — and sends to the PC (stage data scanner-log.txt) — what
 *  the terminal's scanner really delivers to the browser: key events, input, composition, paste, with timing. A scanner
 *  set to «broadcast» or «clipboard» output delivers nothing at all; one that writes through the input method may deliver
 *  only with the on-screen keyboard on. One scan here tells which case it is. */
export function ScannerCheck({ onClose }: { onClose: () => void }) {
  const [lines, setLines] = useState<string[]>([]);
  const [value, setValue] = useState("");
  const [sent, setSent] = useState<"no" | "yes" | "error">("no");
  // Some scanners write through the input method, which is attached only with the keyboard allowed.
  const [keyboard, setKeyboard] = useState(false);
  const start = useRef(Date.now());
  const pending = useRef<string[]>([]);
  const field = useRef<HTMLInputElement>(null);

  useEffect(() => {
    field.current?.focus();
    const push = (line: string) => {
      const stamped = `${String(Date.now() - start.current).padStart(6)}ms ${line}`;
      pending.current.push(stamped);
      // Deferred: a state update inside a capture listener re-rendered the field before React's own onChange ran,
      // and React put the old (empty) value back — the field never showed the scan (Roller_Buyer).
      window.setTimeout(() => setLines((current) => [...current.slice(-39), stamped]), 0);
    };
    const tag = (target: EventTarget | null) =>
      target instanceof HTMLElement ? target.tagName.toLowerCase() + (target === field.current ? "#test" : "") : "?";
    const onKey = (event: KeyboardEvent) =>
      push(`${event.type} key=${JSON.stringify(event.key)} code=${event.code || "-"} keyCode=${event.keyCode} → ${tag(event.target)}`);
    const onInput = (event: Event) => {
      const input = event as InputEvent;
      push(`input type=${input.inputType || "-"} data=${JSON.stringify(input.data === undefined ? null : input.data)} → ${tag(event.target)}`);
    };
    const onComposition = (event: CompositionEvent) => push(`${event.type} data=${JSON.stringify(event.data)}`);
    const onPaste = (event: ClipboardEvent) => push(`paste ${JSON.stringify(event.clipboardData ? event.clipboardData.getData("text") : "")}`);
    const onFocus = () => push(`focus ${tag(document.activeElement)}`);
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("keypress", onKey, true);
    window.addEventListener("input", onInput, true);
    window.addEventListener("compositionend", onComposition, true);
    window.addEventListener("paste", onPaste, true);
    window.addEventListener("focusin", onFocus, true);
    // Batches go to the PC a moment after the scanner goes quiet.
    const timer = window.setInterval(() => {
      if (pending.current.length === 0) return;
      const batch = pending.current.splice(0, 60);
      devicesApi.sendScannerLog(batch).then(() => setSent("yes"), () => setSent("error"));
    }, 2500);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("keypress", onKey, true);
      window.removeEventListener("input", onInput, true);
      window.removeEventListener("compositionend", onComposition, true);
      window.removeEventListener("paste", onPaste, true);
      window.removeEventListener("focusin", onFocus, true);
    };
  }, []);

  return <div className="tsd-card scanner-check">
    <b>Проверка сканера</b>
    <ol>
      <li>Нажмите на поле ниже и отсканируйте этикетку.</li>
      <li>Если в поле пусто и строк ниже нет — нажмите «Включить клавиатуру», затем на поле, и отсканируйте ещё раз.</li>
      <li>Если снова пусто — сканер отдаёт код не как клавиатура. В настройках сканера на ТСД выберите вывод «эмуляция
        клавиатуры» (Keyboard / Keystroke) и суффикс Enter.</li>
    </ol>
    <input
      ref={field}
      className="tsd-scan"
      aria-label="Поле проверки сканера"
      placeholder="сюда сканировать"
      value={value}
      autoComplete="off"
      inputMode={keyboard ? "text" : "none"}
      onChange={(event) => setValue(event.target.value)}
    />
    <button
      type="button"
      className={keyboard ? "tsd-btn active" : "tsd-btn"}
      onClick={() => {
        const mark = `--- клавиатура ${keyboard ? "выключена" : "включена"}`;
        pending.current.push(mark);
        setLines((current) => [...current.slice(-39), mark]);
        setKeyboard(!keyboard);
        field.current?.blur();
        window.setTimeout(() => field.current?.focus(), 0);
      }}
    >
      {keyboard ? "⌨ Клавиатура включена" : "⌨ Включить клавиатуру"}
    </button>
    <div className="muted">
      В поле: «{value}» · событий: {lines.length}
      {sent === "yes" ? " · отправлено на компьютер" : sent === "error" ? " · не отправилось" : ""}
    </div>
    <pre className="scanner-log">{lines.length ? lines.join("\n") : "ещё ничего не пришло"}</pre>
    <button type="button" className="tsd-btn" onClick={onClose}>Закрыть проверку</button>
  </div>;
}
