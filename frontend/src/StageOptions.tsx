import { useEffect, useMemo, useState } from "react";
import { api, failureText } from "./api";

/** Опции стенда (Витя, 05.10.2026): гибридные блоки включаются по одному — «Было» или «Новое». На проде список
 *  недоступен (`available: false`), все опции выключены, панель не показывается. */
export type StageOption = { key: string; block: string; title: string; before: string; after: string; enabled: boolean };
type Payload = { available: boolean; options: StageOption[] };

let current: Payload | null = null;
let loading: Promise<void> | null = null;
const listeners = new Set<() => void>();

function publish(next: Payload) {
  current = next;
  listeners.forEach((listener) => listener());
}

let generation = 0;
// The last answer was a refusal (no sign-in yet, no network) rather than the server's word.
let refused = false;
// The current generation has its answer (a reload after sign-in clears it until the new one comes).
let settled = false;

function load() {
  if (!loading) {
    const mine = generation;
    // An answer that lands after a reset belongs to the previous generation and is dropped.
    loading = api<Payload>("/api/stage-options")
      .then((payload) => { if (mine === generation) { refused = false; settled = true; publish(payload); } })
      .catch(() => { if (mine === generation) { refused = true; settled = true; publish({ available: false, options: [] }); } });
  }
  return loading;
}

/** After a sign-in: a list asked before it (the shell asks at once, the answer was 401) is asked again — otherwise
 *  every option looked off until the page was reloaded. */
export function reloadRefusedStageOptions() {
  if (!refused) return;
  generation += 1;
  refused = false;
  settled = false;
  loading = null;
  void load();
}

/** Test hook: forget the cached answer. */
export function resetStageOptions() {
  generation += 1;
  current = null;
  loading = null;
  refused = false;
  settled = false;
}

function useStageOptions(): Payload | null {
  const [, setTick] = useState(0);
  useEffect(() => {
    const listener = () => setTick((tick) => tick + 1);
    listeners.add(listener);
    void load();
    return () => { listeners.delete(listener); };
  }, []);
  return current;
}

/** True only when the stage runs and the option is switched to «Новое». */
export function useStageOption(key: string): boolean {
  const payload = useStageOptions();
  return Boolean(payload?.options?.find((option) => option.key === key)?.enabled);
}

/** `undefined` while the list is still loading: a screen that exists only behind the option (the ТСД) waits for it
 *  instead of showing «off» for a moment. */
export function useStageOptionState(key: string): boolean | undefined {
  const payload = useStageOptions();
  return payload && settled ? Boolean(payload.options?.find((option) => option.key === key)?.enabled) : undefined;
}

/** The keys switched to «Новое» (empty off the stage) — for things that open on any of several options, like a
 *  menu section. A key the backend does not know is simply never in the set. */
export function useEnabledStageOptions(): ReadonlySet<string> {
  const payload = useStageOptions();
  const keys = (payload?.options ?? []).filter((option) => option.enabled).map((option) => option.key).sort().join("\n");
  return useMemo(() => new Set(keys ? keys.split("\n") : []), [keys]);
}

export function StageOptionsPanel({ canSwitch }: { canSwitch: boolean }) {
  const payload = useStageOptions();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  if (!payload?.available) return null;

  async function toggle(option: StageOption, enabled: boolean) {
    setBusy(option.key);
    setError("");
    try {
      publish(await api<Payload>(`/api/stage-options/${encodeURIComponent(option.key)}`, {
        method: "PUT", body: JSON.stringify({ enabled }),
      }));
    } catch (reason) {
      setError(failureText(reason, "Опция не переключилась"));
    } finally {
      setBusy("");
    }
  }

  const blocks = [...new Set(payload.options.map((option) => option.block))];
  const on = payload.options.filter((option) => option.enabled).length;
  return <div className="stage-options">
    <button className="link-button" aria-expanded={open} onClick={() => setOpen(!open)}>
      Опции стенда · {on}/{payload.options.length}
    </button>
    {open && <div className="stage-options-card">
      {blocks.map((block) => <section key={block}>
        <strong>{block}</strong>
        {payload.options.filter((option) => option.block === block).map((option) => <div key={option.key} className="stage-option">
          <div className="stage-option-head">
            <span>{option.title}</span>
            <span className="stage-option-switch" role="group" aria-label={option.title}>
              <button className={option.enabled ? "secondary small" : "primary small"} aria-pressed={!option.enabled}
                disabled={!canSwitch || busy === option.key} onClick={() => void toggle(option, false)}>Было</button>
              <button className={option.enabled ? "primary small" : "secondary small"} aria-pressed={option.enabled}
                disabled={!canSwitch || busy === option.key} onClick={() => void toggle(option, true)}>Новое</button>
            </span>
          </div>
          <small>{option.enabled ? option.after : option.before}</small>
        </div>)}
      </section>)}
      {!canSwitch && <small>Переключает администратор.</small>}
      {error && <div className="error-banner" role="alert">{error}</div>}
    </div>}
  </div>;
}
