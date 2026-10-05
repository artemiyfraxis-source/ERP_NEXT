import { useState, type ReactElement } from "react";
import "./connection-table-settings.css";
import {
  connectionTableColumnLabels,
  type ConnectionTableColumnId,
  updateConnectionTableSettings,
  useConnectionTableSettings,
} from "./connection-table-settings";

export function ConnectionTableSettings(): ReactElement {
  const settings = useConnectionTableSettings();
  const [dragged, setDragged] = useState<ConnectionTableColumnId | null>(null);
  const move = (target: ConnectionTableColumnId) => {
    if (!dragged || dragged === target) return;
    const columns = [...settings.columns];
    const from = columns.findIndex(column => column.id === dragged);
    const to = columns.findIndex(column => column.id === target);
    if (from < 0 || to < 0) return;
    const [item] = columns.splice(from, 1);
    columns.splice(to, 0, item!);
    updateConnectionTableSettings({ columns });
  };
  return <section className="he-utility-section he-connection-settings" aria-labelledby="he-connection-settings-heading">
    <h3 id="he-connection-settings-heading">Настройка</h3>
    <p className="he-settings-help">Поля таблицы соединений. Перетащите строку, чтобы изменить порядок; флажок управляет отображением во всех проектах.</p>
    <div className="he-connection-settings-list" aria-label="Поля таблицы соединений">
      {settings.columns.map(column => <div key={column.id} className="he-connection-setting-row" draggable
        onDragStart={() => setDragged(column.id)} onDragEnd={() => setDragged(null)} onDragOver={event => event.preventDefault()} onDrop={() => { move(column.id); setDragged(null); }}>
        <span className="he-connection-drag" aria-hidden="true">⋮⋮</span>
        <label><input type="checkbox" checked={column.visible} onChange={event => updateConnectionTableSettings({ columns: settings.columns.map(item => item.id === column.id ? { ...item, visible: event.target.checked } : item) })} />{connectionTableColumnLabels[column.id]}</label>
      </div>)}
    </div>
  </section>;
}
