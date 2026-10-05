export type TemplateViewKind = "e4" | "drawing" | "additional";
export type PrimitiveKind = "line" | "rectangle" | "ellipse" | "text";
export type ContactDirection = "left" | "right" | "up" | "down";

export interface TemplatePrimitive {
  id: string;
  kind: PrimitiveKind;
  x: number;
  y: number;
  width: number;
  height: number;
  color: string;
  text: string;
}

export interface TemplateContactPoint {
  id: string;
  name: string;
  contactNumber: string;
  direction: ContactDirection;
  x: number;
  y: number;
}

export interface TemplateView {
  id: string;
  kind: TemplateViewKind;
  name: string;
  primitives: TemplatePrimitive[];
  contactPoints: TemplateContactPoint[];
}

export interface TemplateContent {
  schemaVersion: 1;
  views: TemplateView[];
}

export const TEMPLATE_WIDTH = 720;
export const TEMPLATE_HEIGHT = 440;

export function newTemplateContent(): TemplateContent {
  return {
    schemaVersion: 1,
    views: [
      { id: crypto.randomUUID(), kind: "e4", name: "Схема Э4", primitives: [], contactPoints: [] },
      { id: crypto.randomUUID(), kind: "drawing", name: "Чертёж", primitives: [], contactPoints: [] },
    ],
  };
}

export function addView(content: TemplateContent, name: string): TemplateContent {
  return {
    ...content,
    views: [...content.views, {
      id: crypto.randomUUID(), kind: "additional", name: name.trim() || `Дополнительный вид ${content.views.length - 1}`,
      primitives: [], contactPoints: [],
    }],
  };
}

export function addPrimitive(content: TemplateContent, viewId: string, kind: PrimitiveKind): [TemplateContent, string] {
  const id = crypto.randomUUID();
  return [updateView(content, viewId, view => ({
    ...view,
    primitives: [...view.primitives, {
      id, kind, x: 100, y: 100, width: kind === "line" ? 110 : 140,
      height: kind === "line" ? 0 : 70, color: "#27445a", text: kind === "text" ? "Текст" : "",
    }],
  })), id];
}

export function addContactPoint(content: TemplateContent, viewId: string): [TemplateContent, string] {
  const id = crypto.randomUUID();
  return [updateView(content, viewId, view => ({
    ...view,
    contactPoints: [...view.contactPoints, {
      id, name: `Контакт ${view.contactPoints.length + 1}`,
      contactNumber: String(view.contactPoints.length + 1), direction: "right", x: 260, y: 200,
    }],
  })), id];
}

export function updateView(content: TemplateContent, viewId: string, edit: (view: TemplateView) => TemplateView): TemplateContent {
  return { ...content, views: content.views.map(view => view.id === viewId ? edit(view) : view) };
}

export function moveItem(content: TemplateContent, viewId: string, itemId: string, x: number, y: number): TemplateContent {
  if (!Number.isFinite(x) || !Number.isFinite(y)) return content;
  return updateView(content, viewId, view => ({
    ...view,
    primitives: view.primitives.map(item => item.id === itemId ? { ...item, x, y } : item),
    contactPoints: view.contactPoints.map(item => item.id === itemId ? { ...item, x, y } : item),
  }));
}

export function recordTemplateUndo(
  stack: readonly TemplateContent[],
  before: TemplateContent,
  after: TemplateContent,
): TemplateContent[] {
  if (before === after) return [...stack];
  return [...stack.slice(-49), before];
}

export function validateTemplate(content: TemplateContent): string | null {
  if (content.schemaVersion !== 1 || content.views.filter(v => v.kind === "e4").length !== 1 ||
      content.views.filter(v => v.kind === "drawing").length !== 1) return "Нужны по одному виду Э4 и Чертежа.";
  const ids = new Set<string>();
  for (const view of content.views) {
    if (!view.id || !view.name.trim() || ids.has(view.id)) return "Название и идентификатор каждого вида должны быть уникальны и заполнены.";
    ids.add(view.id);
    for (const item of [...view.primitives, ...view.contactPoints]) {
      if (!item.id || ids.has(item.id) || !Number.isFinite(item.x) || !Number.isFinite(item.y)) return "Обнаружены повторяющиеся объекты или неверные координаты.";
      ids.add(item.id);
    }
    for (const point of view.contactPoints) {
      if (!point.name.trim() || !point.contactNumber.trim()) return "У каждой точки нужны имя и номер контакта.";
    }
  }
  return null;
}
