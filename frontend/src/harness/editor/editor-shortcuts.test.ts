// @vitest-environment node
import { describe, expect, it } from "vitest";
import { editorToolShortcut } from "./EditorToolbar";
const key = { code:"KeyV", key:"м", ctrlKey:false, altKey:false, metaKey:false, isComposing:false };
describe("editor tool shortcuts", () => {
  it.each([["KeyV","м","select"],["KeyH","р","pan"],["KeyC","с","connector"],["KeyW","ц","wire"],["KeyT","е","text"]])("handles physical %s with Russian layout", (code, value, tool) => {
    expect(editorToolShortcut({...key, code, key:value},"e4")).toBe(tool);
    expect(editorToolShortcut({...key, code, key:value},"drawing")).toBe(tool);
  });
  it("preserves modified shortcuts and IME input, and scopes dimensions to drawing", () => {
    for(const flag of ["ctrlKey","altKey","metaKey","isComposing"])
      expect(editorToolShortcut({...key,[flag]:true},"e4")).toBeNull();
    expect(editorToolShortcut({...key,code:"KeyD"},"e4")).toBeNull();
    expect(editorToolShortcut({...key,code:"KeyD"},"drawing")).toBe("dimension");
  });
});
