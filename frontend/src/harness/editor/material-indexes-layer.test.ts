// @vitest-environment node
import { expect, it } from "vitest";
import { createEmptyHarnessDesign, defaultLayerIds, parseHarnessDesignDocument } from "./model";

// По коммиту TECHMAP-GRAPHER 723ff99: старые чертежи без слоя индексов открываются без изменений,
// слой «Индексы материалов и позиции» добавляется при чтении поверх остальных.
it("opens a drawing saved before the material-indexes layer and adds the layer on top", () => {
  const fresh = JSON.parse(JSON.stringify(createEmptyHarnessDesign())) as { views: { drawing: { layers: { id: string; order: number }[] } } };
  fresh.views.drawing.layers = fresh.views.drawing.layers.filter(layer => layer.id !== defaultLayerIds.materialIndexes);
  const before = fresh.views.drawing.layers.map(layer => layer.id);
  const layers = parseHarnessDesignDocument(fresh).views.drawing.layers;
  expect(layers.map(layer => layer.id)).toEqual([...before, defaultLayerIds.materialIndexes]);
  const top = layers.find(layer => layer.id === defaultLayerIds.materialIndexes)!;
  expect(Math.max(...layers.map(layer => layer.order))).toBe(top.order);
  expect(top.visible && !top.locked).toBe(true);
});
