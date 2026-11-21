import { describe, expect, it } from "vitest";
import { etkTitle, navSub } from "./etkDocOps";

describe("название операции ЭТК", () => {
  it("название из одного кода карты не показывается, берётся категория по коду", () => {
    expect(etkTitle({ code: "PRS_TERMINAL_auto", title: "PRS_TERMINAL_auto" } as never)).toBe("Опрессовка");
    expect(etkTitle({ code: "INS_terminal_1.00", title: "INS_terminal_1.00 | Монтаж терминалов" } as never)).toBe("Монтаж терминалов");
  });
  it("код карты во второй строке только по запросу", () => {
    const operation = { code: "INS_terminal_1.00", connector: "X1" } as never;
    expect(navSub(operation)).toBe("INS_terminal_1.00 · X1");
    expect(navSub(operation, false)).toBe("X1");
  });
});
