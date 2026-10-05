import { useEffect, useState } from "react";
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

function load() {
  loading ??= api<Payload>("/api/stage-options")
    .then(publish)
    .catch(() => publish({ available: false, options: [] }));
  return loading;
}

/** Test hook: forget the cached answer. */
export function resetStageOptions() {
  current = null;
  loading = null;
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
  return Boolean(payload?.options.find((option) => option.key === key)?.enabled);
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
