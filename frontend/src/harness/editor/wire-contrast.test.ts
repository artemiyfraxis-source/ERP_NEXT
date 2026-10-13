// @vitest-environment node
import { expect, it } from "vitest";
import { drawEditorSceneObject } from "./CanvasViewport";
import type { EditorSceneObject } from "./editor-types";
import { isLightWireColor, LIGHT_WIRE_OUTLINE } from "./wire-contrast";

it("tells light conductor colours (white, off-white, yellow, light grey) from the ones that read on white", () => {
  for (const color of ["#FFFFFF", "#fff", "white", "#F4F4F4", "#F2C200", "#C0C0C0", "rgb(255, 240, 200)", "#ffffffcc"]) expect(isLightWireColor(color), color).toBe(true);
  for (const color of ["#222222", "#D7322D", "#2463C7", "#2E9E4F", "#b0a089", "#FF8000", "", "transparent"]) expect(isLightWireColor(color), color).toBe(false);
});

const recorder = () => {
  const strokes: { color: string; width: number }[] = [];
  const state = {
    strokeStyle: "", fillStyle: "", lineWidth: 1, font: "", textAlign: "start", textBaseline: "alphabetic", lineJoin: "", lineCap: "", globalAlpha: 1,
    save: () => undefined, restore: () => undefined, beginPath: () => undefined, moveTo: () => undefined, lineTo: () => undefined,
    arcTo: () => undefined, quadraticCurveTo: () => undefined, closePath: () => undefined, arc: () => undefined, fill: () => undefined,
    fillText: () => undefined, setLineDash: () => undefined, stroke: () => strokes.push({ color: String(state.strokeStyle), width: state.lineWidth }),
  };
  return { context: state as unknown as CanvasRenderingContext2D, strokes };
};
const wire = (color: string, view: "e4" | "drawing", paths = false): EditorSceneObject => {
  const points = [{ x: 0, y: 20 }, { x: 120, y: 20 }];
  return { id: "w", layerId: "wires", kind: "wire", label: "W1", x: 0, y: 0, width: 0, height: 0, color, points,
    ...(paths ? { paths: [points] } : {}), metadata: view === "e4" ? { view: "e4" } : { drawingWidth: "2.5" } };
};

it("outlines an off-white and a yellow conductor on Э4 and on the drawing, keeping its own colour on top", () => {
  for (const color of ["#F4F4F4", "#F2C200"]) {
    const e4 = recorder();
    drawEditorSceneObject(e4.context, wire(color, "e4"), false, "e4");
    expect(e4.strokes.slice(0, 2)).toEqual([{ color: LIGHT_WIRE_OUTLINE, width: 5 }, { color, width: 3 }]);
    for (const paths of [true, false]) {
      const drawing = recorder();
      drawEditorSceneObject(drawing.context, wire(color, "drawing", paths), false, "drawing");
      expect(drawing.strokes.slice(0, 2)).toEqual([{ color: LIGHT_WIRE_OUTLINE, width: 4.5 }, { color, width: 2.5 }]);
    }
  }
  const dark = recorder();
  drawEditorSceneObject(dark.context, wire("#222222", "e4"), false, "e4");
  expect(dark.strokes[0]).toEqual({ color: "#222222", width: 3 });
});
