// @vitest-environment node
import { describe, expect, it } from "vitest";
import { addContactPoint, addPrimitive, addView, moveItem, newTemplateContent, recordTemplateUndo, validateTemplate } from "./template-model";

describe("component template model", () => {
  it("starts with one E4 and one drawing view and adds an additional view", () => {
    const initial = newTemplateContent();
    const result = addView(initial, "Монтажная сторона");
    expect(result.views.map(view => view.kind)).toEqual(["e4", "drawing", "additional"]);
    expect(result.views[2]?.name).toBe("Монтажная сторона");
    expect(validateTemplate(result)).toBeNull();
  });

  it("rejects content that loses a required E4 or drawing view", () => {
    const initial = newTemplateContent();
    expect(validateTemplate({ ...initial, views: initial.views.filter(view => view.kind !== "e4") })).toContain("Э4");
    expect(validateTemplate({ ...initial, views: [...initial.views, { ...initial.views[0]!, id: crypto.randomUUID() }] })).toContain("Э4");
  });

  it("adds and moves simple primitives without mutating the previous version", () => {
    const initial = newTemplateContent();
    const viewId = initial.views[0]!.id;
    const [withPrimitive, id] = addPrimitive(initial, viewId, "rectangle");
    const moved = moveItem(withPrimitive, viewId, id, 325, 144);
    expect(initial.views[0]!.primitives).toHaveLength(0);
    expect(moved.views[0]!.primitives[0]).toMatchObject({ id, kind: "rectangle", x: 325, y: 144 });
  });

  it("creates a named numbered connection point", () => {
    const initial = newTemplateContent();
    const [result] = addContactPoint(initial, initial.views[1]!.id);
    expect(result.views[1]!.contactPoints[0]).toMatchObject({ name: "Контакт 1", contactNumber: "1", direction: "right" });
    expect(validateTemplate(result)).toBeNull();
  });

  it("records one undo entry for an entire drag preview", () => {
    const before = newTemplateContent();
    const viewId = before.views[0]!.id;
    const [created, id] = addPrimitive(before, viewId, "rectangle");
    const preview1 = moveItem(created, viewId, id, 110, 120);
    const preview2 = moveItem(preview1, viewId, id, 210, 220);
    const stack = recordTemplateUndo([], created, preview2);
    expect(stack).toEqual([created]);
    expect(stack).not.toContain(preview1);
    expect(recordTemplateUndo(stack, preview2, preview2)).toEqual(stack);
  });
});
