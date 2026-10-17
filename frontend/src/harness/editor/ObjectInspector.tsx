import { useEffect, useState } from "react";
import type { EditorSceneObject, HarnessEditorView } from "./editor-types";
import type { WireEndStripProfiles } from "./model";
import { builtInWireColors } from "./wire-reference-catalog";
import { WireDatabasePicker } from "./WireDatabasePicker";
import type { WireDatabaseOption } from "./wire-database";
import type { TerminalLengthCorrectionOptions, WireEndTerminalCorrections } from "./terminal-details";

export interface ObjectInspectorProps {
  readonly view: HarnessEditorView;
  readonly selectedObject: EditorSceneObject | null;
  readonly disabled: boolean;
  readonly onChange: (
    objectId: string,
    patch: Partial<Pick<EditorSceneObject, "label" | "x" | "y" | "color" | "metadata">>,
  ) => void;
  readonly onWireMaterialClear?: (wireId: string) => void;
  readonly wireMaterialOptions?: readonly WireDatabaseOption[];
  readonly onWireMaterialSelect?: (wireId:string,option:WireDatabaseOption)=>void;
  readonly wireStripProfiles?: WireEndStripProfiles;
  readonly activeWireStripEnd?: "from" | "to";
  readonly onActiveWireStripEndChange?: (end: "from" | "to") => void;
  readonly onWireStripProfileClear?: (wireId: string, end: "from" | "to") => void;
  readonly terminalCorrections?: WireEndTerminalCorrections;
  readonly onTerminalCorrectionApply?: (wireId: string, end: "from" | "to", correctionMm: number) => void;
}

function finiteNumber(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/** Returns null for valid intermediate text that must stay editable but must not reach the document. */
export function parseWireCorrectionDraft(value: string): number | null {
  const normalized = value.trim();
  if (normalized === "" || normalized === "-" || normalized === "+" ||
      normalized === "." || normalized === "-." || normalized === "+.") return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function WireCorrectionInput({
  label,
  value,
  disabled,
  onCommit,
}: {
  readonly label: string;
  readonly value: string;
  readonly disabled: boolean;
  readonly onCommit: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return <label>
    {label}
    <input
      type="text"
      inputMode="decimal"
      aria-label={label}
      value={draft}
      disabled={disabled}
      onChange={(event) => {
        const next = event.target.value;
        setDraft(next);
        const parsed = parseWireCorrectionDraft(next);
        if (parsed !== null) onCommit(String(parsed));
      }}
      onBlur={() => {
        if (parseWireCorrectionDraft(draft) === null) setDraft(value);
      }}
    />
  </label>;
}

function TerminalCorrectionButtons({
  end,
  correction,
  disabled,
  onApply,
}: {
  readonly end: "from" | "to";
  readonly correction?: TerminalLengthCorrectionOptions;
  readonly disabled: boolean;
  readonly onApply?: (end: "from" | "to", correctionMm: number) => void;
}) {
  if (!correction || !onApply) return null;
  const label = end === "from" ? "начала" : "конца";
  return <div className="he-terminal-correction-buttons" aria-label={`Поправка терминала ${label}`}>
    <span>Терминал {correction.terminalArticle}</span>
    <button type="button" disabled={disabled || correction.plusMm === null}
      aria-label={`Подставить L+ для ${label} провода`}
      title={correction.plusMm === null ? "В терминале не задано L+" : `Подставить +${correction.plusMm} мм`}
      onClick={() => correction.plusMm !== null && onApply(end, correction.plusMm)}
    >L+</button>
    <button type="button" disabled={disabled || correction.minusMm === null}
      aria-label={`Подставить L- для ${label} провода`}
      title={correction.minusMm === null ? "В терминале не задано L-" : `Подставить ${correction.minusMm} мм`}
      onClick={() => correction.minusMm !== null && onApply(end, correction.minusMm)}
    >L−</button>
  </div>;
}

export function ObjectInspector({
  view,
  selectedObject,
  disabled,
  onChange,
  onWireMaterialClear,
  wireMaterialOptions,
  onWireMaterialSelect,
  wireStripProfiles,
  activeWireStripEnd = "from",
  onActiveWireStripEndChange,
  onWireStripProfileClear,
  terminalCorrections,
  onTerminalCorrectionApply,
}: ObjectInspectorProps) {
  if (!selectedObject) {
    return (
      <div className="he-inspector-empty">
        <span className="he-inspector-empty-glyph" aria-hidden="true">◎</span>
        <strong>Объект не выбран</strong>
        <span>Выберите объект на поле, чтобы изменить его параметры.</span>
      </div>
    );
  }

  const wireLengthKnown = selectedObject.kind === "wire" && selectedObject.metadata?.lengthKnown !== "false";
  const wireCutLengthMm = selectedObject.kind === "wire" ? selectedObject.metadata?.cutLengthMm : undefined;

  return (
    <form className="he-property-form" onSubmit={(event) => event.preventDefault()}>
      <label>
        {selectedObject.kind === "wire" ? "Цепь / обозначение" : "Обозначение"}
        <input
          value={selectedObject.label}
          disabled={disabled}
          onChange={(event) => onChange(selectedObject.id, { label: event.target.value })}
        />
      </label>
      {selectedObject.kind === "wire" && wireMaterialOptions && onWireMaterialSelect && <WireDatabasePicker
        key={selectedObject.id}
        options={wireMaterialOptions} currentMark={selectedObject.metadata?.wireMark} currentRecordId={selectedObject.metadata?.materialRecordId}
        disabled={disabled} onSelect={option=>onWireMaterialSelect(selectedObject.id,option)}/>}
      {selectedObject.kind === "wire" && view === "drawing" && (
        <>
          {onActiveWireStripEndChange && (
            <fieldset className="he-wire-strip-profile" aria-label="Оконцовка свободного конца провода">
              <legend>Оконцовка свободного конца</legend>
              <div className="he-field-pair">
                {(["from", "to"] as const).map((end) => {
                  const profile = wireStripProfiles?.[end];
                  const label = end === "from" ? "Начало" : "Конец";
                  return <button
                    key={end}
                    type="button"
                    className={activeWireStripEnd === end ? "active" : ""}
                    aria-label={`${label} провода${profile ? `: ${profile.displayName}` : ": профиль не выбран"}`}
                    aria-pressed={activeWireStripEnd === end}
                    disabled={disabled}
                    onClick={() => onActiveWireStripEndChange(end)}
                  >{label}: {profile?.displayName ?? "профиль не выбран"}</button>;
                })}
              </div>
              {wireStripProfiles?.[activeWireStripEnd] && onWireStripProfileClear && <button
                type="button"
                className="secondary-action"
                disabled={disabled}
                onClick={() => onWireStripProfileClear(selectedObject.id, activeWireStripEnd)}
              >Очистить оконцовку</button>}
              <small>Выберите конец и дважды щёлкните профиль полуфабриката в справочнике.</small>
            </fieldset>
          )}
          <label className="he-toggle-field">
            <input
              type="checkbox"
              aria-label="Длина задана"
              checked={wireLengthKnown}
              disabled={disabled}
              onChange={(event) => onChange(selectedObject.id, {
                metadata: {
                  ...selectedObject.metadata,
                  lengthKnown: String(event.target.checked),
                  lengthMm: event.target.checked ? selectedObject.metadata?.lengthMm || "0" : "",
                },
              })}
            />
            <span>Длина задана</span>
          </label>
          <label>
            Абсолютная длина, мм
            <input
              type="number"
              aria-label="Абсолютная длина, мм"
              min="0"
              step="0.001"
              value={selectedObject.metadata?.lengthMm ?? ""}
              disabled={disabled || !wireLengthKnown}
              onChange={(event) => onChange(selectedObject.id, {
                metadata: { ...selectedObject.metadata, lengthMm: event.target.value },
              })}
            />
          </label>
          <div className="he-field-pair">
            <div className="he-terminal-correction-field">
              <WireCorrectionInput
                key={`${selectedObject.id}:from`}
                label="Поправка начала, мм"
                value={selectedObject.metadata?.endCorrectionFromMm ?? "0"}
                disabled={disabled}
                onCommit={(value) => onChange(selectedObject.id, {
                  metadata: { ...selectedObject.metadata, endCorrectionFromMm: value },
                })}
              />
              <TerminalCorrectionButtons end="from" correction={terminalCorrections?.from} disabled={disabled}
                onApply={(end, correctionMm) => onTerminalCorrectionApply?.(selectedObject.id, end, correctionMm)} />
            </div>
            <div className="he-terminal-correction-field">
              <WireCorrectionInput
                key={`${selectedObject.id}:to`}
                label="Поправка конца, мм"
                value={selectedObject.metadata?.endCorrectionToMm ?? "0"}
                disabled={disabled}
                onCommit={(value) => onChange(selectedObject.id, {
                  metadata: { ...selectedObject.metadata, endCorrectionToMm: value },
                })}
              />
              <TerminalCorrectionButtons end="to" correction={terminalCorrections?.to} disabled={disabled}
                onApply={(end, correctionMm) => onTerminalCorrectionApply?.(selectedObject.id, end, correctionMm)} />
            </div>
          </div>
          <label>
            Шаг округления длины резки, мм
            <input
              type="number"
              aria-label="Шаг округления длины резки, мм"
              min="0.001"
              step="0.001"
              value={selectedObject.metadata?.cutRoundingStepMm ?? "1"}
              disabled={disabled}
              onChange={(event) => onChange(selectedObject.id, {
                metadata: { ...selectedObject.metadata, cutRoundingStepMm: event.target.value },
              })}
            />
          </label>
          {wireLengthKnown ? (
            <div className="he-wire-length-status is-complete" role="status">
              <span>Расчётная длина резки</span>
              <strong>{wireCutLengthMm || "—"} мм</strong>
              <small>Длина готова для карты резки</small>
            </div>
          ) : (
            <div className="he-wire-length-status is-incomplete" role="alert">
              <strong>Длина провода не задана</strong>
              <span>Провод исключён из материалов до заполнения длины.</span>
            </div>
          )}
        </>
      )}
      {selectedObject.kind === "text" && (
        <div className="he-field-pair">
          <label>
            X
            <input
              type="number"
              value={selectedObject.x}
              disabled={disabled}
              onChange={(event) => onChange(selectedObject.id, {
                x: finiteNumber(event.target.value, selectedObject.x),
              })}
            />
          </label>
          <label>
            Y
            <input
              type="number"
              value={selectedObject.y}
              disabled={disabled}
              onChange={(event) => onChange(selectedObject.id, {
                y: finiteNumber(event.target.value, selectedObject.y),
              })}
            />
          </label>
        </div>
      )}
      {selectedObject.kind !== "connector" && <label>
        Цвет
        {selectedObject.kind === "wire" && <span className="he-wire-standard-colors" aria-label="Стандартные цвета проводов">
          {builtInWireColors.map((color) => <button
            key={color.id}
            type="button"
            aria-label={color.name}
            title={color.name}
            disabled={disabled}
            className={selectedObject.color.toUpperCase() === color.hex ? "active" : ""}
            style={{ backgroundColor: color.hex }}
            onClick={() => onChange(selectedObject.id, { color: color.hex })}
          />)}
        </span>}
        <span className="he-color-field">
          <input
            type="color"
            aria-label="Цвет объекта"
            value={selectedObject.color}
            disabled={disabled}
            onChange={(event) => onChange(selectedObject.id, { color: event.target.value })}
          />
          <code>{selectedObject.color.toUpperCase()}</code>
        </span>
      </label>}
      <div className="he-readonly-facts">
        <div><span>Слой</span><strong>{selectedObject.layerId}</strong></div>
        <div><span>Представление</span><strong>{view === "e4" ? "Схема Э4" : "Чертёж"}</strong></div>
      </div>
    </form>
  );
}
