import { calculateCableSheathStrip, type CableInstance } from "./model";
import { InfoHint } from "../InfoHint";

export interface CableSelectionPanelProps {
  readonly selectedWireIds: readonly string[];
  readonly cable: CableInstance | null;
  readonly disabled: boolean;
  readonly onCreate: () => void;
  readonly onUpdate: (patch: Partial<Pick<CableInstance,
    "lengthMm" | "endCorrectionFromMm" | "endCorrectionToMm" | "cutRoundingStepMm" | "sheathStrip">>) => void;
  readonly onMaterialClear: () => void;
  readonly onRemove: () => void;
}

function finiteNumber(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/** Turns selected electrical wires into one physical cable without hiding the wires. */
export function CableSelectionPanel({ selectedWireIds, cable, disabled, onCreate, onUpdate, onMaterialClear, onRemove }: CableSelectionPanelProps) {
  if (!cable) return <section className="he-cable-panel" aria-label="Многожильный кабель">
    <header><strong>Многожильный кабель</strong><span>{selectedWireIds.length} жилы</span></header>
    <InfoHint>Провода останутся отдельными электрическими цепями, а материал и длина будут учтены один раз.</InfoHint>
    <button type="button" disabled={disabled || selectedWireIds.length < 2} onClick={onCreate}>Создать кабель</button>
  </section>;

  const stripping = calculateCableSheathStrip(cable);
  return <section className="he-cable-panel" aria-label="Многожильный кабель">
    <header><strong>Многожильный кабель</strong><span>{cable.memberWireIds.length} жилы</span></header>
    <small>Кабель</small>
    <div className="he-cable-members" aria-label="Жилы кабеля">
      {cable.memberWireIds.map((wireId) => <span key={wireId}>{wireId}</span>)}
    </div>
    <div className="he-wire-material" aria-label="Материал кабеля">
      <strong>Материал</strong>
      {cable.materialBinding ? <>
        <span>{cable.materialBinding.displayName}</span><small>{cable.materialBinding.sourceKey}</small>
        <button type="button" disabled={disabled} onClick={onMaterialClear}>Очистить материал</button>
      </> : <><span>Материал не выбран</span><InfoHint>Дважды щёлкните кабель в нижнем справочнике.</InfoHint></>}
    </div>
    <label>Конечная длина, мм
      <input type="number" aria-label="Конечная длина кабеля, мм" min="0" step="0.001" value={cable.lengthMm ?? ""} disabled={disabled}
        onChange={(event) => onUpdate({ lengthMm: event.target.value.trim() === "" ? null : finiteNumber(event.target.value, cable.lengthMm ?? 0) })} />
    </label>
    <header><strong>Снятие оболочки</strong><InfoHint>
      Размеры от концов кабеля, мм. Начало совпадает с началом первой жилы ({cable.memberWireIds[0]}).
      Пусто — размер не задан; 0 — оболочка не снимается. Положительный размер снятия
      учитывает поправку соответствующего конца. Округление резки отдельно.
      На чертеже показаны конечные размеры без технологических поправок, пропорционально
      длине кабеля. Сырьё учитывается один раз; разделка не увеличивает расход.
    </InfoHint></header>
    <div className="he-field-pair">
      {(["fromMm", "toMm"] as const).map((end, index) => <label key={end}>{index === 0 ? "Начало, мм" : "Конец, мм"}
        <input type="number" aria-label={`Снятие оболочки ${index === 0 ? "начала" : "конца"}, мм`} min="0" step="0.001"
          value={cable.sheathStrip?.[end] ?? ""} disabled={disabled}
          onChange={(event) => onUpdate({ sheathStrip: {
            fromMm: cable.sheathStrip?.fromMm ?? null, toMm: cable.sheathStrip?.toMm ?? null,
            [end]: event.target.value.trim() === "" ? null : finiteNumber(event.target.value, cable.sheathStrip?.[end] ?? 0),
          } })} />
      </label>)}
    </div>
    {stripping && <output aria-label="Снятие оболочки с поправками">
      С поправками: {stripping.fromMm ?? "—"} / {stripping.toMm ?? "—"} мм
      {!stripping.isComplete && <InfoHint>Укажите длину кабеля и размеры обоих концов для отображения оболочки.</InfoHint>}
    </output>}
    <div className="he-field-pair">
      <label>Поправка начала, мм<input type="number" aria-label="Поправка начала кабеля, мм" step="0.001" value={cable.endCorrectionFromMm} disabled={disabled}
        onChange={(event) => onUpdate({ endCorrectionFromMm: finiteNumber(event.target.value, cable.endCorrectionFromMm) })} /></label>
      <label>Поправка конца, мм<input type="number" aria-label="Поправка конца кабеля, мм" step="0.001" value={cable.endCorrectionToMm} disabled={disabled}
        onChange={(event) => onUpdate({ endCorrectionToMm: finiteNumber(event.target.value, cable.endCorrectionToMm) })} /></label>
    </div>
    <label>Шаг округления резки, мм
      <input type="number" aria-label="Шаг округления кабеля, мм" min="0.001" step="0.001" value={cable.cutRoundingStepMm} disabled={disabled}
        onChange={(event) => onUpdate({ cutRoundingStepMm: finiteNumber(event.target.value, cable.cutRoundingStepMm) })} />
    </label>
    {cable.lengthMm === null && <div className="he-wire-length-status is-incomplete" role="alert">
      <strong>Длина кабеля не задана</strong><span>Кабель сохранён, но исключён из расхода до заполнения длины.</span>
    </div>}
    <button className="he-cable-remove" type="button" disabled={disabled} onClick={onRemove}>Расформировать кабель</button>
  </section>;
}
