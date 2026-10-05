import type { EditorObjectKind, EditorSceneObject } from "./editor-types";

export type MaterialObjectGroup = "connectors" | "wires" | "coverings" | "markings";

export interface MaterialObjectsPanelProps {
  readonly objects: readonly EditorSceneObject[];
  readonly hiddenObjectIds: readonly string[];
  readonly selectedObjectIds?: readonly string[];
  readonly onVisibilityChange?: (objectId: string, visible: boolean) => void;
  readonly onObjectSelect?: (objectId: string, additive?: boolean) => void;
}

const groupLabels: Readonly<Record<MaterialObjectGroup, string>> = {
  connectors: "Соединители",
  wires: "Провода",
  coverings: "Оболочки",
  markings: "Маркировка",
};

const groupOrder: readonly MaterialObjectGroup[] = ["connectors", "wires", "coverings", "markings"];

export function materialObjectGroup(kind: EditorObjectKind): MaterialObjectGroup | null {
  switch (kind) {
    case "connector": return "connectors";
    case "wire": return "wires";
    case "physical-covering": return "coverings";
    case "text":
    case "graphic-text": return "markings";
    default: return null;
  }
}

export function MaterialObjectsPanel({
  objects,
  hiddenObjectIds,
  selectedObjectIds = [],
  onVisibilityChange,
  onObjectSelect,
}: MaterialObjectsPanelProps) {
  const hidden = new Set(hiddenObjectIds);
  const grouped = new Map<MaterialObjectGroup, EditorSceneObject[]>(groupOrder.map(group => [group, []]));
  for (const object of objects) {
    const group = materialObjectGroup(object.kind);
    if (group) grouped.get(group)!.push(object);
  }

  return <section className="he-material-objects" aria-label="Материальные объекты">
    <h3 className="he-material-objects-title">Объекты</h3>
    {groupOrder.map(group => {
      const items = grouped.get(group)!;
      if (!items.length) return null;
      return <section className="he-material-group" key={group} aria-label={groupLabels[group]}>
        <h4 className="he-material-group-title">{groupLabels[group]} <span>{items.length}</span></h4>
        <ul className="he-material-list">
          {items.map(object => {
            const label = object.label || object.id;
            const visible = !hidden.has(object.id);
            const selected = selectedObjectIds.includes(object.id);
            return <li className={`he-material-row${visible ? "" : " is-hidden"}${selected ? " is-selected" : ""}`} key={object.id}>
              <button
                type="button"
                className="he-material-select"
                aria-label={`Выбрать ${label}`}
                aria-pressed={selected}
                onClick={event => onObjectSelect?.(object.id, event.ctrlKey || event.metaKey || event.shiftKey)}
              >{label}</button>
              {onVisibilityChange && <label className="he-material-name" title={label}>
                <input
                  type="checkbox"
                  checked={visible}
                  aria-label={`${visible ? "Скрыть" : "Показать"} ${label}`}
                  onChange={event => onVisibilityChange(object.id, event.target.checked)}
                />
              </label>}
            </li>;
          })}
        </ul>
      </section>;
    })}
  </section>;
}
