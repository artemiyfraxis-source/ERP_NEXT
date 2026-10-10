import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { LayerV2 } from "./template-model-v2";
import "./TemplateLayersPanelV2.css";

export interface TemplateLayersPanelV2Props {
  readonly layers: readonly LayerV2[];
  readonly activeLayerId: string | null;
  readonly onActivate: (layerId: string) => void;
  readonly onAdd: () => void;
  readonly onRename: (layerId: string, name: string) => void;
  readonly onMove: (layerId: string, targetIndex: number) => void;
  readonly onToggleVisible: (layerId: string) => void;
  readonly onToggleLocked: (layerId: string) => void;
  readonly onDelete: (layerId: string) => void;
}

export function normalizeTemplateLayerNameV2(value: string): string | null {
  const normalized = value.trim();
  return normalized ? normalized : null;
}

export function TemplateLayersPanelV2({
  layers,
  activeLayerId,
  onActivate,
  onAdd,
  onRename,
  onMove,
  onToggleVisible,
  onToggleLocked,
  onDelete,
}: TemplateLayersPanelV2Props) {
  const [editingLayerId, setEditingLayerId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState("");
  const renameInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingLayerId && !layers.some(layer => layer.id === editingLayerId)) setEditingLayerId(null);
  }, [editingLayerId, layers]);

  useEffect(() => {
    if (editingLayerId) {
      renameInput.current?.focus();
      renameInput.current?.select();
    }
  }, [editingLayerId]);

  const beginRename = (layer: LayerV2) => {
    onActivate(layer.id);
    setEditingLayerId(layer.id);
    setEditingName(layer.name);
  };

  const cancelRename = () => {
    setEditingLayerId(null);
    setEditingName("");
  };

  const finishRename = (layer: LayerV2) => {
    if (editingLayerId !== layer.id) return;
    const normalized = normalizeTemplateLayerNameV2(editingName);
    if (normalized && normalized !== layer.name) onRename(layer.id, normalized);
    cancelRename();
  };

  const renameKeyDown = (event: KeyboardEvent<HTMLInputElement>, layer: LayerV2) => {
    if (event.key === "Enter") {
      event.preventDefault();
      finishRename(layer);
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancelRename();
    }
  };

  return (
    <section className="template-layers-v2" aria-label="Слои шаблона">
      <div className="template-layers-v2-heading">
        <strong>Слои</strong>
        <button type="button" onClick={onAdd} aria-label="Добавить слой">+ Слой</button>
      </div>
      <p className="template-layers-v2-note">Слои ниже в списке рисуются поверх предыдущих.</p>
      <ol className="template-layers-v2-list" aria-label="Порядок слоёв шаблона">
        {layers.map((layer, index) => {
          const active = layer.id === activeLayerId;
          const editing = layer.id === editingLayerId;
          const status = [layer.visible ? "видим" : "скрыт", layer.locked ? "заблокирован" : "разблокирован"].join(", ");
          return (
            <li
              key={layer.id}
              className={active ? "template-layer-v2 active" : "template-layer-v2"}
              data-layer-id={layer.id}
              data-visible={layer.visible ? "true" : "false"}
              data-locked={layer.locked ? "true" : "false"}
            >
              <button
                type="button"
                className="template-layer-v2-activate"
                aria-label={`Активировать слой ${layer.name}`}
                aria-pressed={active}
                onClick={() => onActivate(layer.id)}
              >
                <span aria-hidden="true">{active ? "◆" : "◇"}</span>
              </button>
              <div className="template-layer-v2-name">
                {editing ? (
                  <input
                    ref={renameInput}
                    value={editingName}
                    aria-label={`Название слоя ${layer.name}`}
                    onChange={event => setEditingName(event.target.value)}
                    onBlur={() => finishRename(layer)}
                    onKeyDown={event => renameKeyDown(event, layer)}
                  />
                ) : (
                  <button
                    type="button"
                    aria-label={`Переименовать слой ${layer.name}`}
                    title={layer.locked ? "Сначала разблокируйте слой" : "Переименовать слой"}
                    disabled={layer.locked}
                    onClick={() => beginRename(layer)}
                  >
                    <span>{layer.name}</span>
                    <small>{status}</small>
                  </button>
                )}
              </div>
              <button
                type="button"
                className="template-layer-v2-control"
                aria-label={`${layer.visible ? "Скрыть" : "Показать"} слой ${layer.name}`}
                aria-pressed={layer.visible}
                title={layer.visible ? "Скрыть" : "Показать"}
                onClick={() => onToggleVisible(layer.id)}
              >
                <span aria-hidden="true">{layer.visible ? "◉" : "○"}</span>
              </button>
              <button
                type="button"
                className="template-layer-v2-control"
                aria-label={`${layer.locked ? "Разблокировать" : "Заблокировать"} слой ${layer.name}`}
                aria-pressed={layer.locked}
                title={layer.locked ? "Разблокировать" : "Заблокировать"}
                onClick={() => onToggleLocked(layer.id)}
              >
                <span aria-hidden="true">{layer.locked ? "●" : "◌"}</span>
              </button>
              <span className="template-layer-v2-order">
                <button
                  type="button"
                  aria-label={`Переместить слой ${layer.name} ниже`}
                  title="Ниже"
                  disabled={layer.locked || index === 0}
                  onClick={() => onMove(layer.id, index - 1)}
                >↓</button>
                <button
                  type="button"
                  aria-label={`Переместить слой ${layer.name} выше`}
                  title="Выше"
                  disabled={layer.locked || index === layers.length - 1}
                  onClick={() => onMove(layer.id, index + 1)}
                >↑</button>
              </span>
              <button
                type="button"
                className="template-layer-v2-delete"
                aria-label={`Удалить слой ${layer.name}`}
                title={layer.locked ? "Сначала разблокируйте слой" : layers.length === 1 ? "В виде должен остаться хотя бы один слой" : "Удалить слой"}
                disabled={layer.locked || layers.length === 1}
                onClick={() => onDelete(layer.id)}
              >×</button>
            </li>
          );
        })}
      </ol>
      {layers.length === 0 && <p className="template-layers-v2-empty" role="status">Добавьте первый слой.</p>}
    </section>
  );
}
