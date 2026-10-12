// Test helper: print goes through a hidden iframe (print.ts). This stands in for the browser — it records each frame's
// `srcdoc`, fires its onload and counts `print()` calls. Call `.restore()` (or rely on vi.restoreAllMocks) after.
import { vi } from "vitest";

export function capturePrintFrames() {
  const html: string[] = [];
  const print = vi.fn();
  const original = document.body.appendChild.bind(document.body);
  const spy = vi.spyOn(document.body, "appendChild").mockImplementation(((node: Node) => {
    if (!(node instanceof HTMLIFrameElement)) return original(node);
    html.push(node.srcdoc);
    Object.defineProperty(node, "contentWindow", { value: { focus: vi.fn(), print, addEventListener: vi.fn() } });
    queueMicrotask(() => node.onload?.(new Event("load")));
    return node;
  }) as typeof document.body.appendChild);
  return { html, print, restore: () => spy.mockRestore() };
}
