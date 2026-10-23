import { useEffect, useState } from "react";

export type ThemeChoice = "light" | "dark";
export const THEME_KEY = "erp-theme";

const CHOICES: { value: ThemeChoice; label: string }[] = [
  { value: "light", label: "Светлая" },
  { value: "dark", label: "Тёмная" },
];

/** Сохранённое «system» (бывшая опция «Как в системе») читается как «Светлая» и тихо переписывается. */
function readChoice(): ThemeChoice {
  try {
    const stored = window.localStorage.getItem(THEME_KEY);
    if (stored === "system") window.localStorage.setItem(THEME_KEY, "light");
    return stored === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

function writeChoice(choice: ThemeChoice) {
  try { window.localStorage.setItem(THEME_KEY, choice); } catch { /* storage blocked: the choice lasts until reload */ }
}

/** Переключатель «Светлая · Тёмная». Always mounted in the sidebar. */
export function ThemeSwitch() {
  const [choice, setChoice] = useState<ThemeChoice>(readChoice);

  useEffect(() => { document.documentElement.dataset.theme = choice; }, [choice]);

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
