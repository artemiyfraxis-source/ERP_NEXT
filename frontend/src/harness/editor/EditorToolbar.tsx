import { useEffect, useState } from "react";
import { editorZoomPresets } from "./editor-camera";
import { DrawingToolIcon, drawingToolLabels } from "../component-library/DrawingToolIcon";
import type { EditorTool, HarnessEditorView } from "./editor-types";

type DrawingToolKind = keyof typeof drawingToolLabels;

interface ToolDefinition {
  readonly id: EditorTool;
  readonly label: string;
  readonly shortcut: string;
  readonly glyph?: string;
  readonly iconKind?: DrawingToolKind;
  readonly views: readonly HarnessEditorView[];
}

const tools: readonly ToolDefinition[] = [
  { id: "position-rail", label: "Привязка позиций", shortcut: "R", glyph: "┄", views: ["drawing"] },
  { id: "dimension-auxiliary", label: "Вспомогательный размер", shortcut: "", glyph: "↔", views: ["drawing"] },
  { id: "select", label: "Выбор", shortcut: "V", glyph: "↖", views: ["e4", "drawing"] },
  { id: "pan", label: "Перемещение поля", shortcut: "H", glyph: "✋", views: ["e4", "drawing"] },
  { id: "connector", label: "Соединитель", shortcut: "C", iconKind: "contact", views: ["e4", "drawing"] },
  { id: "wire", label: "Провод", shortcut: "W", iconKind: "line", views: ["e4", "drawing"] },
  { id: "text", label: "Текст", shortcut: "T", iconKind: "text", views: ["e4", "drawing"] },
  { id: "graphic-contact", label: "Графический контакт", shortcut: "", iconKind: "contact", views: ["e4", "drawing"] },
  { id: "graphic-line", label: "Линия", shortcut: "", iconKind: "line", views: ["e4", "drawing"] },
  { id: "graphic-polyline", label: "Полилиния", shortcut: "", iconKind: "polyline", views: ["e4", "drawing"] },
  { id: "graphic-rectangle", label: "Прямоугольник", shortcut: "", iconKind: "rectangle", views: ["e4", "drawing"] },
  { id: "graphic-ellipse", label: "Эллипс", shortcut: "", iconKind: "ellipse", views: ["e4", "drawing"] },
  { id: "graphic-bezier", label: "Кривая Безье", shortcut: "", iconKind: "bezier", views: ["e4", "drawing"] },
  { id: "graphic-closed-contour", label: "Замкнутый контур", shortcut: "", iconKind: "closedContour", views: ["e4", "drawing"] },
  { id: "graphic-text", label: "Текст фигуры", shortcut: "", iconKind: "text", views: ["e4", "drawing"] },
];

type ToolbarSection = "drawing" | "edit" | "view" | "snap";
const toolbarSections: readonly { id: ToolbarSection; label: string }[] = [
  { id: "drawing", label: "Рисование" },
  { id: "edit", label: "Правка" },
  { id: "view", label: "Вид" },
  { id: "snap", label: "Привязки" },
];

export function editorToolShortcut(event: Pick<KeyboardEvent, "code" | "key" | "ctrlKey" | "altKey" | "metaKey" | "isComposing">, view: HarnessEditorView): EditorTool | null {
  if (event.ctrlKey || event.altKey || event.metaKey || event.isComposing) return null;
  const shortcut = event.code.startsWith("Key") ? event.code.slice(3) : event.key.toUpperCase();
  // Keep existing keyboard workflows available without a separate dimensions menu.
  if (shortcut === "D" && view === "drawing") return "dimension";
  return tools.find(tool => tool.shortcut && tool.shortcut === shortcut && tool.views.includes(view))?.id ?? null;
}

export interface EditorToolbarProps {
  readonly view: HarnessEditorView;
  readonly activeTool: EditorTool;
  readonly zoom: number;
  readonly onToolChange: (tool: EditorTool) => void;
  readonly onZoomIn: () => void;
  readonly onZoomOut: () => void;
  readonly onZoomChange: (zoom: number) => void;
  readonly onFitView: () => void;
  readonly selectedGraphic?: boolean;
  readonly canPasteGraphic?: boolean;
  readonly onCopy?: () => void;
  readonly onPaste?: () => void;
  readonly onDelete?: () => void;
  readonly onUndo?: () => void;
  readonly angleStep?: number;
  readonly onAngleStepChange?: (degrees: number) => void;
  readonly snaps?: { corners: boolean; contours: boolean; tangents: boolean };
  readonly onSnapsChange?: (snaps: { corners: boolean; contours: boolean; tangents: boolean }) => void;
}

export function EditorToolbar({
  view,
  activeTool,
  zoom,
  onToolChange,
  onZoomIn,
  onZoomOut,
  onZoomChange,
  onFitView,
  selectedGraphic = false,
  canPasteGraphic = false,
  onCopy,
  onPaste,
  onDelete,
  onUndo,
  angleStep = 15,
  onAngleStepChange,
  snaps,
  onSnapsChange,
}: EditorToolbarProps) {
  const zoomIsPreset = editorZoomPresets.some((preset) => Math.abs(preset - zoom) < 0.001);
  const visibleTools = tools.filter((tool) => tool.views.includes(view));
  const [activeSection, setActiveSection] = useState<ToolbarSection>("drawing");

  useEffect(() => {
    setActiveSection("drawing");
  }, [view]);

  const toolButton = (tool: ToolDefinition) => (
    <button
      className={activeTool === tool.id ? "he-tool active" : "he-tool"}
      type="button"
      key={tool.id}
      title={tool.shortcut ? `${tool.label} · ${tool.shortcut}` : tool.label}
      aria-label={tool.shortcut ? `${tool.label}, клавиша ${tool.shortcut}` : tool.label}
      aria-pressed={activeTool === tool.id}
      aria-keyshortcuts={tool.shortcut || undefined}
      onClick={() => onToolChange(tool.id)}
    >
      {tool.iconKind ? <DrawingToolIcon kind={tool.iconKind} /> : <span className="he-tool-symbol" aria-hidden="true">{tool.glyph}</span>}
      {tool.shortcut && <small>{tool.shortcut}</small>}
    </button>
  );

  const drawingTools = visibleTools.map(toolButton);
  const renderSection = (section: ToolbarSection) => {
    if (section === "drawing") {
      return <div className="he-toolbar-panel" role="tabpanel" id="he-toolbar-panel-drawing" aria-labelledby="he-toolbar-tab-drawing">
        <div className="he-toolbar-group he-toolbar-tools" aria-label="Инструменты рисования">{drawingTools}</div>
      </div>;
    }
    if (section === "edit") {
      return <div className="he-toolbar-panel" role="tabpanel" id="he-toolbar-panel-edit" aria-labelledby="he-toolbar-tab-edit">
        <div className="he-toolbar-group he-toolbar-edit" aria-label="Правка графики">
          <button className="he-tool" type="button" aria-label="Копировать фигуры" title="Копировать" disabled={!selectedGraphic} onClick={onCopy}><span className="he-tool-symbol" aria-hidden="true">⧉</span><span className="he-tool-caption">Копировать</span></button>
          <button className="he-tool" type="button" aria-label="Вставить фигуры" title="Вставить" disabled={!canPasteGraphic} onClick={onPaste}><span className="he-tool-symbol" aria-hidden="true">▣</span><span className="he-tool-caption">Вставить</span></button>
          <button className="he-tool" type="button" aria-label="Удалить выбранную фигуру" title="Удалить" disabled={!selectedGraphic} onClick={onDelete}><span className="he-tool-symbol" aria-hidden="true">⌫</span><span className="he-tool-caption">Удалить</span></button>
          <button className="he-tool" type="button" aria-label="Отменить" title="Отменить" onClick={onUndo}><span className="he-tool-symbol" aria-hidden="true">↶</span><span className="he-tool-caption">Отменить</span></button>
        </div>
      </div>;
    }
    if (section === "view") {
      return <div className="he-toolbar-panel" role="tabpanel" id="he-toolbar-panel-view" aria-labelledby="he-toolbar-tab-view">
        <div className="he-toolbar-group he-zoom-tools" aria-label="Масштаб">
          <button className="he-tool" type="button" aria-label="Увеличить масштаб" title="Увеличить" onClick={onZoomIn}><span className="he-tool-symbol" aria-hidden="true">+</span><span className="he-tool-caption">Увеличить</span></button>
          <select className="he-zoom-select" aria-label="Масштаб редактора" title="Масштаб редактора" value={zoomIsPreset ? String(zoom) : "custom"} onChange={(event) => event.target.value === "fit" ? onFitView() : event.target.value !== "custom" && onZoomChange(Number(event.target.value))}>
            {!zoomIsPreset && <option value="custom">{Math.round(zoom * 100)}%</option>}
            {editorZoomPresets.map((preset) => <option key={preset} value={preset}>{Math.round(preset * 100)}%</option>)}
            <option value="fit">Вписать в экран</option>
          </select>
          <button className="he-tool" type="button" aria-label="Уменьшить масштаб" title="Уменьшить" onClick={onZoomOut}><span className="he-tool-symbol" aria-hidden="true">−</span><span className="he-tool-caption">Уменьшить</span></button>
          <button className="he-tool he-fit-tool" type="button" aria-label="Вписать в экран" title="Вписать в экран" onClick={onFitView}><span className="he-tool-symbol" aria-hidden="true">⌂</span><span className="he-tool-caption">Вписать</span></button>
        </div>
      </div>;
    }
    return <div className="he-toolbar-panel" role="tabpanel" id="he-toolbar-panel-snap" aria-labelledby="he-toolbar-tab-snap">
      <div className="he-toolbar-group he-toolbar-snap" aria-label="Угловая и геометрическая привязка">
        <label className="he-graphic-angle" title="Шаг угловой привязки"><span>Угол</span><select aria-label="Шаг угловой привязки" value={angleStep} onChange={event => onAngleStepChange?.(Number(event.target.value))}><option value={15}>15°</option><option value={30}>30°</option><option value={45}>45°</option><option value={90}>90°</option></select></label>
        {snaps && <div className="he-graphic-snaps" aria-label="Привязки">
          <label title="Привязка к углам"><input aria-label="Углы" type="checkbox" checked={snaps.corners} onChange={event => onSnapsChange?.({ ...snaps, corners: event.target.checked })} /><span>Углы</span></label>
          <label title="Привязка к контурам"><input aria-label="Контуры" type="checkbox" checked={snaps.contours} onChange={event => onSnapsChange?.({ ...snaps, contours: event.target.checked })} /><span>Контуры</span></label>
          <label title="Привязка касательной к окружности"><input aria-label="Касательные" type="checkbox" checked={snaps.tangents} onChange={event => onSnapsChange?.({ ...snaps, tangents: event.target.checked })} /><span>Касательные</span></label>
        </div>}
      </div>
    </div>;
  };

  return (
    <aside className="he-toolbar" aria-label="Инструменты редактора">
      <nav className="he-toolbar-tabs" aria-label="Контекст инструментов" role="tablist">
        {toolbarSections.map(section => <button type="button" role="tab" key={section.id} id={`he-toolbar-tab-${section.id}`} aria-selected={activeSection === section.id} aria-controls={`he-toolbar-panel-${section.id}`} className={activeSection === section.id ? "active" : ""} onClick={() => setActiveSection(section.id)}>{section.label}</button>)}
      </nav>
      {toolbarSections.map(section => <div key={section.id} hidden={activeSection !== section.id}>{renderSection(section.id)}</div>)}
    </aside>
  );
}
