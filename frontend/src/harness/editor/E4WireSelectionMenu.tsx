import type { CSSProperties } from "react";
import { InfoHint } from "../InfoHint";
import {
  clampScreenPosition,
  e4WireSelectionCapabilities,
  normalizeTwistPitchMm,
  type E4CrossingStyle,
  type E4DifferentialPairState,
  type E4DifferentialPairVariant,
  type E4ScreenState,
} from "./e4-wire-selection-state";
import "./e4-wire-selection-menu.css";

export interface E4WireSelectionMenuProps {
  readonly selectedWireIds: readonly string[];
  readonly crossingStyle: E4CrossingStyle;
  readonly differentialPair: E4DifferentialPairState | null;
  readonly screen: E4ScreenState | null;
  readonly canClearGroup: boolean;
  readonly disabled?: boolean;
  readonly anchor?: Readonly<{ x: number; y: number }>;
  readonly onCrossingStyleChange: (style: E4CrossingStyle) => void;
  readonly onDifferentialPairChange: (state: E4DifferentialPairState | null) => void;
  readonly onScreenChange: (state: E4ScreenState | null) => void;
  readonly onClearGroup: () => void;
  readonly detached?: boolean;
  readonly onDetachedChange?: (detached: boolean) => void;
  readonly onReroute?: () => void;
  readonly onDismiss?: () => void;
}

function CrossingPreview({ style }: { readonly style: E4CrossingStyle }) {
  return (
    <svg viewBox="0 0 32 22" aria-hidden="true">
      <path d="M3 4 29 18" />
      {style === "none"
        ? <path d="M3 18 29 4" />
        : <path d="M3 18 12 11 Q16 4 20 11 L29 4" />}
    </svg>
  );
}

export function E4WireSelectionMenu({
  selectedWireIds,
  crossingStyle,
  differentialPair,
  screen,
  canClearGroup,
  disabled = false,
  anchor,
  onCrossingStyleChange,
  onDifferentialPairChange,
  onScreenChange,
  onClearGroup,
  detached = false,
  onDetachedChange,
  onReroute,
  onDismiss,
}: E4WireSelectionMenuProps) {
  const capabilities = e4WireSelectionCapabilities(selectedWireIds);
  const positionedStyle: CSSProperties | undefined = anchor
    ? { left: anchor.x, top: anchor.y }
    : undefined;

  const toggleDifferentialPair = () => {
    if (differentialPair) {
      onDifferentialPairChange(null);
      return;
    }
    if (capabilities.canCreateDifferentialPair) {
      onDifferentialPairChange({ variant: 1, twistPitchMm: 25 });
    }
  };

  const toggleScreen = () => {
    if (screen) {
      onScreenChange(null);
      return;
    }
    if (capabilities.canCreateScreen) onScreenChange({ positionPercent: 50, terminalSide: "above" });
  };

  return (
    <aside
      className={`e4-wire-menu${anchor ? " floating" : ""}`}
      style={positionedStyle}
      aria-label="Соединения выбранных проводов"
    >
      <header className="e4wm-header">
        <div>
          <span>Соединения Э4</span>
          <strong>Выбрано проводов: {capabilities.selectedCount}</strong>
        </div>
        {onDismiss && (
          <button type="button" className="e4wm-close" onClick={onDismiss} aria-label="Закрыть меню">×</button>
        )}
      </header>

      <fieldset className="e4wm-section" disabled={disabled || !capabilities.canSetCrossing}>
        <legend>Отображение пересечения</legend>
        <p className="e4wm-scope-hint">Настройка применяется ко всей схеме.</p>
        <div className="e4wm-crossing-options">
          <button
            type="button"
            className={crossingStyle === "none" ? "active" : ""}
            aria-pressed={crossingStyle === "none"}
            onClick={() => onCrossingStyleChange("none")}
          >
            <CrossingPreview style="none" />
            <span>Без знака</span>
          </button>
          <button
            type="button"
            className={crossingStyle === "bridge" ? "active" : ""}
            aria-pressed={crossingStyle === "bridge"}
            onClick={() => onCrossingStyleChange("bridge")}
          >
            <CrossingPreview style="bridge" />
            <span>Дуга</span>
          </button>
        </div>
      </fieldset>

      <section className="e4wm-section">
        <button
          type="button"
          className={`e4wm-feature-toggle${differentialPair ? " active" : ""}`}
          disabled={disabled || (!differentialPair && !capabilities.canCreateDifferentialPair)}
          aria-pressed={differentialPair !== null}
          onClick={toggleDifferentialPair}
        >
          <span className="e4wm-feature-icon">≈</span>
          <span><strong>Дифф. пара</strong><small>ровно два провода</small></span>
          <span className="e4wm-switch" aria-hidden="true" />
        </button>
        {!differentialPair && capabilities.selectedCount !== 2 && (
          <p className="e4wm-hint">Для пары выберите два провода.</p>
        )}
        {differentialPair && (
          <div className="e4wm-feature-settings">
            <span className="e4wm-setting-label">Вид</span>
            <div className="e4wm-variant-buttons">
              {([1, 2] as const).map((variant: E4DifferentialPairVariant) => (
                <button
                  type="button"
                  key={variant}
                  className={differentialPair.variant === variant ? "active" : ""}
                  disabled={disabled}
                  aria-pressed={differentialPair.variant === variant}
                  onClick={() => onDifferentialPairChange({ ...differentialPair, variant })}
                >
                  <svg viewBox="0 0 44 20" aria-hidden="true">
                    {variant === 1 ? (
                      <>
                        <path d="M2 5 C9 5 10 15 17 15 S25 5 32 5 35 15 42 15" />
                        <path d="M2 15 C9 15 10 5 17 5 S25 15 32 15 35 5 42 5" />
                      </>
                    ) : (
                      <>
                        <path d="M2 6 H14 L20 14 H42" />
                        <path d="M2 14 H14 L20 6 H42" />
                      </>
                    )}
                  </svg>
                  {variant}
                </button>
              ))}
            </div>
            <label className="e4wm-number-field">
              <span>Шаг, мм/оборот</span>
              <input
                type="number"
                min="0.1"
                max="10000"
                step="0.1"
                value={differentialPair.twistPitchMm}
                disabled={disabled}
                onChange={(event) => onDifferentialPairChange({
                  ...differentialPair,
                  twistPitchMm: normalizeTwistPitchMm(Number(event.target.value)),
                })}
              />
            </label>
          </div>
        )}
      </section>

      <section className="e4wm-section">
        <button
          type="button"
          className={`e4wm-feature-toggle${screen ? " active" : ""}`}
          disabled={disabled || (!screen && !capabilities.canCreateScreen)}
          aria-pressed={screen !== null}
          onClick={toggleScreen}
        >
          <span className="e4wm-feature-icon screen">◌</span>
          <span><strong>Экран</strong><small>один или несколько проводов</small></span>
          <span className="e4wm-switch" aria-hidden="true" />
        </button>
        {screen && (
          <div className="e4wm-screen-settings">
            <span className="e4wm-screen-side-label">Вывод экрана</span>
            <div className="e4wm-screen-side" role="group" aria-label="Сторона вывода экрана">
              {([
                ["above", "Сверху"],
                ["below", "Снизу"],
                ["both", "С двух сторон"],
              ] as const).map(([side, label]) => (
                <button
                  type="button"
                  key={side}
                  className={(screen.terminalSide ?? "above") === side ? "active" : ""}
                  aria-pressed={(screen.terminalSide ?? "above") === side}
                  disabled={disabled}
                  onClick={() => onScreenChange({ ...screen, terminalSide: side })}
                >
                  {label}
                </button>
              ))}
            </div>
            <label htmlFor="e4-screen-position">Положение экрана <InfoHint>Экран перемещается вдоль всего доступного диапазона проводов с отступом от компонентов. Высота следует проводам в выбранном сечении. Вывод к контакту компонента — обычный ортогональный провод; подключение к другому проводу — прямой участок.</InfoHint></label>
            <output htmlFor="e4-screen-position">{clampScreenPosition(screen.positionPercent)}%</output>
            <input
              id="e4-screen-position"
              type="range"
              min="0"
              max="100"
              step="1"
              value={clampScreenPosition(screen.positionPercent)}
              disabled={disabled}
              aria-label="Положение экрана вдоль проводов"
              onChange={(event) => onScreenChange({
                ...screen,
                positionPercent: clampScreenPosition(Number(event.target.value)),
              })}
            />
            <span className="start">начало</span>
            <span className="end">конец</span>
          </div>
        )}
      </section>

      <footer className="e4wm-footer">
        {onDetachedChange && <div>
          <button type="button" disabled={disabled} aria-pressed={detached}
            onClick={() => onDetachedChange(!detached)}>{detached ? "Вернуть привязку" : "Открепить привязку"}</button>
          <InfoHint>Временно разрешает двигать сегменты через соседние провода. Электрические подключения и препятствия таблиц сохраняются. Для упорядочивания нажмите «Перестроить».</InfoHint>
        </div>}
        {onReroute && <button type="button" disabled={disabled} onClick={onReroute}>Перестроить</button>}
        <button
          type="button"
          disabled={disabled || !capabilities.canClearGroup || !canClearGroup}
          onClick={onClearGroup}
        >
          Разгруппировать выбранное
        </button>
      </footer>
    </aside>
  );
}
