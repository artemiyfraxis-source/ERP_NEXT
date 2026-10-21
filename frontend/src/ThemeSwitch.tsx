import { useEffect, useState } from "react";
import { useStageOption } from "./StageOptions";

export type ThemeChoice = "light" | "dark" | "system";
export const THEME_KEY = "erp-theme";

const CHOICES: { value: ThemeChoice; label: string }[] = [
  { value: "light", label: "Светлая" },
  { value: "dark", label: "Тёмная" },
  { value: "system", label: "Как в системе" },
];

function readChoice(): ThemeChoice {
  try {
    const stored = window.localStorage.getItem(THEME_KEY);
    return stored === "light" || stored === "dark" || stored === "system" ? stored : "system";
  } catch {
    return "system";
  }
}

function writeChoice(choice: ThemeChoice) {
  try { window.localStorage.setItem(THEME_KEY, choice); } catch { /* storage blocked: the choice lasts until reload */ }
}

function systemQuery(): MediaQueryList | null {
  return typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
}

/** Переключатель «Светлая · Тёмная · Как в системе» (опция ui.dark_theme). Без опции ничего не рисует и держит светлую тему.
 *  Always mounted in the sidebar: its effect also strips data-theme when the option is off. */
export function ThemeSwitch() {
  const enabled = useStageOption("ui.dark_theme");
  const [choice, setChoice] = useState<ThemeChoice>(readChoice);

  useEffect(() => {
    const root = document.documentElement;
    if (!enabled) {
      delete root.dataset.theme;
      return;
    }
    if (choice !== "system") {
      root.dataset.theme = choice;
      return;
    }
    const query = systemQuery();
    const apply = () => { root.dataset.theme = query?.matches ? "dark" : "light"; };
    apply();
    query?.addEventListener?.("change", apply);
    return () => { query?.removeEventListener?.("change", apply); };
  }, [enabled, choice]);

  if (!enabled) return null;

  function pick(next: ThemeChoice) {
    writeChoice(next);
    setChoice(next);
  }

  return <div className="theme-switch" role="group" aria-label="Тема интерфейса">
    <span>Тема</span>
    <div className="theme-switch-buttons">
      {CHOICES.map((item) => <button key={item.value} type="button" aria-pressed={choice === item.value}
        onClick={() => pick(item.value)}>{item.label}</button>)}
    </div>
  </div>;
}
