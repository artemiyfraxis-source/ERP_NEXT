// @vitest-environment node
import { describe, expect, it } from "vitest";
import { terminalArticleLabel } from "./terminal-article-label";

describe("terminal article labels", () => {
  it("shows only the reel article for a complete database key", () => {
    expect(terminalArticleLabel("3:JST|14:SPH-002T-P0.5S|0:|3:PHR")).toBe("SPH-002T-P0.5S");
  });

  it("leaves ordinary and malformed values untouched", () => {
    expect(terminalArticleLabel("T-1")).toBe("T-1");
    expect(terminalArticleLabel("3:JST|bad")).toBe("3:JST|bad");
    expect(terminalArticleLabel("3:JST|3:T-1|0:|3:PHR|" )).toBe("3:JST|3:T-1|0:|3:PHR|");
    expect(terminalArticleLabel("3:JST|3:T-1")).toBe("3:JST|3:T-1");
    expect(terminalArticleLabel("3:JST|0:|3:T-2|0:")).toBe("T-2");
    expect(terminalArticleLabel("3:JST|3:T|1|0:|0:")).toBe("T|1");
  });
});
