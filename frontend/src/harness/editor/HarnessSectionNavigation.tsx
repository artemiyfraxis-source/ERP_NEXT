import type { ReactNode } from "react";
import "./harness-section-navigation.css";

export const harnessSectionItems = [
  { id: "e4", label: "Схема Э4" },
  { id: "drawing", label: "Чертёж" },
  { id: "route", label: "Маршрут" },
  { id: "route-v2", label: "Маршрут v2" },
  { id: "uml", label: "UML универсальный" },
] as const;

export type HarnessSectionId = typeof harnessSectionItems[number]["id"];

interface HarnessSectionNavigationProps {
  readonly active: HarnessSectionId;
  readonly onNavigate: (section: HarnessSectionId) => void | Promise<void>;
  readonly onHome?: () => void | Promise<void>;
  readonly disabled?: boolean;
  readonly prefix?: ReactNode;
}

/** Shared navigation for every harness document editor. */
export function HarnessSectionNavigation({
  active,
  onNavigate,
  onHome,
  disabled = false,
  prefix,
}: HarnessSectionNavigationProps) {
  return (
    <nav className="harness-section-navigation" aria-label="Разделы жгута">
      {onHome && (
        <button
          type="button"
          className="harness-section-home"
          onClick={() => void onHome()}
          disabled={disabled}
          aria-label="Вернуться к проектам"
        >
          <span aria-hidden="true">←</span>
          <span>К проектам</span>
        </button>
      )}
      {prefix}
      <div className="harness-section-links" role="tablist" aria-label="Документы жгута">
        {harnessSectionItems.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            className={active === item.id ? "active" : undefined}
            aria-selected={active === item.id}
            aria-current={active === item.id ? "page" : undefined}
            onClick={() => void onNavigate(item.id)}
            disabled={disabled || active === item.id}
          >
            {item.label}
          </button>
        ))}
      </div>
    </nav>
  );
}
