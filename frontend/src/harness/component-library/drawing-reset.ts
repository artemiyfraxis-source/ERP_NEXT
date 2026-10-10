import { addAdditionalViewV3 } from "./template-commands-v3";
import type { TemplateContentV3 } from "./template-model-v3";
import { drawingSelection, separateLegacyDrawings, type ArticleDrawing, type DrawingTarget } from "./drawing-bindings";
import type { DrawingGenerator } from "./drawing-generator";

export interface DrawingState {
  content: TemplateContentV3;
  articleDrawings?: ArticleDrawing[];
  drawingGenerators?: DrawingGenerator[];
}

const emptyDrawing = (articleVariantId: string, target: DrawingTarget, viewId: string): ArticleDrawing =>
  ({ articleVariantId, target, viewId, nodeIds: [], contactPointIds: [], bundlePortIds: [] });

/** Clear only the chosen articles/section. A separate workspace preserves any
 * shared source still used by siblings; repeated clearing reuses that workspace. */
export function clearArticleDrawings(state: DrawingState, target: DrawingTarget, articleIds: readonly string[], activeViewId: string) {
  const separated = separateLegacyDrawings(state.content, state.articleDrawings);
  let content = separated.content;
  const articles = new Set(articleIds);
  const affected = (drawing: ArticleDrawing) => drawing.target === target && articles.has(drawing.articleVariantId);
  const generators = state.drawingGenerators ?? [];
  const active = content.views.find(view => view.id === activeViewId)!;
  const shared = separated.drawings.some(drawing => drawing.viewId === activeViewId && !affected(drawing))
    || generators.some(generator => generator.viewId === activeViewId && (generator.target !== target || generator.articles.some(article => !articles.has(article.articleId))));
  let viewId = activeViewId;
  // Built-in drawing views can also serve legacy/unassigned articles. Reuse only
  // when clearing the entire series or an already independent additional view.
  if (shared || active.kind === "e4" || active.kind !== "additional" && articles.size !== content.articleVariants.length) {
    [content, viewId] = addAdditionalViewV3(content, `Рисунки · ${{ e4: "Схема Э4", drawing: "Чертёж", route: "Маршрут" }[target]}`);
  } else {
    content = { ...content, views: content.views.map(view => view.id === viewId ? {
      ...view, layers: view.layers.map(layer => ({ ...layer, locked: false, visible: true, nodes: [] })), contactPoints: [], bundlePorts: [], repeatPlacements: [],
    } : view) };
  }
  const drawingGenerators = generators.flatMap(generator => {
    if (generator.target !== target) return [generator];
    const remaining = generator.articles.filter(article => !articles.has(article.articleId));
    return remaining.length || !generator.articles.length && generator.viewId !== activeViewId
      ? [{ ...generator, articles: remaining }] : [];
  });
  return { content, drawingGenerators, viewId,
    articleDrawings: [...separated.drawings.filter(drawing => !affected(drawing)), ...articleIds.map(id => emptyDrawing(id, target, viewId))] };
}

/** A deleted source is an ordinary empty drawing, not an invalid generator.
 * Partially dismantled sources remain editable recipes with no assigned output. */
export function detachIncompleteGenerators(content: TemplateContentV3, generators: DrawingGenerator[], drawings: ArticleDrawing[]): Pick<DrawingState, "drawingGenerators" | "articleDrawings"> {
  let articleDrawings = drawings;
  const drawingGenerators = generators.flatMap(generator => {
    if (generator.roles.period.length && generator.periodPointIds.length) return [generator];
    const assigned = new Set(generator.articles.map(article => article.articleId));
    articleDrawings = [
      ...articleDrawings.filter(drawing => !(drawing.target === generator.target && assigned.has(drawing.articleVariantId))),
      ...[...assigned].map(id => emptyDrawing(id, generator.target, generator.viewId)),
    ];
    const view = content.views.find(item => item.id === generator.viewId)!;
    return view.layers.some(layer => layer.nodes.length) || view.contactPoints.length || view.bundlePorts.length
      ? [{ ...generator, articles: [] }] : [];
  });
  return { drawingGenerators, articleDrawings };
}

/** Newly drawn objects join the bindings of this workspace, including a cleared
 * series workspace. Selection changes never decide whether new geometry is saved. */
export function reconcileArticleDrawings(before: TemplateContentV3, after: TemplateContentV3, drawings: readonly ArticleDrawing[], editedViewId?: string, editedArticleId?: string | null): ArticleDrawing[] {
  const oldView = before.views.find(view => view.id === editedViewId);
  const oldIds = new Set(oldView ? [...oldView.layers.flatMap(layer => layer.nodes.map(node => node.id)), ...oldView.contactPoints.map(point => point.id), ...oldView.bundlePorts.map(port => port.id)] : []);
  const edited = drawings.find(drawing => drawing.articleVariantId === editedArticleId && drawing.viewId === editedViewId);
  const sameIds = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every(id => b.includes(id));
  return drawings.filter(drawing => after.articleVariants.some(article => article.id === drawing.articleVariantId)).flatMap(drawing => {
    const view = after.views.find(view => drawing.viewId ? view.id === drawing.viewId : view.kind === "drawing");
    if (!view) return [];
    const sharesDrawing = !edited || drawing.target === edited.target && sameIds(drawing.nodeIds, edited.nodeIds) && sameIds(drawing.contactPointIds, edited.contactPointIds) && sameIds(drawing.bundlePortIds ?? [], edited.bundlePortIds ?? []);
    const additions = view.id === editedViewId && sharesDrawing ? [...view.layers.flatMap(layer => layer.nodes.map(node => node.id)), ...view.contactPoints.map(point => point.id), ...view.bundlePorts.map(port => port.id)].filter(id => !oldIds.has(id)) : [];
    return [{ ...drawing, ...drawingSelection(view, [...drawing.nodeIds, ...drawing.contactPointIds, ...drawing.bundlePortIds ?? [], ...additions], drawing.articleVariantId) }];
  });
}
